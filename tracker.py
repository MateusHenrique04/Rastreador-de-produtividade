from __future__ import annotations
import time, sqlite3, logging, sys
from datetime import datetime
from classifier import split_app_context, is_audio_app, get_active_audio_process, get_window_title_by_process

DB_NAME = "tracker.db"
AUDIO_BACKGROUND_TIMEOUT = 60   # segundos sem foco antes de parar de contar áudio
POLL_INTERVAL = 5               # intervalo de polling em segundos
AFK_THRESHOLD = 5 * 60          # segundos sem input para considerar AFK
AFK_AUDIO_CUTOFF = 15 * 60      # 15 min AFK → para de contar tela e áudio

AUDIO_PROCESS_KEYWORDS = ["chrome", "brave", "audiobookplayer", "firefox", "spotify"]

logging.basicConfig(
    filename="tracker.log",
    level=logging.DEBUG,          # ← era ERROR, agora DEBUG para capturar tudo
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# Também imprime erros no terminal para diagnóstico imediato
console = logging.StreamHandler(sys.stdout)
console.setLevel(logging.WARNING)
logging.getLogger().addHandler(console)

logger = logging.getLogger(__name__)


# ── Verificação de dependências ────────────────────────────────────────────────

def _check_dependencies():
    """Verifica se as dependências do Windows estão instaladas antes de rodar."""
    missing = []
    try:
        import win32gui  # noqa: F401
    except ImportError:
        missing.append("pywin32  →  pip install pywin32")

    try:
        import ctypes
        ctypes.windll.user32  # noqa: F401
    except Exception:
        missing.append("ctypes/windll (verifique se está no Windows)")

    if missing:
        print("❌ Dependências faltando:")
        for m in missing:
            print(f"   • {m}")
        print("\nRode:  pip install pywin32 pycaw psutil")
        print("Depois: python Scripts/pywin32_postinstall.py -install")
        sys.exit(1)

    print("✅ Dependências OK")


# ── Banco de dados ─────────────────────────────────────────────────────────────

def init_db(conn):
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        app TEXT NOT NULL,
        context TEXT NOT NULL,
        timestamp DATETIME NOT NULL)""")

    conn.execute("""CREATE TABLE IF NOT EXISTS afk_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at DATETIME NOT NULL,
        ended_at DATETIME,
        duration_seconds REAL)""")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_afk_started ON afk_sessions(started_at)")
    conn.commit()
    logger.debug("Banco de dados inicializado: %s", DB_NAME)


def save_log(conn, log_type, app, context, timestamp):
    conn.execute(
        "INSERT INTO logs (type, app, context, timestamp) VALUES (?, ?, ?, ?)",
        (log_type, app, context, timestamp.isoformat()),
    )
    conn.commit()
    logger.debug("LOG [%s] app=%s | ctx=%s | ts=%s", log_type, app, context[:60], timestamp)


def save_afk_session(conn, started_at: datetime, ended_at: datetime):
    duration = (ended_at - started_at).total_seconds()
    conn.execute(
        "INSERT INTO afk_sessions (started_at, ended_at, duration_seconds) VALUES (?, ?, ?)",
        (started_at.isoformat(), ended_at.isoformat(), duration),
    )
    conn.commit()
    logger.debug("AFK salvo: %s → %s (%.0fs)", started_at, ended_at, duration)


# ── Detecção de AFK ────────────────────────────────────────────────────────────

import ctypes

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def get_idle_seconds() -> float:
    """Retorna quantos segundos o usuário está sem mover mouse ou teclar."""
    try:
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return max(millis / 1000.0, 0.0)
    except Exception as e:
        logger.warning("get_idle_seconds falhou: %s — assumindo idle=0", e)
        return 0.0


# ── Janela ativa ───────────────────────────────────────────────────────────────

def get_active_window() -> str:
    try:
        import win32gui
        title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
        return title if title.strip() else "Desconhecido"
    except Exception as e:
        logger.error("Erro ao obter janela ativa: %s", e)
        return "Desconhecido"


# ── Loop principal ─────────────────────────────────────────────────────────────

def track():
    _check_dependencies()

    print("🔍 Rastreamento iniciado... (CTRL+C para parar)")
    print(f"   Banco: {DB_NAME}")
    print(f"   Polling: {POLL_INTERVAL}s | AFK após: {AFK_THRESHOLD}s")
    print(f"   Janela atual: {get_active_window()}\n")

    with sqlite3.connect(DB_NAME, timeout=30.0) as conn:
        init_db(conn)

        last_audio_app = last_audio_ctx = last_audio_seen = None

        # Estado AFK
        afk_start: datetime | None = None
        is_afk = False

        # Contador de logs para feedback visual
        log_count = 0

        while True:
            try:
                now = datetime.now()
                idle = get_idle_seconds()

                title = get_active_window()
                app, context = split_app_context(title)

                logger.debug("Poll: idle=%.1fs | app=%s | title=%s", idle, app, title[:60])

                # ── Suprime AFK enquanto jogos estão em foco ──────────────────────
                # Jogos fullscreen (especialmente com anti-cheat como Vanguard)
                # podem bloquear GetLastInputInfo, gerando falso AFK
                FULLSCREEN_GAMES = {
                    "Valorant",
                    "League of Legends",
                    "Counter-Strike 2",
                    "Counter-Strike: Source",
                    "Risk of Rain 2",
                    "Risk of Rain",
                    "Slay the Spire",
                    "Bloons TD 6",
                    "Bloons TD 5",
                    "Hollow Knight: Silksong",
                    "Hollow Knight",
                    "Megabonk",
                    "Nova Lands",
                    "Rayman Legends",
                    "Rayman Origins",
                    "Stardew Valley",
                    "Terraria",
                    "The Gnorp Apologue",
                    "KOF '97 Global Match",
                    "tModLoader",
                    "Tribes of Midgard",
                    "Vampire Survivors",
                }
                if app in FULLSCREEN_GAMES:
                    idle = 0.0

                # ── Transição ATIVO → AFK ──────────────────────────────────────
                if not is_afk and idle >= AFK_THRESHOLD:
                    is_afk = True
                    afk_start = now
                    print(f"💤 AFK detectado às {now.strftime('%H:%M:%S')} (idle={idle:.0f}s)")

                # ── Transição AFK → ATIVO ──────────────────────────────────────
                elif is_afk and idle < AFK_THRESHOLD:
                    is_afk = False
                    if afk_start:
                        save_afk_session(conn, afk_start, now)
                        duration = (now - afk_start).total_seconds()
                        m, s = int(duration // 60), int(duration % 60)
                        print(f"✅ Voltou às {now.strftime('%H:%M:%S')} — AFK por {m}min {s}s")
                    afk_start = None
                    last_audio_app = last_audio_ctx = last_audio_seen = None

                # ── Se AFK: não registra logs de tela/áudio ────────────────────
                if is_afk:
                    if afk_start:
                        afk_duration = (now - afk_start).total_seconds()
                        if afk_duration >= AFK_AUDIO_CUTOFF:
                            last_audio_app = last_audio_ctx = last_audio_seen = None
                    time.sleep(POLL_INTERVAL)
                    continue

                # ── Rastreamento normal (usuário ativo) ────────────────────────
                save_log(conn, "screen", app, context, now)
                log_count += 1

                # Feedback visual a cada 12 logs (~1 min)
                if log_count % 2 == 0:
                    print(f"[{now.strftime('%H:%M:%S')}] ✍️  {log_count} logs gravados | app atual: {app}")

                audio_in_focus = is_audio_app(app)
                active_audio_proc = get_active_audio_process(AUDIO_PROCESS_KEYWORDS)

                if audio_in_focus:
                    last_audio_app = app
                    last_audio_ctx = context
                    last_audio_seen = now
                    save_log(conn, "audio", last_audio_app, last_audio_ctx, now)

                elif active_audio_proc:
                    if not last_audio_app or active_audio_proc.lower() not in last_audio_app.lower():
                        # Tenta capturar o título real da janela em segundo plano
                        bg_app, bg_title = get_window_title_by_process(AUDIO_PROCESS_KEYWORDS)
                        last_audio_app = bg_app or active_audio_proc
                        last_audio_ctx = bg_title or "Tocando em segundo plano"
                    else:
                        # Atualiza o contexto a cada poll (o vídeo pode ter mudado)
                        bg_app, bg_title = get_window_title_by_process(AUDIO_PROCESS_KEYWORDS)
                        if bg_title:
                            last_audio_ctx = bg_title
                    last_audio_seen = now
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
                if is_afk and afk_start:
                    save_afk_session(conn, afk_start, datetime.now())
                print(f"\n⏹️  Rastreamento encerrado. Total de logs gravados: {log_count}")
                break
            except Exception as e:
                logger.error("Erro no loop principal: %s", e, exc_info=True)
                print(f"⚠️  Erro: {e} — continuando em {POLL_INTERVAL}s...")
                time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    track()