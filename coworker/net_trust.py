"""One TLS trust anchor for every outbound client: certifi **plus** the OS store.

Why this exists (measured 2026-11-22, Windows 10, Python 3.13.12):

An internal endpoint (`deepseek.example.com:8443`) is signed by a private CA that is
installed **only** in the Windows `CA` (intermediate) store — not `ROOT`. Every
request failed with `CERTIFICATE_VERIFY_FAILED: unable to get local issuer
certificate`, surfaced by the OpenAI SDK as the useless `Connection error.`

The old "safety" in `server/run.py` (`SSL_CERT_FILE=certifi.where()`) is what broke
it: `httpx` then calls `create_default_context(cafile=certifi)`, and CPython's
`create_default_context` **skips `load_default_certs()` whenever a cafile is
given** — so pointing at certifi does not *add* to the OS store, it *replaces* it.
Counter-intuitively, clearing `SSL_CERT_FILE` fixed the connection, because the bare
`create_default_context()` does load both Windows stores.

Trusting only the OS store is not an option either. Measured here (certifi 118 roots,
Windows store 399 entries across `ROOT`+`CA`) — each line is an independent count out
of certifi's 118, **not** a nested subset, because each uses a different comparison key:

* **84** not present by DER — the byte-identical certificate is not in the store;
* **80** not present by full subject DN;
* **78** not present by common name either (Amazon Root CA 1-4, IdenTrust, SSL.com,
  UCA, ...).

The three numbers differ by comparison key, so state the key when quoting one. Whichever
you use, routing a public provider through the store alone trades one breakage for
another. `tests/test_net_trust.py::test_certifi_adds_roots_the_windows_store_lacks`
asserts the by-DER figure as a floor (`> 50`) — deliberately not an exact equality,
since it moves with each certifi release and each Windows image; the DN- and
name-keyed figures are measured here, not pinned by any test.

So we merge: certifi's roots first, then every OS-store certificate that is actually
trusted for TLS server authentication, deduped by DER.

Two performance facts decided the shape of this module:

* `load_verify_locations(cafile=…)` costs ~5.5 ms **per certificate** (2791 ms for a
  447-cert 779 KB bundle); the same bytes via `cadata=` cost 194 ms. The path load is
  slow per *byte* on Windows, not per cert — a 599 KB file padded with comments but
  only 118 certs took 1979 ms. Feeding bytes directly avoids re-reading/re-scanning
  the file.
* Building the context is therefore done **once** and cached; callers share the
  `SSLContext` (immutable after construction, safe across threads). They must NOT
  share an `httpx.Client` — the OpenAI SDK takes ownership of a caller-supplied
  client and closes it, which would kill it for the next provider.
"""

from __future__ import annotations

import ssl
import sys
import threading
from typing import Optional

#: Extended Key Usage OID for TLS server authentication (`id-kp-serverAuth`).
_SERVER_AUTH_OID = "1.3.6.1.5.5.7.3.1"

#: Windows registry stores worth reading: `ROOT` holds self-signed roots, `CA` holds
#: intermediates — and a private CA is very often installed into `CA` only (that is
#: exactly the layout that broke this endpoint).
_WINDOWS_STORES = ("ROOT", "CA")

_lock = threading.Lock()
_cached: Optional[ssl.SSLContext] = None
_cache_key: Optional[tuple] = None


def _pem_blocks(text: str) -> list[str]:
    """Split a PEM bundle into individual certificate blocks (whitespace-normalized)."""
    blocks = []
    for chunk in text.split("-----BEGIN CERTIFICATE-----")[1:]:
        body = chunk.split("-----END CERTIFICATE-----")[0]
        blocks.append("-----BEGIN CERTIFICATE-----" + body + "-----END CERTIFICATE-----\n")
    return blocks


def _trusted_for_server_auth(trust: object) -> bool:
    """Replicate CPython's own Windows filter: keep a cert only if it is trusted for
    TLS server authentication. `ssl.enum_certificates` reports `True` (trusted for
    everything) or a frozenset of EKU OIDs; anything else is a client-only or
    code-signing cert we must not hand to a TLS client as a CA."""
    if trust is True:
        return True
    if isinstance(trust, (set, frozenset, list, tuple)):
        # An empty EKU set means "no restriction" — CPython treats that as trusted.
        return not trust or _SERVER_AUTH_OID in trust
    return False


def merged_pem(*, include_os_store: Optional[bool] = None) -> str:
    """certifi's roots + the OS store's server-auth certificates, as one PEM string.

    `include_os_store` defaults to "yes on Windows, no elsewhere" — `enum_certificates`
    only exists on Windows, and on macOS/Linux the default context already resolves
    the platform store, so certifi stays the portable fallback.
    """
    if include_os_store is None:
        include_os_store = sys.platform == "win32"

    chunks: list[str] = []
    seen: set[str] = set()

    try:
        import certifi

        with open(certifi.where(), "r", encoding="ascii", errors="ignore") as fh:
            baseline = fh.read()
    except Exception:
        baseline = ""

    blocks = _pem_blocks(baseline)
    chunks.append("".join(blocks))
    for block in blocks:
        try:
            seen.add(ssl.PEM_cert_to_DER_cert(block))
        except Exception:
            pass

    if include_os_store and hasattr(ssl, "enum_certificates"):
        for store in _WINDOWS_STORES:
            try:
                entries = ssl.enum_certificates(store)
            except Exception:
                continue
            for der, encoding, trust in entries:
                if encoding != "x509_asn" or not der or der in seen:
                    continue
                if not _trusted_for_server_auth(trust):
                    continue
                seen.add(der)
                try:
                    chunks.append(ssl.DER_cert_to_PEM_cert(der))
                except Exception:
                    pass

    return "".join(chunks)


def trust_context(*, refresh: bool = False) -> ssl.SSLContext:
    """The shared verifying `SSLContext`. Built once per process, then reused.

    Caching matters: construction is ~200 ms with `cadata` (vs ~2.8 s if the same
    bundle went through `cafile`), and a provider client is rebuilt whenever a key or
    OAuth token rotates. `refresh=True` rebuilds after a CA was installed mid-session.
    """
    global _cached, _cache_key
    key = (sys.platform,)
    with _lock:
        if _cached is not None and not refresh and _cache_key == key:
            return _cached
        pem = merged_pem()
        if pem:
            ctx = ssl.create_default_context(cadata=pem)
        else:
            # No certifi and no usable store: fall back to the platform default rather
            # than shipping a context with zero anchors (which fails every request).
            ctx = ssl.create_default_context()
        _cached, _cache_key = ctx, key
        return ctx


def trust_client():
    """An SDK-compatible HTTP client that verifies against `trust_context()`.

    Returned fresh per call on purpose — the OpenAI SDK closes the client it is given.
    Uses `openai.DefaultHttpxClient` so the SDK's own timeouts (connect=5, read=600)
    survive; a bare `httpx.Client()` would silently cut reads to 5 s.
    """
    from openai import DefaultHttpxClient

    return DefaultHttpxClient(verify=trust_context())


def describe() -> dict[str, object]:
    """Counts for diagnostic output / the Support panel. No cert material leaks out."""
    ctx = trust_context()
    cas = ctx.get_ca_certs()
    pem = merged_pem()
    return {
        "anchors": len(cas),
        "pem_certificates": len(_pem_blocks(pem)),
        "certifi_present": bool(pem),
        "os_store_used": sys.platform == "win32" and hasattr(ssl, "enum_certificates"),
        "openssl": ssl.OPENSSL_VERSION,
    }
