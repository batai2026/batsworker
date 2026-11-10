"""Egress policy server (P0 — v0.3.1 gap).

A persistent, user-level allowlist of network egress destinations. When the agent
hits a blocked site, ``request_egress`` (approval-gated) adds the domain here, and
the permission engine's ``_domain_allowed`` consults this store so later fetches to
the domain auto-run — "the coworker asks and waits while you allow it; once
allowed, it's part of the network policy."

Storage: ``<state-dir>/egress-policy.json`` — ``{"domains": {"example.com": {"reason": "…"}}}``.
Thread-safe (RLock); writes are atomic-ish (temp + replace).
"""

from __future__ import annotations

import ipaddress
import json
import os
import threading
from pathlib import Path
from typing import Optional

from .secrets import state_dir


def _normalize_domain(domain: str) -> str:
    """Lowercase, strip scheme/port/leading-dot; keep only the hostname."""
    d = (domain or "").strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0].split(":", 1)[0].split("@")[-1]
    return d.strip(".").strip()


# Suffixes that must never be granted: matching is "host or any subdomain", so
# granting one of these would allow the whole name space from a single click.
_PUBLIC_SUFFIXES = frozenset(
    {
        "com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "io",
        "co", "dev", "app", "cloud", "uk", "de", "fr", "jp", "cn", "ru", "nl",
        "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "co.jp", "com.br",
        "github.io", "appspot.com", "azurewebsites.net", "herokuapp.com",
    }
)


def _grantable(domain: str) -> bool:
    """True only for a host a user can meaningfully consent to.

    ``is_allowed`` matches the host itself or any subdomain, so a bare public
    suffix ("com", "co.uk") turns one approval into the whole TLD. Require at
    least two labels and reject public suffixes and bare IPs; an IP needs its own
    explicit decision, and callers that really mean one can still list it in the
    config/session allowlists, which are not gated by this check.
    """
    if not domain or "." not in domain:
        return False
    try:
        ipaddress.ip_address(domain)
        return False
    except ValueError:
        pass
    return domain not in _PUBLIC_SUFFIXES


class EgressPolicy:
    """Persistent domain allowlist for network egress."""

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else (state_dir() / "egress-policy.json")
        self._lock = threading.RLock()
        self._domains: dict[str, str] = {}  # domain → reason
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            data = raw.get("domains", {}) if isinstance(raw, dict) else {}
            self._domains = {
                _normalize_domain(k): str(v or "")
                for k, v in data.items()
                if _grantable(_normalize_domain(k))
            }
        except FileNotFoundError:
            self._domains = {}
        except (OSError, ValueError):  # corrupt file — start clean, never crash
            self._domains = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"domains": self._domains}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    # -- queries --------------------------------------------------------------

    def list_domains(self) -> list[dict]:
        with self._lock:
            return [
                {"domain": d, "reason": r} for d, r in sorted(self._domains.items())
            ]

    def is_allowed(self, host: str) -> bool:
        """Exact host or any subdomain of an allowed domain (docs.python.org matches
        python.org; evil-python.org never matches python.org)."""
        h = _normalize_domain(host)
        if not h:
            return False
        with self._lock:
            for dom in self._domains:
                if h == dom or h.endswith("." + dom):
                    return True
        return False

    # -- mutations (approval-gated via request_egress / the API) --------------

    def add_domain(self, domain: str, reason: str = "") -> bool:
        d = _normalize_domain(domain)
        if not d or not _grantable(d):
            return False
        with self._lock:
            self._domains[d] = (reason or "").strip()
            self._save()
        return True

    def remove_domain(self, domain: str) -> bool:
        d = _normalize_domain(domain)
        with self._lock:
            if d not in self._domains:
                return False
            del self._domains[d]
            self._save()
        return True


def default_policy() -> EgressPolicy:
    """The user-global egress policy (state dir), shared by tools and the API."""
    return EgressPolicy()