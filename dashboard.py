import sqlite3
import json
import webbrowser
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, date, timedelta

DB_NAME = "tracker.db"
MAX_GAP = 300
MIN_GAP = 5

CATEGORY_COLORS = {
    "Estudo":               "#4ade80",
    "Aprendizado leve":     "#60a5fa",
    "Entretenimento":       "#f87171",
    "Produtivo":            "#a78bfa",
    "YouTube (não classificado)": "#fb923c",
    "Outros":               "#94a3b8",
}

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
    c.execute("SELECT date(started_at), SUM(duration_seconds) FROM afk_sessions WHERE duration_seconds IS NOT NULL GROUP BY date(started_at)")
    rows = c.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}

def process_data(rows):
    screen_by_date  = defaultdict(lambda: defaultdict(float))
    audio_by_date   = defaultdict(lambda: defaultdict(float))
    audio_details   = defaultdict(float)
    hour_buckets    = defaultdict(lambda: defaultdict(float))
    # weekly: {day_iso: {hour_str: total_screen_seconds}}
    weekly_hours    = defaultdict(lambda: defaultdict(float))

    today = date.today().isoformat()
    # Semana atual: sempre de segunda-feira até domingo
    _today = date.today()
    week_monday = _today - timedelta(days=_today.weekday())  # weekday(): 0=Seg, 6=Dom
    week_start = week_monday.isoformat()

    for i in range(len(rows) - 1):
        log_type, app, context, t1 = rows[i]
        _, _, _, t2 = rows[i + 1]
        t1 = datetime.fromisoformat(t1)
        t2 = datetime.fromisoformat(t2)
        diff = (t2 - t1).total_seconds()
        if not (MIN_GAP <= diff <= MAX_GAP):
            continue

        day = t1.date().isoformat()
        hour = t1.strftime("%H:00")

        if log_type == "screen":
            screen_by_date[day][app] += diff
            if day == today:
                hour_buckets[hour][app] += diff
            if day >= week_start:
                weekly_hours[day][hour] += diff
        if log_type == "audio":
            # Conta o app de áudio nos gráficos de tela/hora também (ex: YouTube em background)
            screen_by_date[day][app] += diff
            if day == today:
                hour_buckets[hour][app] += diff
            if day >= week_start:
                weekly_hours[day][hour] += diff
            ctx_clean = context.split(" - ")[0][:80]
            audio_details[ctx_clean] += diff
            # try classify
            cat = classify(app, context)
            audio_by_date[day][cat] += diff

    return screen_by_date, audio_by_date, audio_details, hour_buckets, weekly_hours

def classify(app, context):
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    try:
        from classifier import classify_context
        return classify_context(app, context)
    except Exception:
        return "Outros"

def fmt(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}min"

def generate_html(screen_by_date, audio_by_date, audio_details, hour_buckets, weekly_hours, afk_by_date):
    all_days = sorted(set(list(screen_by_date.keys()) + list(audio_by_date.keys())))

    # Build JS-ready data
    days_js         = json.dumps(all_days)
    screen_apps     = sorted({app for d in screen_by_date.values() for app in d})
    audio_cats      = sorted({cat for d in audio_by_date.values()  for cat in d})

    screen_datasets = []
    APP_COLORS = ["#a78bfa","#60a5fa","#4ade80","#fb923c","#f87171","#fbbf24","#94a3b8"]
    for idx, app in enumerate(screen_apps):
        screen_datasets.append({
            "label": app,
            "data": [round(screen_by_date[d].get(app, 0) / 60, 1) for d in all_days],
            "backgroundColor": APP_COLORS[idx % len(APP_COLORS)],
            "borderRadius": 6,
        })

    audio_datasets = []
    for cat in audio_cats:
        color = CATEGORY_COLORS.get(cat, "#94a3b8")
        audio_datasets.append({
            "label": cat,
            "data": [round(audio_by_date[d].get(cat, 0) / 60, 1) for d in all_days],
            "backgroundColor": color,
            "borderRadius": 6,
        })

    top_audio = sorted(audio_details.items(), key=lambda x: x[1], reverse=True)[:12]
    top_labels = json.dumps([t[0][:45] + ("…" if len(t[0]) > 45 else "") for t in top_audio])
    top_values = json.dumps([round(t[1] / 60, 1) for t in top_audio])
    top_colors = json.dumps([
        "#4ade80","#60a5fa","#a78bfa","#f87171","#fb923c","#fbbf24",
        "#34d399","#38bdf8","#c084fc","#f472b6","#facc15","#94a3b8"
    ][:len(top_audio)])

    # Hour timeline (today)
    all_hours = [f"{str(h).zfill(2)}:00" for h in range(0, 24)]
    hour_apps = sorted({app for h in hour_buckets.values() for app in h})
    hour_datasets = []
    for idx, app in enumerate(hour_apps):
        hour_datasets.append({
            "label": app,
            "data": [round(hour_buckets[h].get(app, 0) / 60, 1) for h in all_hours],
            "backgroundColor": APP_COLORS[idx % len(APP_COLORS)],
            "borderRadius": 4,
        })

    # Summary cards
    total_screen = sum(v for d in screen_by_date.values() for v in d.values())
    total_audio  = sum(audio_details.values())
    top_app      = max(
        ((app, sum(screen_by_date[d].get(app, 0) for d in all_days)) for app in screen_apps),
        key=lambda x: x[1], default=("—", 0)
    )
    top_content  = top_audio[0][0][:30] + "…" if top_audio else "—"

    # AFK por dia
    today_str = date.today().isoformat()
    afk_today_secs = afk_by_date.get(today_str, 0)
    afk_today_str = fmt(afk_today_secs) if afk_today_secs else "nenhum"
    afk_history = []
    for day in sorted(afk_by_date.keys(), reverse=True):
        secs = afk_by_date[day]
        if secs:
            dt = datetime.fromisoformat(day)
            WEEKDAY_PT = {0:"Seg",1:"Ter",2:"Qua",3:"Qui",4:"Sex",5:"Sáb",6:"Dom"}
            label = f"{WEEKDAY_PT[dt.weekday()]} {dt.strftime('%d/%m')}"
            afk_history.append({"day": label, "time": fmt(secs), "mins": round(secs/60,1)})
    afk_history_js = json.dumps(afk_history)

    # Weekly comparison: Seg → Dom da semana atual (atualiza automaticamente a cada semana)
    _today = date.today()
    _monday = _today - timedelta(days=_today.weekday())  # weekday() 0=Seg, 6=Dom
    week_days = [(_monday + timedelta(days=i)).isoformat() for i in range(7)]  # Seg..Dom
    WEEK_COLORS = ["#a78bfa","#60a5fa","#4ade80","#fb923c","#f87171","#fbbf24","#94a3b8"]
    WEEKDAY_NAMES = {0:"Seg",1:"Ter",2:"Qua",3:"Qui",4:"Sex",5:"Sáb",6:"Dom"}

    weekly_datasets = []
    for idx, day in enumerate(week_days):
        label_date = datetime.fromisoformat(day)
        day_name = WEEKDAY_NAMES[label_date.weekday()]
        short_label = f"{day_name} {label_date.strftime('%d/%m')}"
        is_today = day == _today.isoformat()
        color = WEEK_COLORS[idx]
        weekly_datasets.append({
            "label": short_label + (" ·hoje" if is_today else ""),
            "data": [round(weekly_hours[day].get(h, 0) / 60, 1) for h in all_hours],
            "borderColor": color,
            "backgroundColor": color + "22",
            "borderWidth": 3 if is_today else 1.5,
            "pointRadius": 3 if is_today else 2,
            "tension": 0.4,
            "fill": False,
        })

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Produtividade · Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
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
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Syne', sans-serif;
    min-height: 100vh;
  }}

  /* ── Header ── */
  header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 28px 40px 0;
    border-bottom: 1px solid var(--border);
    padding-bottom: 20px;
  }}
  .logo {{
    font-size: 1.1rem;
    font-weight: 800;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--accent);
  }}
  .logo span {{ color: var(--text); }}
  header p {{ color: var(--muted); font-size: .85rem; font-family: 'JetBrains Mono', monospace; }}

  /* ── Layout ── */
  main {{ padding: 32px 40px; display: grid; gap: 24px; }}

  /* ── Cards de resumo ── */
  .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 22px 24px;
    position: relative;
    overflow: hidden;
  }}
  .card::before {{
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(135deg, rgba(167,139,250,.06) 0%, transparent 60%);
    pointer-events: none;
  }}
  .card-label {{ font-size: .7rem; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); margin-bottom: 10px; }}
  .card-value {{ font-size: 1.8rem; font-weight: 800; line-height: 1; }}
  .card-sub {{ font-size: .78rem; color: var(--muted); margin-top: 6px; font-family: 'JetBrains Mono', monospace; }}
  .c-purple .card-value {{ color: var(--accent); }}
  .c-green  .card-value {{ color: var(--green); }}
  .c-blue   .card-value {{ color: var(--blue); }}
  .c-orange .card-value {{ color: var(--orange); }}

  /* ── Painéis de gráfico ── */
  .panel {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 28px;
  }}
  .panel-title {{
    font-size: .7rem;
    letter-spacing: .15em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 22px;
    display: flex;
    align-items: center;
    gap: 10px;
  }}
  .panel-title::after {{
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }}

  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  .chart-wrap {{ position: relative; height: 260px; }}
  .chart-wrap-tall {{ position: relative; height: 320px; }}

  /* ── Top conteúdos ── */
  .top-list {{ display: grid; gap: 10px; }}
  .top-item {{
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 12px;
  }}
  .top-bar-bg {{
    height: 6px;
    background: var(--border);
    border-radius: 99px;
    overflow: hidden;
    margin-top: 4px;
  }}
  .top-bar-fill {{ height: 100%; border-radius: 99px; transition: width .6s ease; }}
  .top-name {{ font-size: .85rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .top-time {{ font-family: 'JetBrains Mono', monospace; font-size: .78rem; color: var(--muted); white-space: nowrap; }}

  /* ── AFK ── */
  .afk-today {{
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 20px;
    flex-wrap: wrap;
  }}
  .afk-today-label {{ font-size: .85rem; color: var(--muted); }}
  .afk-today-value {{
    font-size: 1.6rem;
    font-weight: 800;
    color: var(--accent);
    font-family: 'JetBrains Mono', monospace;
  }}
  .afk-history {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }}
  .afk-chip {{
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 14px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 90px;
  }}
  .afk-chip-day {{ font-size: .72rem; color: var(--muted); letter-spacing: .05em; }}
  .afk-chip-time {{ font-size: .9rem; font-weight: 700; color: var(--text); font-family: 'JetBrains Mono', monospace; }}

  /* ── Rodapé ── */
  footer {{
    text-align: center;
    padding: 24px;
    color: var(--muted);
    font-size: .75rem;
    font-family: 'JetBrains Mono', monospace;
    border-top: 1px solid var(--border);
  }}
</style>
</head>
<body>

<header>
  <div class="logo">Analise<span>_Produtividade</span></div>
  <p>gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}</p>
</header>

<main>

  <!-- Cards de resumo -->
  <div class="cards">
    <div class="card c-purple">
      <div class="card-label">Tela total</div>
      <div class="card-value">{fmt(total_screen)}</div>
      <div class="card-sub">{len(all_days)} dia(s) registrado(s)</div>
    </div>
    <div class="card c-green">
      <div class="card-label">Áudio total</div>
      <div class="card-value">{fmt(total_audio)}</div>
      <div class="card-sub">{len(top_audio)} conteúdos distintos</div>
    </div>
    <div class="card c-blue">
      <div class="card-label">App mais usado</div>
      <div class="card-value" style="font-size:1.2rem;padding-top:6px">{top_app[0]}</div>
      <div class="card-sub">{fmt(top_app[1])}</div>
    </div>
    <div class="card c-orange">
      <div class="card-label">Top conteúdo</div>
      <div class="card-value" style="font-size:.95rem;padding-top:4px;line-height:1.3">{top_content}</div>
    </div>
  </div>

  <!-- Linha 1: Comparação por dia -->
  <div class="grid-2">
    <div class="panel">
      <div class="panel-title">Tela por app · por dia</div>
      <div class="chart-wrap"><canvas id="screenChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title">Áudio por categoria · por dia</div>
      <div class="chart-wrap"><canvas id="audioChart"></canvas></div>
    </div>
  </div>

  <!-- Linha 2: Timeline do dia + Top conteúdos -->
  <div class="grid-2">
    <div class="panel">
      <div class="panel-title">Atividade por hora · hoje</div>
      <div class="chart-wrap"><canvas id="hourChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title">Top conteúdos assistidos</div>
      <div class="top-list" id="topList"></div>
    </div>
  </div>

  <!-- Linha 3: Comparação semanal -->
  <div class="panel">
    <div class="panel-title">Comparação semanal · tela por hora do dia (últimos 7 dias)</div>
    <div class="chart-wrap-tall"><canvas id="weeklyChart"></canvas></div>
  </div>

  <!-- AFK -->
  <div class="panel">
    <div class="panel-title">Tempo AFK</div>
    <div class="afk-today">
      <span class="afk-today-label">Hoje você ficou</span>
      <span class="afk-today-value">{afk_today_str}</span>
      <span class="afk-today-label">ausente do teclado/mouse</span>
    </div>
    <div class="afk-history" id="afkHistory"></div>
  </div>

</main>

<footer>tracker.db · {sum(1 for r in [1])} sessão · dados ao vivo</footer>

<script>
Chart.defaults.color = '#64748b';
Chart.defaults.borderColor = '#1f2535';
Chart.defaults.font.family = "'Syne', sans-serif";

const days    = {days_js};
const hours   = {json.dumps(all_hours)};

// ── Tela por dia ──
new Chart(document.getElementById('screenChart'), {{
  type: 'bar',
  data: {{
    labels: days.map(d => d.slice(5)),
    datasets: {json.dumps(screen_datasets)}
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 10, padding: 14, font: {{ size: 11 }} }} }} }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, ticks: {{ callback: v => v + 'min' }}, grid: {{ color: '#1f2535' }} }}
    }}
  }}
}});

// ── Áudio por dia ──
new Chart(document.getElementById('audioChart'), {{
  type: 'bar',
  data: {{
    labels: days.map(d => d.slice(5)),
    datasets: {json.dumps(audio_datasets)}
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 10, padding: 14, font: {{ size: 11 }} }} }} }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, ticks: {{ callback: v => v + 'min' }}, grid: {{ color: '#1f2535' }} }}
    }}
  }}
}});

// ── Por hora ──
new Chart(document.getElementById('hourChart'), {{
  type: 'bar',
  data: {{
    labels: hours,
    datasets: {json.dumps(hour_datasets)}
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 10, padding: 14, font: {{ size: 11 }} }} }} }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }}, ticks: {{ maxTicksLimit: 12 }} }},
      y: {{ stacked: true, ticks: {{ callback: v => v + 'min' }}, grid: {{ color: '#1f2535' }} }}
    }}
  }}
}});

// ── Comparação semanal ──
new Chart(document.getElementById('weeklyChart'), {{
  type: 'line',
  data: {{
    labels: hours,
    datasets: {json.dumps(weekly_datasets)}
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 16, font: {{ size: 11 }} }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y}}min`
        }}
      }}
    }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 12 }} }},
      y: {{ ticks: {{ callback: v => v + 'min' }}, grid: {{ color: '#1f2535' }} }}
    }}
  }}
}});

// ── AFK histórico ──
const afkData = {afk_history_js};
const afkContainer = document.getElementById('afkHistory');
if (afkData.length === 0) {{
  afkContainer.innerHTML = '<span style="color:var(--muted);font-size:.85rem">Nenhum histórico de dias anteriores.</span>';
}} else {{
  afkData.forEach(d => {{
    afkContainer.innerHTML += `
      <div class="afk-chip">
        <span class="afk-chip-day">${{d.day}}</span>
        <span class="afk-chip-time">${{d.time}}</span>
      </div>`;
  }});
}}

// ── Top conteúdos ──
const labels = {top_labels};
const values = {top_values};
const colors = {top_colors};
const maxVal = Math.max(...values);
const list = document.getElementById('topList');
labels.forEach((label, i) => {{
  const pct = maxVal > 0 ? (values[i] / maxVal * 100).toFixed(1) : 0;
  const h = Math.floor(values[i] / 60);
  const m = Math.round(values[i] % 60);
  const timeStr = h > 0 ? h + 'h ' + m + 'min' : m + 'min';
  list.innerHTML += `
    <div class="top-item">
      <div>
        <div class="top-name" title="${{label}}">${{label}}</div>
        <div class="top-bar-bg"><div class="top-bar-fill" style="width:${{pct}}%;background:${{colors[i]}}"></div></div>
      </div>
      <div class="top-time">${{timeStr}}</div>
    </div>`;
}});
</script>
</body>
</html>"""
    return html

def main():
    rows = fetch_all_data()
    afk_by_date = fetch_afk_data()
    screen_by_date, audio_by_date, audio_details, hour_buckets, weekly_hours = process_data(rows)
    html = generate_html(screen_by_date, audio_by_date, audio_details, hour_buckets, weekly_hours, afk_by_date)

    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    out = os.path.join(base_dir, "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ Dashboard gerado: {out}")
    webbrowser.open(f"file:///{out.replace(chr(92), '/')}")

if __name__ == "__main__":
    main()