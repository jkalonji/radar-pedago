"""Source collection: RSS, feed auto-discovery, page change detection, Crossref journals, YouTube channels, Google News.

Every fetcher returns (articles, SourceReport). A failing source falls back to its `fallback` config, and a failing
site (rss/auto) without explicit fallback falls back to a Google News `site:` search, so one broken site never
silences a domain.
"""

import asyncio
import json
import logging
import os
import re
import urllib.parse as up
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from time import mktime

import aiohttp
import feedparser
import requests

from entities import EntityIndex
from models import Article, now_utc
from text import clean_html, guess_lang, normalize

USER_AGENT = "Mozilla/5.0 (compatible; RadarPedago/1.0; +https://github.com/jkalonji)"
MAX_ENTRIES_PER_SOURCE = 60
MAX_NEW_LINKS_PER_PAGE = 25
TIMEOUT = 30
PROBE_TIMEOUT = 10   # per guessed feed path

# Common feed locations tried when a page does not advertise its feed (WordPress, SPIP, OJS, Hugo/Netlify...).
FEED_CANDIDATES = ["feed", "feed/", "rss", "rss.xml", "feed.xml", "index.xml", "atom.xml", "?feed=rss2",
                   "spip.php?page=backend", "gateway/plugin/WebFeedGatewayPlugin/rss2"]

# Crossref items that are not articles.
_CROSSREF_SKIP = re.compile(r"^(editorial board|issue information|table of contents|front matter|back matter|"
                            r"cover|masthead|erratum|corrigendum|index|sommaire|reviewers?|in this issue)\b", re.I)


@dataclass
class SourceReport:
    name: str
    domain: str = ""
    type: str = ""
    method: str = ""             # what actually worked: rss, auto→flux, auto→page, crossref, fallback→gnews...
    fetched: int = 0
    kept: int = 0
    error: str = ""
    note: str = ""
    feed_url: str = ""
    sample: list[str] = field(default_factory=list)


class SourceError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _requests_get(url: str, timeout: int = TIMEOUT) -> tuple[str, str]:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5"},
                        timeout=timeout)
    if resp.status_code != 200:
        raise SourceError(f"HTTP {resp.status_code}")
    resp.encoding = resp.encoding or resp.apparent_encoding
    return resp.text, resp.url


async def get_text(session: aiohttp.ClientSession, url: str, timeout: int = TIMEOUT) -> tuple[str, str]:
    """GET a URL, return (body, final_url). Some CDNs challenge aiohttp's TLS fingerprint: retry with requests on 403."""
    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5"},
                               timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200:
                return await resp.text(errors="replace"), str(resp.url)
            status = resp.status
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise SourceError(f"{type(e).__name__}: {e}"[:160]) from e
    if status in (403, 406, 429):
        return await asyncio.to_thread(_requests_get, url, timeout)
    raise SourceError(f"HTTP {status}")


# ---------------------------------------------------------------------------
# Feed helpers
# ---------------------------------------------------------------------------

def parse_feed_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
            except (OverflowError, ValueError):
                return None
    return None


def gnews_url(query: str, lookback_hours: int | None = None) -> str:
    """Google News RSS search URL (French edition). `when:Nd` is rewritten to honour the lookback window."""
    if lookback_hours:
        days = max(1, -(-lookback_hours // 24))
        query = re.sub(r"when:\d+[dh]", f"when:{days}d", query) if "when:" in query else f"{query} when:{days}d"
    return f"https://news.google.com/rss/search?q={up.quote(query)}&hl=fr&gl=FR&ceid=FR:fr"


def looks_like_feed(body: str) -> bool:
    head = body.lstrip()[:600].lower()
    return head.startswith("<?xml") or "<rss" in head or "<feed" in head or "<rdf:rdf" in head


def parse_feed(body: str):
    if not looks_like_feed(body):
        raise SourceError("not a feed")
    feed = feedparser.parse(body)
    if not feed.entries:
        raise SourceError("empty feed")
    return feed


def entries_to_articles(feed, source: dict, cutoff: datetime, *, strip_publisher: bool = False,
                        kind: str = "article") -> list[Article]:
    articles = []
    for entry in feed.entries[:MAX_ENTRIES_PER_SOURCE]:
        pub = parse_feed_date(entry)
        if pub and pub < cutoff:
            continue
        title = clean_html(entry.get("title", ""))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        if strip_publisher:
            publisher = (entry.get("source") or {}).get("title", "")
            if publisher and title.endswith(f" - {publisher}"):
                title = title[: -len(publisher) - 3].rstrip()
        description = "" if strip_publisher else clean_html(entry.get("summary", ""))[:600]
        articles.append(_article(source, title, link, pub, description, kind))
    return articles


def _article(source: dict, title: str, url: str, pub: datetime | None, description: str, kind: str) -> Article:
    return Article(
        title=title, url=url, source=source["name"], domain=source["domain"],
        published=(pub or now_utc()).isoformat(), published_is_estimated=pub is None,
        description=description, kind=kind, lang=guess_lang(f"{title}. {description}"),
        filter=source.get("filter", ""), domains=[source["domain"]],
        entities=[source["school"]] if source.get("school") else [],
    )


# ---------------------------------------------------------------------------
# HTML: feed discovery and link extraction
# ---------------------------------------------------------------------------

class _PageParser(HTMLParser):
    """Collects <link rel=alternate> feeds and content links (skipping nav, footer, aside and menu blocks)."""

    SKIP_BLOCKS = {"nav", "footer", "aside", "script", "style", "noscript", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.feeds: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._skip = 0
        self._href: str | None = None
        self._text: list[str] = []
        self._in_title = False
        self._div_stack: list[bool] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and "alternate" in (a.get("rel") or "").lower() and \
                re.search(r"(rss|atom)\+xml", a.get("type") or "") and a.get("href"):
            self.feeds.append(a["href"])
        if tag in self.SKIP_BLOCKS:
            self._skip += 1
        cls = (a.get("class") or "") + " " + (a.get("id") or "")
        if tag in ("div", "ul", "section"):
            noisy = bool(re.search(r"\b(menu|navbar|breadcrumb|cookie|footer|site-header|sidebar|share|social)\b", cls, re.I))
            self._div_stack.append(noisy)
            if noisy:
                self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "a" and not self._skip and a.get("href"):
            self._href, self._text = a["href"], []
        if tag == "img" and self._href is not None and a.get("alt"):
            self._text.append(a["alt"])

    def handle_endtag(self, tag):
        if tag in self.SKIP_BLOCKS and self._skip:
            self._skip -= 1
        if tag in ("div", "ul", "section") and self._div_stack:
            if self._div_stack.pop() and self._skip:
                self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", " ".join(self._text)).strip()
            self.links.append((self._href, text))
            self._href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._href is not None:
            self._text.append(data)


_SKIP_EXT = re.compile(r"\.(css|js|png|jpe?g|gif|svg|webp|ico|zip|xml|json|mp3|mp4)(\?|$)", re.I)
_SKIP_TEXT = re.compile(r"^(lire la suite|en savoir plus|voir plus|suite|plus|read more|more|accueil|home|contact|"
                        r"mentions légales|plan du site|connexion|login|s'abonner|subscribe|rss|facebook|twitter|x|"
                        r"linkedin|instagram|youtube|partager|share|précédent|suivant|next|previous|retour)$", re.I)


def discover_feeds(html: str, base_url: str) -> list[str]:
    p = _PageParser()
    try:
        p.feed(html)
    except Exception:  # pragma: no cover - malformed HTML
        pass
    feeds = [up.urljoin(base_url, f) for f in p.feeds]
    # comment feeds are not what we want
    return [f for f in dict.fromkeys(feeds) if "comments" not in f.lower()]


def extract_links(html: str, base_url: str, link_pattern: str = "") -> list[tuple[str, str]]:
    """Content links of a page as (absolute_url, text): same site, meaningful text, no nav/footer, no assets."""
    p = _PageParser()
    try:
        p.feed(html)
    except Exception:  # pragma: no cover
        pass
    host = up.urlparse(base_url).netloc.removeprefix("www.")
    base_clean = base_url.split("#")[0].rstrip("/")
    pattern = re.compile(link_pattern) if link_pattern else None
    out: dict[str, str] = {}
    for href, text in p.links:
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = up.urljoin(base_url, href).split("#")[0]
        parsed = up.urlparse(url)
        if parsed.scheme not in ("http", "https") or _SKIP_EXT.search(parsed.path):
            continue
        if pattern:
            if not pattern.search(url):
                continue
        elif parsed.netloc.removeprefix("www.") != host:
            continue
        if url.rstrip("/") == base_clean:
            continue
        text = text.strip(" »›>|-–—")
        if len(text) < 15 or len(text.split()) < 3 or _SKIP_TEXT.match(text):
            continue
        out.setdefault(url, text[:250])
    return list(out.items())


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

async def fetch_rss(session, source: dict, cutoff: datetime, url: str | None = None) -> list[Article]:
    body, _ = await get_text(session, url or source["url"])
    return entries_to_articles(parse_feed(body), source, cutoff)


async def fetch_gnews(session, source: dict, cutoff: datetime, lookback_hours: int) -> list[Article]:
    body, _ = await get_text(session, gnews_url(source["query"], lookback_hours))
    articles = entries_to_articles(parse_feed(body), source, cutoff, strip_publisher=True)
    # Google News searches are supplementary: the team wants French sources there.
    return [a for a in articles if a.lang == "fr"]


async def fetch_youtube(session, source: dict, cutoff: datetime) -> list[Article]:
    channel = source.get("channel_id")
    if not channel:
        body, _ = await get_text(session, f"https://www.youtube.com/@{source['handle'].lstrip('@')}")
        m = (re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', body)
             or re.search(r'"externalId":"(UC[\w-]{22})"', body)
             or re.search(r'"channelId":"(UC[\w-]{22})"', body))
        if not m:
            raise SourceError("YouTube channel id not found (set 'channel_id' in sources.json)")
        channel = m.group(1)
    body, _ = await get_text(session, f"https://www.youtube.com/feeds/videos.xml?channel_id={channel}")
    articles = entries_to_articles(parse_feed(body), source, cutoff, kind="video")
    for a in articles:
        a.description = a.description[:400]
    return articles


def _same_journal(container: str, wanted: str, match: str) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9 ]", " ", normalize(s).replace("&", " et ")).split()
    c, w = norm(container), norm(wanted)
    return " ".join(w) in " ".join(c) if match == "contains" else c == w


async def fetch_crossref(session, source: dict, cutoff: datetime) -> list[Article]:
    """New items of a journal (by Crossref creation date): title, abstract when deposited, DOI link.

    With `issn`, the journal's own endpoint is used; otherwise a container-title query, checked against `journal`
    (exact title, or `match: contains`) and optionally `doi_prefix` to tell homonymous journals apart.
    """
    params = {
        "filter": f"from-created-date:{cutoff.strftime('%Y-%m-%d')}",
        "rows": "100",
        "select": "DOI,title,container-title,abstract,created,URL,type",
    }
    if os.environ.get("CROSSREF_MAILTO"):
        params["mailto"] = os.environ["CROSSREF_MAILTO"]
    if source.get("issn"):
        url = f"https://api.crossref.org/journals/{source['issn']}/works?" + up.urlencode(params)
    else:
        # Field query + default relevance order: within the date window, the journal's own items rank first.
        url = "https://api.crossref.org/works?" + up.urlencode({"query.container-title": source["journal"], **params})
    body, _ = await get_text(session, url)
    items = json.loads(body).get("message", {}).get("items", [])
    match = source.get("match", "exact")
    prefix = source.get("doi_prefix", "")
    out = []
    for it in items:
        containers = it.get("container-title") or []
        if not source.get("issn") and not any(_same_journal(c, source["journal"], match) for c in containers):
            continue
        if prefix and not (it.get("DOI") or "").startswith(prefix):
            continue
        if it.get("type") in ("journal-issue", "journal", "component", "peer-review"):
            continue
        title = clean_html(" ".join(it.get("title") or []))
        if not title or _CROSSREF_SKIP.match(title):
            continue
        created = (it.get("created") or {}).get("date-time")
        pub = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
        url = f"https://doi.org/{it['DOI']}" if it.get("DOI") else it.get("URL", "")
        abstract = clean_html(re.sub(r"</?jats:[^>]+>", " ", it.get("abstract") or ""))
        abstract = re.sub(r"^(abstract|résumé|resume)\s*[:.]?\s*", "", abstract, flags=re.I)
        out.append(_article(source, title, url, pub, abstract[:900], "paper"))
        if len(out) >= MAX_ENTRIES_PER_SOURCE:
            break
    return out


async def fetch_page(session, source: dict, store, html: str | None = None, final_url: str | None = None,
                     report: SourceReport | None = None) -> list[Article]:
    """New links on a page since the last run. The first run only records the current links (baseline)."""
    if html is None:
        html, final_url = await get_text(session, source["url"])
    links = extract_links(html, final_url or source["url"], source.get("link_pattern", ""))
    if not links:
        raise SourceError("no content link found on the page")
    if report is not None:
        report.sample = [t for _, t in links[:3]]
    if store is None:   # diagnostic run (check_sources): report what the page exposes
        if report is not None:
            report.note = f"{len(links)} liens de contenu détectés"
        return []
    known = store.seen_links(source["name"])
    new = [(u, t) for u, t in links if u not in known]
    store.add_seen_links(source["name"], [u for u, _ in new])
    if not known:
        if report is not None:
            report.note = f"première visite : {len(links)} liens mémorisés comme référence, aucun article créé"
        return []
    if len(new) > MAX_NEW_LINKS_PER_PAGE and len(new) > len(links) / 2:
        if report is not None:
            report.note = f"{len(new)} liens nouveaux d'un coup (refonte du site ?) : nouvelle référence, aucun article"
        return []
    return [_article(source, text, url, None, "", "page") for url, text in new[:MAX_NEW_LINKS_PER_PAGE]]


async def fetch_auto(session, source: dict, cutoff: datetime, store, report: SourceReport) -> list[Article]:
    """Advertised feed > common feed paths > page change detection."""
    html, final_url = await get_text(session, source["url"])
    if looks_like_feed(html):
        report.method, report.feed_url = "auto→flux", final_url
        return entries_to_articles(parse_feed(html), source, cutoff)
    candidates = discover_feeds(html, final_url)
    base = final_url if final_url.endswith("/") else final_url.rsplit("/", 1)[0] + "/"
    root = f"{up.urlparse(final_url).scheme}://{up.urlparse(final_url).netloc}/"
    candidates += [up.urljoin(b, c) for b in dict.fromkeys([base, root]) for c in FEED_CANDIDATES]
    for url in dict.fromkeys(candidates):
        try:
            body, _ = await get_text(session, url, timeout=PROBE_TIMEOUT)
            feed = parse_feed(body)
        except SourceError as e:
            if str(e).startswith(("HTTP", "not a feed", "empty feed")):
                continue
            break   # timeouts / connection errors: the site will not answer the other paths either
        except Exception:
            continue
        report.method, report.feed_url = "auto→flux", url
        return entries_to_articles(feed, source, cutoff)
    report.method = "auto→page"
    return await fetch_page(session, source, store, html, final_url, report)


async def _fetch_one(session, source: dict, cutoff: datetime, lookback_hours: int, store, report: SourceReport):
    stype = source["type"]
    report.method = report.method or stype
    if stype == "rss":
        report.feed_url = source["url"]
        return await fetch_rss(session, source, cutoff)
    if stype == "gnews":
        return await fetch_gnews(session, source, cutoff, lookback_hours)
    if stype == "crossref":
        # Journals publish in batches: look further back than feeds (deduplication keeps it cheap).
        return await fetch_crossref(session, source, min(cutoff, now_utc() - timedelta(days=10)))
    if stype == "youtube":
        return await fetch_youtube(session, source, cutoff)
    if stype == "page":
        return await fetch_page(session, source, store, report=report)
    if stype == "auto":
        return await fetch_auto(session, source, cutoff, store, report)
    raise SourceError(f"unknown source type '{stype}'")


def fallback_for(source: dict) -> dict | None:
    """Explicit `fallback`, else a Google News `site:` search for sites (rss/auto/page)."""
    base = {k: source[k] for k in ("name", "domain", "filter", "school") if k in source}
    if source.get("fallback"):
        return {**base, **source["fallback"]}
    if source["type"] in ("rss", "auto", "page") and source.get("url"):
        parsed = up.urlparse(source["url"])
        host = parsed.netloc.removeprefix("www.")
        if host and "linkedin.com" not in host:
            path = parsed.path.strip("/").split("/")[0] if parsed.path.strip("/") else ""
            site = f"{host}/{path}" if path and host in ("journals.openedition.org", "erudit.org") else host
            return {**base, "type": "gnews", "query": f"site:{site}"}
    return None


async def fetch_source(session, source: dict, lookback_hours: int, store=None) -> tuple[list[Article], SourceReport]:
    report = SourceReport(source["name"], domain=source["domain"], type=source["type"])
    cutoff = now_utc() - timedelta(hours=lookback_hours)
    try:
        articles = await _fetch_one(session, source, cutoff, lookback_hours, store, report)
    except Exception as e:
        primary_error = str(e) or type(e).__name__
        fb = fallback_for(source)
        if not fb:
            report.error = primary_error
            logging.error(f"[{source['name']}] {primary_error}")
            return [], report
        try:
            report.method = ""
            articles = await _fetch_one(session, fb, cutoff, lookback_hours, store, report)
            report.method = f"secours→{fb['type']}" if report.method == fb["type"] else f"secours→{report.method}"
            report.note = (f"source principale en échec ({primary_error}) ; " + report.note).strip(" ;")
            logging.warning(f"[{source['name']}] primary failed ({primary_error}) - fallback {fb['type']} used")
        except Exception as e2:
            report.method = f"{source['type']} + secours {fb['type']}"
            report.error = f"{primary_error[:90]} ; secours : {str(e2)[:90]}"
            logging.error(f"[{source['name']}] {report.error}")
            return [], report
    report.fetched = len(articles)
    if articles and not report.sample:
        report.sample = [a.title for a in articles[:3]]
    logging.info(f"[{source['name']}] {len(articles)} items ({report.method})")
    return articles, report


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def load_sources(path: str = "sources.json", index: EntityIndex | None = None) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    sources = [s for s in data["sources"] if "name" in s and s.get("enabled", True)]
    if index is not None:
        sources += index.school_sources()
    logging.info(f"Loaded {len(sources)} enabled sources")
    return sources


def finalize(articles: list[Article], index: EntityIndex) -> list[Article]:
    """Deduplicate by URL and by (source, title); tag school mentions (a school mention adds the benchmark tag)."""
    seen: set[str] = set()
    out = []
    for a in articles:
        key = f"{a.source}|{' '.join(normalize(a.title).split())}"
        if a.url in seen or key in seen:
            continue
        seen.update((a.url, key))
        a.entities = list(dict.fromkeys(a.entities + index.tag(f"{a.title} {a.description}")))
        if a.entities and "benchmark" not in a.domains:
            a.domains.append("benchmark")
        out.append(a)
    return out


async def fetch_all(sources: list[dict], index: EntityIndex, lookback_hours: int = 72, store=None,
                    concurrency: int = 8) -> tuple[list[Article], list[SourceReport]]:
    sem = asyncio.Semaphore(concurrency)
    crossref_lock = asyncio.Lock()   # Crossref asks clients to stay polite: one request at a time

    async def run(session, s):
        if s["type"] == "crossref":
            async with crossref_lock, sem:
                res = await fetch_source(session, s, lookback_hours, store)
                await asyncio.sleep(1)
                return res
        async with sem:
            return await fetch_source(session, s, lookback_hours, store)

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(run(session, s) for s in sources), return_exceptions=True)
    articles: list[Article] = []
    reports: list[SourceReport] = []
    for src, res in zip(sources, results):
        if isinstance(res, Exception):
            reports.append(SourceReport(src["name"], domain=src["domain"], type=src["type"], error=str(res)))
            logging.error(f"[{src['name']}] task failed: {res}")
        else:
            articles.extend(res[0])
            reports.append(res[1])
    kept = finalize(articles, index)
    per_source: dict[str, int] = {}
    for a in kept:
        per_source[a.source] = per_source.get(a.source, 0) + 1
    for r in reports:
        r.kept = per_source.get(r.name, 0)
    logging.info(f"{len(kept)}/{len(articles)} items kept after deduplication")
    return kept, reports
