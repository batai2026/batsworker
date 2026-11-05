"""Tests for the unified PluginManifest adapter layer (P1-3).

Covers: four adapters, consent_summary, capability_diff, and the lifecycle
install/update/uninstall facade (requires_consent logic + signature passthrough).
"""

from __future__ import annotations

import pytest

from coworker.mcp.config import MCPServerDef
from coworker.personas.manifest import PersonaManifest, parse_manifest
from coworker.plugins import (
    KIND_CONNECTOR,
    KIND_MCP,
    KIND_PERSONA,
    KIND_SKILL,
    ConnectorPluginAdapter,
    MCPAdapter,
    PersonaAdapter,
    PluginManifest,
    SkillAdapter,
    capability_diff,
    consent_summary,
    install,
    uninstall,
    update,
)
from coworker.skills.base import Skill


# -- fixtures -----------------------------------------------------------------


@pytest.fixture
def persona_manifest() -> PersonaManifest:
    text = (
        "---\n"
        "id: ops\n"
        "name: Ops\n"
        "description: On-call operator\n"
        "tools:\n  - shell\n  - files\n"
        "mcp:\n  - prometheus\n"
        "connectors:\n  - slack\n"
        "messaging: true\n"
        "version: \"1.2\"\n"
        "---\n"
        "You are an ops coworker.\n"
    )
    return parse_manifest(text)


@pytest.fixture
def skill() -> Skill:
    return Skill(
        name="write-tests",
        description="Write unit tests for a function",
        instructions="Given a function, write pytest tests.",
        path="/tmp/skills/write-tests",
        allowed_tools=["write_file", "run_shell"],
    )


@pytest.fixture
def connector_plugin() -> dict:
    return {
        "name": "intercom",
        "title": "Intercom",
        "blurb": "Search conversations in Intercom.",
        "auth": "token",
        "two_way": False,
        "mcp_url": "",
    }


@pytest.fixture
def mcp_server() -> MCPServerDef:
    return MCPServerDef(
        name="prometheus",
        transport="http",
        url="https://mcp.example.com/prometheus",
        requires_approval=True,
        include_tools=["query"],
    )


# -- adapter tests ------------------------------------------------------------


class TestPersonaAdapter:
    def test_to_manifest_fields(self, persona_manifest):
        pm = PersonaAdapter().to_manifest(persona_manifest)
        assert pm.kind == KIND_PERSONA
        assert pm.id == "ops"
        assert pm.name == "Ops"
        assert pm.version == "1.2"
        assert pm.description == "On-call operator"

    def test_capability_set_includes_tools_mcp_connectors(self, persona_manifest):
        caps = PersonaAdapter().capability_set(persona_manifest)
        assert "tool:shell" in caps
        assert "tool:files" in caps
        assert "mcp:prometheus" in caps
        assert "connector:slack" in caps
        assert "messaging" in caps

    def test_permissions(self, persona_manifest):
        perms = PersonaAdapter().permissions(persona_manifest)
        assert perms["connectors"] == ["slack"]
        assert perms["messaging"] is True
        assert perms["default_permission_mode"] == "interactive"


class TestSkillAdapter:
    def test_to_manifest(self, skill):
        pm = SkillAdapter().to_manifest(skill)
        assert pm.kind == KIND_SKILL
        assert pm.id == "write-tests"
        assert pm.capability_set == frozenset({"skill:write-tests"})

    def test_permissions(self, skill):
        assert SkillAdapter().permissions(skill)["allowed_tools"] == [
            "write_file",
            "run_shell",
        ]


class TestConnectorPluginAdapter:
    def test_to_manifest(self, connector_plugin):
        pm = ConnectorPluginAdapter().to_manifest(connector_plugin)
        assert pm.kind == KIND_CONNECTOR
        assert pm.id == "intercom"
        assert pm.capability_set == frozenset({"connector:intercom"})
        assert pm.permissions["auth"] == "token"
        assert pm.permissions["mcp_backed"] is False

    def test_mcp_backed(self):
        pm = ConnectorPluginAdapter().to_manifest({"name": "jira", "mcp_url": "https://x"})
        assert pm.permissions["mcp_backed"] is True


class TestMCPAdapter:
    def test_to_manifest(self, mcp_server):
        pm = MCPAdapter().to_manifest(mcp_server)
        assert pm.kind == KIND_MCP
        assert pm.id == "prometheus"
        assert pm.capability_set == frozenset({"mcp:prometheus"})
        assert pm.permissions["requires_approval"] is True
        assert pm.permissions["include_tools"] == ["query"]
        assert pm.permissions["transport"] == "http"


# -- consent tests ------------------------------------------------------------


class TestConsent:
    def test_consent_summary_shape(self, persona_manifest):
        pm = PersonaAdapter().to_manifest(persona_manifest)
        summary = consent_summary(pm)
        for key in (
            "kind",
            "id",
            "name",
            "version",
            "capability_set",
            "risks",
            "permissions",
            "signature",
        ):
            assert key in summary
        assert summary["kind"] == KIND_PERSONA
        assert "tools" in summary["risks"]
        assert "mcp" in summary["risks"]
        assert "connectors" in summary["risks"]

    def test_consent_summary_all_kinds(self, persona_manifest, skill, connector_plugin, mcp_server):
        pms = [
            PersonaAdapter().to_manifest(persona_manifest),
            SkillAdapter().to_manifest(skill),
            ConnectorPluginAdapter().to_manifest(connector_plugin),
            MCPAdapter().to_manifest(mcp_server),
        ]
        for pm in pms:
            summary = consent_summary(pm)
            assert isinstance(summary["capability_set"], list)
            assert isinstance(summary["risks"], list)
            assert isinstance(summary["permissions"], dict)

    def test_capability_diff_grew(self):
        old = PluginManifest(kind=KIND_SKILL, id="x", name="x", capability_set=frozenset({"skill:x"}))
        new = PluginManifest(
            kind=KIND_SKILL, id="x", name="x",
            capability_set=frozenset({"skill:x", "skill:y"}),
        )
        diff = capability_diff(old, new)
        assert diff["grew"] == ["skill:y"]
        assert diff["shrank"] == []
        assert diff["same"] == ["skill:x"]

    def test_capability_diff_shrank(self):
        old = PluginManifest(kind=KIND_SKILL, id="x", name="x", capability_set=frozenset({"skill:x", "skill:y"}))
        new = PluginManifest(kind=KIND_SKILL, id="x", name="x", capability_set=frozenset({"skill:x"}))
        diff = capability_diff(old, new)
        assert diff["shrank"] == ["skill:y"]
        assert diff["grew"] == []

    def test_capability_diff_same(self):
        s = frozenset({"skill:x"})
        old = PluginManifest(kind=KIND_SKILL, id="x", name="x", capability_set=s)
        new = PluginManifest(kind=KIND_SKILL, id="x", name="x", capability_set=s)
        diff = capability_diff(old, new)
        assert diff["grew"] == []
        assert diff["shrank"] == []
        assert diff["same"] == ["skill:x"]


# -- lifecycle tests ----------------------------------------------------------


class TestLifecycle:
    def test_install_requires_consent_true(self, persona_manifest):
        result = install(KIND_PERSONA, persona_manifest)
        assert result["requires_consent"] is True
        assert result["kind"] == KIND_PERSONA
        assert result["id"] == "ops"

    def test_update_same_capabilities_no_consent(self, persona_manifest):
        # existing_native 同能力 → grew 为空 → requires_consent False
        result = update(
            KIND_PERSONA,
            persona_manifest,
            existing_native=persona_manifest,
        )
        assert result["requires_consent"] is False
        assert result["diff"]["grew"] == []

    def test_update_grew_capabilities_requires_consent(self, persona_manifest):
        grown = parse_manifest(
            "---\nid: ops\nname: Ops\ntools:\n  - shell\n  - files\n  - git\nmcp:\n  - prometheus\n---\nbody\n"
        )
        result = update(KIND_PERSONA, grown, existing_native=persona_manifest)
        assert result["requires_consent"] is True
        assert "tool:git" in result["diff"]["grew"]

    def test_update_without_existing_requires_consent(self, persona_manifest):
        result = update(KIND_PERSONA, persona_manifest)
        assert result["requires_consent"] is True

    def test_install_with_source_dir_signature(self, tmp_path, persona_manifest):
        # 未签名目录 → verify_directory 返回 {"signed": False}
        result = install(KIND_PERSONA, persona_manifest, source_dir=tmp_path)
        assert result["signature"] == {"signed": False}

    def test_install_signed_directory(self, tmp_path, skill):
        # 手动写 manifest.sha256 + .minisig 来验证 signature 字段被填入
        (tmp_path / "manifest.sha256").write_text("", encoding="utf-8")
        (tmp_path / "manifest.sha256.minisig").write_text(
            "untrusted comment: signature from abc123\n"
            "RWRCAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==\n"
            "trusted comment: timestamp:1\n"
            "RWRCAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==\n",
            encoding="ascii",
        )
        result = install(KIND_SKILL, skill, source_dir=tmp_path)
        # 无 public_key → valid 为 None（密码学校验需要 key）；signed 为 True
        assert result["signature"].get("signed") is True
        assert result["signature"].get("valid") is None

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            install("unknown_kind", object())

    def test_uninstall_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            uninstall("unknown_kind", "x")


class TestLifecycleCommit:
    """commit=True 时应委托原生 store。用 fake store 验证调用。"""

    def test_commit_skill_calls_store_create(self, skill):
        calls = []

        class FakeStore:
            def create(self, **kwargs):
                calls.append(kwargs)

        store = FakeStore()
        # adapter 输入是 Skill，store create 入参是 kwargs dict → 用 commit_payload
        commit_payload = {
            "name": skill.name,
            "description": skill.description,
            "instructions": skill.instructions,
        }
        result = install(KIND_SKILL, skill, commit=True, commit_payload=commit_payload, store=store)
        assert result["requires_consent"] is True
        assert len(calls) == 1
        assert calls[0]["name"] == skill.name

    def test_commit_persona_calls_install_from_dir(self, tmp_path, persona_manifest):
        calls = []

        class FakeRegistry:
            def install_from_dir(self, directory):
                calls.append(directory)

        registry = FakeRegistry()
        # adapter 输入是 PersonaManifest，store 入参是目录路径 → 用 commit_payload
        result = install(
            KIND_PERSONA,
            persona_manifest,
            commit=True,
            commit_payload=str(tmp_path),
            store=registry,
        )
        assert result["requires_consent"] is True
        assert calls == [str(tmp_path)]

    def test_uninstall_skill_calls_delete(self):
        calls = []

        class FakeStore:
            def delete(self, name):
                calls.append(name)

        store = FakeStore()
        result = uninstall(KIND_SKILL, "my-skill", store=store)
        assert result["ok"] is True
        assert calls == ["my-skill"]
