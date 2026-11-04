"""minisign-compatible signing + install verification (personas/signing.py,
distribution.py, and the registry wiring)."""

import base64
import json
from pathlib import Path

import pytest

from coworker.personas import signing
from coworker.personas.distribution import (
    CatalogError,
    TrustedKeyStore,
    catalog_entry,
    fetch_catalog,
)


@pytest.fixture()
def bundle(tmp_path: Path) -> Path:
    d = tmp_path / "bundle"
    (d / "skills" / "semgrep-review").mkdir(parents=True)
    (d / "persona-security.md").write_text(
        "---\nid: security\nname: Security\nversion: 1.0\n---\nYou are security.",
        encoding="utf-8",
    )
    (d / "skills" / "semgrep-review" / "SKILL.md").write_text(
        "---\nname: semgrep-review\n---\nRun semgrep.", encoding="utf-8"
    )
    return d


@pytest.fixture()
def keypair(tmp_path: Path) -> signing.KeyPair:
    return signing.generate_keypair(tmp_path / "keys", "hunter2")


# -- crypto round trip ---------------------------------------------------------


def test_sign_and_verify_roundtrip(bundle, keypair):
    result = signing.sign_directory(bundle, keypair.secret_key, "hunter2")
    assert result["ok"] and result["valid"]
    check = signing.verify_directory(bundle, public_key=keypair.public_key)
    assert check["signed"] and check["valid"] and check["files_match"]
    assert check["key_id"] == keypair.key_id


def test_tampered_file_detected(bundle, keypair):
    signing.sign_directory(bundle, keypair.secret_key, "hunter2")
    (bundle / "skills" / "semgrep-review" / "SKILL.md").write_text(
        "RUN EVIL", encoding="utf-8"
    )
    check = signing.verify_directory(bundle, public_key=keypair.public_key)
    assert check["signed"] and not check["valid"]
    assert "manifest" in check["error"]


def test_extra_file_detected(bundle, keypair):
    signing.sign_directory(bundle, keypair.secret_key, "hunter2")
    (bundle / "sneaky.md").write_text("extra", encoding="utf-8")
    check = signing.verify_directory(bundle, public_key=keypair.public_key)
    assert check["signed"] and not check["valid"]


def test_wrong_key_rejected(bundle, keypair, tmp_path):
    other = signing.generate_keypair(tmp_path / "other", "pw")
    signing.sign_directory(bundle, keypair.secret_key, "hunter2")
    check = signing.verify_directory(bundle, public_key=other.public_key)
    assert check["signed"] and not check["valid"]
    assert check["error"] == "signature mismatch"


def test_wrong_password_refused(bundle, keypair):
    with pytest.raises(Exception):
        signing.sign_directory(bundle, keypair.secret_key, "WRONG")


def test_unsigned_bundle_is_a_fact_not_an_error(bundle):
    check = signing.verify_directory(bundle)
    assert check == {"signed": False}


def test_signature_file_format_is_minisign_shaped(bundle, keypair):
    """Line 1 untrusted comment; line 2 base64('Ed' + 64-byte sig); line 3
    trusted comment; line 4 base64('Ed' + 64-byte global sig) — what the real
    minisign CLI parses."""
    signing.sign_directory(bundle, keypair.secret_key, "hunter2")
    lines = (bundle / (signing.MANIFEST_NAME + signing.SIG_SUFFIX)).read_text(
        encoding="ascii"
    ).splitlines()
    assert lines[0].startswith("untrusted comment: signature from ")
    blob = base64.b64decode(lines[1])
    assert blob[:2] == b"Ed" and len(blob) == 66
    assert lines[2].startswith("trusted comment: ")
    global_blob = base64.b64decode(lines[3])
    assert global_blob[:2] == b"Ed" and len(global_blob) == 66


def test_public_key_file_is_minisign_shaped(keypair):
    """A real minisign public key is base64('Ed' + 32-byte pk) — byte-identical
    layout, so keys interop on the verify side."""
    text = Path(keypair.public_key).read_text(encoding="ascii")
    lines = text.splitlines()
    assert lines[0].startswith("untrusted comment: minisign public key ")
    blob = base64.b64decode(lines[1])
    assert blob[:2] == b"Ed" and len(blob) == 34


def test_pinned_key_mismatch_reported(bundle, keypair, tmp_path):
    """verify_directory with a pinned key that doesn't match: valid=False and
    pinned=False — the registry turns that into a refused install."""
    other = signing.generate_keypair(tmp_path / "other", "pw")
    signing.sign_directory(bundle, keypair.secret_key, "hunter2")
    check = signing.verify_directory(bundle, public_key=other.public_key)
    assert check["pinned"] is False and check["valid"] is False


# -- registry install wiring -----------------------------------------------------


def _make_registry(tmp_path: Path):
    from coworker.personas.registry import PersonaRegistry

    return PersonaRegistry(state_path=tmp_path / "registry" / "state.json")


def test_install_signed_bundle_carries_signature_fact(tmp_path, bundle, keypair):
    signing.sign_directory(bundle, keypair.secret_key, "hunter2")
    reg = _make_registry(tmp_path)
    summaries = reg.install_from_dir(bundle, expected_pubkey=Path(keypair.public_key).read_text("ascii"))
    assert summaries[0]["signature"]["valid"] is True
    assert summaries[0]["signature"]["pinned"] is True


def test_pinned_install_refuses_unsigned(tmp_path, bundle):
    reg = _make_registry(tmp_path)
    summaries = reg.install_from_dir(
        bundle, expected_pubkey="untrusted comment: x\nRWQ=\n"
    )
    assert summaries[0]["ok"] is False
    assert "refusing" in summaries[0]["error"]


def test_manual_install_reports_unsigned(tmp_path, bundle):
    reg = _make_registry(tmp_path)
    summaries = reg.install_from_dir(bundle)
    assert summaries[0]["signature"] == {"signed": False}


# -- trusted keys ---------------------------------------------------------------


def test_trusted_key_store_roundtrip(tmp_path, keypair):
    store = TrustedKeyStore(tmp_path / "state")
    assert store.list() == []
    added = store.add(keypair.public_key, "community author")
    assert added["key_id"] == keypair.key_id
    assert store.is_trusted(keypair.key_id)
    assert store.list()[0]["label"] == "community author"
    assert "pubkey" not in store.list()[0]  # public keys never echo back
    assert store.remove(keypair.key_id) == {"ok": True}
    assert not store.is_trusted(keypair.key_id)


def test_trusted_key_decorates_signature(tmp_path, keypair):
    store = TrustedKeyStore(tmp_path / "state")
    store.add(keypair.public_key)
    bundle = tmp_path / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "p.md").write_text("---\nid: x\nname: X\nversion: 1\n---\nhi", encoding="utf-8")
    signing.sign_directory(bundle, keypair.secret_key, "hunter2")
    check = signing.verify_directory(bundle, public_key=keypair.public_key)
    decorated = store.decorate(check)
    assert decorated["trusted"] is True
    # An untrusted key's signature just lacks the flag — a fact, not an approval.
    other = signing.generate_keypair(tmp_path / "other", "pw")
    other_check = signing.verify_directory(bundle, public_key=other.public_key)
    assert other_check["valid"] is False  # wrong key for THIS signature
    assert "trusted" not in store.decorate(other_check) or not other_check.get("trusted")


# -- catalog ---------------------------------------------------------------------


CATALOG_JSON = json.dumps(
    {
        "name": "Community personas",
        "updated": "2026-10-01",
        "personas": [
            {
                "slug": "security-pro",
                "name": "Security Pro",
                "git_url": "https://github.com/acme/security-pro.git",
                "pubkey": "untrusted comment: minisign public key 0\nRWQ=\n",
                "author": "acme",
            },
            {"slug": "unpinnable", "name": "No key"},  # dropped: no git_url/pubkey
        ],
    }
)


def test_fetch_catalog_drops_unpinned_entries(tmp_path):
    data = fetch_catalog(
        "https://example.com/catalog.json",
        tmp_path / "cache.json",
        fetcher=lambda url: CATALOG_JSON,
        now=lambda: 1000.0,
    )
    assert data["name"] == "Community personas"
    slugs = [p["slug"] for p in data["personas"]]
    assert slugs == ["security-pro"]
    assert catalog_entry(data, "security-pro")["pubkey"].startswith("untrusted comment")


def test_fetch_catalog_caches(tmp_path):
    calls = []
    url = "https://example.com/catalog.json"
    fetcher = lambda u: (calls.append(u), CATALOG_JSON)[1]  # noqa: E731
    fetch_catalog(url, tmp_path / "cache.json", fetcher=fetcher, now=lambda: 1000.0)
    fetch_catalog(url, tmp_path / "cache.json", fetcher=fetcher, now=lambda: 1100.0)
    assert len(calls) == 1  # served from cache within TTL
    _data = fetch_catalog(url, tmp_path / "cache.json", fetcher=fetcher, now=lambda: 9_999_999.0)
    assert len(calls) == 2  # TTL expired → refetched


def test_fetch_catalog_stale_cache_on_failure(tmp_path):
    url = "https://example.com/catalog.json"
    good = fetch_catalog(url, tmp_path / "cache.json", fetcher=lambda u: CATALOG_JSON, now=lambda: 1000.0)
    def broken(u):
        raise OSError("network down")
    data = fetch_catalog(url, tmp_path / "cache.json", fetcher=broken, now=lambda: 9_999_999.0)
    assert data.pop("stale") is True
    assert data == good
    with pytest.raises(CatalogError):
        fetch_catalog(url, tmp_path / "no-cache.json", fetcher=broken)


def test_empty_url_is_a_configuration_fact(tmp_path):
    with pytest.raises(CatalogError):
        fetch_catalog("", tmp_path / "cache.json")
