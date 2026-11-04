"""Trusted signing keys + the community persona catalog (distribution v1).

Two halves of the "signed distribution" story:

**Trusted keys** (``TrustedKeyStore``): per-user list of minisign public keys
the user has explicitly trusted, persisted under the data dir. A signature that
verifies against a trusted key renders as "trusted" in the consent summary;
anything else is "signed, key unknown" — a fact, never an automatic approval.

**Catalog** (``fetch_catalog``): a remote JSON directory of community personas —
name, description, source URL, and the author's pinned minisign public key.
The app never ships a default catalog URL (the ecosystem doesn't exist yet);
when ``config.persona_catalog_url`` is set, ``GET /v1/personas/catalog`` serves
the fetched list (cached, TTL'd, defensively parsed) and catalog installs pin
verification to the entry's key — fail closed, per
``registry.install_from_dir(expected_pubkey=…)``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

CATALOG_TTL_SECONDS = 3600


# -- trusted keys ---------------------------------------------------------------


class TrustedKeyStore:
    """Per-user list of trusted minisign public keys (JSON under the data dir)."""

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "trusted-persona-keys.json"

    def _load(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _save(self, keys: List[Dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def list(self) -> List[Dict[str, Any]]:
        # A key's secret half never lives here — public keys only.
        return [
            {k: v for k, v in entry.items() if k != "pubkey"}
            for entry in self._load()
        ]

    def is_trusted(self, key_id: str) -> bool:
        return any(entry.get("key_id") == key_id for entry in self._load())

    def add(self, pubkey_path: str, label: str = "") -> dict:
        """Add a minisign-format public key file. Re-adding the same key id is a
        no-op that returns the existing entry (ids are derived from the key)."""
        from .signing import load_public_key

        key_id, _pk = load_public_key(pubkey_path)
        keys = self._load()
        for entry in keys:
            if entry.get("key_id") == key_id:
                return {**entry, "already_trusted": True}
        keys.append(
            {
                "key_id": key_id,
                "label": label or Path(pubkey_path).name,
                "source": str(pubkey_path),
                "added_at": time.strftime("%Y-%m-%d"),
            }
        )
        self._save(keys)
        return {"key_id": key_id, "label": label or Path(pubkey_path).name}

    def remove(self, key_id: str) -> dict:
        keys = self._load()
        remaining = [entry for entry in keys if entry.get("key_id") != key_id]
        if len(remaining) == len(keys):
            return {"ok": False, "error": f"no trusted key {key_id}"}
        self._save(remaining)
        return {"ok": True}

    def decorate(self, signature: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Attach ``trusted`` to a verify_directory result (signed facts only)."""
        if not signature or not signature.get("signed") or not signature.get("valid"):
            return signature
        key_id = signature.get("key_id")
        if key_id:
            signature["trusted"] = self.is_trusted(str(key_id))
        return signature


# -- community catalog ----------------------------------------------------------


class CatalogError(RuntimeError):
    pass


def fetch_catalog(
    url: str,
    cache_path: Path,
    *,
    ttl: int = CATALOG_TTL_SECONDS,
    force_fresh: bool = False,
    fetcher: Optional[Callable[[str], str]] = None,
    now: Optional[Callable[[], float]] = None,
) -> dict:
    """Fetch and parse the community catalog JSON, cached at ``cache_path`` for
    ``ttl``. ``fetcher`` is injectable so tests never touch the network. The
    response is parsed defensively: an entry missing any pin (source + pubkey)
    is dropped rather than trusted — a catalog entry that can't be verified
    against a pinned key is exactly what this feature exists to prevent."""
    now = now or time.time
    if not force_fresh:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if now() - float(cached.get("fetched_at", 0)) < ttl and cached.get("url") == url:
                return cached["data"]
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
            pass

    if not url:
        raise CatalogError("no community catalog configured (set persona_catalog_url)")
    import httpx

    try:
        if fetcher is not None:
            body = fetcher(url)
        else:
            resp = httpx.get(url, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            body = resp.text
        data = json.loads(body)
    except Exception as exc:
        # A stale cache beats nothing: serve it with a stale marker if we have one.
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("url") == url:
                return {**cached["data"], "stale": True}
        except Exception:
            pass
        raise CatalogError(f"catalog fetch failed: {exc}") from exc

    out: Dict[str, Any] = {
        "name": str(data.get("name", "Community personas")),
        "updated": str(data.get("updated", "")),
        "personas": [],
    }
    for entry in data.get("personas") or []:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("slug", "")).strip()
        source = str(entry.get("git_url") or entry.get("dir") or "").strip()
        pubkey = str(entry.get("pubkey", "")).strip()
        if not slug or not source or not pubkey:
            continue  # unpinnable entry — drop, never surface
        out["personas"].append(
            {
                "slug": slug,
                "name": str(entry.get("name", slug)),
                "description": str(entry.get("description", "")),
                "author": str(entry.get("author", "")),
                "version": str(entry.get("version", "")),
                "git_url": str(entry.get("git_url", "")),
                "dir": str(entry.get("dir", "")),
                "pubkey": pubkey,
                "signed": True,
            }
        )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"url": url, "fetched_at": now(), "data": out}, ensure_ascii=False),
        encoding="utf-8",
    )
    return out


def catalog_entry(data: dict, slug: str) -> Optional[Dict[str, Any]]:
    for entry in data.get("personas", []):
        if entry.get("slug") == slug:
            return entry
    return None
