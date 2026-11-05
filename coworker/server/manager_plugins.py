"""Creator 面（建议路线 P1-4）：能力槽只读视图 + 插件 dry-run 预览。

两个能力都只读：slots 汇总平台能力槽与已安装的四类扩展；preview 复用 P1-3 的
PluginManifest 适配层做 consent 预览，绝不落盘。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("coworker.manager")


def _fail(status: int, error: str) -> dict[str, Any]:
    return {"ok": False, "status": status, "error": error}


class PluginsMixin:
    # -- 能力槽 -------------------------------------------------------------------
    def plugin_slots(self) -> dict[str, Any]:
        """catalog 能力槽 + 四类已安装扩展的统一清单（只读）。"""
        from ..catalog import CATALOG
        from ..plugins import consent_summary
        from ..plugins.adapters import (
            ConnectorPluginAdapter,
            MCPAdapter,
            PersonaAdapter,
            SkillAdapter,
        )
        from ..plugins.manifest import PluginManifest

        capabilities = [
            {
                "id": cap.id,
                "name": cap.name,
                "description": cap.description,
                "requires": list(cap.requires),
                "risk": [r.value for r in cap.risk],
            }
            for cap in CATALOG.values()
        ]
        plugins: dict[str, list[dict[str, Any]]] = {
            "persona": [],
            "skill": [],
            "connector": [],
            "mcp": [],
        }

        # personas: manifest-backed 走 PersonaAdapter；代码内建的（cowork/code）用
        # entry 字段合成同形投影，保证清单完整。
        try:
            for row in self.personas.list_all():  # type: ignore[attr-defined]
                entry = self.personas.get(row["id"])  # type: ignore[attr-defined]
                manifest = entry.manifest if entry else None
                if manifest is not None:
                    pm = PersonaAdapter().to_manifest(manifest)
                else:
                    pm = PluginManifest(
                        kind="persona",
                        id=row["id"],
                        name=row["name"],
                        version=str(row.get("version") or ""),
                        capability_set=frozenset(
                            f"tool:{t}" for t in row.get("tools") or ()
                        ),
                        permissions={
                            "requires_folder": bool(row.get("requires_folder")),
                            "enabled": bool(row.get("enabled", True)),
                        },
                        description=str(row.get("tagline") or ""),
                    )
                plugins["persona"].append(consent_summary(pm))
        except Exception as exc:  # 一类数据源坏掉不能拖垮整个清单
            logger.warning("plugin_slots personas failed: %s", exc)
            plugins["persona"].append({"error": str(exc)})

        try:
            for row in self.skill_store.rows(None):  # type: ignore[attr-defined]
                pm = SkillAdapter().from_row(row)
                plugins["skill"].append(consent_summary(pm))
        except Exception as exc:
            logger.warning("plugin_slots skills failed: %s", exc)
            plugins["skill"].append({"error": str(exc)})

        try:
            for connector in self.list_connectors():  # type: ignore[attr-defined]
                pm = ConnectorPluginAdapter().to_manifest(connector)
                plugins["connector"].append(consent_summary(pm))
        except Exception as exc:
            logger.warning("plugin_slots connectors failed: %s", exc)
            plugins["connector"].append({"error": str(exc)})

        try:
            from ..mcp.config import load_mcp_servers

            for server in load_mcp_servers(secrets=self.secrets):  # type: ignore[attr-defined]
                pm = MCPAdapter().to_manifest(server)
                plugins["mcp"].append(consent_summary(pm))
        except Exception as exc:
            logger.warning("plugin_slots mcp failed: %s", exc)
            plugins["mcp"].append({"error": str(exc)})

        return {"ok": True, "capabilities": capabilities, "plugins": plugins}

    # -- dry-run 预览 --------------------------------------------------------------
    def preview_plugin(
        self,
        kind: str,
        *,
        source: Optional[str] = None,
        name: Optional[str] = None,
    ) -> dict[str, Any]:
        """按 kind 解析原生对象并返回统一 consent 摘要（不安装）。"""
        from ..plugins import install as preview_install
        from ..plugins.manifest import PLUGIN_KINDS

        kind = (kind or "").strip().lower()
        if kind not in PLUGIN_KINDS:
            return _fail(400, f"unknown plugin kind: {kind!r}")

        try:
            if kind == "persona":
                if not source:
                    return _fail(400, "source directory is required for persona preview")
                from ..personas.manifest import load_manifest_file

                manifest_path = Path(source).expanduser() / "manifest.md"
                if not manifest_path.is_file():
                    return _fail(404, f"manifest.md not found in {source}")
                native = load_manifest_file(manifest_path, builtin=False)
            elif kind == "skill":
                if not source:
                    return _fail(400, "source directory is required for skill preview")
                from ..skills.base import _parse_skill

                skill_path = Path(source).expanduser() / "SKILL.md"
                if not skill_path.is_file():
                    return _fail(404, f"SKILL.md not found in {source}")
                native = _parse_skill(skill_path)
            elif kind == "connector":
                if not name:
                    return _fail(400, "name is required for connector preview")
                from ..connectors.marketplace import marketplace_catalog

                match = [
                    p
                    for p in marketplace_catalog().get("plugins", [])
                    if p.get("name") == name
                ]
                if not match:
                    return _fail(404, f"connector plugin not found: {name}")
                native = match[0]
            else:  # mcp
                if not name:
                    return _fail(400, "name is required for mcp preview")
                from ..mcp.config import load_mcp_servers

                match = [
                    s for s in load_mcp_servers(secrets=self.secrets) if s.name == name
                ]
                if not match:
                    return _fail(404, f"mcp server not found: {name}")
                native = match[0]

            # 走 P1-3 的统一 install(commit=False)：只返回 consent 预览，绝不落盘。
            result = preview_install(
                kind, native, source_dir=source if source else None, commit=False
            )
        except Exception as exc:
            # 恶意/损坏的 bundle 解析失败必须是 400，不能冒成 500。
            return _fail(400, f"preview failed: {type(exc).__name__}: {exc}")

        pm = result.pop("manifest")
        return {
            "ok": True,
            **result,
            "manifest": {
                "kind": pm.kind,
                "id": pm.id,
                "name": pm.name,
                "version": pm.version,
                "capability_set": sorted(pm.capability_set),
            },
        }
