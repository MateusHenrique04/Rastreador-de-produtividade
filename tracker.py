# -*- coding: utf-8 -*-
"""
tracker.py — v3
---------------

Rastreador de produtividade (Windows). Mesma tabela `logs` e mesmas colunas da
versão anterior, então dashboard_live.py, dashboard.py, report.py,
Reclassificar.py etc. continuam funcionando sem alteração.

O QUE MUDOU
  * Em vez de gravar 1-2 linhas a cada 5s, grava só quando algo MUDA
    (app/contexto) e um "batimento" a cada HEARTBEAT_SECONDS enquanto nada muda
    (também na virada de hora/dia, para os gráficos ficarem corretos).
    O tempo calculado é o mesmo; o banco cresce ~90% menos.
  * Marcadores de fim de trecho (END_MARKERS): quando a tela/áudio para (AFK,
    áudio que acabou, encerramento), grava uma linha do tipo "screen_end" /
    "audio_end". Antes, qualquer intervalo de até 30 min entre duas linhas era
    somado ao último app/áudio (AFK curto e pausas de áudio contavam como uso).
    O dashboard_live.py v4 entende os marcadores; scripts antigos simplesmente
    ignoram essas linhas. Para voltar ao comportamento antigo: END_MARKERS = False.
  * Áudio em segundo plano agora expira após AUDIO_BACKGROUND_TIMEOUT s sem áudio
    detectado (antes last_audio_seen era renovado dentro da própria tolerância).
  * Corrigido: GetTickCount ficava negativo/errado após ~25 dias ligado (AFK falso).
  * Impede duas instâncias rodando ao mesmo tempo (linhas duplicadas).
  * Encerramento limpo (CTRL+C, fechar a janela, kill): fecha os trechos abertos
    e salva a sessão AFK em andamento.
  * tracker.log com rotação (antes crescia sem limite com nível DEBUG).
  * Erros de gravação não perdem dados: as linhas ficam em fila e são
    regravadas no próximo ciclo.

Uso:  python tracker.py        (CTRL+C para parar)
"""
from __future__ import annotations

import ctypes
import logging
import os
import signal
import socket
import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)  # o classifier.py lê rules.json do diretório atual
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from classifier import (  # noqa: E402
    split_app_context,
    is_audio_app,
    get_active_audio_process,
    get_window_title_by_process,
)

# ── Configuração ───────────────────────────────────────────────────────────────
DB_NAME = os.path.join(BASE_DIR, "tracker.db")

POLL_INTERVAL = 5                 # segundos entre verificações
HEARTBEAT_SECONDS = 60            # grava uma linha a cada N s se nada mudou
AFK_THRESHOLD = 5 * 60            # segundos sem input para considerar AFK
AUDIO_BACKGROUND_TIMEOUT = 60     # segundos sem áudio detectado antes de parar de contar
AUDIO_PROCESS_KEYWORDS = ["chrome", "brave", "audiobookplayer", "firefox", "spotify"]
END_MARKERS = True                # grava "screen_end"/"audio_end" quando um trecho termina
LOCK_PORT = 47653                 # trava de instância única (localhost)

# Jogos fullscreen (principalmente com anti-cheat, como o Vanguard) podem bloquear
# GetLastInputInfo e gerar falso AFK; enquanto estiverem em foco, idle = 0.
GAME_APPS = frozenset({
    "Valorant", "League of Legends", "Counter-Strike 2", "Counter-Strike: Source",
    "Risk of Rain 2", "Risk of Rain", "Slay the Spire", "Bloons TD 6", "Bloons TD 5",
    "Hollow Knight: Silksong", "Hollow Knight", "Megabonk", "Nova Lands",
    "Rayman Legends", "Rayman Origins", "Stardew Valley", "Terraria",
    "The Gnorp Apologue", "KOF '97 Global Match", "tModLoader",
    "Tribes of Midgard", "Vampire Survivors",
})

logger = logging.getLogger("tracker")


def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(os.path.join(BASE_DIR, "tracker.log"),
                             maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root.addHandler(fh)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.WARNING)
    root.addHandler(console)


# ── Banco de dados ─────────────────────────────────────────────────────────────
class Store:
    """Mesmo esquema da versão anterior (logs + afk_sessions)."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path, timeout=30.0)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            app TEXT NOT NULL,
            context TEXT NOT NULL,
            timestamp DATETIME NOT NULL)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS afk_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at DATETIME NOT NULL,
            ended_at DATETIME,
            duration_seconds REAL)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_afk_started ON afk_sessions(started_at)")
        self.conn.commit()

    def add_logs(self, rows):
        """rows: [(type, app, context, datetime)] — uma transação para o lote todo."""
        if not rows:
            return
        with self.conn:
            self.conn.executemany(
                "INSERT INTO logs (type, app, context, timestamp) VALUES (?, ?, ?, ?)",
                [(t, a, c, ts.isoformat()) for t, a, c, ts in rows],
            )

    def add_afk(self, started_at: datetime, ended_at: datetime):
        duration = (ended_at - started_at).total_seconds()
        with self.conn:
            self.conn.execute(
                "INSERT INTO afk_sessions (started_at, ended_at, duration_seconds) VALUES (?, ?, ?)",
                (started_at.isoformat(), ended_at.isoformat(), duration),
            )
        return duration

    def close(self):
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass


# ── Fluxo de logs com gravação por mudança ─────────────────────────────────────
class Stream:
    """
    Controla um tipo de log ("screen" ou "audio").

    Os dashboards calculam o tempo como a diferença entre linhas consecutivas
    (a linha i vale de timestamp[i] até timestamp[i+1]). Por isso:
      * troca de app/contexto  -> uma linha nova; ela já encerra o trecho anterior;
      * nada mudou             -> batimento a cada `heartbeat` s, na virada de hora
                                  e no último ciclo antes da meia-noite;
      * o fluxo terminou       -> linha de fim no ÚLTIMO instante em que ainda foi
                                  visto. Com end_markers o tipo é "<kind>_end", o
                                  que impede que a pausa seguinte seja somada.
    """

    def __init__(self, kind, heartbeat, poll=POLL_INTERVAL, end_markers=END_MARKERS):
        self.kind = kind
        self.heartbeat = heartbeat
        self.poll = poll
        self.end_markers = end_markers
        self.key = None            # (app, context) atual
        self.last_row_ts = None    # timestamp da última linha gravada
        self.last_seen_ts = None   # último ciclo em que a chave foi observada

    def _due(self, now):
        last = self.last_row_ts
        # o próximo ciclo pode cair no dia seguinte? (a diferença que cruza a
        # meia-noite é descartada pelos dashboards, então precisamos de uma linha antes)
        next_tick = now + timedelta(seconds=self.poll + 1)
        return ((now - last).total_seconds() >= self.heartbeat
                or now.hour != last.hour
                or now.date() != last.date()
                or next_tick.date() != now.date())

    def update(self, key, now):
        if key is None:
            return self.close()
        rows = []
        if key != self.key:
            rows.append((self.kind, key[0], key[1], now))
            self.key = key
            self.last_row_ts = now
        elif self._due(now):
            rows.append((self.kind, key[0], key[1], now))
            self.last_row_ts = now
        self.last_seen_ts = now
        return rows

    def close(self):
        rows = []
        if self.key is not None:
            app, context = self.key
            if self.end_markers:
                rows.append((self.kind + "_end", app, context, self.last_seen_ts))
            elif self.last_seen_ts != self.last_row_ts:
                rows.append((self.kind, app, context, self.last_seen_ts))
            self.last_row_ts = self.last_seen_ts
            self.key = None
        return rows


# ── Lógica principal (independente de Windows, por isso testável) ──────────────
class Tracker:
    def __init__(self, store, sensors, heartbeat=HEARTBEAT_SECONDS, end_markers=END_MARKERS):
        self.store = store
        self.sensors = sensors
        self.screen = Stream("screen", heartbeat, end_markers=end_markers)
        self.audio = Stream("audio", heartbeat, end_markers=end_markers)
        self.is_afk = False
        self.afk_start = None
        self.last_audio = None        # (app, context)
        self.last_audio_seen = None   # datetime
        self.pending = []             # linhas ainda não gravadas (erro de I/O)
        self.rows_written = 0
        self.ticks = 0

    # -- gravação ---------------------------------------------------------------
    def _write(self, rows):
        self.pending.extend(rows)
        if not self.pending:
            return
        try:
            self.store.add_logs(self.pending)
            self.rows_written += len(self.pending)
            self.pending = []
        except Exception as e:  # noqa: BLE001
            logger.error("Falha ao gravar %d linha(s); tentarei de novo: %s",
                         len(self.pending), e)

    # -- áudio ------------------------------------------------------------------
    def _audio_key(self, app, context, now):
        s = self.sensors
        if s.is_audio_app(app):
            self.last_audio = (app, context)
            self.last_audio_seen = now
            return self.last_audio

        proc = s.active_audio_process(AUDIO_PROCESS_KEYWORDS)
        if proc:
            bg_app, bg_title = s.window_title_by_process(AUDIO_PROCESS_KEYWORDS)
            if self.last_audio is None or proc.lower() not in self.last_audio[0].lower():
                self.last_audio = (bg_app or proc, bg_title or "Tocando em segundo plano")
            elif bg_title:
                # o vídeo/faixa pode ter mudado
                self.last_audio = (self.last_audio[0], bg_title)
            self.last_audio_seen = now
            return self.last_audio

        if self.last_audio is not None and self.last_audio_seen is not None:
            if (now - self.last_audio_seen).total_seconds() < AUDIO_BACKGROUND_TIMEOUT:
                # tolerância: NÃO renova last_audio_seen (era o bug da versão anterior)
                return self.last_audio
            self.last_audio = None
            self.last_audio_seen = None
        return None

    # -- ciclo ------------------------------------------------------------------
    def tick(self, now):
        s = self.sensors
        idle = s.idle_seconds()
        title = s.active_window()
        app, context = s.split(title)
        self.ticks += 1

        if app in GAME_APPS:
            idle = 0.0

        # Transição ATIVO -> AFK
        if not self.is_afk and idle >= AFK_THRESHOLD:
            self.is_afk = True
            self.afk_start = now
            self._write(self.screen.close() + self.audio.close())
            print(f"AFK detectado às {now:%H:%M:%S} (idle={idle:.0f}s)")
            logger.info("AFK iniciado (idle=%.0fs)", idle)

        # Transição AFK -> ATIVO
        elif self.is_afk and idle < AFK_THRESHOLD:
            self.is_afk = False
            if self.afk_start:
                duration = self.store.add_afk(self.afk_start, now)
                m, sec = int(duration // 60), int(duration % 60)
                print(f"Voltou às {now:%H:%M:%S} — AFK por {m}min {sec}s")
                logger.info("AFK encerrado (%dmin %ds)", m, sec)
            self.afk_start = None
            self.last_audio = None
            self.last_audio_seen = None

        if self.is_afk:
            self._write([])  # reenvia pendências, se houver
            return

        rows = self.screen.update((app, context), now)
        rows += self.audio.update(self._audio_key(app, context, now), now)
        self._write(rows)

        if self.ticks % 12 == 0:  # ~1 min
            print(f"[{now:%H:%M:%S}] {self.rows_written} linhas gravadas | app atual: {app}")

    def shutdown(self, now):
        self._write(self.screen.close() + self.audio.close())
        if self.is_afk and self.afk_start:
            self.store.add_afk(self.afk_start, now)
            self.afk_start = None
            self.is_afk = False


def run(tracker, stop, poll=POLL_INTERVAL):
    while not stop.is_set():
        try:
            tracker.tick(datetime.now())
        except Exception as e:  # noqa: BLE001
            logger.error("Erro no ciclo principal: %s", e, exc_info=True)
            print(f"Erro: {e} — continuando em {poll}s...")
        stop.wait(poll)  # dorme DEPOIS de processar (diferença entre linhas fica >= poll)
    tracker.shutdown(datetime.now())


# ── Sensores do Windows ────────────────────────────────────────────────────────
class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class WindowsSensors:
    def __init__(self):
        import win32gui  # noqa: F401
        self._win32gui = win32gui
        self._kernel32 = ctypes.windll.kernel32
        self._kernel32.GetTickCount.restype = ctypes.c_uint32  # sem sinal (evita valor negativo)
        self._user32 = ctypes.windll.user32

    def idle_seconds(self):
        try:
            lii = _LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
            if not self._user32.GetLastInputInfo(ctypes.byref(lii)):
                return 0.0
            millis = (self._kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF
            return millis / 1000.0
        except Exception as e:  # noqa: BLE001
            logger.warning("idle_seconds falhou: %s — assumindo idle=0", e)
            return 0.0

    def active_window(self):
        try:
            title = self._win32gui.GetWindowText(self._win32gui.GetForegroundWindow())
            return title if title.strip() else "Desconhecido"
        except Exception as e:  # noqa: BLE001
            logger.error("Erro ao obter janela ativa: %s", e)
            return "Desconhecido"

    def split(self, title):
        return split_app_context(title)

    def is_audio_app(self, app):
        return is_audio_app(app)

    def active_audio_process(self, keywords):
        return get_active_audio_process(keywords)

    def window_title_by_process(self, keywords):
        return get_window_title_by_process(keywords)


# ── Infra: instância única e encerramento limpo ────────────────────────────────
def acquire_single_instance(port=LOCK_PORT):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def install_stop_handlers(stop, done):
    def _sig(*_):
        stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _sig)
            except Exception:  # noqa: BLE001
                pass

    if os.name == "nt":
        try:
            handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

            def _ctrl(event):
                stop.set()
                done.wait(4.0)  # o Windows encerra o processo ~5s após fechar a janela
                return 1

            handler = handler_type(_ctrl)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, 1)
            return handler  # manter a referência viva
        except Exception as e:  # noqa: BLE001
            logger.warning("Não consegui instalar o handler de fechamento de janela: %s", e)
    return None


def _check_dependencies():
    missing = []
    try:
        import win32gui  # noqa: F401
    except ImportError:
        missing.append("pywin32 -> pip install pywin32")
    if missing:
        print("Dependências faltando:")
        for m in missing:
            print(f"  - {m}")
        print("\nRode: pip install pywin32 pycaw psutil")
        print("Depois: python Scripts/pywin32_postinstall.py -install")
        sys.exit(1)


def main():
    if os.name != "nt":
        print("Este tracker só funciona no Windows (usa pywin32 e GetLastInputInfo).")
        sys.exit(1)
    try:  # emojis/acentos no console do Windows
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    _check_dependencies()
    setup_logging()

    lock = acquire_single_instance()
    if lock is None:
        print("O tracker já está rodando (outra janela/processo). Fechando esta.")
        sys.exit(1)

    store = Store(DB_NAME)
    sensors = WindowsSensors()
    tracker = Tracker(store, sensors)

    stop, done = threading.Event(), threading.Event()
    _keep = install_stop_handlers(stop, done)  # noqa: F841

    print("Rastreamento iniciado... (CTRL+C para parar)")
    print(f"  Banco: {DB_NAME}")
    print(f"  Verificação: {POLL_INTERVAL}s | batimento: {HEARTBEAT_SECONDS}s | AFK após: {AFK_THRESHOLD}s")
    print(f"  Janela atual: {sensors.active_window()}\n")
    logger.info("Tracker iniciado")

    try:
        run(tracker, stop)
    finally:
        store.close()
        logger.info("Tracker encerrado (%d linhas nesta sessão)", tracker.rows_written)
        print(f"\nRastreamento encerrado. Linhas gravadas nesta sessão: {tracker.rows_written}")
        done.set()
        lock.close()


if __name__ == "__main__":
    main()
