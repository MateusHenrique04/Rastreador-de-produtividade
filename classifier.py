import json
import os

RULES_FILE = "rules.json"

_rules_cache = None
_rules_mtime = 0.0


def _load_rules():
    global _rules_cache, _rules_mtime
    try:
        mtime = os.path.getmtime(RULES_FILE)
    except OSError:
        mtime = 0.0

    if _rules_cache is None or mtime != _rules_mtime:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "app_rules" not in data:
            raise ValueError("rules.json deve conter a chave 'app_rules'")
        if "content_rules" not in data:
            raise ValueError("rules.json deve conter a chave 'content_rules'")
        for rule in data["app_rules"]:
            if "match" not in rule:
                raise ValueError(f"Regra de app sem campo 'match': {rule}")
        for rule in data["content_rules"]:
            if "category" not in rule:
                raise ValueError(f"Regra de conteúdo sem campo 'category': {rule}")

        _rules_cache = {
            "app_rules": data["app_rules"],
            "content_rules": data["content_rules"],
            "screen_category_rules": data.get("screen_category_rules", []),
        }
        _rules_mtime = mtime

    return _rules_cache


def get_app_rules():
    return _load_rules()["app_rules"]


def get_content_rules():
    return _load_rules()["content_rules"]


def get_screen_category_rules():
    return _load_rules()["screen_category_rules"]


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


def classify_app_category(app: str) -> str:
    """Classifica um app de tela em uma categoria (definida em screen_category_rules)."""
    for rule in get_screen_category_rules():
        if rule["app"] == app:
            return rule["category"]
    return "Outros"


def is_audio_app(app: str) -> bool:
    return app in ["YouTube", "Estudo (Audiobook)"]