#!/usr/bin/env python
"""Write the branch-protection settings that docs/branch-protection.md describes.

Replaces the archived ``bp_apply.py`` that the document used to point at: that file
lived in a scratch directory, is not in this repo, and hard-coded its own copy of
the required-check list -- so the doc and the thing that wrote the gate could drift
apart. Here the payload is *built from the document*, which means there is no
second copy to drift.

WHY A FULL-OBJECT PUT IS DANGEROUS ENOUGH TO WRAP. Classic protection is a single
``PUT`` of the whole object: any field omitted is reset to its default. A script
that sends only the checks it cares about therefore silently turns off everything
it did not mention. So this one:

  * refuses to run if the document is missing a setting it needs (there is nothing
    to write from);
  * prints the complete payload and the diff against the live state, and writes
    nothing without ``--apply``;
  * reads the remote back after writing and exits non-zero unless the result is
    byte-for-byte what was described -- "the PUT returned 200" is not evidence
    that the gate is now what the document says.

WHAT IT DOES NOT TOUCH. ``required_pull_request_reviews`` and ``restrictions`` are
sent as ``null`` on purpose: this repo has neither, and ``null`` is how the API
expresses "off". If that ever stops being true, this script is the wrong tool and
should be extended deliberately rather than silently.

Usage:
    python scripts/apply_branch_protection.py            # show what would change
    python scripts/apply_branch_protection.py --apply    # write, then read back

Exit 0 when the remote already matches (in dry-run) or after a verified write.
1 when the write cannot proceed, the read-back disagrees, or dry-run found a
difference (so it can be used as a check). 2 on unusable input.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_branch_protection import (  # noqa: E402
    DEFAULT_REPO,
    DOC,
    REPO_FLAGS,
    parse_doc,
    parse_live,
    token,
)

ACTIONS_APP = 15368  # GitHub Actions: the app every workflow check run reports under


def api(repo: str, path: str, tok: str, method: str = "GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}",
        data=data, method=method,
        headers={
            "Authorization": f"Bearer {tok}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "openworker-branch-protection-apply",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            return (json.loads(body) if body else None), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:600]}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def build_payload(doc) -> dict:
    """Turn the document into a full protection object.

    Every setting is restated because the API resets anything absent. A setting the
    document does not declare raises instead of defaulting -- guessing here would
    write a gate nobody described.
    """
    missing = [k for k in ("strict", "enforce_admins", "required_linear_history",
                           "allow_force_pushes") if k not in doc.settings]
    if missing:
        raise ValueError(
            f"{DOC.as_posix()} does not state {missing}; refusing to write "
            "protection from an incomplete description")
    if not doc.checks:
        raise ValueError(f"{DOC.as_posix()} lists no required checks")

    return {
        "required_status_checks": {
            "strict": doc.settings["strict"],
            "checks": [{"context": c, "app_id": ACTIONS_APP} for c in doc.checks],
        },
        "enforce_admins": doc.settings["enforce_admins"],
        "required_pull_request_reviews": None,
        "restrictions": None,
        "required_linear_history": doc.settings["required_linear_history"],
        "allow_force_pushes": doc.settings["allow_force_pushes"],
        "allow_deletions": doc.settings.get("allow_deletions", False),
        "block_creations": False,
        "required_conversation_resolution": False,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }


def diff(doc, live) -> list[str]:
    """Human-readable differences, or an empty list when the remote already agrees."""
    out: list[str] = []
    doc_set, live_set = set(doc.checks), set(live.checks)
    for name in sorted(live_set - doc_set):
        out.append(f"  remove required check: {name}")
    for name in sorted(doc_set - live_set):
        out.append(f"  add required check:    {name}")
    for key, claimed in doc.settings.items():
        actual = live.settings.get(key)
        if actual is not None and claimed != actual:
            out.append(f"  set {key}: {str(actual).lower()} -> {str(claimed).lower()}")
    for key in REPO_FLAGS:
        if key in doc.flags and key in live.flags and doc.flags[key] != live.flags[key]:
            out.append(f"  repo flag {key}: {str(live.flags[key]).lower()} -> "
                       f"{str(doc.flags[key]).lower()} (separate API call)")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--doc", default=str(DOC), help="the document to apply")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name")
    parser.add_argument("--apply", action="store_true",
                        help="write the change (default: dry run)")
    parser.add_argument("--repo-flags", action="store_true",
                        help="also PATCH the repo-level flags the doc records")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    doc_path = root / args.doc
    try:
        doc = parse_doc(doc_path.read_text(encoding="utf-8"))
    except OSError as exc:
        sys.stderr.write(f"error: cannot read {doc_path}: {exc}\n")
        return 2
    try:
        payload = build_payload(doc)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    tok = token()
    if not tok:
        sys.stderr.write(
            "error: needs a GitHub token (GITHUB_TOKEN / GH_TOKEN, or git's "
            "credential helper)\n")
        return 2

    current, err = api(args.repo, "/branches/main/protection", tok)
    if err:
        sys.stderr.write(f"error: cannot read current protection: {err}\n")
        return 2
    live = parse_live(current)
    repo_doc, rerr = api(args.repo, "", tok)
    if rerr:
        sys.stderr.write(f"error: cannot read repo settings: {rerr}\n")
        return 2
    from dataclasses import replace
    live = replace(live, flags={k: bool(repo_doc.get(k)) for k in REPO_FLAGS
                                if repo_doc.get(k) is not None})

    changes = diff(doc, live)
    print(f"{args.repo}: docs/branch-protection.md -> live protection")
    print(f"  document declares {len(doc.checks)} required check(s), "
          + ", ".join(f"{k}={str(v).lower()}" for k, v in doc.settings.items()))
    if not changes:
        print("\nalready matches; nothing to write")
        return 0

    print(f"\n{len(changes)} change(s):")
    for line in changes:
        print(line)

    if not args.apply:
        print("\nDRY RUN -- re-run with --apply to write.")
        return 1

    print("\nwriting protection...")
    _, werr = api(args.repo, "/branches/main/protection", tok,
                  method="PUT", payload=payload)
    if werr:
        sys.stderr.write(f"error: PUT failed: {werr}\n")
        return 1

    if args.repo_flags:
        patch = {k: v for k, v in doc.flags.items() if k in REPO_FLAGS}
        if patch:
            _, perr = api(args.repo, "", tok, method="PATCH", payload=patch)
            if perr:
                sys.stderr.write(f"error: repo-flag PATCH failed: {perr}\n")
                return 1

    after, aerr = api(args.repo, "/branches/main/protection", tok)
    if aerr:
        sys.stderr.write(f"error: read-back failed: {aerr}\n")
        return 1
    live_after = parse_live(after)
    repo_after, _ = api(args.repo, "", tok)
    live_after = replace(live_after, flags={
        k: bool((repo_after or {}).get(k)) for k in REPO_FLAGS
        if (repo_after or {}).get(k) is not None})

    remaining = diff(doc, live_after)
    if remaining:
        print("\nREAD-BACK DISAGREES -- the gate is not what the document says:")
        for line in remaining:
            print(line)
        return 1

    print(f"\nread back OK: {len(live_after.checks)} required check(s), "
          + ", ".join(f"{k}={str(v).lower()}" for k, v in live_after.settings.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
