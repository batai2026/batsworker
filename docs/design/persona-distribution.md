# Signed persona distribution (marketplace v1)

> Source: `coworker/personas/signing.py` (minisign-compatible crypto),
> `coworker/personas/distribution.py` (trusted keys + catalog),
> `coworker/personas/registry.py::install_from_dir` (verification wiring),
> `coworker/server/app.py` (endpoints), `surfaces/gui` (consent badge).
> Author CLI: `python -m coworker.personas.signing genkey|sign|verify`.

The consent flow already made installing a persona a deliberate trust event
(`consent_summary`, `capability_set` growth re-consent — see
[personas-and-skills.md](personas-and-skills.md)). What it couldn't say was
**who the bundle came from**. Distribution v1 adds minisign signatures to the
existing flow without widening the trust model: a signature is a *fact* shown
next to capabilities, and only catalog-pinned installs fail closed.

## Signing (author side)

`python -m coworker.personas.signing genkey` produces a keypair whose public
file is byte-identical to a real minisign public key (base64 `"Ed"` + 32-byte
Ed25519, via the `cryptography` dependency — no new packages). `sign` writes
two files into the bundle:

    manifest.sha256           "sha256hex  relpath" lines (LF, sorted, POSIX paths)
    manifest.sha256.minisig   minisign signature over those exact bytes

Everything in the bundle (manifest `.md` + `skills/**`) is covered; the
manifest files themselves are excluded (they ARE the statement). Signatures
verify with the real `minisign` CLI; our verifier accepts real minisign public
keys and signatures. One deliberate divergence: the SECRET key container is our
own (`ow1` = scrypt + ChaCha20-Poly1305, not libsodium's KDF), so real-minisign
secret keys can't be loaded here — the verify side interops fully, the sign
side uses keys from this tool.

## Verification (install side)

`verify_directory` returns facts, tiered by what a key was provided:

| Condition | Result |
|---|---|
| No signature files | `{signed: false}` |
| Signed, no key given | `{signed: true, files_match, key_id, valid: null}` — integrity vs the signed manifest is checked; **who signed it is unverifiable without a key** |
| Signed + verifying key | `valid: true/false` (+ `pinned` mirrors it), tampering ⇒ `valid: false` |
| Pinned install (catalog), invalid signature | **install refused** before consent is ever shown (fail closed) |

`key_id` before verification comes from the signature's UNTRUSTED comment — a
display label only (comment lines are attacker-writable; cryptographic identity
is the pinned/trusted key match).

## Trust surfaces

- **Catalog installs** pin the entry's key: `install_from_git/dir(expected_pubkey=…)`
  refuses a bundle that doesn't verify against it. The catalog entry IS the
  trust act.
- **Manual installs** are labeled, never refused: the consent card shows
  `✓ 已签名` (verified), `✕ 签名校验失败` (tampered/mismatch — do not enable),
  or `已签名 · 密钥未验证` (no key provided).
- **Trusted keys** (`TrustedKeyStore`, `trusted-persona-keys.json`): a per-user
  list the user grows explicitly; a verifying signature from a trusted key
  renders `· trusted`. Public keys never echo back out of the API.

## Community catalog

`config.persona_catalog_url` (user-global, empty default — the app never ships
a catalog URL for an ecosystem that doesn't exist yet). When set:
`GET /v1/personas/catalog` fetches + caches (TTL 1h, stale-cache fallback,
defensive parse — **entries without slug+source+pubkey are dropped, never
surfaced**), `POST /v1/personas/catalog/install {slug}` installs with the
entry's pinned key and auto-trusts the key on a verifying install (choosing the
entry is the user's trust act). Trusted-key management:
`GET/POST/DELETE /v1/personas/trusted-keys`.

## What v1 does not do

- No hosted catalog (the pipeline is tested with an injectable fetcher; the
  URL is configuration).
- No auto-update of installed personas from the catalog (re-install +
  capability-growth re-consent remains the update path).
- No revocation lists: removing a trusted key stops *future* trust; already-
  installed personas keep their consent state (that's the manifest's job).
