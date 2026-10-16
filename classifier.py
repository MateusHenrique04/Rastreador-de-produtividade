"""
classifier.py  —  v2 (Corrigido)
--------------------
Classificador de conteúdo do tracker de produtividade.
"""

import json
import re
import unicodedata
import os

RULES_FILE = "rules.json"
_rules_cache = None
_rules_mtime = 0.0


# ── Utilitários ────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII").lower()


def _load_rules():
    # Recarrega as regras automaticamente se rules.json for alterado em disco
    global _rules_cache, _rules_mtime
    if not os.path.exists(RULES_FILE):
        if _rules_cache is None:
            _rules_cache = {"app_rules": [], "content_rules": []}
        return _rules_cache

    try:
        current_mtime = os.path.getmtime(RULES_FILE)
        if _rules_cache is None or current_mtime != _rules_mtime:
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                _rules_cache = json.load(f)
            _rules_mtime = current_mtime
    except Exception:
        if _rules_cache is None:
            _rules_cache = {"app_rules": [], "content_rules": []}
    return _rules_cache


def get_app_rules():
    return _load_rules().get("app_rules", [])


def get_content_rules():
    return _load_rules().get("content_rules", [])


def split_app_context(title: str) -> tuple[str, str]:
    title_norm = normalize(title)
    for rule in get_app_rules():
        for keyword in rule["match"]:
            if normalize(keyword) in title_norm:
                return rule["app"], title
    return "Outros", title


# ── Mapeamento app → domínio (tela) ───────────────────────────────────────────

APP_DOMAIN = {
    "Lector":             "Literatura",
    "Estudo (Audiobook)": "Literatura",
    "Estudo e Leitura":   "Literatura",
    "Okular":            "Literatura",
    "Google Books":       "Leitura",
    "Zettlr":             "Escrita",
    "Gemini":             "Escrita",
    "VS Code":            "Tecnologia",
    "Replit":             "Tecnologia",
    "CMD":                "Tecnologia",
    "PowerShell":         "Tecnologia",
    "Tracker":            "Tecnologia",
    "Antigravity":        "Tecnologia",
    "Claude":             "Tecnologia",
    "ChatGPT":            "Tecnologia",
    "Discord":            "Social",
    "WhatsApp":           "Social",
    "Email":              "Social",
    "Valorant":           "Jogos",
    "League of Legends":  "Jogos",
    "Counter-Strike 2":   "Jogos",
    "Counter-Strike: Source": "Jogos",
    "Stardew Valley":     "Jogos",
    "Risk of Rain 2":     "Jogos",
    "Risk of Rain":       "Jogos",
    "Slay the Spire":     "Jogos",
    "Bloons TD 6":        "Jogos",
    "Bloons TD 5":        "Jogos",
    "Hollow Knight: Silksong": "Jogos",
    "Hollow Knight":      "Jogos",
    "Megabonk":           "Jogos",
    "Nova Lands":         "Jogos",
    "Rayman Legends":     "Jogos",
    "Rayman Origins":     "Jogos",
    "Terraria":           "Jogos",
    "The Gnorp Apologue": "Jogos",
    "KOF '97 Global Match": "Jogos",
    "tModLoader":         "Jogos",
    "Tribes of Midgard":  "Jogos",
    "Vampire Survivors":  "Jogos",
    "Steam":              "Jogos",
    "Riot Client":        "Jogos",
    "Windows":            "Sistema",
    "Gerenciador de Tarefas": "Sistema",
    "Google Drive":       "Sistema",
    "WinRAR":             "Sistema",
    "Bloco de Notas":     "Sistema",
}

RAP_NACIONAL = {
    "djonga", "baco exu do blues", "costa gold", "pumapjl", "gyylo",
    "tz da coronel", "drinho sp", "aldeia records", "bk -",
    "febre90s", "m4rkim",
}
MUSICA_BRASILEIRA = {
    "toquinho", "caetano veloso", "geraldo vandre", "luiz gonzaga",
    "martnalia", "cazuza", "tribalistas", "lo borges",
    "chico buarque", "gilberto gil", "milton nascimento",
}

_LEITURA_PATTERNS = [
    r"\.pdf\b",
    r"\bcapítulo\s+\d+\b",
    r"\bcapitulo\s+\d+\b",
    r"\bvolume\s+\d+\b",
    r"\bscribd\.com\b",
    r"\bscribd\b",
    r"\bgoogle\s+books\b",
    r"\bbooks\.google\.com\b",
    r"\bplay\.google\.com/books\b",
    r"\bplay\s+books\b",
    r"\bgoogle\s+play\s+livros\b",
    r"\bplay\s+livros\b",
    r"\bgoogle\s+livros\b",
]

_KNOWN_CHANNELS: list[tuple[list[str], str]] = [
    (["breve introducao ao pensamento", "bourdieu", "campo de bourdieu",
      "campos autonomos de bourdieu", "mana na visao de bourdieu",
      "genealogia da moral", "nietzsche", "foucault", "marx", "marxismo",
      "hannah arendt", "platao", "aristoteles", "hegel", "materialismo",
      "fenomenologia", "ontologia", "epistemologia", "semiotica",
      "fetichismo da mercadoria", "modo de producao", "hegemonia",
      "psicanalise", "lacan", "freud", "estruturalismo",
      "o que e o marxismo cultural", "sociologia politica",
      "prof. dr. erico andrade", "vogalizando a historia",
      "genealogia da moral", "introducao ao pensamento",
      ], "Debate Denso"),

    (["quadrinhos na sarjeta", "edson castro", "pirulla", "jones manoel",
      "alexandre linck", "joao carvalho", "vídeo-resposta", "video-resposta",
      "3continentes", "3 continentes", "achismos podcast", "podcast do edson",
      "inteligencia ltda", "inteligencia ltda.", "3 irmaos",
      "pc siqueira", "paulo galo", "jean wyllys", "breno altman",
      "jones vs sued", "trabalhista expoe", "leviano", "lil vinicin",
      "chavoso", "ryan sp", "nabil bonduki", "castrinho", "vogel responde",
      "canal rede rs", "matheus benites",
      "canais de react", "banalizar escroques",
      "cinema nacional", "cota de tela", "lei rouanet",
      "editais de cultura", "filme brasileiro",
      "quadrinhos", "hqmix", "pipoca & nanquim", "nanquim e ferrez",
      "pirata editorial", "darkside", "boys love",
      "representatividade", "diversidade", "identitarismo",
      "trans nao", "lgbtq", "identidade de genero", "criancas trans",
      "heterofobia", "fundamentalismo cristao",
      "politicamente correto", "palavras ofendem",
      "guerra cultural", "anticapitalismo", "elitismo",
      "capital cultural", "classe media",
      "universidades brasileiras", "paulo freire",
      "imigrante", "imigracao", "florianopolis imigrantes",
      "interior do brasil", "brasil bizarro", "viver no brasil",
      "escala 6x1", "pec da blindagem", "punitivismo",
      "fim da escala", "governo vacilando",
      "airbnb", "privatizacao", "moradia popular",
      "vale a pena morar em sao paulo",
      "racismo", "cotas", "questao das cotas",
      "bolsonarismo", "esquerda", "direita",
      "trump", "charlie kirk", "pos-verdade",
      "evangélicos", "damares", "religiao",
      "monark", "youtube bane", "erika hilton",
      "influencers", "comunicacao caotica",
      "neutralidade politica", "bom-mocismo",
      "papel da critica", "autor da obra",
      "separar o autor da obra",
      "neil gaiman", "cancelar obras",
      "ghibli bilionario", "estilo ghibli",
      "atila da shell", "reputacao",
      "madame teia", "ovolete",
      "virgem negra", "bife na calcinha",
      "tropa de elite", "autor esta morto",
      "alma do negocio", "queda do meteoro",
      "ideologia dominante", "etica biscoiteira",
      "amor e um problem", "busca pelo amor", "autoajuda",
      "sapiossexualidade", "herois individualismo",
      "cabra marcado para morrer", "eduardo coutinho",
      "alan moore", "quem e alan moore",
      "sarjeta", "6 anos de quadrinhos",
      "family guy", "o desenho perfeito",
      "one piece desabafos", "acabei alabasta", "cheguei em alabasta",
      "documentario", "documentários",
      "tratado de tordesilhas",
      "vfleets", "fretebras",
      "o problema dos reacts", "brino vs ben mendes",
      "comunicacao olavista", "olavista",
      "foi traicao", "eterno bbb da vida alheia",
      "misoginia na internet",
      "sionista", "supremacia etnica",
      "anarquistas x comunistas",
      "papa bet", "forca da igreja",
      "capital cultural da classe media",
      "falsa neutralidade politica",
      "as polemicas do mc kevin",
      "pabllo vittar e o sertanejo",
      "boca rosa e criticada",
      ], "Debate Leve"),

    (["will they survive", "can they survive", "| cut",
      "cblol", "playoffs #cblol", "co-stream", "ilha das lendas",
      "arena brucesa", "idl nights", "los x fluxo", "loud x vks",
      "fx 1x1 los", "fx x los",
      "virginia expoe", "virgínia expõe", "neymar", "copa",
      "davi brito", "bbb", "big brother",
      "sogra lendo", "fazendo amizade",
      "treta de carnaval", "insustentavel treta",
      "jojo todynho", "malevola", "jojo arrega",
      "tudo sobre a treta", "treta entre", "treta do",
      "boca rosa criticada", "boca rosa e criticada",
      "monark e banido", "monark banido",
      "neymar bateu", "neymar robinho",
      "leviano cita ryan", "tudo sobre a treta entre leviano",
      "historias da plateia", "contos de zolpidem",
      "surpresa na globo", "fazendo amizade #",
      "rafinha bastos",
      "nova capsula fora dos pares", "valorant nova capsula",
      "compramos um novo carro", "avantrip",
      "pabllo vittar e o sertanejo: o entertainment",
      "tao zoando o cristian figueiredo",
      "vaishali e sindarov",
      "head coachs e jogadores", "contratos longos",
      "horarios do futebol brasileiro",
      "champions assinaturas", "jogadores terem champions",
      "nuzlocke", "minecraft", "gta gameplay",
      ], "Entretenimento"),

    (["m4rkim", "davy d. jones", "rocks d. xebec",
      "face do desespero", "ulquiorra", "enygma",
      "shunsui kyoraku", "katen kyokotsu", "kaito",
      ], "RAP Nacional"),

    (["mizael", "estatuto do pcc", "pcc", "crim3",
      "naldinho", "c0m4nd0", "ent0rpecentes",
      "juiza denise frossard", "historia da juiza",
      "historia da farsa", "gugu liberato",
      "adilio patricio", "incels",
      "paulo galo admite", "paulo galo furtou",
      ], "Debate Leve"),
]

_STRUCTURAL_PATTERNS: list[tuple[str, str]] = [
    (r"\baula\s+\d+\b",                        "Debate Denso"),
    (r"\bintroducao\s+ao\s+pensamento\b",       "Debate Denso"),
    (r"\bintroducao\s+a\b",                     "Debate Denso"),
    (r"\bbreve\s+introducao\b",                 "Debate Denso"),
    (r"\bprologo\s+\d+\b",                      "Debate Denso"),
    (r"\bprof\.\s+dr\.\b",                      "Debate Denso"),
    (r"\bufpe\b",                               "Debate Denso"),
    (r"\bpodcast\s+#\d+\b",                     "Debate Leve"),
    (r"\bpodcast\b.*\b#\d{3,}\b",               "Debate Leve"),
    (r"inteligência ltda\.?\s+#\d+",            "Debate Leve"),
    (r"#\d{3,}",                                "Debate Leve"),
    (r"\bvídeo.?resposta\b",                    "Debate Leve"),
    (r"\banalise\s+de\b",                       "Debate Leve"),
    (r"\bo\s+problema\s+d[aeo]\b",              "Debate Leve"),
    (r"\bpor\s+que\b.*\?",                      "Debate Leve"),
    (r"\bo\s+que\s+e\b",                        "Debate Leve"),
    (r"\bcomo\s+e\s+a\s+vida\b",                "Debate Leve"),
    (r"\bexpose\b.*\bsobre\b",                  "Debate Leve"),
    (r"\bco.?stream\b",                         "Entretenimento"),
    (r"\bplayoffs\b.*\bcblol\b",                "Entretenimento"),
    (r"\bao\s+vivo\b.*\b(cblol|vct|lpl)\b",    "Entretenimento"),
    (r"\b(bleach|one piece|dragon ball|naruto|demon slayer)\b.*episod", "Entretenimento"),
    (r"\breview\b.*\b(one piece|bleach|dragon ball)\b.*capítulo", "Debate Leve"),
]

_CONTENT_KEYWORDS: list[tuple[list[str], str]] = [
    (["filosofia", "sociologia", "semiotica", "epistemologia",
      "ontologia", "dialectica", "fenomenologia", "psicanalise",
      "materialismo historico", "fetichismo", "modo de producao"],
     "Debate Denso"),

    (["podcast", "debate", "entrevista", "analise", "documentario",
      "vlog", "rotina", "reflexao", "critica", "ensaio",
      "politica", "economia", "sociedade", "cultura",
      "saude mental", "psicologia", "comportamento",
      "racismo", "feminismo", "lgbtq", "genero",
      "literatura", "cinema", "musica", "arte",
      "quadrinhos", "manga", "anime",
      "educacao", "escola", "universidade",
      "trabalho", "emprego", "sindicato",
      "religiao", "fe", "espiritualidade",
      "historia", "historico", "origens"],
     "Debate Leve"),

    (["gameplay", "ao vivo", "live", "stream",
      "react", "reagindo", "reage",
      "exposed", "exposto", "treta", "polemica",
      "briga", "cancelamento", "chocante",
      "viral", "meme", "humor",
      "reality", "bbb", "big brother",
      "futebol", "gol", "partida", "jogo ao vivo",
      "e-sports", "cblol", "playoffs"],
     "Entretenimento"),
]


def _clean_title(context: str) -> str:
    for suffix in [" - YouTube - Brave", " - YouTube - Google Chrome",
                   " - YouTube - Firefox", " - YouTube",
                   " - Brave", " - Google Chrome", " - Firefox", 
                   " - Microsoft Edge", " - Edge"]:
        if context.endswith(suffix):
            context = context[:-len(suffix)]
    return context.strip()


def _classify_browser_content(context: str) -> str:
    clean = _clean_title(context)
    norm = normalize(clean)

    # 1. Leitura no navegador
    for pat in _LEITURA_PATTERNS:
        if re.search(pat, norm):
            return "Leitura"

    # 2. Canais conhecidos
    for keywords, category in _KNOWN_CHANNELS:
        for kw in keywords:
            if normalize(kw) in norm:
                return category

    # 3. Padrões estruturais
    for pattern, category in _STRUCTURAL_PATTERNS:
        if re.search(pattern, norm):
            return category

    # 4. Keywords de conteúdo
    scores: dict[str, int] = {}
    for keywords, category in _CONTENT_KEYWORDS:
        score = sum(1 for kw in keywords if normalize(kw) in norm)
        if score > 0:
            scores[category] = scores.get(category, 0) + score

    if scores:
        winner = max(scores, key=scores.get)
        if scores.get("Debate Leve", 0) >= scores.get("Entretenimento", 0):
            return "Debate Leve"
        return winner

    # 5. Fallback conservador
    if norm in {"youtube - brave", "youtube", "nova guia", "nova guia - brave",
                "sem titulo", "sem titulo - brave", "fazer login nas contas do google",
                "home / x - brave", "spotify premium", "nova guia - google chrome",
                "youtube - google chrome"}:
        return "Outros"

    WORK_TOOLS = ["vfleets", "fretebras", "google sheets", "google docs",
                  "transporte", "ct-e", "editar ct-e", "inserir ct-e",
                  "localizacao da frota", "analise de movimento",
                  "analise de tempo de permanencia", "historico detalhado",
                  "dreamshaper", "area do aluno", "replit", "code enhancer",
                  "produtividade", "dashboard ao vivo", "tracker"]
    for tool in WORK_TOOLS:
        if normalize(tool) in norm:
            return "Tecnologia"

    if re.search(r"\bhome\s*/\s*x\b", norm) or "twitter.com" in norm:
        return "Social"

    if norm.strip() == "riot client":
        return "Jogos"

    STUDY_BROWSE = ["bibliografia", "campos autonomos", "mana na visao",
                    "planejamento vs", "administracao estrategica",
                    "imaginacao sociologica", "lendo lobato",
                    "mangas semelhantes", "canais parecidos",
                    "genero na tailandia", "manga e nietzsche"]
    for kw in STUDY_BROWSE:
        if normalize(kw) in norm:
            return "Debate Leve"

    return "Debate Leve"


def _classify_spotify(context: str) -> str:
    ctx_norm = normalize(context)
    for artist in RAP_NACIONAL:
        if artist in ctx_norm:
            return "RAP Nacional"
    for artist in MUSICA_BRASILEIRA:
        if artist in ctx_norm:
            return "Música Brasileira"
    return "Música"


def classify_context(app: str, context: str) -> str:
    """Ponto de entrada principal. Corrigido para aceitar múltiplos navegadores."""
    if app in APP_DOMAIN:
        return APP_DOMAIN[app]
    if app == "Spotify":
        return _classify_spotify(context)
    
    # ✅ FIX CRÍTICO: Agora aceita Chrome, Firefox, Edge e variações comuns capturadas pelo tracker
    if app in ("YouTube", "Navegador", "Brave", "Chrome", "Firefox", "Edge", "Google Chrome", "Brave Browser"):
        return _classify_browser_content(context)
        
    return "Outros"


def is_audio_app(app: str) -> bool:
    return app in ["YouTube", "Estudo (Audiobook)", "Lector", "Spotify", "Chrome", "Brave", "Firefox", "Edge"]


def get_active_audio_process(process_keywords: list[str]) -> str | None:
    try:
        from pycaw.pycaw import AudioUtilities
        sessions = AudioUtilities.GetAllSessions()
        for session in sessions:
            if session.Process is None:
                continue
            name = session.Process.name().lower()
            if session.State == 1:
                for kw in process_keywords:
                    if kw.lower() in name:
                        return kw.capitalize()
        return None
    except Exception:
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                name = proc.info["name"].lower()
                for kw in process_keywords:
                    if kw.lower() in name:
                        return kw.capitalize()
        except Exception:
            pass
        return None


def get_window_title_by_process(process_keywords: list[str]) -> tuple[str | None, str | None]:
    try:
        import win32gui, win32process, psutil
        from pycaw.pycaw import AudioUtilities
        active_pid = None
        active_kw = None
        for session in AudioUtilities.GetAllSessions():
            if session.Process is None or session.State != 1:
                continue
            name = session.Process.name().lower()
            for kw in process_keywords:
                if kw.lower() in name:
                    active_pid = session.Process.pid
                    active_kw = kw.capitalize()
                    break
            if active_pid:
                break
        if not active_pid:
            return None, None
        try:
            proc = psutil.Process(active_pid)
            parent = proc.parent()
            siblings = parent.children(recursive=True) if parent else []
            candidate_pids = {active_pid} | {p.pid for p in siblings}
        except Exception:
            candidate_pids = {active_pid}
        best_title = None
        def _visitor(hwnd, _):
            nonlocal best_title
            if not win32gui.IsWindowVisible(hwnd):
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                return
            if pid not in candidate_pids:
                return
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return
            skip = {"", "chrome", "brave", "firefox", "spotify", "chromium",
                    "google chrome", "brave browser", "default ime", "msctfime ui"}
            if title.lower() in skip:
                return
            if best_title is None or len(title) > len(best_title):
                best_title = title
        win32gui.EnumWindows(_visitor, None)
        if best_title:
            return active_kw, best_title
        return active_kw, None
    except Exception:
        try:
            from pycaw.pycaw import AudioUtilities
            for session in AudioUtilities.GetAllSessions():
                if session.Process is None or session.State != 1:
                    continue
                name = session.Process.name().lower()
                for kw in process_keywords:
                    if kw.lower() in name:
                        return kw.capitalize(), None
        except Exception:
            pass
        return None, None