"""
youtube_videos.py
-----------------
Lista todos os vídeos do YouTube assistidos com o tempo total em cada um.

Usa apenas logs de TELA (screen) para medir o tempo — evita dupla contagem
com áudio em segundo plano e elimina falsos positivos do background timeout.

Uso:
    python youtube_videos.py                    → todos os dias
    python youtube_videos.py --date 2026-04-29  → só um dia
    python youtube_videos.py --min 60           → só vídeos com >= 60 segundos
"""

import sqlite3
import argparse
from datetime import datetime, date
from collections import defaultdict

DB_NAME = "tracker.db"
MAX_GAP = 300   # segundos (igual ao tracker)
MIN_GAP = 5


def clean_title(ctx: str) -> str:
    for suffix in [
        " - YouTube - Brave",
        " - YouTube - Google Chrome",
        " - YouTube - Firefox",
        " - YouTube",
        " - Brave",
        " - Google Chrome",
        " - Firefox",
    ]:
        if ctx.endswith(suffix):
            ctx = ctx[: -len(suffix)]
    return ctx.strip()


def fmt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m:02d}min"
    if m > 0:
        return f"{m}min {s:02d}s"
    return f"{s}s"


def fetch_youtube_screen_logs(filter_date: date | None) -> list[tuple]:
    """Busca apenas logs de TELA do YouTube — o usuário estava de fato na janela."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if filter_date:
        c.execute(
            "SELECT context, timestamp FROM logs "
            "WHERE app='YouTube' AND type='screen' AND date(timestamp)=? "
            "ORDER BY timestamp",
            (filter_date.isoformat(),),
        )
    else:
        c.execute(
            "SELECT context, timestamp FROM logs "
            "WHERE app='YouTube' AND type='screen' "
            "ORDER BY timestamp"
        )
    rows = c.fetchall()
    conn.close()
    return rows


def compute_video_times(rows: list[tuple], min_seconds: int) -> list[tuple]:
    video_time: dict[str, float] = defaultdict(float)

    for i in range(len(rows) - 1):
        ctx, t1 = rows[i]
        _, t2 = rows[i + 1]
        t1 = datetime.fromisoformat(t1)
        t2 = datetime.fromisoformat(t2)
        diff = (t2 - t1).total_seconds()

        if not (MIN_GAP <= diff <= MAX_GAP):
            continue

        title = clean_title(ctx)
        video_time[title] += diff

    result = [
        (title, secs)
        for title, secs in video_time.items()
        if secs >= min_seconds
    ]
    return sorted(result, key=lambda x: -x[1])


def print_report(videos: list[tuple], filter_date: date | None):
    label = filter_date.strftime("%d/%m/%Y") if filter_date else "todos os dias"
    total = sum(s for _, s in videos)

    print(f"\n{'─' * 70}")
    print(f"  🎬  Vídeos do YouTube assistidos — {label}")
    print(f"{'─' * 70}")
    print(f"  {'TEMPO':>10}   TÍTULO")
    print(f"{'─' * 70}")

    for title, secs in videos:
        title_display = title[:54] + "…" if len(title) > 55 else title
        print(f"  {fmt(secs):>10}   {title_display}")

    print(f"{'─' * 70}")
    print(f"  {'TOTAL':>10}   {len(videos)} vídeo(s) — {fmt(total)}")
    print(f"{'─' * 70}\n")


def main():
    parser = argparse.ArgumentParser(description="Vídeos do YouTube assistidos")
    parser.add_argument(
        "--date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        metavar="YYYY-MM-DD",
        help="Filtrar por data (ex: 2026-04-29)",
    )
    parser.add_argument(
        "--min",
        type=int,
        default=30,
        metavar="SEGUNDOS",
        help="Tempo mínimo em segundos para incluir (padrão: 30)",
    )
    args = parser.parse_args()

    rows = fetch_youtube_screen_logs(args.date)
    videos = compute_video_times(rows, min_seconds=args.min)

    if not videos:
        print("\nNenhum vídeo encontrado com os filtros aplicados.\n")
        return

    print_report(videos, args.date)


if __name__ == "__main__":
    main()