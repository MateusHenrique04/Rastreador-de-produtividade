"""
dashboard_live.py  —  v2
-------------------------
Servidor Flask local. Não precisa de pasta templates/.
O HTML é servido inline, com dados atualizados via /api/data a cada 30s.

Como usar:
    1) pip install flask
    2) Coloque este arquivo na mesma pasta que tracker.db, classifier.py e rules.json
    3) python dashboard_live.py
    4) O navegador abre sozinho em http://localhost:5000
"""

import os
import sys
import json
import sqlite3
import webbrowser
from collections import defaultdict
from datetime import date, datetime, timedelta
from threading import Timer

from flask import Flask, jsonify

# ── Configuração ───────────────────────────────────────────────────────────────
DB_NAME   = "tracker.db"
MAX_GAP   = 1800
MIN_GAP   = 5
HOST      = "127.0.0.1"
PORT      = 5000

CATEGORY_COLORS = {
    "Literatura":        "#1D9E75",
    "Escrita":           "#D4537E",
    "Tecnologia":        "#534AB7",
    "Jogos":             "#D85A30",
    "Social":            "#EF9F27",
    "Sistema":           "#888780",
    "Debate Denso":      "#185FA5",
    "Debate Leve":       "#85B7EB",
    "Entretenimento":    "#E24B4A",
    "Leitura":           "#2ECC71",
    "RAP Nacional":      "#26215C",
    "Música Brasileira": "#7F77DD",
    "Música":            "#AFA9EC",
    "Navegador":         "#B4B2A9",
    "Outros":            "#94a3b8",
}

APP_COLORS = [
    "#a78bfa","#60a5fa","#4ade80","#fb923c","#f87171","#fbbf24",
    "#94a3b8","#34d399","#38bdf8","#c084fc","#f472b6","#facc15",
    "#e879f9","#2dd4bf",
]
WEEK_COLORS = ["#a78bfa","#60a5fa","#4ade80","#fb923c","#f87171","#fbbf24","#94a3b8"]
TOP_COLORS  = ["#4ade80","#60a5fa","#a78bfa","#f87171","#fb923c","#fbbf24",
               "#34d399","#38bdf8","#c084fc","#f472b6","#facc15","#94a3b8"]
WEEKDAY_PT  = {0:"Seg",1:"Ter",2:"Qua",3:"Qui",4:"Sex",5:"Sab",6:"Dom"}


# ── Classificador ──────────────────────────────────────────────────────────────
def classify(app, context):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from classifier import classify_context
        return classify_context(app, context)
    except Exception:
        return "Outros"


# ── Banco ──────────────────────────────────────────────────────────────────────
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
    c.execute("SELECT date(started_at), SUM(duration_seconds) FROM afk_sessions "
              "WHERE duration_seconds IS NOT NULL GROUP BY date(started_at)")
    rows = c.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


# ── Processamento ──────────────────────────────────────────────────────────────
def process_data(rows):
    screen_by_date = defaultdict(lambda: defaultdict(float))
    audio_by_date  = defaultdict(lambda: defaultdict(float))
    audio_details  = defaultdict(float)
    hour_buckets   = defaultdict(lambda: defaultdict(float))
    weekly_hours   = defaultdict(lambda: defaultdict(float))

    today       = date.today().isoformat()
    _today      = date.today()
    week_monday = _today - timedelta(days=_today.weekday())
    week_start  = week_monday.isoformat()

    screen_rows   = [(a,c,t) for lt,a,c,t in rows if lt == "screen"]
    audio_rows    = [(a,c,t) for lt,a,c,t in rows if lt == "audio"]
    audio_ts_seen = set()

    def _acc(log_rows, is_audio):
        for i in range(len(log_rows) - 1):
            app, context, t1 = log_rows[i]
            _,   _,       t2 = log_rows[i + 1]
            t1   = datetime.fromisoformat(t1)
            t2   = datetime.fromisoformat(t2)
            diff = (t2 - t1).total_seconds()
            day  = t1.date().isoformat()
            if t1.date() != t2.date(): continue
            if not (MIN_GAP <= diff <= MAX_GAP): continue
            hour = t1.strftime("%H:00")

            if not is_audio:
                domain = classify(app, context)
                screen_by_date[day][domain] += diff
                if day == today:
                    hour_buckets[hour][domain] += diff
                if day >= week_start:
                    weekly_hours[day][hour] += diff
                CONTENT_APPS = {"YouTube", "Estudo (Audiobook)"}
                if app in CONTENT_APPS and (app, t1.isoformat()) not in audio_ts_seen:
                    parts = context.split(" - ")
                    ctx_c = " - ".join(p for p in parts
                               if p.strip().lower() not in {"youtube","youtube music","youtube premium"})
                    ctx_c = ctx_c.strip(" -")[:80] or context[:80]
                    audio_details[ctx_c] += diff
                    audio_by_date[day][classify(app, context)] += diff
            else:
                cat = classify(app, context)
                if day == today:
                    hour_buckets[hour][cat] += diff
                if day >= week_start:
                    weekly_hours[day][hour] += diff
                parts = context.split(" - ")
                ctx_c = " - ".join(p for p in parts
                           if p.strip().lower() not in {"youtube","youtube music","youtube premium"})
                ctx_c = ctx_c.strip(" -")[:80] or context[:80]
                audio_details[ctx_c] += diff
                audio_by_date[day][cat] += diff
                audio_ts_seen.add((app, t1.isoformat()))

    _acc(audio_rows,  is_audio=True)
    _acc(screen_rows, is_audio=False)
    return screen_by_date, audio_by_date, audio_details, hour_buckets, weekly_hours


def fmt(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}min"


# ── Payload JSON ───────────────────────────────────────────────────────────────
def build_payload():
    rows        = fetch_all_data()
    afk_by_date = fetch_afk_data()
    screen_by_date, audio_by_date, audio_details, hour_buckets, weekly_hours = process_data(rows)

    all_days    = sorted(set(list(screen_by_date) + list(audio_by_date)))
    screen_apps = sorted({d for day in screen_by_date.values() for d in day})
    audio_cats  = sorted({c for day in audio_by_date.values()  for c in day})

    screen_datasets = [
        {"label": dom,
         "data":  [round(screen_by_date[d].get(dom, 0) / 60, 1) for d in all_days],
         "backgroundColor": CATEGORY_COLORS.get(dom, APP_COLORS[i % len(APP_COLORS)]),
         "borderRadius": 6}
        for i, dom in enumerate(screen_apps)
    ]
    audio_datasets = [
        {"label": cat,
         "data":  [round(audio_by_date[d].get(cat, 0) / 60, 1) for d in all_days],
         "backgroundColor": CATEGORY_COLORS.get(cat, "#94a3b8"),
         "borderRadius": 6}
        for cat in audio_cats
    ]

    top_audio  = sorted([(t, s) for t, s in audio_details.items() if s >= 300],
                        key=lambda x: x[1], reverse=True)
    top_labels = [t[0][:45] + ("…" if len(t[0]) > 45 else "") for t in top_audio]
    top_values = [round(t[1] / 60, 1) for t in top_audio]
    top_colors = [TOP_COLORS[i % len(TOP_COLORS)] for i in range(len(top_audio))]

    all_hours  = [f"{str(h).zfill(2)}:00" for h in range(24)]
    hour_apps  = sorted({a for h in hour_buckets.values() for a in h})
    hour_datasets = [
        {"label": app,
         "data":  [round(hour_buckets[h].get(app, 0) / 60, 1) for h in all_hours],
         "backgroundColor": CATEGORY_COLORS.get(app, APP_COLORS[i % len(APP_COLORS)]),
         "borderRadius": 4}
        for i, app in enumerate(hour_apps)
    ]

    total_screen = sum(v for d in screen_by_date.values() for v in d.values())
    total_audio  = sum(audio_details.values())
    top_app = max(
        ((d, sum(screen_by_date[day].get(d, 0) for day in all_days)) for d in screen_apps),
        key=lambda x: x[1]
    ) if screen_apps else ("--", 0)
    top_content = (top_audio[0][0][:30] + "…") if top_audio else "--"

    today_str      = date.today().isoformat()
    afk_today_secs = afk_by_date.get(today_str, 0)
    afk_today_str  = fmt(afk_today_secs) if afk_today_secs else "nenhum"
    afk_history    = []
    for day in sorted(afk_by_date, reverse=True):
        secs = afk_by_date[day]
        if secs:
            dt    = datetime.fromisoformat(day)
            label = f"{WEEKDAY_PT[dt.weekday()]} {dt.strftime('%d/%m')}"
            afk_history.append({"day": label, "time": fmt(secs), "mins": round(secs / 60, 1)})

    _today    = date.today()
    _monday   = _today - timedelta(days=_today.weekday())
    week_days = [(_monday + timedelta(days=i)).isoformat() for i in range(7)]
    weekly_datasets = []
    for idx, day in enumerate(week_days):
        ld       = datetime.fromisoformat(day)
        dn       = WEEKDAY_PT[ld.weekday()]
        lbl      = f"{dn} {ld.strftime('%d/%m')}"
        is_today = day == _today.isoformat()
        color    = WEEK_COLORS[idx]
        weekly_datasets.append({
            "label":           lbl + (" (hoje)" if is_today else ""),
            "data":            [round(weekly_hours[day].get(h, 0) / 60, 1) for h in all_hours],
            "borderColor":     color,
            "backgroundColor": color + "22",
            "borderWidth":     3 if is_today else 1.5,
            "pointRadius":     3 if is_today else 2,
            "tension":         0.4,
            "fill":            False,
        })

    return {
        "generatedAt":   datetime.now().strftime("%d/%m/%Y às %H:%M:%S"),
        "cards": {
            "totalScreen": fmt(total_screen),
            "daysCount":   len(all_days),
            "totalAudio":  fmt(total_audio),
            "audioCount":  len(top_audio),
            "topApp":      top_app[0],
            "topAppTime":  fmt(top_app[1]),
            "topContent":  top_content,
        },
        "days":           all_days,
        "hours":          all_hours,
        "screenDatasets": screen_datasets,
        "audioDatasets":  audio_datasets,
        "hourDatasets":   hour_datasets,
        "weeklyDatasets": weekly_datasets,
        "top":  {"labels": top_labels, "values": top_values, "colors": top_colors},
        "afk":  {"today": afk_today_str, "history": afk_history},
    }


# ── HTML inline (sem templates/) ──────────────────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Produtividade \xb7 Dashboard ao vivo</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg:      #0d0f14;
    --surface: #161a24;
    --border:  #1f2535;
    --text:    #e2e8f0;
    --muted:   #64748b;
    --accent:  #a78bfa;
    --green:   #4ade80;
    --blue:    #60a5fa;
    --red:     #f87171;
    --orange:  #fb923c;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:'Syne',sans-serif; min-height:100vh; }
  header { display:flex; align-items:center; justify-content:space-between; padding:28px 40px 20px; border-bottom:1px solid var(--border); }
  .logo { font-size:1.1rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; color:var(--accent); }
  .logo span { color:var(--text); }
  header p { color:var(--muted); font-size:.85rem; font-family:'JetBrains Mono',monospace; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--green); margin-right:6px; animation:pulse 2s infinite; vertical-align:middle; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  main { padding:32px 40px; display:grid; gap:24px; }
  .cards { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; }
  .card { background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:22px 24px; position:relative; overflow:hidden; }
  .card::before { content:''; position:absolute; inset:0; background:linear-gradient(135deg,rgba(167,139,250,.06) 0%,transparent 60%); pointer-events:none; }
  .card-label { font-size:.7rem; letter-spacing:.12em; text-transform:uppercase; color:var(--muted); margin-bottom:10px; }
  .card-value { font-size:1.8rem; font-weight:800; line-height:1; }
  .card-sub   { font-size:.78rem; color:var(--muted); margin-top:6px; font-family:'JetBrains Mono',monospace; }
  .c-purple .card-value { color:var(--accent); }
  .c-green  .card-value { color:var(--green); }
  .c-blue   .card-value { color:var(--blue); }
  .c-orange .card-value { color:var(--orange); }
  .panel { background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:28px; }
  .panel-title { font-size:.7rem; letter-spacing:.15em; text-transform:uppercase; color:var(--muted); margin-bottom:22px; display:flex; align-items:center; gap:10px; }
  .panel-title::after { content:''; flex:1; height:1px; background:var(--border); }
  .grid-2 { display:grid; grid-template-columns:1fr 1fr; gap:24px; }
  .chart-wrap      { position:relative; height:260px; }
  .chart-wrap-tall { position:relative; height:320px; }
  .top-list { display:grid; gap:10px; }
  .top-item { display:grid; grid-template-columns:1fr auto; align-items:center; gap:12px; }
  .top-bar-bg   { height:6px; background:var(--border); border-radius:99px; overflow:hidden; margin-top:4px; }
  .top-bar-fill { height:100%; border-radius:99px; transition:width .6s ease; }
  .top-name { font-size:.85rem; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .top-time { font-family:'JetBrains Mono',monospace; font-size:.78rem; color:var(--muted); white-space:nowrap; }
  .afk-today       { display:flex; align-items:baseline; gap:10px; margin-bottom:20px; flex-wrap:wrap; }
  .afk-today-label { font-size:.85rem; color:var(--muted); }
  .afk-today-value { font-size:1.6rem; font-weight:800; color:var(--accent); font-family:'JetBrains Mono',monospace; }
  .afk-history { display:flex; flex-wrap:wrap; gap:10px; }
  .afk-chip      { background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:8px 14px; display:flex; flex-direction:column; gap:2px; min-width:90px; }
  .afk-chip-day  { font-size:.72rem; color:var(--muted); letter-spacing:.05em; }
  .afk-chip-time { font-size:.9rem; font-weight:700; color:var(--text); font-family:'JetBrains Mono',monospace; }
  footer { text-align:center; padding:24px; color:var(--muted); font-size:.75rem; font-family:'JetBrains Mono',monospace; border-top:1px solid var(--border); }
</style>
</head>
<body>

<header>
  <div class="logo">Analise<span>_Produtividade</span></div>
  <p><span class="dot"></span><span id="genAt">carregando...</span></p>
</header>

<main>
  <div class="cards">
    <div class="card c-purple">
      <div class="card-label">Tela total</div>
      <div class="card-value" id="c-screen">--</div>
      <div class="card-sub"   id="c-days">--</div>
    </div>
    <div class="card c-green">
      <div class="card-label">\\xc1udio total</div>
      <div class="card-value" id="c-audio">--</div>
      <div class="card-sub"   id="c-audiocount">--</div>
    </div>
    <div class="card c-blue">
      <div class="card-label">App mais usado</div>
      <div class="card-value" id="c-topapp" style="font-size:1.2rem;padding-top:6px">--</div>
      <div class="card-sub"   id="c-topapptime">--</div>
    </div>
    <div class="card c-orange">
      <div class="card-label">Top conte\\xfado</div>
      <div class="card-value" id="c-topcontent" style="font-size:.95rem;padding-top:4px;line-height:1.3">--</div>
    </div>
  </div>

  <div class="grid-2">
    <div class="panel">
      <div class="panel-title">Tela por dom\\xednio \\xb7 por dia</div>
      <div class="chart-wrap"><canvas id="screenChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title">\\xc1udio por categoria \\xb7 por dia</div>
      <div class="chart-wrap"><canvas id="audioChart"></canvas></div>
    </div>
  </div>

  <div class="grid-2">
    <div class="panel">
      <div class="panel-title">Atividade por hora \\xb7 hoje</div>
      <div class="chart-wrap"><canvas id="hourChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title">Top conte\\xfados assistidos</div>
      <div class="top-list" id="topList"></div>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">Compara\\xe7\\xe3o semanal \\xb7 tela por hora (semana atual)</div>
    <div class="chart-wrap-tall"><canvas id="weeklyChart"></canvas></div>
  </div>

  <div class="panel">
    <div class="panel-title">Tempo AFK</div>
    <div class="afk-today">
      <span class="afk-today-label">Hoje voc\\xea ficou</span>
      <span class="afk-today-value" id="afkToday">--</span>
      <span class="afk-today-label">ausente do teclado/mouse</span>
    </div>
    <div class="afk-history" id="afkHistory"></div>
  </div>
</main>

<footer>tracker.db \\xb7 dados ao vivo \\xb7 atualiza a cada 30s</footer>

<script>
const REFRESH_MS = 30000;

Chart.defaults.color       = '#64748b';
Chart.defaults.borderColor = '#1f2535';
Chart.defaults.font.family = "'Syne', sans-serif";

const BAR_OPTS = (stacked) => ({
  responsive:true, maintainAspectRatio:false,
  plugins:{ legend:{ position:'bottom', labels:{ boxWidth:10, padding:14, font:{size:11} } } },
  scales:{
    x:{ stacked, grid:{display:false} },
    y:{ stacked, ticks:{callback:v=>v+'min'}, grid:{color:'#1f2535'} }
  }
});
const LINE_OPTS = {
  responsive:true, maintainAspectRatio:false,
  interaction:{mode:'index',intersect:false},
  plugins:{
    legend:{position:'bottom',labels:{boxWidth:12,padding:16,font:{size:11}}},
    tooltip:{callbacks:{label:ctx=>` ${ctx.dataset.label}: ${ctx.parsed.y}min`}}
  },
  scales:{
    x:{grid:{display:false},ticks:{maxTicksLimit:12}},
    y:{ticks:{callback:v=>v+'min'},grid:{color:'#1f2535'}}
  }
};

const screenChart = new Chart(document.getElementById('screenChart'), { type:'bar',  data:{labels:[],datasets:[]}, options:BAR_OPTS(true)  });
const audioChart  = new Chart(document.getElementById('audioChart'),  { type:'bar',  data:{labels:[],datasets:[]}, options:BAR_OPTS(true)  });
const hourChart   = new Chart(document.getElementById('hourChart'),   { type:'bar',  data:{labels:[],datasets:[]}, options:BAR_OPTS(false) });
const weeklyChart = new Chart(document.getElementById('weeklyChart'), { type:'line', data:{labels:[],datasets:[]}, options:LINE_OPTS       });

function sync(chart, labels, datasets) {
  chart.data.labels   = labels;
  chart.data.datasets = datasets;
  chart.update('active');
}

function renderTop(top) {
  const max  = Math.max(...top.values, 1);
  const list = document.getElementById('topList');
  list.innerHTML = top.labels.map((lbl,i) => {
    const pct  = (top.values[i]/max*100).toFixed(1);
    const h    = Math.floor(top.values[i]/60);
    const m    = Math.round(top.values[i]%60);
    const time = h>0 ? h+'h '+m+'min' : m+'min';
    return `<div class="top-item"><div>
      <div class="top-name" title="${lbl}">${lbl}</div>
      <div class="top-bar-bg"><div class="top-bar-fill" style="width:${pct}%;background:${top.colors[i]}"></div></div>
    </div><div class="top-time">${time}</div></div>`;
  }).join('');
}

function renderAfk(afk) {
  document.getElementById('afkToday').textContent = afk.today;
  const el = document.getElementById('afkHistory');
  el.innerHTML = afk.history.length
    ? afk.history.map(d=>`<div class="afk-chip"><span class="afk-chip-day">${d.day}</span><span class="afk-chip-time">${d.time}</span></div>`).join('')
    : '<span style="color:var(--muted);font-size:.85rem">Nenhum hist\\xf3rico.</span>';
}

async function refresh() {
  try {
    const res = await fetch('/api/data');
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();

    document.getElementById('genAt').textContent        = d.generatedAt;
    document.getElementById('c-screen').textContent     = d.cards.totalScreen;
    document.getElementById('c-days').textContent       = d.cards.daysCount + ' dia(s) registrado(s)';
    document.getElementById('c-audio').textContent      = d.cards.totalAudio;
    document.getElementById('c-audiocount').textContent = d.cards.audioCount + ' conte\\xfados distintos';
    document.getElementById('c-topapp').textContent     = d.cards.topApp;
    document.getElementById('c-topapptime').textContent = d.cards.topAppTime;
    document.getElementById('c-topcontent').textContent = d.cards.topContent;

    const dayLabels = d.days.map(x => x.slice(5));
    sync(screenChart, dayLabels, d.screenDatasets);
    sync(audioChart,  dayLabels, d.audioDatasets);
    sync(hourChart,   d.hours,   d.hourDatasets);
    sync(weeklyChart, d.hours,   d.weeklyDatasets);

    renderTop(d.top);
    renderAfk(d.afk);
  } catch(e) {
    console.warn('Erro ao buscar dados:', e);
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>"""


# ── Flask ──────────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def index():
    return DASHBOARD_HTML

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