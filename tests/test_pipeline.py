import main
from models import Article, now_utc
from store import SqliteStore
from tests.fakes import FakeLLM


def art(title, url, domain="recherche", **kw):
    return Article(title=title, url=url, source=kw.pop("source", "Educational Researcher"), domain=domain,
                   published=now_utc().isoformat(), **kw)


async def test_daily_run_stores_new_items_and_retries_fallbacks(monkeypatch, tmp_path):
    db = tmp_path / "p.db"
    monkeypatch.setenv("LOCAL_DB", str(db))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    store = SqliteStore(str(db))
    old = art("Ancien sans LLM", "https://ex.fr/old", filter="superieur")
    old.category = "x"
    store.save_articles([old])   # classified = False -> retried

    fetched = [art("Primary school reading", "https://ex.fr/1", filter="superieur"),
               art("Engineering students and feedback", "https://ex.fr/2", filter="superieur"),
               art("Déjà vu", "https://ex.fr/old")]

    async def fake_fetch_all(sources, index, lookback_hours, store):
        assert any(s["type"] == "gnews" and s.get("school") for s in sources)   # school searches are added
        return list(fetched), []
    monkeypatch.setattr(main, "fetch_all", fake_fetch_all)

    def respond(system, items):
        return {"items": [{"id": i["id"], "category": "Formation des ingénieurs", "content_type": "Étude / recherche",
                           "title_fr": "Titre FR", "summary": "Résumé.",
                           "relevant": "Primary" not in i["title"]}
                          for i in items]}
    llm = FakeLLM(respond)
    monkeypatch.setattr(main, "make_client", lambda: llm)
    monkeypatch.setattr("classify.BATCH_PAUSE", 0)
    rc = await main.run(main.parse_args([]))
    assert rc == 0
    rows = {r["url"]: r for r in SqliteStore(str(db)).load_articles(10, relevant_only=False)}
    assert set(rows) == {"https://ex.fr/1", "https://ex.fr/2", "https://ex.fr/old"}
    assert rows["https://ex.fr/1"]["relevant"] is False and rows["https://ex.fr/2"]["relevant"] is True
    assert rows["https://ex.fr/old"]["classified"] is True and rows["https://ex.fr/old"]["title_fr"] == "Titre FR"
    # the retried item got its source filter back from sources.json
    assert any('"check_relevance"' in c["user"] and "Ancien" in c["user"] for c in llm.calls)
