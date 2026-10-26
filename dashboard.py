"""Radar Pédago dashboard - static export for GitHub Pages.

  python dashboard.py --export --output site/index.html       # also writes site/embeddings.bin
  python dashboard.py --export --no-embeddings                 # keyword search only (faster, no model download)

The data is embedded as JSON in a single page and filtered in the browser (tags, search, period), so the published
page needs no backend and no API key. Hot topic labels are named by the LLM at export time when a key is set.
"""

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from embeddings import DIM, MODEL_ID, build_embeddings
from entities import EntityIndex
from llm import make_client
from models import CONTENT_TYPES, DOMAINS
from store import Store, get_store
from text import STOPWORDS
from topics import emerging_terms, name_clusters, weekly_hot_topics

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_template.html")
WINDOW_DAYS = 26 * 7 + 1


def _article(r: dict) -> dict:
    """Compact record for the page (short keys keep the embedded JSON small)."""
    title_fr = r.get("title_fr") or r["title"]
    return {"t": title_fr, "o": r["title"] if r["title"] != title_fr else "", "u": r["url"], "s": r.get("source") or "",
            "d": r["domain"], "ds": r.get("domains") or [r["domain"]], "p": r["published"],
            "est": bool(r.get("published_is_estimated")), "k": r.get("kind") or "article", "c": r.get("category") or "",
            "y": r.get("content_type") or "", "sm": r.get("summary") or "", "e": r.get("entities") or []}


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


def render_html(payload: dict) -> str:
    with open(TEMPLATE, encoding="utf-8") as f:
        template = f.read()
    # </script> inside JSON would end the tag early; U+2028/9 break some JS parsers.
    data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            .replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return template.replace("/*__DATA__*/null", data, 1)


def export(output: str, days: int = WINDOW_DAYS, store: Store | None = None, index: EntityIndex | None = None,
           with_embeddings: bool = True, client="auto", encoder=None) -> str:
    store = store or get_store()
    index = index or EntityIndex.load()
    client = make_client() if client == "auto" else client
    payload, vectors = asyncio.run(build_payload(store, index, days, client, with_embeddings, encoder))
    html = render_html(payload)
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(html)
    bin_path = os.path.join(os.path.dirname(output) or ".", "embeddings.bin")
    if vectors:
        with open(bin_path, "wb") as f:
            f.write(vectors)
    elif os.path.exists(bin_path):
        os.remove(bin_path)   # never leave vectors that no longer match the page's article order
    logging.info(f"Dashboard exported to {output} ({len(html) / 1024:.0f} KB, {len(payload['articles'])} articles, "
                 f"embeddings: {'yes' if vectors else 'no'})")
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
