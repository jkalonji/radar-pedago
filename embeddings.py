"""Semantic search vectors, computed in the pipeline with the exact ONNX model the browser loads (transformers.js).

Model: Xenova/paraphrase-multilingual-MiniLM-L12-v2 (384 dims, 50+ languages: a French query also finds English
papers). Both sides use onnx/model_quantized.onnx with mean pooling + L2 normalisation, so the vectors are
comparable. Vectors are exported as int8 (x127) in site/embeddings.bin, in the order of the articles of the page.
"""

import logging
import os

import numpy as np

MODEL_ID = "Xenova/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_FILE = "onnx/model_quantized.onnx"
DIM = 384
MAX_TOKENS = 128
BATCH = 32


class Encoder:
    """Mean-pooled, L2-normalised sentence vectors from the ONNX model (same recipe as transformers.js)."""

    def __init__(self, cache_dir: str | None = None):
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
        cache_dir = cache_dir or os.environ.get("MODEL_CACHE", "data/models")
        model_path = hf_hub_download(MODEL_ID, MODEL_FILE, cache_dir=cache_dir)
        tok_path = hf_hub_download(MODEL_ID, "tokenizer.json", cache_dir=cache_dir)
        self.tokenizer = Tokenizer.from_file(tok_path)
        self.tokenizer.enable_truncation(MAX_TOKENS)
        pad = self.tokenizer.token_to_id("<pad>")
        self.tokenizer.enable_padding(pad_id=pad if pad is not None else 0, pad_token="<pad>")
        self.session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}

    def encode(self, texts: list[str]) -> np.ndarray:
        out = []
        for i in range(0, len(texts), BATCH):
            enc = self.tokenizer.encode_batch(texts[i:i + BATCH])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            out.append(pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None))
        return np.vstack(out) if out else np.zeros((0, DIM), dtype=np.float32)


def article_text(r: dict) -> str:
    """What gets embedded: French title, original title when translated, and the French summary."""
    parts = [r.get("title_fr") or r.get("title") or ""]
    if r.get("title") and r.get("title") != r.get("title_fr"):
        parts.append(r["title"])
    if r.get("summary"):
        parts.append(r["summary"])
    return ". ".join(p for p in parts if p)


def quantize(vectors: np.ndarray) -> bytes:
    return np.clip(np.round(vectors * 127.0), -127, 127).astype(np.int8).tobytes()


def build_embeddings(rows: list[dict], encoder=None) -> bytes | None:
    """int8 vectors for `rows` (same order), or None when the model cannot be loaded (keyword search still works)."""
    if not rows:
        return b""
    try:
        encoder = encoder or Encoder()
        vectors = encoder.encode([article_text(r) for r in rows])
    except Exception as e:
        logging.warning(f"Embeddings skipped (semantic search will fall back to keywords): {e}")
        print(f"::warning::Embeddings skipped: {e}")
        return None
    logging.info(f"Embeddings: {len(rows)} articles x {vectors.shape[1]} dims")
    return quantize(vectors)
