"""Catalog snapshot for the connector tool surface.

`coworker/connectors/integration_tools.py` is ~4.9k lines with 134 nested tool definitions
across 30-odd platforms. P3 splits it by platform, and every one of those steps must be a
**pure move**: the same tools, in the same order, with the same schema, gating and
capabilities. That is exactly what the route-inventory baseline did for `app.py`, and this
is the same instrument for the tool surface — freeze it first, then move code against it.

One line per tool, `<TAB>`-separated, in `make_integration_tools()` order:

    name <TAB> approval <TAB> capabilities <TAB> required <TAB> properties <TAB> description

`properties` is rendered as `name:type` pairs sorted by name so a schema tweak shows up as
a readable diff instead of an opaque hash. `approval` and `capabilities` come from the
`__aisuite_tool_metadata__` the registry actually reads; `description` is model-facing text,
so it is part of the contract too.

Regenerate only when the surface is meant to change, and review the diff — that diff is the
tool-API change:

    python tests/test_integration_tool_catalog.py --update
"""

from __future__ import annotations

import sys
from pathlib import Path

BASELINE = Path(__file__).parent / "data" / "integration_tool_catalog.txt"


def _collect(scratch: Path) -> list[str]:
    from coworker.connectors.integration_tools import make_integration_tools
    from coworker.secrets import SecretStore

    secrets = SecretStore(scratch / "secrets.json")
    lines: list[str] = []
    for tool in make_integration_tools(secrets):
        schema = getattr(tool, "__coworker_schema__", None)
        meta = getattr(tool, "__aisuite_tool_metadata__", None)
        if schema is None or meta is None:
            # Anything in the catalog must be registry-ready; a bare function here would be
            # invisible to the model and to permission checks.
            raise AssertionError(f"{getattr(tool, '__name__', tool)!r} has no schema/metadata")
        fn = schema["function"]
        params = fn.get("parameters", {})
        props = params.get("properties", {})
        rendered_props = ",".join(
            f"{key}:{value.get('type', '?')}" for key, value in sorted(props.items())
        )
        lines.append(
            "\t".join(
                [
                    fn["name"],
                    "approval" if meta.requires_approval else "read",
                    ",".join(meta.capabilities or []),
                    ",".join(params.get("required", [])),
                    rendered_props,
                    " ".join(fn.get("description", "").split()),
                ]
            )
        )
    return lines


def test_catalog_matches_baseline(tmp_path):
    expected = BASELINE.read_text(encoding="utf-8").splitlines()
    actual = _collect(tmp_path)

    if actual != expected:
        missing = [line for line in expected if line not in actual]
        added = [line for line in actual if line not in expected]
        detail = []
        if missing:
            detail.append("gone from the catalog:\n  " + "\n  ".join(missing))
        if added:
            detail.append("not in the baseline:\n  " + "\n  ".join(added))
        if not detail:  # same set, different order — the registry order is a contract too
            for index, (want, got) in enumerate(zip(expected, actual)):
                if want != got:
                    detail.append(
                        f"order changed at #{index + 1}: baseline {want!r} vs catalog {got!r}"
                    )
                    break
        detail.append(
            "If the tool surface is meant to change, regenerate with "
            "`python tests/test_integration_tool_catalog.py --update` and review the diff."
        )
        raise AssertionError("\n\n".join(detail))


def test_catalog_is_not_empty(tmp_path):
    # Guard on the guard: a broken collector would otherwise "match" an empty baseline.
    assert len(_collect(tmp_path)) >= 100


def test_every_tool_name_is_unique(tmp_path):
    names = [line.split("\t")[0] for line in _collect(tmp_path)]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f"duplicate tool names would shadow each other: {duplicates}"


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        lines = _collect(Path(tmp))
    if "--update" in sys.argv:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {len(lines)} tools to {BASELINE}")
    else:
        print("\n".join(lines))
