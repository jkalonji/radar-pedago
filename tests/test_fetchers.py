import json
from datetime import timedelta

import pytest

import fetchers
from entities import EntityIndex
from fetchers import (SourceError, discover_feeds, extract_links, fallback_for, fetch_all, fetch_source)
from models import now_utc
from store import SqliteStore


def rss(items, title="Flux"):
    body = "".join(f"<item><title>{t}</title><link>{u}</link><description>{d}</description>"
                   f"<pubDate>{p}</pubDate></item>" for t, u, d, p in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>{title}</title>{body}</channel></rss>'


def rfc822(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


NOW = now_utc()
FEED = rss([
    ("Évaluer par compétences en licence", "https://ex.fr/a1", "Un retour d'expérience sur l'APC.", rfc822(NOW)),
    ("Vieux billet", "https://ex.fr/old", "", rfc822(NOW - timedelta(days=40))),
])

HTML_WITH_FEED = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed/"></head><body></body></html>'
HTML_NO_FEED = """<html><head><title>Ressources</title></head><body>
<nav><a href="/a-propos">À propos de notre service pédagogique</a></nav>
<div class="menu"><a href="/contact-equipe">Contacter l'équipe du centre</a></div>
<main>
 <article><header class="entry-header"><h2><a href="/ressources/fiche-classe-inversee">Fiche : mettre en place une classe inversée</a></h2></header></article>
 <a href="/ressources/guide-apc">Guide de l'approche par compétences pour les équipes</a>
 <a href="/ressources/guide-apc#top">Guide de l'approche par compétences pour les équipes</a>
 <a href="/ressources/x">Lire la suite</a>
 <a href="https://autre-site.fr/article-externe">Un article sur un autre site web</a>
 <a href="/style.css">Feuille de style du site complet</a>
</main>
<footer><a href="/mentions">Mentions légales et politique de confidentialité</a></footer></body></html>"""


class Router:
    """Patches fetchers.get_text: maps URL substrings to bodies (or exceptions)."""

    def __init__(self, monkeypatch, routes):
        self.routes, self.calls = routes, []

        async def get_text(session, url, timeout=30):
            self.calls.append(url)
            for key, body in self.routes.items():
                if key in url:
                    if isinstance(body, Exception):
                        raise body
                    return body, url
            raise SourceError("HTTP 404")
        monkeypatch.setattr(fetchers, "get_text", get_text)


SRC = {"name": "Test", "domain": "pedagogie"}


async def test_rss_source_keeps_recent_items(monkeypatch):
    Router(monkeypatch, {"ex.fr/rss": FEED})
    arts, rep = await fetch_source(None, {**SRC, "type": "rss", "url": "https://ex.fr/rss"}, 72)
    assert [a.url for a in arts] == ["https://ex.fr/a1"]
    a = arts[0]
    assert a.domain == "pedagogie" and a.domains == ["pedagogie"] and a.lang == "fr" and not a.published_is_estimated
    assert rep.method == "rss" and rep.fetched == 1 and not rep.error


async def test_auto_uses_advertised_feed(monkeypatch):
    r = Router(monkeypatch, {"site.fr/feed/": FEED, "site.fr/": HTML_WITH_FEED})
    arts, rep = await fetch_source(None, {**SRC, "type": "auto", "url": "https://site.fr/"}, 72)
    assert len(arts) == 1 and rep.method == "auto→flux" and rep.feed_url == "https://site.fr/feed/"
    assert r.calls[:2] == ["https://site.fr/", "https://site.fr/feed/"]


async def test_auto_tries_common_paths_then_page(monkeypatch, tmp_path):
    Router(monkeypatch, {"site.fr/ressources": HTML_NO_FEED})
    store = SqliteStore(str(tmp_path / "t.db"))
    src = {**SRC, "type": "auto", "url": "https://site.fr/ressources"}
    arts, rep = await fetch_source(None, src, 72, store)
    assert arts == [] and rep.method == "auto→page" and "première visite" in rep.note
    assert len(store.seen_links("Test")) == 2
    # a new link appears on the next run
    Router(monkeypatch, {"site.fr/ressources": HTML_NO_FEED.replace(
        "<main>", '<main><a href="/ressources/nouveau-dossier">Nouveau dossier sur l\'évaluation formative</a>')})
    arts, rep = await fetch_source(None, src, 72, store)
    assert [a.url for a in arts] == ["https://site.fr/ressources/nouveau-dossier"]
    assert arts[0].published_is_estimated and arts[0].kind == "page"


def test_extract_links_skips_navigation_and_noise():
    links = dict(extract_links(HTML_NO_FEED, "https://site.fr/ressources"))
    assert set(links) == {"https://site.fr/ressources/fiche-classe-inversee", "https://site.fr/ressources/guide-apc"}
    assert discover_feeds(HTML_WITH_FEED, "https://site.fr/x") == ["https://site.fr/feed/"]


async def test_failing_site_falls_back_to_google_news(monkeypatch):
    gn = rss([("Classe inversée : bilan - Le Média", "https://news.google.com/x", "", rfc822(NOW))])
    gn = gn.replace("<item>", "<item><source url='https://m.fr'>Le Média</source>", 1)
    Router(monkeypatch, {"news.google.com": gn, "journals.openedition.org": SourceError("HTTP 503")})
    src = {**SRC, "type": "rss", "url": "https://journals.openedition.org/ripes/backend?format=rssdocuments"}
    fb = fallback_for(src)
    assert fb["type"] == "gnews" and fb["query"] == "site:journals.openedition.org/ripes"
    arts, rep = await fetch_source(None, src, 72)
    assert [a.title for a in arts] == ["Classe inversée : bilan"]
    assert rep.method == "secours→gnews" and "HTTP 503" in rep.note and not rep.error


async def test_explicit_fallback_and_total_failure(monkeypatch):
    Router(monkeypatch, {})
    src = {**SRC, "type": "rss", "url": "https://x.fr/rss", "fallback": {"type": "crossref", "journal": "Revue X"}}
    arts, rep = await fetch_source(None, src, 72)
    assert arts == [] and "secours" in rep.error


async def test_gnews_keeps_only_french(monkeypatch):
    gn = rss([("Les étudiants et l'IA générative", "https://g/1", "", rfc822(NOW)),
              ("Students and the future of generative AI in the classroom", "https://g/2", "", rfc822(NOW))])
    Router(monkeypatch, {"news.google.com": gn})
    arts, _ = await fetch_source(None, {**SRC, "type": "gnews", "query": "IA when:3d"}, 72)
    assert [a.url for a in arts] == ["https://g/1"]


async def test_crossref_filters_journal_and_noise(monkeypatch):
    items = [
        {"DOI": "10.1/a", "title": ["Project-based learning in engineering"], "container-title": ["European Journal of Engineering Education"],
         "abstract": "<jats:p>Abstract: We study PBL.</jats:p>", "created": {"date-time": NOW.isoformat().replace("+00:00", "Z")}, "type": "journal-article"},
        {"DOI": "10.1/b", "title": ["Editorial Board"], "container-title": ["European Journal of Engineering Education"],
         "created": {"date-time": NOW.isoformat()}, "type": "journal-article"},
        {"DOI": "10.1/c", "title": ["Other"], "container-title": ["European Journal of Something Else"],
         "created": {"date-time": NOW.isoformat()}, "type": "journal-article"},
    ]
    r = Router(monkeypatch, {"api.crossref.org": json.dumps({"message": {"items": items}})})
    arts, rep = await fetch_source(None, {**SRC, "domain": "recherche", "type": "crossref",
                                          "journal": "European Journal of Engineering Education"}, 72)
    assert [a.url for a in arts] == ["https://doi.org/10.1/a"]
    assert arts[0].description == "We study PBL." and arts[0].kind == "paper" and arts[0].lang == "en"
    assert "from-created-date" in r.calls[0] and "query.container-title" in r.calls[0]


async def test_crossref_issn_endpoint(monkeypatch):
    items = [{"DOI": "10.1/z", "title": ["Un article"], "container-title": ["Nom variable"],
              "created": {"date-time": NOW.isoformat()}, "type": "journal-article"}]
    r = Router(monkeypatch, {"api.crossref.org/journals/0142-694X/works": json.dumps({"message": {"items": items}})})
    arts, _ = await fetch_source(None, {**SRC, "type": "crossref", "journal": "Design Studies", "issn": "0142-694X"}, 72)
    assert len(arts) == 1 and "query.container-title" not in r.calls[0]


async def test_crossref_doi_prefix_and_contains(monkeypatch):
    items = [{"DOI": "10.3917/phron.1", "title": ["Former les formateurs"], "container-title": ["Phronesis"],
              "created": {"date-time": NOW.isoformat()}, "type": "journal-article"},
             {"DOI": "10.1163/phil.1", "title": ["Aristotle on virtue"], "container-title": ["Phronesis"],
              "created": {"date-time": NOW.isoformat()}, "type": "journal-article"}]
    Router(monkeypatch, {"api.crossref.org": json.dumps({"message": {"items": items}})})
    arts, _ = await fetch_source(None, {**SRC, "type": "crossref", "journal": "Phronesis", "doi_prefix": "10.3917"}, 72)
    assert [a.title for a in arts] == ["Former les formateurs"]
    assert fetchers._same_journal("Proceedings of the 57th ACM Technical Symposium on Computer Science Education V. 1",
                                  "Technical Symposium on Computer Science Education", "contains")
    assert fetchers._same_journal("Éducation & formations", "Education et formations", "exact")


async def test_youtube_resolves_handle(monkeypatch):
    atom = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Chaîne</title>'
            f'<entry><title>Épisode 12 : les agents IA</title><link href="https://www.youtube.com/watch?v=1"/>'
            f'<published>{NOW.isoformat()}</published></entry></feed>')
    r = Router(monkeypatch, {"youtube.com/@alafrench": '..."channelId":"UCabcdefghijklmnopqrstuv"...',
                             "feeds/videos.xml?channel_id=UCabcdefghijklmnopqrstuv": atom})
    arts, _ = await fetch_source(None, {**SRC, "domain": "innovation_ia", "type": "youtube", "handle": "alafrench"}, 72)
    assert len(arts) == 1 and arts[0].kind == "video"


async def test_fetch_all_dedups_and_tags_schools(monkeypatch):
    feed = rss([("L'Institut Villebon-Charpak lance un parcours", "https://ex.fr/v", "", rfc822(NOW)),
                ("L'Institut Villebon-Charpak lance un parcours", "https://ex.fr/v?utm=1", "", rfc822(NOW))])
    Router(monkeypatch, {"ex.fr/rss": feed, "crash": RuntimeError("boom")})
    sources = [{**SRC, "type": "rss", "url": "https://ex.fr/rss"},
               {"name": "Cassé", "domain": "design_ux", "type": "rss", "url": "https://crash.fr/rss", "fallback": {"type": "page", "url": "https://crash.fr"}}]
    arts, reports = await fetch_all(sources, EntityIndex.load(), 72)
    assert len(arts) == 1 and arts[0].entities == ["villebon_charpak"] and arts[0].domains == ["pedagogie", "benchmark"]
    by = {r.name: r for r in reports}
    assert by["Test"].kept == 1 and by["Cassé"].error


@pytest.mark.live
async def test_live_one_openedition_feed():
    import aiohttp
    async with aiohttp.ClientSession() as s:
        arts, rep = await fetch_source(s, {"name": "RIPES", "domain": "recherche", "type": "rss",
                                           "url": "https://journals.openedition.org/ripes/backend?format=rssdocuments"}, 24 * 365)
    assert not rep.error


async def test_auto_stops_probing_when_site_stops_answering(monkeypatch, tmp_path):
    routes = {"site.fr/feed": SourceError("TimeoutError: "), "site.fr/ressources": HTML_NO_FEED}
    r = Router(monkeypatch, routes)
    store = SqliteStore(str(tmp_path / "t.db"))
    _, rep = await fetch_source(None, {**SRC, "type": "auto", "url": "https://site.fr/ressources"}, 72, store)
    assert rep.method == "auto→page" and len(r.calls) == 2   # page + first probe, then straight to page mode
