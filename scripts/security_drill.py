"""Security closure-loop drill (P2) — exercise the threat-model + finding loop.

Runs the REAL store and agent tools that the Security Coworker uses, against a
small intentionally-vulnerable repo, and writes a drill report to ``reports/``.
No model or network is required: this validates the loop mechanics (threat model
edit → finding log → fix PR link → fixed → closed) and produces the report shape
a real security-persona engagement should fill in.

Usage:
    .venv/bin/python scripts/security_drill.py [--out reports/security-drill.md]

To turn this into a REAL agent drill, open a session with the Security Coworker
on the created repo (``--keep`` keeps the repo on disk) and follow the report
checklist: let it scan, triage with the threat model, log findings via
``security_finding_log``, open a fix PR, then ``security_finding_update(fixed)``
once merged and ``closed`` to finish.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

# Ensure the repo root is importable when run as a script.
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from coworker.security import SecurityStore  # noqa: E402
from coworker.tools.security import security_tools  # noqa: E402

VULNERABLE_REPO = {
    "app.py": (
        "import sqlite3\n"
        "from flask import Flask, request\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "@app.route('/search')\n"
        "def search():\n"
        "    q = request.args.get('q', '')\n"
        "    # VULN: string-interpolated SQL — attacker-controlled input reaches the\n"
        "    # query builder directly.\n"
        "    conn = sqlite3.connect('app.db')\n"
        "    rows = conn.execute(f\"SELECT * FROM items WHERE name LIKE '%{q}%'\").fetchall()\n"
        "    return {'results': rows}\n"
    ),
    "requirements.txt": "flask==3.0.0\n",
}


def _write_repo(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name, content in VULNERABLE_REPO.items():
        (dest / name).write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "reports" / "security-drill.md"))
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the drill repo on disk (default: temp dir, removed on exit)",
    )
    args = parser.parse_args()

    tmp_base = ROOT / ".drill-tmp"
    tmp_base.mkdir(parents=True, exist_ok=True)
    # Plain mkdir (not tempfile.mkdtemp): on Windows mkdtemp creates an owner-only
    # DACL that breaks sandboxed/CI runs; a normal mkdir inherits the parent ACL.
    import uuid

    repo = tmp_base / ("ow-security-drill-" + uuid.uuid4().hex[:8])
    repo.mkdir(parents=True, exist_ok=True)
    _write_repo(repo)
    print(f"Drill repo: {repo}")
    print("Vulnerable file: app.py (SQL injection in /search)")

    store = SecurityStore(ROOT / ".drill-state" / "security")
    tools = {t.__name__: t for t in security_tools(store)}

    # 1. Threat model — the editable rules decide what to flag.
    tm = tools["threat_model_read"]()
    print("\n[1/6] threat model read (default template)")
    print("      " + tm["content"].splitlines()[0])

    # 2. Edit the threat model to reflect this repo's priorities.
    edited = (
        "# Drill Threat Model\n\n"
        "## In scope\n"
        "- Anything reachable from a Flask route.\n"
        "- Secrets or credentials in source or history.\n\n"
        "## Out of scope\n"
        "- Test fixtures and generated code.\n\n"
        "## Reachability rules\n"
        "- A finding is real only if the input is attacker-controlled.\n"
    )
    tools["threat_model_update"](content=edited)
    assert tools["threat_model_read"]()["content"] == edited
    print("[2/6] threat model updated (reachability rules set)")

    # 3. Log the triaged finding (what the agent does after scanning).
    logged = tools["security_finding_log"](
        title="SQL injection in /search",
        severity="high",
        file="app.py",
        description="User-controlled 'q' reaches the SQL query builder (reachable, attacker-controlled).",
    )
    fid = logged["finding"]["id"]
    print(f"[3/6] finding logged: {fid} (high)")

    # 4. Link the fix PR and mark fixed → closed (the validation loop).
    tools["security_finding_update"](finding_id=fid, status="fixed", pr_url="https://github.com/acme/repo/pull/42")
    tools["security_finding_update"](finding_id=fid, status="closed")
    final = tools["security_finding_list"]()["findings"][0]
    assert final["status"] == "closed" and final["pr_url"].endswith("/42")
    print("[4/6] finding carried fixed → closed with PR #42")

    # 5. Egress policy (P0): request_egress adds a blocked domain to the allowlist,
    #    the permission engine auto-runs it, and removal leaves a clean state.
    from coworker.egress import EgressPolicy
    from coworker.tools.egress import request_egress_tool

    policy = EgressPolicy(ROOT / ".drill-state" / "egress-policy.json")
    egress_tools = {t.__name__: t for t in request_egress_tool(policy)}
    req = egress_tools["request_egress"]("api.drill.example.com", "drill egress check")
    assert req["ok"] is True and policy.is_allowed("api.drill.example.com")
    assert policy.remove_domain("api.drill.example.com")
    print("[5/6] egress policy round-trip (request_egress → allowed → removed)")

    # 6. Write the drill report.
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = f"""# Security closure-loop drill report

- Date: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}
- Repo: `{repo}` (kept on disk: {args.keep})
- State dir: `{store._dir}`

## What was validated

1. Threat model read/update round-trip (`threat_model_read` / `threat_model_update`).
2. Finding logging (`security_finding_log`) with reachability reasoning.
3. Finding status carried `open → fixed → closed` with a linked fix PR
   (`security_finding_update`).
4. Findings persist to `findings.json` and survive re-read.
5. Egress policy round-trip (`request_egress` → allowlist → remove), the P0
   network-policy loop.

## Findings

| id | title | severity | file | status | pr_url |
|----|-------|----------|------|--------|--------|
| {final['id']} | {final['title']} | {final['severity']} | {final['file']} | {final['status']} | {final['pr_url']} |

## Turning this into a REAL agent drill

1. `git init` the repo (or use `--keep` and open it in OpenWorker).
2. Start a session with the **Security Coworker** on that folder.
3. Watch it: threat-model read → semgrep/secret scan → triage → `security_finding_log`
   → fix PR → `security_finding_update(fixed)` when merged → `closed`.
4. If a scanner/download needs a blocked site, watch `request_egress` ask and wait
   for approval, then the domain appears in Settings ▸ Security ▸ Egress policy.
5. Record the coverage note (which scanners ran, which were degraded) here.
"""
    out.write_text(report, encoding="utf-8")
    print(f"[6/6] report written: {out}")

    if not args.keep:
        import shutil

        shutil.rmtree(repo, ignore_errors=True)
        print("Drill repo removed (use --keep to retain it).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())