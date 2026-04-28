import sqlite3
from collections import defaultdict
from datetime import datetime, date

from classifier import classify_context

DB_NAME = "tracker.db"

# Pausa máxima entre dois logs consecutivos para considerar tempo ativo.
# Acima disso assume-se que o PC foi desligado / entrou em sleep.
MAX_GAP_SECONDS = 300   # 5 minutos

# Pausa mínima — abaixo disso o intervalo é ruído de medição
MIN_GAP_SECONDS = 5


# ── Coleta de dados ────────────────────────────────────────────────────────────

def _fetch_logs(filter_date: date | None = None) -> list[tuple]:
    """Retorna os logs do banco, opcionalmente filtrados por data."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if filter_date:
        cursor.execute(
            "SELECT type, app, context, timestamp FROM logs "
            "WHERE date(timestamp) = ? ORDER BY timestamp",
            (filter_date.isoformat(),),
        )
    else:
        cursor.execute(
            "SELECT type, app, context, timestamp FROM logs ORDER BY timestamp"
        )

    rows = cursor.fetchall()
    conn.close()
    return rows


def get_screen_time(filter_date: date | None = None) -> dict[str, float]:
    """Retorna {app: segundos} de tempo em tela."""
    rows = _fetch_logs(filter_date)
    screen_time: dict[str, float] = defaultdict(float)

    for i in range(len(rows) - 1):
        log_type, app, context, t1 = rows[i]
        _, _, _, t2 = rows[i + 1]

        diff = (datetime.fromisoformat(t2) - datetime.fromisoformat(t1)).total_seconds()

        if not (MIN_GAP_SECONDS <= diff <= MAX_GAP_SECONDS):
            continue

        if log_type == "screen":
            screen_time[app] += diff

    return dict(screen_time)


def get_audio_time(filter_date: date | None = None) -> tuple[dict[str, float], dict[str, float]]:
    """Retorna ({categoria: segundos}, {contexto: segundos}) de consumo de áudio."""
    rows = _fetch_logs(filter_date)
    audio_time: dict[str, float] = defaultdict(float)
    audio_details: dict[str, float] = defaultdict(float)

    for i in range(len(rows) - 1):
        log_type, app, context, t1 = rows[i]
        _, _, _, t2 = rows[i + 1]

        diff = (datetime.fromisoformat(t2) - datetime.fromisoformat(t1)).total_seconds()

        if not (MIN_GAP_SECONDS <= diff <= MAX_GAP_SECONDS):
            continue

        if log_type == "audio":
            category = classify_context(app, context)
            audio_time[category] += diff
            audio_details[context] += diff

    return dict(audio_time), dict(audio_details)


# ── Apresentação ───────────────────────────────────────────────────────────────

def _fmt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}min"


def print_report(filter_date: date | None = None, top_audio: int = 10):
    label = filter_date.strftime("%d/%m/%Y") if filter_date else "todo o período"
    print(f"\n📅 Relatório: {label}\n{'─' * 50}")

    # 🖥️ Tela
    screen = get_screen_time(filter_date)
    print("\n🖥️  Tempo em Tela:\n")
    for app, secs in sorted(screen.items(), key=lambda x: x[1], reverse=True):
        print(f"  {app:22} → {_fmt(secs)}")

    # 🎧 Áudio por categoria
    audio, details = get_audio_time(filter_date)
    print("\n🎧 Consumo de Áudio:\n")
    for cat, secs in sorted(audio.items(), key=lambda x: x[1], reverse=True):
        print(f"  {cat:25} → {_fmt(secs)}")

    # 🎥 Detalhamento
    print(f"\n🎥 Detalhamento do Áudio (Top {top_audio}):\n")
    for ctx, secs in sorted(details.items(), key=lambda x: x[1], reverse=True)[:top_audio]:
        print(f"  {ctx[:68]:68} → {_fmt(secs)}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Relatório do tracker de tempo")
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        metavar="YYYY-MM-DD",
        help="Filtra por uma data específica (ex: 2025-04-20). Omitir mostra tudo.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        metavar="N",
        help="Quantos itens mostrar no detalhamento de áudio (padrão: 10)",
    )
    args = parser.parse_args()

    print_report(filter_date=args.date, top_audio=args.top)