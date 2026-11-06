"""Creator 面（P1-4）：能力槽清单与插件 dry-run 预览。

覆盖：`plugin_slots()` 的只读汇总、`preview_plugin()` 四类 kind 的解析与错误码、
以及 `/v1/plugins/*` 路由的错误映射。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import pytest

from coworker.server.app import create_app
from coworker.server.manager import SessionManager


@pytest.fixture
def manager(tmp_path) -> SessionManager:
    return SessionManager(data_dir=tmp_path / "data", workspace=tmp_path)


@pytest.fixture
def client(manager) -> TestClient:
    return TestClient(create_app(manager))


def _write_manifest(folder, manifest_id="demo", tools=("shell",)) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        f"id: {manifest_id}\n"
        "name: Demo\n"
        "tagline: demo coworker\n"
        f"tools: [{', '.join(tools)}]\n"
        "version: \"1\"\n"
        "---\n"
        "You are a demo coworker.\n"
    )
    (folder / "manifest.md").write_text(text, encoding="utf-8")


def test_plugin_slots_lists_capabilities_and_four_kinds(manager):
    result = manager.plugin_slots()
    assert result["ok"] is True

    ids = {c["id"] for c in result["capabilities"]}
    assert {"code_files", "files", "git", "search", "shell", "todo", "security", "egress"} <= ids
    assert all(isinstance(c["risk"], list) for c in result["capabilities"])

    assert set(result["plugins"]) == {"persona", "skill", "connector", "mcp"}
    persona_ids = {p["id"] for p in result["plugins"]["persona"]}
    assert {"cowork", "code", "minimal", "creator", "code-mode"} <= persona_ids
    # 四类都走同一个 consent 形状。
    for rows in result["plugins"].values():
        for row in rows:
            assert set(row) >= {"kind", "id", "name", "capability_set", "risks"}


def test_preview_persona_from_bundle_dir(manager, tmp_path):
    folder = tmp_path / "bundle"
    _write_manifest(folder, manifest_id="demo", tools=("shell", "search"))

    result = manager.preview_plugin("persona", source=str(folder))
    assert result["ok"] is True
    assert result["requires_consent"] is True
    assert result["kind"] == "persona"
    assert result["id"] == "demo"
    assert set(result["capability_set"]) == {"tool:shell", "tool:search"}
    assert "preview" not in result  # dry-run：没有安装动作的字段


def test_preview_persona_errors(manager, tmp_path):
    assert manager.preview_plugin("persona", source=None)["status"] == 400
    missing = manager.preview_plugin("persona", source=str(tmp_path / "nope"))
    assert missing["status"] == 404
    assert missing["ok"] is False


def test_preview_skill_from_bundle_dir(manager, tmp_path):
    folder = tmp_path / "skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: does demo things\n---\n\nSteps.\n",
        encoding="utf-8",
    )
    result = manager.preview_plugin("skill", source=str(folder))
    assert result["ok"] is True
    assert result["kind"] == "skill"
    assert result["id"] == "demo-skill"
    assert result["capability_set"] == ["skill:demo-skill"]


def test_preview_connector_and_mcp_lookup(manager):
    connector = manager.preview_plugin("connector", name="intercom")
    assert connector["ok"] is True
    assert connector["capability_set"] == ["connector:intercom"]

    assert manager.preview_plugin("connector", name="nope")["status"] == 404
    assert manager.preview_plugin("mcp", name="nope")["status"] == 404
    assert manager.preview_plugin("mcp", name=None)["status"] == 400


def test_preview_rejects_unknown_kind(manager):
    result = manager.preview_plugin("wasm")
    assert result["ok"] is False
    assert result["status"] == 400


def test_plugin_routes(client, tmp_path):
    slots = client.get("/v1/plugins/slots")
    assert slots.status_code == 200
    assert slots.json()["ok"] is True

    folder = tmp_path / "bundle"
    _write_manifest(folder)
    ok = client.post("/v1/plugins/preview", json={"kind": "persona", "source": str(folder)})
    assert ok.status_code == 200
    assert ok.json()["requires_consent"] is True

    bad = client.post("/v1/plugins/preview", json={"kind": "wasm"})
    assert bad.status_code == 400
    assert "unknown plugin kind" in bad.json()["error"]

    missing = client.post("/v1/plugins/preview", json={"kind": "persona"})
    assert missing.status_code == 400
