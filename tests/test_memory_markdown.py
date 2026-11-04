"""Plain-text memory tests — Markdown export/import round-trip + API.

Covers:
- ``memory_to_markdown`` groups entries by scope and renders readable bullets.
- ``parse_memory_markdown`` reads an edited file back into rows.
- ``POST /v1/memory/export`` writes MEMORY.md (workspace or state dir).
- ``POST /v1/memory/import`` replaces per-section entries (edit → source of truth).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from coworker.memory.base import Scope
from coworker.memory.markdown import (
    memory_to_markdown,
    parse_memory_markdown,
)
from coworker.memory.sqlite_store import SQLiteMemoryStore
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app


class _StubProvider(ProviderClient):
    def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()


def _fixture(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_StubProvider())
    return TestClient(create_app(manager)), manager


# -- markdown round-trip ------------------------------------------------------------


def test_markdown_roundtrip(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "mem.sqlite")
    store.add("prefers short replies", scope=Scope.GLOBAL)
    store.add("avoid merge commits", scope=Scope.GLOBAL)
    store.add("auth lives in login.py", scope=Scope.WORKSPACE, workspace=str(tmp_path))

    md = memory_to_markdown(store.list())
    assert md.startswith("# OpenWorker Memory")
    assert "## Global" in md
    assert f"## Workspace: {tmp_path}" in md
    assert "- prefers short replies" in md
    assert "- auth lives in login.py" in md

    rows = parse_memory_markdown(md)
    globals_ = [r for r in rows if r["scope"] == "global"]
    workspaces = [r for r in rows if r["scope"] == "workspace"]
    assert len(globals_) == 2
    assert len(workspaces) == 1
    assert workspaces[0]["workspace"] == str(tmp_path)
    assert workspaces[0]["content"] == "auth lives in login.py"


def test_parse_handles_edited_bullets_and_empty_sections():
    md = (
        "# OpenWorker Memory\n\n"
        "## Global\n"
        "- first fact\n"
        "- second fact\n\n"
        "## Workspace: /some/project\n"
        "- _nothing_\n\n"
        "## Session: s1\n"
        "- a session note\n"
    )
    rows = parse_memory_markdown(md)
    assert [r["content"] for r in rows if r["scope"] == "global"] == [
        "first fact",
        "second fact",
    ]
    assert [r for r in rows if r["scope"] == "workspace"] == []
    assert [r["content"] for r in rows if r["scope"] == "session"] == ["a session note"]


# -- API ---------------------------------------------------------------------------


def test_memory_export_writes_file(tmp_path):
    client, manager = _fixture(tmp_path)
    manager.memory_store.add("prefers short replies", scope=Scope.GLOBAL)
    res = client.post("/v1/memory/export", json={}).json()
    assert res["ok"] is True
    path = res["path"]
    text = open(path, encoding="utf-8").read()
    assert "prefers short replies" in text
    assert "## Global" in text


def test_memory_import_replaces_sections(tmp_path):
    client, manager = _fixture(tmp_path)
    # Seed a fact that will be removed by the replace semantics.
    manager.memory_store.add("stale global fact", scope=Scope.GLOBAL)
    manager.memory_store.add("stale project fact", scope=Scope.WORKSPACE, workspace=str(tmp_path))

    # Export, edit the file (remove stale, add fresh), re-import.
    path = client.post("/v1/memory/export", json={}).json()["path"]
    edited = (
        "# OpenWorker Memory\n\n"
        "## Global\n"
        "- fresh global fact\n\n"
        f"## Workspace: {tmp_path}\n"
        "- fresh project fact\n"
    )
    open(path, "w", encoding="utf-8").write(edited)

    res = client.post("/v1/memory/import", json={}).json()
    assert res["ok"] is True
    rows = manager.memory_store.list()
    contents = [m.content for m in rows]
    assert "fresh global fact" in contents
    assert "fresh project fact" in contents
    assert "stale global fact" not in contents
    assert "stale project fact" not in contents


def test_memory_import_requires_export_first(tmp_path):
    client, _ = _fixture(tmp_path)
    res = client.post("/v1/memory/import", json={}).json()
    assert res["ok"] is False
    assert "export it first" in res["error"]


def test_memory_reveal_creates_folder(tmp_path):
    client, _ = _fixture(tmp_path)
    res = client.post("/v1/memory/reveal", json={}).json()
    # Reveal opens the OS file manager; on headless CI just the path is created.
    assert res["ok"] is True