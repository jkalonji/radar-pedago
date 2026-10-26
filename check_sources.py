"""Radar Pédago - source diagnostic: does every source answer, and through which method?

Runs every enabled source without touching the database (pages without feed only report how many content links
they expose) and prints a Markdown table, also written to the GitHub Actions job summary and output/.

  python check_sources.py                 # all sources, last 30 days
  python check_sources.py --only "RIPES"  # sources whose name contains the text
"""

import argparse
import asyncio
import logging
import sys

from entities import EntityIndex
from fetchers import fetch_all, load_sources
from report import write_report


async def run(args) -> int:
    index = EntityIndex.load(args.entities)
    sources = load_sources(args.sources, index)
    if args.only:
        sources = [s for s in sources if args.only.lower() in s["name"].lower()]
    _, reports = await fetch_all(sources, index, lookback_hours=args.days * 24, store=None)
    md = write_report(reports, path="output/sources_check.md", title=f"Diagnostic des sources ({args.days} derniers jours)")
    print(md)
    failed = [r for r in reports if r.error]
    if failed:
        print(f"::warning::{len(failed)}/{len(reports)} source(s) en échec : voir le tableau du résumé du job")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default="sources.json")
    ap.add_argument("--entities", default="entities.json")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--only", default="")
    sys.exit(asyncio.run(run(ap.parse_args())))
