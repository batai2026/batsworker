"""Route inventory guard for the `coworker/server/app.py` split (P2, plan A).

`app.py` is being dismantled domain by domain into `APIRouter`s under
`coworker/server/routes/`. Every one of those steps is meant to be a *pure move*, so the
HTTP surface has to come out the other side identical:

* the same set of paths and methods,
* the same endpoint function names (they become OpenAPI operation ids and are part of the
  contract the GUI's generated client rides on),
* the same registration order — FastAPI resolves in order, so `/v1/personas/catalog` must
  stay ahead of `/v1/personas/{persona_id}`, `/v1/skills/upload` ahead of
  `/v1/skills/{name}`, `/v1/attachments/inspect-pdf` ahead of nothing but still in place.
  A reordering that shadows a route is invisible to a "same set" assertion, which is why
  this baseline is ordered.

The snapshot lives in `tests/data/route_inventory.txt`, one route per line as
`METHODS<TAB>PATH<TAB>NAME`, in registration order.

Regenerate it only when the surface is meant to change (a route added, removed, renamed or
deliberately reordered). Review the diff — that diff *is* the API change:

    python tests/test_route_inventory.py --update
"""

from __future__ import annotations

import sys
from pathlib import Path

from starlette.routing import WebSocketRoute

BASELINE = Path(__file__).parent / "data" / "route_inventory.txt"


def _route_lines(app) -> list[str]:
    """`METHODS<TAB>PATH<TAB>NAME` for every registered route, in registration order."""
    lines: list[str] = []
    for route in app.router.routes:
        methods = getattr(route, "methods", None)
        if isinstance(route, WebSocketRoute):
            method_label = "WEBSOCKET"
        elif methods:
            # HEAD is implied by Starlette for GET routes; listing it would only add noise.
            method_label = ",".join(sorted(set(methods) - {"HEAD"}))
        else:
            method_label = "-"  # the bare openapi/docs routes carry no methods of their own
        lines.append(f"{method_label}\t{route.path}\t{route.name}")
    return lines


def _collect(scratch: Path) -> list[str]:
    """Build a real app on a throwaway data dir and read its route table.

    Nothing is exercised here — creating the app only registers routes, so no network, no
    cluster and no state dir is touched. `scratch` is caller-owned because the manager's
    sqlite stores keep the file open on Windows and defeat `TemporaryDirectory` cleanup.
    """
    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    manager = SessionManager(data_dir=scratch / "data")
    return _route_lines(create_app(manager))


def test_route_inventory_matches_baseline(tmp_path):
    expected = BASELINE.read_text(encoding="utf-8").splitlines()
    actual = _collect(tmp_path)

    if actual != expected:
        missing = [line for line in expected if line not in actual]
        added = [line for line in actual if line not in expected]
        detail = []
        if missing:
            detail.append("dropped from the app:\n  " + "\n  ".join(missing))
        if added:
            detail.append("not in the baseline:\n  " + "\n  ".join(added))
        if not detail:  # same contents, different order — the shadowing case
            for index, (want, got) in enumerate(zip(expected, actual)):
                if want != got:
                    detail.append(
                        f"registration order changed at #{index + 1}: "
                        f"baseline {want!r} vs app {got!r}"
                    )
                    break
        detail.append(
            "If the HTTP surface is meant to change, regenerate with "
            "`python tests/test_route_inventory.py --update` and review the diff."
        )
        raise AssertionError("\n\n".join(detail))


def test_route_inventory_is_not_empty(tmp_path):
    # A guard on the guard: a broken collector would otherwise "match" an empty baseline.
    assert len(_collect(tmp_path)) >= 200


def test_debug_inject_route_stays_env_gated(tmp_path, monkeypatch):
    """`/v1/_debug/inject_inbound` only exists under COWORKER_DEBUG_INJECT=1.

    It is the 217th route and the one that a naive extraction is most likely to turn into
    an always-on endpoint: it feeds text straight through the real inbound path. The
    baseline above therefore does NOT contain it, and this test pins both halves of the
    gate — absent by default, present (and nothing else) when the flag is on.
    """
    default = _collect(tmp_path / "default")
    monkeypatch.setenv("COWORKER_DEBUG_INJECT", "1")
    debug = _collect(tmp_path / "debug")

    assert "POST\t/v1/_debug/inject_inbound\tdebug_inject_inbound" not in default
    assert "POST\t/v1/_debug/inject_inbound\tdebug_inject_inbound" in debug
    assert [line for line in debug if line not in default] == [
        "POST\t/v1/_debug/inject_inbound\tdebug_inject_inbound"
    ]
    assert len(debug) == len(default) + 1


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        lines = _collect(Path(tmp))
    if "--update" in sys.argv:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {len(lines)} routes to {BASELINE}")
    else:
        print("\n".join(lines))
