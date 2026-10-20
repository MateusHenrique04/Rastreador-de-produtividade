# -*- coding: utf-8 -*-
"""
dashboard_live.py — Dashboard Unificado de Produtividade (Live + Todas as funções do Dashboard)
---------------------------------------------------------------------------------------------
Combina a atualização em tempo real (Flask + cache incremental) com todas as funcionalidades
e funções do dashboard original:
  - Abas: Geral, Leitura (Google Books / Play Livros), Semanal e AFK.
  - Funções exportadas: fetch_all_data, fetch_afk_data, fetch_reading_data, process_data,
    classify, fmt, generate_html, build_payload, etc.
  - Atualização automática em segundo plano a cada 30 segundos sem recarregar a página.
"""

import hashlib
import json
import os
import re
import socket
import sqlite3
import sys
import time
import webbrowser
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
from functools import lru_cache
from threading import Thread, Timer

from flask import Flask, jsonify

# ── Bloco 1: Configurações de Diretórios e Constantes ───────────────────────────
# Garante que o diretório base seja o local do próprio script para carregar DBs e regras
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DB_NAME = os.path.join(BASE_DIR, "tracker.db")
CACHE_DB = os.path.join(BASE_DIR, "dashboard_cache.db")
CACHE_SCHEMA = 2  # Incrementado para suportar tabelas de agregação de leitura

MAX_GAP = 1800  # Limite máximo de segundos entre eventos para contabilizar sessão contínua
MIN_GAP = 5     # Limite mínimo de segundos para filtrar ruídos rápidos
HOST = "127.0.0.1"
PORT = 5000

REFRESH_SECONDS = 30     # Intervalo de recálculo em segundo plano
CHART_DAYS = 30          # Número de dias nos gráficos diários (0 = todos)
TOP_N = 15               # Itens na lista de conteúdos mais assistidos
AFK_HISTORY_DAYS = 14    # Dias de histórico de ausência (AFK)

# Paleta de cores padronizada para categorias de produtividade e entretenimento
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
    "Música Brasileira": "#7F77DD",
    "Música":            "#AFA9EC",
    "Navegador":         "#B4B2A9",
    "Outros":            "#94a3b8",
}

APP_COLORS = [
    "#a78bfa", "#60a5fa", "#4ade80", "#fb923c", "#f87171", "#fbbf24",
    "#94a3b8", "#34d399", "#38bdf8", "#c084fc", "#f472b6", "#facc15",
    "#e879f9", "#2dd4bf",
]

WEEK_COLORS = ["#a78bfa", "#60a5fa", "#4ade80", "#fb923c", "#f87171", "#fbbf24", "#94a3b8"]
TOP_COLORS = [
    "#4ade80", "#60a5fa", "#a78bfa", "#f87171", "#fb923c", "#fbbf24",
    "#34d399", "#38bdf8", "#c084fc", "#f472b6", "#facc15", "#94a3b8"
]

WEEKDAY_PT = {0: "Seg", 1: "Ter", 2: "Qua", 3: "Qui", 4: "Sex", 5: "Sab", 6: "Dom"}
CONTENT_APPS = {"YouTube", "Estudo (Audiobook)"}
_STRIP_TITLES = {"youtube", "youtube music", "youtube premium"}


# ── Bloco 2: Classificador de Contexto com Cache em Memória ─────────────────────
# Importa e utiliza a lógica de regras de classifier.py com cache para otimização
try:
    from classifier import classify_context as _classify_context
except Exception as _exc:  # noqa: BLE001
    _classify_context = None
    print(f"[aviso] Não foi possível importar classifier.py ({_exc}). Classificações serão 'Outros'.")


@lru_cache(maxsize=50_000)
def classify(app, context):
    """Classifica app/contexto usando classifier.py com cache em memória LRU."""
    if _classify_context is None:
        return "Outros"
    try:
        return _classify_context(app, context)
    except Exception:  # noqa: BLE001
        return "Outros"


def clean_label(context):
    """Limpa sufixos de serviços de vídeo/áudio de títulos para exibição nos rankings."""
    parts = context.split(" - ")
    label = " - ".join(p for p in parts if p.strip().lower() not in _STRIP_TITLES)
    return label.strip(" -")[:80] or context[:80]


def fmt(seconds):
    """Formata segundos no formato legível 'Xh Ymin' ou '0min'."""
    if not seconds or seconds < 0:
        return "0min"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h > 0:
        return f"{h}h {m}min"
    return f"{m}min"


# ── Bloco 3: Funções de Consulta Direta ao Banco de Dados (Compatibilidade) ─────
def fetch_all_data():
    """Busca todos os registros de log ordenados por timestamp diretamente do tracker.db."""
    if not os.path.exists(DB_NAME):
        return []
    with closing(sqlite3.connect(DB_NAME, timeout=30)) as conn:
        c = conn.cursor()
        c.execute("SELECT type, app, context, timestamp FROM logs ORDER BY timestamp")
        return c.fetchall()


def fetch_afk_data(since_days=None):
    """
    Busca tempo AFK agrupado por dia a partir da tabela afk_sessions.
    Permite filtrar pelos últimos 'since_days' ou trazer todo o histórico.
    """
    if not os.path.exists(DB_NAME):
        return {}
    with closing(sqlite3.connect(DB_NAME, timeout=30)) as conn:
        c = conn.cursor()
        has_table = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='afk_sessions'").fetchone()
        if not has_table:
            return {}
        if since_days:
            since = (date.today() - timedelta(days=since_days - 1)).isoformat()
            c.execute(
                "SELECT date(started_at), SUM(duration_seconds) FROM afk_sessions "
                "WHERE duration_seconds IS NOT NULL AND started_at >= ? "
                "GROUP BY date(started_at)",
                (since,),
            )
        else:
            c.execute(
                "SELECT date(started_at), SUM(duration_seconds) FROM afk_sessions "
                "WHERE duration_seconds IS NOT NULL GROUP BY date(started_at)"
            )
        return {r[0]: r[1] for r in c.fetchall()}


def _xournal_document_name(context: str) -> str:
    """'*Livro.pdf - Xournal++' -> 'Livro' ('*' = alterações não salvas)."""
    name = re.sub(r"\s*[-–—]\s*Xournal\+\+\s*$", "", context.strip())
    name = name.lstrip("*").strip()
    for ext in (".pdf", ".xopp", ".xoj"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
    return name or "Documento sem título"


# ── Bloco 4: Extração de Dados de Leitura (Google Books / Play Livros) ───────────
def fetch_reading_data(rows):
    """
    Extrai dados de leitura (Google Books, Google Play Livros, etc.) a partir dos logs de tela.
    Retorna reading_by_day, book_times e reading_total.
    Suporta tanto tuplas tradicionais (type, app, context, timestamp)
    quanto marcadores de fim de sessão (screen_end).
    """
    reading_by_day = defaultdict(float)
    book_times = defaultdict(float)

    def _is_reading(app_name, ctx):
        if app_name in ("Google Books", "Google Play Livros", "Leitura", "Xournal++"):
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

    # Filtra eventos de tela relacionados a leitura
    gb_events = []
    for r in rows:
        lt = r[0]
        app = r[1]
        ctx = r[2]
        ts = r[3]
        if lt == "screen" and _is_reading(app, ctx):
            gb_events.append((app, ctx, ts, False))
        elif lt == "screen_end":
            gb_events.append((app, ctx, ts, True))

    # Calcula tempo decorrido entre leituras
    prev = None
    for app, context, ts, is_end in gb_events:
        t2 = datetime.fromisoformat(ts)
        if prev is not None:
            p_app, p_ctx, t1 = prev
            diff = (t2 - t1).total_seconds()
            day = t1.date().isoformat()
            if t1.date() == t2.date() and MIN_GAP <= diff <= MAX_GAP:
                reading_by_day[day] += diff

                # Limpeza do título do livro
                title = _xournal_document_name(p_ctx) if p_app == "Xournal++" else p_ctx
                for sfx in suffixes:
                    if title.endswith(sfx):
                        title = title[:-len(sfx)]
                if title.lower().endswith(".pdf"):
                    title = title[:-4]
                elif title.lower().endswith(".epub"):
                    title = title[:-5]

                title = title.strip() or "Livro desconhecido"
                if title.lower() not in ["google play livros", "play livros", "google books", "livro desconhecido"]:
                    book_times[title] += diff

        prev = None if is_end else (app, context, t2)

    total_seconds = sum(reading_by_day.values())
    return reading_by_day, book_times, total_seconds


# ── Bloco 5: Processamento Geral em Lote (Compatibilidade com dashboard.py) ──────
def process_data(rows):
    """
    Processa todos os logs separando tela e áudio conforme a implementação original.
    Calcula tempos por aplicativo/categoria por dia, hora e semana.
    """
    screen_by_date = defaultdict(lambda: defaultdict(float))
    audio_by_date = defaultdict(lambda: defaultdict(float))
    audio_details = defaultdict(float)
    hour_buckets = defaultdict(lambda: defaultdict(float))
    weekly_hours = defaultdict(lambda: defaultdict(float))

    today = date.today().isoformat()
    _today = date.today()
    week_monday = _today - timedelta(days=_today.weekday())
    week_start = week_monday.isoformat()

    screen_rows = [(r[1], r[2], r[3]) for r in rows if r[0] == "screen"]
    audio_rows = [(r[1], r[2], r[3]) for r in rows if r[0] == "audio"]
    audio_ts_seen = set()

    def _accumulate_compat(log_rows, is_audio):
        for i in range(len(log_rows) - 1):
            app, context, t1 = log_rows[i]
            _, _, t2 = log_rows[i + 1]
            t1_dt = datetime.fromisoformat(t1)
            t2_dt = datetime.fromisoformat(t2)
            diff = (t2_dt - t1_dt).total_seconds()
            day = t1_dt.date().isoformat()
            day2 = t2_dt.date().isoformat()
            if day != day2 or not (MIN_GAP <= diff <= MAX_GAP):
                continue
            hour = t1_dt.strftime("%H:00")
            if not is_audio:
                screen_by_date[day][app] += diff
                if day == today:
                    hour_buckets[hour][app] += diff
                if day >= week_start:
                    weekly_hours[day][hour] += diff
                if app in CONTENT_APPS and (app, t1) not in audio_ts_seen:
                    ctx_clean = clean_label(context)
                    audio_details[ctx_clean] += diff
                    cat = classify(app, context)
                    audio_by_date[day][cat] += diff
            else:
                if day == today:
                    hour_buckets[hour][app] += diff
                if day >= week_start:
                    weekly_hours[day][hour] += diff
                ctx_clean = clean_label(context)
                audio_details[ctx_clean] += diff
                cat = classify(app, context)
                audio_by_date[day][cat] += diff
                audio_ts_seen.add((app, t1))

    _accumulate_compat(audio_rows, is_audio=True)
    _accumulate_compat(screen_rows, is_audio=False)
    return screen_by_date, audio_by_date, audio_details, hour_buckets, weekly_hours


# ── Bloco 6: Acumulador e Processamento Incremental para Cache Rápido ────────────
def new_acc():
    """Cria estruturas de dicionário aninhadas para acumulação rápida de métricas."""
    return {
        "screen":   defaultdict(lambda: defaultdict(float)),  # dia -> categoria -> segundos
        "audio":    defaultdict(lambda: defaultdict(float)),  # dia -> categoria -> segundos
        "content":  defaultdict(lambda: defaultdict(float)),  # dia -> rótulo de conteúdo -> segundos
        "hours":    defaultdict(lambda: defaultdict(float)),  # dia -> "HH:00" -> segundos
        "hour_cat": defaultdict(lambda: defaultdict(lambda: defaultdict(float))),  # dia -> hora -> cat -> s
    }


def _add(acc, app, context, t1, diff, is_audio):
    """Adiciona fatia de tempo ao acumulador por dia, hora e categoria."""
    day = t1.date().isoformat()
    hour = f"{t1.hour:02d}:00"
    cat = classify(app, context)

    if not is_audio:
        acc["screen"][day][cat] += diff
        acc["hours"][day][hour] += diff
        acc["hour_cat"][day][hour][cat] += diff
        return

    acc["audio"][day][cat] += diff
    acc["content"][day][clean_label(context)] += diff
    if app not in CONTENT_APPS:
        acc["hours"][day][hour] += diff
        acc["hour_cat"][day][hour][cat] += diff


def accumulate(acc, rows, is_audio):
    """Processa sequência de eventos tratando marcadores screen_end/audio_end."""
    prev = None
    for app, context, ts, is_end in rows:
        t2 = datetime.fromisoformat(ts)
        if prev is not None:
            p_app, p_ctx, t1 = prev
            diff = (t2 - t1).total_seconds()
            if MIN_GAP <= diff <= MAX_GAP and t1.date() == t2.date():
                _add(acc, p_app, p_ctx, t1, diff, is_audio)
        prev = None if is_end else (app, context, t2)


def process_rows(acc, rows):
    """Separa e acumula registros de tela e áudio com identificação de fechamento de trecho."""
    screen_rows, audio_rows = [], []
    for lt, app, ctx, ts in rows:
        if lt == "screen":
            screen_rows.append((app, ctx, ts, False))
        elif lt == "screen_end":
            screen_rows.append((app, ctx, ts, True))
        elif lt == "audio":
            audio_rows.append((app, ctx, ts, False))
        elif lt == "audio_end":
            audio_rows.append((app, ctx, ts, True))
    accumulate(acc, audio_rows, True)
    accumulate(acc, screen_rows, False)


# ── Bloco 7: Gerenciamento do Cache SQLite de Dias Encerrados ────────────────────
def init_cache(c):
    """Inicializa as tabelas do cache incremental caso não existam."""
    c.executescript("""
        CREATE TABLE IF NOT EXISTS agg_meta         (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS agg_days         (day TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS agg_screen       (day TEXT, category TEXT, seconds REAL, PRIMARY KEY (day, category));
        CREATE TABLE IF NOT EXISTS agg_audio        (day TEXT, category TEXT, seconds REAL, PRIMARY KEY (day, category));
        CREATE TABLE IF NOT EXISTS agg_content      (day TEXT, label TEXT, seconds REAL, PRIMARY KEY (day, label));
        CREATE TABLE IF NOT EXISTS agg_hour         (day TEXT, hour TEXT, seconds REAL, PRIMARY KEY (day, hour));
        CREATE TABLE IF NOT EXISTS agg_reading_day  (day TEXT PRIMARY KEY, seconds REAL);
        CREATE TABLE IF NOT EXISTS agg_reading_book (day TEXT, book TEXT, seconds REAL, PRIMARY KEY (day, book));
    """)


def fingerprint():
    """Gera hash das regras, do classificador e dos parâmetros de gap para invalidar cache."""
    h = hashlib.sha1()
    for name in ("rules.json", "classifier.py"):
        try:
            with open(os.path.join(BASE_DIR, name), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"-")
    h.update(f"schema{CACHE_SCHEMA}|gap{MIN_GAP}-{MAX_GAP}".encode())
    return h.hexdigest()


def init_runtime(rebuild=False):
    """Prepara o cache verificando integridade do schema e do fingerprint de regras."""
    with closing(sqlite3.connect(CACHE_DB)) as c:
        init_cache(c)
        row = c.execute("SELECT value FROM agg_meta WHERE key='fingerprint'").fetchone()
        stored = row[0] if row else None
        fp = fingerprint()
        if rebuild or (stored is not None and stored != fp):
            print("Regras/parâmetros mudaram (ou --rebuild): refazendo cache do zero...")
            with c:
                for t in ("agg_days", "agg_screen", "agg_audio", "agg_content", "agg_hour",
                          "agg_reading_day", "agg_reading_book"):
                    c.execute(f"DELETE FROM {t}")
            classify.cache_clear()
        with c:
            c.execute("INSERT OR REPLACE INTO agg_meta(key, value) VALUES ('fingerprint', ?)", (fp,))


def _next_day(day):
    """Calcula a data do dia subsequente em formato ISO (YYYY-MM-DD)."""
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def seal_day(src, cache, day):
    """Processa e consolida um dia inteiro no cache, incluindo métricas de tela, áudio e leitura."""
    rows = src.execute(
        "SELECT type, app, context, timestamp FROM logs "
        "WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (day, _next_day(day)),
    ).fetchall()

    acc = new_acc()
    process_rows(acc, rows)
    r_by_day, b_times, _ = fetch_reading_data(rows)

    with cache:
        cache.executemany("INSERT OR REPLACE INTO agg_screen VALUES (?,?,?)",
                          [(d, c, s) for d, cats in acc["screen"].items() for c, s in cats.items()])
        cache.executemany("INSERT OR REPLACE INTO agg_audio VALUES (?,?,?)",
                          [(d, c, s) for d, cats in acc["audio"].items() for c, s in cats.items()])
        cache.executemany("INSERT OR REPLACE INTO agg_content VALUES (?,?,?)",
                          [(d, l, s) for d, labs in acc["content"].items() for l, s in labs.items()])
        cache.executemany("INSERT OR REPLACE INTO agg_hour VALUES (?,?,?)",
                          [(d, h, s) for d, hrs in acc["hours"].items() for h, s in hrs.items()])
        cache.executemany("INSERT OR REPLACE INTO agg_reading_day VALUES (?,?)",
                          [(d, s) for d, s in r_by_day.items()])
        cache.executemany("INSERT OR REPLACE INTO agg_reading_book VALUES (?,?,?)",
                          [(day, b, s) for b, s in b_times.items()])
        cache.execute("INSERT OR REPLACE INTO agg_days(day) VALUES (?)", (day,))


def ensure_sealed(verbose=False):
    """Indexa incrementalmente no cache todos os dias anteriores a hoje que ainda não foram consolidados."""
    today = date.today().isoformat()
    sealed_now = 0
    with closing(sqlite3.connect(CACHE_DB)) as cache, \
            closing(sqlite3.connect(DB_NAME, timeout=30)) as src:
        init_cache(cache)
        row = cache.execute("SELECT MAX(day) FROM agg_days").fetchone()
        cursor_ts = _next_day(row[0]) if row and row[0] else ""
        while True:
            r = src.execute(
                "SELECT substr(timestamp, 1, 10) FROM logs WHERE timestamp >= ? "
                "ORDER BY timestamp LIMIT 1", (cursor_ts,)
            ).fetchone()
            if not r or r[0] >= today:
                break
            day = r[0]
            seal_day(src, cache, day)
            sealed_now += 1
            if verbose:
                print(f"  dia {day} indexado no cache ({sealed_now})", end="\r", flush=True)
            cursor_ts = _next_day(day)
    if verbose and sealed_now:
        print()
    return sealed_now


def load_sealed():
    """Carrega dados consolidados do cache para combinação instantânea com o dia ao vivo."""
    with closing(sqlite3.connect(CACHE_DB)) as c:
        init_cache(c)
        screen = defaultdict(dict)
        audio = defaultdict(dict)
        hours = defaultdict(dict)
        reading_by_day = {}

        for day, cat, s in c.execute("SELECT day, category, seconds FROM agg_screen"):
            screen[day][cat] = s
        for day, cat, s in c.execute("SELECT day, category, seconds FROM agg_audio"):
            audio[day][cat] = s
        for day, hour, s in c.execute("SELECT day, hour, seconds FROM agg_hour"):
            hours[day][hour] = s
        for day, s in c.execute("SELECT day, seconds FROM agg_reading_day"):
            reading_by_day[day] = s

        content = dict(c.execute("SELECT label, SUM(seconds) FROM agg_content GROUP BY label"))
        screen_cat_total = dict(c.execute("SELECT category, SUM(seconds) FROM agg_screen GROUP BY category"))
        book_times = dict(c.execute("SELECT book, SUM(seconds) FROM agg_reading_book GROUP BY book"))

    return {
        "screen": dict(screen),
        "audio": dict(audio),
        "hours": dict(hours),
        "content": content,
        "screen_cat_total": screen_cat_total,
        "reading_by_day": reading_by_day,
        "book_times": book_times,
    }


# ── Bloco 8: Construção do Payload JSON Completo com Todas as Métricas ──────────
def build_payload(sealed):
    """
    Constrói a resposta unificada combinando histórico selado e eventos do dia de hoje.
    Fornece dados estruturados para telas, áudio, AFK, comparação semanal e leitura.
    """
    today = date.today()
    today_s = today.isoformat()

    # Leitura apenas dos logs de hoje para manter latência mínima
    with closing(sqlite3.connect(DB_NAME, timeout=30)) as src:
        rows = src.execute(
            "SELECT type, app, context, timestamp FROM logs "
            "WHERE timestamp >= ? ORDER BY timestamp", (today_s,)
        ).fetchall()

    live = new_acc()
    process_rows(live, rows)
    r_today_day, b_today_times, _ = fetch_reading_data(rows)

    # União de tela e áudio
    screen_by_date = dict(sealed["screen"])
    screen_by_date.update(live["screen"])
    audio_by_date = dict(sealed["audio"])
    audio_by_date.update(live["audio"])

    all_days = sorted(set(screen_by_date) | set(audio_by_date))
    chart_days = all_days[-CHART_DAYS:] if CHART_DAYS else all_days

    screen_cats = sorted({c for d in chart_days for c in screen_by_date.get(d, {})})
    audio_cats = sorted({c for d in chart_days for c in audio_by_date.get(d, {})})

    screen_datasets = [
        {
            "label": cat,
            "data": [round(screen_by_date.get(d, {}).get(cat, 0) / 60, 1) for d in chart_days],
            "backgroundColor": CATEGORY_COLORS.get(cat, APP_COLORS[i % len(APP_COLORS)]),
            "borderRadius": 6,
        }
        for i, cat in enumerate(screen_cats)
    ]
    audio_datasets = [
        {
            "label": cat,
            "data": [round(audio_by_date.get(d, {}).get(cat, 0) / 60, 1) for d in chart_days],
            "backgroundColor": CATEGORY_COLORS.get(cat, "#94a3b8"),
            "borderRadius": 6,
        }
        for cat in audio_cats
    ]

    # Totais consolidados
    cat_totals = defaultdict(float, sealed["screen_cat_total"])
    for cats in live["screen"].values():
        for cat, s in cats.items():
            cat_totals[cat] += s
    total_screen = sum(cat_totals.values())

    content = defaultdict(float, sealed["content"])
    for labels in live["content"].values():
        for label, s in labels.items():
            content[label] += s
    total_audio = sum(content.values())

    top_all = sorted(((l, s) for l, s in content.items() if s >= 300), key=lambda x: x[1], reverse=True)
    top_audio = top_all[:TOP_N]
    top_labels = [t[0][:45] + ("..." if len(t[0]) > 45 else "") for t in top_audio]
    top_values = [round(t[1] / 60, 1) for t in top_audio]
    top_colors = [TOP_COLORS[i % len(TOP_COLORS)] for i in range(len(top_audio))]

    top_app = max(cat_totals.items(), key=lambda kv: kv[1]) if cat_totals else ("---", 0)
    top_content = (top_audio[0][0][:30] + "...") if top_audio else "---"

    # Horários de hoje
    all_hours = [f"{h:02d}:00" for h in range(24)]
    hour_cat = live["hour_cat"].get(today_s, {})
    hour_apps = sorted({a for h in hour_cat.values() for a in h})
    hour_datasets = [
        {
            "label": a,
            "data": [round(hour_cat.get(h, {}).get(a, 0) / 60, 1) for h in all_hours],
            "backgroundColor": CATEGORY_COLORS.get(a, APP_COLORS[i % len(APP_COLORS)]),
            "borderRadius": 4,
        }
        for i, a in enumerate(hour_apps)
    ]

    # Dados AFK
    afk_by_date = fetch_afk_data(since_days=AFK_HISTORY_DAYS)
    afk_today_secs = afk_by_date.get(today_s, 0)
    afk_today_str = fmt(afk_today_secs) if afk_today_secs else "nenhum"
    afk_history = []
    for day in sorted(afk_by_date, reverse=True):
        secs = afk_by_date[day]
        if secs:
            dt = datetime.fromisoformat(day)
            afk_history.append({
                "day": f"{WEEKDAY_PT[dt.weekday()]} {dt.strftime('%d/%m')}",
                "time": fmt(secs),
                "mins": round(secs / 60, 1),
            })

    # Comparativo semanal
    monday = today - timedelta(days=today.weekday())
    week_days = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    weekly_datasets = []
    for idx, day in enumerate(week_days):
        ld = datetime.fromisoformat(day)
        is_today = day == today_s
        hrs = live["hours"].get(day, {}) if is_today else sealed["hours"].get(day, {})
        color = WEEK_COLORS[idx]
        weekly_datasets.append({
            "label": f"{WEEKDAY_PT[ld.weekday()]} {ld.strftime('%d/%m')}" + (" hoje" if is_today else ""),
            "data": [round(hrs.get(h, 0) / 60, 1) for h in all_hours],
            "borderColor": color,
            "backgroundColor": color + "22",
            "borderWidth": 3 if is_today else 1.5,
            "pointRadius": 3 if is_today else 2,
            "tension": 0.4,
            "fill": False,
        })

    # Agregação e cálculos de leitura (Google Books / Play Livros)
    reading_by_day = dict(sealed.get("reading_by_day", {}))
    for d, s in r_today_day.items():
        reading_by_day[d] = reading_by_day.get(d, 0.0) + s

    book_times = dict(sealed.get("book_times", {}))
    for b, s in b_today_times.items():
        book_times[b] = book_times.get(b, 0.0) + s

    reading_total = sum(reading_by_day.values())
    reading_today_secs = reading_by_day.get(today_s, 0.0)
    reading_today_min = round(reading_today_secs / 60, 1)
    reading_total_min = round(reading_total / 60, 1)
    reading_total_h = int(reading_total_min // 60)
    reading_total_m = int(reading_total_min % 60)

    reading_30d_labels = []
    reading_30d_values = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        reading_30d_labels.append(d[5:])
        reading_30d_values.append(round(reading_by_day.get(d, 0.0) / 60, 1))

    reading_peak = max(reading_30d_values) if reading_30d_values else 0
    top_books = sorted(book_times.items(), key=lambda x: x[1], reverse=True)[:15]
    top_books_data = [
        {
            "title": b[0],
            "seconds": b[1],
            "mins": round(b[1] / 60, 1),
            "time": fmt(b[1]),
        }
        for b in top_books
    ]

    return {
        "generatedAt": datetime.now().strftime("%d/%m/%Y às %H:%M:%S"),
        "cards": {
            "totalScreen": fmt(total_screen),
            "daysCount": len(all_days),
            "totalAudio": fmt(total_audio),
            "audioCount": len(top_all),
            "topApp": top_app[0],
            "topAppTime": fmt(top_app[1]),
            "topContent": top_content,
        },
        "days": chart_days,
        "hours": all_hours,
        "screenDatasets": screen_datasets,
        "audioDatasets": audio_datasets,
        "hourDatasets": hour_datasets,
        "weeklyDatasets": weekly_datasets,
        "top": {"labels": top_labels, "values": top_values, "colors": top_colors},
        "afk": {"today": afk_today_str, "history": afk_history},
        "reading": {
            "hasReading": reading_total > 0,
            "todaySecs": reading_today_secs,
            "todayMin": int(reading_today_min),
            "todayStr": fmt(reading_today_secs),
            "totalSecs": reading_total,
            "totalH": reading_total_h,
            "totalMin": reading_total_m,
            "totalStr": f"{reading_total_h}h {reading_total_m}min",
            "booksCount": len(book_times),
            "peakMin": int(reading_peak),
            "daysLabels": reading_30d_labels,
            "daysValues": reading_30d_values,
            "topBooks": top_books_data,
        },
    }


# ── Bloco 9: Gerador HTML Estático (Compatibilidade total com dashboard.py) ─────
def generate_html(screen_by_date, audio_by_date, audio_details, hour_buckets,
                  weekly_hours, afk_by_date, reading_by_day, book_times, reading_total):
    """
    Gera o HTML autônomo com todas as abas (Geral, Leitura, Semanal, AFK),
    preservando exatamente o template e a assinatura de generate_html de dashboard.py.
    """
    all_days = sorted(set(list(screen_by_date.keys()) + list(audio_by_date.keys())))
    days_js = json.dumps(all_days)
    screen_apps = sorted({app for d in screen_by_date.values() for app in d})
    audio_cats = sorted({cat for d in audio_by_date.values() for cat in d})

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

    top_audio = sorted(audio_details.items(), key=lambda x: x[1], reverse=True)[:12]
    top_labels = json.dumps([t[0][:45] + ("..." if len(t[0]) > 45 else "") for t in top_audio])
    top_values = json.dumps([round(t[1] / 60, 1) for t in top_audio])
    top_colors = json.dumps([
        "#4ade80", "#60a5fa", "#a78bfa", "#f87171", "#fb923c", "#fbbf24",
        "#34d399", "#38bdf8", "#c084fc", "#f472b6", "#facc15", "#94a3b8"
    ][:len(top_audio)])

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

    total_screen = sum(v for d in screen_by_date.values() for v in d.values())
    total_audio = sum(audio_details.values())
    top_app = max(
        ((app, sum(screen_by_date[d].get(app, 0) for d in all_days)) for app in screen_apps),
        key=lambda x: x[1], default=("---", 0)
    )
    top_content = top_audio[0][0][:30] + "..." if top_audio else "---"

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
    afk_history_js = json.dumps(afk_history)

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
            "label": short_label + (" hoje" if is_today else ""),
            "data": [round(weekly_hours[day].get(h, 0) / 60, 1) for h in all_hours],
            "borderColor": color,
            "backgroundColor": color + "22",
            "borderWidth": 3 if is_today else 1.5,
            "pointRadius": 3 if is_today else 2,
            "tension": 0.4,
            "fill": False,
        })

    today_date = date.today()
    reading_30d_labels = []
    reading_30d_values = []
    for i in range(29, -1, -1):
        d = (today_date - timedelta(days=i)).isoformat()
        reading_30d_labels.append(d[5:])
        reading_30d_values.append(round(reading_by_day.get(d, 0) / 60, 1))

    reading_today_min = round(reading_by_day.get(today_str, 0) / 60, 1)
    reading_total_min = round(reading_total / 60, 1)
    reading_total_h = int(reading_total_min // 60)
    reading_total_m = int(reading_total_min % 60)
    reading_peak = max(reading_30d_values) if reading_30d_values else 0

    top_books = sorted(book_times.items(), key=lambda x: x[1], reverse=True)[:10]
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
      <div class="rh-stat-label">Total histórico</div>
      <div class="rh-stat-value">{reading_total_h}<span class="rh-unit">h</span> {reading_total_m}<span class="rh-unit">min</span></div>
      <div class="rh-stat-sub">{len(book_times)} título(s) lido(s)</div>
    </div>
    <div class="rh-stat">
      <div class="rh-stat-label">Pico (30 dias)</div>
      <div class="rh-stat-value">{int(reading_peak)}<span class="rh-unit">min</span></div>
      <div class="rh-stat-sub">melhor sessão recente</div>
    </div>
  </div>"""
        reading_charts_html = """
  <div class="grid-2">
    <div class="panel">
      <div class="panel-title"><span class="pt-dot green"></span>Leitura diária · últimos 30 dias</div>
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
    <p>Nenhuma sessão de leitura no <strong>Google Books / Google Play Livros</strong> detectada ainda.<br>
    Certifique-se de que a janela aberta contenha <code>Google Play Livros</code> ou <code>Google Books</code>.<br>
    Exemplo: <code>Pessoas Normais - Sally Rooney - Google Play Livros - Brave</code></p>
  </div>"""
        reading_charts_html = ""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Produtividade · Dashboard</title>
<meta name="description" content="Dashboard pessoal de produtividade com tempo de tela, leitura, áudio e AFK.">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
{CSS_SHARED}
</style>
</head>
<body>
<header>
  <div class="logo-wrap">
    <div class="logo-icon">&#x1F4CA;</div>
    <div class="logo"><em>Produtividade</em> &middot; Dashboard</div>
  </div>
  <div class="gen-badge">gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}</div>
</header>
<div class="nav-tabs">
  <div class="tab active"      onclick="switchTab('geral',   this)">Geral</div>
  <div class="tab tab-reading" onclick="switchTab('leitura', this)">&#x1F4DA; Leitura</div>
  <div class="tab"             onclick="switchTab('semanal', this)">Semanal</div>
  <div class="tab"             onclick="switchTab('afk',     this)">AFK</div>
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
      <div class="card-label">Áudio total</div>
      <div class="card-value">{fmt(total_audio)}</div>
      <div class="card-sub">{len(top_audio)} conteúdos distintos</div>
    </div>
    <div class="card c-blue">
      <div class="card-icon">&#x26A1;</div>
      <div class="card-label">App mais usado</div>
      <div class="card-value" style="font-size:1.1rem;padding-top:4px">{top_app[0]}</div>
      <div class="card-sub">{fmt(top_app[1])}</div>
    </div>
    <div class="card c-orange">
      <div class="card-icon">&#x1F3C6;</div>
      <div class="card-label">Top conteúdo</div>
      <div class="card-value" style="font-size:.88rem;padding-top:4px;line-height:1.4">{top_content}</div>
    </div>
  </div>
  <div class="grid-2">
    <div class="panel">
      <div class="panel-title"><span class="pt-dot"></span>Tela por app · por dia</div>
      <div class="chart-wrap"><canvas id="screenChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title"><span class="pt-dot green"></span>Áudio por categoria · por dia</div>
      <div class="chart-wrap"><canvas id="audioChart"></canvas></div>
    </div>
  </div>
  <div class="grid-2">
    <div class="panel">
      <div class="panel-title"><span class="pt-dot blue"></span>Atividade por hora · hoje</div>
      <div class="chart-wrap"><canvas id="hourChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title"><span class="pt-dot orange"></span>Top conteúdos assistidos</div>
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
    <div class="panel-title"><span class="pt-dot"></span>Comparação semanal · tela por hora (semana atual)</div>
    <div class="chart-wrap-tall"><canvas id="weeklyChart"></canvas></div>
  </div>
</main></section>
<section id="sec-afk"><main>
  <div class="panel">
    <div class="panel-title"><span class="pt-dot orange"></span>Tempo AFK</div>
    <div class="afk-today">
      <span class="afk-today-label">Hoje você ficou</span>
      <span class="afk-today-value">{afk_today_str}</span>
      <span class="afk-today-label">ausente do teclado/mouse</span>
    </div>
    <div class="afk-history" id="afkHistory"></div>
  </div>
</main></section>
<footer>tracker.db &middot; <em>{len(all_days)} dias</em> de dados &middot; gerado automaticamente</footer>
<script>
Chart.defaults.color = '#5a6688';
Chart.defaults.borderColor = '#1e2540';
Chart.defaults.font.family = "'Inter', sans-serif";
const OPTS = (stacked, mt) => ({{
  responsive: true, maintainAspectRatio: false,
  plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 10, padding: 14, font: {{ size: 11 }} }} }} }},
  scales: {{
    x: {{ stacked, grid: {{ display: false }}, ticks: {{ maxTicksLimit: mt || 20 }} }},
    y: {{ stacked, ticks: {{ callback: v => v + 'min' }}, grid: {{ color: '#1e2540' }} }}
  }}
}});
const days = {days_js};
const hours = {json.dumps(all_hours)};
new Chart(document.getElementById('screenChart'), {{
  type: 'bar', data: {{ labels: days.map(d => d.slice(5)), datasets: {json.dumps(screen_datasets)} }}, options: OPTS(true)
}});
new Chart(document.getElementById('audioChart'), {{
  type: 'bar', data: {{ labels: days.map(d => d.slice(5)), datasets: {json.dumps(audio_datasets)} }}, options: OPTS(true)
}});
new Chart(document.getElementById('hourChart'), {{
  type: 'bar', data: {{ labels: hours, datasets: {json.dumps(hour_datasets)} }}, options: OPTS(true, 12)
}});
new Chart(document.getElementById('weeklyChart'), {{
  type: 'line',
  data: {{ labels: hours, datasets: {json.dumps(weekly_datasets)} }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 16, font: {{ size: 11 }} }} }},
      tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y}}min` }} }}
    }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 12 }} }},
      y: {{ ticks: {{ callback: v => v + 'min' }}, grid: {{ color: '#1e2540' }} }}
    }}
  }}
}});
const afkData = {afk_history_js};
const afkEl = document.getElementById('afkHistory');
if (afkData.length === 0) {{
  afkEl.innerHTML = '<span style="color:var(--muted);font-size:.85rem">Nenhum histórico de dias anteriores.</span>';
}} else {{
  afkData.forEach(d => {{
    afkEl.innerHTML += `<div class="afk-chip"><span class="afk-chip-day">${{d.day}}</span><span class="afk-chip-time">${{d.time}}</span></div>`;
  }});
}}
const tLabels = {top_labels};
const tValues = {top_values};
const tColors = {top_colors};
const tMax = Math.max(...tValues, 1);
const tList = document.getElementById('topList');
tLabels.forEach((lbl, i) => {{
  const pct = (tValues[i] / tMax * 100).toFixed(1);
  const h = Math.floor(tValues[i] / 60);
  const m = Math.round(tValues[i] % 60);
  const time = h > 0 ? h + 'h ' + m + 'min' : m + 'min';
  tList.innerHTML += `<div class="top-item"><div>
    <div class="top-name" title="${{lbl}}">${{lbl}}</div>
    <div class="top-bar-bg"><div class="top-bar-fill" style="width:${{pct}}%;background:${{tColors[i]}}"></div></div>
  </div><div class="top-time">${{time}}</div></div>`;
}});
const readingEl = document.getElementById('readingChart');
if (readingEl) {{
  new Chart(readingEl, {{
    type: 'line',
    data: {{
      labels: {reading_30d_labels_js},
      datasets: [{{
        label: 'Leitura (min)', data: {reading_30d_values_js},
        borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,.12)',
        borderWidth: 2.5, pointRadius: 3, pointBackgroundColor: '#10b981', tension: 0.4, fill: true,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.parsed.y}} min lidos` }} }} }},
      scales: {{
        x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 10 }} }},
        y: {{ ticks: {{ callback: v => v + 'min' }}, grid: {{ color: '#1e2540' }} }}
      }}
    }}
  }});
}}
const bookEl = document.getElementById('bookList');
if (bookEl) {{
  const bLabels = {top_books_labels};
  const bValues = {top_books_values};
  const bMax = Math.max(...bValues, 1);
  bLabels.forEach((t, i) => {{
    const pct = (bValues[i] / bMax * 100).toFixed(1);
    const h = Math.floor(bValues[i] / 60);
    const m = Math.round(bValues[i] % 60);
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


# ── Bloco 10: Estilos CSS Compartilhados (Dark Mode Premium) ────────────────────
CSS_SHARED = """
  :root {
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
    padding:20px 40px; border-bottom:1px solid var(--border);
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
    background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:6px 14px;
    display:flex; align-items:center; gap:8px;
  }
  .dot {
    display:inline-block; width:8px; height:8px; border-radius:50%;
    background:var(--green2); box-shadow:0 0 10px var(--green2);
    animation:pulse 2s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  .nav-tabs {
    display:flex; gap:4px; padding:12px 40px 0;
    border-bottom:1px solid var(--border); background:var(--bg2);
  }
  .tab {
    padding:9px 22px; font-size:.75rem; font-weight:700; font-family:'Syne',sans-serif;
    letter-spacing:.07em; text-transform:uppercase; color:var(--muted);
    cursor:pointer; border-radius:8px 8px 0 0; border:1px solid transparent;
    border-bottom:none; transition:all .2s; position:relative; bottom:-1px;
    user-select:none;
  }
  .tab:hover { color:var(--text2); background:var(--surface); }
  .tab.active { color:var(--text); background:var(--bg); border-color:var(--border); border-bottom-color:var(--bg); }
  .tab-reading.active { color:var(--green2); }
  section { display:none; }
  section.active { display:block; }
  main { padding:30px 40px; display:grid; gap:26px; max-width:1600px; margin:0 auto; }
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
  .chart-reading { position:relative; height:240px; }
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
    border:1px solid rgba(16,185,129,.25); border-radius:18px; padding:28px 32px;
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
  footer em { color:var(--accent2);font-style:normal; }
"""


# ── Bloco 11: Interface Web com Abas e Atualização ao Vivo via API ─────────────
DASHBOARD_LIVE_HTML = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Produtividade · Dashboard ao vivo</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
{CSS_SHARED}
</style>
</head>
<body>
<header>
  <div class="logo-wrap">
    <div class="logo-icon">&#x1F4CA;</div>
    <div class="logo"><em>Produtividade</em> &middot; Dashboard ao vivo</div>
  </div>
  <div class="gen-badge">
    <span class="dot"></span>
    <span id="genAt">carregando dados...</span>
  </div>
</header>
<div class="nav-tabs">
  <div class="tab active"      id="tab-btn-geral"   onclick="switchTab('geral',   this)">Geral</div>
  <div class="tab tab-reading" id="tab-btn-leitura" onclick="switchTab('leitura', this)">&#x1F4DA; Leitura</div>
  <div class="tab"             id="tab-btn-semanal" onclick="switchTab('semanal', this)">Semanal</div>
  <div class="tab"             id="tab-btn-afk"     onclick="switchTab('afk',     this)">AFK</div>
</div>

<!-- Seção 1: Visão Geral -->
<section id="sec-geral" class="active"><main>
  <div class="cards">
    <div class="card c-purple">
      <div class="card-icon">&#x1F5A5;</div>
      <div class="card-label">Tela total</div>
      <div class="card-value" id="c-screen">--</div>
      <div class="card-sub" id="c-days">--</div>
    </div>
    <div class="card c-green">
      <div class="card-icon">&#x1F3A7;</div>
      <div class="card-label">Áudio total</div>
      <div class="card-value" id="c-audio">--</div>
      <div class="card-sub" id="c-audiocount">--</div>
    </div>
    <div class="card c-blue">
      <div class="card-icon">&#x26A1;</div>
      <div class="card-label">App mais usado</div>
      <div class="card-value" id="c-topapp" style="font-size:1.1rem;padding-top:4px">--</div>
      <div class="card-sub" id="c-topapptime">--</div>
    </div>
    <div class="card c-orange">
      <div class="card-icon">&#x1F3C6;</div>
      <div class="card-label">Top conteúdo</div>
      <div class="card-value" id="c-topcontent" style="font-size:.88rem;padding-top:4px;line-height:1.4">--</div>
    </div>
  </div>

  <div class="grid-2">
    <div class="panel">
      <div class="panel-title"><span class="pt-dot"></span>Tela por categoria · por dia</div>
      <div class="chart-wrap"><canvas id="screenChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title"><span class="pt-dot green"></span>Áudio por categoria · por dia</div>
      <div class="chart-wrap"><canvas id="audioChart"></canvas></div>
    </div>
  </div>

  <div class="grid-2">
    <div class="panel">
      <div class="panel-title"><span class="pt-dot blue"></span>Atividade por hora · hoje</div>
      <div class="chart-wrap"><canvas id="hourChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title"><span class="pt-dot orange"></span>Top conteúdos assistidos</div>
      <div class="top-list" id="topList"></div>
    </div>
  </div>
</main></section>

<!-- Seção 2: Leitura -->
<section id="sec-leitura"><main>
  <div id="readingContentWrap">
    <div class="reading-hero" id="readingHero">
      <div class="rh-stat">
        <div class="rh-stat-label">Hoje</div>
        <div class="rh-stat-value"><span id="rh-today">0</span><span class="rh-unit">min</span></div>
        <div class="rh-stat-sub" id="rh-today-sub">0min</div>
      </div>
      <div class="rh-stat">
        <div class="rh-stat-label">Total histórico</div>
        <div class="rh-stat-value" id="rh-total">0h 0min</div>
        <div class="rh-stat-sub" id="rh-total-sub">0 título(s) lido(s)</div>
      </div>
      <div class="rh-stat">
        <div class="rh-stat-label">Pico (30 dias)</div>
        <div class="rh-stat-value"><span id="rh-peak">0</span><span class="rh-unit">min</span></div>
        <div class="rh-stat-sub">melhor sessão recente</div>
      </div>
    </div>
    <div class="grid-2" style="margin-top:22px">
      <div class="panel">
        <div class="panel-title"><span class="pt-dot green"></span>Leitura diária · últimos 30 dias</div>
        <div class="chart-reading"><canvas id="readingChart"></canvas></div>
      </div>
      <div class="panel">
        <div class="panel-title"><span class="pt-dot green"></span>Livros por tempo lido</div>
        <div class="book-list" id="bookList"></div>
      </div>
    </div>
  </div>
  <div class="reading-empty" id="readingEmpty" style="display:none">
    <div class="empty-icon">&#x1F4DA;</div>
    <p>Nenhuma sessão de leitura no <strong>Google Books / Google Play Livros</strong> detectada ainda.<br>
    Certifique-se de que a janela aberta contenha <code>Google Play Livros</code> ou <code>Google Books</code>.<br>
    Exemplo: <code>Pessoas Normais - Sally Rooney - Google Play Livros - Brave</code></p>
  </div>
</main></section>

<!-- Seção 3: Semanal -->
<section id="sec-semanal"><main>
  <div class="panel">
    <div class="panel-title"><span class="pt-dot"></span>Comparação semanal · tela por hora (semana atual)</div>
    <div class="chart-wrap-tall"><canvas id="weeklyChart"></canvas></div>
  </div>
</main></section>

<!-- Seção 4: AFK -->
<section id="sec-afk"><main>
  <div class="panel">
    <div class="panel-title"><span class="pt-dot orange"></span>Tempo AFK</div>
    <div class="afk-today">
      <span class="afk-today-label">Hoje você ficou</span>
      <span class="afk-today-value" id="afkToday">--</span>
      <span class="afk-today-label">ausente do teclado/mouse</span>
    </div>
    <div class="afk-history" id="afkHistory"></div>
  </div>
</main></section>

<footer>tracker.db &middot; dados ao vivo &middot; atualiza a cada 30s &middot; <em>Produtividade Live</em></footer>

<script>
const REFRESH_MS = 30000;
Chart.defaults.color = '#5a6688';
Chart.defaults.borderColor = '#1e2540';
Chart.defaults.font.family = "'Inter', sans-serif";

const esc = s => String(s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));

const BAR_OPTS = (stacked, maxTicks) => ({{
  responsive: true, maintainAspectRatio: false,
  plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 10, padding: 14, font: {{ size: 11 }} }} }} }},
  scales: {{
    x: {{ stacked, grid: {{ display: false }}, ticks: {{ maxTicksLimit: maxTicks || 20 }} }},
    y: {{ stacked, ticks: {{ callback: v => v + 'min' }}, grid: {{ color: '#1e2540' }} }}
  }}
}});

const LINE_OPTS = {{
  responsive: true, maintainAspectRatio: false,
  interaction: {{ mode: 'index', intersect: false }},
  plugins: {{
    legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 16, font: {{ size: 11 }} }} }},
    tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y}}min` }} }}
  }},
  scales: {{
    x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 12 }} }},
    y: {{ ticks: {{ callback: v => v + 'min' }}, grid: {{ color: '#1e2540' }} }}
  }}
}};

const screenChart  = new Chart(document.getElementById('screenChart'),  {{ type: 'bar',  data: {{ labels: [], datasets: [] }}, options: BAR_OPTS(true) }});
const audioChart   = new Chart(document.getElementById('audioChart'),   {{ type: 'bar',  data: {{ labels: [], datasets: [] }}, options: BAR_OPTS(true) }});
const hourChart    = new Chart(document.getElementById('hourChart'),    {{ type: 'bar',  data: {{ labels: [], datasets: [] }}, options: BAR_OPTS(false, 12) }});
const weeklyChart  = new Chart(document.getElementById('weeklyChart'),  {{ type: 'line', data: {{ labels: [], datasets: [] }}, options: LINE_OPTS }});

let readingChart = null;
const readingCanvas = document.getElementById('readingChart');
if (readingCanvas) {{
  readingChart = new Chart(readingCanvas, {{
    type: 'line',
    data: {{
      labels: [],
      datasets: [{{
        label: 'Leitura (min)', data: [],
        borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,.12)',
        borderWidth: 2.5, pointRadius: 3, pointBackgroundColor: '#10b981', tension: 0.4, fill: true,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.parsed.y}} min lidos` }} }} }},
      scales: {{
        x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 10 }} }},
        y: {{ ticks: {{ callback: v => v + 'min' }}, grid: {{ color: '#1e2540' }} }}
      }}
    }}
  }});
}}

function syncChart(chart, labels, datasets) {{
  if (!chart) return;
  chart.data.labels = labels;
  chart.data.datasets = datasets;
  chart.update('active');
}}

function switchTab(id, el) {{
  document.querySelectorAll('section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const target = document.getElementById('sec-' + id);
  if (target) target.classList.add('active');
  if (el) el.classList.add('active');
  // Salva no storage para manter a aba ativa no refresh
  window.localStorage.setItem('active_productivity_tab', id);
}}

function restoreActiveTab() {{
  const saved = window.localStorage.getItem('active_productivity_tab') || 'geral';
  const tabEl = document.getElementById('tab-btn-' + saved);
  if (tabEl) {{
    switchTab(saved, tabEl);
  }}
}}

function renderTopList(top) {{
  const max = Math.max(...top.values, 1);
  const list = document.getElementById('topList');
  list.innerHTML = top.labels.map((lbl, i) => {{
    const pct = (top.values[i] / max * 100).toFixed(1);
    const h = Math.floor(top.values[i] / 60);
    const m = Math.round(top.values[i] % 60);
    const time = h > 0 ? h + 'h ' + m + 'min' : m + 'min';
    return `<div class="top-item"><div>
      <div class="top-name" title="${{esc(lbl)}}">${{esc(lbl)}}</div>
      <div class="top-bar-bg"><div class="top-bar-fill" style="width:${{pct}}%;background:${{top.colors[i]}}"></div></div>
    </div><div class="top-time">${{time}}</div></div>`;
  }}).join('');
}}

function renderAfk(afk) {{
  document.getElementById('afkToday').textContent = afk.today;
  const el = document.getElementById('afkHistory');
  el.innerHTML = afk.history.length
    ? afk.history.map(d => `<div class="afk-chip"><span class="afk-chip-day">${{esc(d.day)}}</span><span class="afk-chip-time">${{esc(d.time)}}</span></div>`).join('')
    : '<span style="color:var(--muted);font-size:.85rem">Nenhum histórico anterior.</span>';
}}

function renderReading(reading) {{
  const wrap = document.getElementById('readingContentWrap');
  const empty = document.getElementById('readingEmpty');
  if (!reading.hasReading) {{
    wrap.style.display = 'none';
    empty.style.display = 'block';
    return;
  }}
  wrap.style.display = 'block';
  empty.style.display = 'none';

  document.getElementById('rh-today').textContent = reading.todayMin;
  document.getElementById('rh-today-sub').textContent = reading.todayStr;
  document.getElementById('rh-total').textContent = reading.totalStr;
  document.getElementById('rh-total-sub').textContent = reading.booksCount + ' título(s) lido(s)';
  document.getElementById('rh-peak').textContent = reading.peakMin;

  if (readingChart) {{
    readingChart.data.labels = reading.daysLabels;
    readingChart.data.datasets[0].data = reading.daysValues;
    readingChart.update('active');
  }}

  const bookEl = document.getElementById('bookList');
  if (bookEl && reading.topBooks && reading.topBooks.length > 0) {{
    const maxVal = Math.max(...reading.topBooks.map(b => b.mins), 1);
    bookEl.innerHTML = reading.topBooks.map((b, i) => {{
      const pct = (b.mins / maxVal * 100).toFixed(1);
      return `<div class="book-item">
        <span class="book-rank">${{i+1}}.</span>
        <div class="book-info">
          <div class="book-title" title="${{esc(b.title)}}">${{esc(b.title)}}</div>
          <div class="book-bar-bg"><div class="book-bar-fill" style="width:${{pct}}%"></div></div>
        </div>
        <span class="book-time">${{esc(b.time)}}</span>
      </div>`;
    }}).join('');
  }} else if (bookEl) {{
    bookEl.innerHTML = '<span style="color:var(--muted);font-size:.85rem">Nenhum livro registrado.</span>';
  }}
}}

async function refresh() {{
  try {{
    const res = await fetch('/api/data');
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();
    if (d.error) {{
      document.getElementById('genAt').textContent = 'erro: ' + d.error;
      return;
    }}

    document.getElementById('genAt').textContent = 'ao vivo &middot; ' + d.generatedAt;
    document.getElementById('c-screen').textContent = d.cards.totalScreen;
    document.getElementById('c-days').textContent = d.cards.daysCount + ' dia(s) registrado(s)';
    document.getElementById('c-audio').textContent = d.cards.totalAudio;
    document.getElementById('c-audiocount').textContent = d.cards.audioCount + ' conteúdos distintos';
    document.getElementById('c-topapp').textContent = d.cards.topApp;
    document.getElementById('c-topapptime').textContent = d.cards.topAppTime;
    document.getElementById('c-topcontent').textContent = d.cards.topContent;

    const dayLabels = d.days.map(x => x.slice(5));
    syncChart(screenChart, dayLabels, d.screenDatasets);
    syncChart(audioChart,  dayLabels, d.audioDatasets);
    syncChart(hourChart,   d.hours,   d.hourDatasets);
    syncChart(weeklyChart, d.hours,   d.weeklyDatasets);

    renderTopList(d.top);
    renderAfk(d.afk);
    if (d.reading) renderReading(d.reading);
  }} catch(e) {{
    console.warn('Erro ao atualizar dados:', e);
  }}
}}

restoreActiveTab();
refresh();
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>"""


# ── Bloco 12: Aplicação Flask e Thread de Recálculo em Segundo Plano ─────────────
app = Flask(__name__)
_state = {"payload": None, "sealed": None, "sealed_day": None}


@app.route("/")
def index():
    """Rota raiz: entrega o dashboard interativo ao vivo."""
    return DASHBOARD_LIVE_HTML


@app.route("/api/data")
def api_data():
    """Rota da API: entrega os dados formatados em JSON para o cliente JS."""
    payload = _state["payload"]
    if payload is None:
        return jsonify({"error": "dados ainda carregando"}), 503
    return jsonify(payload)


def refresh_once(verbose=False):
    """Executa um ciclo de recálculo: consolida dias no cache se necessário e monta payload."""
    today_s = date.today().isoformat()
    if _state["sealed"] is None or _state["sealed_day"] != today_s:
        ensure_sealed(verbose=verbose)
        _state["sealed"] = load_sealed()
        _state["sealed_day"] = today_s
    t0 = time.perf_counter()
    payload = build_payload(_state["sealed"])
    payload["buildMs"] = round((time.perf_counter() - t0) * 1000)
    _state["payload"] = payload


def refresh_loop():
    """Loop contínuo em segundo plano que atualiza as métricas a cada REFRESH_SECONDS."""
    while True:
        time.sleep(REFRESH_SECONDS)
        try:
            refresh_once()
        except Exception as e:  # noqa: BLE001
            print(f"[erro] atualização em segundo plano falhou: {e}")


def port_in_use(host, port):
    """Verifica se a porta do servidor já está em uso por outra instância."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(1)
        return sk.connect_ex((host, port)) == 0


def open_browser():
    """Abre o navegador padrão apontando para a interface do dashboard ao vivo."""
    webbrowser.open(f"http://{HOST}:{PORT}")


# ── Bloco 13: Ponto de Entrada (Execução Principal) ──────────────────────────────
def main():
    """
    Função principal de inicialização:
    Valida existência do banco, indexa dados no cache, inicia o servidor Flask
    e abre o navegador web automaticamente.
    """
    if not os.path.exists(DB_NAME):
        print(f"Não encontrei {DB_NAME}. Execute o tracker.py primeiro.")
        sys.exit(1)

    # Suporte a geração de arquivo estático caso o usuário deseje
    if "--static" in sys.argv or "--html" in sys.argv:
        print("Gerando dashboard estático em dashboard.html...")
        rows = fetch_all_data()
        afk = fetch_afk_data()
        s, a, ad, hb, wh = process_data(rows)
        rbd, bt, tot = fetch_reading_data(rows)
        html = generate_html(s, a, ad, hb, wh, afk, rbd, bt, tot)
        out_path = os.path.join(BASE_DIR, "dashboard.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Arquivo gravado com sucesso: {out_path}")
        webbrowser.open(f"file:///{out_path.replace(chr(92), '/')}")
        return

    if port_in_use(HOST, PORT):
        print(f"A porta {PORT} já está em uso por outro processo.")
        print("Feche a janela anterior ou termine o processo no CMD antes de iniciar novamente.")
        sys.exit(1)

    init_runtime(rebuild="--rebuild" in sys.argv)

    print("Indexando dados (primeira execução analisa o histórico completo)...")
    t0 = time.perf_counter()
    refresh_once(verbose=True)
    print(f"Indexação concluída em {time.perf_counter() - t0:.1f}s.")

    Thread(target=refresh_loop, daemon=True).start()

    print(f"Servidor ao vivo disponível em: http://{HOST}:{PORT}")
    print("Pressione CTRL+C para encerrar.\n")
    Timer(1.0, open_browser).start()
    app.run(host=HOST, port=PORT, debug=False)


if __name__ == "__main__":
    main()
