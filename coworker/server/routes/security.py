"""Security posture — threat model, findings, egress allowlist, sandbox backend.

Five related surfaces that all answer "what is this machine allowed to do":

* `/v1/security/*` — the editable threat model and the finding closure loop.
* `/v1/egress/*` — the persistent network allowlist the `request_egress` tool consults.
* `/v1/settings/sandbox` — the GUI sandbox toggle.
* `/v1/settings/openshell/*` — backend availability for the settings pane.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..manager import SessionManager


def build_security_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    # -- security workflow (editable threat model + finding closure loop) ---------
    @router.get("/v1/security/threat-model")
    def threat_model_get() -> dict[str, Any]:
        return manager.get_threat_model()

    @router.put("/v1/security/threat-model")
    def threat_model_put(body: dict) -> dict[str, Any]:
        return manager.save_threat_model(str((body or {}).get("content", "")))

    @router.post("/v1/security/threat-model/reveal")
    def threat_model_reveal() -> dict[str, Any]:
        return manager.reveal_threat_model()

    @router.get("/v1/security/findings")
    def security_findings_list(status: str = "") -> dict[str, Any]:
        return manager.list_security_findings(status)

    @router.post("/v1/security/findings")
    def security_findings_add(body: dict) -> dict[str, Any]:
        return manager.add_security_finding(body or {})

    @router.patch("/v1/security/findings/{finding_id}")
    def security_finding_patch(finding_id: str, body: dict) -> dict[str, Any]:
        return manager.update_security_finding(finding_id, body or {})

    @router.delete("/v1/security/findings/{finding_id}")
    def security_finding_delete(finding_id: str) -> dict[str, Any]:
        return manager.delete_security_finding(finding_id)

    # -- egress policy (P0: persistent network allowlist + request_egress tool) --
    @router.get("/v1/egress/policy")
    def egress_policy_get() -> dict[str, Any]:
        from ...egress import default_policy

        return {"ok": True, "domains": default_policy().list_domains()}

    @router.post("/v1/egress/policy")
    def egress_policy_add(body: dict) -> dict[str, Any]:
        from ...egress import default_policy

        domain = str((body or {}).get("domain", ""))
        reason = str((body or {}).get("reason", ""))
        if not default_policy().add_domain(domain, reason):
            return {"ok": False, "error": "invalid domain"}
        return {"ok": True, "domain": domain}

    @router.delete("/v1/egress/policy/{domain}")
    def egress_policy_delete(domain: str) -> dict[str, Any]:
        from ...egress import default_policy

        return {"ok": default_policy().remove_domain(domain), "domain": domain}

    # -- GUI sandbox toggle ---------------------------------------------------------
    @router.get("/v1/settings/sandbox")
    def sandbox_settings_get() -> dict[str, Any]:
        return manager.get_sandbox_settings()

    @router.put("/v1/settings/sandbox")
    def sandbox_settings_put(body: dict) -> dict[str, Any]:
        return manager.set_sandbox_settings(body or {})

    # -- OpenShell status (P0: desktop/settings check for the openshell backend) --
    @router.get("/v1/settings/openshell/status")
    def openshell_status_get() -> dict[str, Any]:
        from ...config import load_config
        from ...tools.openshell import _parse_prefix, openshell_available

        cfg = load_config()
        prefix = _parse_prefix(cfg.sandbox_openshell_prefix)
        return {
            "ok": True,
            "available": openshell_available(),
            "prefix": prefix,
            "backend": cfg.sandbox_backend,
        }

    return router
