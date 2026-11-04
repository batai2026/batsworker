"""Demo → skill: turn a recorded/pasted demonstration into a SKILL.md skill.

Two supported sources feed the same deterministic generator (no LLM call):

- **Pasted steps** — the user types the procedure as plain lines, one step
  per line (or a short markdown list). Each line becomes an ordered step in
  the generated SKILL.md body.
- **Browser recording** — a saved browser replay (``browser-replays/*.json``,
  produced by the ``browser_record_*`` tools) is converted into human-readable
  ordered steps describing the exact same actions.

The generated skill goes through the SAME staged install path as an uploaded
archive (``SkillStore.stage_upload``/``confirm_upload``): nothing is installed
until the user reviews the full instructions on a preview card and confirms.
The staged SKILL.md carries ``source: demo`` so the Settings list can tell
demo-generated skills apart from uploaded/local ones.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Optional

from .store import SkillStore, validate_name

# Browser replay action → human-readable step template. Placeholders are filled
# from the recording's ``args``; unknown tools degrade to a safe generic line.
_RECORDING_STEP_TEMPLATES: dict[str, str] = {
    "browser_open_url": "Open the page {url} in the browser.",
    "browser_click": "Click the element: {target}.",
    "browser_type": "Type “{text}” into the element {target}.",
    "browser_select": "Select the option “{value}” in {target}.",
    "browser_upload_file": "Upload the file {path} through {target}.",
    "browser_wait": "Wait {milliseconds} milliseconds.",
}


def _fmt(value: Any) -> str:
    return "" if value is None else str(value)


def steps_from_recording(actions: list[dict[str, Any]]) -> list[str]:
    """Convert a saved browser replay's action list into ordered, readable steps.

    Only the recorded action tools appear (reads are skipped by the recorder);
    each action maps through ``_RECORDING_STEP_TEMPLATES`` with its args. A
    missing/unknown action degrades to a neutral line rather than dropping the
    step — the generated skill must never silently lose a step.
    """
    out: list[str] = []
    for i, action in enumerate(actions or []):
        tool = str(action.get("tool", ""))
        args = action.get("args") or {}
        template = _RECORDING_STEP_TEMPLATES.get(tool)
        if template is None:
            out.append(f"Repeat the recorded action: {tool} with {json.dumps(args, ensure_ascii=False)}.")
            continue
        try:
            line = template.format(
                **{k: _fmt(v) for k, v in args.items()}
            ).strip()
        except (KeyError, AttributeError):
            line = f"Repeat the recorded action: {tool}."
        out.append(line or f"Repeat the recorded action: {tool}.")
    return out


def steps_from_text(text: str) -> list[str]:
    """Split pasted demonstration text into ordered steps.

    Accepts one-step-per-line plain text or a light markdown list (``1.``,
    ``-``, ``*``). Blank lines are dropped; each surviving line is trimmed so a
    step reads cleanly inside the generated SKILL.md.
    """
    steps: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        # Strip a leading markdown list marker ("1.", "-", "*") — the body
        # renumbers anyway, so keeping the marker would read as "1. 1. ...".
        line = line.lstrip("0123456789").lstrip(".").strip()
        line = line.lstrip("-*").strip()
        if line:
            steps.append(line)
    return steps


def build_skill_markdown(
    name: str, description: str, steps: list[str]
) -> str:
    """Generate the SKILL.md document for a demo-generated skill.

    YAML frontmatter (name / description / ``source: demo``) plus a markdown
    body: a short context line, the ordered steps, and guardrails that keep the
    skill honest (stop on failure, no guessing, no skipped steps). The body is
    what the worker follows after ``load_skill``.
    """
    clean_steps = [s for s in (steps or []) if s.strip()]
    body = [f"# {name}", "", description.strip(), "", "## Steps", ""]
    for i, step in enumerate(clean_steps, 1):
        body.append(f"{i}. {step}")
    body += [
        "",
        "## Guidelines",
        "",
        "- Follow the steps in order. Do not skip or reorder them.",
        "- If a step fails or the result looks wrong, stop and report what happened.",
        "- Do not invent extra steps or guess at details the demonstration did not show.",
        "",
    ]
    frontmatter = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "source: demo",
        "---",
        "",
    ]
    return "\n".join(frontmatter + body)


def _write_staged_skill(
    store: SkillStore, *, name: str, description: str, instructions: str
) -> dict[str, Any]:
    """Write a generated SKILL.md into the staging dir and return the preview.

    Mirrors ``SkillStore.stage_upload``'s preview shape (token/name/description/
    instructions/files) so the existing upload-confirm endpoint and the GUI's
    review card work unchanged. The staged folder is a plain ``SKILL.md`` file,
    which ``SkillStore.confirm_upload`` already knows how to install.
    """
    staged_dir = store._staging_dir
    staged_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staged = staged_dir / token
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "SKILL.md").write_text(instructions, encoding="utf-8")
    return {
        "token": token,
        "name": name,
        "description": description,
        "instructions": instructions,
        "files": [],
    }


def stage_demo_skill(
    store: SkillStore,
    *,
    name: str,
    description: str,
    steps: list[str],
    recording: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Validate demo input and stage the generated skill for review.

    ``steps`` are used as-is when provided; otherwise a ``recording`` dict
    (``{name, actions}``) is converted with :func:`steps_from_recording`. Returns
    the same preview shape as ``stage_upload`` — the caller turns it into the
    API's ``{"ok": True, ...}`` response.
    """
    name = validate_name(name)
    description = (description or "").strip()
    if not description:
        raise ValueError("A one-line description is required.")
    if steps:
        clean_steps = [s for s in (steps or []) if s and str(s).strip()]
    elif recording:
        clean_steps = steps_from_recording(recording.get("actions") or [])
    else:
        clean_steps = []
    if not clean_steps:
        raise ValueError(
            "No demonstration steps were provided — paste steps or choose a "
            "saved browser recording."
        )
    markdown = build_skill_markdown(name, description, clean_steps)
    return _write_staged_skill(
        store, name=name, description=description, instructions=markdown
    )


def load_recording_actions(recordings_dir: Path, name: str) -> Optional[dict[str, Any]]:
    """Load a saved browser replay by name from the state dir's browser-replays/."""
    d = Path(recordings_dir)
    if not d.is_dir():
        return None
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (name or ""))
    path = d / f"{safe}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None