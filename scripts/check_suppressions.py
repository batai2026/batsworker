"""Keep osv-scanner.toml honest: no suppression may outlive its package.

Two kinds of rot are checked, both of which were real on 2026-11-18 when 13 of
20 entries were deleted after the fact:

  DEAD      the advisory id no longer says anything about this tree -- the
            package is absent from every lockfile, or it is present but pinned
            at a version the advisory marks as patched. Suppressing it hides
            future findings for no benefit. (6 zip rows: the lock had moved to
            9.6.0, past `patched >= 9.0.0`. 5 unic-* rows: idna 1.1.0 stopped
            pulling them.)

  UNMAPPED  the id is in no known advisory database, so nothing ties it to a
            package. That is what the 21st entry turned out to be -- a phantom
            the issue text had counted, which no lockfile could justify.

Why this lives outside osv-scanner. osv-scanner reports unused ignores, but it
can only do so when it has the advisory and scans crates.io; it cannot tell you
an entry is dead because the crate is gone, and it says nothing at all about an
id it has never heard of. This check is offline on purpose: it reads lockfiles
and the advisory snapshot, so it works on a laptop, in a sandbox with no
egress, and in CI without a token.

WHY THE ID -> PACKAGE MAP IS EXPLICIT. osv-scanner 2.7.1 rejects unknown keys:
adding `package = "..."` to an [[IgnoredVulns]] entry makes the loader fail with
"unknown keys in config file: IgnoredVulns.package" and exit 127, which would
silently disable every acceptance in the file. Verified against 2.7.1 on
2026-11-18. So the association lives here, is reviewed in a diff, and the test
exists partly to force that review when an entry is added without one.

WHAT THIS DOES NOT DO. It does not re-litigate whether an acceptance is
*justified* -- whether the crate is reachable on a shipped platform. That is a
judgement per entry, written down in the reason text.

LAPSE CHECKING. Without flags, a date that is close is a warning, not a
failure: the acceptance is still valid today, and blocking would red a push
that changed nothing. Pass `--check-expiry [DAYS]` (default 30) to fail
instead -- that is what the PR gate uses, so an acceptance that expires soon
(or has already lapsed) turns the pull request red rather than waiting for the
next full-backlog scan. osv-scanner itself makes a lapsed date reappear as a
finding; this makes it visible on the PR instead of at the next scheduled run.

Usage:
    python scripts/check_suppressions.py [--root .] [--json]
    python scripts/check_suppressions.py --check-expiry [DAYS]
    python scripts/check_suppressions.py --markdown docs/dependency-risk-register.md

Exits 0 when every suppression still maps to a live, affected package and none
is expiring within the `--check-expiry` window, 1 otherwise, 2 on unreadable
input.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # stdlib since 3.11
except ModuleNotFoundError:  # pragma: no cover - the 3.10 floor
    import tomli as tomllib  # type: ignore[no-redef]

# --- advisory -> package, and the fixed-version boundary ---------------------
#
# `fixed_at` is what the advisory says, not what we wish it said: a version is
# only "already patched" if the lockfile pins a version outside every affected
# range. For unmaintained crates there is no fix, so None means "no version is
# safe" and any pin is alive.
#
# Extend this when an advisory becomes OSV-listed but is not here yet; the
# UNMAPPED report names the id so the fix is obvious. Keys are lowercase so a
# case change in an id cannot silently unmapped an entry.
ADVISORIES: dict[str, dict] = {
    # glib VariantStrIter unsoundness. gtk 0.18.2 pins glib ^0.18; patched >= 0.20.0.
    "ghsa-wrw7-89jp-8q8g": {"package": "glib", "ecosystem": "crates.io", "fixed_at": "0.20.0"},
    # The five below have no published fix -- every version is affected.
    "rustsec-2024-0370": {"package": "proc-macro-error", "ecosystem": "crates.io", "fixed_at": None},
    "rustsec-2026-0543": {"package": "chrono", "ecosystem": "crates.io", "fixed_at": None},
    "rustsec-2026-0370": {"package": "zvariant", "ecosystem": "crates.io", "fixed_at": None},
    "rustsec-2026-0371": {"package": "zbus_names", "ecosystem": "crates.io", "fixed_at": None},
    "rustsec-2026-0372": {"package": "zvariant_derive", "ecosystem": "crates.io", "fixed_at": None},
    # Ids deleted on 2026-11-18, kept here so that re-adding one by hand is
    # reported as DEAD instead of UNMAPPED -- the message points at the reason.
    # picomatch's row was added to this list on 2026-11-26: the advisory was
    # WITHDRAWN upstream on 2026-11-18 ("not a security issue"), so no version is
    # affected and there is nothing to fix. fixed_at stays None on purpose -- the
    # withdrawal is not a fix, and if picomatch ever gets a real advisory it will
    # have a new id and need a new row, not this one.
    #
    # `withdrawn` is what actually carries that conclusion: with fixed_at None and
    # picomatch still present in the npm lockfile, the checks below would call a
    # re-added entry "alive" and pass. Verified counterfactually on 2026-11-28 --
    # before this key existed, re-adding the entry exited 0.
    "ghsa-fc5q-39xc-vjpf": {"package": "picomatch", "ecosystem": "npm",
                            "fixed_at": None,
                            "withdrawn": "2026-11-18T15:38:56Z"},
    "ghsa-m665-v7pm-7grj": {"package": "zip", "ecosystem": "crates.io", "fixed_at": "9.0.5"},
    "ghsa-q7v9-x93c-xqv3": {"package": "zip", "ecosystem": "crates.io", "fixed_at": "9.0.0"},
    "ghsa-8hxf-qr8p-62x4": {"package": "zip", "ecosystem": "crates.io", "fixed_at": "9.0.0"},
    "rustsec-2026-0367": {"package": "zip", "ecosystem": "crates.io", "fixed_at": "9.0.0"},
    "rustsec-2026-0368": {"package": "zip", "ecosystem": "crates.io", "fixed_at": "9.0.0"},
    "rustsec-2026-0369": {"package": "zip", "ecosystem": "crates.io", "fixed_at": "9.0.0"},
    "rustsec-2026-0194": {"package": "quick-xml", "ecosystem": "crates.io", "fixed_at": "0.41.0"},
    "rustsec-2026-0195": {"package": "quick-xml", "ecosystem": "crates.io", "fixed_at": "0.41.0"},
    "rustsec-2025-0075": {"package": "unic-char-range", "ecosystem": "crates.io", "fixed_at": None},
    "rustsec-2025-0080": {"package": "unic-common", "ecosystem": "crates.io", "fixed_at": None},
    "rustsec-2025-0081": {"package": "unic-char-property", "ecosystem": "crates.io", "fixed_at": None},
    "rustsec-2025-0098": {"package": "unic-ucd-version", "ecosystem": "crates.io", "fixed_at": None},
    "rustsec-2025-0100": {"package": "unic-ucd-ident", "ecosystem": "crates.io", "fixed_at": None},
}

# Lockfile globs per ecosystem. Cargo.lock and package-lock.json carry explicit
# name/version pairs; uv.lock does too, so one parser covers all three.
LOCK_GLOBS: dict[str, tuple[str, ...]] = {
    "crates.io": ("**/Cargo.lock",),
    "npm": ("**/package-lock.json",),
    "pypi": ("uv.lock",),
}

# Warn this many days before an acceptance lapses, so the rotation has room to
# re-argue or upgrade rather than discovering it on a red scheduled run.
EXPIRY_WARN_DAYS = 90


# --- minimal, comparable versions -------------------------------------------


@dataclass(frozen=True)
class Version:
    """A version comparable across the semver/calver shapes these locks use.

    Deliberately not packaging.Version or node-semver: this script must stay
    stdlib-only so it runs with no install step, and the only comparison that
    matters is "is the pinned release at or past the fixed one". Pre-release
    tags sort before their release (`0.0.0-x` is a pre-release of `0.0.0`),
    which is the conservative direction: a pre-release is not treated as
    patched.
    """

    release: tuple[int, ...]
    prerelease: str = ""

    @classmethod
    def parse(cls, text: str) -> "Version":
        core, _, pre = text.strip().partition("-")
        core = core.split("+", 1)[0]
        parts = []
        for piece in core.split("."):
            m = re.match(r"^\d+", piece)
            if not m:
                break
            parts.append(int(m.group(0)))
        while len(parts) > 1 and parts[-1] == 0:
            parts.pop()
        return cls(tuple(parts), pre)

    def __lt__(self, other: "Version") -> bool:
        if self.release != other.release:
            return self.release < other.release
        # 1.2.3-beta < 1.2.3
        if self.prerelease == other.prerelease:
            return False
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        return self.prerelease < other.prerelease


# --- lockfile parsing --------------------------------------------------------


@dataclass
class Pinned:
    package: str
    version: str
    lockfile: str


def _pairs(text: str):
    """Yield (name, version) for every package block in a lockfile.

    Works for Cargo.lock, uv.lock (both TOML with adjacent name/version lines)
    and package-lock.json (JSON, handled separately because its keys are paths).
    """
    name = None
    for line in text.splitlines():
        m = re.match(r'^\s*name\s*=\s*"([^"]+)"', line)
        if m:
            name = m.group(1)
            continue
        m = re.match(r'^\s*version\s*=\s*"([^"]+)"', line)
        if m and name:
            yield name, m.group(1)
            name = None


def _npm_pairs(text: str):
    """package-lock.json: packages are keyed by install path, so the name is the
    last segment of the key (or the `name` field for the root package)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return
    for path, meta in (data.get("packages") or {}).items():
        if not path or not isinstance(meta, dict):
            continue
        if meta.get("link"):
            continue
        name = path.rsplit("node_modules/", 1)[-1]
        version = meta.get("version")
        if name and version:
            yield name, str(version)


def load_pins(root: Path, ecosystem: str) -> tuple[list[Pinned], list[str]]:
    """Return (pins, problems). A lockfile that exists but cannot be read is a
    problem, not an empty result -- silence here would read as 'package gone'."""
    pins: list[Pinned] = []
    problems: list[str] = []
    seen: set[Path] = set()
    for pattern in LOCK_GLOBS.get(ecosystem, ()):
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            if rel in seen or "/node_modules/" in "/" + rel:
                continue
            seen.add(rel)
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                problems.append(f"{rel}: cannot read ({exc})")
                continue
            if not text.strip():
                problems.append(f"{rel}: empty lockfile")
                continue
            pairs = _npm_pairs(text) if path.suffix == ".json" else _pairs(text)
            for name, version in pairs:
                pins.append(Pinned(name, version, rel))
    if not pins and not problems:
        problems.append(
            f"no {ecosystem} lockfile matched {LOCK_GLOBS.get(ecosystem)} -- "
            "every suppression for this ecosystem would look dead"
        )
    return pins, problems


# --- the checks -------------------------------------------------------------


@dataclass
class EntryReport:
    id: str
    status: str  # alive | dead | unmapped
    detail: str
    package: str = ""
    pins: list[Pinned] = field(default_factory=list)
    effective_until: str = ""
    days_left: int | None = None
    near_expiry: bool = False
    why_not_fixed: str = ""


def parse_entries(config_text: str) -> list[dict]:
    """Read [[IgnoredVulns]] out of the real TOML, not by regex.

    A second parser would be a second source of truth. Unknown-key strictness is
    the scanner's; here a file that does not parse is simply an error.
    """
    data = tomllib.loads(config_text)
    raw = data.get("IgnoredVulns") or []
    if isinstance(raw, dict):  # a single [IgnoredVulns] table
        raw = [raw]
    return list(raw)


def _days_until(date_text: str, today: dt.date) -> int | None:
    try:
        return (dt.date.fromisoformat(str(date_text)) - today).days
    except (TypeError, ValueError):
        return None


def audit(entries: list[dict], pins_by_eco: dict[str, list[Pinned]],
          today: dt.date) -> list[EntryReport]:
    reports: list[EntryReport] = []
    for entry in entries:
        vid = str(entry.get("id") or "").strip()
        until = str(entry.get("effectiveUntil") or entry.get("ignoreUntil") or "")
        days = _days_until(until, today) if until else None
        near = days is not None and days <= EXPIRY_WARN_DAYS
        # First sentence of the reason names the blocker; that is what a reader of
        # the register needs, not the whole argument.
        why = (entry.get("reason") or "").strip().split(". ")[0].rstrip(".")
        why = why.replace("|", "/")[:200]

        meta = ADVISORIES.get(vid.lower())
        if not meta:
            reports.append(EntryReport(
                vid, "unmapped",
                "not in the advisory map in scripts/check_suppressions.py, so "
                "nothing ties this id to a package -- add it there, or drop the "
                "entry if the advisory no longer exists",
                effective_until=until, days_left=days, near_expiry=near,
                why_not_fixed=why))
            continue

        package = meta["package"]
        ecosystem = meta["ecosystem"]
        pins = [p for p in pins_by_eco.get(ecosystem, []) if p.package == package]
        fixed = meta.get("fixed_at")

        # A withdrawn advisory is not "unfixed", it is retracted: the CVE was
        # judged not to be a security issue (picomatch, withdrawn
        # 2026-11-18T15:38:56Z). osv-scanner drops the id and reports the entry
        # as an unused ignore, but this check reads a static map, so without this
        # branch it sees `fixed_at is None` plus the package still in the lockfile
        # and calls the entry "alive" forever -- the register keeps advertising a
        # risk GitHub retracted, and the suppression goes on swallowing any future
        # finding for that package. Checked before the `fixed is None` case, which
        # would otherwise classify every withdrawn advisory as alive.
        if meta.get("withdrawn"):
            reports.append(EntryReport(
                vid, "withdrawn",
                f"the advisory was withdrawn on {meta['withdrawn']} (not a "
                "security issue), so there is nothing left to accept -- delete "
                "the entry from osv-scanner.toml",
                package=package, pins=pins, effective_until=until,
                days_left=days, near_expiry=near, why_not_fixed=why))
            continue

        if not pins:
            reports.append(EntryReport(
                vid, "dead",
                f"{package} is in no {ecosystem} lockfile -- the suppression "
                "cannot match anything, so it hides nothing and reports nothing",
                package=package, effective_until=until, days_left=days,
                near_expiry=near, why_not_fixed=why))
            continue

        if fixed is None:
            where = ", ".join(f"{p.version} ({p.lockfile})" for p in pins)
            reports.append(EntryReport(
                vid, "alive",
                f"{package} present at {where}; no fixed version is published",
                package=package, pins=pins, effective_until=until,
                days_left=days, near_expiry=near, why_not_fixed=why))
            continue

        boundary = Version.parse(fixed)
        unpatched = [p for p in pins if Version.parse(p.version) < boundary]
        if not unpatched:
            where = ", ".join(f"{p.version} ({p.lockfile})" for p in pins)
            reports.append(EntryReport(
                vid, "dead",
                f"{package} is pinned at {where}, at or past the fixed version "
                f"{fixed} -- the advisory no longer applies, so the entry hides "
                "any future finding for this crate",
                package=package, pins=pins, effective_until=until,
                days_left=days, near_expiry=near))
        else:
            where = ", ".join(f"{p.version} ({p.lockfile})" for p in unpatched)
            reports.append(EntryReport(
                vid, "alive",
                f"{package} present at {where}; fixed only at {fixed}",
                package=package, pins=unpatched, effective_until=until,
                days_left=days, near_expiry=near, why_not_fixed=why))
    return reports


def render(reports: list[EntryReport], fail_window: int | None = None) -> str:
    dead = [r for r in reports if r.status == "dead"]
    unmapped = [r for r in reports if r.status == "unmapped"]
    withdrawn = [r for r in reports if r.status == "withdrawn"]
    alive = [r for r in reports if r.status == "alive"]
    near = [r for r in reports if r.near_expiry]

    lines = [
        f"{len(reports)} suppression(s): {len(alive)} alive, "
        f"{len(dead)} dead, {len(unmapped)} unmapped"
        + (f", {len(withdrawn)} withdrawn" if withdrawn else "")
        + (f", {len(near)} expiring within {EXPIRY_WARN_DAYS} days" if near else ""),
        "",
    ]

    for report in dead:
        print(f"::error file=osv-scanner.toml,title={report.id} no longer applies"
              f"::{report.id}: {report.detail}")
    for report in unmapped:
        print(f"::error file=osv-scanner.toml,title={report.id} is unmapped"
              f"::{report.id}: {report.detail}")
    for report in withdrawn:
        print(f"::error file=osv-scanner.toml,title={report.id} was withdrawn"
              f"::{report.id}: {report.detail}")

    if dead or unmapped or withdrawn:
        lines.append("These entries must be removed (or mapped, if the id is real):")
        for report in dead:
            lines.append(f"  DEAD      {report.id}")
            lines.append(f"            {report.detail}")
        for report in unmapped:
            lines.append(f"  UNMAPPED  {report.id}")
            lines.append(f"            {report.detail}")
        for report in withdrawn:
            lines.append(f"  WITHDRAWN {report.id}")
            lines.append(f"            {report.detail}")
        lines.append("")
    else:
        lines.append("Every suppression still maps to a package that is present "
                     "and still affected.")
        lines.append("")

    if alive:
        lines.append("Accepted, still applicable:")
        for report in sorted(alive, key=lambda r: (r.days_left is None,
                                                   r.days_left or 0, r.id)):
            when = ""
            if report.days_left is not None:
                when = f"  expires {report.effective_until} ({report.days_left}d)"
                if report.near_expiry:
                    when += "  <- review now"
            lines.append(f"  {report.id:<24} {report.package}{when}")
        lines.append("")

    # A date that is close to lapsing is a warning, not a failure, unless
    # --check-expiry is set: the acceptance is still valid today, and blocking
    # here would red a push that changed nothing. The PR gate passes
    # --check-expiry so an acceptance running out of runway turns the PR red
    # instead of waiting for the scheduled full-backlog scan (which hard-fails on
    # a lapsed date via osv-scanner itself).
    failing = [r for r in reports
               if r.days_left is not None
               and (fail_window is not None and r.days_left <= fail_window)]
    for report in failing:
        print(f"::error file=osv-scanner.toml,title={report.id} expires soon"
              f"::{report.id} ({report.package}) lapses "
              f"{report.effective_until}, {report.days_left} day(s) away "
              f"-- re-argue or upgrade before it goes red")
    # Inside the wide warning window but outside the fail window: still warn,
    # just do not fail -- there is runway left, and the PR gate should only
    # force action inside its stated window.
    failing_ids = {r.id for r in failing}
    for report in near:
        if report.id in failing_ids:
            continue
        print(f"::warning file=osv-scanner.toml,title={report.id} expires soon"
              f"::{report.id} ({report.package}) lapses "
              f"{report.effective_until}, {report.days_left} day(s) away "
              f"-- re-argue or upgrade")
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(reports: list[EntryReport], today: dt.date) -> str:
    """The register: every live acceptance with its due date, soonest first.

    Kept as a generated artefact so it cannot disagree with the config -- a test
    asserts this file equals what the current osv-scanner.toml produces.

    `today` is used ONLY to order rows (soonest deadline first) and is
    deliberately absent from the output. An earlier version stamped the snapshot
    date and a "days left" column into the text, which made the file change every
    midnight and left the drift test permanently red -- the test pins a fixed
    date, so no regeneration could ever match it again. The countdown belongs in
    the daily stdout run, not in a committed file: a committed number is a number
    that is wrong by tomorrow.
    """
    # Only live acceptances belong in the register. Selecting on `r.package` alone
    # (the pre-2026-11-28 form) also pulled in dead and withdrawn rows that still
    # name a package, which put a retracted advisory back on the page as if it
    # were accepted debt -- the opposite of what this file is for.
    alive = sorted(
        (r for r in reports if r.status == "alive"),
        key=lambda r: (r.effective_until == "", r.effective_until),
    )
    lines = [
        "# Accepted dependency risk \u2014 register and due dates",
        "",
        "<!-- GENERATED by scripts/check_suppressions.py --markdown. Do not edit by hand;",
        "     regenerate after changing osv-scanner.toml. A test compares this file with",
        "     the live config, so a stale table fails CI rather than misleads a reader. -->",
        "",
        "Every entry in `osv-scanner.toml` is debt with a due date. When an "
        "`effectiveUntil` passes, osv-scanner stops honouring the acceptance and the "
        "finding returns to CI \u2014 there is no silent ageing. This table exists so the "
        "next one can be seen coming. Run `python scripts/check_suppressions.py` for "
        "the live countdown; this file records only what does not change daily.",
        "",
        "| due | advisory | package | why it cannot be fixed |",
        "|---|---|---|---|",
    ]
    for report in alive:
        due = report.effective_until if report.effective_until else "no date"
        lines.append(
            f"| {due} | `{report.id}` | `{report.package}` | {report.why_not_fixed} |"
        )
    lines += [
        "",
        "## Rotation",
        "",
        "Each row needs one of two outcomes before its date: an upgrade that closes "
        "the advisory, or a fresh argument written into the entry's `reason` plus a "
        "new `effectiveUntil`. Extending a date without re-arguing it is the failure "
        "mode this register is meant to make visible.",
        "",
        "`scripts/check_suppressions.py` (run by `tests/test_suppressions.py`) fails "
        "the build if an entry no longer maps to a package, or if the package is "
        "already pinned past the advisory's fixed version \u2014 both were true of 13 of "
        "the 20 entries removed on 2026-11-18.",
        "",
        "It also fails on a third thing an entry can outlive: the advisory itself. "
        "`GHSA-fc5q-39xc-vjpf` (picomatch) was withdrawn by GitHub on "
        "2026-11-18T15:38:56Z as \"not a security issue\", so there is no version "
        "left to be affected by it. A suppression for a retracted advisory is not "
        "harmless: it goes on swallowing any future finding for that package for "
        "as long as the id stays in `osv-scanner.toml`. Such an entry is reported "
        "as WITHDRAWN and fails the build. The first two modes catch evidence that "
        "stopped existing (the package moved, or was never affected); this one "
        "catches a conclusion that was overturned.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--config", default="osv-scanner.toml",
                        help="suppression file, relative to --root")
    parser.add_argument("--today", help="override today's date (YYYY-MM-DD), for tests")
    parser.add_argument("--check-expiry", nargs="?", const=30, type=int, metavar="DAYS",
                        default=None,
                        help="fail (exit 1) when an acceptance lapses within DAYS "
                             "(default 30, or already lapsed); without this flag a "
                             "close date is only a warning")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--markdown", metavar="PATH",
                        help="write the expiry register to PATH (and check nothing else)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    config_path = root / args.config
    try:
        config_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"error: cannot read {config_path}: {exc}\n")
        return 2
    try:
        entries = parse_entries(config_text)
    except Exception as exc:  # noqa: BLE001 - any TOML failure is the same verdict
        sys.stderr.write(f"error: {args.config} does not parse as TOML: {exc}\n")
        sys.stderr.write(
            "osv-scanner would reject this file too, so every acceptance is "
            "already inactive.\n"
        )
        return 2

    pins_by_eco: dict[str, list[Pinned]] = {}
    problems: list[str] = []
    # Only ecosystems this config actually references need a lockfile. Demanding
    # one for an ecosystem no entry mentions would fail a repo that legitimately
    # has no npm tree -- a guard that fires on nothing is a guard people disable.
    referenced = {
        ADVISORIES[str(e.get("id") or "").lower()]["ecosystem"]
        for e in entries
        if str(e.get("id") or "").lower() in ADVISORIES
    }
    for ecosystem in sorted(referenced):
        pins, errs = load_pins(root, ecosystem)
        pins_by_eco[ecosystem] = pins
        problems.extend(errs)

    if problems:
        for problem in problems:
            sys.stderr.write(f"error: {problem}\n")
        sys.stderr.write(
            "Refusing to judge suppressions from unreadable lockfiles -- a "
            "missing lockfile would mark every entry dead.\n"
        )
        return 2

    today = (dt.date.fromisoformat(args.today) if args.today
             else dt.date.today())
    reports = audit(entries, pins_by_eco, today)

    if args.markdown:
        target = Path(args.markdown)
        if not target.is_absolute():
            target = root / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(reports, today), encoding="utf-8",
                          newline="\n")
        print(f"wrote {target}")
    elif args.json:
        print(json.dumps(
            {"entries": [
                {"id": r.id, "status": r.status, "package": r.package,
                 "detail": r.detail, "effectiveUntil": r.effective_until,
                 "daysLeft": r.days_left}
                for r in reports], "today": today.isoformat()}, indent=2))
    else:
        sys.stdout.write(render(reports, fail_window=args.check_expiry))

    if any(r.status in ("dead", "unmapped", "withdrawn") for r in reports):
        return 1
    if args.check_expiry is not None and any(
            r.days_left is not None and r.days_left <= args.check_expiry
            for r in reports):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
