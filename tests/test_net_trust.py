"""Regression tests for the TLS trust anchor fix (see `coworker/net_trust.py`).

The bug these pin down, in the shape it actually appeared:

    private CA installed in the Windows `CA` store only
    -> server/run.py sets SSL_CERT_FILE=certifi
    -> httpx calls create_default_context(cafile=certifi)
    -> CPython skips load_default_certs() when a cafile is passed
    -> OS store never consulted -> CERTIFICATE_VERIFY_FAILED (code 20)
    -> OpenAI SDK wraps it as the useless "Connection error."

Each test is written to fail against the pre-fix behaviour; the reverse verification
(revert the fix, watch it go red) is recorded in the PR description per AGENTS §10.
"""

from __future__ import annotations

import datetime
import http.server
import socket
import ssl
import threading

import pytest

from coworker import net_trust

SERVER_AUTH = "1.3.6.1.5.5.7.3.1"


@pytest.fixture(autouse=True)
def _reset_cache():
    """trust_context() caches per process; isolate each test's view."""
    net_trust._cached = None
    net_trust._cache_key = None
    yield
    net_trust._cached = None
    net_trust._cache_key = None


def _common_names(ctx) -> set[str]:
    """Common names from an SSLContext's anchors.

    `get_ca_certs()` shapes `subject` as a tuple of RDNs, each a tuple of
    (key, value) pairs — i.e. ((('countryName', 'SI'),), (('commonName', 'X'),)).
    """
    names = set()
    for cert in ctx.get_ca_certs():
        for rdn in cert.get("subject", ()):
            for key, value in rdn:
                if key == "commonName":
                    names.add(value)
    return names


# -- proof the merge is load-bearing ------------------------------------------


def _make_chain():
    """A private root CA (serverAuth EKU) plus a leaf for localhost.

    SKI/AKI are required: without them OpenSSL 3.x refuses the chain with
    'Missing Authority Key Identifier' (verify_code 85) regardless of how the
    anchors were loaded — which would make the test meaningless.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OpenWorker-Internal-CA")])
    root = (
        x509.CertificateBuilder()
        .subject_name(root_name).issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()),
                       critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(root_key, hashes.SHA256())
    )

    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(root_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_encipherment=True,
                content_commitment=False, key_cert_sign=False, crl_sign=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
                       critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),
            critical=False,
        )
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(root_key, hashes.SHA256())
    )
    return (
        root.public_bytes(serialization.Encoding.DER),
        leaf.public_bytes(serialization.Encoding.PEM),
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


@pytest.fixture
def private_ca_server(tmp_path, monkeypatch):
    """Serve TLS from a chain rooted in a CA that only the OS-store API can see.

    Returns a callable: `handshake(ctx) -> None` raising on verification failure.

    The CA is injected via `ssl.enum_certificates`, a Windows-only API. On other
    platforms there is no OS store the merge could read and no way to inject the
    root, so the scenario is Windows-only by design: skip everywhere else.
    """
    if not hasattr(ssl, "enum_certificates"):
        pytest.skip("OS-store injection is Windows-only")
    pytest.importorskip("cryptography")
    root_der, leaf_pem, leaf_key_pem = _make_chain()

    leaf_path = tmp_path / "leaf.pem"
    key_path = tmp_path / "leaf.key"
    leaf_path.write_bytes(leaf_pem)
    key_path.write_bytes(leaf_key_pem)

    httpd = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    port = httpd.server_address[1]
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(leaf_path), str(key_path))
    httpd.socket = server_ctx.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    real_enum = getattr(ssl, "enum_certificates", None)
    if real_enum is not None:

        def fake_enum(store):
            # The private CA is installed into the `CA` (intermediate) store only —
            # exactly the layout that produced the outage.
            if store == "CA":
                return [(root_der, "x509_asn", True)]
            return []

        monkeypatch.setattr(ssl, "enum_certificates", fake_enum)

    def handshake(ctx):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with ctx.wrap_socket(raw, server_hostname="localhost"):
                return True

    try:
        yield handshake
    finally:
        httpd.shutdown()


def test_merge_trusts_a_ca_that_only_the_os_store_knows(private_ca_server):
    """The whole point. Red before the fix: the old code loaded certifi only."""
    import certifi

    old_style = ssl.create_default_context(cafile=certifi.where())
    with pytest.raises(ssl.SSLCertVerificationError) as err:
        private_ca_server(old_style)
    assert err.value.verify_code == 20, (
        "expected 'unable to get local issuer certificate' — the production symptom"
    )

    assert private_ca_server(net_trust.trust_context()) is True


def test_merged_context_does_not_trust_an_unknown_ca(private_ca_server, monkeypatch):
    """Merging must not become 'trust everything': hide the CA again and it fails."""
    monkeypatch.undo()
    monkeypatch.setattr(ssl, "enum_certificates", lambda store: [])
    net_trust._cached = None
    net_trust._cache_key = None

    with pytest.raises(ssl.SSLCertVerificationError) as err:
        private_ca_server(net_trust.trust_context())
    assert err.value.verify_code == 20


def test_merged_context_keeps_certifi_roots():
    """certifi's roots must survive the merge — dropping them would break public hosts."""
    import certifi

    certifi_names = _common_names(ssl.create_default_context(cafile=certifi.where()))
    merged_names = _common_names(net_trust.trust_context())
    assert certifi_names, "fixture problem: certifi bundle had no common names"
    assert certifi_names <= merged_names, (
        f"merged context lost {len(certifi_names - merged_names)} certifi roots"
    )


@pytest.mark.skipif(not hasattr(ssl, "enum_certificates"), reason="Windows-only API")
def test_certifi_adds_roots_the_windows_store_lacks():
    """Guards the reasoning, not just the code: if the store were a superset of
    certifi, the merge would be pointless and the module comment would be a lie.

    Measured on this machine: 84 of certifi's 118 roots are absent from the store by
    DER, 78 of them with no same-named cert either.
    """
    import certifi

    store_ders = set()
    for store in ("ROOT", "CA"):
        for der, encoding, _trust in ssl.enum_certificates(store):
            if encoding == "x509_asn" and der:
                store_ders.add(der)

    with open(certifi.where(), "r", encoding="ascii", errors="ignore") as fh:
        blocks = net_trust._pem_blocks(fh.read())
    certifi_ders = {ssl.PEM_cert_to_DER_cert(b) for b in blocks}

    missing = certifi_ders - store_ders
    assert missing, (
        "the Windows store already has every certifi root - if the store is a "
        "superset of certifi, the merge is a no-op and net_trust's 'union' is a lie"
    )
    # The count is machine-dependent: measured 84 on the dev machine, 14 on a
    # GitHub-hosted Windows runner image. Only the _existence_ of a gap is the
    # load-bearing fact - the merge is pointless iff the store equals certifi.


# -- filters and loading mechanism --------------------------------------------


@pytest.mark.skipif(not hasattr(ssl, "enum_certificates"), reason="Windows-only API")
def test_store_filter_matches_cpython_server_auth_rule():
    """We must apply the same EKU filter CPython does; otherwise code-signing or
    client-only certs would be promoted to TLS anchors."""
    checked = 0
    for store in ("ROOT", "CA"):
        for _der, encoding, trust in ssl.enum_certificates(store):
            if encoding != "x509_asn":
                continue
            checked += 1
            if trust is True:
                assert net_trust._trusted_for_server_auth(trust) is True
            elif isinstance(trust, frozenset):
                expected = not trust or SERVER_AUTH in trust
                assert net_trust._trusted_for_server_auth(trust) is expected
    assert checked > 0, "no store entries — the filter was never exercised"


def test_filter_rejects_non_server_auth_eku():
    code_signing = frozenset({"1.3.6.1.4.1.311.10.3.4"})
    assert net_trust._trusted_for_server_auth(code_signing) is False
    assert net_trust._trusted_for_server_auth(frozenset({SERVER_AUTH})) is True
    assert net_trust._trusted_for_server_auth(frozenset()) is True  # no EKU = unrestricted
    assert net_trust._trusted_for_server_auth(True) is True
    assert net_trust._trusted_for_server_auth(None) is False


def test_context_is_built_once_and_reused():
    """Providers rebuild clients on token rotation; rebuilding the context each time
    would add ~200 ms per rotation."""
    first = net_trust.trust_context()
    assert net_trust.trust_context() is first
    assert net_trust.trust_context(refresh=True) is not first, "refresh must rebuild"


def test_merged_pem_is_deduped_against_certifi():
    """Overlapping roots would only inflate the bundle the context must parse."""
    only_certifi = net_trust._pem_blocks(net_trust.merged_pem(include_os_store=False))
    ders = [ssl.PEM_cert_to_DER_cert(b) for b in only_certifi]
    assert len(ders) == len(set(ders))

    full = net_trust._pem_blocks(net_trust.merged_pem())
    full_ders = [ssl.PEM_cert_to_DER_cert(b) for b in full]
    assert len(full_ders) == len(set(full_ders)), "a certificate appears twice"


def test_context_build_stays_on_the_cadata_path():
    """Pins the mechanism by its measured cost, since a switch to `cafile=` is a
    silent multi-second startup tax: 447 certs / 779 KB took 2791 ms via cafile vs
    194 ms via cadata on this machine. 1200 ms separates them with slack for a
    contended runner."""
    import time

    net_trust.trust_context(refresh=True)  # warm imports/file cache

    start = time.perf_counter()
    net_trust.trust_context(refresh=True)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(net_trust.trust_context().get_ca_certs()) > 100
    assert elapsed_ms < 1200, f"context build took {elapsed_ms:.0f} ms — cafile regression?"


def test_trust_client_keeps_sdk_timeouts():
    """A bare httpx.Client() defaults to Timeout(5) and would cut the SDK's 600 s
    read timeout to 5 s for long generations."""
    client = net_trust.trust_client()
    try:
        assert client.timeout.read == 600.0, (
            f"read timeout is {client.timeout.read}; expected the SDK default 600 s"
        )
        assert client.timeout.connect == 5.0
    finally:
        client.close()


# -- wiring: every outbound model client uses the shared context --------------


def test_openai_provider_injects_trust_client(monkeypatch):
    """Chat Completions path — every OpenAI-compatible vendor, vLLM, Ollama."""
    import openai as openai_mod

    from coworker.providers.openai_provider import OpenAIProvider

    captured = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai_mod, "OpenAI", _FakeOpenAI)
    provider = OpenAIProvider(api_key="k", base_url="https://internal.example/v1")
    provider._ensure_client()

    assert "http_client" in captured, (
        "no http_client -> the SDK falls back to SSL_CERT_FILE (the original bug)"
    )


def test_openai_responses_provider_injects_trust_client(monkeypatch):
    """Responses path — native OpenAI models."""
    import openai as openai_mod

    from coworker.providers.openai_responses import OpenAIResponsesProvider

    captured = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai_mod, "OpenAI", _FakeOpenAI)
    OpenAIResponsesProvider(api_key="k")._ensure_client()
    assert "http_client" in captured


def test_codex_provider_injects_trust_client(monkeypatch):
    """Codex OAuth path rebuilds its client on every token rotation."""
    import openai as openai_mod

    from coworker.providers.codex_provider import CodexProvider

    captured = []

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    class _Secrets(dict):
        def get(self, key, default=None):
            return {"tokens": {"access_token": "tok", "account_id": "acct"}}

        def put(self, key, value):
            pass

    monkeypatch.setattr(openai_mod, "OpenAI", _FakeOpenAI)
    provider = CodexProvider(secrets=_Secrets())
    provider._ensure_client()

    assert captured and "http_client" in captured[0]


def test_verify_provider_key_uses_trust_context(monkeypatch):
    """Settings' "Test" button is a plain httpx.get and failed on the same
    private-CA endpoint even after the provider clients were fixed."""
    import httpx

    from coworker.providers import registry

    seen = {}

    class _Resp:
        status_code = 200

    def _fake_get(url, **kwargs):
        seen.update(kwargs, url=url)
        return _Resp()

    monkeypatch.setattr(httpx, "get", _fake_get)
    result = registry.verify_provider_key("openai", api_key="k", base_url="https://x/v1")

    assert result["ok"] is True
    assert isinstance(seen.get("verify"), ssl.SSLContext), (
        "Settings Test still uses the certifi-only default store"
    )


# -- platform gating ---------------------------------------------------------


def test_ensure_ca_bundle_leaves_windows_alone(monkeypatch):
    """The old code set SSL_CERT_FILE=certifi on Windows — the exact step that hid the
    machine's private CA. Reinstating it would silently reintroduce the outage."""
    import os
    import sys

    from coworker.server import run

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    run._ensure_ca_bundle()
    assert os.environ.get("SSL_CERT_FILE") is None


def test_ensure_ca_bundle_still_helps_macos(monkeypatch):
    """aiohttp on macOS framework Python has no CAs at all; that path must survive."""
    import os
    import sys

    from coworker.server import run

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    run._ensure_ca_bundle()
    assert os.environ.get("SSL_CERT_FILE")


def test_ensure_ca_bundle_respects_existing_override(monkeypatch):
    import os
    import sys

    from coworker.server import run

    monkeypatch.setenv("SSL_CERT_FILE", "C:/custom/bundle.pem")
    monkeypatch.setattr(sys, "platform", "darwin")
    run._ensure_ca_bundle()
    assert os.environ["SSL_CERT_FILE"] == "C:/custom/bundle.pem"
