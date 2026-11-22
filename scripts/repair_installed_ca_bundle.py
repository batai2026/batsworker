#!/usr/bin/env python
"""Make a *shipped* OpenWorker install trust this machine's internal CA.

Why a repair script exists at all
--------------------------------
v1.0.5's `server/run.py` pins `SSL_CERT_FILE` to the bundled `certifi/cacert.pem`
(`_ensure_ca_bundle()`), and only does so when the variable is unset -- an existing
value is honoured. Every outbound client therefore validates against those 118 public
roots, and an internal CA that lives only in the Windows `CA` store is not among them:
`CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate`.

The permanent fix is in the source tree (`coworker/net_trust.py`, PR #56): build the
context from certifi **union** the OS stores. Builds made after that commit need nothing
from this file. Installs predating it -- i.e. every 1.0.x binary already on disk -- need
the internal CA appended to the bundle they inherited, and **that file is replaced on
every reinstall/upgrade**, so the repair is a thing you re-run, not a thing you do once.

Usage
-----
    python scripts/repair_installed_ca_bundle.py --check      # assert, change nothing
    python scripts/repair_installed_ca_bundle.py --apply      # back up, then append
    python scripts/repair_installed_ca_bundle.py --check --bundle <path/to/cacert.pem>

Exit codes: 0 healthy / already correct, 1 needs repair (--check only), 2 unusable
(bundle missing, CA not found, bundle does not re-load after the append).

Notes
-----
* Idempotent: a bundle that already carries the marker is reported and left alone.
* Self-asserting, per AGENTS §2.1 -- an append that "looks done" but leaves a bundle
  OpenSSL rejects would be worse than no append, so the file is re-parsed and the
  anchor count checked, and a failure restores the backup in the same run.
* A sidecar that is already running has its TLS context bound at `httpx.Client`
  construction. Appending now does NOT change that process: re-save the provider in
  Settings (which calls `router.invalidate()`) or restart the app.
* The CA certificate is public material; no key material is read or printed.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import ssl
import sys
import time
from pathlib import Path

CA_SUBJECT_HINT = "OpenWorker-Internal-CA"
MARKER_PREFIX = "# --- OpenWorker internal CA"
DEFAULT_CANDIDATES = [
    Path(r"D:\OpenWorker\sidecar\_internal\certifi\cacert.pem"),
    Path(r"C:\Program Files\OpenWorker\sidecar\_internal\certifi\cacert.pem"),
]


# ---------------------------------------------------------------- helpers
def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_subject(der: bytes) -> str:
    """Subject string for a DER cert, without writing a temp file to the user's tree."""
    pem = ssl.DER_cert_to_PEM_cert(der)
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False,
                                     encoding="ascii") as fh:
        fh.write(pem)
        tmp = Path(fh.name)
    try:
        info = ssl._ssl._test_decode_cert(str(tmp))  # noqa: SLF001 - stdlib has no other API
    finally:
        tmp.unlink(missing_ok=True)
    return " ".join(
        "=".join(part) for rdn in info.get("subject", ()) for part in rdn
    )


def find_internal_ca() -> bytes:
    """DER of the CA that anchors this machine's internal endpoint.

    Searched in the Windows stores only. `CA` first: this machine's CA is installed as
    an intermediate, so a `ROOT`-only search finds nothing and reports a false negative.
    """
    seen = 0
    for store in ("CA", "ROOT"):
        try:
            entries = ssl.enum_certificates(store)
        except Exception:  # noqa: BLE001 - store unavailable on non-Windows
            continue
        for der, enc, _trust in entries:
            if enc != "x509_asn" or not der:
                continue
            seen += 1
            try:
                if CA_SUBJECT_HINT in decode_subject(der):
                    return der
            except Exception:  # noqa: BLE001 - undecodable entry, skip it
                continue
    raise SystemExit(
        "ABORT: no certificate matching %r in the Windows CA/ROOT stores "
        "(%d certificates examined). Is this the right machine?" % (CA_SUBJECT_HINT, seen)
    )


def anchor_count(path: Path) -> int:
    """Parse the bundle the way a TLS client would; -1 if OpenSSL rejects it."""
    try:
        return len(ssl.create_default_context(cafile=str(path)).get_ca_certs())
    except Exception:  # noqa: BLE001
        return -1


def ca_present(path: Path) -> bool:
    """Is the internal CA one of the anchors in this bundle right now?"""
    try:
        ctx = ssl.create_default_context(cafile=str(path))
    except Exception:  # noqa: BLE001
        return False
    for cert in ctx.get_ca_certs():
        subject = " ".join(
            "=".join(str(p) for p in item) for rdn in cert.get("subject", ()) for item in rdn
        )
        if CA_SUBJECT_HINT in subject:
            return True
    return False


def pick_bundle(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit("ABORT: bundle not found: %s" % p)
        return p
    for candidate in DEFAULT_CANDIDATES:
        if candidate.exists():
            return candidate
    raise SystemExit(
        "ABORT: no install bundle found. Pass --bundle <path to _internal/certifi/cacert.pem>."
    )


# ---------------------------------------------------------------- modes
def check(bundle: Path) -> int:
    print("bundle : %s" % bundle)
    print("size   : %d bytes" % bundle.stat().st_size)
    print("sha256 : %s" % sha256_of(bundle))
    patched = MARKER_PREFIX in bundle.read_text(encoding="ascii", errors="ignore")
    print("marker : %s" % ("present" if patched else "ABSENT"))
    anchors = anchor_count(bundle)
    print("anchors: %s" % (anchors if anchors >= 0 else "BUNDLE DOES NOT LOAD"))
    has_ca = ca_present(bundle)
    print("internal CA trusted: %s" % has_ca)
    if anchors < 0:
        print("\nRESULT=BROKEN  the bundle cannot be parsed; re-run with --apply")
        return 2
    if has_ca:
        print("\nRESULT=OK  this install will trust the internal endpoint")
        return 0
    print("\nRESULT=NEEDS REPAIR  re-run with --apply "
          "(a reinstall or upgrade replaced the bundle)")
    return 1


def apply(bundle: Path) -> int:
    before_sha = sha256_of(bundle)
    before_anchors = anchor_count(bundle)
    print("bundle : %s" % bundle)
    print("size before   : %d bytes" % bundle.stat().st_size)
    print("sha256 before : %s" % before_sha)
    print("anchors before: %s" % before_anchors)

    if ca_present(bundle):
        print("\nALREADY CORRECT -- the internal CA is trusted; nothing to do")
        return 0
    if before_anchors < 0:
        print("\nWARNING: the bundle does not parse cleanly before the append."
              " It will be backed up either way.")

    der = find_internal_ca()
    ca_pem = ssl.DER_cert_to_PEM_cert(der)
    print("\ninternal CA: subject=%s" % decode_subject(der))
    print("             sha256(public)=%s" % hashlib.sha256(der).hexdigest())

    backup_dir = bundle.parent / "_openworker_ca_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("cacert.pem.%s.%s.bak"
                           % (before_sha[:12], time.strftime("%Y%m%d-%H%M%S")))
    shutil.copy2(bundle, backup)
    if sha256_of(backup) != before_sha:
        raise SystemExit("ABORT: backup copy does not match the original; nothing changed")
    print("\nbackup: %s (sha256 verified)" % backup)

    text = bundle.read_text(encoding="ascii", errors="ignore")
    with bundle.open("a", encoding="ascii", newline="\n") as fh:
        if not text.endswith("\n"):
            fh.write("\n")
        fh.write("\n%s (added %s to trust this machine's internal endpoint) ---\n"
                 % (MARKER_PREFIX, time.strftime("%Y-%m-%d")))
        fh.write(ca_pem)
        if not ca_pem.endswith("\n"):
            fh.write("\n")

    after_anchors = anchor_count(bundle)
    print("\nsize after    : %d bytes" % bundle.stat().st_size)
    print("sha256 after  : %s" % sha256_of(bundle))
    print("anchors after : %s" % after_anchors)
    trusted = ca_present(bundle)

    # The append has to be proven, not assumed: an unparseable or unchanged bundle is a
    # failure, and a failure restores the backup before returning.
    ok = after_anchors >= 0 and trusted
    if not ok:
        shutil.copy2(backup, bundle)
        print("\nRESULT=FAILED  bundle rejected or CA absent after the append;"
              " restored from %s" % backup.name)
        return 2
    print("\nRESULT=OK  internal CA added (%s -> %s anchors)."
          % (before_anchors, after_anchors))
    print("A running sidecar keeps the TLS context it already built: re-save the provider")
    print("in Settings, or restart the app, for the new trust set to take effect.")
    print("rollback: copy %s over %s" % (backup, bundle))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="verify only; change nothing")
    group.add_argument("--apply", action="store_true", help="back up, then append the CA")
    parser.add_argument("--bundle", help="explicit path to the install's certifi bundle")
    args = parser.parse_args(argv)
    try:
        bundle = pick_bundle(args.bundle)
    except SystemExit as exc:
        print(str(exc) or "ABORT")
        return 2
    return check(bundle) if args.check else apply(bundle)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))