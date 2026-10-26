"""Article enrichment: category, content type, secondary domains, relevance, French title and French summary.

The LLM works in batches grouped by domain (so the prompt only lists that domain's categories). Without a key, or
when a batch fails, a rule-based fallback fills every field and leaves `classified=False`: those articles are
sent to the LLM again on the next runs.
"""

import asyncio
import json
import logging
import re

from entities import EntityIndex
from llm import chat_json, model_name
from models import (CONTENT_TYPES, DEFAULT_CONTENT_TYPE, DOMAIN_IDS, DOMAINS, Article, categories,
                    default_category)
from text import normalize

BATCH_SIZE = 10
BATCH_PAUSE = 3.0
MAX_LLM_ARTICLES = 300   # per run: keeps a big backfill within the free Groq quota (the rest is retried later)

FILTER_NOTES = {
    "superieur": ("cette revue est généraliste : relevant=true seulement si l'article concerne l'enseignement supérieur, "
                  "la formation des ingénieurs ou des adultes, la formation des enseignants, le design ou la conception, "
                  "l'IA ou le numérique en formation, ou s'il apporte un résultat transposable au supérieur ; "
                  "relevant=false pour ce qui ne concerne que l'école primaire ou le secondaire sans transposition possible."),
}


def _system_prompt(domain: str) -> str:
    d = DOMAINS[domain]
    cats = "\n".join(f'  - "{c}" : {note}' for c, note in d["categories"].items())
    others = ", ".join(f'"{k}" ({v["label"]})' for k, v in DOMAINS.items() if k != domain)
    types = json.dumps(list(CONTENT_TYPES), ensure_ascii=False)
    return (
        "Tu assistes une équipe d'ingénierie pédagogique d'une école d'ingénieurs française qui fait de la veille. "
        f"Domaine des articles reçus : « {d['label']} ». Pour chaque article (titre, source, extrait), renvoie :\n"
        f'- "category" : exactement une valeur parmi :\n{cats}\n'
        f'- "content_type" : une valeur parmi {types}\n'
        f'- "other_domains" : liste (souvent vide) d\'autres domaines nettement concernés, parmi {others}\n'
        '- "title_fr" : le titre en français. S\'il est déjà en français, recopie-le tel quel ; sinon traduis-le '
        "fidèlement (garde les noms propres et sigles).\n"
        '- "summary" : 2 à 3 phrases en français (35 à 60 mots) qui disent de quoi parle le contenu et ce qu\'il apporte '
        "à l'équipe. Appuie-toi UNIQUEMENT sur le titre et l'extrait, sans rien inventer ; si l'extrait est vide, "
        "une seule phrase prudente sur ce que le titre annonce. Reformule, ne recopie pas l'extrait.\n"
        '- "relevant" : true, sauf si "check_relevance" est présent : applique alors la consigne donnée.\n'
        'Réponds UNIQUEMENT avec un objet JSON {"items": [{"id": <id>, "category": ..., "content_type": ..., '
        '"other_domains": [...], "title_fr": ..., "summary": ..., "relevant": ...}]} avec un élément par article reçu.'
    )


def _user_payload(batch: list[Article]) -> str:
    items = []
    for i, a in enumerate(batch):
        item = {"id": i, "title": a.title, "source": a.source, "excerpt": a.description[:600]}
        if a.filter in FILTER_NOTES:
            item["check_relevance"] = FILTER_NOTES[a.filter]
        items.append(item)
    return json.dumps(items, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Rule-based fallback
# ---------------------------------------------------------------------------

def _phrases(note: str) -> list[re.Pattern]:
    parts = [p.strip() for p in re.split(r"[,;()]| et | ou ", normalize(note)) if len(p.strip()) >= 3]
    return [re.compile(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])") for p in parts]


_CATEGORY_RULES = {d: [(c, _phrases(f"{c}, {note}")) for c, note in DOMAINS[d]["categories"].items()] for d in DOMAINS}

_TYPE_RULES = [
    ("Podcast / vidéo", r"podcast|episode|vid[eé]o|webinaire|replay|youtube"),
    ("Événement", r"colloque|congr[eè]s|journ[eé]e d.[eé]tude|conf[eé]rence|s[eé]minaire|appel [aà] communication|"
                  r"inscriptions?|salon|rencontres?"),
    ("Réglementation", r"d[eé]cret|loi |arr[eê]t[eé]|circulaire|r[eé]forme|r[eé]glement|ai act|cadre l[eé]gal"),
    ("Outil / ressource", r"guide|fiche|outil|ressource|tutoriel|boite [aà] outils|toolkit|mode d.emploi|kit"),
    ("Retour d'expérience", r"retour d.exp[eé]rience|t[eé]moignage|exp[eé]rimentation|dispositif|mise en place|"
                            r"case study|lessons learned"),
]
_TYPE_RULES = [(t, re.compile(normalize(p))) for t, p in _TYPE_RULES]


def fallback_category(article: Article) -> str:
    text = normalize(f"{article.title} {article.description}")
    best, best_hits = default_category(article.domain), 0
    for cat, patterns in _CATEGORY_RULES.get(article.domain, []):
        hits = sum(1 for p in patterns if p.search(text))
        if hits > best_hits:
            best, best_hits = cat, hits
    return best


def fallback_content_type(article: Article) -> str:
    if article.kind == "paper":
        return "Étude / recherche"
    if article.kind == "video":
        return "Podcast / vidéo"
    text = normalize(f"{article.title} {article.description}")
    for ctype, pattern in _TYPE_RULES:
        if pattern.search(text):
            return ctype
    return DEFAULT_CONTENT_TYPE


def fallback_summary(article: Article) -> str:
    """The French excerpt, trimmed to two sentences (nothing for non-French excerpts: no translation without LLM)."""
    if article.lang != "fr" or not article.description:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", article.description)
    out = " ".join(sentences[:2])
    return out if len(out) <= 320 else out[:317].rsplit(" ", 1)[0] + "…"


def apply_fallback(article: Article, index: EntityIndex | None = None) -> None:
    article.category = fallback_category(article)
    article.content_type = fallback_content_type(article)
    article.title_fr = article.title_fr or article.title
    article.summary = article.summary or fallback_summary(article)
    if article.domain not in article.domains:
        article.domains.insert(0, article.domain)
    if article.filter and index is not None:
        article.relevant = index.is_relevant(f"{article.title} {article.description}", article.filter)
    article.classified = False


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

def _apply_item(article: Article, item: dict, index: EntityIndex | None) -> None:
    cat = item.get("category")
    article.category = cat if cat in categories(article.domain) else fallback_category(article)
    ctype = item.get("content_type")
    article.content_type = ctype if ctype in CONTENT_TYPES else fallback_content_type(article)
    if article.kind == "paper" and article.content_type == DEFAULT_CONTENT_TYPE:
        article.content_type = "Étude / recherche"
    others = [d for d in (item.get("other_domains") or []) if d in DOMAIN_IDS and d != article.domain]
    article.domains = list(dict.fromkeys([article.domain, *article.domains, *others]))
    title_fr = (item.get("title_fr") or "").strip()
    article.title_fr = title_fr if 3 <= len(title_fr) <= 400 else article.title
    summary = (item.get("summary") or "").strip()
    article.summary = summary[:700] if summary else fallback_summary(article)
    if article.filter:
        article.relevant = item.get("relevant", True) is not False
    article.classified = True


async def _classify_batch(client, model: str, batch: list[Article], index: EntityIndex | None) -> bool:
    try:
        data = await chat_json(client, _system_prompt(batch[0].domain), _user_payload(batch), model=model)
        by_id = {it.get("id"): it for it in data.get("items", []) if isinstance(it, dict)}
        if not by_id:
            raise ValueError("no items in response")
        for i, article in enumerate(batch):
            if i in by_id:
                _apply_item(article, by_id[i], index)
            else:
                apply_fallback(article, index)
        return True
    except Exception as e:
        logging.warning(f"LLM batch failed ({len(batch)} articles, {batch[0].domain}): {e}")
        for article in batch:
            apply_fallback(article, index)
        return False


async def classify_articles(articles: list[Article], client=None, index: EntityIndex | None = None,
                            model: str | None = None, batch_size: int = BATCH_SIZE, pause: float = BATCH_PAUSE,
                            max_llm: int = MAX_LLM_ARTICLES) -> list[Article]:
    """Enrich articles in place. Returns them all; irrelevant ones keep relevant=False (dropped by the caller)."""
    for a in articles:
        if a.domain not in a.domains:
            a.domains.insert(0, a.domain)
    if client is None:
        logging.warning("No LLM key set - using the rule-based classifier (no translation, no generated summary)")
        for a in articles:
            apply_fallback(a, index)
        return articles

    model = model or model_name()
    for_llm, later = articles[:max_llm], articles[max_llm:]
    for a in later:
        apply_fallback(a, index)
    if later:
        logging.warning(f"{len(later)} articles above the per-run LLM budget: rule fallback now, LLM on a later run")
    batches = []
    for domain in DOMAIN_IDS:
        group = [a for a in for_llm if a.domain == domain]
        batches += [group[i:i + batch_size] for i in range(0, len(group), batch_size)]
    failures = 0
    for i, batch in enumerate(batches):
        logging.info(f"LLM: batch {i + 1}/{len(batches)} ({len(batch)} articles, {batch[0].domain})")
        if not await _classify_batch(client, model, batch, index):
            failures += 1
        if i < len(batches) - 1:
            await asyncio.sleep(pause)
    if batches and failures / len(batches) > 0.2:
        msg = (f"LLM failed on {failures}/{len(batches)} batches - check LLM_API_KEY/GROQ_API_KEY and the "
               f"LLM_MODEL/GROQ_MODEL variable (current: {model}). Articles were kept with the rule fallback.")
        logging.error(msg)
        print(f"::error::{msg}")
    return articles
