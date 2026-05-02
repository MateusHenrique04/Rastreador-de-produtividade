import json
import unicodedata

RULES_FILE = "rules.json"

_rules_cache = None


# =========================
# 🔧 UTIL
# =========================
def normalize(text: str) -> str:
    """Remove acentos e padroniza texto"""
    return unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII").lower()


# =========================
# 📥 LOAD RULES
# =========================
def _load_rules():
    global _rules_cache
    if _rules_cache is None:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _rules_cache = {
            "app_rules": data["app_rules"],
            "content_rules": data["content_rules"],
        }
    return _rules_cache


def get_app_rules():
    return _load_rules()["app_rules"]


def get_content_rules():
    return _load_rules()["content_rules"]


# =========================
# 🖥️ APP DETECTION
# =========================
def split_app_context(title: str) -> tuple[str, str]:
    """Retorna (app, contexto) a partir do título da janela."""
    title_norm = normalize(title)

    for rule in get_app_rules():
        for keyword in rule["match"]:
            if normalize(keyword) in title_norm:
                return rule["app"], title

    return "Outros", title


# =========================
# 🧠 CLASSIFICAÇÃO INTELIGENTE
# =========================
def classify_context(app: str, context: str) -> str:
    context_norm = normalize(context)

    # 🎯 Score inicial
    scores = {
        "Estudo": 0,
        "Aprendizado leve": 0,
        "Entretenimento": 0
    }

    # ⚖️ Pesos (você pode ajustar depois)
    PESOS = {
        "Estudo": 3,
        "Aprendizado leve": 2,
        "Entretenimento": 1
    }

    # 🔎 Aplica regras de conteúdo
    for rule in get_content_rules():
        category = rule["category"]
        for keyword in rule["match"]:
            if normalize(keyword) in context_norm:
                scores[category] += PESOS.get(category, 1)

    # =========================
    # 🎧 Regras especiais
    # =========================

    # Audiobook e Lector sempre puxam forte pra estudo
    if app in ["Estudo (Audiobook)", "Lector"]:
        scores["Estudo"] += 5

    # VS Code é produtividade direta
    if app == "VS Code":
        return "Produtivo"

    # =========================
    # 🧠 Decisão final
    # =========================

    # Se nenhum match
    if all(score == 0 for score in scores.values()):
        if app == "YouTube":
            return "Entretenimento"
        if app == "Spotify":
            return "Entretenimento"
        if context_norm in ("tocando em segundo plano", "spotify premium", "spotify"):
            return "Entretenimento"
        return "Outros"

    # Retorna maior score
    return max(scores, key=scores.get)


# =========================
# 🔊 AUDIO CHECK
# =========================
def is_audio_app(app: str) -> bool:
    return app in ["YouTube", "Estudo (Audiobook)", "Lector", "Spotify"]


def get_active_audio_process(process_keywords: list[str]) -> str | None:
    """
    Verifica se o app está realmente tocando áudio (via pycaw).
    Retorna o nome amigável do processo (ex: 'Spotify') se estiver tocando, senão None.
    Fallback usa psutil se necessário.
    """
    try:
        from pycaw.pycaw import AudioUtilities
        sessions = AudioUtilities.GetAllSessions()

        for session in sessions:
            if session.Process is None:
                continue

            name = session.Process.name().lower()
            state = session.State  # 0=inactive, 1=active

            if state == 1:
                for kw in process_keywords:
                    if kw.lower() in name:
                        return kw.capitalize()

        return None

    except Exception:
        # fallback
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
    """
    Busca o título da janela de um processo que está tocando áudio em segundo plano.
    Usa pycaw para confirmar qual processo está ativo e win32gui para ler o título
    de qualquer janela pertencente a esse processo (mesmo sem foco).

    Retorna (app_amigavel, titulo_da_janela) ou (None, None) se não encontrar.

    Exemplo de retorno:
        ("Youtube", "Lofi Hip Hop Radio - YouTube")
        ("Spotify", "Spotify Premium")
    """
    try:
        import win32gui
        import win32process
        import psutil
        from pycaw.pycaw import AudioUtilities

        # 1. Descobre qual processo está tocando via pycaw
        active_pid = None
        active_kw = None
        sessions = AudioUtilities.GetAllSessions()
        for session in sessions:
            if session.Process is None:
                continue
            if session.State != 1:  # só sessões ativas
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

        # 2. Coleta todos os PIDs do mesmo grupo de processo (ex: várias abas do Chrome)
        try:
            proc = psutil.Process(active_pid)
            parent = proc.parent()
            # Pega todos filhos do pai (abas do navegador) + o próprio processo
            siblings = parent.children(recursive=True) if parent else []
            candidate_pids = {active_pid} | {p.pid for p in siblings}
        except Exception:
            candidate_pids = {active_pid}

        # 3. Varre todas as janelas abertas em busca de uma com título útil
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
            # Ignora títulos genéricos / de chrome sem conteúdo real
            skip = {"", "chrome", "brave", "firefox", "spotify", "chromium",
                    "google chrome", "brave browser", "default ime", "msctfime ui"}
            if title.lower() in skip:
                return
            # Prefere títulos mais longos (mais descritivos)
            if best_title is None or len(title) > len(best_title):
                best_title = title

        win32gui.EnumWindows(_visitor, None)

        if best_title:
            return active_kw, best_title

        # 4. Fallback: sem título útil, retorna só o app
        return active_kw, None

    except Exception:
        # Se win32gui não estiver disponível, cai no comportamento antigo
        try:
            from pycaw.pycaw import AudioUtilities
            sessions = AudioUtilities.GetAllSessions()
            for session in sessions:
                if session.Process is None or session.State != 1:
                    continue
                name = session.Process.name().lower()
                for kw in process_keywords:
                    if kw.lower() in name:
                        return kw.capitalize(), None
        except Exception:
            pass
        return None, None