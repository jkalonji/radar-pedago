import json

from models import CONTENT_TYPES, DOMAINS, categories, default_category

TYPES = {"rss", "auto", "page", "crossref", "youtube", "gnews"}
REQUIRED = {"rss": "url", "auto": "url", "page": "url", "crossref": "journal", "youtube": "handle", "gnews": "query"}


def _sources():
    with open("sources.json", encoding="utf-8") as f:
        return [s for s in json.load(f)["sources"] if "name" in s]


def test_every_source_is_well_formed():
    names = set()
    for s in _sources():
        assert s["name"] not in names, f"duplicate source name {s['name']}"
        names.add(s["name"])
        assert s["domain"] in DOMAINS, s["name"]
        assert s["type"] in TYPES, s["name"]
        assert s.get(REQUIRED[s["type"]]), f"{s['name']}: missing {REQUIRED[s['type']]}"
        if s.get("filter"):
            assert s["filter"] == "superieur"
        fb = s.get("fallback")
        if fb:
            assert fb["type"] in TYPES and fb.get(REQUIRED[fb["type"]]), s["name"]


def test_every_domain_has_sources():
    used = {s["domain"] for s in _sources() if s.get("enabled", True)}
    assert used == set(DOMAINS)


def test_linkedin_is_disabled():
    for s in _sources():
        if "linkedin.com" in s.get("url", ""):
            assert s.get("enabled") is False


def test_taxonomy_defaults_exist():
    for d in DOMAINS:
        assert default_category(d) in categories(d)
        assert len(categories(d)) >= 4
    assert "Actualité" in CONTENT_TYPES


def test_entities_and_glossary_parse():
    with open("entities.json", encoding="utf-8") as f:
        ent = json.load(f)
    assert ent["schools"] and all(s["id"] and s["aliases"] for s in ent["schools"])
    with open("glossary.json", encoding="utf-8") as f:
        gl = json.load(f)
    assert all(len(e["terms"]) >= 2 for e in gl["entries"])
