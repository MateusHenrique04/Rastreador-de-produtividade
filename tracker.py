from __future__ import annotations
import time, sqlite3, logging
from datetime import datetime
import win32gui
import ctypes
from classifier import split_app_context, is_audio_app, is_actually_playing_audio

DB_NAME = "tracker.db"
AUDIO_BACKGROUND_TIMEOUT = 6   # segundos sem foco antes de parar de contar áudio
POLL_INTERVAL = 5               # intervalo de polling em segundos
AFK_THRESHOLD = 60              # segundos sem input para considerar AFK
AFK_AUDIO_CUTOFF = 15 * 60      # 15 min AFK → para de contar tela e áudio

AUDIO_PROCESS_KEYWORDS = ["chrome", "brave", "audiobookplayer", "firefox", "spotify"]

logging.basicConfig(filename="tracker.log", level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── Banco de dados ─────────────────────────────────────────────────────────────

def init_db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        app TEXT NOT NULL,
        context TEXT NOT NULL,
        timestamp DATETIME NOT NULL)""")

    # Tabela de sessões AFK — criada automaticamente se não existir
    conn.execute("""CREATE TABLE IF NOT EXISTS afk_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at DATETIME NOT NULL,
        ended_at DATETIME,
        duration_seconds REAL)""")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_afk_started ON afk_sessions(started_at)")
    conn.commit()


def save_log(conn, log_type, app, context, timestamp):
    conn.execute(
        "INSERT INTO logs (type, app, context, timestamp) VALUES (?, ?, ?, ?)",
        (log_type, app, context, timestamp.isoformat()),
    )
    conn.commit()


def save_afk_session(conn, started_at: datetime, ended_at: datetime):
    duration = (ended_at - started_at).total_seconds()
    conn.execute(
        "INSERT INTO afk_sessions (started_at, ended_at, duration_seconds) VALUES (?, ?, ?)",
        (started_at.isoformat(), ended_at.isoformat(), duration),
    )
    conn.commit()


# ── Detecção de AFK ────────────────────────────────────────────────────────────

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def get_idle_seconds() -> float:
    """Retorna quantos segundos o usuário está sem mover mouse ou teclar."""
    lii = _LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
    return max(millis / 1000.0, 0.0)


# ── Janela ativa ───────────────────────────────────────────────────────────────

def get_active_window():
    try:
        return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or "Desconhecido"
    except Exception as e:
        logger.error("Erro janela ativa: %s", e)
        return "Desconhecido"


# ── Loop principal ─────────────────────────────────────────────────────────────

def track():
    print("🔍 Rastreamento iniciado... (CTRL+C para parar)")
    with sqlite3.connect(DB_NAME) as conn:
        init_db(conn)

        last_audio_app = last_audio_ctx = last_audio_seen = None

        # Estado AFK
        afk_start: datetime | None = None
        is_afk = False

        while True:
            try:
                now = datetime.now()
                idle = get_idle_seconds()

                # ── Suprime AFK enquanto Valorant está em foco ─────────────────
                # O Vanguard pode bloquear GetLastInputInfo e gerar falso AFK
                _title_check = get_active_window()
                _app_check, _ = split_app_context(_title_check)
                if _app_check == "Valorant":
                    idle = 0.0

                # ── Transição ATIVO → AFK ──────────────────────────────────────
                if not is_afk and idle >= AFK_THRESHOLD:
                    is_afk = True
                    afk_start = now
                    print(f"💤 AFK detectado às {now.strftime('%H:%M:%S')}")

                # ── Transição AFK → ATIVO ──────────────────────────────────────
                elif is_afk and idle < AFK_THRESHOLD:
                    is_afk = False
                    if afk_start:
                        save_afk_session(conn, afk_start, now)
                        duration = (now - afk_start).total_seconds()
                        m = int(duration // 60)
                        s = int(duration % 60)
                        print(f"✅ Voltou às {now.strftime('%H:%M:%S')} — ficou AFK por {m}min {s}s")
                    afk_start = None
                    # Reseta estado de áudio para não inflar contagem com tempo AFK
                    last_audio_app = last_audio_ctx = last_audio_seen = None

                # ── Se AFK: não registra logs de tela/áudio ────────────────────
                if is_afk:
                    # Após AFK_AUDIO_CUTOFF, garante reset do estado de áudio
                    if afk_start:
                        afk_duration = (now - afk_start).total_seconds()
                        if afk_duration >= AFK_AUDIO_CUTOFF:
                            last_audio_app = last_audio_ctx = last_audio_seen = None
                    time.sleep(POLL_INTERVAL)
                    continue

                # ── Rastreamento normal (usuário ativo) ────────────────────────
                # Reutiliza title/app já capturados acima (antes do bloco AFK)
                title = _title_check
                app, context = split_app_context(title)
                save_log(conn, "screen", app, context, now)

                audio_in_focus = is_audio_app(app)
                real_audio = is_actually_playing_audio(AUDIO_PROCESS_KEYWORDS)

                if audio_in_focus or real_audio:
                    last_audio_app = app if audio_in_focus else last_audio_app
                    last_audio_ctx = context if audio_in_focus else last_audio_ctx
                    last_audio_seen = now
                    if last_audio_app and last_audio_ctx:
                        save_log(conn, "audio", last_audio_app, last_audio_ctx, now)

                elif last_audio_app and last_audio_seen:
                    elapsed = (now - last_audio_seen).total_seconds()
                    if elapsed < AUDIO_BACKGROUND_TIMEOUT:
                        save_log(conn, "audio", last_audio_app, last_audio_ctx, now)
                        last_audio_seen = now
                    else:
                        last_audio_app = last_audio_ctx = last_audio_seen = None

                time.sleep(POLL_INTERVAL)

            except KeyboardInterrupt:
                # Garante que sessão AFK em curso é salva ao encerrar
                if is_afk and afk_start:
                    save_afk_session(conn, afk_start, datetime.now())
                print("\n⏹️  Rastreamento encerrado.")
                break
            except Exception as e:
                logger.error("Erro no loop: %s", e)
                time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    track()