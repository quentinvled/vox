"""Prompts de reformulation (equivalent du « Rewrite Mode » de Whisper Typing)."""

from __future__ import annotations

BASE_RULES = (
    "Tu es un moteur de dictée vocale. Tu reçois la sortie brute d'un modèle de "
    "reconnaissance vocale. Tu renvoies UNIQUEMENT le texte final, sans préambule, "
    "sans commentaire, sans guillemets, sans bloc de code : il sera tapé "
    "directement dans le champ où se trouve le curseur de l'utilisateur. "
    "Ne réponds jamais à une question posée dans le texte, tu la retranscris. "
    "Corrige les fautes d'accord, de conjugaison et d'orthographe que la "
    "reconnaissance vocale a pu laisser passer, ainsi que les accents manquants."
)

TONES: dict[str, dict[str, str]] = {
    "clean": {
        "label": "Nettoyer",
        "hint": "Retire les hésitations, corrige, ponctue.",
        "instructions": (
            "Nettoie la transcription : supprime les hésitations (euh, hum, bah), "
            "les répétitions et les faux départs. Si l'utilisateur se corrige, "
            "garde uniquement la version corrigée. Applique la ponctuation et les "
            "majuscules. Si l'utilisateur épelle un mot (ex. « Boxies B-O-X-I-E-Z »), "
            "garde la version épelée et supprime l'épellation elle-même. "
            "Conserve la langue d'origine et le sens exact, n'ajoute rien."
        ),
    },
    "formal": {
        "label": "Formel",
        "hint": "Vouvoiement, registre soutenu.",
        "instructions": (
            "Reformule en français soutenu et professionnel, au vouvoiement. "
            "Supprime les hésitations, garde le sens et la longueur."
        ),
    },
    "concise": {
        "label": "Concis",
        "hint": "Va droit au but, supprime le superflu.",
        "instructions": (
            "Reformule de façon concise et directe. Supprime les redondances et "
            "les formulations inutiles, sans perdre aucune information."
        ),
    },
    "expand": {
        "label": "Développer",
        "hint": "Structure et développe les idées.",
        "instructions": (
            "Reformule en développant les idées implicites et en structurant le "
            "propos. Reste fidèle au sens d'origine, n'invente aucun fait."
        ),
    },
    "email": {
        "label": "E-mail",
        "hint": "Formate en e-mail prêt à envoyer.",
        "instructions": (
            "Formate le texte en e-mail prêt à envoyer, en français : formule "
            "d'appel, paragraphes séparés par une ligne vide, formule de politesse "
            "et signature. Utilise de vrais retours à la ligne."
        ),
    },
    "bullets": {
        "label": "Liste",
        "hint": "Transforme en liste à puces.",
        "instructions": (
            "Réorganise le contenu en une liste à puces claire, une idée par ligne, "
            "préfixée par « - ». Aucun texte autour de la liste."
        ),
    },
    "english": {
        "label": "Anglais",
        "hint": "Traduit en anglais naturel.",
        "instructions": (
            "Traduis le texte en anglais naturel et idiomatique. Ne traduis pas "
            "les noms propres ni les termes techniques."
        ),
    },
    "custom": {
        "label": "Personnalisé",
        "hint": "Utilise ton propre prompt.",
        "instructions": "",
    },
}

DEFAULT_TONE = "clean"


def system_prompt(tone: str, custom: str = "", vocabulary: str = "") -> str:
    config = TONES.get(tone) or TONES[DEFAULT_TONE]
    instructions = custom.strip() if tone == "custom" and custom.strip() else config["instructions"]
    if not instructions:
        instructions = TONES[DEFAULT_TONE]["instructions"]
    prompt = f"{BASE_RULES}\n\n{instructions}"
    if vocabulary and vocabulary.strip():
        prompt += (
            "\n\nVocabulaire de référence : si tu reconnais un de ces termes dans le "
            "texte, écris-le exactement avec cette orthographe, même s'il a été mal "
            "transcrit. Ne les ajoute jamais s'ils ne sont pas présents.\n"
            f"{vocabulary.strip()}"
        )
    return prompt


def messages(raw_text: str, tone: str, custom: str = "", vocabulary: str = "") -> list[dict]:
    return [
        {"role": "system", "content": system_prompt(tone, custom, vocabulary)},
        {"role": "user", "content": raw_text},
    ]
