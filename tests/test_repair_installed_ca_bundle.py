"""Tests for scripts/repair_installed_ca_bundle.py.

The claim under test is narrow and has to be proven twice over:

  1. the script's verdict flips when the CA is absent vs present (a check that
     passes on both would be decoration -- AGENTS §10), and
  2. the bundle it produces actually completes a TLS handshake against a server
     whose chain ends in that CA, while the untouched bundle does not.

The second one is the point. A string marker in a PEM file proves nothing; the
original bug was precisely a bundle that looked complete and still failed with
`unable to get local issuer certificate`.
"""

from __future__ import annotations

import importlib.util
import http.server
import socket
import ssl
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "repair_installed_ca_bundle.py"

# The script under test is a Windows-install repair tool: its CA source is the
# Windows cert store (`ssl.enum_certificates`, a Windows-only API) and its probe
# targets are Windows install paths. There is nothing to test on other platforms.
pytestmark = pytest.mark.skipif(
    not hasattr(ssl, "enum_certificates"),
    reason="repair script reads the Windows cert store (Windows-only API)",
)


def _load_script():
    spec = importlib.util.spec_from_file_location("repair_ca_bundle", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------- fixtures
def _make_ca():
    """(cert, key) for a CA whose subject matches the script's search hint."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    import datetime

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OpenWorker-Internal-CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        # OpenSSL refuses to use a CA as a signer without keyCertSign (verify_code 92),
        # so the fixture must carry the real KeyUsage a real CA has.
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    der = cert.public_bytes(serialization.Encoding.DER)
    return cert, key, der


def _make_leaf(ca_cert, ca_key, hostname="localhost"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    import datetime

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        # Real leaves always carry these two; without the AKI, OpenSSL reports
        # "Missing Authority Key Identifier" (verify_code 85) instead of the chain
        # error under test, which would mask the A/B this fixture exists to prove.
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib naming
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # silence the test output
        pass


def _serve_tls(cert_pem, key_pem):
    """Run a one-shot TLS server on localhost; returns (port, stop_callable)."""
    import tempfile

    d = Path(tempfile.mkdtemp())
    cert_file, key_file = d / "cert.pem", d / "key.pem"
    cert_file.write_bytes(cert_pem)
    key_file.write_bytes(key_pem)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_file), str(key_file))

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def stop():
        httpd.shutdown()
        httpd.server_close()

    return port, stop


def _handshake(port, cafile):
    """True if a client trusting only `cafile` can talk to the server."""
    ctx = ssl.create_default_context(cafile=str(cafile))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with ctx.wrap_socket(raw, server_hostname="localhost") as tls:
                return tls.version() is not None
    except ssl.SSLError:
        return False


@pytest.fixture()
def bundle_under_test(tmp_path, monkeypatch):
    """A certifi-only bundle, with the script's Windows-store lookup pointed at a fake CA."""
    pytest.importorskip("cryptography")
    import certifi

    module = _load_script()
    bundle = tmp_path / "cacert.pem"
    bundle.write_bytes(Path(certifi.where()).read_bytes())

    ca_cert, ca_key, ca_der = _make_ca()

    # The script reads the OS store; feed it the generated CA instead of this machine's.
    def fake_enum(store):
        return [(ca_der, "x509_asn", True)] if store == "CA" else []

    monkeypatch.setattr(module.ssl, "enum_certificates", fake_enum)
    return module, bundle, ca_cert, ca_key


# --------------------------------------------------------------- the verdict
def test_check_reports_repair_needed_on_the_shipped_bundle(bundle_under_test, capsys):
    """The unpatched bundle must come back as NEEDS REPAIR.

    Run in reverse this is the whole point: if this test passed on a patched bundle
    too, `--check` could not distinguish the broken state from the fixed one.
    """
    module, bundle, _ca_cert, _ca_key = bundle_under_test
    rc = module.main(["--check", "--bundle", str(bundle)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "NEEDS REPAIR" in out
    assert "internal CA trusted: False" in out


def test_apply_then_check_is_clean(bundle_under_test, capsys):
    module, bundle, _ca_cert, _ca_key = bundle_under_test
    assert module.main(["--apply", "--bundle", str(bundle)]) == 0

    before = bundle.stat().st_size
    assert module.main(["--check", "--bundle", str(bundle)]) == 0
    out = capsys.readouterr().out
    assert "RESULT=OK" in out
    assert "internal CA trusted: True" in out
    assert bundle.stat().st_size == before, "check must not modify the bundle"


def test_apply_is_idempotent(bundle_under_test, capsys):
    module, bundle, _ca_cert, _ca_key = bundle_under_test
    assert module.main(["--apply", "--bundle", str(bundle)]) == 0
    first = bundle.read_bytes()
    assert module.main(["--apply", "--bundle", str(bundle)]) == 0
    assert bundle.read_bytes() == first, "a second run must not append the CA again"
    assert capsys.readouterr().out.count("ALREADY CORRECT") == 1


def test_apply_backs_up_the_original_and_the_backup_is_byte_identical(
    bundle_under_test, tmp_path
):
    module, bundle, _ca_cert, _ca_key = bundle_under_test
    original = bundle.read_bytes()
    assert module.main(["--apply", "--bundle", str(bundle)]) == 0

    backups = list((tmp_path / "_openworker_ca_backups").glob("cacert.pem.*.bak"))
    assert len(backups) == 1, "exactly one backup should be written"
    assert backups[0].read_bytes() == original


# --------------------------------------------------------------- the real proof
def test_patched_bundle_completes_the_handshake_the_original_cannot(
    bundle_under_test, capsys
):
    """A/B against a live TLS server -- the check the bug actually calls for.

    The leaf is issued by the generated internal CA. certifi alone must fail with
    verify error 20 (exactly the user's symptom); after --apply the same handshake
    must succeed. Only then has the patch been shown to do anything.
    """
    module, bundle, ca_cert, ca_key = bundle_under_test
    cert_pem, key_pem = _make_leaf(ca_cert, ca_key)
    port, stop = _serve_tls(cert_pem, key_pem)
    try:
        assert _handshake(port, bundle) is False, (
            "the untouched bundle must NOT verify this chain; if it does, the "
            "fixture is not reproducing the bug and the test proves nothing"
        )
        assert module.main(["--apply", "--bundle", str(bundle)]) == 0
        assert _handshake(port, bundle) is True, (
            "the patched bundle must verify the chain"
        )
    finally:
        stop()


def test_failed_append_restores_the_backup(bundle_under_test, monkeypatch, capsys):
    """If the rewritten bundle no longer parses, the original comes back.

    A repair that can brick the trust store is worse than the bug: this forces the
    post-write assertion to fail and checks the bundle is byte-identical afterwards.
    """
    module, bundle, _ca_cert, _ca_key = bundle_under_test
    original = bundle.read_bytes()

    real_anchor_count = module.anchor_count
    calls = {"n": 0}

    def flaky_anchor_count(path):
        calls["n"] += 1
        return -1 if calls["n"] >= 2 else real_anchor_count(path)

    monkeypatch.setattr(module, "anchor_count", flaky_anchor_count)
    rc = module.main(["--apply", "--bundle", str(bundle)])
    assert rc == 2
    assert "RESULT=FAILED" in capsys.readouterr().out
    assert bundle.read_bytes() == original, "the original bundle must be restored"


def test_missing_bundle_is_refused_not_created(tmp_path, capsys):
    module = _load_script()
    missing = tmp_path / "nope" / "cacert.pem"
    rc = module.main(["--check", "--bundle", str(missing)])
    assert rc == 2
    assert not missing.exists()


def test_absent_ca_is_an_error_not_a_silent_no_op(bundle_under_test):
    """On a machine without the internal CA, --apply must stop rather than 'succeed'."""
    module, bundle, _ca_cert, _ca_key = bundle_under_test
    original = bundle.read_bytes()
    module.ssl.enum_certificates = lambda store: []  # type: ignore[assignment]
    with pytest.raises(SystemExit) as exc:
        module.main(["--apply", "--bundle", str(bundle)])
    assert "no certificate matching" in str(exc.value)
    assert bundle.read_bytes() == original