"""Shared data model: the six watch domains, their categories, content types, and the Article record."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Domains (the tags of the dashboard) and their own categories.
# Changing a category = update this file only: the LLM prompt, the rule fallback and the dashboard read it.
# ---------------------------------------------------------------------------

DOMAINS: dict[str, dict] = {
    "design_ux": {
        "label": "Design UX", "emoji": "🎨",
        "categories": {
            "Recherche utilisateur & méthodes": "études utilisateurs, méthodes de conception centrée utilisateur, design thinking, co-conception",
            "Accessibilité & inclusion": "accessibilité numérique, RGAA, handicap, conception inclusive",
            "Interfaces & design systems": "design d'interface, design graphique, systèmes de design, ergonomie",
            "Théorie & recherche en design": "sciences du design, épistémologie du design, design et société, design spéculatif",
            "UX des dispositifs d'apprentissage": "expérience apprenant, ergonomie des plateformes de formation, design pédagogique d'interfaces",
        },
        "default": "Théorie & recherche en design",
    },
    "pedagogie": {
        "label": "Pédagogie", "emoji": "🧑‍🏫",
        "categories": {
            "Approche par compétences & évaluation": "APC, référentiels de compétences, évaluation, feedback, notation, portfolio",
            "Pédagogies actives": "classe inversée, apprentissage par projet ou par problèmes (APP), travail en équipe, études de cas",
            "Hybridation & distanciel": "formation hybride, à distance, MOOC, outils numériques de cours",
            "Ludification & simulation": "jeux sérieux, escape games pédagogiques, simulation, mise en situation",
            "Engagement & accompagnement": "motivation, réussite étudiante, tutorat, accompagnement, bien-être des étudiants",
            "Développement pédagogique des enseignants": "formation des enseignants du supérieur, conseil pédagogique, SoTL, communautés de pratique",
        },
        "default": "Développement pédagogique des enseignants",
    },
    "recherche": {
        "label": "Recherches scientifiques", "emoji": "🔬",
        "categories": {
            "Pédagogie du supérieur": "recherche sur l'enseignement et l'apprentissage dans le supérieur, pédagogie universitaire",
            "Formation des ingénieurs": "engineering education, didactique de l'ingénierie et de l'informatique, computing education",
            "Sciences cognitives & apprentissage": "mémoire, attention, motivation, psychologie de l'apprentissage",
            "Conception & design": "recherche en design, conception, processus de conception, design studies",
            "Technologies éducatives": "EIAH, numérique éducatif, IA en éducation, learning analytics",
            "Transitions & enjeux socio-écologiques": "éducation au développement durable, enjeux climatiques et sociaux dans les formations",
            "Didactique & sciences de l'éducation": "didactique des disciplines, évaluation, orientation, formation des adultes, éducation en général",
        },
        "default": "Didactique & sciences de l'éducation",
    },
    "enseignement_sup": {
        "label": "Enseignement supérieur", "emoji": "🏛️",
        "categories": {
            "Politiques publiques & réglementation": "ministère, lois, Hcéres, CTI, Parcoursup, réformes",
            "Financement & modèles économiques": "budget, frais de scolarité, financement des établissements, privé lucratif",
            "Vie étudiante & insertion": "conditions de vie, santé, insertion professionnelle, alternance",
            "International & mobilité": "mobilité étudiante, attractivité, partenariats internationaux",
            "Classements & accréditations": "palmarès, rankings, labels, accréditations",
        },
        "default": "Politiques publiques & réglementation",
    },
    "benchmark": {
        "label": "Benchmark écoles d'ingénieurs", "emoji": "🏁",
        "categories": {
            "Nouvelles formations": "ouverture de programmes, nouveaux diplômes, nouvelles spécialités",
            "Partenariats": "partenariats entreprises, académiques, internationaux",
            "Innovation pédagogique": "dispositifs pédagogiques nouveaux, projets, labs, pédagogie par projet",
            "Classements & accréditations": "palmarès, accréditations CTI, labels",
            "Campus & investissements": "nouveaux campus, bâtiments, levées de fonds, rapprochements",
            "Communication & recrutement": "admissions, concours, événements, communication institutionnelle",
        },
        "default": "Communication & recrutement",
    },
    "innovation_ia": {
        "label": "Innovation & IA", "emoji": "🤖",
        "categories": {
            "IA générative en formation": "ChatGPT, LLM et IA générative dans l'enseignement, l'évaluation, la préparation de cours",
            "Outils & EdTech": "outils numériques, plateformes, start-up EdTech",
            "Usages étudiants & intégrité": "usages de l'IA par les étudiants, triche, plagiat, intégrité académique",
            "Cadres & politiques de l'IA": "AI Act, chartes d'établissement, régulation, éthique de l'IA",
            "Tech & entrepreneuriat": "actualité tech et IA, start-up, entrepreneuriat, innovation en entreprise",
        },
        "default": "Tech & entrepreneuriat",
    },
}

DOMAIN_IDS = list(DOMAINS)

CONTENT_TYPES: dict[str, str] = {
    "Étude / recherche": "📄",
    "Retour d'expérience": "💬",
    "Outil / ressource": "🧰",
    "Actualité": "📰",
    "Réglementation": "⚖️",
    "Événement": "📅",
    "Podcast / vidéo": "🎙️",
}
DEFAULT_CONTENT_TYPE = "Actualité"


def categories(domain: str) -> list[str]:
    return list(DOMAINS[domain]["categories"]) if domain in DOMAINS else []


def default_category(domain: str) -> str:
    return DOMAINS[domain]["default"] if domain in DOMAINS else ""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_str() -> str:
    return now_utc().strftime("%Y-%m-%d")


@dataclass
class Article:
    title: str                          # original title, as published
    url: str
    source: str
    domain: str                         # primary domain, from the source (see sources.json)
    published: str                      # ISO 8601 datetime (UTC)
    description: str = ""               # feed excerpt / abstract (never the full paid text)
    lang: str = "fr"                    # detected language of title + description
    kind: str = "article"               # article | paper | page | video
    title_fr: str = ""                  # French title (translated by the LLM when the original is not French)
    summary: str = ""                   # 2-3 sentence French summary
    category: str = ""                  # one of DOMAINS[domain]["categories"]
    content_type: str = ""              # one of CONTENT_TYPES
    domains: list[str] = field(default_factory=list)   # primary domain + secondary ones (tag filtering)
    entities: list[str] = field(default_factory=list)  # school ids from entities.json
    classified: bool = False            # False until the LLM (or the rule fallback) has run
    published_is_estimated: bool = False
    filter: str = ""                    # source filter (e.g. "superieur") — not persisted
    relevant: bool = True               # set by the classifier for filtered sources — not persisted

    @property
    def day(self) -> str:
        return self.published[:10]
