import sqlite3
import json
import webbrowser
import os
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta

DB_NAME = "tracker.db"
MAX_GAP = 300
MIN_GAP = 5

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
    "Leitura":           "#10b981",
    "RAP Nacional":      "#26215C",
    "Musica Brasileira": "#7F77DD",
    "Musica":            "#AFA9EC",
    "Navegador":         "#B4B2A9",
    "Outros":            "#94a3b8",
}


def fetch_all_data():
    """Busca todos os registros de log ordenados por timestamp."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT type, app, context, timestamp FROM logs ORDER BY timestamp")
    rows = c.fetchall()
    conn.close()
    return rows


def fetch_afk_data():
    """Busca tempo AFK agrupado por dia."""
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


def fetch_reading_data(rows):
    """
    Extrai dados de leitura do Google Books e Google Play Livros a partir dos logs de screen.
    Retorna reading_by_day, book_times e total_seconds.
    """
    reading_by_day = defaultdict(float)
    book_times     = defaultdict(float)

    # Função auxiliar para identificar se o log pertence a uma sessão de leitura
    def _is_reading(app_name, ctx):
        if app_name in ("Google Books", "Google Play Livros", "Leitura"):
            return True
        c_lower = ctx.lower()
        reading_keywords = [
            "google play livros",
            "play livros",
            "google books",
            "books.google",
            "play.google.com/books",
        ]
        return any(term in c_lower for term in reading_keywords)

    # Filtra logs de tela que correspondem a leitura
    gb_rows = [
        (app, context, ts)
        for lt, app, context, ts in rows
        if lt == "screen" and _is_reading(app, context)
    ]

    for i in range(len(gb_rows) - 1):
        app, context, t1 = gb_rows[i]
        _,   _,       t2 = gb_rows[i + 1]
        t1   = datetime.fromisoformat(t1)
        t2   = datetime.fromisoformat(t2)
        diff = (t2 - t1).total_seconds()
        day  = t1.date().isoformat()

        if t1.date() != t2.date():
            continue
        if not (MIN_GAP <= diff <= MAX_GAP):
            continue

        reading_by_day[day] += diff

        # Extrai título do livro removendo sufixos do navegador e do serviço de leitura
        title = context
        suffixes = [
            " - Google Play Livros - Brave",
            " - Google Play Livros - Google Chrome",
            " - Google Play Livros - Firefox",
            " - Google Play Livros - Edge",
            " - Google Play Livros",
            " - Play Livros - Brave",
            " - Play Livros - Google Chrome",
            " - Play Livros - Firefox",
            " - Play Livros - Edge",
            " - Play Livros",
            " - Google Books - Brave",
            " - Google Books - Google Chrome",
            " - Google Books - Firefox",
            " - Google Books - Edge",
            " - Google Books",
            " - Brave",
            " - Google Chrome",
            " - Firefox",
            " - Edge",
        ]
        for suffix in suffixes:
            if title.endswith(suffix):
                title = title[:-len(suffix)]

        # Remove extensão de arquivo se presente no título do e-book
        if title.lower().endswith(".pdf"):
            title = title[:-4]
        elif title.lower().endswith(".epub"):
            title = title[:-5]

        title = title.strip() or "Livro desconhecido"

        # Não lista a página inicial da biblioteca como um livro específico
        if title.lower() not in ["google play livros", "play livros", "google books", "livro desconhecido"]:
            book_times[title] += diff

    total_seconds = sum(reading_by_day.values())
    return reading_by_day, book_times, total_seconds


def process_data(rows):
    """
    Processa todos os logs separando screen e audio.
    Calcula tempo por app/categoria por dia, hora e semana.
    """
    screen_by_date  = defaultdict(lambda: defaultdict(float))
    audio_by_date   = defaultdict(lambda: defaultdict(float))
    audio_details   = defaultdict(float)
    hour_buckets    = defaultdict(lambda: defaultdict(float))
    weekly_hours    = defaultdict(lambda: defaultdict(float))

    today = date.today().isoformat()
    _today = date.today()
    week_monday = _today - timedelta(days=_today.weekday())
    week_start = week_monday.isoformat()

    # Separa screen e audio para evitar descarte indevido de pares com diff=0
    screen_rows = [(app, context, ts) for lt, app, context, ts in rows if lt == "screen"]
    audio_rows  = [(app, context, ts) for lt, app, context, ts in rows if lt == "audio"]

    # Timestamps ja contabilizados via audio para evitar dupla contagem
    audio_ts_seen = set()

    def _accumulate(log_rows, is_audio):
        for i in range(len(log_rows) - 1):
            app, context, t1 = log_rows[i]
            _,   _,       t2 = log_rows[i + 1]
            t1 = datetime.fromisoformat(t1)
            t2 = datetime.fromisoformat(t2)
            diff = (t2 - t1).total_seconds()
            day  = t1.date().isoformat()
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
                # YouTube e Audiobook alimentam Top Conteudos sem audio correspondente
                CONTENT_APPS = {"YouTube", "Estudo (Audiobook)"}
                if app in CONTENT_APPS and (app, t1.isoformat()) not in audio_ts_seen:
                    parts = context.split(" - ")
                    ctx_clean = " - ".join(p for p in parts if p.strip().lower() not in
                                           {"youtube", "youtube music", "youtube premium"})
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
                ctx_clean = " - ".join(p for p in parts if p.strip().lower() not in
                                       {"youtube", "youtube music", "youtube premium"})
                ctx_clean = ctx_clean.strip(" -")[:80] or context[:80]
                audio_details[ctx_clean] += diff
                cat = classify(app, context)
                audio_by_date[day][cat] += diff
                audio_ts_seen.add((app, t1.isoformat()))

    _accumulate(audio_rows,  is_audio=True)
    _accumulate(screen_rows, is_audio=False)
    return screen_by_date, audio_by_date, audio_details, hour_buckets, weekly_hours


def classify(app, context):
    """Classifica app/contexto usando o classifier.py externo."""
    sys.path.insert(0, os.path.dirname(__file__))
    try:
        from classifier import classify_context
        return classify_context(app, context)
    except Exception:
        return "Outros"


def fmt(seconds):
    """Formata segundos como 'Xh Ymin'."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}min"


def generate_html(screen_by_date, audio_by_date, audio_details, hour_buckets,
                  weekly_hours, afk_by_date, reading_by_day, book_times, reading_total):
    """Gera o HTML completo do dashboard com design premium e painel de leitura."""

    all_days    = sorted(set(list(screen_by_date.keys()) + list(audio_by_date.keys())))
    days_js     = json.dumps(all_days)
    screen_apps = sorted({app for d in screen_by_date.values() for app in d})
    audio_cats  = sorted({cat for d in audio_by_date.values()  for cat in d})

    APP_COLORS = ["#a78bfa","#60a5fa","#4ade80","#fb923c","#f87171","#fbbf24","#94a3b8",
                  "#34d399","#38bdf8","#c084fc","#f472b6","#facc15","#e879f9","#2dd4bf"]

    screen_datasets = []
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

    top_audio  = sorted(audio_details.items(), key=lambda x: x[1], reverse=True)[:12]
    top_labels = json.dumps([t[0][:45] + ("..." if len(t[0]) > 45 else "") for t in top_audio])
    top_values = json.dumps([round(t[1] / 60, 1) for t in top_audio])
    top_colors = json.dumps([
        "#4ade80","#60a5fa","#a78bfa","#f87171","#fb923c","#fbbf24",
        "#34d399","#38bdf8","#c084fc","#f472b6","#facc15","#94a3b8"
    ][:len(top_audio)])

    all_hours   = [f"{str(h).zfill(2)}:00" for h in range(0, 24)]
    hour_apps   = sorted({app for h in hour_buckets.values() for app in h})
    hour_datasets = []
    for idx, app in enumerate(hour_apps):
        hour_datasets.append({
            "label": app,
            "data": [round(hour_buckets[h].get(app, 0) / 60, 1) for h in all_hours],
            "backgroundColor": APP_COLORS[idx % len(APP_COLORS)],
            "borderRadius": 4,
        })

    total_screen = sum(v for d in screen_by_date.values() for v in d.values())
    total_audio  = sum(audio_details.values())
    top_app      = max(
        ((app, sum(screen_by_date[d].get(app, 0) for d in all_days)) for app in screen_apps),
        key=lambda x: x[1], default=("---", 0)
    )
    top_content  = top_audio[0][0][:30] + "..." if top_audio else "---"

    today_str      = date.today().isoformat()
    afk_today_secs = afk_by_date.get(today_str, 0)
    afk_today_str  = fmt(afk_today_secs) if afk_today_secs else "nenhum"
    afk_history    = []
    WEEKDAY_PT     = {0:"Seg",1:"Ter",2:"Qua",3:"Qui",4:"Sex",5:"Sab",6:"Dom"}
    for day in sorted(afk_by_date.keys(), reverse=True):
        secs = afk_by_date[day]
        if secs:
            dt    = datetime.fromisoformat(day)
            label = f"{WEEKDAY_PT[dt.weekday()]} {dt.strftime('%d/%m')}"
            afk_history.append({"day": label, "time": fmt(secs), "mins": round(secs/60,1)})
    afk_history_js = json.dumps(afk_history)

    _today          = date.today()
    _monday         = _today - timedelta(days=_today.weekday())
    week_days       = [(_monday + timedelta(days=i)).isoformat() for i in range(7)]
    WEEK_COLORS     = ["#a78bfa","#60a5fa","#4ade80","#fb923c","#f87171","#fbbf24","#94a3b8"]
    WEEKDAY_NAMES   = {0:"Seg",1:"Ter",2:"Qua",3:"Qui",4:"Sex",5:"Sab",6:"Dom"}

    weekly_datasets = []
    for idx, day in enumerate(week_days):
        label_date  = datetime.fromisoformat(day)
        day_name    = WEEKDAY_NAMES[label_date.weekday()]
        short_label = f"{day_name} {label_date.strftime('%d/%m')}"
        is_today    = day == _today.isoformat()
        color       = WEEK_COLORS[idx]
        weekly_datasets.append({
            "label":           short_label + (" hoje" if is_today else ""),
            "data":            [round(weekly_hours[day].get(h, 0) / 60, 1) for h in all_hours],
            "borderColor":     color,
            "backgroundColor": color + "22",
            "borderWidth":     3 if is_today else 1.5,
            "pointRadius":     3 if is_today else 2,
            "tension":         0.4,
            "fill":            False,
        })

    today_date          = date.today()
    reading_30d_labels  = []
    reading_30d_values  = []
    for i in range(29, -1, -1):
        d = (today_date - timedelta(days=i)).isoformat()
        reading_30d_labels.append(d[5:])
        reading_30d_values.append(round(reading_by_day.get(d, 0) / 60, 1))

    reading_today_min  = round(reading_by_day.get(today_str, 0) / 60, 1)
    reading_total_min  = round(reading_total / 60, 1)
    reading_total_h    = int(reading_total_min // 60)
    reading_total_m    = int(reading_total_min % 60)
    reading_peak       = max(reading_30d_values) if reading_30d_values else 0

    top_books        = sorted(book_times.items(), key=lambda x: x[1], reverse=True)[:10]
    top_books_labels = json.dumps([b[0][:50] + ("..." if len(b[0]) > 50 else "") for b in top_books])
    top_books_values = json.dumps([round(b[1] / 60, 1) for b in top_books])
    reading_30d_labels_js = json.dumps(reading_30d_labels)
    reading_30d_values_js = json.dumps(reading_30d_values)
    has_reading = reading_total > 0

    if has_reading:
        reading_hero_html = f"""
  <div class="reading-hero">
    <div class="rh-stat">
      <div class="rh-stat-label">Hoje</div>
      <div class="rh-stat-value">{int(reading_today_min)}<span class="rh-unit">min</span></div>
      <div class="rh-stat-sub">{fmt(reading_by_day.get(today_str, 0))}</div>
    </div>
    <div class="rh-stat">
      <div class="rh-stat-label">Total historico</div>
      <div class="rh-stat-value">{reading_total_h}<span class="rh-unit">h</span> {reading_total_m}<span class="rh-unit">min</span></div>
      <div class="rh-stat-sub">{len(book_times)} titulo(s) lido(s)</div>
    </div>
    <div class="rh-stat">
      <div class="rh-stat-label">Pico (30 dias)</div>
      <div class="rh-stat-value">{int(reading_peak)}<span class="rh-unit">min</span></div>
      <div class="rh-stat-sub">melhor sessao recente</div>
    </div>
  </div>"""
        reading_charts_html = """
  <div class="grid-2">
    <div class="panel">
      <div class="panel-title"><span class="pt-dot green"></span>Leitura diaria - ultimos 30 dias</div>
      <div class="chart-reading"><canvas id="readingChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title"><span class="pt-dot green"></span>Livros por tempo lido</div>
      <div class="book-list" id="bookList"></div>
    </div>
  </div>"""
    else:
        reading_hero_html = """
  <div class="reading-empty">
    <div class="empty-icon">&#x1F4DA;</div>
    <p>Nenhuma sessao de leitura no <strong>Google Books / Google Play Livros</strong> detectada ainda.<br>
    Certifique-se de que a janela aberta contenha <code>Google Play Livros</code> ou <code>Google Books</code>.<br>
    Exemplo: <code>Pessoas Normais - Sally Rooney - Google Play Livros - Brave</code></p>
  </div>"""
        reading_charts_html = ""

    HTML_STYLE = """  :root {
    --bg:       #080a10;
    --bg2:      #0d0f18;
    --surface:  #111420;
    --surface2: #161b2a;
    --border:   #1e2540;
    --border2:  #252d45;
    --text:     #e8eaf0;
    --text2:    #b0b8d0;
    --muted:    #5a6688;
    --accent:   #7c3aed;
    --accent2:  #a78bfa;
    --green:    #10b981;
    --green2:   #34d399;
    --blue:     #3b82f6;
    --blue2:    #60a5fa;
    --orange:   #f59e0b;
    --orange2:  #fbbf24;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body {
    background:var(--bg);
    color:var(--text);
    font-family:'Inter','Syne',sans-serif;
    min-height:100vh;
    background-image:
      radial-gradient(ellipse 80% 50% at 20% -10%,rgba(124,58,237,.12) 0%,transparent 60%),
      radial-gradient(ellipse 60% 40% at 80% 110%,rgba(16,185,129,.08) 0%,transparent 60%);
  }
  ::-webkit-scrollbar { width:6px; }
  ::-webkit-scrollbar-track { background:var(--bg2); }
  ::-webkit-scrollbar-thumb { background:var(--border2); border-radius:99px; }
  header {
    display:flex; align-items:center; justify-content:space-between;
    padding:22px 44px; border-bottom:1px solid var(--border);
    backdrop-filter:blur(12px); position:sticky; top:0; z-index:100;
    background:rgba(8,10,16,.88);
  }
  .logo-wrap { display:flex; align-items:center; gap:14px; }
  .logo-icon {
    width:36px; height:36px;
    background:linear-gradient(135deg,var(--accent),var(--accent2));
    border-radius:10px; display:flex; align-items:center; justify-content:center;
    font-size:1.1rem; box-shadow:0 0 20px rgba(124,58,237,.4);
  }
  .logo { font-family:'Syne',sans-serif; font-size:.95rem; font-weight:800; letter-spacing:.06em; text-transform:uppercase; }
  .logo em { color:var(--accent2); font-style:normal; }
  .gen-badge {
    font-size:.72rem; font-family:'JetBrains Mono',monospace; color:var(--muted);
    background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:5px 12px;
  }
  .nav-tabs {
    display:flex; gap:4px; padding:14px 44px 0;
    border-bottom:1px solid var(--border); background:var(--bg2);
  }
  .tab {
    padding:8px 20px; font-size:.75rem; font-weight:700; font-family:'Syne',sans-serif;
    letter-spacing:.07em; text-transform:uppercase; color:var(--muted);
    cursor:pointer; border-radius:8px 8px 0 0; border:1px solid transparent;
    border-bottom:none; transition:all .2s; position:relative; bottom:-1px;
  }
  .tab:hover { color:var(--text2); background:var(--surface); }
  .tab.active { color:var(--text); background:var(--bg); border-color:var(--border); border-bottom-color:var(--bg); }
  .tab-reading.active { color:var(--green2); }
  section { display:none; }
  section.active { display:block; }
  main { padding:30px 44px; display:grid; gap:26px; max-width:1600px; margin:0 auto; }
  @keyframes fadeUp { from{opacity:0;transform:translateY(14px)} to{opacity:1;transform:translateY(0)} }
  .cards { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; }
  .card {
    background:var(--surface); border:1px solid var(--border); border-radius:16px;
    padding:22px 24px; position:relative; overflow:hidden;
    transition:transform .2s,border-color .2s,box-shadow .2s;
    animation:fadeUp .45s ease both;
  }
  .card:hover { transform:translateY(-2px); border-color:var(--border2); box-shadow:0 8px 32px rgba(0,0,0,.4); }
  .card::before {
    content:''; position:absolute; inset:0;
    background:linear-gradient(135deg,rgba(167,139,250,.05) 0%,transparent 55%); pointer-events:none;
  }
  .card:nth-child(1){animation-delay:.0s} .card:nth-child(2){animation-delay:.07s}
  .card:nth-child(3){animation-delay:.14s} .card:nth-child(4){animation-delay:.21s}
  .card-icon { width:34px;height:34px;border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:1rem;margin-bottom:12px; }
  .c-purple .card-icon{background:rgba(124,58,237,.15)} .c-green .card-icon{background:rgba(16,185,129,.15)}
  .c-blue .card-icon{background:rgba(59,130,246,.15)} .c-orange .card-icon{background:rgba(245,158,11,.15)}
  .card-label { font-size:.66rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:7px;font-family:'Syne',sans-serif; }
  .card-value { font-size:1.7rem;font-weight:800;line-height:1;font-family:'Syne',sans-serif; }
  .card-sub   { font-size:.73rem;color:var(--muted);margin-top:6px;font-family:'JetBrains Mono',monospace; }
  .c-purple .card-value{color:var(--accent2)} .c-green .card-value{color:var(--green2)}
  .c-blue .card-value{color:var(--blue2)} .c-orange .card-value{color:var(--orange2)}
  .panel {
    background:var(--surface); border:1px solid var(--border); border-radius:16px;
    padding:26px 28px; animation:fadeUp .45s ease both;
  }
  .panel-title {
    font-size:.66rem; letter-spacing:.16em; text-transform:uppercase; color:var(--muted);
    margin-bottom:22px; display:flex; align-items:center; gap:10px; font-family:'Syne',sans-serif;
  }
  .pt-dot { width:6px;height:6px;border-radius:50%;background:var(--accent2);flex-shrink:0; }
  .pt-dot.green{background:var(--green)} .pt-dot.blue{background:var(--blue2)} .pt-dot.orange{background:var(--orange2)}
  .panel-title::after { content:'';flex:1;height:1px;background:var(--border); }
  .grid-2 { display:grid; grid-template-columns:1fr 1fr; gap:22px; }
  .chart-wrap { position:relative; height:260px; }
  .chart-wrap-tall { position:relative; height:320px; }
  .chart-reading { position:relative; height:220px; }
  .top-list { display:grid; gap:11px; }
  .top-item { display:grid; grid-template-columns:1fr auto; align-items:center; gap:14px; }
  .top-bar-bg { height:5px;background:var(--border);border-radius:99px;overflow:hidden;margin-top:5px; }
  .top-bar-fill { height:100%;border-radius:99px;transition:width .7s cubic-bezier(.4,0,.2,1); }
  .top-name { font-size:.82rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text); }
  .top-time { font-family:'JetBrains Mono',monospace;font-size:.74rem;color:var(--muted);white-space:nowrap; }
  .afk-today { display:flex;align-items:baseline;gap:12px;margin-bottom:20px;flex-wrap:wrap; }
  .afk-today-label { font-size:.85rem;color:var(--muted); }
  .afk-today-value { font-size:1.5rem;font-weight:800;color:var(--accent2);font-family:'JetBrains Mono',monospace; }
  .afk-history { display:flex;flex-wrap:wrap;gap:10px; }
  .afk-chip { background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:9px 16px;display:flex;flex-direction:column;gap:2px;min-width:88px;transition:border-color .2s; }
  .afk-chip:hover { border-color:var(--border2); }
  .afk-chip-day  { font-size:.7rem;color:var(--muted);letter-spacing:.06em;font-family:'Syne',sans-serif; }
  .afk-chip-time { font-size:.9rem;font-weight:700;color:var(--text);font-family:'JetBrains Mono',monospace; }
  .reading-hero {
    background:linear-gradient(135deg,rgba(16,185,129,.08) 0%,rgba(52,211,153,.04) 50%,transparent 100%);
    border:1px solid rgba(16,185,129,.25); border-radius:18px; padding:30px 34px;
    display:grid; grid-template-columns:1fr 1fr 1fr; gap:24px; align-items:start;
    position:relative; overflow:hidden; animation:fadeUp .45s ease both;
  }
  .reading-hero::before {
    content:''; position:absolute; top:-60px; right:-60px; width:200px; height:200px;
    background:radial-gradient(circle,rgba(16,185,129,.12),transparent 70%); pointer-events:none;
  }
  .rh-stat { display:flex;flex-direction:column;gap:6px; }
  .rh-stat-label { font-size:.66rem;text-transform:uppercase;letter-spacing:.14em;color:rgba(16,185,129,.75);font-family:'Syne',sans-serif; }
  .rh-stat-value { font-size:2.2rem;font-weight:800;color:var(--green2);font-family:'Syne',sans-serif;line-height:1; }
  .rh-unit { font-size:1rem;font-weight:600;margin-left:2px; }
  .rh-stat-sub { font-size:.72rem;color:var(--muted);font-family:'JetBrains Mono',monospace; }
  .book-list { display:grid;gap:11px; }
  .book-item {
    display:grid; grid-template-columns:auto 1fr auto; align-items:center; gap:14px;
    padding:11px 15px; background:var(--bg2); border:1px solid var(--border);
    border-radius:12px; transition:border-color .2s,background .2s;
  }
  .book-item:hover { border-color:rgba(16,185,129,.3); background:rgba(16,185,129,.04); }
  .book-rank { font-size:.7rem;font-family:'JetBrains Mono',monospace;color:var(--muted);width:22px;text-align:center; }
  .book-info { overflow:hidden; }
  .book-title { font-size:.86rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text); }
  .book-bar-bg { height:4px;background:var(--border);border-radius:99px;overflow:hidden;margin-top:5px; }
  .book-bar-fill { height:100%;background:linear-gradient(90deg,var(--green),var(--green2));border-radius:99px;transition:width .8s cubic-bezier(.4,0,.2,1); }
  .book-time { font-family:'JetBrains Mono',monospace;font-size:.74rem;color:var(--green2);white-space:nowrap; }
  .reading-empty { text-align:center;padding:48px 24px;color:var(--muted); }
  .reading-empty .empty-icon { font-size:3rem;margin-bottom:12px;opacity:.5; }
  .reading-empty p { font-size:.88rem;line-height:1.7; }
  .reading-empty code { background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:2px 8px;font-family:'JetBrains Mono',monospace;font-size:.78rem;color:var(--green2); }
  footer { text-align:center;padding:26px;color:var(--muted);font-size:.7rem;font-family:'JetBrains Mono',monospace;border-top:1px solid var(--border);letter-spacing:.04em; }
  footer em { color:var(--accent2);font-style:normal; }"""

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Produtividade - Dashboard</title>
<meta name="description" content="Dashboard pessoal de produtividade com tempo de tela, leitura, audio e AFK.">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
{HTML_STYLE}
</style>
</head>
<body>
<header>
  <div class="logo-wrap">
    <div class="logo-icon">&#x1F4CA;</div>
    <div class="logo"><em>Produtividade</em> &middot; Dashboard</div>
  </div>
  <div class="gen-badge">gerado em {datetime.now().strftime('%d/%m/%Y as %H:%M')}</div>
</header>
<div class="nav-tabs">
  <div class="tab active"         onclick="switchTab('geral',   this)">Geral</div>
  <div class="tab tab-reading"    onclick="switchTab('leitura', this)">&#x1F4DA; Leitura</div>
  <div class="tab"                onclick="switchTab('semanal', this)">Semanal</div>
  <div class="tab"                onclick="switchTab('afk',     this)">AFK</div>
</div>
<section id="sec-geral" class="active"><main>
  <div class="cards">
    <div class="card c-purple">
      <div class="card-icon">&#x1F5A5;</div>
      <div class="card-label">Tela total</div>
      <div class="card-value">{fmt(total_screen)}</div>
      <div class="card-sub">{len(all_days)} dia(s) registrado(s)</div>
    </div>
    <div class="card c-green">
      <div class="card-icon">&#x1F3A7;</div>
      <div class="card-label">Audio total</div>
      <div class="card-value">{fmt(total_audio)}</div>
      <div class="card-sub">{len(top_audio)} conteudos distintos</div>
    </div>
    <div class="card c-blue">
      <div class="card-icon">&#x26A1;</div>
      <div class="card-label">App mais usado</div>
      <div class="card-value" style="font-size:1.1rem;padding-top:4px">{top_app[0]}</div>
      <div class="card-sub">{fmt(top_app[1])}</div>
    </div>
    <div class="card c-orange">
      <div class="card-icon">&#x1F3C6;</div>
      <div class="card-label">Top conteudo</div>
      <div class="card-value" style="font-size:.88rem;padding-top:4px;line-height:1.4">{top_content}</div>
    </div>
  </div>
  <div class="grid-2">
    <div class="panel">
      <div class="panel-title"><span class="pt-dot"></span>Tela por app - por dia</div>
      <div class="chart-wrap"><canvas id="screenChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title"><span class="pt-dot green"></span>Audio por categoria - por dia</div>
      <div class="chart-wrap"><canvas id="audioChart"></canvas></div>
    </div>
  </div>
  <div class="grid-2">
    <div class="panel">
      <div class="panel-title"><span class="pt-dot blue"></span>Atividade por hora - hoje</div>
      <div class="chart-wrap"><canvas id="hourChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title"><span class="pt-dot orange"></span>Top conteudos assistidos</div>
      <div class="top-list" id="topList"></div>
    </div>
  </div>
</main></section>
<section id="sec-leitura"><main>
  {reading_hero_html}
  {reading_charts_html}
</main></section>
<section id="sec-semanal"><main>
  <div class="panel">
    <div class="panel-title"><span class="pt-dot"></span>Comparacao semanal - tela por hora (semana atual)</div>
    <div class="chart-wrap-tall"><canvas id="weeklyChart"></canvas></div>
  </div>
</main></section>
<section id="sec-afk"><main>
  <div class="panel">
    <div class="panel-title"><span class="pt-dot orange"></span>Tempo AFK</div>
    <div class="afk-today">
      <span class="afk-today-label">Hoje voce ficou</span>
      <span class="afk-today-value">{afk_today_str}</span>
      <span class="afk-today-label">ausente do teclado/mouse</span>
    </div>
    <div class="afk-history" id="afkHistory"></div>
  </div>
</main></section>
<footer>tracker.db &middot; <em>{len(all_days)} dias</em> de dados &middot; gerado automaticamente</footer>
<script>
Chart.defaults.color       = '#5a6688';
Chart.defaults.borderColor = '#1e2540';
Chart.defaults.font.family = "'Inter', sans-serif";
const OPTS = (stacked, mt) => ({{
  responsive:true, maintainAspectRatio:false,
  plugins:{{ legend:{{ position:'bottom', labels:{{ boxWidth:10, padding:14, font:{{size:11}} }} }} }},
  scales:{{
    x:{{ stacked, grid:{{display:false}}, ticks:{{maxTicksLimit:mt||20}} }},
    y:{{ stacked, ticks:{{callback:v=>v+'min'}}, grid:{{color:'#1e2540'}} }}
  }}
}});
const days  = {days_js};
const hours = {json.dumps(all_hours)};
new Chart(document.getElementById('screenChart'), {{
  type:'bar', data:{{ labels:days.map(d=>d.slice(5)), datasets:{json.dumps(screen_datasets)} }}, options:OPTS(true)
}});
new Chart(document.getElementById('audioChart'), {{
  type:'bar', data:{{ labels:days.map(d=>d.slice(5)), datasets:{json.dumps(audio_datasets)} }}, options:OPTS(true)
}});
new Chart(document.getElementById('hourChart'), {{
  type:'bar', data:{{ labels:hours, datasets:{json.dumps(hour_datasets)} }}, options:OPTS(true,12)
}});
new Chart(document.getElementById('weeklyChart'), {{
  type:'line',
  data:{{ labels:hours, datasets:{json.dumps(weekly_datasets)} }},
  options:{{
    responsive:true, maintainAspectRatio:false,
    interaction:{{mode:'index',intersect:false}},
    plugins:{{
      legend:{{position:'bottom',labels:{{boxWidth:12,padding:16,font:{{size:11}}}}}},
      tooltip:{{callbacks:{{label:ctx=>` ${{ctx.dataset.label}}: ${{ctx.parsed.y}}min`}}}}
    }},
    scales:{{
      x:{{grid:{{display:false}},ticks:{{maxTicksLimit:12}}}},
      y:{{ticks:{{callback:v=>v+'min'}},grid:{{color:'#1e2540'}}}}
    }}
  }}
}});
const afkData = {afk_history_js};
const afkEl   = document.getElementById('afkHistory');
if (afkData.length === 0) {{
  afkEl.innerHTML = '<span style="color:var(--muted);font-size:.85rem">Nenhum historico de dias anteriores.</span>';
}} else {{
  afkData.forEach(d => {{
    afkEl.innerHTML += `<div class="afk-chip"><span class="afk-chip-day">${{d.day}}</span><span class="afk-chip-time">${{d.time}}</span></div>`;
  }});
}}
const tLabels = {top_labels};
const tValues = {top_values};
const tColors = {top_colors};
const tMax    = Math.max(...tValues, 1);
const tList   = document.getElementById('topList');
tLabels.forEach((lbl, i) => {{
  const pct  = (tValues[i] / tMax * 100).toFixed(1);
  const h    = Math.floor(tValues[i] / 60);
  const m    = Math.round(tValues[i] % 60);
  const time = h > 0 ? h + 'h ' + m + 'min' : m + 'min';
  tList.innerHTML += `<div class="top-item"><div>
    <div class="top-name" title="${{lbl}}">${{lbl}}</div>
    <div class="top-bar-bg"><div class="top-bar-fill" style="width:${{pct}}%;background:${{tColors[i]}}"></div></div>
  </div><div class="top-time">${{time}}</div></div>`;
}});
const readingEl = document.getElementById('readingChart');
if (readingEl) {{
  new Chart(readingEl, {{
    type:'line',
    data:{{
      labels:{reading_30d_labels_js},
      datasets:[{{
        label:'Leitura (min)', data:{reading_30d_values_js},
        borderColor:'#10b981', backgroundColor:'rgba(16,185,129,.12)',
        borderWidth:2.5, pointRadius:3, pointBackgroundColor:'#10b981', tension:0.4, fill:true,
      }}]
    }},
    options:{{
      responsive:true, maintainAspectRatio:false,
      plugins:{{ legend:{{display:false}}, tooltip:{{callbacks:{{label:ctx=>` ${{ctx.parsed.y}} min lidos`}}}} }},
      scales:{{
        x:{{grid:{{display:false}},ticks:{{maxTicksLimit:10}}}},
        y:{{ticks:{{callback:v=>v+'min'}},grid:{{color:'#1e2540'}}}}
      }}
    }}
  }});
}}
const bookEl = document.getElementById('bookList');
if (bookEl) {{
  const bLabels = {top_books_labels};
  const bValues = {top_books_values};
  const bMax    = Math.max(...bValues, 1);
  bLabels.forEach((t, i) => {{
    const pct  = (bValues[i] / bMax * 100).toFixed(1);
    const h    = Math.floor(bValues[i] / 60);
    const m    = Math.round(bValues[i] % 60);
    const time = h > 0 ? h + 'h ' + m + 'min' : m + 'min';
    bookEl.innerHTML += `<div class="book-item">
      <span class="book-rank">${{i+1}}.</span>
      <div class="book-info">
        <div class="book-title" title="${{t}}">${{t}}</div>
        <div class="book-bar-bg"><div class="book-bar-fill" style="width:${{pct}}%"></div></div>
      </div>
      <span class="book-time">${{time}}</span>
    </div>`;
  }});
}}
function switchTab(id, el) {{
  document.querySelectorAll('section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('sec-' + id).classList.add('active');
  el.classList.add('active');
}}
</script>
</body>
</html>"""
    return html


def main():
    """Ponto de entrada: busca dados, processa e gera o dashboard HTML."""
    rows        = fetch_all_data()
    afk_by_date = fetch_afk_data()
    screen_by_date, audio_by_date, audio_details, hour_buckets, weekly_hours = process_data(rows)
    reading_by_day, book_times, reading_total = fetch_reading_data(rows)

    html = generate_html(
        screen_by_date, audio_by_date, audio_details,
        hour_buckets, weekly_hours, afk_by_date,
        reading_by_day, book_times, reading_total
    )

    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    out = os.path.join(base_dir, "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard gerado: {out}")
    webbrowser.open(f"file:///{out.replace(chr(92), '/')}")


if __name__ == "__main__":
    main()
