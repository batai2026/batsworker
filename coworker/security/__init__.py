"""Security workflow store — editable threat model + finding tracking loop.

Two pieces back the Security Coworker's "editable threat model + validation
closure" story (Claude Security style):

- **Threat model** — a plain Markdown file (``<state_dir>/security/threat-model.md``)
  the user/team edits to tell the coworker what matters in THEIR codebase: what
  to flag, what to treat as noise, reachability rules, preferred mitigations.
  The agent reads it before triaging and can update it (approval-gated).

- **Finding tracker** — a small JSON store (``<state_dir>/security/findings.json``)
  that carries a finding from discovery → fix PR → closed, so the review loop is
  verifiable: log a finding, link its fix PR, mark it fixed when merged, close it.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ..secrets import state_dir

SEVERITIES = ("critical", "high", "medium", "low", "noise")
STATUSES = ("open", "fixing", "fixed", "closed")

DEFAULT_THREAT_MODEL = """# Threat Model

Edit this file to tell the Security Coworker what matters in YOUR codebase. It is
read before every triage, so the rules here decide which scanner findings are worth
flagging and which are noise.

## In scope / worth flagging
- Anything reachable from a public entry point (routes, message handlers, CLI args).
- Secrets or credentials in source, history, or config.
- Dependency upgrades that close a known-vulnerable reachable path.

## Out of scope / treat as noise
- Findings in test fixtures or generated code.
- Theoretical issues with no attacker-controlled input path.

## Reachability rules
- A finding is real only if the input is attacker-controlled or the path is
  reachable from a public entry point. Rate it critical/high/medium/low/noise
  and say why in one or two sentences.

## Preferred mitigations
- Match the codebase's existing validation/escaping patterns.
- Never disable a check to silence a warning; say so explicitly and get agreement.
"""


class SecurityStore:
    """Thread-safe, file-backed security workflow store."""

    def __init__(self, base: Optional[str | Path] = None) -> None:
        self._dir = Path(base) if base else state_dir() / "security"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._threat_model_path = self._dir / "threat-model.md"
        self._findings_path = self._dir / "findings.json"
        self._lock = threading.RLock()

    # -- threat model ---------------------------------------------------------

    @property
    def threat_model_path(self) -> Path:
        return self._threat_model_path

    def read_threat_model(self) -> str:
        try:
            return self._threat_model_path.read_text(encoding="utf-8")
        except OSError:
            return DEFAULT_THREAT_MODEL

    def write_threat_model(self, content: str) -> None:
        self._threat_model_path.write_text(content or "", encoding="utf-8")

    # -- findings -------------------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._findings_path.read_text(encoding="utf-8"))
            return data.get("findings", []) if isinstance(data, dict) else []
        except (OSError, ValueError):
            return []

    def _save(self, findings: list[dict[str, Any]]) -> None:
        self._findings_path.write_text(
            json.dumps({"findings": findings}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_findings(self, status: str = "") -> list[dict[str, Any]]:
        with self._lock:
            out = self._load()
            if status:
                out = [f for f in out if f.get("status") == status]
            # Newest first.
            return sorted(out, key=lambda f: f.get("created_at", ""), reverse=True)

    def get_finding(self, finding_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return next(
                (f for f in self._load() if f.get("id") == finding_id), None
            )

    def add_finding(
        self,
        *,
        title: str,
        severity: str,
        file: str = "",
        description: str = "",
        pr_url: str = "",
    ) -> dict[str, Any]:
        title = (title or "").strip()
        severity = (severity or "medium").strip().lower()
        if severity not in SEVERITIES:
            severity = "medium"
        if not title:
            raise ValueError("A finding title is required.")
        finding = {
            "id": "F-" + uuid.uuid4().hex[:8],
            "title": title,
            "severity": severity,
            "file": (file or "").strip(),
            "description": (description or "").strip(),
            "pr_url": (pr_url or "").strip(),
            "status": "open",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with self._lock:
            findings = self._load()
            findings.append(finding)
            self._save(findings)
        return finding

    def update_finding(
        self,
        finding_id: str,
        *,
        status: Optional[str] = None,
        pr_url: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            findings = self._load()
            for f in findings:
                if f.get("id") != finding_id:
                    continue
                if status:
                    st = status.strip().lower()
                    if st in STATUSES:
                        f["status"] = st
                if pr_url is not None:
                    f["pr_url"] = (pr_url or "").strip()
                f["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._save(findings)
                return f
        return None

    def delete_finding(self, finding_id: str) -> bool:
        with self._lock:
            findings = self._load()
            remaining = [f for f in findings if f.get("id") != finding_id]
            if len(remaining) == len(findings):
                return False
            self._save(remaining)
            return True