from html import escape
import json
from datetime import datetime, timedelta, timezone

import numpy as np

import dashboard
from demo_data import generate
from embeddings import article_text, build_embeddings, quantize
from entities import EntityIndex
from store import SqliteStore
from tests.fakes import FakeEncoder, FakeLLM
from topics import emerging_terms, extract_clusters, weekly_hot_topics

NOW = datetime(2026, 10, 26, 6, 30, tzinfo=timezone.utc)


def row(title, source, days_ago, domain="pedagogie", url=None):
    return {"title": title, "title_fr": title, "source": source, "domain": domain,
            "url": url or f"https://ex.fr/{source}/{title}/{days_ago}", "published": (NOW - timedelta(days=days_ago)).isoformat()}


def test_clusters_need_several_sources():
    rows = [row("Classe inversée en licence : bilan", "A", 1), row("La classe inversée en master", "B", 2),
            row("Classe inversée : fiche pratique", "C", 3), row("Classe inversée encore", "A", 3)]
    clusters = extract_clusters(rows)
    assert clusters and clusters[0]["phrase"] == "classe inversée" and clusters[0]["source_count"] == 3
    assert extract_clusters([row("Classe inversée", "A", 1)] * 3) == []


async def test_hot_topics_are_per_domain_and_named():
    rows = [row(f"Agents IA en formation {i}", s, 1, "innovation_ia") for i, s in enumerate("ABC")]
    rows += [row(f"Agents IA en formation bis {i}", s, 1, "pedagogie") for i, s in enumerate("AB")]
    hot = weekly_hot_topics(rows, NOW)
    assert {h["domain"] for h in hot} == {"innovation_ia", "pedagogie"}
    from topics import name_clusters
    llm = FakeLLM(lambda s, items: {"labels": [{"id": 0, "label": "Agents IA en formation"}]})
    named = await name_clusters(hot, llm)
    assert named[0]["label"] == "Agents IA en formation"


def test_emerging_terms_wait_for_history_then_detect():
    start = (NOW - timedelta(weeks=2)).isoformat()
    assert emerging_terms([], NOW, start)["ready"] is False
    rows = []
    for w in range(1, 5):   # baseline: "tutorat" once a week
        rows.append(row("Le tutorat par les pairs", "A", 7 * w + 2))
    for i, s in enumerate("ABCD"):   # this week: 4 articles from 4 sources
        rows.append(row(f"Tutorat par les pairs et réussite {i}", s, 2))
    rows += [row("Évaluation formative", "A", 2), row("Évaluation formative", "B", 9)]
    out = emerging_terms(rows, NOW, (NOW - timedelta(weeks=6)).isoformat())
    assert out["ready"]
    terms = [t["term"] for t in out["terms"]]
    assert terms and terms[0] in ("tutorat", "tutorat pairs", "pairs réussite", "réussite")
    assert len(terms) == 1   # overlapping n-grams of the same headlines collapse into one signal
    assert out["terms"][0]["series"][-1] == 4


def test_embeddings_quantize_and_order():
    rows = [{"title": "A study", "title_fr": "Une étude", "summary": "Résumé"}, {"title": "Titre", "title_fr": "Titre"}]
    assert article_text(rows[0]) == "Une étude. A study. Résumé" and article_text(rows[1]) == "Titre"
    blob = build_embeddings(rows, FakeEncoder())
    vec = np.frombuffer(blob, dtype=np.int8).reshape(2, 384)
    assert vec.shape == (2, 384) and np.abs(vec).max() <= 127
    assert quantize(np.array([[1.0, -1.0, 0.0]])) == bytes([127, 129, 0])

    class Broken:
        def encode(self, texts):
            raise OSError("no network")
    assert build_embeddings(rows, Broken()) is None


def test_export_writes_page_and_aligned_vectors(tmp_path):
    store = SqliteStore(str(tmp_path / "d.db"))
    store.save_articles(generate(n_weeks=6, per_week=10))
    out = tmp_path / "site" / "index.html"
    dashboard.export(str(out), store=store, index=EntityIndex.load(), client=None, encoder=FakeEncoder())
    html = out.read_text(encoding="utf-8")
    data = json.loads(html.split('<script id="data" type="application/json">')[1].split("</script>")[0])
    assert data["demo"] is True and len(data["domains"]) == 6 and data["glossary"]
    assert data["embeddings"]["count"] == len(data["articles"]) and data["embeddings"]["dim"] == 384
    assert (tmp_path / "site" / "embeddings.bin").stat().st_size == 384 * len(data["articles"])
    a = data["articles"][0]
    assert set(a) >= {"t", "o", "u", "s", "d", "ds", "p", "c", "y", "sm", "e"}
    # no encoder -> no stale vectors left behind
    dashboard.export(str(out), store=store, index=EntityIndex.load(), client=None, with_embeddings=False)
    assert not (tmp_path / "site" / "embeddings.bin").exists()
    assert "</script>" not in html.split('<script id="data" type="application/json">')[1].split("</script>")[0]
    # the sources page lists each source once, with its count
    sources = (tmp_path / "site" / "sources.html").read_text(encoding="utf-8")
    names = {a["s"] for a in data["articles"]}
    assert all(sources.count(f">{escape(n)}<") == 1 for n in names)
    assert f"{len(names)} sources" in sources and "__SECTIONS__" not in sources


def test_source_directory_dedupes_groups_and_links():
    arts = [{"s": "Blog B", "d": "pedagogie"}, {"s": "Blog B", "d": "recherche"}, {"s": "Revue A", "d": "pedagogie"},
            {"s": "Inconnue", "d": "innovation_ia"}, {"s": "Actu (Google News)", "d": "enseignement_sup"}]
    configs = {"Blog B": {"name": "Blog B", "domain": "pedagogie", "type": "rss", "url": "https://blog-b.fr/feed/x"},
               "Revue A": {"name": "Revue A", "domain": "pedagogie", "type": "crossref", "journal": "A"},
               "Actu (Google News)": {"name": "Actu (Google News)", "domain": "enseignement_sup", "type": "gnews",
                                      "query": "\"enseignement supérieur\" when:3d"}}
    d = dashboard.source_directory(arts, configs)
    assert [(s["name"], s["domain"], s["count"]) for s in d] == [
        ("Blog B", "pedagogie", 2), ("Revue A", "pedagogie", 1),
        ("Actu (Google News)", "enseignement_sup", 1), ("Inconnue", "innovation_ia", 1)]
    by = {s["name"]: s["url"] for s in d}
    assert by["Blog B"] == "https://blog-b.fr/" and by["Revue A"] == "" and by["Inconnue"] == ""
    assert by["Actu (Google News)"].startswith("https://news.google.com/search?q=") and "when" not in by["Actu (Google News)"]
    page = dashboard.render_sources_html(d, datetime(2026, 10, 26, tzinfo=timezone.utc), 183)
    assert 'href="https://blog-b.fr/"' in page and "<span>Revue A</span>" in page and "4 sources" in page
