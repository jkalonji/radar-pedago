"""Weekly trends: hot topics (n-gram clusters of French titles, per domain, named by the LLM) and emerging terms."""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from llm import chat_json
from text import tokens

GENERIC_NGRAMS = {
    "enseignement supérieur", "higher education", "sciences éducation", "intelligence artificielle", "école ingénieurs",
    "formation ingénieurs", "engineering education", "pédagogie universitaire", "numéro thématique",
}
EMERGING_MIN_WEEKS = 4      # weeks of history needed before emerging terms are computed
EMERGING_RATIO = 2.0
EMERGING_FLOOR = 3          # a term needs at least this many articles in the latest week


def title_of(r: dict) -> str:
    return r.get("title_fr") or r.get("title") or ""


def title_ngrams(title: str) -> set[str]:
    words = tokens(title)
    grams = {f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)}
    grams |= {f"{words[i]} {words[i + 1]} {words[i + 2]}" for i in range(len(words) - 2)}
    return grams - GENERIC_NGRAMS


# ---------------------------------------------------------------------------
# Hot topics
# ---------------------------------------------------------------------------

def extract_clusters(rows: list[dict], min_articles: int = 3, min_sources: int = 2) -> list[dict]:
    """Articles sharing a distinctive bigram/trigram in their French title, merged when they overlap >= 70%."""
    by_gram: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        for g in title_ngrams(title_of(r)):
            by_gram[g].append(r)
    candidates = []
    for phrase, arts in by_gram.items():
        n_sources = len({a["source"] for a in arts})
        if len(arts) >= min_articles and n_sources >= min_sources:
            candidates.append({"phrase": phrase, "label": phrase.capitalize(), "articles": arts,
                               "article_count": len(arts), "source_count": n_sources, "score": len(arts) * n_sources})
    candidates.sort(key=lambda c: (-c["score"], -len(c["phrase"])))
    merged: list[dict] = []
    for c in candidates:
        urls = {a["url"] for a in c["articles"]}
        for m in merged:
            other = {a["url"] for a in m["articles"]}
            if len(urls & other) / len(urls | other) >= 0.7:
                break
        else:
            merged.append(c)
    return merged


def weekly_hot_topics(rows: list[dict], now: datetime, days: int = 7, per_domain: int = 4) -> list[dict]:
    """Hot topics of the last `days` days, clustered separately in each domain."""
    cutoff = (now - timedelta(days=days)).isoformat()
    recent = [r for r in rows if r["published"] >= cutoff]
    out = []
    for domain in sorted({r["domain"] for r in recent}):
        group = [r for r in recent if r["domain"] == domain]
        clusters = extract_clusters(group) or extract_clusters(group, min_articles=2)
        for c in clusters[:per_domain]:
            out.append({**c, "domain": domain})
    out.sort(key=lambda c: -c["score"])
    logging.info(f"{len(out)} hot topic(s) over the last {days} days")
    return out


async def name_clusters(clusters: list[dict], client, model: str | None = None) -> list[dict]:
    """One LLM call to give each cluster a short French label; n-gram labels are kept on failure."""
    if not clusters or client is None:
        return clusters
    top = clusters[:20]
    items = [{"id": i, "phrase": c["phrase"], "titles": [title_of(a) for a in c["articles"][:4]]}
             for i, c in enumerate(top)]
    system = ("Tu nommes des sujets de veille pour une équipe d'ingénierie pédagogique. Pour chaque item, donne un label "
              "court en français (2 à 5 mots) qui identifie précisément le sujet commun aux titres, par exemple "
              "« Évaluation par compétences », « Réforme de Parcoursup », « IA générative et triche ». Évite les mots "
              'vagues seuls (Pédagogie, Éducation, Actualité). Réponds uniquement avec {"labels": [{"id": 0, "label": "..."}]}.')
    try:
        data = await chat_json(client, system, json.dumps(items, ensure_ascii=False), model=model, max_tokens=900)
        for entry in data.get("labels", []):
            i, label = entry.get("id"), (entry.get("label") or "").strip()
            if isinstance(i, int) and 0 <= i < len(top) and 2 < len(label) <= 80:
                top[i]["label"] = label
    except Exception as e:
        logging.warning(f"Cluster naming failed, keeping n-gram labels: {e}")
    return clusters


# ---------------------------------------------------------------------------
# Emerging terms (week over week)
# ---------------------------------------------------------------------------

def week_start(iso: str) -> str:
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def article_terms(r: dict, exclude: set[str]) -> set[str]:
    words = tokens(title_of(r))
    grams = {w for w in words if len(w) >= 4 and w not in exclude}
    grams |= {f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)
              if words[i] not in exclude and words[i + 1] not in exclude}
    return grams - GENERIC_NGRAMS


def emerging_terms(rows: list[dict], now: datetime, history_start: str | None, exclude: set[str] = frozenset(),
                   weeks_back: int = 4, limit: int = 12) -> dict:
    """Terms whose count over the last 7 days is >= EMERGING_RATIO x their weekly mean over the 4 weeks before.

    Weeks are rolling 7-day windows ending at `now` (the dashboard is published on Monday morning, so the latest
    window is the week just finished). Returns {"ready": bool, "ready_on": iso date, "terms": [...]}.
    """
    ready_on = None
    if history_start:
        start = datetime.fromisoformat(history_start.replace("Z", "+00:00"))
        ready_on = (start + timedelta(weeks=weeks_back + 1)).date().isoformat()
    if not history_start or now.date().isoformat() < ready_on:
        return {"ready": False, "ready_on": ready_on, "terms": []}

    def window(i: int) -> tuple[str, str]:   # i = 0: last 7 days, i = 1: the 7 days before...
        end = now - timedelta(days=7 * i)
        return (end - timedelta(days=7)).isoformat(), end.isoformat()

    counts: list[dict[str, set]] = [defaultdict(set) for _ in range(weeks_back + 1)]
    sources: dict[str, set] = defaultdict(set)
    for r in rows:
        for i in range(weeks_back + 1):
            lo, hi = window(i)
            if lo <= r["published"] < hi:
                for t in article_terms(r, exclude):
                    counts[i][t].add(r["url"])
                    if i == 0:
                        sources[t].add(r["source"])
                break
    out = []
    for term, urls in counts[0].items():
        n = len(urls)
        if n < EMERGING_FLOOR or len(sources[term]) < 2:
            continue
        history = [len(counts[i].get(term, ())) for i in range(1, weeks_back + 1)]
        baseline = sum(history) / weeks_back
        if n >= EMERGING_RATIO * max(baseline, 1.0):
            out.append({"term": term, "count": n, "baseline": round(baseline, 1), "sources": len(sources[term]),
                        "series": list(reversed(history)) + [n]})
    # Overlapping n-grams of the same headlines ("basés llm", "llm panorama"...) are one signal: keep the first term
    # of each group of articles (Jaccard >= 0.6), and never a unigram already inside a kept bigram.
    out.sort(key=lambda t: (-t["count"] / max(t["baseline"], 1.0), -t["count"], -len(t["term"])))
    kept: list[dict] = []
    for t in out:
        urls = counts[0][t["term"]]
        if any(len(urls & counts[0][k["term"]]) / len(urls | counts[0][k["term"]]) >= 0.6 for k in kept):
            continue
        if any(t["term"] in k["term"].split() for k in kept):
            continue
        kept.append(t)
    return {"ready": True, "ready_on": ready_on, "terms": kept[:limit]}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
