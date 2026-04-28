import json

RULES_FILE = "rules.json"

_rules_cache = None


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


def split_app_context(title: str) -> tuple[str, str]:
    """Retorna (app, contexto) a partir do título da janela."""
    title_lower = title.lower()

    for rule in get_app_rules():
        for keyword in rule["match"]:
            if keyword.lower() in title_lower:
                return rule["app"], title

    return "Outros", title


def classify_context(app: str, context: str) -> str:
    """Classifica o contexto de áudio em uma categoria."""
    context_lower = context.lower()

    for rule in get_content_rules():
        for keyword in rule["match"]:
            if keyword.lower() in context_lower:
                return rule["category"]

    if app == "Estudo (Audiobook)":
        return "Estudo"
    if app == "VS Code":
        return "Produtivo"
    if app == "YouTube":
        return "YouTube (não classificado)"

    return "Outros"


def is_audio_app(app: str) -> bool:
    return app in ["YouTube", "Estudo (Audiobook)"]


def is_actually_playing_audio(process_keywords: list[str]) -> bool:
    """
    Retorna True se algum processo da lista está com sessão de áudio ativa
    (ou seja, está realmente reproduzindo som agora).

    Usa a API de sessões de áudio do Windows (pycaw).
    Se pycaw não estiver instalado, cai num fallback que checa apenas
    se o processo está rodando — menos preciso, mas sem crash.
    """
    try:
        from pycaw.pycaw import AudioUtilities
        sessions = AudioUtilities.GetAllSessions()
        for session in sessions:
            if session.Process is None:
                continue
            name = session.Process.name().lower()
            state = session.State  # 0=inactive, 1=active, 2=expired
            if state == 1 and any(kw.lower() in name for kw in process_keywords):
                return True
        return False
    except Exception:
        # Fallback: verifica só se o processo existe (sem checar se toca áudio)
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                name = proc.info["name"].lower()
                if any(kw.lower() in name for kw in process_keywords):
                    return True
        except Exception:
            pass
        return False