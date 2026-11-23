"""End-to-end checks of the published page in headless Chromium (tags, persistence, glossary and semantic search).

The transformers.js module is replaced by a deterministic fake whose vectors match tests.fakes.FakeEncoder, so the
whole semantic path (vector file, alignment, scoring, ranking) runs without downloading the real model.
"""

import functools
import http.server
import threading

import pytest

import dashboard
from demo_data import generate
from entities import EntityIndex
from store import SqliteStore
from tests.fakes import FAKE_TRANSFORMERS_JS, FakeEncoder

pytestmark = pytest.mark.browser
playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    store = SqliteStore(str(root / "d.db"))
    store.save_articles(generate(n_weeks=8, per_week=20))
    dashboard.export(str(root / "index.html"), store=store, index=EntityIndex.load(), client=None, encoder=FakeEncoder())
    # same data, sources page with a public API (faked by the test): built here, before Playwright owns the event loop
    dashboard.export(str(root / "edit" / "index.html"), store=store, index=EntityIndex.load(), client=None,
                     with_embeddings=False, api={"url": "https://fake.supabase.test", "key": "sb_publishable_test"})
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/index.html"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:   # pragma: no cover - browser not installed
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        b.close()


def new_page(browser, errors):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("https://cdn.jsdelivr.net/npm/@huggingface/transformers@3",
               lambda r: r.fulfill(status=200, content_type="application/javascript", body=FAKE_TRANSFORMERS_JS,
                                   headers={"Access-Control-Allow-Origin": "*"}))
    return ctx, page


def count(page) -> int:
    return int(page.inner_text("#list-count").split(" ")[0].replace(" ", "").replace("\xa0", ""))


def test_tags_persist_and_shape_every_section(site, browser):
    errors = []
    ctx, page = new_page(browser, errors)
    page.goto(site)
    total = count(page)
    page.click('#tags .tag[data-d="pedagogie"]')
    assert page.get_attribute('#tags .tag[data-d="pedagogie"]', "aria-pressed") == "true"
    assert 0 < count(page) < total
    assert not page.is_hidden("#subtags")
    assert "d=pedagogie" in page.url
    page.click('#subtags .tag >> nth=0')
    sub = count(page)
    page.click('#period button[data-w="26"]')
    page.goto(site)   # reload without hash: localStorage brings the view back
    assert page.get_attribute('#tags .tag[data-d="pedagogie"]', "aria-pressed") == "true"
    assert page.get_attribute('#period button[data-w="26"]', "aria-pressed") == "true"
    assert count(page) >= sub
    assert page.is_hidden("#bench-sec")   # benchmark hidden when only Pédagogie is selected
    assert "picking" in page.get_attribute("#tags", "class")   # unselected tags are greyed out
    page.click('#tags .tag[data-d="benchmark"]')
    assert not page.is_hidden("#bench-sec")
    page.click("#reset") if page.query_selector("#reset") else page.click("#all-tags")
    assert page.is_hidden("#bench-sec")   # no tag picked: benchmark stays hidden
    assert "picking" not in page.get_attribute("#tags", "class")
    assert page.query_selector("#types") is None
    assert errors == []
    ctx.close()


def test_glossary_and_semantic_search(site, browser):
    errors = []
    ctx, page = new_page(browser, errors)
    page.goto(site)
    page.fill("#q", "APC")
    page.wait_for_function("document.querySelector('#list-title').textContent.includes('APC')")
    page.wait_for_function("document.querySelector('#search-status').textContent.includes('sémantique active')", timeout=10000)
    titles = page.locator("#list h3").all_inner_texts()
    assert titles and any("compétences" in t.lower() for t in titles[:5])   # APC -> approche par compétences
    assert page.evaluate("window.__pipelineArgs.model") == "Xenova/paraphrase-multilingual-MiniLM-L12-v2"
    assert page.evaluate("window.__pipelineArgs.opts.dtype") == "q8"
    # a query with no shared keyword still finds items through the vectors (fake vectors share hashed words)
    page.fill("#q", "programmation exercices")
    page.wait_for_function("document.querySelector('#list-title').textContent.includes('programmation')")
    page.wait_for_timeout(400)
    assert count(page) >= 1
    assert page.locator("#list .score").count() >= 1
    # the hash carries the query: a shared link reopens the same search
    url = page.url
    assert "q=programmation" in url
    page.keyboard.press("Escape")
    assert page.inner_text("#list-title") == "Contenus"
    assert errors == []
    ctx.close()


def test_cloud_click_and_hot_topic_filter(site, browser):
    errors = []
    ctx, page = new_page(browser, errors)
    page.goto(site + "#w=26")
    words = page.locator("#cloud button").count()
    assert page.locator(".cloud-reset").first.is_hidden()
    word = page.locator("#cloud button").first.inner_text()
    page.locator("#cloud button").first.click()
    page.wait_for_function(f"document.querySelector('#list-title').textContent.includes({word!r})")
    assert page.input_value("#q") == word
    page.wait_for_timeout(800)
    page.locator("#articles .cloud-reset").click()   # back to the full cloud without reloading
    assert page.inner_text("#list-title") == "Contenus"
    assert page.input_value("#q") == ""
    assert page.locator("#cloud button").count() == words
    assert page.locator(".cloud-reset").first.is_hidden()
    page.locator("#cloud button").first.click()
    page.wait_for_function(f"document.querySelector('#list-title').textContent.includes({word!r})")
    page.click("#q-clear")
    page.wait_for_timeout(800)   # let the smooth scroll to the list finish before clicking elsewhere
    if page.locator("#hot .lbl").count():
        # unfolding a hot topic lists every source with its own contents, replacing the 2-item preview
        topic = page.locator("#hot .topic").first
        n_sources = int(topic.locator("summary").inner_text().split("·")[1].split()[0])
        topic.locator("summary").click()
        assert topic.locator(".srcs h4").count() == n_sources
        assert topic.locator(".preview").is_hidden()
        page.locator("#hot .lbl").first.click()
        assert page.locator("#hot details").first.get_attribute("open") is not None   # stays open after re-render
        assert "Sujet chaud" in page.inner_text("#active")
        assert count(page) >= 2
    assert errors == []
    ctx.close()


def test_agent_finds_are_marked_and_filterable(site, browser):
    errors = []
    ctx, page = new_page(browser, errors)
    page.goto(site + "#w=26")
    total = count(page)
    assert not page.is_hidden("#agent-sec") and page.locator("#agent-list .disc").count() == 6
    assert "nouvelles sources proposées" in page.inner_text("#agent-cands")
    assert "sources.html#proposees" in page.get_attribute("#agent-cands a", "href")
    page.click("#agent-all")   # only the agent finds, each with its badge and the query that found it
    assert count(page) == 6 and page.get_attribute("#agent-all", "aria-pressed") == "true"
    assert page.locator("#list .agent").count() == 6 and page.locator("#list .why").count() == 6
    assert "o=agent" in page.url and "découvertes de l'agent" in page.inner_text("#active")
    page.select_option("#origin", "sources")
    assert count(page) == total - 6 and page.locator("#list .agent").count() == 0
    page.click('#tags .tag[data-d="innovation_ia"]')   # the section follows the tags
    assert 0 < page.locator("#agent-list .disc").count() < 6
    page.goto(site.replace("index.html", "sources.html"))
    assert page.locator("#proposees .cand").count() == 2
    assert errors == []
    ctx.close()


def test_mobile_has_no_horizontal_scroll(site, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.goto(site)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    ctx.close()


@pytest.fixture(scope="module")
def editable_site(site):
    return site.replace("index.html", "edit/sources.html")


def test_sources_can_be_added_removed_and_restored_from_the_page(editable_site, browser):
    import json
    rows, errors = [], []
    ctx, page = new_page(browser, errors)

    def api(route):
        req = route.request
        assert req.headers.get("apikey") == "sb_publishable_test"
        cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*",
                "Access-Control-Allow-Methods": "GET, POST"}
        if req.method == "OPTIONS":
            return route.fulfill(status=204, headers=cors)
        if req.method == "POST":
            body = json.loads(req.post_data)
            assert set(body) <= {"action", "name", "domain", "url", "author"}
            rows.append({**body, "id": len(rows) + 1, "at": "2026-11-23T09:00:00+00:00"})
            return route.fulfill(status=201, headers=cors, body="")
        return route.fulfill(status=200, headers=cors, content_type="application/json", body=json.dumps(rows))
    page.route("https://fake.supabase.test/**", api)
    page.goto(editable_site)
    page.wait_for_function("document.querySelectorAll('#mg-list li').length > 0")
    before = page.locator("#mg-list li").count()
    assert page.is_visible("#mg-form") and page.is_hidden("#mg-off")

    page.fill("#mg-name", "Blog Pédago Lab")
    page.fill("#mg-url", "blog-pedago-lab.example/")
    page.click("#mg-form button[type=submit]")
    assert "Choisissez le sujet" in page.inner_text("#mg-status") and rows == []
    page.select_option("#mg-domain", "pedagogie")
    page.fill("#mg-author", "Léa")
    page.click("#mg-form button[type=submit]")
    page.wait_for_function("document.querySelector('#mg-status').classList.contains('ok')")
    assert rows[-1] == {"action": "add", "name": "Blog Pédago Lab", "domain": "pedagogie", "author": "Léa",
                        "url": "https://blog-pedago-lab.example/", "id": 1, "at": "2026-11-23T09:00:00+00:00"}
    assert page.locator("#mg-list li").count() == before + 1
    assert "ajoutée le 23/11/2026 par Léa" in page.inner_text("#mg-list")

    page.fill("#mg-name", "Autre nom")   # same site, another name: refused before any request
    page.fill("#mg-url", "https://www.blog-pedago-lab.example")
    page.select_option("#mg-domain", "recherche")
    page.click("#mg-form button[type=submit]")
    assert "déjà suivi" in page.inner_text("#mg-status") and len(rows) == 1

    page.click('#mg-list button[aria-label="Retirer Blog Pédago Lab"]')
    page.wait_for_selector("#mg-removed")
    assert rows[-1]["action"] == "remove" and "Blog Pédago Lab" in page.inner_text("#mg-removed")
    page.locator("#mg-removed button", has_text="Réactiver").click()
    page.wait_for_function("!document.querySelector('#mg-removed')")
    assert rows[-1]["action"] == "restore" and page.locator("#mg-list li").count() == before + 1

    page.fill("#mg-filter", "pedago lab")   # accents and case ignored
    assert page.locator("#mg-list li").count() == 1
    page.fill("#mg-filter", "")

    cand = page.locator("#proposees .add-cand").first
    name = cand.get_attribute("data-name")
    cand.click()
    page.wait_for_function("document.querySelector('#proposees .add-cand').textContent.startsWith('Ajoutée')")
    assert rows[-1]["name"] == name and rows[-1]["action"] == "add" and "Ajoutée" in cand.inner_text()
    assert errors == []
    ctx.close()
    ctx = browser.new_context(viewport={"width": 390, "height": 844})   # the form fits on a phone
    page = ctx.new_page()
    page.goto(editable_site)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    ctx.close()
