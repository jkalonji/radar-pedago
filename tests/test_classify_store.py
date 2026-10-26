from datetime import timedelta

import pytest

from classify import apply_fallback, classify_articles, fallback_category, fallback_content_type
from entities import EntityIndex
from models import Article, now_utc
from store import SqliteStore
from tests.fakes import FakeLLM


def art(title, domain="pedagogie", url=None, **kw):
    return Article(title=title, url=url or f"https://ex.fr/{abs(hash(title))}", source="Src", domain=domain,
                   published=now_utc().isoformat(), **kw)


def test_rule_fallback():
    a = art("Évaluation par compétences : un référentiel pour la première année")
    assert fallback_category(a) == "Approche par compétences & évaluation"
    assert fallback_content_type(art("Colloque QPES : appel à communications")) == "Événement"
    assert fallback_content_type(art("Un article", kind="paper")) == "Étude / recherche"
    b = art("Classe inversée", description="Un retour d'expérience. Deuxième phrase. Troisième phrase.")
    apply_fallback(b)
    assert b.summary == "Un retour d'expérience. Deuxième phrase." and b.title_fr == "Classe inversée" and not b.classified
    c = art("A study on reading", description="An English abstract.", lang="en")
    apply_fallback(c)
    assert c.summary == ""


def test_fallback_relevance_filter():
    idx = EntityIndex.load()
    a = art("La lecture au cours préparatoire", domain="recherche", filter="superieur")
    apply_fallback(a, idx)
    assert a.relevant is False


async def test_llm_enrichment_and_validation():
    def respond(system, items):
        assert "Pédagogie" in system and "Pédagogies actives" in system
        return {"items": [
            {"id": 0, "category": "Pédagogies actives", "content_type": "Retour d'expérience",
             "other_domains": ["innovation_ia", "pedagogie", "nope"], "title_fr": "Classe inversée et IA",
             "summary": "Résumé en français.", "relevant": True},
            {"id": 1, "category": "Catégorie inventée", "content_type": "Truc", "title_fr": "", "summary": ""},
        ]}
    llm = FakeLLM(respond)
    arts = [art("Flipped classroom and AI", lang="en"), art("Guide de l'évaluation formative", description="Un guide.")]
    await classify_articles(arts, client=llm, pause=0)
    a, b = arts
    assert a.category == "Pédagogies actives" and a.content_type == "Retour d'expérience"
    assert a.domains == ["pedagogie", "innovation_ia"] and a.title_fr == "Classe inversée et IA" and a.classified
    assert b.category == "Approche par compétences & évaluation"   # invalid -> rule fallback
    assert b.content_type == "Outil / ressource" and b.title_fr == b.title and b.summary == "Un guide."
    assert llm.calls[0]["kwargs"]["response_format"] == {"type": "json_object"}


async def test_llm_batches_by_domain_and_relevance_check():
    seen = []

    def respond(system, items):
        seen.append((system.split("« ")[1].split(" »")[0], [i.get("check_relevance") is not None for i in items]))
        return {"items": [{"id": i["id"], "category": "x", "relevant": False} for i in items]}
    arts = [art("A", domain="recherche", filter="superieur"), art("B", domain="pedagogie"), art("C", domain="recherche")]
    await classify_articles(arts, client=FakeLLM(respond), pause=0)
    assert seen == [("Pédagogie", [False]), ("Recherches scientifiques", [True, False])]
    assert [a.relevant for a in arts] == [False, True, True]   # only filtered sources can be dropped


async def test_llm_failure_falls_back_and_budget():
    llm = FakeLLM(lambda s, items: RuntimeError("503"))
    arts = [art(f"Titre {i}") for i in range(5)]
    await classify_articles(arts, client=llm, pause=0, max_llm=2, batch_size=10)
    assert all(not a.classified and a.category for a in arts)
    assert len(llm.calls) == 1 and '"Titre 2"' not in llm.calls[0]["user"]


def test_store_roundtrip(tmp_path):
    s = SqliteStore(str(tmp_path / "t.db"))
    a = art("Un", domains=["pedagogie", "innovation_ia"], entities=["villebon_charpak"], classified=True)
    b = art("Deux", relevant=False)
    old = art("Vieux")
    old.published = (now_utc() - timedelta(days=400)).isoformat()
    s.save_articles([a, b, old, a])
    rows = s.load_articles(30)
    assert [r["title"] for r in rows] == ["Un"] and rows[0]["domains"] == ["pedagogie", "innovation_ia"]
    assert rows[0]["classified"] is True and rows[0]["entities"] == ["villebon_charpak"]
    assert len(s.load_articles(30, relevant_only=False)) == 2
    assert s.known_urls(30) == {a.url, b.url}
    assert [x.title for x in s.load_unclassified()] == ["Deux"]
    s.add_seen_links("P", ["u1", "u2", "u1"])
    s.add_seen_links("P", ["u2", "u3"])
    assert s.seen_links("P") == {"u1", "u2", "u3"} and s.seen_links("Q") == set()


class FakeSupabase:
    """Records the PostgREST calls of SupabaseStore."""

    def __init__(self, data):
        self.data, self.ops = data, []

    def table(self, name):
        outer = self

        class Q:
            def __init__(self):
                self.filters = []

            def select(self, *_):
                return self

            def gte(self, k, v):
                self.filters.append(("gte", k, v)); return self

            def eq(self, k, v):
                self.filters.append(("eq", k, v)); return self

            def order(self, *_):
                return self

            def range(self, a, b):
                self.rng = (a, b); return self

            def upsert(self, rows, on_conflict):
                outer.ops.append(("upsert", name, len(rows), on_conflict)); return self

            def execute(self):
                rows = outer.data.get(name, [])[self.rng[0]:self.rng[1] + 1] if hasattr(self, "rng") else []
                outer.ops.append(("select", name, self.filters))
                return type("R", (), {"data": rows})()
        return Q()


def test_supabase_store_paging_and_upsert():
    from store import SupabaseStore
    fake = FakeSupabase({"seen_links": [{"source": "P", "url": f"u{i}"} for i in range(1500)]})
    s = SupabaseStore("u", "k", client=fake)
    assert len(s.seen_links("P")) == 1500
    s.save_articles([art("Un"), art("Un")])
    assert ("upsert", "articles", 1, "url") in fake.ops
    s.load_unclassified()
    assert any(op[0] == "select" and ("eq", "classified", False) in op[2] for op in fake.ops)


@pytest.mark.parametrize("model,expected", [("openai/gpt-oss-120b", {"reasoning_effort": "low"}), ("llama-3.3-70b", {})])
def test_llm_kwargs(model, expected):
    from llm import llm_kwargs
    assert llm_kwargs(model) == expected


def test_llm_env(monkeypatch):
    import llm
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "GROQ_API_KEY", "GROQ_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert llm.make_client() is None and llm.base_url() == llm.GROQ_BASE_URL and llm.model_name() == llm.DEFAULT_MODEL
    monkeypatch.setenv("GROQ_API_KEY", "gsk")
    monkeypatch.setenv("GROQ_MODEL", "'qwen/qwen3-32b'")
    assert llm.api_key() == "gsk" and llm.model_name() == "qwen/qwen3-32b"
    monkeypatch.setenv("LLM_BASE_URL", "http://gpu.epita.local:8000/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek")
    client = llm.make_client()
    assert str(client.base_url).startswith("http://gpu.epita.local:8000/v1") and llm.model_name() == "deepseek"
