"""Radar Pédago - daily pipeline: collect every source, enrich new items (LLM), store them.

The dashboard is rebuilt separately (dashboard.py --export), every Monday morning in GitHub Actions.

Usage:
  python main.py                       # daily run (last 72h of feeds, last 10 days of journals)
  python main.py --backfill-days 60    # first run: load the last 60 days of feeds and journals
  python main.py --no-llm              # rule-based enrichment only (no API call)
"""

import argparse
import asyncio
import json
import logging
import sys

from classify import classify_articles
from entities import EntityIndex
from fetchers import fetch_all, load_sources
from llm import make_client, model_name
from report import write_report
from store import get_store

KNOWN_DAYS_MARGIN = 60
RETRY_LIMIT = 150


def source_filters(path: str) -> dict[str, str]:
    """{source name: filter} - the filter is not stored with the article, so retried items get it back from here."""
    with open(path, encoding="utf-8") as f:
        return {s["name"]: s.get("filter", "") for s in json.load(f)["sources"] if "name" in s}


async def run(args: argparse.Namespace) -> int:
    index = EntityIndex.load(args.entities)
    store = get_store()
    sources = load_sources(args.sources, index)
    lookback = (args.backfill_days * 24) if args.backfill_days else args.lookback_hours

    articles, reports = await fetch_all(sources, index, lookback_hours=lookback, store=store)
    write_report(reports, title="État des sources (collecte du jour)")
    failed = [r.name for r in reports if r.error]
    if len(failed) > len(reports) / 3:
        print(f"::warning::{len(failed)}/{len(reports)} sources failed: {', '.join(failed[:15])}")

    known = store.known_urls(lookback // 24 + KNOWN_DAYS_MARGIN)
    new = [a for a in articles if a.url not in known]
    logging.info(f"{len(new)} new items ({len(articles) - len(new)} already stored)")

    client = None if args.no_llm else make_client()
    retry = []
    if client is not None:
        filters = source_filters(args.sources)
        retry = [a for a in store.load_unclassified(limit=RETRY_LIMIT) if a.url not in {n.url for n in new}]
        for a in retry:
            a.filter = filters.get(a.source, "")
        if retry:
            logging.info(f"{len(retry)} earlier item(s) only had the rule fallback: sending them to the LLM again")
        logging.info(f"LLM: {model_name()}")

    batch = new + retry
    if batch:
        await classify_articles(batch, client=client, index=index)
        store.save_articles(batch)
    kept = sum(1 for a in batch if a.relevant)
    print(f"::notice::{len(new)} nouveaux éléments, {kept} retenus après filtre de pertinence "
          f"({sum(1 for a in batch if a.classified)} enrichis par le LLM).")
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sources", default="sources.json")
    p.add_argument("--entities", default="entities.json")
    p.add_argument("--lookback-hours", type=int, default=72)
    p.add_argument("--backfill-days", type=int, default=0, help="first run: fetch the last N days")
    p.add_argument("--no-llm", action="store_true", help="rule-based enrichment only")
    return p.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    sys.exit(asyncio.run(run(parse_args())))
