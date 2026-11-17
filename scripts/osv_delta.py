"""Fail a push only when the push introduces a vulnerable dependency.

Why this exists. `main` went red twice in one day on the same job for reasons that
had nothing to do with the commits being pushed:

  * RUSTSEC-2026-0543 -- OSV re-published a chrono advisory at 10:15Z; the merges
    that went red (12:12Z, 12:15Z) never touched chrono, and chrono was already in
    the lockfile at the last green run.
  * GHSA-fc5q-39xc-vjpf -- picomatch 4.0.4 became a *reviewed* advisory at 15:56Z
    while `surfaces/gui/package-lock.json` sat at exactly the version it had had
    at the last green run.

Both are real, both need handling, but neither is something the pushing author
changed. A gate that cannot tell "our diff exposed this" from "the world learned
something new about code we already had" teaches people to ignore it. So the
blocking decision becomes a delta:

  * push    -> block ONLY on findings this push introduces (this script)
  * schedule / workflow_dispatch -> full backlog scan, still blocking. That is
              where an advisory landing on unchanged code gets caught, once, by
              the person on audit duty, instead of on a random dependency-free
              commit.

The baseline is a fresh scan of the commit being pushed FROM, not a file committed
to the repo. Both sides are scanned in the same run against the same OSV data, so
a newly published advisory appears on both sides and cancels out; only a difference
attributable to the pushed code survives. A recorded-in-repo baseline would go
stale exactly when OSV is updated, which is the confusion this removes. It also
means push and pull_request share one definition of "new" -- the PR job already
scans base and head this way.

Comparison semantics mirror google/osv-scanner's own DiffVulnerabilityResults
(internal/ci/vulnerability_result_diff.go): a finding is keyed by source lockfile,
then package *including version*, then advisory id; only keys present in head and
absent from base count as new. Version being part of the key is what gets the two
cases right:

  * an advisory published today against a version that was already installed is
    not new (cancels against the base scan);
  * upgrading INTO a vulnerable version is new (base never had that version).

Two consequences stated plainly, because both are deliberate:

  * Removing an acceptance from `osv-scanner.toml` shows up as new: the base scan
    honoured the exemption, the head scan does not. That is the correct report --
    dropping an exemption is exactly when a human should look.
  * A finding that was already present at the base commit never blocks a push, even
    if the base tree is old. Reaching for `git checkout <old commit>` to escape a
    red gate therefore does not work, but a genuinely ancient base does not
    retroactively block either; the scheduled run is the backstop.

Fail closed. osv-scanner exits non-zero both when it finds vulnerabilities and when
the scan itself breaks, and a broken scan can still leave a JSON file with no
`results`. A delta tool that read "no results" as "no new findings" would let a push
that disables the scanner pass as clean. Every unusable input -- missing file,
empty file, unparsable JSON, no `results` key -- is an error (exit 2), never an
empty set.

Usage:
    python scripts/osv_delta.py --base base.json --head head.json \
        [--base-root DIR] [--head-root DIR] [--summary-out PATH] [--output-found PATH]

Exits 0 when head introduces no new finding, 1 when it does (listing them), 2 on
unusable input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# osv-scanner's JSON nests findings under results[].packages[].vulnerabilities[].
# `source` is the lockfile a package came from; the same advisory on two lockfiles
# is two findings because they are two independent install surfaces.
Key = tuple[str, str, str, str]  # (lockfile, ecosystem, name@version, advisory id)


def _relativise(path: str, root: str | None) -> str:
    """Turn an absolute lockfile path into a repo-relative one.

    osv-scanner echoes whatever path it walked, and it resolves `./` to an absolute
    path, so a scan from `/w/base-checkout` and one from `/w/head-checkout` report
    the same lockfile under different strings. Without stripping each scan's own
    root, every lockfile would look "new" the moment the two roots differ -- a
    uniform false positive that would make the gate useless.
    """
    p = path.replace("\\", "/")
    if root:
        r = root.replace("\\", "/").rstrip("/") + "/"
        if p.startswith(r):
            p = p[len(r) :]
    return p.lstrip("./")


def load_results(path: Path, root: str | None = None) -> dict[Key, dict]:
    """Return findings keyed by (lockfile, ecosystem, name@version, advisory id).

    Raises ValueError for anything that is not a usable scan result.
    """
    if not path.exists():
        raise ValueError(
            f"{path}: no scan output (the scan did not run, or wrote nothing)"
        )
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise ValueError(f"{path}: empty scan output")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc

    if not isinstance(data, dict) or "results" not in data:
        # A scan that died mid-way can still emit `{}` or a truncated document.
        raise ValueError(f"{path}: no 'results' key -- not a completed scan")

    findings: dict[Key, dict] = {}
    for result in data.get("results") or []:
        source = _relativise(str((result.get("source") or {}).get("path", "")), root)
        for pkg in result.get("packages") or []:
            info = pkg.get("package") or {}
            name = info.get("name") or ""
            if not name:
                continue
            version = info.get("version") or ""
            ecosystem = info.get("ecosystem") or ""
            for vuln in pkg.get("vulnerabilities") or []:
                # One canonical id per entry, deliberately not every alias: OSV
                # lists the same issue as GHSA/RUSTSEC/CVE depending on publish
                # order, and treating a pair as two findings would report an
                # alias being added as a brand-new vulnerability.
                vid = vuln.get("id")
                if not vid:
                    continue
                findings[(source, ecosystem, f"{name}@{version}", vid)] = {
                    "source": source,
                    "ecosystem": ecosystem,
                    "package": name,
                    "version": version,
                    "id": vid,
                    "aliases": [a for a in (vuln.get("aliases") or []) if a != vid],
                    "summary": (vuln.get("summary") or "").strip(),
                    "severity": str(
                        (vuln.get("database_specific") or {}).get("severity") or ""
                    ),
                    "fixed": _fixed_versions(vuln),
                }
    return findings


def _fixed_versions(vuln: dict) -> list[str]:
    """Versions the advisory names as fixes, so the report can say if one exists."""
    out: list[str] = []
    for affected in vuln.get("affected") or []:
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                if event.get("fixed"):
                    out.append(str(event["fixed"]))
    return sorted(set(out))


def new_findings(base: dict[Key, dict], head: dict[Key, dict]) -> list[dict]:
    """Findings in head whose key the base scan does not already report."""
    return [head[k] for k in sorted(head) if k not in base]


def render(found: list[dict], base_count: int, head_count: int) -> str:
    lines = [
        f"Base scan: {base_count} finding(s)   Head scan: {head_count} finding(s)",
        "",
    ]
    if not found:
        lines.append(
            "No finding is new in this push. Advisories that appeared against "
            "unchanged code are the scheduled audit's job, not this push's."
        )
        return "\n".join(lines) + "\n"

    lines.append(f"{len(found)} finding(s) introduced by this push:")
    lines.append("")
    for f in found:
        # GitHub annotation syntax, so the finding appears in the run summary and
        # against the changed file rather than only inside the log.
        print(
            f"::error file={f['source']},title={f['id']} on {f['package']}@{f['version']}"
            f"::{f['id']} ({f['ecosystem']}) in {f['source']} -- "
            f"{f['summary'] or 'no summary'} "
            f"[fix: {', '.join(f['fixed']) if f['fixed'] else 'none published'}]"
        )
        lines.append(f"  {f['id']}  {f['package']}@{f['version']}  ({f['ecosystem']})")
        lines.append(f"    lockfile: {f['source']}")
        if f["severity"]:
            lines.append(f"    severity: {f['severity']}")
        if f["aliases"]:
            lines.append(f"    aliases:  {', '.join(f['aliases'])}")
        lines.append(
            f"    fix:      {', '.join(f['fixed']) if f['fixed'] else 'none published'}"
        )
        if f["summary"]:
            lines.append(f"    {f['summary']}")
        lines.append("")
    lines.append(
        "Delta only: findings already present at the base commit are not listed. "
        "If a row here has a published fix, upgrade rather than accept it -- see "
        "the POLICY block at the top of osv-scanner.toml."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", required=True, help="scan of the commit being pushed from (JSON)"
    )
    parser.add_argument(
        "--head", required=True, help="scan of the tree being pushed (JSON)"
    )
    parser.add_argument("--base-root", help="directory root to strip from base paths")
    parser.add_argument("--head-root", help="directory root to strip from head paths")
    parser.add_argument("--summary-out", help="also write the human summary here")
    parser.add_argument("--output-found", help="write the new findings as JSON here")
    args = parser.parse_args(argv)

    try:
        base = load_results(Path(args.base), args.base_root)
        head = load_results(Path(args.head), args.head_root)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        sys.stderr.write(
            "Refusing to report 'no new findings' from an unusable scan -- a broken "
            "scan must not read as a clean diff.\n"
        )
        return 2

    found = new_findings(base, head)
    summary = render(found, len(base), len(head))
    sys.stdout.write(summary)

    if args.summary_out:
        Path(args.summary_out).write_text(summary, encoding="utf-8")
    if args.output_found:
        Path(args.output_found).write_text(
            json.dumps({"new": found}, indent=2) + "\n", encoding="utf-8"
        )
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
