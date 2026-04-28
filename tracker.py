import time
import sqlite3
import logging
from datetime import datetime

import win32gui

from classifier import split_app_context, is_audio_app

DB_NAME = "tracker.db"

# Tempo (segundos) que um áudio continua contando se você mudar a janela de foco
AUDIO_BACKGROUND_TIMEOUT = 15

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    filename="tracker.log",
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ── Banco de Dados ─────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection):
    """Cria a tabela de logs se não existir."""
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
    """Salva uma entrada de log no banco de dados."""
    conn.execute(
        "INSERT INTO logs (type, app, context, timestamp) VALUES (?, ?, ?, ?)",
        (log_type, app, context, timestamp.isoformat()),
    )
    conn.commit()


# ── Janela Ativa ───────────────────────────────────────────────────────────────

def get_active_window() -> str:
    """Captura o título da janela que está atualmente em primeiro plano."""
    try:
        window = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(window)
        return title if title else "Desktop"
    except Exception as e:
        logging.error(f"Erro ao capturar janela: {e}")
        return "Desconhecido"


# ── Loop Principal ─────────────────────────────────────────────────────────────

def track():
    print("🔍 Rastreamento total iniciado...")
    print("🖥️  Qualquer janela em foco (YouTube, Audiobook PRO, etc) contará como TELA.")
    print("🎧 Áudio será registrado em paralelo. Pressione CTRL+C para parar.")

    with sqlite3.connect(DB_NAME) as conn:
        init_db(conn)

        last_audio: tuple[str, str] | None = None
        last_audio_time: datetime | None = None

        while True:
            try:
                # 1. Obtém a janela que o usuário está vendo agora
                title = get_active_window()
                app, context = split_app_context(title)
                now = datetime.now()

                # 2. REGISTRO DE TELA (Independente do tipo de app)
                # Se está na tela, é registrado como 'screen'. 
                # Isso corrige o problema do YouTube e Audiobook PRO não contarem tela.
                save_log(conn, "screen", app, context, now)

                # 3. REGISTRO DE ÁUDIO
                # Se o app atual for categorizado como áudio, registra como 'audio'
                if is_audio_app(app):
                    last_audio = (app, context)
                    last_audio_time = now
                    save_log(conn, "audio", app, context, now)
                
                # Se o usuário mudou de janela, mas o áudio anterior ainda está no timeout
                elif last_audio and last_audio_time:
                    elapsed = (now - last_audio_time).total_seconds()
                    if elapsed < AUDIO_BACKGROUND_TIMEOUT:
                        save_log(conn, "audio", last_audio[0], last_audio[1], now)

                # Intervalo de amostragem
                time.sleep(5)

            except KeyboardInterrupt:
                print("\n⏹️  Rastreamento encerrado.")
                break
            except Exception as e:
                logging.error(f"Erro no loop de rastreamento: {e}")
                time.sleep(1)

if __name__ == "__main__":
    track()