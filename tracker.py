import time
import sqlite3
import logging
from datetime import datetime

import win32gui

from classifier import split_app_context, is_audio_app

DB_NAME = "tracker.db"

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    filename="tracker.log",
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ── Banco de dados ─────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            type      TEXT,
            app       TEXT,
            context   TEXT,
            timestamp DATETIME
        )
    """)
    conn.commit()


def save_log(conn: sqlite3.Connection, log_type: str, app: str, context: str, timestamp: datetime):
    conn.execute(
        "INSERT INTO logs (type, app, context, timestamp) VALUES (?, ?, ?, ?)",
        (log_type, app, context, timestamp.isoformat()),
    )
    conn.commit()


# ── Janela ativa ───────────────────────────────────────────────────────────────

def get_active_window() -> str:
    try:
        window = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(window)
        return title if title else "Desconhecido"
    except Exception as e:
        logging.error("Erro ao obter janela ativa: %s", e)
        return "Desconhecido"


# ── Loop principal ─────────────────────────────────────────────────────────────

# Tempo máximo em segundos que o áudio em background é mantido após perder o foco
AUDIO_BACKGROUND_TIMEOUT = 15


def track():
    print("🔍 Rastreamento iniciado... (CTRL+C para parar)")

    # ✅ Conexão aberta uma única vez — muito mais eficiente
    with sqlite3.connect(DB_NAME) as conn:
        init_db(conn)

        last_audio: tuple[str, str] | None = None
        last_audio_time: datetime | None = None

        while True:
            try:
                title = get_active_window()
                app, context = split_app_context(title)
                now = datetime.now()

                # 🖥️ Tela
                save_log(conn, "screen", app, context, now)

                # 🎧 Áudio
                if is_audio_app(app):
                    last_audio = (app, context)
                    last_audio_time = now
                    save_log(conn, "audio", app, context, now)

                elif last_audio and last_audio_time:
                    # ✅ fix: .total_seconds() em vez de .seconds
                    elapsed = (now - last_audio_time).total_seconds()
                    if elapsed < AUDIO_BACKGROUND_TIMEOUT:
                        save_log(conn, "audio", last_audio[0], last_audio[1], now)

                time.sleep(5)

            except KeyboardInterrupt:
                print("\n⏹️  Rastreamento encerrado.")
                break
            except Exception as e:
                logging.error("Erro inesperado no loop principal: %s", e)
                time.sleep(5)


if __name__ == "__main__":
    track()