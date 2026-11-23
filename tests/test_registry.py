"""Sources added / removed from the sources page: replay over sources.json, storage, collection and export."""

import json

import dashboard
import registry
from entities import EntityIndex
from fetchers import load_sources
from store import SqliteStore

BASE = [{"name": "Blog A", "domain": "pedagogie", "type": "rss", "url": "https://a.fr/feed"},
        {"name": "LinkedIn", "domain": "design_ux", "type": "page", "url": "https://linkedin.com/x", "enabled": False}]


def row(i, action, name, domain=None, url=None, author=""):
    return {"id": i, "at": f"2026-11-2{i % 10}T08:00:00+00:00", "action": action, "name": name, "domain": domain,
            "url": url, "author": author}


def test_last_action_wins_and_invalid_rows_are_skipped():
    changes = [
        row(1, "add", "Nouveau", "recherche", "https://nouveau.fr/", "Léa"),
        row(2, "remove", "Blog A", author="Tom"),
        row(3, "add", "Piège", "recherche", "javascript:alert(1)"),          # not a web address
        row(4, "add", "Hors sujet", "cuisine", "https://x.fr/"),              # unknown domain
        row(5, "add", "Blog A", "recherche", "https://autre.fr/"),            # cannot override sources.json
        row(6, "restore", "LinkedIn"),                                        # disabled in sources.json: stays off
        row(7, "remove", "Inconnue"),                                         # nothing to remove
        row(8, "delete", "Blog A"),                                           # unknown action
        row(9, "add", "x" * 121, "recherche", "https://long.fr/"),
    ]
    out = {s["name"]: s for s in registry.apply_changes(BASE, list(reversed(changes)))}   # replayed by id
    assert set(out) == {"Blog A", "LinkedIn", "Nouveau"}
    assert out["Blog A"]["enabled"] is False and out["Blog A"]["removed_by"] == "Tom"
    assert out["Blog A"]["url"] == "https://a.fr/feed"
    assert out["LinkedIn"]["enabled"] is False and "removed_at" not in out["LinkedIn"]
    assert out["Nouveau"] == {"name": "Nouveau", "domain": "recherche", "type": "auto", "url": "https://nouveau.fr/",
                              "added_at": "2026-11-21T08:00:00+00:00", "added_by": "Léa"}
    # restore, then re-add an added source with a new address: back on, last address kept
    more = changes + [row(10, "restore", "Blog A"), row(11, "remove", "Nouveau"),
                      row(12, "add", "Nouveau", "recherche", "https://nouveau.fr/blog")]
    out = {s["name"]: s for s in registry.apply_changes(BASE, more)}
    assert out["Blog A"].get("enabled", True) and out["Nouveau"].get("enabled", True)
    assert out["Nouveau"]["url"] == "https://nouveau.fr/blog"
    assert BASE[0].get("enabled", True)   # the base list is never modified in place


def test_store_and_collection_use_page_changes(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": [{"comment": "x"}] + BASE}), encoding="utf-8")
    store = SqliteStore(str(tmp_path / "t.db"))
    store.add_source_change("add", "Nouveau", "recherche", "https://nouveau.fr/", "Léa")
    store.add_source_change("remove", "Blog A")
    rows = store.source_changes()
    assert [r["id"] for r in rows] == [1, 2] and rows[0]["at"].startswith("20") and rows[1]["author"] == ""
    assert [s["name"] for s in load_sources(str(path), store=store)] == ["Nouveau"]
    assert [s["name"] for s in load_sources(str(path))] == ["Blog A"]

    class Broken:
        def source_changes(self):
            raise RuntimeError("PGRST205 table not found")
    assert [s["name"] for s in load_sources(str(path), store=Broken())] == ["Blog A"]   # never blocks the collection


def test_export_embeds_public_api_and_page_changes(tmp_path, monkeypatch):
    from demo_data import generate
    store = SqliteStore(str(tmp_path / "d.db"))
    store.save_articles(generate(n_weeks=4, per_week=10))
    store.add_source_change("add", "Atelier Pédago (démo)", "pedagogie", "https://atelier-pedago.example/")
    out = tmp_path / "site" / "index.html"
    monkeypatch.setenv("SUPABASE_URL", "https://projet.supabase.co/")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "sb_publishable_x")
    monkeypatch.setenv("SUPABASE_KEY", "service")
    dashboard.export(str(out), store=store, index=EntityIndex.load(), client=None, with_embeddings=False, repo="a/b")
    page = (tmp_path / "site" / "sources.html").read_text(encoding="utf-8")
    m = json.loads(page.split('<script id="manage" type="application/json">')[1].split("</script>")[0])
    assert m["api"] == {"url": "https://projet.supabase.co", "key": "sb_publishable_x"}
    assert "service" not in page
    assert [c["name"] for c in m["changes"]] == ["Atelier Pédago (démo)"]
    assert "Design Spot (LinkedIn)" in m["reserved"] and all(b["n"] != "Design Spot (LinkedIn)" for b in m["base"])
    keys = {b["n"]: b["k"] for b in m["base"]}
    assert len(m["domains"]) == 6 and keys["Veille design 5 degrés"] == ["veille-design-5degres.netlify.app"]
    # the added site is now followed: no longer proposed by the agent; the other one gets the add button
    proposed = page.split('id="proposees"')[1].split("</section>")[0]
    assert "Atelier Pédago (démo)" not in proposed and 'class="btn add-cand"' in proposed
    assert "issues/new" not in page
    # the service_role key is never written into the page, even when given as the public key
    monkeypatch.setenv("SUPABASE_ANON_KEY", "service")
    assert dashboard.public_api() is None
    monkeypatch.delenv("SUPABASE_ANON_KEY")
    dashboard.export(str(out), store=store, index=EntityIndex.load(), client=None, with_embeddings=False, repo="a/b")
    page = (tmp_path / "site" / "sources.html").read_text(encoding="utf-8")
    assert '"api":null' in page and "issues/new" in page and "add-cand" not in page.split('id="proposees"')[1].split("</section>")[0]
