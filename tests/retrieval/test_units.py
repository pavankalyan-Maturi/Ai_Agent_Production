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


def test_groq_contextualizer_retries_429_and_caches(tmp_path, monkeypatch) -> None:
    import httpx2

    from retrieval.contextual import ContextBudgetExceeded, GroqContextualizer

    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(req: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        assert req.headers["authorization"] == "Bearer k"
        if calls["n"] == 1:
            return httpx2.Response(429, headers={"retry-after": "1"})
        return httpx2.Response(
            200,
            json={
                "choices": [{"message": {"content": " ctx "}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 5},
            },
        )

    cz = GroqContextualizer(DiskCache(tmp_path / "c.jsonl"), api_key="k")
    cz._http = httpx2.Client(
        transport=httpx2.MockTransport(handler), headers={"Authorization": "Bearer k"}
    )
    chunks = chunk_document(doc("alpha beta gamma"))
    assert cz.contextualize("alpha beta gamma", chunks)[0].context == "ctx"
    assert calls["n"] == 2 and cz.usage.input_tokens == 50
    cz.contextualize("alpha beta gamma", chunks)
    assert calls["n"] == 2  # served from disk cache

    late = GroqContextualizer(DiskCache(tmp_path / "d.jsonl"), api_key="k", deadline=0.0)
    with pytest.raises(ContextBudgetExceeded):
        late.contextualize("x y", chunk_document(doc("x y")))


def test_groq_resolve_model_falls_back_when_preferred_is_retired() -> None:
    import httpx2
    import pytest

    from retrieval.contextual import GroqContextualizer

    def models(ids: list[str]) -> httpx2.MockTransport:
        data = {"data": [{"id": i, "active": True} for i in ids]}
        return httpx2.MockTransport(lambda req: httpx2.Response(200, json=data))

    pick = GroqContextualizer.resolve_model
    assert pick("k", "x-model", models(["x-model", "llama-3.1-8b-instant"])) == "x-model"
    assert pick("k", "gone", models(["whisper", "llama-3.3-70b-versatile"])) == (
        "llama-3.3-70b-versatile"
    )
    with pytest.raises(RuntimeError, match="none of"):
        pick("k", "gone", models(["whisper-large-v3"]))


def test_groq_daily_quota_stops_with_budget_exceeded(tmp_path, monkeypatch) -> None:
    import httpx2
    import pytest

    from retrieval.contextual import ContextBudgetExceeded, GroqContextualizer

    monkeypatch.setattr("time.sleep", lambda s: None)
    cz = GroqContextualizer(
        DiskCache(tmp_path / "c.jsonl"), api_key="k", model="openai/gpt-oss-20b"
    )
    seen = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["body"] = req.read()
        return httpx2.Response(429, headers={"retry-after": "3600"}, text="tokens per day")

    cz._http = httpx2.Client(transport=httpx2.MockTransport(handler))
    with pytest.raises(ContextBudgetExceeded, match="quota"):
        cz.contextualize("alpha", chunk_document(doc("alpha")))
    assert b'"reasoning_effort":"low"' in seen["body"].replace(b" ", b"")
