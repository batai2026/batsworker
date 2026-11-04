"""Skills cluster split out of manager.py (manager-split refactor).

Mixin methods only; shared state lives on the SessionManager facade (self.*). Module-level
imports are limited to what the moved code references; everything else resolves via self.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..secrets import state_dir
from ..skills import SkillLoader, effective_skills


class SkillsMixin:
    # -- skills (SKILLS-SPEC §4.4) ------------------------------------------------
    def list_skills(self, workspace: Optional[str] = None) -> list[dict[str, Any]]:
        """Enriched rows for the Settings screen (scope/source/enabled). Optional workspace
        adds that project's skills, with project copies shadowing same-named global ones."""
        return self.skill_store.rows(workspace or None)

    def reveal_skill(
        self, name: str, workspace: Optional[str] = None
    ) -> dict[str, Any]:
        """Open the skill's folder in the OS file manager (§6 "Show folder" — the power-user
        window into folder-is-truth). Same local-machine rationale as reveal_artifact."""
        import subprocess
        import sys

        try:
            folder, _scope = self.skill_store.find(name, workspace or None)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            if sys.platform == "darwin":
                subprocess.Popen(
                    ["open", str(folder)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "win32":
                import os

                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(
                    ["xdg-open", str(folder)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def persona_mcp_scope(self, persona_id: str) -> Optional[set[str]]:
        """The persona's declared MCP-server scope (OPE-58 sibling stub): the manifest's
        `mcp:` names, or None when it declares none (= no scoping). Only ever narrows —
        the user's enabled/configured/authed gates apply regardless."""
        entry = self.personas.get(persona_id)
        names = list((entry.manifest.mcp if entry and entry.manifest else []) or [])
        return {n for n in names if n} or None

    def persona_skill_scope(
        self, persona_id: str
    ) -> tuple[Optional[Path], Optional[set[str]]]:
        """The persona's own skill folder + optional allowlist (OPE-58).

        A manifest-backed persona carries skills as a `skills/` dir next to its manifest —
        the sharing bundle shape (manifest + skill folders). The manifest's `skills:` list,
        when non-empty, narrows which of those activate. Additive on top of global/project
        scopes: the persona SHIPS skills; it never hides the user's own."""
        entry = self.personas.get(persona_id)
        manifest = entry.manifest if entry else None
        if manifest is None or not manifest.source:
            return None, None
        d = Path(manifest.source).parent / "skills"
        if not d.is_dir():
            return None, None
        allow = {s for s in manifest.skills if s} or None
        return d, allow

    def effective_skill_names(
        self,
        session_id: str,
        workspace: Optional[str | Path] = None,
        agent: Optional[str] = None,
    ) -> set[str]:
        """The session's skill menu (§3): merged scopes − Settings disables − session mutes.
        The single resolver behind the engine catalog, the rail list, and the composer popup.
        Persona-carried skills (OPE-58) join the merge for the session's persona — user
        disables and mutes still win over them."""
        dirs = [self.skill_store.global_dir]
        if workspace:
            dirs.append(self.skill_store.project_dir(workspace))
        loader = SkillLoader(dirs)
        names = set(loader.names())
        persona_dir, allow = self.persona_skill_scope(self._persona_of(session_id, agent))
        if persona_dir is not None:
            persona_names = set(SkillLoader([persona_dir]).names())
            if allow is not None:
                persona_names &= allow
            names |= persona_names
        return effective_skills(
            names=names,
            disabled=self.skill_store.disabled_names(),
            session_overrides=self.session_skills.get(session_id),
        )

    def session_skills_view(
        self, session_id: str, workspace: Optional[str] = None
    ) -> dict[str, Any]:
        """The rail payload: every in-scope, Settings-enabled skill with its mute state.
        Persona-carried skills (OPE-58) appear with scope "coworker" — mutable per session
        like any other, but owned by the persona bundle, not the Settings store."""
        disabled = self.skill_store.disabled_names()
        overrides = self.session_skills.get(session_id)
        rows = [
            {
                "name": r["name"],
                "description": r["description"],
                "scope": r["scope"],
                "enabled": overrides.get(r["name"], True),
            }
            for r in self.skill_store.rows(workspace or None)
            if r["name"] not in disabled
        ]
        seen = {r["name"] for r in rows}
        persona_dir, allow = self.persona_skill_scope(self._persona_of(session_id))
        if persona_dir is not None:
            for entry in SkillLoader([persona_dir]).catalog():
                name = entry["name"]
                if name in seen or name in disabled:
                    continue  # a global/project copy shadows the bundle's
                if allow is not None and name not in allow:
                    continue
                rows.append(
                    {
                        "name": name,
                        "description": entry["description"],
                        "scope": "coworker",
                        "enabled": overrides.get(name, True),
                    }
                )
        return {"skills": rows}

    def create_skill(self, body: dict[str, Any]) -> dict[str, Any]:
        blocked = self._scratch_workspace_error(body.get("workspace"))
        if blocked:
            return blocked
        try:
            created = self.skill_store.create(
                name=str(body.get("name", "")),
                description=str(body.get("description", "")),
                instructions=str(body.get("instructions", "")),
                scope=str(body.get("scope", "global") or "global"),
                workspace=body.get("workspace") or None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "skill": created}

    def update_skill(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            if "enabled" in body:
                self.skill_store.set_enabled(name, bool(body["enabled"]))
            if body.get("description") is not None or body.get("instructions") is not None:
                self.skill_store.update(
                    name,
                    description=body.get("description"),
                    instructions=body.get("instructions"),
                    workspace=body.get("workspace") or None,
                )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def delete_skill(self, name: str, workspace: Optional[str] = None) -> dict[str, Any]:
        try:
            self.skill_store.delete(name, workspace or None)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def move_skill(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        # Moving INTO project scope must not target a scratch dir (moving OUT is fine —
        # that's the rescue path for already-stranded skills).
        if str(body.get("scope", "")) == "project":
            blocked = self._scratch_workspace_error(body.get("workspace"))
            if blocked:
                return blocked
        try:
            moved = self.skill_store.move(
                name,
                to_scope=str(body.get("scope", "")),
                workspace=body.get("workspace") or None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "skill": moved}

    def stage_skill_upload(self, data: bytes, filename: str = "") -> dict[str, Any]:
        try:
            preview = self.skill_store.stage_upload(data, filename)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **preview}

    def stage_demo_skill(self, body: dict[str, Any]) -> dict[str, Any]:
        """Stage a skill generated from a pasted/recorded demonstration.

        Accepts ``name``, ``description``, and either ``steps`` (list of strings,
        one per step) or ``recording`` (name of a saved browser replay in the
        state dir). Generates a SKILL.md from the steps deterministically and
        stages it through the SAME preview→confirm path as uploaded skills —
        nothing is installed until the user reviews and confirms.
        """
        from ..skills.demo import load_recording_actions, stage_demo_skill as _stage

        name = str(body.get("name", ""))
        description = str(body.get("description", ""))
        steps = body.get("steps") or []
        if isinstance(steps, str):
            from ..skills.demo import steps_from_text

            steps = steps_from_text(steps)
        elif not isinstance(steps, list):
            steps = []
        steps = [str(s) for s in steps]

        recording = None
        rec_name = str(body.get("recording", ""))
        if rec_name:
            recording = load_recording_actions(state_dir() / "browser-replays", rec_name)
            if recording is None:
                return {
                    "ok": False,
                    "error": f"No saved browser recording named '{rec_name}'.",
                }
        try:
            preview = _stage(
                self.skill_store,
                name=name,
                description=description,
                steps=steps,
                recording=recording,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **preview}

    def confirm_skill_upload(self, body: dict[str, Any]) -> dict[str, Any]:
        blocked = self._scratch_workspace_error(body.get("workspace"))
        if blocked:
            return blocked
        try:
            saved = self.skill_store.confirm_upload(
                str(body.get("token", "")),
                scope=str(body.get("scope", "global") or "global"),
                workspace=body.get("workspace") or None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "skill": saved}
