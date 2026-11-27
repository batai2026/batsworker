"""Coverage of the TLS trust anchor beyond the model provider clients.

These are the two remaining outbound endpoints whose host is chosen by the user
(config, not a hardcoded SaaS host), i.e. the ones that could sit behind the same
private CA that broke `deepseek_v4`:

* `rag.EmbeddingClient` - OpenAI-compatible `/embeddings`, base URL from
  `config.embedding_base_url`.
* `SessionManager.export_trace_otlp` - OTLP/HTTP collector, URL from
  `config.otlp_endpoint`.

Both used a bare `httpx` call, which resolves the trust store on its own
(`SSL_CERT_FILE` -> certifi-only file, else the platform store). That is exactly
the resolution order that hid the internal CA, so both must verify against the
shared merged context instead.

Reverse verification (AGENTS section 10): every test below was run against the
pre-fix code and failed there. The failure was the intended one -- `verify` came
back `None` -- not a fixture error. The two service tests were written first and
their fakes corrected until the only red assertion was the missing `verify`.
"""

from __future__ import annotations

import ast
import ssl
from pathlib import Path

import pytest


def test_embedding_client_verifies_against_merged_context(monkeypatch):
    """A private-CA embeddings endpoint (the same vLLM box, or an internal gateway)."""
    import httpx

    from coworker import net_trust, rag

    seen = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"embedding": [0.0, 1.0]}]}

    def _fake_post(url, **kwargs):
        seen.update(kwargs, url=url)
        return _Resp()

    monkeypatch.setattr(httpx, "post", _fake_post)
    client = rag.make_embedder(
        {
            "embedding_base_url": "https://internal.example/v1",
            "embedding_api_key": "k",
            "embedding_model": "m",
        }
    )
    assert client is not None
    assert client.embed(["hello"]) == [[0.0, 1.0]]

    verify = seen.get("verify")
    assert isinstance(verify, ssl.SSLContext), (
        "embedding call does not pass verify= -> it uses httpx's default store "
        f"(got {verify!r}), which is certifi-only whenever SSL_CERT_FILE is set"
    )
    assert verify is net_trust.trust_context(), (
        "embedding call built its own context instead of sharing the merged one "
        "(certifi or the OS store alone misses half the anchors)"
    )


def test_export_trace_otlp_verifies_against_merged_context(monkeypatch):
    """The user picks the collector URL, so it can be an internal host too."""
    import httpx

    from coworker import net_trust
    from coworker.server import manager_sessions

    seen = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

    def _fake_post(url, **kwargs):
        seen.update(kwargs, url=url)
        return _Resp()

    class _Cfg:
        otlp_endpoint = "https://collector.internal:4318"

    monkeypatch.setattr(httpx, "post", _fake_post)
    monkeypatch.setattr("coworker.config.load_config", lambda: _Cfg())

    # `manager_sessions` holds only the mixin; `export_trace_otlp` needs no other
    # state than `trace_store`, so build a bare instance of that class.
    mgr = manager_sessions.SessionsMixin.__new__(manager_sessions.SessionsMixin)

    class _Store:
        def session_spans(self, session_id):
            return [
                {
                    "session_id": session_id,
                    "id": "span-1",
                    "kind": "tool",
                    "name": "read_file",
                    "started": 1.0,
                    "ended": 1.5,
                    "status": "ok",
                    "duration_ms": 500,
                }
            ]

    mgr.trace_store = _Store()

    result = mgr.export_trace_otlp("sess")

    assert result.get("ok") is True, result
    verify = seen.get("verify")
    assert isinstance(verify, ssl.SSLContext), (
        "OTLP export does not pass verify= -> it uses httpx's default store "
        f"(got {verify!r}), which is certifi-only whenever SSL_CERT_FILE is set"
    )
    assert verify is net_trust.trust_context()


# -- regression guard ---------------------------------------------------------


def _bare_httpx_verbs(path: Path) -> list[tuple[int, str]]:
    """Every `httpx.<verb>(...)` / `httpx.Client(...)` call in `path` that omits
    `verify=`. AST rather than text: a substring match would pass on a file that
    happens to mention `verify=` in a comment while the call site still lacks it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if not (isinstance(owner, ast.Name) and owner.id == "httpx"):
            continue
        if node.func.attr not in {"get", "post", "put", "patch", "delete", "request",
                                  "Client", "AsyncClient", "stream"}:
            continue
        if not any(kw.arg == "verify" for kw in node.keywords):
            missing.append((node.lineno, node.func.attr))
    return missing


@pytest.mark.parametrize(
    "rel", ["coworker/rag.py", "coworker/server/manager_sessions.py"]
)
def test_user_configurable_endpoints_always_pass_verify(rel):
    """Both files reach a host the user picks; a new call without `verify=` is the
    exact bug this module exists to prevent."""
    import coworker

    path = Path(coworker.__file__).parent.parent / rel
    calls = [
        node
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "httpx"
    ]
    assert calls, f"{rel}: expected outbound httpx calls; the parser found none"
    assert _bare_httpx_verbs(path) == [], (
        f"{rel}: httpx call(s) without verify= at "
        + ", ".join(f"line {n} httpx.{v}" for n, v in _bare_httpx_verbs(path))
    )
