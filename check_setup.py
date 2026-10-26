"""Radar Pédago - setup check: are the secrets present and do Supabase and the LLM endpoint accept them?

Run it from GitHub (Actions -> "Radar Pédago - Check setup") or locally with a .env. It never prints a secret:
only OK/KO statuses, the Supabase project id from SUPABASE_URL and the LLM endpoint host.
"""

import base64
import json
import os
import sys
import urllib.parse as up

import requests

TABLES = ("articles", "seen_links")
OK, KO, WARN = "OK ", "KO ", "?? "
TIMEOUT = 20


def jwt_claims(key: str) -> dict:
    """Payload of a legacy Supabase key (a JWT). Returns {} for new-format keys (sb_secret_...) or garbage."""
    parts = key.split(".")
    if len(parts) != 3:
        return {}
    try:
        return json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except Exception:
        return {}


def check_supabase(env=os.environ) -> list[tuple[str, str]]:
    url, key = (env.get("SUPABASE_URL") or "").strip(), (env.get("SUPABASE_KEY") or "").strip()
    if not url or not key:
        return [(KO, "SUPABASE_URL / SUPABASE_KEY not set: nothing would be kept between runs")]
    out = []
    host = up.urlparse(url).netloc
    if not url.startswith("https://") or not host.endswith(".supabase.co") or up.urlparse(url).path not in ("", "/"):
        out.append((WARN, "SUPABASE_URL should look like https://<project-id>.supabase.co (no path, no /rest/v1)"))
    url_ref = host.split(".")[0]
    out.append((OK, f"SUPABASE_URL points to project '{url_ref}'"))
    claims = jwt_claims(key)
    if claims:
        role, ref = claims.get("role"), claims.get("ref", "")
        out.append((OK if role == "service_role" else KO, f"key role: {role} (must be service_role, not anon)"))
        if ref and ref != url_ref:
            out.append((KO, f"key belongs to project '{ref}' but SUPABASE_URL points to '{url_ref}'"))
    elif key.startswith("sb_secret_"):
        out.append((OK, "key is a new-format secret key"))
    elif key.startswith("sb_publishable_"):
        out.append((KO, "key is a PUBLISHABLE key: use the secret key (Project Settings -> API Keys)"))
    else:
        out.append((WARN, "key format not recognised: role cannot be inspected"))
    base = url.rstrip("/")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    for table in TABLES:
        try:
            r = requests.get(f"{base}/rest/v1/{table}?select=*&limit=1", headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            out.append((KO, f"table {table}: unreachable ({type(e).__name__})"))
            continue
        if r.status_code == 200:
            out.append((OK, f"table {table}: exists"))
        elif r.status_code == 404 and "PGRST205" in r.text:
            out.append((KO, f"table {table}: MISSING in this project -> run schema.sql there"))
        elif r.status_code in (401, 403):
            out.append((KO, f"table {table}: key rejected (HTTP {r.status_code})"))
        else:
            out.append((KO, f"table {table}: HTTP {r.status_code}"))
    return out


def check_llm(env=os.environ) -> list[tuple[str, str]]:
    import llm
    get = lambda *names: next((v for n in names if (v := (env.get(n) or "").strip().strip("'\"").strip())), "")
    key = get("LLM_API_KEY", "GROQ_API_KEY")
    base = get("LLM_BASE_URL") or llm.GROQ_BASE_URL
    model = get("LLM_MODEL", "GROQ_MODEL") or llm.DEFAULT_MODEL
    host = up.urlparse(base).netloc
    if not key:
        return [(WARN, "no LLM key (LLM_API_KEY or GROQ_API_KEY): rule-based enrichment only — no translation, "
                       "no generated summary")]
    try:
        r = requests.get(f"{base.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
    except requests.RequestException as e:
        return [(KO, f"LLM endpoint {host} unreachable ({type(e).__name__})")]
    if r.status_code != 200:
        return [(KO, f"LLM endpoint {host}: key rejected (HTTP {r.status_code})")]
    ids = {m.get("id") for m in r.json().get("data", [])}
    return [(OK, f"LLM endpoint {host}: key accepted"),
            (OK if model in ids else KO, f"model '{model}' " + ("is available" if model in ids else
                                                               "is NOT available: fix the LLM_MODEL / GROQ_MODEL variable"))]


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    failed = False
    for title, check in (("Supabase", check_supabase), ("LLM", check_llm)):
        print(f"\n== {title}")
        for status, msg in check():
            print(f"  {status} {msg}")
            failed |= status == KO
    print("\n" + ("Some checks failed." if failed else "Setup looks good."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
