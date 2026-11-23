"""Research agent: once a week, searches the web for each domain to find new contents and new sources.

  python discover.py               # weekly run (GitHub Actions, just before the Monday publication)
  python discover.py --dry-run     # print the queries and what they found, store nothing

For each of the 6 domains, the LLM writes search queries (alternately French and English) aimed at categories that
rotate from week to week and at the week's hot topics. Each query goes to Tavily (when TAVILY_API_KEY is set) and to
Google News. Results already stored are dropped; the others go through the usual enrichment (classify.py) with the
"agent" relevance check, so they reach the dashboard in French like any other content, marked as agent finds.

Safety: the LLM never writes a URL. It writes queries and judges the results that the search engines returned; web
text is data for it, never instructions. Agent items are only shown once the LLM has checked them.

Sites missing from sources.json that keep bringing relevant contents become proposed sources (dashboard.py).
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import urllib.parse as up
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import aiohttp
import feedparser

from classify import classify_articles
from entities import EntityIndex
from fetchers import finalize, get_text, gnews_url, looks_like_feed, parse_feed_date
from llm import chat_json, make_client, model_name
from models import DOMAINS, Article, now_utc
from registry import all_sources
from store import Store, get_store
from text import clean_html, guess_lang, normalize
from topics import weekly_hot_topics

TAVILY_URL = "https://api.tavily.com/search"
QUERIES_PER_DOMAIN = 4      # alternately French and English: 24 queries a week, about 100 Tavily credits a month
RESULTS_PER_QUERY = 5       # per search engine
MAX_PER_DOMAIN = 20         # new items sent to the LLM per domain and per run
MAX_AGE_DAYS = 30           # older dated results are not news
KNOWN_DAYS = 400            # stored items older than this may come back (not worth a bigger query)
TIMEOUT = 40

# Never a source of the watch: social networks and hosts that only mirror other sites.
EXCLUDED_HOSTS = ["linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com",
                  "reddit.com", "pinterest.com", "quora.com", "news.google.com", "google.com", "msn.com",
                  "yahoo.com", "wikipedia.org"]
# Shared platforms: one site per first path segment (one journal, one channel), not per host.
PLATFORM_HOSTS = {"journals.openedition.org", "erudit.org", "cairn.info", "youtube.com", "medium.com",
                  "sciencedirect.com", "tandfonline.com", "onlinelibrary.wiley.com", "link.springer.com",
                  "dl.acm.org", "emerald.com", "hal.science", "theses.hal.science", "github.com"}


class SearchError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Sites: what is already followed, how agent finds are grouped
# ---------------------------------------------------------------------------

def _host(url: str) -> str:
    return up.urlparse(url or "").netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")


def site_key(url: str) -> str:
    """'host' or 'host/first-segment' on shared platforms: the unit a proposed source is made of."""
    host = _host(url)
    if host in PLATFORM_HOSTS:
        seg = up.urlparse(url).path.strip("/").split("/")[0]
        return f"{host}/{seg.lower()}" if seg else host
    return host


def site_root(url: str) -> str:
    p = up.urlparse(url)
    if not p.scheme or not p.netloc:
        return ""
    if _host(url) in PLATFORM_HOSTS:
        seg = p.path.strip("/").split("/")[0]
        return f"{p.scheme}://{p.netloc}/{seg}/" if seg else f"{p.scheme}://{p.netloc}/"
    return f"{p.scheme}://{p.netloc}/"


def is_excluded(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in EXCLUDED_HOSTS)


def known_sites(sources: list[dict]) -> dict[str, str]:
    """{site key: source name} for every configured source (urls, fallbacks, `site`, Google News `site:` queries)."""
    out: dict[str, str] = {}
    for s in sources:
        urls = [s.get("url"), s.get("site"), (s.get("fallback") or {}).get("url")]
        for q in (s.get("query"), (s.get("fallback") or {}).get("query")):
            urls += [f"https://{m}" for m in re.findall(r"site:(\S+)", q or "")]
        for u in urls:
            if u and _host(u):
                out.setdefault(site_key(u), s["name"])
    return out


def load_all_sources(path: str = "sources.json", store=None) -> list[dict]:
    """Disabled sources included: a disabled or removed site (LinkedIn...) must not be proposed again."""
    return all_sources(path, store)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def plan_targets(now: datetime, per_domain: int = QUERIES_PER_DOMAIN) -> list[dict]:
    """Per domain, `per_domain` (category, language) targets. Categories rotate each ISO week, languages alternate."""
    year, week, _ = now.isocalendar()
    k = year * 53 + week
    out = []
    for domain, d in DOMAINS.items():
        cats = list(d["categories"])
        for i in range(per_domain):
            cat = cats[(k * per_domain + i) % len(cats)]
            out.append({"id": len(out), "domain": domain, "category": cat, "note": d["categories"][cat],
                        "lang": "fr" if i % 2 == 0 else "en"})
    return out


def weekly_trends(store: Store, now: datetime, per_domain: int = 3) -> dict[str, list[str]]:
    """The week's hot topic phrases, per domain: the agent digs further into what is rising."""
    out: dict[str, list[str]] = {}
    for c in weekly_hot_topics(store.load_articles(8), now):
        phrases = out.setdefault(c["domain"], [])
        if len(phrases) < per_domain:
            phrases.append(c["phrase"])
    return out


QUERY_SYSTEM = (
    "Tu es l'agent de recherche d'une équipe d'ingénierie pédagogique d'une école d'ingénieurs française. Tu écris des "
    "requêtes pour un moteur de recherche web, afin de trouver des contenus récents (études, retours d'expérience, "
    "ressources, actualités) ET des sources que l'équipe ne suit pas encore (sites, blogs, revues, laboratoires, "
    "services d'appui à la pédagogie). Pour chaque cible reçue (domaine, catégorie, langue), écris UNE requête de 3 à 8 "
    "mots dans la langue demandée (\"fr\" : français, \"en\" : anglais). Requêtes précises et concrètes, sans "
    "guillemets ni opérateurs (site:, OR...), sans nom de site. Quand des sujets de la semaine (\"trends\") sont donnés "
    "pour un domaine, oriente la moitié des requêtes de ce domaine vers eux, en restant dans la catégorie. "
    'Réponds UNIQUEMENT avec {"queries": [{"id": <id>, "q": "..."}]}, un élément par cible.'
)


def clean_query(q) -> str:
    q = re.sub(r"\b(site|inurl|intitle|filetype):\S*|[\"“”«»]", " ", str(q or ""))
    q = " ".join(q.split())
    return q[:120] if 2 <= len(q.split()) <= 12 else ""


def fallback_query(target: dict) -> str:
    """Without an LLM answer: the category label (French targets only, an English one would need a translation)."""
    return clean_query(target["category"].replace("&", "et")) if target["lang"] == "fr" else ""


async def write_queries(client, targets: list[dict], trends: dict[str, list[str]], model: str | None = None) -> list[dict]:
    """One LLM call for every target; targets the LLM skipped fall back to their category label."""
    payload = {
        "targets": [{"id": t["id"], "domain": DOMAINS[t["domain"]]["label"], "category": t["category"],
                     "note": t["note"], "lang": t["lang"]} for t in targets],
        "trends": {DOMAINS[d]["label"]: phrases for d, phrases in trends.items() if d in DOMAINS},
    }
    written: dict[int, str] = {}
    try:
        data = await chat_json(client, QUERY_SYSTEM, json.dumps(payload, ensure_ascii=False), model=model, max_tokens=2500)
        for item in data.get("queries", []):
            if isinstance(item, dict) and isinstance(item.get("id"), int) and clean_query(item.get("q")):
                written[item["id"]] = clean_query(item["q"])
    except Exception as e:
        logging.warning(f"Query writing failed, using category labels: {e}")
    plans = []
    for t in targets:
        q = written.get(t["id"]) or fallback_query(t)
        if q:
            plans.append({**t, "q": q, "hits": {}})
    return plans


# ---------------------------------------------------------------------------
# Search engines (results: title, url, excerpt, published, publisher, publisher_url)
# ---------------------------------------------------------------------------

def parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        d = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def search_tavily(session, key: str, query: str, max_results: int = RESULTS_PER_QUERY) -> list[dict]:
    body = {"query": query, "search_depth": "basic", "max_results": max_results, "time_range": "week",
            "exclude_domains": EXCLUDED_HOSTS, "include_answer": False}
    try:
        async with session.post(TAVILY_URL, json=body, headers={"Authorization": f"Bearer {key}"},
                                timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
            if resp.status != 200:
                raise SearchError(f"Tavily HTTP {resp.status}")
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise SearchError(f"Tavily {type(e).__name__}") from e
    return [{"title": clean_html(r.get("title") or ""), "url": (r.get("url") or "").strip(),
             "excerpt": clean_html(r.get("content") or "")[:600], "published": parse_date(r.get("published_date")),
             "publisher": "", "publisher_url": (r.get("url") or "").strip()}
            for r in data.get("results") or [] if isinstance(r, dict)]


async def search_gnews(session, query: str, lang: str, max_results: int = RESULTS_PER_QUERY) -> list[dict]:
    body, _ = await get_text(session, gnews_url(query, lookback_hours=7 * 24, lang=lang), timeout=TIMEOUT)
    if not looks_like_feed(body):
        raise SearchError("Google News: not a feed")
    out = []
    for e in feedparser.parse(body).entries[:max_results]:
        src = e.get("source") or {}
        publisher = (src.get("title") or "").strip()
        title = clean_html(e.get("title", ""))
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3].rstrip()
        out.append({"title": title, "url": (e.get("link") or "").strip(), "excerpt": "",
                    "published": parse_feed_date(e), "publisher": publisher,
                    "publisher_url": (src.get("href") or src.get("url") or "").strip()})
    return out


async def search_all(plans: list[dict], tavily_key: str = "", concurrency: int = 4) -> list[tuple[dict, dict]]:
    """(plan, hit) pairs, in plan order. A failing engine only costs its own results."""
    sem = asyncio.Semaphore(concurrency)
    engines = (["tavily"] if tavily_key else []) + ["gnews"]

    async def one(session, plan, engine):
        async with sem:
            try:
                hits = (await search_tavily(session, tavily_key, plan["q"]) if engine == "tavily"
                        else await search_gnews(session, plan["q"], plan["lang"]))
            except Exception as e:
                logging.warning(f"[{engine}] « {plan['q']} » : {e}")
                hits = []
            plan["hits"][engine] = len(hits)
            return [(plan, h) for h in hits]

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(one(session, p, e) for p in plans for e in engines))
    return [pair for r in results for pair in r]


# ---------------------------------------------------------------------------
# From hits to articles
# ---------------------------------------------------------------------------

def title_key(title: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalize(title)).split())


def to_article(hit: dict, plan: dict, known: dict[str, str], now: datetime) -> Article | None:
    title, url = hit["title"].strip(), hit["url"]
    if len(title) < 10 or not url.startswith(("http://", "https://")):
        return None
    site_url = hit["publisher_url"] if hit["publisher_url"].startswith(("http://", "https://")) else ""
    if is_excluded(site_url or url):   # Google News links without a publisher stay unattributable: dropped
        return None
    pub = hit["published"]
    if pub and (pub < now - timedelta(days=MAX_AGE_DAYS) or pub > now + timedelta(days=1)):
        return None
    name = known.get(site_key(site_url)) if site_url else None
    name = name or hit["publisher"] or (_host(site_url) if site_url else "") or "Recherche web"
    return Article(
        title=title[:400], url=url, source=name[:120], domain=plan["domain"], domains=[plan["domain"]],
        published=(pub or now).isoformat(), published_is_estimated=pub is None, description=hit["excerpt"],
        lang=guess_lang(f"{title}. {hit['excerpt']}"), origin="agent", agent_query=plan["q"],
        site=site_root(site_url) if site_url else "", filter="agent",
    )


def select_new(pairs: list[tuple[dict, dict]], known_sites_map: dict[str, str], known_urls: set[str],
               known_titles: set[str], now: datetime, per_domain: int = MAX_PER_DOMAIN) -> list[Article]:
    """New, deduplicated items (by URL and by title, against the store and each other), capped per domain."""
    out, seen, per = [], set(), {}
    for plan, hit in pairs:
        a = to_article(hit, plan, known_sites_map, now)
        if a is None or a.url in known_urls or a.url in seen:
            continue
        key = title_key(a.title)
        if key in known_titles or key in seen or per.get(a.domain, 0) >= per_domain:
            continue
        seen.update((a.url, key))
        per[a.domain] = per.get(a.domain, 0) + 1
        out.append(a)
    return out


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

async def run_agent(store: Store, index: EntityIndex, client, tavily_key: str = "", sources_path: str = "sources.json",
                    now: datetime | None = None, dry_run: bool = False) -> dict:
    now = now or now_utc()
    plans = await write_queries(client, plan_targets(now), weekly_trends(store, now))
    logging.info(f"Agent: {len(plans)} queries ({'Tavily + ' if tavily_key else ''}Google News)")
    pairs = await search_all(plans, tavily_key)
    stored = store.load_articles(60, relevant_only=False)
    known_titles = {title_key(t) for r in stored for t in (r.get("title"), r.get("title_fr")) if t}
    sites = known_sites(load_all_sources(sources_path, store) + index.school_sources())
    articles = finalize(select_new(pairs, sites, store.known_urls(KNOWN_DAYS), known_titles, now), index)
    logging.info(f"Agent: {len(pairs)} results, {len(articles)} new")
    if dry_run:
        for a in articles:
            print(f"[{a.domain}] {a.title} — {a.source} ({a.url}) ← « {a.agent_query} »")
        return {"plans": plans, "articles": articles}
    if articles:
        await classify_articles(articles, client=client, index=index, max_llm=len(articles))
        store.save_articles(articles)
    write_summary(plans, articles, sites)
    kept = [a for a in articles if a.relevant]
    print(f"::notice::Agent de recherche : {len(plans)} requêtes, {len(articles)} nouveaux résultats, "
          f"{len(kept)} retenus après vérification.")
    return {"plans": plans, "articles": articles}


def summary_markdown(plans: list[dict], articles: list[Article], sites: dict[str, str]) -> str:
    kept = [a for a in articles if a.relevant]
    lines = ["## Agent de recherche", "",
             f"{len(plans)} requêtes · {len(articles)} nouveaux résultats · {len(kept)} retenus après vérification", "",
             "| Sujet | Langue | Requête | Tavily | Google News | Retenus |", "|---|---|---|---:|---:|---:|"]
    for p in plans:
        n = sum(1 for a in kept if a.agent_query == p["q"] and a.domain == p["domain"])
        tav = p["hits"].get("tavily", "–")
        lines.append(f"| {DOMAINS[p['domain']]['label']} | {p['lang']} | {p['q'].replace('|', '/')} | {tav} | "
                     f"{p['hits'].get('gnews', 0)} | {n} |")
    new_sites = sorted({a.source for a in kept if a.site and site_key(a.site) not in sites})
    if new_sites:
        lines += ["", f"Sites hors de sources.json avec un contenu retenu : {', '.join(new_sites)}"]
    return "\n".join(lines) + "\n"


def write_summary(plans, articles, sites, path: str = "output/agent_report.md") -> None:
    md = summary_markdown(plans, articles, sites)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(md)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sources", default="sources.json")
    p.add_argument("--entities", default="entities.json")
    p.add_argument("--dry-run", action="store_true", help="print the queries and new results, store nothing")
    return p.parse_args(argv)


async def main(args: argparse.Namespace) -> int:
    client = make_client()
    if client is None:
        print("::warning::Agent de recherche non lancé : il faut une clé LLM (GROQ_API_KEY ou LLM_API_KEY) pour "
              "écrire les requêtes et vérifier les résultats.")
        return 0
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not key:
        print("::warning::TAVILY_API_KEY absente : l'agent ne cherche que dans Google News.")
    logging.info(f"LLM: {model_name()}")
    await run_agent(get_store(), EntityIndex.load(args.entities), client, key, args.sources, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    sys.exit(asyncio.run(main(parse_args())))
