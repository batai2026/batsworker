"""Personas — the `/v1/personas` surface plus the cloud gallery that feeds it.

Install (dir / git / bundle zip / gallery slug, each behind the same consent summary and
the same parser), the signed-distribution catalog and its trusted-key store, persona
CRUD/enable, bundle media, and persona-level connector defaults.

The gallery routes live here rather than with the cloud account routes because they are
persona *content*: the manifest goes through the identical parse → verify → consent path
as a local install, and nothing lands enabled without the user's approval.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from ..manager import SessionManager


def build_personas_router(manager: SessionManager) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/personas")
    def personas() -> dict[str, Any]:
        from ...personas.registry import include_unshipped

        # `internal` tells the GUI it may show internal-build affordances (the
        # "Not in this release" group, the Gallery entry point).
        return {"personas": manager.personas.list_all(), "internal": include_unshipped()}

    @router.post("/v1/personas/install")
    def install_persona(body: dict) -> dict[str, Any]:
        # Returns a consent summary per persona; they land disabled pending the user's approval
        # (then POST /v1/personas/{id} {enabled:true, surfaced:true}). A catalog-pinned
        # install passes expected_pubkey (fail closed); every summary's signature fact is
        # decorated with the user's trusted-keys state.
        reg = manager.personas
        pin = body.get("expected_pubkey")
        try:
            if body.get("git_url"):
                summaries = reg.install_from_git(
                    str(body["git_url"]), expected_pubkey=pin
                )
            elif body.get("dir"):
                summaries = reg.install_from_dir(str(body["dir"]), expected_pubkey=pin)
            elif body.get("zip_b64"):
                # Sharing v1 (OPE-7): a bundle zip — the export format — round-trips
                # through the same dir installer + consent path.
                try:
                    data = base64.b64decode(str(body["zip_b64"]), validate=True)
                except (ValueError, binascii.Error):
                    return {"ok": False, "error": "Invalid archive encoding."}
                summaries = reg.install_from_zip(
                    data, str(body.get("filename", ""))
                )
            elif body.get("gallery_slug"):
                # Gallery install = fetch the manifest markdown from the cloud
                # (sign-in required), verify its hash, then reuse the exact
                # same parser + consent path as a local/Git install. The
                # gallery never changes the trust model: no executable code,
                # lands disabled pending consent.
                import hashlib
                import tempfile

                from ... import cloud
                from ...config import load_config

                slug = str(body["gallery_slug"]).strip()
                manifest = cloud.gallery_manifest(manager.secrets, load_config(), slug)
                if manifest is None:
                    return {
                        "ok": False,
                        "error": "gallery requires cloud sign-in (or the cloud is unreachable)",
                    }
                markdown = manifest.get("manifest_markdown", "")
                digest = "sha256:" + hashlib.sha256(markdown.encode()).hexdigest()
                if (
                    manifest.get("manifest_hash")
                    and manifest["manifest_hash"] != digest
                ):
                    return {"ok": False, "error": "manifest hash mismatch"}
                with tempfile.TemporaryDirectory() as td:
                    (Path(td) / f"{slug}.md").write_text(markdown)
                    summaries = reg.install_from_dir(td)
                cloud.gallery_install_event(manager.secrets, load_config(), slug)
            else:
                return {
                    "ok": False,
                    "error": "provide a `dir`, `git_url`, `zip_b64`, or `gallery_slug`",
                }
        except Exception as e:  # surface manifest/clone errors to the caller
            return {"ok": False, "error": str(e)}
        # Decorate each summary's signature fact with the user's trusted-key state
        # (a verifying signature from a key the user trusted ⇒ trusted: true).
        from ...personas.distribution import TrustedKeyStore

        store = TrustedKeyStore(manager.data_dir)
        for summary in summaries or []:
            summary["signature"] = store.decorate(summary.get("signature"))
        return {"ok": True, "consent": summaries, "personas": reg.list_all()}

    # -- signed persona distribution (coworker/personas/{signing,distribution}.py) --
    @router.get("/v1/personas/catalog")
    def persona_catalog(force: bool = False) -> dict[str, Any]:
        return manager.persona_catalog(force=force)

    @router.post("/v1/personas/catalog/install")
    def persona_catalog_install(body: dict) -> dict[str, Any]:
        # Pinned-key install: the registry refuses an unsigned/mismatched bundle
        # before consent is ever shown (fail closed, the catalog IS the pin).
        return manager.install_persona_from_catalog(str((body or {}).get("slug", "")))

    @router.get("/v1/personas/trusted-keys")
    def trusted_keys_list() -> dict[str, Any]:
        return {"keys": manager.list_trusted_keys()}

    @router.post("/v1/personas/trusted-keys")
    def trusted_keys_add(body: dict) -> dict[str, Any]:
        body = body or {}
        if not body.get("pubkey_path"):
            return {"ok": False, "error": "pubkey_path is required"}
        return manager.add_trusted_key(
            str(body["pubkey_path"]), str(body.get("label", ""))
        )

    @router.delete("/v1/personas/trusted-keys/{key_id}")
    def trusted_keys_remove(key_id: str) -> dict[str, Any]:
        return manager.remove_trusted_key(key_id)

    @router.post("/v1/personas/{persona_id}/export")
    def export_persona(persona_id: str, body: dict) -> dict[str, Any]:
        # Sharing v1 (OPE-7): zip the persona's bundle into the chosen folder. The zip
        # is the import format — send it to a teammate, they import it from the picker.
        return manager.personas.export_persona(
            persona_id, str((body or {}).get("dir", ""))
        )

    @router.get("/v1/cloud/gallery/{slug}")
    def cloud_gallery_detail(slug: str) -> dict[str, Any]:
        """Solo page for one gallery coworker: publisher pitch + capabilities
        derived locally from the manifest (same parser as install)."""
        from ... import cloud
        from ...config import load_config

        body = cloud.gallery_detail(manager.secrets, load_config(), slug)
        if body is None:
            return {"ok": False, "error": "gallery requires cloud sign-in"}
        return body

    @router.get("/v1/cloud/gallery")
    def cloud_gallery() -> dict[str, Any]:
        """Gallery cards for the GUI. Signed out ⇒ ok:false (the gallery is a
        signed-in feature by design; local personas are unaffected)."""
        from ... import cloud
        from ...config import load_config

        body = cloud.gallery_list(manager.secrets, load_config())
        if body is None:
            return {
                "ok": False,
                "error": "gallery requires cloud sign-in",
                "personas": [],
            }
        return {"ok": True, "personas": body.get("personas", [])}

    @router.post("/v1/personas/{persona_id}")
    def update_persona(persona_id: str, body: dict) -> dict[str, Any]:
        reg = manager.personas
        archived = 0
        try:
            if "enabled" in body:
                # Disable archives the persona's sessions atomically (server-side, one
                # request) so any client gets the same semantic. See set_persona_enabled.
                archived = manager.set_persona_enabled(
                    persona_id, bool(body["enabled"])
                )["archived_sessions"]
            if "surfaced" in body:
                reg.set_surfaced(persona_id, bool(body["surfaced"]))
            if body.get("default"):
                reg.set_default(persona_id)
        except KeyError:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        return {"ok": True, "personas": reg.list_all(), "archived_sessions": archived}

    @router.delete("/v1/personas/{persona_id}")
    def persona_delete(persona_id: str) -> dict[str, Any]:
        # Uninstall a non-builtin persona (snapshot dir + lifecycle state). Local
        # operation — works signed out, regardless of where the persona came from.
        try:
            manager.personas.uninstall(persona_id)
        except KeyError:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "personas": manager.personas.list_all()}

    @router.get("/v1/personas/{persona_id}")
    def persona_detail(persona_id: str) -> dict[str, Any]:
        # §5 detail page: identity + capabilities + recommends(+connected) + default connections.
        detail = manager.persona_detail(persona_id)
        if detail is None:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        return detail

    @router.get("/v1/personas/{persona_id}/media/{name}")
    def persona_media(persona_id: str, name: str) -> Any:
        # Screenshots from the persona bundle's media/ folder. The name is confined
        # to that folder: no separators, resolved path must stay inside it.
        from fastapi.responses import FileResponse, Response

        media_dir = manager.personas.media_dir(persona_id)
        if media_dir is None or "/" in name or "\\" in name or name.startswith("."):
            return Response(status_code=404)
        f = (media_dir / name).resolve()
        try:
            inside = f.is_relative_to(media_dir.resolve())
        except AttributeError:  # pragma: no cover — py<3.9 has no is_relative_to
            inside = str(f).startswith(str(media_dir.resolve()))
        if not inside or not f.is_file():
            return Response(status_code=404)
        return FileResponse(f)

    @router.post("/v1/personas/{persona_id}/enable")
    def persona_enable(persona_id: str, body: dict) -> dict[str, Any]:
        # Dedicated §5/§8 route; delegates to the same manager toggle as POST /v1/personas/{id}
        # (so disable archives the persona's sessions here too).
        try:
            manager.set_persona_enabled(
                persona_id, bool((body or {}).get("enabled", True))
            )
        except KeyError:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        return {"ok": True, "personas": manager.personas.list_all()}

    @router.post("/v1/personas/{persona_id}/connections")
    def persona_set_connection(persona_id: str, body: dict) -> dict[str, Any]:
        # §5: flip a persona-default connector on/off; re-reads so the client can refresh.
        body = body or {}
        connector = str(body.get("connector", "")).strip()
        if not connector:
            return {"ok": False, "error": "connector required"}
        return manager.set_persona_connection(
            persona_id, connector, bool(body.get("enabled", False))
        )

    return router
