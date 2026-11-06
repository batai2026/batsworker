"""Harness 注册表（P2-6）：harness 定义从 persona manifest 单一派生。

覆盖：四个内置档的能力集/提示/权限模式与来源；manifest 正文即系统提示；
用户安装的 persona 可直接当 harness；未知 id 拒绝；注入自定义 spec 能跑 stub 矩阵。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coworker.agents.code import CODE_CAPABILITIES, CODE_INSTRUCTIONS
from coworker.eval.matrix import RecordedTurn, run_matrix
from coworker.eval import matrix
from coworker.personas import harness as harness_module
from coworker.personas.harness import (
    HarnessSpec,
    builtin_specs,
    installed_specs,
    resolve,
)
from coworker.personas.manifest import load_manifest_file
from coworker.personas.registry import PersonaRegistry


def _builtin_manifest(persona_id: str):
    path = Path(harness_module.__file__).parent / "builtin" / persona_id / "manifest.md"
    return load_manifest_file(path, builtin=True)


def _write_persona(directory: Path, *, persona_id: str, tools: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.md").write_text(
        "---\n"
        f"id: {persona_id}\n"
        f"name: {persona_id}\n"
        "requires_folder: true\n"
        "scheduling: false\n"
        "subagents: false\n"
        f"tools: [{tools}]\n"
        "default_permission_mode: interactive\n"
        "version: \"1\"\n"
        "---\n"
        "You are a custom harness for the eval matrix.\n",
        encoding="utf-8",
    )
    return directory


def test_builtin_specs_are_the_four_shipped_harnesses():
    specs = builtin_specs()
    assert set(specs) == {"minimal", "code", "creator", "code-mode"}
    assert specs["minimal"].capabilities == ("code_files", "shell")
    assert specs["creator"].capabilities == ("code_files", "search", "shell", "todo")
    assert specs["code-mode"].capabilities == ("code_mode",)
    # code 没有 manifest：口径跟随代码内建的定义（含当前 catalog 里的 egress 槽）。
    assert specs["code"].capabilities == tuple(CODE_CAPABILITIES)


def test_harness_prompts_come_from_the_single_source():
    specs = builtin_specs()
    assert specs["minimal"].system_prompt == _builtin_manifest("minimal").system_prompt
    assert specs["creator"].system_prompt == _builtin_manifest("creator").system_prompt
    assert specs["code-mode"].system_prompt == _builtin_manifest("code-mode").system_prompt
    assert specs["code"].system_prompt == CODE_INSTRUCTIONS
    # manifest 正文（不再是一份手写常量）里独有的规则必须出现在派生结果里。
    assert "If a command fails twice" in specs["minimal"].system_prompt


def test_harness_mode_and_folder_gate_follow_the_manifest():
    specs = builtin_specs()
    for name in ("minimal", "creator", "code-mode"):
        assert specs[name].mode == _builtin_manifest(name).default_permission_mode
        assert specs[name].requires_folder == _builtin_manifest(name).requires_folder
    assert specs["code"].source == "builtin"
    assert specs["minimal"].source == "manifest"


def test_installed_persona_becomes_a_harness(tmp_path):
    reg = PersonaRegistry(state_path=tmp_path / "personas.json")
    reg.install_from_dir(
        _write_persona(tmp_path / "bundle", persona_id="eval-custom", tools="code_files, shell")
    )
    spec = installed_specs(reg)["eval-custom"]
    assert spec.capabilities == ("code_files", "shell")
    assert spec.mode == "interactive"
    assert spec.source == "manifest"
    # 内置档仍在，不被用户状态覆盖。
    assert {"minimal", "code", "creator", "code-mode"} <= set(installed_specs(reg))


def test_resolve_rejects_unknown_harness():
    with pytest.raises(ValueError, match="unknown harness: nope"):
        resolve("nope", specs=builtin_specs())


@pytest.mark.asyncio
async def test_run_matrix_accepts_an_injected_custom_harness(tmp_path):
    spec = HarnessSpec(
        name="custom",
        capabilities=("code_files", "shell"),
        system_prompt="You are a custom harness.",
        source="manifest",
    )
    report = await run_matrix(
        turns=[RecordedTurn(turn=1, input_text="hello")],
        models=["stub:model"],
        harness_names=["custom"],
        workspace=tmp_path,
        provider_factory=matrix._stub_provider_factory(),
        work_dir=tmp_path / "work",
        harness_specs={"custom": spec},
    )
    assert report["harnesses"] == ["custom"]
    assert report["harness_sources"] == {"custom": "manifest"}
    (cell,) = report["cells"]
    assert cell["status"] == "ok"
    (turn,) = cell["turns"]
    assert "read_file" in turn["tools"]  # code_files 槽展开
    assert "grep" not in turn["tools"]  # 没有 search 槽
