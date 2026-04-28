import sqlite3
from collections import defaultdict
from datetime import datetime, date

from classifier import classify_context

DB_NAME = "tracker.db"
MAX_GAP_SECONDS = 300
MIN_GAP_SECONDS = 5


def _fetch_logs(filter_date: date | None = None) -> list[tuple]:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if filter_date:
        cursor.execute(
            "SELECT type, app, context, timestamp FROM logs "
            "WHERE date(timestamp) = ? ORDER BY timestamp",
            (filter_date.isoformat(),),
        )
    else:
        cursor.execute("SELECT type, app, context, timestamp FROM logs ORDER BY timestamp")
    rows = cursor.fetchall()
    conn.close()
    return rows


def _fetch_afk_sessions(filter_date: date | None = None) -> list[tuple]:
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Verifica se a tabela existe
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='afk_sessions'")
    if not cursor.fetchone():
        conn.close()
        return []
    if filter_date:
        cursor.execute(
            "SELECT started_at, ended_at, duration_seconds FROM afk_sessions "
            "WHERE date(started_at) = ? ORDER BY started_at",
            (filter_date.isoformat(),),
        )
    else:
        cursor.execute(
            "SELECT started_at, ended_at, duration_seconds FROM afk_sessions ORDER BY started_at"
        )
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_screen_time(filter_date: date | None = None) -> dict[str, float]:
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


def get_afk_summary(filter_date: date | None = None) -> dict:
    sessions = _fetch_afk_sessions(filter_date)
    if not sessions:
        return {"total_seconds": 0, "count": 0, "longest_seconds": 0, "sessions": []}
    total = sum(s[2] for s in sessions if s[2])
    longest = max(s[2] for s in sessions if s[2])
    return {
        "total_seconds": total,
        "count": len(sessions),
        "longest_seconds": longest,
        "sessions": sessions,
    }


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

    # 💤 AFK
    afk = get_afk_summary(filter_date)
    print(f"\n💤 Tempo AFK:\n")
    if afk["count"] == 0:
        print("  Nenhuma sessão AFK registrada.")
    else:
        print(f"  {'Total AFK':22} → {_fmt(afk['total_seconds'])}")
        print(f"  {'Sessões':22} → {afk['count']}")
        print(f"  {'Maior sessão':22} → {_fmt(afk['longest_seconds'])}")
        print(f"\n  Detalhamento:\n")
        for started, ended, duration in afk["sessions"]:
            if not duration:
                continue
            start_fmt = datetime.fromisoformat(started).strftime("%H:%M:%S")
            end_fmt   = datetime.fromisoformat(ended).strftime("%H:%M:%S") if ended else "em curso"
            print(f"  {start_fmt} → {end_fmt}  ({_fmt(duration)})")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Relatório do tracker de tempo")
    parser.add_argument("--date", type=lambda s: date.fromisoformat(s), default=None,
        metavar="YYYY-MM-DD")
    parser.add_argument("--top", type=int, default=10, metavar="N")
    args = parser.parse_args()
    print_report(filter_date=args.date, top_audio=args.top)