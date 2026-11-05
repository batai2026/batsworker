"""Unified install / update / uninstall facade over the four native stores.

Responsibilities:
  1. Adapt the native object into a PluginManifest.
  2. Verify bundle signature (signing.verify_directory) when a source dir is given.
  3. On update, diff the capability set against the existing manifest; a non-empty
     ``grew`` set flips ``requires_consent`` to True (same as persona's update rule).
  4. Optionally delegate the actual mutation to the native store (so existing behavior
     is preserved; skill/mcp keep their instant-install semantics).

The native stores are the source of truth for mutation — lifecycle never rewrites them.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from ..connectors import marketplace
from ..mcp.config import delete_global_server, put_global_server
from ..personas.signing import verify_directory
from .adapters import (
    ConnectorPluginAdapter,
    MCPAdapter,
    PersonaAdapter,
    SkillAdapter,
)
from .consent import capability_diff, consent_summary
from .manifest import (
    KIND_CONNECTOR,
    KIND_MCP,
    KIND_PERSONA,
    KIND_SKILL,
    PLUGIN_KINDS,
    PluginManifest,
)

_ADAPTERS = {
    KIND_PERSONA: PersonaAdapter(),
    KIND_SKILL: SkillAdapter(),
    KIND_CONNECTOR: ConnectorPluginAdapter(),
    KIND_MCP: MCPAdapter(),
}


def _adapter(kind: str):
    if kind not in _ADAPTERS:
        raise ValueError(f"unknown plugin kind: {kind!r} (expected one of {PLUGIN_KINDS})")
    return _ADAPTERS[kind]


def _verify(source_dir: Optional[str | Path], expected_pubkey: Optional[str]) -> dict:
    """校验 bundle 签名；无目录则返回空 dict。"""
    if not source_dir:
        return {}
    return verify_directory(source_dir, public_key=expected_pubkey)


def _to_manifest(kind: str, native: Any, source_dir: Optional[str | Path], expected_pubkey: Optional[str]) -> PluginManifest:
    pm = _adapter(kind).to_manifest(native)
    sig = _verify(source_dir, expected_pubkey)
    if sig:
        pm = replace(pm, signature=sig)
    return pm


def install(
    kind: str,
    native: Any,
    *,
    source_dir: Optional[str | Path] = None,
    expected_pubkey: Optional[str] = None,
    commit: bool = False,
    commit_payload: Any = None,
    store: Any = None,
) -> dict:
    """统一安装入口。

    - ``native`` 是 adapter 的输入（已解析的原生对象：PersonaManifest / Skill /
      plugin dict / MCPServerDef）。
    - ``commit_payload`` 是原生 store 的入参；为 None 时回退到 native。当 adapter
      输入与 store 入参不同形时（如 persona：adapter 要 PersonaManifest，store 要
      目录路径），显式传 commit_payload。
    - 首次安装 requires_consent 恒为 True（与 persona 的 disabled-until-consent 对齐）。
    - ``commit=True`` 时委托原生 store 落地；否则仅返回 consent 预览（dry-run）。
    """
    pm = _to_manifest(kind, native, source_dir, expected_pubkey)
    if commit:
        _commit_install(kind, commit_payload if commit_payload is not None else native, store)
    return {
        **consent_summary(pm),
        "requires_consent": True,
        "manifest": pm,
    }


def update(
    kind: str,
    native: Any,
    *,
    existing_native: Optional[Any] = None,
    source_dir: Optional[str | Path] = None,
    expected_pubkey: Optional[str] = None,
    commit: bool = False,
    commit_payload: Any = None,
    store: Any = None,
) -> dict:
    """统一更新入口。

    - 若提供 existing_native，则做 capability_set diff；grew 非空 → requires_consent=True。
    - 无 existing_native（无法比对）→ 保守 requires_consent=True。
    - commit_payload 语义同 install。
    """
    pm = _to_manifest(kind, native, source_dir, expected_pubkey)
    diff: Optional[dict] = None
    requires_consent = True
    if existing_native is not None:
        old_pm = _adapter(kind).to_manifest(existing_native)
        diff = capability_diff(old_pm, pm)
        requires_consent = bool(diff["grew"])
    if commit:
        _commit_install(kind, commit_payload if commit_payload is not None else native, store)
    return {
        **consent_summary(pm),
        "requires_consent": requires_consent,
        "diff": diff,
        "manifest": pm,
    }


def uninstall(kind: str, plugin_id: str, *, store: Any = None) -> dict:
    """统一卸载入口，委托给原生 store。"""
    if kind == KIND_PERSONA:
        if store is None:
            raise ValueError("persona uninstall requires a PersonaRegistry instance")
        store.uninstall(plugin_id)
        return {"ok": True, "kind": kind, "id": plugin_id}
    if kind == KIND_SKILL:
        if store is None:
            raise ValueError("skill uninstall requires a SkillStore instance")
        store.delete(plugin_id)
        return {"ok": True, "kind": kind, "id": plugin_id}
    if kind == KIND_CONNECTOR:
        marketplace.uninstall_plugin(plugin_id)
        return {"ok": True, "kind": kind, "id": plugin_id}
    if kind == KIND_MCP:
        delete_global_server(plugin_id)
        return {"ok": True, "kind": kind, "id": plugin_id}
    raise ValueError(f"unknown plugin kind: {kind!r}")


def _commit_install(kind: str, native: Any, store: Any) -> None:
    """委托原生 store 落地（不做 consent 判断——由调用方根据 requires_consent 决定）。"""
    if kind == KIND_PERSONA:
        if store is None:
            raise ValueError("persona install requires a PersonaRegistry instance")
        # native 是一个目录路径（PersonaRegistry.install_from_dir 的入参）。
        store.install_from_dir(native)
    elif kind == KIND_SKILL:
        if store is None:
            raise ValueError("skill install requires a SkillStore instance")
        # native 是 create 的 kwargs dict。
        store.create(**native)
    elif kind == KIND_CONNECTOR:
        marketplace.install_plugin(native)  # native 是 plugin name
    elif kind == KIND_MCP:
        # native 是 (name, config) 元组。
        name, config = native
        put_global_server(name, config)
