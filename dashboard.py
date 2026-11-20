"""Radar Pédago dashboard - static export for GitHub Pages.

  python dashboard.py --export --output site/index.html       # also writes site/embeddings.bin and site/sources.html
  python dashboard.py --export --no-embeddings                 # keyword search only (faster, no model download)

The data is embedded as JSON in a single page and filtered in the browser (tags, search, period), so the published
page needs no backend and no API key. Hot topic labels are named by the LLM at export time when a key is set.
"""

import argparse
import asyncio
import html
import json
import logging
import os
import re
import urllib.parse as up
from collections import Counter
from datetime import datetime, timezone

from discover import known_sites, site_key
from embeddings import DIM, MODEL_ID, build_embeddings
from entities import EntityIndex
from llm import make_client
from models import CONTENT_TYPES, DOMAINS
from store import Store, get_store
from text import STOPWORDS, normalize
from topics import emerging_terms, name_clusters, weekly_hot_topics

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_template.html")
SOURCES_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources_template.html")
WINDOW_DAYS = 26 * 7 + 1
CANDIDATE_MIN = 2           # relevant agent finds a site needs before it is proposed as a source
MAX_CANDIDATES = 24


def _article(r: dict) -> dict:
    """Compact record for the page (short keys keep the embedded JSON small; agent keys only on agent finds)."""
    title_fr = r.get("title_fr") or r["title"]
    out = {"t": title_fr, "o": r["title"] if r["title"] != title_fr else "", "u": r["url"], "s": r.get("source") or "",
           "d": r["domain"], "ds": r.get("domains") or [r["domain"]], "p": r["published"],
           "est": bool(r.get("published_is_estimated")), "k": r.get("kind") or "article", "c": r.get("category") or "",
           "y": r.get("content_type") or "", "sm": r.get("summary") or "", "e": r.get("entities") or []}
    if r.get("origin") == "agent":
        out.update(ag=1, aq=r.get("agent_query") or "")
    return out


def candidate_sources(rows: list[dict], configs: dict[str, dict], limit: int = MAX_CANDIDATES) -> list[dict]:
    """Sites outside sources.json that brought at least CANDIDATE_MIN relevant agent finds, most productive first."""
    known = known_sites(list(configs.values()))
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("origin") == "agent" and r.get("site") and r.get("relevant", True):
            key = site_key(r["site"])
            if key not in known:
                groups.setdefault(key, []).append(r)
    out = []
    for rs in groups.values():
        if len(rs) < CANDIDATE_MIN:
            continue
        rs = sorted(rs, key=lambda r: r["published"], reverse=True)
        out.append({
            "name": Counter(r.get("source") or "" for r in rs).most_common(1)[0][0] or site_key(rs[0]["site"]),
            "domain": Counter(r["domain"] for r in rs).most_common(1)[0][0], "url": rs[0]["site"], "count": len(rs),
            "last": rs[0]["published"], "examples": [{"t": r.get("title_fr") or r["title"], "u": r["url"]} for r in rs[:3]],
            "queries": list(dict.fromkeys(r["agent_query"] for r in rs if r.get("agent_query")))[:3],
        })
    out.sort(key=lambda c: c["last"], reverse=True)
    out.sort(key=lambda c: c["count"], reverse=True)   # stable: equal counts stay most recent first
    return out[:limit]


def history_start(rows: list[dict]) -> str | None:
    """First collection date (not publication date: journals and backfills bring older items)."""
    dates = [r.get("collected_at") or r["published"] for r in rows]
    return min(dates) if dates else None


async def build_payload(store: Store, index: EntityIndex, days: int = WINDOW_DAYS, client=None,
                        with_embeddings: bool = True, encoder=None) -> tuple[dict, bytes | None]:
    rows = store.load_articles(days)
    now = datetime.now(timezone.utc)
    hot = await name_clusters(weekly_hot_topics(rows, now), client)
    start = history_start(store.load_articles(days, relevant_only=False))
    emerging = emerging_terms(rows, now, start, exclude=index.alias_words())
    vectors = build_embeddings(rows, encoder) if with_embeddings else None
    payload = {
        "generated_at": now.isoformat(timespec="minutes"),
        "demo": bool(rows) and all(r["url"].startswith("https://example.org/demo/") for r in rows),
        "days": days,
        "history_start": start,
        "domains": [{"id": k, "label": v["label"], "emoji": v["emoji"], "categories": list(v["categories"])}
                    for k, v in DOMAINS.items()],
        "content_types": [{"name": k, "emoji": v} for k, v in CONTENT_TYPES.items()],
        "articles": [_article(r) for r in rows],
        "hot": [{"label": c["label"], "domain": c["domain"], "urls": [a["url"] for a in c["articles"]],
                 "count": c["article_count"], "sources": c["source_count"]} for c in hot],
        "emerging": emerging,
        "candidate_sources": candidate_sources(rows, load_source_configs(index=index)),
        "schools": [{"id": s.id, "label": s.label, "group": s.group} for s in index.schools],
        "alias_words": sorted(index.alias_words()),
        "stopwords": sorted(STOPWORDS),
        "glossary": load_glossary(),
        "embeddings": ({"file": "embeddings.bin", "dim": DIM, "model": MODEL_ID, "count": len(rows)}
                       if vectors else None),
    }
    return payload, vectors


def load_glossary(path: str = "glossary.json") -> list[list[str]]:
    try:
        with open(path, encoding="utf-8") as f:
            return [e["terms"] for e in json.load(f).get("entries", []) if e.get("terms")]
    except FileNotFoundError:
        return []


def load_source_configs(path: str = "sources.json", index: EntityIndex | None = None) -> dict[str, dict]:
    """Every configured source by name, disabled ones included: they may still own contents in the window."""
    try:
        with open(path, encoding="utf-8") as f:
            sources = [s for s in json.load(f)["sources"] if "name" in s]
    except FileNotFoundError:
        sources = []
    sources += index.school_sources() if index else []
    return {s["name"]: s for s in sources}


def site_url(source: dict) -> str:
    """Human-facing address of a source (its site, not the feed or API it is collected from); '' when unknown."""
    stype = source.get("type")
    if source.get("site"):   # explicit override in sources.json, e.g. a journal collected through Crossref
        return source["site"]
    if (source.get("fallback") or {}).get("url"):
        return source["fallback"]["url"]
    if stype == "rss" and source.get("url"):
        p = up.urlparse(source["url"])
        return f"{p.scheme}://{p.netloc}/"
    if stype in ("auto", "page") and source.get("url"):
        return source["url"]
    if stype == "gnews" and source.get("query"):
        query = re.sub(r"\s*when:\d+[dh]", "", source["query"]).strip()
        return f"https://news.google.com/search?q={up.quote(query)}&hl=fr&gl=FR&ceid=FR:fr"
    if stype == "youtube" and source.get("handle"):
        return f"https://www.youtube.com/@{source['handle'].lstrip('@')}"
    return ""


def source_directory(articles: list[dict], configs: dict[str, dict]) -> list[dict]:
    """Sources that brought at least one page article (compact records), each listed once, by domain then name."""
    counts = Counter(a["s"] for a in articles)
    domains = {}
    for a in articles:   # unconfigured sources (renamed, demo) fall back to the domain of their contents
        domains.setdefault(a["s"], Counter())[a["d"]] += 1
    order = list(DOMAINS)
    out = []
    for name, n in counts.items():
        if not name:
            continue
        cfg = configs.get(name, {})
        domain = cfg.get("domain") if cfg.get("domain") in DOMAINS else domains[name].most_common(1)[0][0]
        out.append({"name": name, "domain": domain, "url": site_url(cfg), "count": n})
    return sorted(out, key=lambda s: (order.index(s["domain"]) if s["domain"] in order else len(order),
                                      normalize(s["name"])))


def source_entry(c: dict) -> str:
    """The sources.json line for a proposed source: `auto` finds its feed, or watches its page for new links."""
    return json.dumps({"name": c["name"], "domain": c["domain"], "type": "auto", "url": c["url"], "suggested": True},
                      ensure_ascii=False)


def proposal_issue_url(c: dict, repo: str) -> str:
    """Pre-filled GitHub issue asking the team to add a proposed source ('' without a valid owner/repo)."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        return ""
    body = "\n".join([
        f"L'agent de recherche propose d'ajouter **{c['name']}** ({c['url']}) au sujet "
        f"« {DOMAINS[c['domain']]['label']} ».", "",
        f"{c['count']} contenus jugés pertinents, par exemple :", *[f"- [{x['t']}]({x['u']})" for x in c["examples"]], "",
        "Si l'équipe valide : ajouter cette ligne dans `sources.json`, puis lancer le workflow « Check sources ».", "",
        "```json", source_entry(c), "```"])
    return f"https://github.com/{repo}/issues/new?" + up.urlencode({"title": f"Source proposée : {c['name']}", "body": body})


def render_candidates_html(candidates: list[dict], repo: str, agent_ran: bool) -> str:
    e = html.escape
    order = list(DOMAINS)
    if not candidates:
        msg = ("Aucune source proposée pour l'instant. L'agent en propose une dès qu'un site qui ne fait pas partie de la "
               f"veille lui a fourni au moins {CANDIDATE_MIN} contenus pertinents." if agent_ran else
               "Aucun contenu de l'agent de recherche sur la période : il cherche chaque lundi, avant la publication.")
        return f'    <p class="empty">{e(msg)}</p>'
    cards = []
    for c in candidates:
        i = order.index(c["domain"]) + 1 if c["domain"] in order else 1
        examples = "".join(f'<li><a href="{e(x["u"])}" target="_blank" rel="noopener noreferrer">{e(x["t"])}</a></li>'
                           for x in c["examples"])
        queries = ", ".join(f"« {e(q)} »" for q in c["queries"])
        issue = proposal_issue_url(c, repo)
        action = (f'<a class="btn" href="{e(issue)}" target="_blank" rel="noopener noreferrer">Proposer l\'ajout</a>'
                  if issue else "")
        cards.append(
            f'    <article class="card cand"><h3><a href="{e(c["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{e(c["name"])}<span class="ext" aria-hidden="true">↗</span></a></h3>'
            f'<p class="m"><span class="sw" style="background:var(--dom-{i})"></span>{e(DOMAINS[c["domain"]]["label"])} · '
            f'{c["count"]} contenus pertinents · dernier le {e(datetime.fromisoformat(c["last"]).strftime("%d/%m/%Y"))}</p>'
            f'<ul class="ex">{examples}</ul>'
            + (f'<p class="m">Trouvée avec : {queries}</p>' if queries else "")
            + f'<div class="actions">{action}<details><summary>Ligne à ajouter dans sources.json</summary>'
              f'<pre>{e(source_entry(c))}</pre></details></div></article>')
    return "\n".join(cards)


def render_sources_html(directory: list[dict], generated_at: datetime, days: int, candidates: list[dict] = (),
                        repo: str = "", agent_ran: bool = False) -> str:
    with open(SOURCES_TEMPLATE, encoding="utf-8") as f:
        template = f.read()
    e = html.escape
    plural = lambda n, w: f"{n} {w}{'s' if n > 1 else ''}"

    def item(s: dict) -> str:
        name = e(s["name"])
        if s["url"]:
            name = (f'<a href="{e(s["url"])}" target="_blank" rel="noopener noreferrer">{name}'
                    f'<span class="ext" aria-hidden="true">↗</span></a>')
        else:
            name = f"<span>{name}</span>"
        return f'<li>{name}<span class="c">{plural(s["count"], "contenu")}</span></li>'

    sections = []
    for i, (key, d) in enumerate(DOMAINS.items()):
        group = [s for s in directory if s["domain"] == key]
        if not group:
            continue
        items = "".join(item(s) for s in group)
        sections.append(f'    <section class="card"><h2><span class="sw" style="background:var(--dom-{i + 1})"></span>'
                        f'{e(d["label"])} <span class="n">· {plural(len(group), "source")}</span></h2><ul>{items}</ul></section>')
    total = sum(s["count"] for s in directory)
    weeks = round(days / 7)
    intro = (f"{plural(len(directory), 'source')} ont fourni les {total} contenus du tableau de bord sur les {weeks} dernières "
             f"semaines. Chaque source n'apparaît qu'une fois, dans le sujet auquel elle est rattachée."
             if directory else "Aucun contenu collecté pour l'instant.")
    footer = f"Mis à jour le {generated_at.strftime('%d/%m/%Y')}. Un clic sur une source ouvre son site dans un nouvel onglet."
    n = len(candidates)
    cand_intro = (f"{plural(n, 'site')} hors de la veille {'ont' if n > 1 else 'a'} fourni des contenus pertinents à "
                  "l'agent de recherche. Chaque proposition est à valider par l'équipe avant d'entrer dans sources.json."
                  if n else "")
    return (template.replace("__INTRO__", e(intro)).replace("__FOOTER__", e(footer))
            .replace("__SECTIONS__", "\n".join(sections) or '    <p class="empty">Aucune source.</p>')
            .replace("__CAND_COUNT__", f" · {n}" if n else "").replace("__CAND_INTRO__", e(cand_intro))
            .replace("__CANDIDATES__", render_candidates_html(list(candidates), repo, agent_ran)))


def render_html(payload: dict) -> str:
    with open(TEMPLATE, encoding="utf-8") as f:
        template = f.read()
    # </script> inside JSON would end the tag early; U+2028/9 break some JS parsers.
    data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            .replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return template.replace("/*__DATA__*/null", data, 1)


def export(output: str, days: int = WINDOW_DAYS, store: Store | None = None, index: EntityIndex | None = None,
           with_embeddings: bool = True, client="auto", encoder=None, repo: str | None = None) -> str:
    store = store or get_store()
    index = index or EntityIndex.load()
    client = make_client() if client == "auto" else client
    repo = os.environ.get("GITHUB_REPOSITORY", "") if repo is None else repo   # set by GitHub Actions
    payload, vectors = asyncio.run(build_payload(store, index, days, client, with_embeddings, encoder))
    page = render_html(payload)
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(page)
    followed = [a for a in payload["articles"] if not a.get("ag")]   # agent finds are proposals, not followed sources
    directory = source_directory(followed, load_source_configs(index=index))
    sources_path = os.path.join(os.path.dirname(output) or ".", "sources.html")
    with open(sources_path, "w", encoding="utf-8") as f:
        f.write(render_sources_html(directory, datetime.fromisoformat(payload["generated_at"]), days,
                                    payload["candidate_sources"], repo,
                                    agent_ran=len(followed) < len(payload["articles"])))
    bin_path = os.path.join(os.path.dirname(output) or ".", "embeddings.bin")
    if vectors:
        with open(bin_path, "wb") as f:
            f.write(vectors)
    elif os.path.exists(bin_path):
        os.remove(bin_path)   # never leave vectors that no longer match the page's article order
    logging.info(f"Dashboard exported to {output} ({len(page) / 1024:.0f} KB, {len(payload['articles'])} articles, "
                 f"{len(directory)} sources, embeddings: {'yes' if vectors else 'no'})")
    return output


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", action="store_true", help="write the static page")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--output", default="site/index.html")
    ap.add_argument("--no-embeddings", action="store_true", help="skip the semantic vectors (keyword search only)")
    args = ap.parse_args()
    if not args.export:
        ap.error("use --export")
    export(args.output, args.days, with_embeddings=not args.no_embeddings)
