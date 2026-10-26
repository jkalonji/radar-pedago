"""Test doubles: an in-memory LLM client and a deterministic encoder mirrored by a fake transformers.js module."""

import json
import re
from types import SimpleNamespace

import numpy as np

from text import normalize

DIM = 384


def fake_vector(text: str) -> np.ndarray:
    """Hashing bag-of-words, L2-normalised. FAKE_TRANSFORMERS_JS computes exactly the same thing in the browser."""
    v = np.zeros(DIM, dtype=np.float32)
    for w in re.findall(r"[a-z0-9]+", normalize(text)):
        if len(w) >= 3:
            h = 0
            for ch in w:
                h = (h * 31 + ord(ch)) % DIM
            v[h] += 1
    n = np.linalg.norm(v)
    return v / n if n else v


class FakeEncoder:
    def encode(self, texts):
        return np.vstack([fake_vector(t) for t in texts]) if texts else np.zeros((0, DIM), dtype=np.float32)


FAKE_TRANSFORMERS_JS = """
const norm = s => String(s).normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toLowerCase();
function vec(text) {
  const v = new Float32Array(384);
  for (const w of norm(text).match(/[a-z0-9]+/g) || []) {
    if (w.length < 3) continue;
    let h = 0; for (const ch of w) h = (h * 31 + ch.charCodeAt(0)) % 384;
    v[h] += 1;
  }
  let n = 0; for (const x of v) n += x * x; n = Math.sqrt(n) || 1;
  return v.map(x => x / n);
}
export async function pipeline(task, model, opts) {
  window.__pipelineArgs = {task, model, opts};
  return async (text, o) => ({data: vec(text), dims: [1, 384]});
}
"""


class FakeLLM:
    """Mimics client.chat.completions.create; `responder(system, user_items)` returns the JSON object to answer."""

    def __init__(self, responder):
        self.calls = []
        self.responder = responder
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        system, user = kwargs["messages"][0]["content"], kwargs["messages"][1]["content"]
        self.calls.append({"system": system, "user": user, "kwargs": kwargs})
        content = self.responder(system, json.loads(user))
        if isinstance(content, Exception):
            raise content
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))])
