from types import SimpleNamespace

import numpy as np
import pytest

from retrieval.chunking import chunk_document
from retrieval.contextual import AnthropicContextualizer, DiskCache, MockContextualizer
from retrieval.embedding import HashEmbedder
from retrieval.fusion import rrf
from retrieval.types import Chunk, Document, Hit


def doc(text: str, tenant: str = "t1", title: str = "") -> Document:
    return Document(tenant_id=tenant, doc_id="d1", text=text, title=title)


def test_chunker_windows_overlap_and_cover_all_words() -> None:
    words = [f"w{i}" for i in range(250)]
    chunks = chunk_document(doc(" ".join(words)), size=100, overlap=20)
    assert [c.ord for c in chunks] == [0, 1, 2]
    assert chunks[1].content.split()[0] == "w80"  # step = 80
    seen = {w for c in chunks for w in c.content.split()}
    assert seen == set(words)


def test_chunk_ids_are_deterministic_and_tenant_scoped() -> None:
    a = chunk_document(doc("alpha beta gamma"))
    b = chunk_document(doc("alpha beta gamma"))
    c = chunk_document(doc("alpha beta gamma", tenant="t2"))
    assert a[0].chunk_id == b[0].chunk_id
    assert a[0].chunk_id != c[0].chunk_id


def test_chunker_rejects_bad_params_and_empty_docs() -> None:
    with pytest.raises(ValueError):
        chunk_document(doc("x"), size=10, overlap=10)
    assert chunk_document(doc("   ")) == []


def test_title_is_part_of_first_chunk() -> None:
    chunks = chunk_document(doc("body text", title="My Title"))
    assert chunks[0].content.startswith("My Title.")


def _h(cid: str) -> Hit:
    return Hit(cid, "d-" + cid, 0.0, cid)


def test_rrf_rewards_agreement() -> None:
    fused = rrf([[_h("a"), _h("b"), _h("c")], [_h("c"), _h("a"), _h("d")]], k=60)
    assert [h.chunk_id for h in fused][:2] == ["a", "c"]
    assert {h.chunk_id for h in fused} == {"a", "b", "c", "d"}
    assert rrf([[_h("a")], []], top_k=1)[0].chunk_id == "a"


def test_hash_embedder_is_normalised_and_similarity_tracks_overlap() -> None:
    e = HashEmbedder(dim=256)
    q = e.embed_query("vitamin d deficiency in adults")
    near, far = e.embed_documents(["vitamin d deficiency adults study", "graph neural networks"])
    assert np.isclose(np.linalg.norm(q), 1.0)
    assert float(q @ near) > float(q @ far)


def test_indexed_text_includes_context() -> None:
    c = Chunk("t", "d", "c", 0, "content", context="ctx")
    assert c.indexed_text == "ctx\n\ncontent"
    assert Chunk("t", "d", "c", 0, "content").indexed_text == "content"


def test_mock_contextualizer() -> None:
    chunks = chunk_document(doc("First sentence. Second one."))
    out = MockContextualizer().contextualize("First sentence. Second one.", chunks)
    assert out[0].context.endswith("First sentence")


class _FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=" situating context ")],
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=5,
                cache_read_input_tokens=7,
                cache_creation_input_tokens=0,
            ),
        )


def test_anthropic_contextualizer_caches_on_disk_and_marks_doc_cacheable(tmp_path) -> None:
    pytest.importorskip("anthropic")
    fake = _FakeMessages()
    cache = DiskCache(tmp_path / "ctx.jsonl")
    cz = AnthropicContextualizer(cache)
    cz._client = SimpleNamespace(messages=fake)
    chunks = chunk_document(doc(" ".join(f"w{i}" for i in range(200))), size=100, overlap=0)

    out = cz.contextualize("the doc", chunks)
    assert [c.context for c in out] == ["situating context"] * 2
    assert len(fake.calls) == 2
    first_block = fake.calls[0]["messages"][0]["content"][0]
    assert first_block["cache_control"] == {"type": "ephemeral"}
    assert fake.calls[0]["model"] == "claude-haiku-5-5"
    assert cz.usage.cache_read_input_tokens == 14

    # Second run (new process, same cache file) makes no API calls.
    cz2 = AnthropicContextualizer(DiskCache(tmp_path / "ctx.jsonl"))
    cz2._client = SimpleNamespace(messages=fake)
    cz2.contextualize("the doc", chunks)
    assert len(fake.calls) == 2
    assert cz2.usage.cache_hits_local == 2
