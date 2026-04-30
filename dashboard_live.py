"""
dashboard_live.py
-----------------
Servidor Flask local que serve o dashboard de produtividade e atualiza
sozinho a cada 30 segundos lendo os dados frescos de tracker.db.

Como usar:
    1) pip install flask
    2) Coloque este arquivo + a pasta templates/ na MESMA pasta onde estao
       tracker.db, classifier.py, rules.json e dashboard.py.
    3) python dashboard_live.py
    4) Abra http://localhost:5000 no navegador (ja abre sozinho).

Pronto — a pagina vai buscar dados novos a cada 30s sem piscar e sem
precisar regenerar HTML.
"""

import json
import os
import sqlite3
import sys
import webbrowser
from collections import defaultdict
from datetime import date, datetime, timedelta
from threading import Timer

from flask import Flask, jsonify, render_template

# ── Configuracao ──────────────────────────────────────────────────────────────
DB_NAME = "tracker.db"
MAX_GAP = 300
MIN_GAP = 5
HOST = "127.0.0.1"
PORT = 5000

CATEGORY_COLORS = {
    "Estudo": "#4ade80",
    "Aprendizado leve": "#60a5fa",
    "Entretenimento": "#f87171",
    "Produtivo": "#a78bfa",
    "YouTube (nao classificado)": "#fb923c",
    "Outros": "#94a3b8",
}

APP_COLORS = [
    "#a78bfa", "#60a5fa", "#4ade80", "#fb923c", "#f87171", "#fbbf24",
    "#94a3b8", "#34d399", "#38bdf8", "#c084fc", "#f472b6", "#facc15",
    "#e879f9", "#2dd4bf",
]

WEEK_COLORS = ["#a78bfa", "#60a5fa", "#4ade80", "#fb923c", "#f87171", "#fbbf24", "#94a3b8"]
WEEKDAY_PT = {0: "Seg", 1: "Ter", 2: "Qua", 3: "Qui", 4: "Sex", 5: "Sab", 6: "Dom"}

TOP_COLORS = [
    "#4ade80", "#60a5fa", "#a78bfa", "#f87171", "#fb923c", "#fbbf24",
    "#34d399", "#38bdf8", "#c084fc", "#f472b6", "#facc15", "#94a3b8",
]


# ── Classificador (importa do projeto existente) ──────────────────────────────
def classify(app, context):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from classifier import classify_context
        return classify_context(app, context)
    except Exception:
        return "Outros"


# ── Acesso ao banco ───────────────────────────────────────────────────────────
def fetch_all_data():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT type, app, context, timestamp FROM logs ORDER BY timestamp")
    rows = c.fetchall()
    conn.close()
    return rows


def fetch_afk_data():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='afk_sessions'")
    if not c.fetchone():
        conn.close()
        return {}
    c.execute(
        "SELECT date(started_at), SUM(duration_seconds) FROM afk_sessions "
        "WHERE duration_seconds IS NOT NULL GROUP BY date(started_at)"
    )
    rows = c.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}


# ── Processamento (igual ao dashboard.py original) ────────────────────────────
def process_data(rows):
    screen_by_date = defaultdict(lambda: defaultdict(float))
    audio_by_date = defaultdict(lambda: defaultdict(float))
    audio_details = defaultdict(float)
    hour_buckets = defaultdict(lambda: defaultdict(float))
    weekly_hours = defaultdict(lambda: defaultdict(float))

    today = date.today().isoformat()
    _today = date.today()
    week_monday = _today - timedelta(days=_today.weekday())
    week_start = week_monday.isoformat()

    screen_rows = [(app, ctx, ts) for lt, app, ctx, ts in rows if lt == "screen"]
    audio_rows = [(app, ctx, ts) for lt, app, ctx, ts in rows if lt == "audio"]

    audio_ts_seen = set()

    def _accumulate(log_rows, is_audio):
        for i in range(len(log_rows) - 1):
            app, context, t1 = log_rows[i]
            _, _, t2 = log_rows[i + 1]
            t1 = datetime.fromisoformat(t1)
            t2 = datetime.fromisoformat(t2)
            diff = (t2 - t1).total_seconds()

            day = t1.date().isoformat()
            day2 = t2.date().isoformat()

            if day != day2:
                continue
            if not (MIN_GAP <= diff <= MAX_GAP):
                continue

            hour = t1.strftime("%H:00")

            if not is_audio:
                screen_by_date[day][app] += diff
                if day == today:
                    hour_buckets[hour][app] += diff
                if day >= week_start:
                    weekly_hours[day][hour] += diff

                CONTENT_APPS = {"YouTube", "Estudo (Audiobook)"}
                if app in CONTENT_APPS and (app, t1.isoformat()) not in audio_ts_seen:
                    parts = context.split(" - ")
                    ctx_clean = " - ".join(
                        p for p in parts
                        if p.strip().lower() not in {"youtube", "youtube music", "youtube premium"}
                    )
                    ctx_clean = ctx_clean.strip(" -")[:80] or context[:80]
                    audio_details[ctx_clean] += diff
                    cat = classify(app, context)
                    audio_by_date[day][cat] += diff
            else:
                if day == today:
                    hour_buckets[hour][app] += diff
                if day >= week_start:
                    weekly_hours[day][hour] += diff

                parts = context.split(" - ")
                ctx_clean = " - ".join(
                    p for p in parts
                    if p.strip().lower() not in {"youtube", "youtube music", "youtube premium"}
                )
                ctx_clean = ctx_clean.strip(" -")[:80] or context[:80]
                audio_details[ctx_clean] += diff
                cat = classify(app, context)
                audio_by_date[day][cat] += diff
                audio_ts_seen.add((app, t1.isoformat()))

    _accumulate(audio_rows, is_audio=True)
    _accumulate(screen_rows, is_audio=False)

    return screen_by_date, audio_by_date, audio_details, hour_buckets, weekly_hours


def fmt(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}min"


# ── Monta o payload JSON ──────────────────────────────────────────────────────
def build_payload():
    rows = fetch_all_data()
    afk_by_date = fetch_afk_data()
    screen_by_date, audio_by_date, audio_details, hour_buckets, weekly_hours = process_data(rows)

    all_days = sorted(set(list(screen_by_date.keys()) + list(audio_by_date.keys())))
    screen_apps = sorted({app for d in screen_by_date.values() for app in d})
    audio_cats = sorted({cat for d in audio_by_date.values() for cat in d})

    screen_datasets = [
        {
            "label": app,
            "data": [round(screen_by_date[d].get(app, 0) / 60, 1) for d in all_days],
            "backgroundColor": APP_COLORS[idx % len(APP_COLORS)],
            "borderRadius": 6,
        }
        for idx, app in enumerate(screen_apps)
    ]

    audio_datasets = [
        {
            "label": cat,
            "data": [round(audio_by_date[d].get(cat, 0) / 60, 1) for d in all_days],
            "backgroundColor": CATEGORY_COLORS.get(cat, "#94a3b8"),
            "borderRadius": 6,
        }
        for cat in audio_cats
    ]

    top_audio = sorted(audio_details.items(), key=lambda x: x[1], reverse=True)[:12]
    top_labels = [t[0][:45] + ("..." if len(t[0]) > 45 else "") for t in top_audio]
    top_values = [round(t[1] / 60, 1) for t in top_audio]
    top_colors = TOP_COLORS[:len(top_audio)]

    all_hours = [f"{str(h).zfill(2)}:00" for h in range(24)]
    hour_apps = sorted({app for h in hour_buckets.values() for app in h})
    hour_datasets = [
        {
            "label": app,
            "data": [round(hour_buckets[h].get(app, 0) / 60, 1) for h in all_hours],
            "backgroundColor": APP_COLORS[idx % len(APP_COLORS)],
            "borderRadius": 4,
        }
        for idx, app in enumerate(hour_apps)
    ]

    total_screen = sum(v for d in screen_by_date.values() for v in d.values())
    total_audio = sum(audio_details.values())
    if screen_apps:
        top_app = max(
            ((app, sum(screen_by_date[d].get(app, 0) for d in all_days)) for app in screen_apps),
            key=lambda x: x[1],
        )
    else:
        top_app = ("--", 0)
    top_content = (top_audio[0][0][:30] + "...") if top_audio else "--"

    today_str = date.today().isoformat()
    afk_today_secs = afk_by_date.get(today_str, 0)
    afk_today_str = fmt(afk_today_secs) if afk_today_secs else "nenhum"
    afk_history = []
    for day in sorted(afk_by_date.keys(), reverse=True):
        secs = afk_by_date[day]
        if secs:
            dt = datetime.fromisoformat(day)
            label = f"{WEEKDAY_PT[dt.weekday()]} {dt.strftime('%d/%m')}"
            afk_history.append({"day": label, "time": fmt(secs), "mins": round(secs / 60, 1)})

    _today = date.today()
    _monday = _today - timedelta(days=_today.weekday())
    week_days = [(_monday + timedelta(days=i)).isoformat() for i in range(7)]

    weekly_datasets = []
    for idx, day in enumerate(week_days):
        label_date = datetime.fromisoformat(day)
        day_name = WEEKDAY_PT[label_date.weekday()]
        short_label = f"{day_name} {label_date.strftime('%d/%m')}"
        is_today = day == _today.isoformat()
        color = WEEK_COLORS[idx]
        weekly_datasets.append({
            "label": short_label + (" (hoje)" if is_today else ""),
            "data": [round(weekly_hours[day].get(h, 0) / 60, 1) for h in all_hours],
            "borderColor": color,
            "backgroundColor": color + "22",
            "borderWidth": 3 if is_today else 1.5,
            "pointRadius": 3 if is_today else 2,
            "tension": 0.4,
            "fill": False,
        })

    return {
        "generatedAt": datetime.now().strftime("%d/%m/%Y as %H:%M:%S"),
        "cards": {
            "totalScreen": fmt(total_screen),
            "daysCount": len(all_days),
            "totalAudio": fmt(total_audio),
            "audioCount": len(top_audio),
            "topApp": top_app[0],
            "topAppTime": fmt(top_app[1]),
            "topContent": top_content,
        },
        "days": all_days,
        "hours": all_hours,
        "screenDatasets": screen_datasets,
        "audioDatasets": audio_datasets,
        "hourDatasets": hour_datasets,
        "weeklyDatasets": weekly_datasets,
        "top": {"labels": top_labels, "values": top_values, "colors": top_colors},
        "afk": {"today": afk_today_str, "history": afk_history},
    }


# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/data")
def api_data():
    try:
        return jsonify(build_payload())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def open_browser():
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    print(f"Servidor iniciando em http://{HOST}:{PORT}")
    print("CTRL+C para parar.\n")
    Timer(1.0, open_browser).start()
    app.run(host=HOST, port=PORT, debug=False)
