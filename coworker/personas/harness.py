"""Harness registry — harness（能力槽集合 + 系统提示 + 权限模式）的单一来源。

运行时 preset 与评测 harness 都从这里派生：manifest 型 persona 直接用它的
``tools`` / ``system_prompt`` / ``default_permission_mode``；``code`` 没有 manifest，
取代码内建的 ``CODE_CAPABILITIES`` / ``CODE_INSTRUCTIONS``。这样评测口径就是运行时口径，
用户装上的 persona 也能直接当 harness 跑评测。

不涉及执行语义：能力槽仍由 catalog 展开，权限仍由引擎审批门裁决。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 仅类型提示
    from .manifest import PersonaManifest

BUILTIN_HARNESS_IDS = ("minimal", "code", "creator", "code-mode")
_MANIFEST_BUILTINS = ("minimal", "creator", "code-mode")


@dataclass(frozen=True)
class HarnessSpec:
    """一个 harness = 能力槽集合 + 系统提示 + 权限模式。"""

    name: str
    capabilities: tuple[str, ...]
    system_prompt: str
    mode: str = "interactive"
    requires_folder: bool = True
    # "manifest"：来自 persona manifest；"builtin"：代码内建（当前只有 code）。
    source: str = "manifest"


def from_manifest(manifest: "PersonaManifest") -> HarnessSpec:
    return HarnessSpec(
        name=manifest.id,
        capabilities=tuple(manifest.tools),
        system_prompt=manifest.system_prompt,
        mode=manifest.default_permission_mode,
        requires_folder=manifest.requires_folder,
        source="manifest",
    )


def _builtin_dir() -> Path:
    return Path(__file__).parent / "builtin"


def _code_spec() -> HarnessSpec:
    from ..agents.code import CODE_CAPABILITIES, CODE_INSTRUCTIONS

    return HarnessSpec(
        name="code",
        capabilities=tuple(CODE_CAPABILITIES),
        system_prompt=CODE_INSTRUCTIONS,
        source="builtin",
    )


def builtin_specs() -> dict[str, HarnessSpec]:
    """四个内置 harness（minimal / code / creator / code-mode）。"""
    from .manifest import load_manifest_file

    specs: dict[str, HarnessSpec] = {}
    for persona_id in _MANIFEST_BUILTINS:
        path = _builtin_dir() / persona_id / "manifest.md"
        specs[persona_id] = from_manifest(load_manifest_file(path, builtin=True))
    specs["code"] = _code_spec()
    return specs


def installed_specs(registry: Optional[Any] = None) -> dict[str, HarnessSpec]:
    """内置 harness + 所有 manifest 型 persona（含用户安装的）。

    注册表不可用（如状态目录损坏）时退回内置集合——评测不该被用户状态拖垮。
    """
    specs = builtin_specs()
    try:
        if registry is None:
            from .registry import PersonaRegistry

            registry = PersonaRegistry()
        for persona_id in registry.ids():
            entry = registry.get(persona_id)
            manifest = getattr(entry, "manifest", None)
            if manifest is not None:
                specs.setdefault(manifest.id, from_manifest(manifest))
    except Exception:  # noqa: BLE001 - 用户状态问题不能吞掉内置 harness
        pass
    return specs


def resolve(name: str, *, specs: Optional[dict[str, HarnessSpec]] = None) -> HarnessSpec:
    """按 id 解析 harness；未知时抛 ValueError。"""
    table = specs if specs is not None else installed_specs()
    try:
        return table[name]
    except KeyError:
        raise ValueError(f"unknown harness: {name}") from None
