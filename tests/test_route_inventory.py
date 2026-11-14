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
    for path, method_label, name, _route in _flatten(app.router.routes, ""):
        lines.append(f"{method_label}\t{path}\t{name}")
    return lines


def _flatten(routes, prefix: str):
    """`(path, method_label, name)` for each route, descending into included routers."""
    out = []
    for route in routes:
        # FastAPI (0.14x) keeps an `include_router(...)` call as one `_IncludedRouter`
        # entry holding the child router instead of splicing its routes in place. Descend,
        # carrying the include prefix, so the table stays the flat list FastAPI matches
        # against — and so `include_router` order is compared exactly like decorator order.
        include_context = getattr(route, "include_context", None)
        if include_context is not None:
            out.extend(
                _flatten(route.original_router.routes, prefix + include_context.prefix)
            )
            continue
        methods = getattr(route, "methods", None)
        if isinstance(route, WebSocketRoute):
            method_label = "WEBSOCKET"
        elif methods:
            # HEAD is implied by Starlette for GET routes; listing it would only add noise.
            method_label = ",".join(sorted(set(methods) - {"HEAD"}))
        else:
            method_label = "-"  # the bare openapi/docs routes carry no methods of their own
        out.append((prefix + route.path, method_label, route.name, route))
    return out


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


def test_no_route_shadows_a_later_one(tmp_path):
    """Registration order is behaviour, so the table must stay shadow-free.

    `/v1/personas/catalog` only survives because it is registered before
    `/v1/personas/{persona_id}`; the same trap exists for `/v1/skills/upload`,
    `/v1/memory/settings`, `/v1/inbox/routing` and every other static path that shares a
    prefix with a parametric route. The baseline catches an accidental reorder, but it
    cannot tell a harmless one from a fatal one — this can: no literal (parameter-free)
    path may be claimed by an earlier route that shares any HTTP method with it.
    """
    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    manager = SessionManager(data_dir=tmp_path / "shadow")
    flat = _flatten(create_app(manager).router.routes, "")

    shadowed = []
    for index, (path, method_label, name, route) in enumerate(flat):
        if "{" in path or method_label == "WEBSOCKET":
            continue  # a parametric/WS target is matched, not matching
        for earlier_path, earlier_methods, earlier_name, earlier in flat[:index]:
            if earlier_methods != method_label:
                continue
            if earlier.path_regex.match(path):
                shadowed.append(
                    f"{method_label} {path} ({name}) is claimed first by "
                    f"{earlier_path} ({earlier_name})"
                )
                break

    assert not shadowed, "registration order shadows these routes:\n  " + "\n  ".join(
        shadowed
    )


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


def _router_factories() -> list[tuple[str, str, object]]:
    """`(module, factory name, factory)` for every `build_*_router` under routes/."""
    import importlib
    import inspect
    import pkgutil

    from coworker.server import routes as routes_pkg

    factories: list[tuple[str, str, object]] = []
    for info in pkgutil.iter_modules(routes_pkg.__path__):
        module = importlib.import_module(f"{routes_pkg.__name__}.{info.name}")
        for name, obj in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("build_") and name.endswith("_router"):
                factories.append((info.name, name, obj))
    return factories


def test_every_router_module_is_wired(tmp_path):
    """A router module nobody includes is dead code — and invisible to every other guard
    here, because the route table never learns those routes exist.

    So this walks `coworker/server/routes/`, builds each `build_*_router(manager[, token])`
    and asserts that every route it declares is present in the real app with the same path
    and methods. Behavioural on purpose: it does not care HOW `create_app` wires them, only
    that the mounted surface actually contains them.

    Written because the connectors round shipped `routes/observability.py` and
    `routes/attachments_messaging.py` without including either, and nothing failed.
    """
    import inspect
    import pkgutil

    from coworker.server import routes as routes_pkg
    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    manager = SessionManager(data_dir=tmp_path / "wiring")
    mounted = {
        (path, method_label)
        for path, method_label, _name, _route in _flatten(
            create_app(manager).router.routes, ""
        )
    }

    factories = _router_factories()
    assert len(factories) >= 15, "router discovery found suspiciously few factories"

    # Every module in routes/ must expose a factory under the naming convention, otherwise
    # discovery silently skips it and the check below passes while the module stays dead.
    modules = {info.name for info in pkgutil.iter_modules(routes_pkg.__path__)}
    discovered = {module for module, _name, _factory in factories}
    assert not (modules - discovered), (
        "module(s) in routes/ expose no build_*_router factory, so the wiring check cannot "
        "see them: " + repr(sorted(modules - discovered))
    )

    unwired = []
    for module, name, factory in factories:
        positional = [
            p
            for p in inspect.signature(factory).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        router = factory(manager, "") if len(positional) == 2 else factory(manager)
        missing = [
            f"{method_label} {path} ({endpoint})"
            for path, method_label, endpoint, _route in _flatten(router.routes, "")
            if (path, method_label) not in mounted
        ]
        if missing:
            unwired.append(
                f"routes/{module}.py::{name} is not mounted by create_app:\n    "
                + "\n    ".join(missing)
            )

    assert not unwired, "\n\n".join(unwired)


def test_app_declares_no_routes_of_its_own():
    """`app.py` is assembly now; a route declared there belongs in a router instead.

    Not a correctness rule but a shape rule: the split's whole point is that the surface is
    discoverable one-module-per-domain and `create_app` reads as a table of contents. Relax
    it deliberately if a genuinely app-level route ever appears.
    """
    import re

    source = (Path(__file__).parents[1] / "coworker" / "server" / "app.py").read_text(
        encoding="utf-8"
    )
    declared = [
        line.strip()
        for line in source.splitlines()
        if re.match(r"\s*@app\.(get|post|put|patch|delete|websocket|api_route)\(", line)
    ]
    assert not declared, "routes declared in app.py instead of a router: " + repr(declared)


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
