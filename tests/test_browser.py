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
    page.click("#reset") if page.query_selector("#reset") else page.click("#all-tags")
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
    word = page.locator("#cloud button").first.inner_text()
    page.locator("#cloud button").first.click()
    page.wait_for_function(f"document.querySelector('#list-title').textContent.includes({word!r})")
    assert page.input_value("#q") == word
    page.click("#q-clear")
    page.wait_for_timeout(800)   # let the smooth scroll to the list finish before clicking elsewhere
    if page.locator("#hot .lbl").count():
        page.locator("#hot .lbl").first.click()
        assert "Sujet chaud" in page.inner_text("#active")
        assert count(page) >= 2
    assert errors == []
    ctx.close()


def test_mobile_has_no_horizontal_scroll(site, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.goto(site)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    ctx.close()
