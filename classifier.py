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

    # Fallback para títulos de música que aparecem como "Artista - Faixa"
    if " - " in title and not any(block in title_lower for block in [
        "chrome", "brave", "visual studio", "code", "youtube",
        "audiobook", "desktop", "explorer", "word", "excel",
        "powerpoint", "notepad"
    ]):
        return "Spotify", title

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
    if app == "Spotify":
        return "Entretenimento"

    return "Outros"


def is_audio_app(app: str) -> bool:
    return app in ["YouTube", "Spotify", "Estudo (Audiobook)"]
