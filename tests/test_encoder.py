"""The ONNX encoder plumbing (tokenizer, feeds, mean pooling, L2 norm) on a tiny local model: no download."""

import numpy as np
import pytest

import embeddings

onnx = pytest.importorskip("onnx")


def _tiny_model(path, vocab=8, dim=4):
    from onnx import TensorProto, helper, numpy_helper
    table = np.arange(vocab * dim, dtype=np.float32).reshape(vocab, dim) / 10
    graph = helper.make_graph(
        [helper.make_node("Gather", ["table", "input_ids"], ["last_hidden_state"])],
        "tiny",
        [helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["b", "t"]),
         helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["b", "t"]),
         helper.make_tensor_value_info("token_type_ids", TensorProto.INT64, ["b", "t"])],
        [helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, ["b", "t", dim])],
        [numpy_helper.from_array(table, "table")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, path)
    return table


def _tiny_tokenizer(path):
    from tokenizers import Tokenizer, models, pre_tokenizers
    tok = Tokenizer(models.WordLevel({"<pad>": 1, "<unk>": 0, "classe": 2, "inversee": 3, "ia": 4}, unk_token="<unk>"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.save(path)


def test_encoder_mean_pools_and_ignores_padding(monkeypatch, tmp_path):
    table = _tiny_model(str(tmp_path / "m.onnx"))
    _tiny_tokenizer(str(tmp_path / "t.json"))
    files = {embeddings.MODEL_FILE: str(tmp_path / "m.onnx"), "tokenizer.json": str(tmp_path / "t.json")}
    monkeypatch.setattr("huggingface_hub.hf_hub_download", lambda repo, name, cache_dir=None: files[name])
    enc = embeddings.Encoder(cache_dir=str(tmp_path))
    out = enc.encode(["classe inversee", "ia"])
    expected0 = (table[2] + table[3]) / 2
    expected1 = table[4]          # the padded position of the short text must not count
    assert np.allclose(out[0], expected0 / np.linalg.norm(expected0), atol=1e-6)
    assert np.allclose(out[1], expected1 / np.linalg.norm(expected1), atol=1e-6)
