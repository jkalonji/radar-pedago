"""Single OpenAI-compatible LLM client: Groq today, the school's vLLM tomorrow (change LLM_BASE_URL / LLM_MODEL only).

Environment:
  LLM_BASE_URL   default https://api.groq.com/openai/v1
  LLM_API_KEY    falls back to GROQ_API_KEY (a self-hosted vLLM may accept any value)
  LLM_MODEL      falls back to GROQ_MODEL, then openai/gpt-oss-120b
"""

import asyncio
import json
import logging
import os
import re

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"


def _env(*names: str) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip().strip("'\"").strip()
        if v:
            return v
    return ""


def base_url() -> str:
    return _env("LLM_BASE_URL") or GROQ_BASE_URL


def model_name() -> str:
    return _env("LLM_MODEL", "GROQ_MODEL") or DEFAULT_MODEL


def api_key() -> str:
    return _env("LLM_API_KEY", "GROQ_API_KEY")


def make_client():
    """AsyncOpenAI client on the configured endpoint, or None when no key is set (rule-based fallback)."""
    key = api_key()
    if not key:
        return None
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=key, base_url=base_url(), max_retries=2, timeout=90)


def llm_kwargs(model: str) -> dict:
    """Only reasoning models accept `reasoning_effort`."""
    return {"reasoning_effort": "low"} if re.search(r"gpt-oss|qwen3", model or "") else {}


def _retry_after(exc: Exception) -> float | None:
    """Seconds to wait on a rate limit (HTTP 429), None for other errors."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if status != 429:
        return None
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    try:
        return min(float(headers.get("retry-after", 20)), 90.0)
    except (TypeError, ValueError):
        return 20.0


async def chat_json(client, system: str, user: str, *, model: str | None = None, max_tokens: int = 4000,
                    attempts: int = 3) -> dict:
    """One chat completion answered as a JSON object. Waits and retries on rate limits."""
    model = model or model_name()
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.1, max_tokens=max_tokens, response_format={"type": "json_object"},
                **llm_kwargs(model),
            )
            content = resp.choices[0].message.content or ""
            return json.loads(content[content.find("{"): content.rfind("}") + 1] or content)
        except Exception as e:
            last = e
            wait = _retry_after(e)
            if wait is None or attempt == attempts - 1:
                break
            logging.warning(f"LLM rate limited - waiting {wait:.0f}s")
            await asyncio.sleep(wait)
    raise last if last else RuntimeError("LLM call failed")
