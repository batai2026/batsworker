"""Local RAG (rag.py): chunking, incremental re-index, cosine search, tools."""

from pathlib import Path

import pytest

from coworker.rag import (
    VectorStore,
    chunk_text,
    collect_files,
    make_embedder,
    reindex_workspace,
    search_workspace,
)


class FakeEmbedder:
    """Deterministic embedder: vector = [count('a'), count('b'), …] over a fixed
    vocabulary, so search results are predictable without a network."""

    VOCAB = "abcdef"

    def embed(self, texts):
        return [self._vec(t) for t in texts]

    def _vec(self, text: str):
        lower = text.lower()
        return [float(lower.count(c)) for c in self.VOCAB]


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    d = tmp_path / "ws"
    d.mkdir(parents=True)
    return d


# -- chunking ---------------------------------------------------------------------


def test_short_text_single_chunk():
    assert chunk_text("hello world") == ["hello world"]


def test_long_text_chunks_with_overlap():
    text = "word " * 500  # ~2500 chars
    chunks = chunk_text(text)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 1200


def test_empty_text_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


# -- collect ----------------------------------------------------------------------


def test_collect_whitelist_extensions(ws):
    (ws / "a.md").write_text("text")
    (ws / "b.py").write_text("code")
    (ws / "c.bin").write_bytes(b"\x00\x01")
    (ws / "node_modules" / "junk.js").parent.mkdir(parents=True)
    (ws / "node_modules" / "junk.js").write_text("code")
    found = collect_files(ws)
    names = {f.name for f in found}
    assert names == {"a.md", "b.py"}


def test_collect_size_cap(ws):
    big = ws / "big.md"
    big.write_text("x" * (1024 * 1024 + 1))
    assert collect_files(ws) == []


# -- incremental re-index ----------------------------------------------------------


def test_reindex_incremental(ws):
    (ws / "a.md").write_text("about database indexes")
    store = VectorStore(ws / ".rag" / "rag.db")
    embedder = FakeEmbedder()
    stats = reindex_workspace(str(ws), embedder=embedder, store=store)
    assert stats["embedded"] == 1 and stats["skipped"] == 0
    # Second run: content unchanged → skipped (embedder not called for it).
    stats2 = reindex_workspace(str(ws), embedder=embedder, store=store)
    assert stats2["skipped"] == 1 and stats2["embedded"] == 0
    # Content changes → re-embedded.
    (ws / "a.md").write_text("about database indexes, revised")
    stats3 = reindex_workspace(str(ws), embedder=embedder, store=store)
    assert stats3["embedded"] == 1


def test_reindex_drops_removed_files(ws):
    (ws / "gone.md").write_text("temporary")
    store = VectorStore(ws / ".rag" / "rag.db")
    reindex_workspace(str(ws), embedder=FakeEmbedder(), store=store)
    assert "gone.md" in {p.split("/")[-1] for p in store.indexed_paths(str(ws.resolve()))}
    (ws / "gone.md").unlink()
    stats = reindex_workspace(str(ws), embedder=FakeEmbedder(), store=store)
    assert stats["removed"] == 1


# -- search -------------------------------------------------------------------------


def test_search_ranks_relevant_first(ws):
    (ws / "database.md").write_text("database indexes and query optimization")
    (ws / "cooking.md").write_text("how to bake bread and roast vegetables")
    store = VectorStore(ws / ".rag" / "rag.db")
    reindex_workspace(str(ws), embedder=FakeEmbedder(), store=store)
    hits = search_workspace(
        str(ws), "database query optimization", embedder=FakeEmbedder(), store=store, k=2
    )
    assert len(hits) == 2
    assert "database.md" in hits[0]["path"]
    assert hits[0]["score"] > hits[1]["score"]


def test_search_empty_index(ws):
    store = VectorStore(ws / ".rag" / "rag.db")
    hits = search_workspace(str(ws), "anything", embedder=FakeEmbedder(), store=store)
    assert hits == []


# -- tools --------------------------------------------------------------------------


def _make_tools(ws: Path, config: dict):
    from coworker.rag import rag_tools

    return rag_tools(workspace=str(ws), config=config, state_dir=ws / ".rag")


def test_tools_not_configured_error(ws):
    tools = _make_tools(ws, {"embedding_base_url": "", "embedding_model": ""})
    by_name = {t.__name__: t for t in tools}
    result = by_name["semantic_search"](query="anything")
    assert "embedding endpoint" in result["error"]


def test_tools_with_fake_embedder(ws):
    # Monkeypatch the embedder factory to avoid the network.
    import coworker.rag as rag_mod
    from coworker.rag import VectorStore

    (ws / "note.md").write_text("database indexes and optimization")
    store = VectorStore(ws / ".rag" / "rag.db")
    reindex_workspace(str(ws), embedder=FakeEmbedder(), store=store)
    tools = _make_tools(ws, {"embedding_base_url": "http://fake", "embedding_model": "m"})
    # Patch the tool's internal embedder construction.
    real = rag_mod.make_embedder
    rag_mod.make_embedder = lambda cfg: FakeEmbedder()
    try:
        by_name = {t.__name__: t for t in tools}
        result = by_name["semantic_search"](query="database indexes")
        assert result["ok"]
        assert any("note.md" in r["path"] for r in result["results"])
    finally:
        rag_mod.make_embedder = real


def test_make_embedder_incomplete_config():
    assert make_embedder({"embedding_base_url": ""}) is None
    assert make_embedder({"embedding_base_url": "http://x", "embedding_model": "m"}) is not None
