"""Tests for scripts/check_branch_protection.py and apply_branch_protection.py.

Both scripts exist because a merge gate was described in a document and had drifted
away from the thing it described, with no failure anywhere. The rules that matter
here are therefore all "does the check actually bite" rules, and each one is pinned
against a *real* fixture tree rather than a mock:

* the offline check must fail when a job that reports on pull requests is missing
  from the table, and when the table names a check no job produces;
* it must NOT fail for the three jobs that are deliberately push/schedule-only --
  requiring one of those blocks every merge forever, and a check that flags the
  correct configuration is worse than no check;
* the live comparison must fail when the document says ``strict: true`` and the
  remote says ``false`` -- the exact drift this was written for;
* the applier must build its payload from the document, so there is no second copy
  of the required-check list to drift.

Every mutation test asserts that the *unmutated* input passes first. Without that
half, a check that fails on everything would satisfy the test and prove nothing
(AGENTS.md section 10).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_branch_protection as cbp  # noqa: E402
import apply_branch_protection as abp  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

def _has_yaml() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


DOC_TEMPLATE = """\
# Branch protection on `main`

| setting | value |
|---|---|
| required checks | **{count}** |
| strict (up to date before merge) | **{strict}** |
| enforce admins | {admins} |
| required linear history | false |
| force pushes / deletions | false / false |
| required PR reviews | none |

```
allow_auto_merge: {auto}
delete_branch_on_merge: false
```

### The {count} required checks

| check | job time | what it protects |
|---|---|---|
{rows}
"""


def make_doc(checks, strict="false", admins="true", auto="true", count=None) -> str:
    rows = "\n".join(f"| `{c}` | 0m00s | fixture |" for c in checks)
    return DOC_TEMPLATE.format(
        count=count if count is not None else len(checks),
        strict=strict, admins=admins, auto=auto, rows=rows,
    )


# -- workflow fixtures ----------------------------------------------------------

CI_WORKFLOW = textwrap.dedent("""\
    name: CI
    on: [push, pull_request]
    jobs:
      lint:
        runs-on: ubuntu-latest
        steps: [{run: echo}]
      pytest:
        name: pytest
        strategy:
          matrix:
            python-version: ["3.12", "3.15"]
        runs-on: ubuntu-latest
        steps: [{run: echo}]
      named-matrix:
        name: pytest (${{ matrix.python-version }})
        strategy:
          matrix:
            python-version: ["3.12"]
        runs-on: ubuntu-latest
        steps: [{run: echo}]
    """)

PUSH_ONLY_WORKFLOW = textwrap.dedent("""\
    name: Dependency audit
    on:
      pull_request:
        branches: [main]
      push:
        branches: [main]
      schedule:
        - cron: "0 3 * * *"
    jobs:
      new-vulnerabilities:
        if: github.event_name == 'pull_request'
        uses: google/osv-scanner-action/.github/workflows/osv-scanner-reusable-pr.yml@abc
      push-delta:
        if: github.event_name == 'push'
        runs-on: ubuntu-latest
        steps: [{run: echo}]
      suppression-register:
        if: github.event_name != 'schedule'
        runs-on: ubuntu-latest
        steps: [{run: echo}]
    """)

FIXTURE_CHECKS = [
    "lint",
    "pytest (3.12)",
    "pytest (3.15)",
    "new-vulnerabilities / osv-scan",
    "suppression-register",
]


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(CI_WORKFLOW, encoding="utf-8")
    (wf / "dependency-audit.yml").write_text(PUSH_ONLY_WORKFLOW, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "branch-protection.md").write_text(
        make_doc(FIXTURE_CHECKS), encoding="utf-8")
    return tmp_path


@pytest.mark.skipif(not _has_yaml(), reason="needs PyYAML")
def test_correct_fixture_tree_has_no_problems(tree: Path):
    """The baseline half: without this, 'it flags everything' would pass every
    mutation test below."""
    doc_text = (tree / "docs" / "branch-protection.md").read_text(encoding="utf-8")
    doc = cbp.parse_doc(doc_text)
    jobs = cbp.read_jobs(tree)
    assert cbp.doc_problems(doc) == []
    assert cbp.offline_problems(doc, jobs, tree) == []


@pytest.mark.skipif(not _has_yaml(), reason="needs PyYAML")
def test_push_only_jobs_are_not_demanded(tree: Path):
    """`push-delta` never reports on a PR. If the checker demanded it, the 'fix'
    would block every merge forever -- the opposite failure, just as silent."""
    doc = cbp.parse_doc(
        (tree / "docs" / "branch-protection.md").read_text(encoding="utf-8"))
    jobs = cbp.read_jobs(tree)
    names = {j.context: j.pr_capable for j in jobs}
    assert names["push-delta"] is False
    assert names["new-vulnerabilities"] is True
    assert "push-delta" not in {p for p in cbp.offline_problems(doc, jobs, tree)}


@pytest.mark.skipif(not _has_yaml(), reason="needs PyYAML")
def test_guard_direction_is_read_from_the_expression(tree: Path):
    """`!= 'schedule'` reports on PRs; `== 'push'` does not. Both appear in real
    workflows, and getting the direction backwards inverts the whole table."""
    jobs = {j.job_id: j for j in cbp.read_jobs(tree)}
    assert jobs["push-delta"].pr_capable is False
    assert jobs["suppression-register"].pr_capable is True
    assert jobs["new-vulnerabilities"].pr_capable is True


@pytest.mark.skipif(not _has_yaml(), reason="needs PyYAML")
def test_missing_entry_for_a_pr_job_is_caught(tree: Path):
    """Mutation: drop `lint` from the table. Before: no problems. After: one."""
    path = tree / "docs" / "branch-protection.md"
    path.write_text(make_doc([c for c in FIXTURE_CHECKS if c != "lint"]),
                    encoding="utf-8")
    problems = cbp.offline_problems(
        cbp.parse_doc(path.read_text(encoding="utf-8")), cbp.read_jobs(tree), tree)
    assert any("`lint`" in p and "not a required check" in p for p in problems)


@pytest.mark.skipif(not _has_yaml(), reason="needs PyYAML")
def test_entry_for_a_nonexistent_check_is_caught(tree: Path):
    """Mutation: add a name no job produces. That is a typo in real life, and it
    leaves every PR pending forever."""
    path = tree / "docs" / "branch-protection.md"
    path.write_text(make_doc([*FIXTURE_CHECKS, "pytest (3.10)"]), encoding="utf-8")
    problems = cbp.offline_problems(
        cbp.parse_doc(path.read_text(encoding="utf-8")), cbp.read_jobs(tree), tree)
    assert any("`pytest (3.10)`" in p and "no job" in p for p in problems)


@pytest.mark.skipif(not _has_yaml(), reason="needs PyYAML")
def test_requiring_a_push_only_job_is_caught(tree: Path):
    """Mutation: the tempting mistake -- add `push-delta` because it sounds
    important. It never reports on a PR, so the gate would never open."""
    path = tree / "docs" / "branch-protection.md"
    path.write_text(make_doc([*FIXTURE_CHECKS, "push-delta"]), encoding="utf-8")
    problems = cbp.offline_problems(
        cbp.parse_doc(path.read_text(encoding="utf-8")), cbp.read_jobs(tree), tree)
    assert any("`push-delta`" in p and "guards it off pull requests" in p
               for p in problems)


@pytest.mark.skipif(not _has_yaml(), reason="needs PyYAML")
def test_matrix_expansion_matches_the_names_github_reports(tree: Path):
    """A matrix job reports one check per value, and the name may or may not
    interpolate the value. Both shapes must produce `pytest (3.12)`."""
    contexts = {j.context for j in cbp.read_jobs(tree)}
    assert "pytest (3.12)" in contexts
    assert "pytest (3.15)" in contexts
    # The interpolating form must be substituted, not left as the raw expression:
    # a required check documented as `pytest (${{ matrix.x }})` never reports.
    assert not any("${{" in c for c in contexts)


# -- document parsing -----------------------------------------------------------


def test_headline_count_must_match_the_table():
    doc = cbp.parse_doc(make_doc(["lint", "rust"], count=5))
    assert doc.headline == 5
    assert any("headline says 5" in p for p in cbp.doc_problems(doc))


def test_missing_setting_row_is_a_problem():
    text = make_doc(["lint"]).replace(
        "| strict (up to date before merge) | **false** |\n", "")
    doc = cbp.parse_doc(text)
    assert "strict" not in doc.settings
    assert any("strict (up to date before merge)" in p for p in cbp.doc_problems(doc))


def test_settings_are_parsed_from_markdown_emphasis():
    doc = cbp.parse_doc(make_doc(["lint"], strict="true", admins="false"))
    assert doc.settings["strict"] is True
    assert doc.settings["enforce_admins"] is False


def test_force_push_row_carries_two_settings():
    doc = cbp.parse_doc(make_doc(["lint"]))
    assert doc.settings["allow_force_pushes"] is False


def test_repo_flags_are_parsed():
    doc = cbp.parse_doc(make_doc(["lint"], auto="false"))
    assert doc.flags["allow_auto_merge"] is False


# -- live comparison ------------------------------------------------------------


def _live(checks, strict=False, admins=True, linear=False, flags=None) -> cbp.Live:
    return cbp.Live(
        checks=tuple(checks),
        settings={"strict": strict, "enforce_admins": admins,
                  "required_linear_history": linear},
        flags=flags if flags is not None else {
            "allow_auto_merge": True, "delete_branch_on_merge": False},
    )


def test_live_matching_document_has_no_problems():
    doc = cbp.parse_doc(make_doc(["lint", "rust"], strict="false"))
    assert cbp.live_problems(doc, _live(["lint", "rust"])) == []


def test_live_strict_drift_is_caught():
    """The failure this tool was written for: the doc says the branch must be up
    to date, the remote says it need not be. This exact pair was live on
    2026-11-28."""
    doc = cbp.parse_doc(make_doc(["lint"], strict="true"))
    problems = cbp.live_problems(doc, _live(["lint"], strict=False))
    assert any("strict (up to date before merge)" in p and "says true" in p
               for p in problems)


def test_live_extra_required_check_is_caught():
    doc = cbp.parse_doc(make_doc(["lint"]))
    problems = cbp.live_problems(doc, _live(["lint", "secret-scan"]))
    assert any("`secret-scan`" in p and "missing from" in p for p in problems)


def test_live_documented_but_unarmed_check_is_caught():
    doc = cbp.parse_doc(make_doc(["lint", "rust"]))
    problems = cbp.live_problems(doc, _live(["lint"]))
    assert any("`rust`" in p and "NOT enforced" in p for p in problems)


def test_unrecorded_repo_flag_is_caught():
    """`delete_branch_on_merge` decides whether cleanup is needed at all; if the
    doc does not record it, nobody learns when it flips."""
    text = make_doc(["lint"]).replace("allow_auto_merge: true\n", "")
    doc = cbp.parse_doc(text)
    problems = cbp.live_problems(doc, _live(["lint"]))
    assert any("allow_auto_merge" in p and "does not record" in p for p in problems)


def test_repo_flag_drift_is_caught():
    doc = cbp.parse_doc(make_doc(["lint"], auto="false"))
    problems = cbp.live_problems(doc, _live(["lint"], flags={
        "allow_auto_merge": True, "delete_branch_on_merge": False}))
    assert any("allow_auto_merge" in p and "says false" in p for p in problems)


# -- applier --------------------------------------------------------------------


def test_payload_is_built_from_the_document():
    """The point of the applier: no second copy of the check list exists."""
    doc = cbp.parse_doc(make_doc(["alpha", "beta"], strict="true"))
    payload = abp.build_payload(doc)
    assert [c["context"] for c in payload["required_status_checks"]["checks"]] == \
        ["alpha", "beta"]
    assert payload["required_status_checks"]["strict"] is True


def test_payload_refuses_an_incomplete_document():
    text = make_doc(["lint"]).replace(
        "| enforce admins | true |\n", "")
    doc = cbp.parse_doc(text)
    with pytest.raises(ValueError, match="enforce_admins"):
        abp.build_payload(doc)


def test_payload_refuses_an_empty_check_list():
    doc = cbp.parse_doc(make_doc([]))
    with pytest.raises(ValueError, match="no required checks"):
        abp.build_payload(doc)


def test_payload_restates_every_writable_setting():
    """A full-object PUT resets omitted fields, so a missing key is a silent
    downgrade of the gate."""
    payload = abp.build_payload(cbp.parse_doc(make_doc(["lint"])))
    for key in ("required_status_checks", "enforce_admins", "restrictions",
                "required_linear_history", "allow_force_pushes", "allow_deletions",
                "block_creations", "required_conversation_resolution"):
        assert key in payload, key


def test_diff_is_empty_when_live_matches():
    doc = cbp.parse_doc(make_doc(["lint"], strict="false"))
    live = _live(["lint"], strict=False)
    assert abp.diff(doc, live) == []


def test_diff_reports_strict_change():
    doc = cbp.parse_doc(make_doc(["lint"], strict="true"))
    lines = abp.diff(doc, _live(["lint"], strict=False))
    assert any("strict" in line and "-> true" in line for line in lines)


# -- the repository's own document ---------------------------------------------


def test_repository_document_satisfies_the_offline_check():
    """Runs against the real files, so a future edit that names a check no job
    produces fails here rather than at the next audit."""
    if not _has_yaml():
        pytest.skip("needs PyYAML")
    doc = cbp.parse_doc((REPO_ROOT / "docs" / "branch-protection.md")
                        .read_text(encoding="utf-8"))
    assert cbp.doc_problems(doc) == []
    assert cbp.offline_problems(doc, cbp.read_jobs(REPO_ROOT), REPO_ROOT) == []
    assert len(doc.checks) == 13


def test_the_document_no_longer_points_at_the_scratch_script():
    """It used to say `wdocs/bp_apply.py`, which never existed in this repo -- the
    documented way to change the gate was a dead path."""
    text = (REPO_ROOT / "docs" / "branch-protection.md").read_text(encoding="utf-8")
    assert "wdocs" not in text
    assert "scripts/apply_branch_protection.py" in text
