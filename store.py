"""Persistence: Supabase (production) or a local SQLite file (dev/tests, when SUPABASE_* is unset)."""

import json
import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from models import Article

ARTICLE_COLUMNS = [
    "url", "title", "title_fr", "source", "domain", "domains", "published", "published_is_estimated", "description",
    "lang", "kind", "summary", "category", "content_type", "entities", "classified", "relevant",
]
_JSON_COLUMNS = {"domains", "entities"}
_BOOL_COLUMNS = {"published_is_estimated", "classified", "relevant"}
_ORDER = {"articles": ["url"], "seen_links": ["source", "url"]}   # unique keys: stable paging


def article_row(a: Article) -> dict:
    return {k: v for k, v in asdict(a).items() if k in ARTICLE_COLUMNS}


def row_to_article(r: dict) -> Article:
    fields = {k: r[k] for k in ARTICLE_COLUMNS if k in r and r[k] is not None}
    return Article(**fields)


def _since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _dedup(rows: list[dict], conflict: str) -> list[dict]:
    """Last row wins per conflict key (Postgres refuses an upsert that touches the same row twice)."""
    keys = conflict.split(",")
    return list({tuple(r[k] for k in keys): r for r in rows}.values())


class Store(ABC):
    """Domain API used by the pipeline and the dashboard. Subclasses provide 2 primitives."""

    @abstractmethod
    def _upsert(self, table: str, rows: list[dict], conflict: str) -> None: ...

    @abstractmethod
    def _select(self, table: str, gte: tuple[str, str] | None = None, eq: dict | None = None) -> list[dict]: ...

    # -- articles ----------------------------------------------------------
    def known_urls(self, days: int) -> set[str]:
        """URLs already stored (relevant or not) - so an off-topic item is not classified again every day."""
        return {r["url"] for r in self._select("articles", gte=("published", _since_iso(days)))}

    def save_articles(self, articles: list[Article]) -> int:
        rows = _dedup([article_row(a) for a in articles], "url")
        if rows:
            self._upsert("articles", rows, "url")
        return len(rows)

    def load_articles(self, days: int, relevant_only: bool = True) -> list[dict]:
        rows = self._select("articles", gte=("published", _since_iso(days)))
        if relevant_only:
            rows = [r for r in rows if r.get("relevant", True)]
        return sorted(rows, key=lambda r: r["published"], reverse=True)

    def load_unclassified(self, days: int = 45, limit: int = 200) -> list[Article]:
        """Articles that only got the rule fallback (LLM down or over budget): retried on the next runs."""
        rows = [r for r in self._select("articles", gte=("published", _since_iso(days)), eq={"classified": False})]
        rows.sort(key=lambda r: r["published"], reverse=True)
        return [row_to_article(r) for r in rows[:limit]]

    # -- page change detection --------------------------------------------
    def seen_links(self, source: str) -> set[str]:
        return {r["url"] for r in self._select("seen_links", eq={"source": source})}

    def add_seen_links(self, source: str, urls: list[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rows = _dedup([{"source": source, "url": u, "first_seen": now} for u in urls], "source,url")
        if rows:
            self._upsert("seen_links", rows, "source,url")


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    url TEXT PRIMARY KEY, title TEXT NOT NULL, title_fr TEXT DEFAULT '', source TEXT, domain TEXT NOT NULL,
    domains TEXT DEFAULT '[]', published TEXT NOT NULL, published_is_estimated INTEGER DEFAULT 0,
    description TEXT DEFAULT '', lang TEXT, kind TEXT DEFAULT 'article', summary TEXT DEFAULT '', category TEXT,
    content_type TEXT, entities TEXT DEFAULT '[]', classified INTEGER DEFAULT 0, relevant INTEGER DEFAULT 1,
    collected_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS seen_links (
    source TEXT NOT NULL, url TEXT NOT NULL, first_seen TEXT NOT NULL, PRIMARY KEY (source, url));
"""


class SqliteStore(Store):
    def __init__(self, path: str = "data/local.db"):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SQLITE_SCHEMA)

    @staticmethod
    def _to_db(row: dict) -> dict:
        return {k: (json.dumps(v, ensure_ascii=False) if k in _JSON_COLUMNS else int(v) if isinstance(v, bool) else v)
                for k, v in row.items()}

    @staticmethod
    def _from_db(row: sqlite3.Row) -> dict:
        d = dict(row)
        for k in _JSON_COLUMNS & d.keys():
            d[k] = json.loads(d[k] or "[]")
        for k in _BOOL_COLUMNS & d.keys():
            d[k] = bool(d[k])
        return d

    def _upsert(self, table, rows, conflict):
        keys = conflict.split(",")
        for row in map(self._to_db, rows):
            cols = list(row)
            updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in keys) or f"{keys[0]}=excluded.{keys[0]}"
            sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
                   f"ON CONFLICT({conflict}) DO UPDATE SET {updates}")
            self.conn.execute(sql, [row[c] for c in cols])
        self.conn.commit()

    def _select(self, table, gte=None, eq=None):
        sql, params, clauses = f"SELECT * FROM {table}", [], []
        if gte:
            clauses.append(f"{gte[0]} >= ?")
            params.append(gte[1])
        for k, v in (eq or {}).items():
            clauses.append(f"{k} = ?")
            params.append(int(v) if isinstance(v, bool) else v)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [self._from_db(r) for r in self.conn.execute(sql, params)]


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

class SchemaMissingError(RuntimeError):
    """The Supabase tables do not exist (schema.sql was not run on this project)."""


def _explain(exc: Exception) -> Exception:
    text = str(exc)
    if "PGRST205" in text or "schema cache" in text or "PGRST204" in text:
        msg = ("Supabase tables or columns not found: run schema.sql in the SQL Editor of the Supabase project that "
               "SUPABASE_URL points to (then, if it still fails, run: NOTIFY pgrst, 'reload schema';). "
               f"Original error: {text[:200]}")
        print(f"::error::{msg}")
        return SchemaMissingError(msg)
    return exc


class SupabaseStore(Store):
    PAGE = 1000
    CHUNK = 500

    def __init__(self, url: str, key: str, client=None):
        if client is None:
            from supabase import create_client
            client = create_client(url, key)
        self.client = client

    def _upsert(self, table, rows, conflict):
        try:
            for i in range(0, len(rows), self.CHUNK):
                self.client.table(table).upsert(rows[i:i + self.CHUNK], on_conflict=conflict).execute()
        except Exception as e:
            raise _explain(e) from e

    def _select(self, table, gte=None, eq=None):
        try:
            out, start = [], 0
            while True:
                q = self.client.table(table).select("*")
                if gte:
                    q = q.gte(*gte)
                for k, v in (eq or {}).items():
                    q = q.eq(k, v)
                for col in _ORDER[table]:
                    q = q.order(col)
                page = q.range(start, start + self.PAGE - 1).execute().data or []
                out.extend(page)
                if len(page) < self.PAGE:
                    return out
                start += self.PAGE
        except Exception as e:
            raise _explain(e) from e


def get_store() -> Store:
    """Supabase when SUPABASE_URL/SUPABASE_KEY are set, otherwise a local SQLite file."""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if url and key:
        return SupabaseStore(url, key)
    path = os.environ.get("LOCAL_DB", "data/local.db")
    logging.warning(f"SUPABASE_URL/SUPABASE_KEY not set - using local SQLite ({path})")
    return SqliteStore(path)
