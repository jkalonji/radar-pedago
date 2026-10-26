"""Text helpers shared by the pipeline and the dashboard: normalization, language guess, tokens, stopwords."""

import re
import unicodedata


def normalize(text: str) -> str:
    """Lowercase + strip accents so 'pédagogie' matches 'pedagogie'."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"), ("&rsquo;", "’"), ("&lt;", "<"),
                    ("&gt;", ">"), ("&eacute;", "é"), ("&egrave;", "è"), ("&agrave;", "à"), ("&ccedil;", "ç")):
        text = text.replace(ent, ch)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Language guess (French vs other): ratio of very common French vs English function words.
# ---------------------------------------------------------------------------

_FR_MARKERS = set("le la les des une du de et est dans pour sur avec par que qui au aux ce cette ses son leur leurs "
                  "nous vous être sont entre comme plus mais ou où l d qu à".split())
_EN_MARKERS = set("the and of to in for on with by from is are an as at this that these their its into how what "
                  "between among students study learning using".split())
_WORD_RE = re.compile(r"[a-zà-ÿ]+")


def guess_lang(text: str) -> str:
    """'fr', 'en' or 'other' — cheap heuristic, good enough to decide whether a title needs translating."""
    words = _WORD_RE.findall((text or "").lower().replace("’", " ").replace("'", " "))
    if not words:
        return "fr"
    fr = sum(w in _FR_MARKERS for w in words) + 2 * sum(1 for c in text if c in "éèêàçùâîôû")
    en = sum(w in _EN_MARKERS for w in words)
    if fr == 0 and en == 0:
        return "other"
    return "fr" if fr >= en else "en"


# ---------------------------------------------------------------------------
# Tokens for the word cloud, hot topics and emerging terms (mirrored in dashboard_template.html).
# ---------------------------------------------------------------------------

_FR = """les des une dans pour avec sur par que qui sont était été être avoir fait faire plus moins très aussi mais donc
car est ont ses son sa leur leurs cette ces cet nous vous ils elles lui elle entre après avant sans sous chez comme
tout tous toute toutes encore déjà depuis pendant vers contre selon dont où quand comment pourquoi quoi quel quelle
quels quelles peut peuvent doit doivent nouveau nouvelle nouveaux nouvelles premier première dernier dernière ans
jour jours semaine semaines mois année années fois une aux the and for with from that this how what why can more
its their are not into about between through using use new study studies case analysis approach role effects effect
based towards toward among within une deux trois cas étude études analyse approche rôle effet effets lors autour
ainsi enjeux enjeu question questions regard regards point points cadre partir travers pratiques pratique""".split()
_GENERIC = """article articles numéro dossier dossiers revue revues journal volume vol issue special spécial
introduction éditorial editorial actes colloque congrès présentation compte rendu note notes lecture lectures
podcast épisode episode vidéo video live replay webinaire webinar inscription inscriptions programme
actualité actualités news lire voir découvrir savoir comprendre guide guides fiche fiches ressources ressource
page accueil contact plus suite faut retenir premiers premières misent mise change changent publiée publié
voici quoi tour bilan mode plutôt""".split()

STOPWORDS: set[str] = set(_FR) | set(_GENERIC)
_TOKEN_RE = re.compile(r"[a-zà-ÿ0-9][a-zà-ÿ0-9\-]*")


def tokens(text: str, min_len: int = 3) -> list[str]:
    """Lowercased content tokens without stopwords, numbers and hyphenated compounds containing a stopword."""
    out = []
    for w in _TOKEN_RE.findall((text or "").lower().replace("’", " ").replace("'", " ")):
        w = w.strip("-")
        if len(w) < min_len or w in STOPWORDS or w.isdigit():
            continue
        if "-" in w and any(part in STOPWORDS for part in w.split("-")):
            continue
        out.append(w)
    return out
