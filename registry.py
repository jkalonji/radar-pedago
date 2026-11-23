"""Effective source list: sources.json (the versioned base) + the changes made from the sources page.

The page appends rows to the `source_changes` table (anyone can, for now): 'add' a source (collected as `auto`),
'remove' one (base or added) and 'restore' a removed one. Rows are replayed in order, so the last action on a name
wins. Every row is validated again here: the table is public, a malformed row is skipped, never trusted.

sources_template.html replays the same rules in JavaScript (live view of the page): keep both in sync.
"""

import json
import logging
import re

from models import DOMAINS

ACTIONS = ("add", "remove", "restore")
MAX_NAME, MAX_URL, MAX_AUTHOR = 120, 500, 80
_URL = re.compile(r"https?://[^\s/$.?#][^\s]*", re.IGNORECASE)


def load_base(path: str = "sources.json") -> list[dict]:
    """Every source of sources.json, disabled ones included (comment lines dropped)."""
    with open(path, encoding="utf-8") as f:
        return [s for s in json.load(f)["sources"] if "name" in s]


def valid_change(row: dict) -> bool:
    name = (row.get("name") or "").strip()
    if row.get("action") not in ACTIONS or not name or len(name) > MAX_NAME or len(row.get("author") or "") > MAX_AUTHOR:
        return False
    if row["action"] == "add":
        url = row.get("url") or ""
        return row.get("domain") in DOMAINS and len(url) <= MAX_URL and bool(_URL.fullmatch(url))
    return True


def apply_changes(base: list[dict], changes: list[dict]) -> list[dict]:
    """Base sources + added ones; removed ones get `enabled: False` and `removed_at`/`removed_by`.

    A source disabled in sources.json (LinkedIn: forbidden collection) cannot be restored from the page, and an
    'add' cannot override a source of sources.json with the same name.
    """
    sources = {s["name"]: dict(s) for s in base}
    base_names = set(sources)
    removed: dict[str, dict] = {}
    for row in sorted(changes, key=lambda r: r.get("id") or 0):
        if not valid_change(row):
            logging.warning(f"Ignored invalid source change: {str(row)[:200]}")
            continue
        name = row["name"].strip()
        if row["action"] == "add":
            if name in base_names:
                continue
            sources[name] = {"name": name, "domain": row["domain"], "type": "auto", "url": row["url"],
                             "added_at": row.get("at") or "", "added_by": row.get("author") or ""}
            removed.pop(name, None)
        elif name in sources:
            if row["action"] == "remove":
                removed[name] = {"removed_at": row.get("at") or "", "removed_by": row.get("author") or ""}
            else:
                removed.pop(name, None)
    for name, info in removed.items():
        if sources[name].get("enabled", True):
            sources[name].update(enabled=False, **info)
    return list(sources.values())


def all_sources(path: str = "sources.json", store=None) -> list[dict]:
    """Base + page changes, disabled sources included. A missing or unreachable table never blocks the collection."""
    base = load_base(path)
    if store is None:
        return base
    try:
        changes = store.source_changes()
    except Exception as e:   # table not created yet (schema.sql not replayed), network...
        print(f"::warning::Changements de sources faits depuis la page non lus ({str(e)[:150]}) : "
              "seul sources.json est pris en compte.")
        return base
    return apply_changes(base, changes)
