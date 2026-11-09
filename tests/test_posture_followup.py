"""Regression tests for the 2026-11-10 posture follow-up.

Four findings from the 2026-11-08 review were still open at v1.0.5; each test here
pins the fix so it cannot silently regress:

* state dir under the workspace  -> readable by `read_file` (had the sidecar token)
* egress allowlist               -> a bare public suffix granted the whole TLD
* persona bundle zip             -> zip-slip guard used a string prefix compare
* /v1/attachments/inspect-pdf    -> decoded an unbounded base64 body
"""

from __future__ import annotations

import io
import zipfile

import pytest

from coworker.egress import EgressPolicy, _grantable
from coworker.pdf_support import inspect as inspect_pdf
from coworker.personas.registry import PersonaRegistry
from coworker.server.run import _refuse_state_dir_inside_workspace
from coworker.tools.files import file_tools


# -- egress: a public suffix must never be grantable ---------------------------
def test_egrantable_rejects_public_suffixes_and_bare_hosts():
    assert _grantable("example.com") is True
    assert _grantable("api.example.com") is True
    assert _grantable("com") is False
    assert _grantable("co.uk") is False
    assert _grantable("localhost") is False
    assert _grantable("10.0.0.5") is False


def test_add_domain_refuses_a_public_suffix(tmp_path):
    policy = EgressPolicy(path=tmp_path / "egress.json")
    assert policy.add_domain("com") is False
    assert policy.add_domain("co.uk") is False
    # the point of the finding: one click must not allow every .com host
    assert policy.is_allowed("evil.com") is False
    assert policy.is_allowed("gov.co.uk") is False


def test_legacy_policy_file_with_a_tld_is_filtered_on_load(tmp_path):
    path = tmp_path / "egress.json"
    path.write_text(
        '{"domains": {"com": "legacy", "example.com": "ok"}}', encoding="utf-8"
    )
    policy = EgressPolicy(path=path)
    assert [d["domain"] for d in policy.list_domains()] == ["example.com"]
    assert policy.is_allowed("evil.com") is False
    assert policy.is_allowed("api.example.com") is True


def test_a_real_site_is_still_granted(tmp_path):
    policy = EgressPolicy(path=tmp_path / "egress.json")
    assert policy.add_domain("docs.python.org") is True
    assert policy.is_allowed("docs.python.org") is True


# -- persona bundle: zip-slip ------------------------------------------------
def test_zip_slip_entry_is_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escaped.md", "pwned")
    reg = PersonaRegistry(state_path=tmp_path / "personas.json")
    with pytest.raises(FileNotFoundError):
        reg.install_from_zip(buf.getvalue())


def test_zip_slip_prefix_sibling_is_rejected(tmp_path, monkeypatch):
    """The old `startswith` guard let `<root>-evil/x` through; pin the real root."""
    import tempfile

    fixed = tmp_path / "ocw-persona-zip-abc"
    fixed.mkdir()

    class _FixedTD:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return str(fixed)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(tempfile, "TemporaryDirectory", _FixedTD)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../ocw-persona-zip-abc-evil/x.md", "pwned")
    reg = PersonaRegistry(state_path=tmp_path / "personas.json")
    with pytest.raises(FileNotFoundError):
        reg.install_from_zip(buf.getvalue())
    assert not (tmp_path / "ocw-persona-zip-abc-evil").exists()


# -- pdf inspection: bounded before decode -----------------------------------
def test_inspect_pdf_rejects_an_oversized_data_url():
    huge = "data:application/pdf;base64," + "A" * 17_000_000
    out = inspect_pdf(huge)
    assert out["ok"] is False
    assert "too large" in out["error"]


def test_inspect_pdf_still_rejects_a_non_pdf_url():
    out = inspect_pdf("data:image/png;base64,AAAA")
    assert out["ok"] is False


# -- read_file: never reads OpenWorker's own state dir ------------------------
def test_read_file_refuses_the_state_dir(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    state = ws / ".coworker-state"
    state.mkdir(parents=True)
    (state / "secrets.json").write_text('{"token": "x"}', encoding="utf-8")
    monkeypatch.setenv("COWORKER_STATE_DIR", str(state))

    read_file = file_tools(str(ws))[0]
    out = read_file(path=".coworker-state/secrets.json")
    assert "error" in out
    assert "state directory" in out["error"]


def test_read_file_still_reads_normal_workspace_files(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / ".coworker-state").mkdir(parents=True)
    (ws / "notes.md").write_text("hello\n", encoding="utf-8")
    monkeypatch.setenv("COWORKER_STATE_DIR", str(ws / ".coworker-state"))

    read_file = file_tools(str(ws))[0]
    out = read_file(path="notes.md")
    assert out["total_lines"] == 1


# -- server startup refuses the dangerous layout -----------------------------
def test_server_refuses_a_state_dir_inside_the_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    state = ws / ".coworker-state"
    state.mkdir(parents=True)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(state))
    with pytest.raises(SystemExit) as exc:
        _refuse_state_dir_inside_workspace(str(ws))
    assert "inside the workspace" in str(exc.value)


def test_server_allows_a_state_dir_outside_the_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "elsewhere"))
    _refuse_state_dir_inside_workspace(str(ws))  # must not raise
