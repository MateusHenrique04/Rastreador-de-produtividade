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

    # Audiobook sempre puxa forte pra estudo
    if app == "Estudo (Audiobook)":
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
        return "Outros"

    # Retorna maior score
    return max(scores, key=scores.get)


# =========================
# 🔊 AUDIO CHECK
# =========================
def is_audio_app(app: str) -> bool:
    return app in ["YouTube", "Estudo (Audiobook)"]


def is_actually_playing_audio(process_keywords: list[str]) -> bool:
    """
    Verifica se o app está realmente tocando áudio (via pycaw).
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

            if state == 1 and any(kw.lower() in name for kw in process_keywords):
                return True

        return False

    except Exception:
        # fallback
        try:
            import psutil
            for proc in psutil.process_iter(["name"]):
                name = proc.info["name"].lower()
                if any(kw.lower() in name for kw in process_keywords):
                    return True
        except Exception:
            pass

        return False