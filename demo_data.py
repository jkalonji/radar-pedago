"""Demo data: fills a local SQLite base with fictitious items so the dashboard can be previewed without any key.

  python demo_data.py                     # writes data/demo.db
  LOCAL_DB=data/demo.db python dashboard.py --export --no-embeddings --output site/index.html

Every item is invented (sources are suffixed « démo », links point to example.org).
"""

import random
from datetime import datetime, timedelta, timezone

from models import Article, DOMAINS
from store import SqliteStore

TITLES = {
    "design_ux": [
        ("Recherche utilisateur & méthodes", "Co-concevoir un cours avec les étudiants : retour sur trois ateliers de design thinking", "Retour d'expérience"),
        ("Accessibilité & inclusion", "Accessibilité numérique : le RGAA appliqué aux supports de cours", "Outil / ressource"),
        ("Interfaces & design systems", "Un design system pour harmoniser les espaces de cours en ligne", "Retour d'expérience"),
        ("Théorie & recherche en design", "Le design comme pratique de recherche : enjeux épistémologiques", "Étude / recherche"),
        ("UX des dispositifs d'apprentissage", "Parcours apprenant : cartographier les irritants d'une plateforme de formation", "Outil / ressource"),
        ("Recherche utilisateur & méthodes", "Entretiens utilisateurs : guide pratique pour les équipes pédagogiques", "Outil / ressource"),
    ],
    "pedagogie": [
        ("Approche par compétences & évaluation", "Approche par compétences : construire un référentiel en équipe", "Outil / ressource"),
        ("Approche par compétences & évaluation", "Évaluer par compétences en première année d'école d'ingénieurs", "Retour d'expérience"),
        ("Pédagogies actives", "Classe inversée en mathématiques : bilan après deux semestres", "Retour d'expérience"),
        ("Pédagogies actives", "Apprentissage par problèmes : fiches pour démarrer", "Outil / ressource"),
        ("Hybridation & distanciel", "Hybridation des cours : cinq scénarios pédagogiques testés", "Retour d'expérience"),
        ("Ludification & simulation", "Escape game pédagogique : concevoir une énigme alignée sur les objectifs", "Outil / ressource"),
        ("Engagement & accompagnement", "Réussite étudiante : le tutorat par les pairs en première année", "Retour d'expérience"),
        ("Développement pédagogique des enseignants", "Communautés de pratique enseignantes : comment les faire vivre", "Actualité"),
    ],
    "recherche": [
        ("Formation des ingénieurs", "Project-based learning in engineering education: a systematic review", "Étude / recherche"),
        ("Pédagogie du supérieur", "Le feedback formatif dans l'enseignement supérieur : effets sur l'engagement", "Étude / recherche"),
        ("Technologies éducatives", "Generative AI feedback on programming assignments: a randomized study", "Étude / recherche"),
        ("Conception & design", "Design studio pedagogy and the development of design reasoning", "Étude / recherche"),
        ("Sciences cognitives & apprentissage", "Effet de la pratique de récupération sur la mémorisation en amphithéâtre", "Étude / recherche"),
        ("Transitions & enjeux socio-écologiques", "Enseigner les enjeux socio-écologiques en école d'ingénieurs : état des lieux", "Étude / recherche"),
        ("Formation des ingénieurs", "Approche par compétences et formation des ingénieurs : une revue de la littérature", "Étude / recherche"),
    ],
    "enseignement_sup": [
        ("Politiques publiques & réglementation", "Parcoursup 2027 : ce qui change pour les formations d'ingénieurs", "Réglementation"),
        ("Financement & modèles économiques", "Budget de l'enseignement supérieur : les universités sous tension", "Actualité"),
        ("Vie étudiante & insertion", "Insertion des jeunes diplômés : l'enquête annuelle publiée", "Étude / recherche"),
        ("International & mobilité", "Mobilité étudiante : les écoles misent sur les doubles diplômes", "Actualité"),
        ("Classements & accréditations", "Palmarès des écoles d'ingénieurs : les nouveaux critères", "Actualité"),
    ],
    "benchmark": [
        ("Nouvelles formations", "L'Institut Villebon-Charpak ouvre un nouveau parcours en ingénierie", "Actualité"),
        ("Innovation pédagogique", "Villebon-Charpak : un projet interdisciplinaire autour de la transition écologique", "Retour d'expérience"),
        ("Partenariats", "Une école d'ingénieurs signe un partenariat avec un industriel de l'énergie", "Actualité"),
        ("Communication & recrutement", "Journées portes ouvertes : les écoles d'ingénieurs misent sur l'immersion", "Événement"),
    ],
    "innovation_ia": [
        ("IA générative en formation", "IA générative en cours : une charte d'usage pour les enseignants", "Outil / ressource"),
        ("Usages étudiants & intégrité", "Triche et IA générative : repenser l'évaluation plutôt que surveiller", "Actualité"),
        ("Cadres & politiques de l'IA", "AI Act : ce que les établissements doivent anticiper", "Réglementation"),
        ("Outils & EdTech", "Assistants pédagogiques basés sur des LLM : panorama des outils", "Outil / ressource"),
        ("Tech & entrepreneuriat", "Agents IA : ce qui change pour les métiers de la formation", "Podcast / vidéo"),
    ],
}
SOURCES = {
    "design_ux": ["Veille design 5 degrés (démo)", "Design Spot (démo)", "Sciences du Design (démo)"],
    "pedagogie": ["Innovation Pédagogique (démo)", "Louvain Learning Lab (démo)", "IPPA (démo)", "Pedagoscope (démo)"],
    "recherche": ["RIPES (démo)", "European Journal of Engineering Education (démo)", "Design Studies (démo)", "RITPU (démo)"],
    "enseignement_sup": ["Le Monde Campus (démo)", "Actu sup (démo)"],
    "benchmark": ["Institut Villebon (démo)", "Écoles d'ingénieurs (démo)"],
    "innovation_ia": ["À la French (démo)", "IA & éducation (démo)", "IA & sup (démo)"],
}
VARIANTS = ["", " : premiers résultats", " : ce qu'il faut retenir", " — témoignages", " : mode d'emploi", " (dossier)"]
EMERGING = ["Agents IA en formation : premiers usages en école", "Agents IA : quels effets sur l'évaluation ?",
            "Des agents IA pour accompagner les étudiants en projet", "Agents IA et tutorat : retour d'une expérimentation"]


def generate(n_weeks: int = 12, per_week: int = 26, seed: int = 7) -> list[Article]:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    out = []
    for w in range(n_weeks):
        for i in range(per_week + rng.randint(-6, 6)):
            domain = rng.choices(list(TITLES), weights=[2, 5, 6, 3, 1.5, 3])[0]
            cat, title, ctype = rng.choice(TITLES[domain])
            title = title + rng.choice(VARIANTS)
            pub = now - timedelta(days=7 * w + rng.uniform(0, 7))
            out.append(_make(domain, cat, title, ctype, pub, rng, f"{w}-{i}"))
    for j, title in enumerate(EMERGING * 2):   # a surge in the last week -> hot topic + emerging term
        pub = now - timedelta(days=rng.uniform(0, 6))
        out.append(_make("innovation_ia", "IA générative en formation", title, "Actualité", pub, rng, f"e{j}"))
    return out


TRANSLATIONS = {
    "Project-based": "L'apprentissage par projet en formation d'ingénieurs : revue systématique",
    "Generative": "Retours générés par IA sur des exercices de programmation : étude randomisée",
    "Design studio": "La pédagogie de l'atelier de design et le raisonnement de conception",
}


def _make(domain, cat, title, ctype, pub, rng, key) -> Article:
    english = next((k for k in TRANSLATIONS if title.startswith(k)), None)
    title_fr = TRANSLATIONS[english] if english else title
    domains = [domain]
    if "IA" in title_fr and domain != "innovation_ia":
        domains.append("innovation_ia")
    entities = ["villebon_charpak"] if "Villebon" in title else []
    return Article(
        title=title, title_fr=title_fr, url=f"https://example.org/demo/{domain}/{key}", source=rng.choice(SOURCES[domain]),
        domain=domain, domains=domains, published=pub.isoformat(), lang="en" if english else "fr",
        kind="paper" if domain == "recherche" else "article", category=cat, content_type=ctype, entities=entities,
        summary=("Contenu fictif de démonstration. " + "Il illustre le type de résumé en français produit pour chaque "
                 "contenu : de quoi il parle et ce qu'il peut apporter à l'équipe."),
        classified=True,
    )


if __name__ == "__main__":
    import os
    path = os.environ.get("LOCAL_DB", "data/demo.db")
    if os.path.exists(path):
        os.remove(path)
    store = SqliteStore(path)
    articles = generate()
    store.save_articles(articles)
    # pretend the collection started 12 weeks ago so the emerging-terms section is active
    start = (datetime.now(timezone.utc) - timedelta(weeks=12)).isoformat()
    store.conn.execute("UPDATE articles SET collected_at = ?", (start,))
    store.conn.commit()
    print(f"{len(articles)} demo items written to {path}")
