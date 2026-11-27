"""The three ACL tables in the desktop shell must agree (AGENTS.md §8).

A Tauri v2 app's own commands are declared in three separate places, and getting
any one of them wrong is a *silent* runtime failure -- the compiler says nothing:

  * ``src-tauri/src/lib.rs``          ``generate_handler![...]``  -- the real list
  * ``src-tauri/build.rs``            ``AppManifest::new().commands(&[...])``
    -> without this, no ``__app-acl__`` manifest is generated at all, so *every*
       app command is rejected by the ACL
  * ``src-tauri/capabilities/default.json``  ``allow-*`` entries
    -> without one of these, that one command is rejected by the ACL

Why this file exists
--------------------
"Select folder" silently did nothing in the packaged build for exactly this
reason, and it took two separate faults to explain it (see AGENTS.md §8). Fixing
both by hand left nothing in CI to catch the next one -- which is the failure
mode AGENTS.md §2.1 is about: a manual sweep with no assertion is correct once,
not fixed.

Two more invariants are covered here because they fail the same silent way:

  * the capability targets ``windows: ["main"]``, so renaming the window label
    stops the capability matching;
  * the page is served from ``http://127.0.0.1:<random port>`` when the sidecar
    ships ``dist/``, so the capability needs a ``remote`` entry. The port comes
    from ``bind("127.0.0.1:0")``, i.e. it differs every launch -- a literal port
    would pass locally and fail in the field.

Every failure mode this file guards is exercised against a synthetic tree, not
only against the real one, so a parser that quietly matches nothing shows up as
a failure rather than as a green tick.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "surfaces" / "gui" / "src-tauri"


# --- parsing ---------------------------------------------------------------


def _block_after(text: str, opener: str, closers: tuple[str, ...]) -> str | None:
    """Text between ``opener`` and the first closer after it, or None if absent.

    Returns None rather than raising when the construct is gone: "``build.rs``
    never declares its commands" is one of the faults this file reports, so it
    has to arrive as a finding, not as a parser explosion.
    """
    start = text.find(opener)
    if start == -1:
        return None
    start += len(opener)
    ends = [text.find(c, start) for c in closers]
    ends = [e for e in ends if e != -1]
    return text[start:min(ends)] if ends else text[start:]


# `generate_handler![pick_folder, get_autostart]` -- bare identifiers.
_IDENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*,?\s*(?://.*)?$", re.M)
# `commands(&["pick_folder", "get_autostart"])` -- quoted string literals.
_STRING = re.compile(r'"([^"\\]*)"')
# `#[tauri::command]` (optionally `(async)`, `(rename_all = ..)`) above `fn name`.
_COMMAND_ATTR = re.compile(
    r"#\[tauri::command(?:\([^)]*\))?\]\s*(?:\n\s*)*"
    r"(?:pub\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)"
)
# `invoke<string>("pick_folder")` / `invokeStrict<void>("pick_folder")`.
_INVOKE = re.compile(
    r"(?:invoke|invokeStrict)\s*(?:<[^>]*>)?\s*\(\s*[\"'`]([A-Za-z_][A-Za-z0-9_]*)[\"'`]"
)


def handler_commands(src: str) -> list[str]:
    """Command names from `generate_handler![...]` (bare identifiers)."""
    block = _block_after(src, "generate_handler![", ("])", "],", "]", ")"))
    return _IDENT.findall(block) if block else []


def manifest_commands(build_rs: str) -> list[str]:
    """Command names from `.commands(&["a", "b"])` (string literals)."""
    block = _block_after(build_rs, ".commands(&[", ("])", "],", "]", ")"))
    return _STRING.findall(block) if block else []


def defined_commands(src_dir: Path) -> list[str]:
    """Names of every ``#[tauri::command] fn`` under ``src/`` -- the source of truth.

    The three tables are only cross-checked against *each other*, so a command
    that is missing from all three leaves them equal and passes. This scan is
    what makes that case a failure instead of a green tick.
    """
    names: list[str] = []
    for rs in sorted(src_dir.rglob("*.rs")):
        try:
            text = rs.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        names.extend(_COMMAND_ATTR.findall(text))
    return names


def invoked_commands(spa_src: Path) -> list[str]:
    """Command names the SPA passes to ``invoke(...)``, or [] if that tree is absent.

    A name the shell no longer registers is the same silent failure seen from the
    other side: the command is registered correctly and nothing calls it, while
    every UI click quietly resolves to null.
    """
    if not spa_src.is_dir():
        return []
    names: list[str] = []
    for pattern in ("*.ts", "*.tsx"):
        for f in sorted(spa_src.rglob(pattern)):
            if "node_modules" in f.parts:
                continue
            try:
                names.extend(_INVOKE.findall(f.read_text(encoding="utf-8")))
            except UnicodeDecodeError:
                continue
    return names


@dataclass
class Acl:
    """What the three tables say, plus the window/origin they are scoped to."""

    handler: list[str] = field(default_factory=list)
    manifest: list[str] = field(default_factory=list)
    granted: list[str] = field(default_factory=list)
    # From the source itself, not from any table -- see `defined_commands`.
    defined: list[str] = field(default_factory=list)
    invoked: list[str] = field(default_factory=list)
    window_labels: list[str] = field(default_factory=list)
    # The label lib.rs actually builds. Kept separate from ``window_labels``
    # (what the capability targets) precisely so a mismatch can be reported.
    built_window: str | None = None
    remote_urls: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def granted_commands(self) -> list[str]:
        """``allow-pick-folder`` -> ``pick_folder``."""
        return [p[len("allow-"):].replace("-", "_") for p in self.granted]


def built_window_label(lib: str) -> str | None:
    """The label passed to ``WebviewWindowBuilder::new(app, "<label>", ..)``."""
    built = re.search(r'WebviewWindowBuilder::new\(\s*app\s*,\s*"([^"]+)"', lib)
    return built.group(1) if built else None


def read_acl(root: Path) -> Acl:
    """Parse the three tables out of a ``src-tauri`` tree.

    ``root`` is the ``src-tauri`` directory, not the repo root, so the synthetic
    trees in this file can be laid out the same way as the real one.
    """
    lib = (root / "src" / "lib.rs").read_text(encoding="utf-8")
    build = (root / "build.rs").read_text(encoding="utf-8")
    caps = json.loads((root / "capabilities" / "default.json").read_text(encoding="utf-8"))

    acl = Acl(
        handler=handler_commands(lib),
        manifest=manifest_commands(build),
        granted=[p for p in caps.get("permissions", []) if p.startswith("allow-")],
        defined=defined_commands(root / "src"),
        invoked=invoked_commands(root.parent / "src"),
        window_labels=list(caps.get("windows", [])),
        built_window=built_window_label(lib),
        remote_urls=list(caps.get("remote", {}).get("urls", [])),
    )
    if "app_manifest" not in build:
        acl.warnings.append(
            "build.rs never calls `app_manifest(...)`: no `__app-acl__` manifest "
            "is generated, so every app command is rejected by the ACL"
        )
    return acl


def problems(acl: Acl) -> list[str]:
    """Every way the three tables can disagree, as human-readable lines."""
    out = list(acl.warnings)

    if not acl.handler:
        out.append("no commands parsed out of lib.rs `generate_handler![...]`")

    missing_manifest = sorted(set(acl.handler) - set(acl.manifest))
    if missing_manifest:
        out.append(
            "declared in generate_handler! but not in build.rs "
            f"AppManifest::commands(): {missing_manifest}"
        )

    stale_manifest = sorted(set(acl.manifest) - set(acl.handler))
    if stale_manifest:
        out.append(
            f"in build.rs AppManifest::commands() but not registered: {stale_manifest}"
        )

    granted = set(acl.granted_commands)
    not_granted = sorted(set(acl.handler) - granted)
    if not_granted:
        out.append(
            "registered but missing an `allow-*` entry in capabilities/default.json: "
            f"{not_granted}"
        )

    stale_grants = sorted(granted - set(acl.handler))
    if stale_grants:
        out.append(
            f"`allow-*` entries with no matching command registered: {stale_grants}"
        )

    # --- the source of truth ------------------------------------------------
    #
    # Everything above compares the tables to each other. That is necessary but
    # not sufficient: a `#[tauri::command]` fn that is absent from ALL THREE
    # tables keeps them perfectly equal, compiles fine, and is unreachable at
    # runtime. `pick_folder` would have shipped broken that way.
    if acl.defined:
        unregistered = sorted(set(acl.defined) - set(acl.handler))
        if unregistered:
            out.append(
                "declared `#[tauri::command]` but absent from `generate_handler![...]`: "
                f"{unregistered} -- it compiles and can never be called"
            )
        undefined = sorted(set(acl.handler) - set(acl.defined))
        if undefined:
            out.append(
                "in `generate_handler![...]` with no `#[tauri::command]` fn behind it: "
                f"{undefined}"
            )

    # And the same failure seen from the UI: a call to a name nobody registers.
    if acl.invoked:
        dead_calls = sorted(set(acl.invoked) - set(acl.handler))
        if dead_calls:
            out.append(
                f"the SPA invokes commands the shell does not register: {dead_calls} "
                "-- every such call resolves to null at runtime"
            )

    # A hyphen in a Rust command name would generate a permission nobody expects.
    for name in acl.handler + acl.manifest:
        if "-" in name:
            out.append(f"command `{name}` uses a hyphen; Rust command names use underscores")

    # --- the capability must actually match the page the app loads ----------
    #
    # This is the fault that started all of it: a capability that does not match
    # the page's origin is not "partially applied" -- *every* command is denied,
    # core ones included.
    if not acl.remote_urls:
        out.append(
            "capabilities/default.json has no `remote.urls`: the packaged build "
            "serves the SPA from http://127.0.0.1:<port>, which is not a local "
            "origin, so no capability matches and every command is denied"
        )
    elif not _covers_loopback(acl.remote_urls):
        out.append(
            f"no wildcard loopback origin in remote.urls {acl.remote_urls!r}; the "
            'port comes from bind("127.0.0.1:0"), so a fixed port breaks on relaunch'
        )

    if acl.built_window is None:
        out.append(
            "could not find `WebviewWindowBuilder::new(app, \"<label>\", ..)` in "
            "lib.rs -- if the window is built some other way, check `windows` by hand"
        )
    elif acl.built_window not in acl.window_labels:
        out.append(
            f"the app builds window {acl.built_window!r} but the capability targets "
            f"{acl.window_labels!r}, so it would not match"
        )

    return out


def _covers_loopback(urls: list[str]) -> bool:
    """True if some URL covers a loopback origin on *any* port."""
    for url in urls:
        if "127.0.0.1" not in url and "localhost" not in url:
            continue
        # `http://127.0.0.1:*`, `http://127.0.0.1:*/*`, or a bare origin with no
        # port at all. A literal port (`http://127.0.0.1:51234`) is not enough.
        rest = url.split("://", 1)[-1].split("/", 1)[0]
        host, _, port = rest.partition(":")
        if "*" in port or port == "":
            return True
    return False


# --- the real tree ---------------------------------------------------------


def test_the_three_tables_agree() -> None:
    acl = read_acl(GUI)
    assert not problems(acl), "\n".join(problems(acl))


def test_parsers_actually_found_something() -> None:
    """A regex that matches nothing would make every other test vacuous.

    This is the AGENTS.md §1.1 failure mode applied to this very file: a check
    that never runs its core step is not evidence. Pin the parsed shape so a
    parser regression fails loudly instead of passing silently.
    """
    acl = read_acl(GUI)
    assert len(acl.handler) > 10, f"only parsed {acl.handler!r}"
    assert "pick_folder" in acl.handler, "the command that started all this is missing"
    assert len(acl.manifest) == len(acl.handler)
    assert len(acl.granted) == len(acl.handler)
    # The source scan and the SPA scan are the two dimensions that are easy to
    # leave silently empty -- pin them here so an empty parse fails loudly.
    assert len(acl.defined) == len(acl.handler), f"parsed {acl.defined!r}"
    assert len(acl.invoked) >= 10, f"parsed {acl.invoked!r}"
    assert set(acl.invoked) == set(acl.handler), (
        f"SPA/backend mismatch: {sorted(set(acl.invoked) ^ set(acl.handler))}"
    )


# --- the guards must actually bite ----------------------------------------


def _synthetic(tmp_path: Path, *, handler: list[str], manifest: list[str],
               granted: list[str], remote: list[str] | None = None,
               drop_remote: bool = False,
               windows: list[str] | None = None,
               built_window: str = "main",
               defined: list[str] | None = None) -> Path:
    """Lay out a fake ``src-tauri`` tree so failures can be provoked on purpose.

    ``defined`` defaults to ``handler`` -- i.e. a consistent tree. Pass it
    explicitly to model a command that exists in the source but in no table.
    """
    defined = handler if defined is None else defined
    root = tmp_path / "src-tauri"
    (root / "src").mkdir(parents=True)
    (root / "capabilities").mkdir()

    (root / "src" / "lib.rs").write_text(
        "".join(f"#[tauri::command]\nfn {n}() {{}}\n\n" for n in defined)
        + "fn main() {\n"
        "    tauri::Builder::default()\n"
        "        .invoke_handler(tauri::generate_handler![\n"
        + "".join(f"            {n},\n" for n in handler)
        + "        ])\n"
        "        .setup(|app| {\n"
        "            WebviewWindowBuilder::new(app, \"" + built_window + "\", url)\n"
        "        })\n"
        "        .run(tauri::generate_context!())\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "build.rs").write_text(
        "fn main() {\n"
        "    let attributes = tauri_build::Attributes::new().app_manifest(\n"
        "        tauri_build::AppManifest::new().commands(&[\n"
        + "".join(f'            "{n}",\n' for n in manifest)
        + "        ]),\n"
        "    );\n"
        "    tauri_build::try_build(attributes).unwrap();\n"
        "}\n",
        encoding="utf-8",
    )
    caps: dict[str, object] = {
        "windows": ["main"] if windows is None else windows,
        "permissions": granted,
    }
    if not drop_remote:
        caps["remote"] = {"urls": ["http://127.0.0.1:*"] if remote is None else remote}
    (root / "capabilities" / "default.json").write_text(
        json.dumps(caps, indent=2), encoding="utf-8"
    )
    return root


def test_clean_synthetic_tree_passes(tmp_path: Path) -> None:
    """The fixtures must be able to pass, or the failure cases prove nothing."""
    root = _synthetic(
        tmp_path,
        handler=["pick_folder", "get_autostart"],
        manifest=["pick_folder", "get_autostart"],
        granted=["allow-pick-folder", "allow-get-autostart"],
    )
    assert problems(read_acl(root)) == []


def test_missing_manifest_declaration_is_caught(tmp_path: Path) -> None:
    """Fault (b): build.rs omits a command -> no `__app-acl__` entry for it."""
    root = _synthetic(
        tmp_path,
        handler=["pick_folder", "start_dictation"],
        manifest=["pick_folder"],
        granted=["allow-pick-folder", "allow-start-dictation"],
    )
    found = " ".join(problems(read_acl(root)))
    assert "start_dictation" in found
    assert "not in build.rs" in found


def test_missing_capability_grant_is_caught(tmp_path: Path) -> None:
    """Fault (a) at command granularity: the capability forgot one `allow-*`."""
    root = _synthetic(
        tmp_path,
        handler=["pick_folder", "start_dictation"],
        manifest=["pick_folder", "start_dictation"],
        granted=["allow-pick-folder"],
    )
    found = " ".join(problems(read_acl(root)))
    assert "start_dictation" in found
    assert "allow-*" in found


def test_stale_grant_is_caught(tmp_path: Path) -> None:
    """A permission left behind after a command was removed is also drift."""
    root = _synthetic(
        tmp_path,
        handler=["pick_folder"],
        manifest=["pick_folder"],
        granted=["allow-pick-folder", "allow-deleted-command"],
    )
    found = " ".join(problems(read_acl(root)))
    assert "deleted_command" in found


def test_missing_app_manifest_call_is_caught(tmp_path: Path) -> None:
    """build.rs calling bare `tauri_build::build()` is the original fault (b)."""
    root = _synthetic(
        tmp_path,
        handler=["pick_folder"],
        manifest=["pick_folder"],
        granted=["allow-pick-folder"],
    )
    (root / "build.rs").write_text(
        "fn main() { tauri_build::build() }\n", encoding="utf-8"
    )
    found = " ".join(problems(read_acl(root)))
    # `app_manifest` is gone, so the commands list parses empty and both the
    # warning and the set difference must fire.
    assert "app_manifest" in found
    assert "pick_folder" in found


def test_hyphenated_command_name_is_caught(tmp_path: Path) -> None:
    """`"pick-folder"` in build.rs would not generate `allow-pick-folder`."""
    root = _synthetic(
        tmp_path,
        handler=["pick_folder"],
        manifest=["pick-folder"],
        granted=["allow-pick-folder"],
    )
    found = " ".join(problems(read_acl(root)))
    assert "hyphen" in found


def test_empty_handler_is_caught(tmp_path: Path) -> None:
    """The vacuous-pass guard: an unparsable handler must not look clean."""
    root = _synthetic(tmp_path, handler=[], manifest=[], granted=[])
    found = " ".join(problems(read_acl(root)))
    assert "no commands parsed" in found


def test_missing_remote_origin_is_caught(tmp_path: Path) -> None:
    """The original fault: a local-only capability never matches the sidecar page."""
    root = _synthetic(
        tmp_path,
        handler=["pick_folder"],
        manifest=["pick_folder"],
        granted=["allow-pick-folder"],
        drop_remote=True,
    )
    found = " ".join(problems(read_acl(root)))
    assert "remote.urls" in found
    assert "every command is denied" in found


def test_literal_port_origin_is_caught(tmp_path: Path) -> None:
    """A pinned port passes one launch and fails the next -- still not a fix."""
    root = _synthetic(
        tmp_path,
        handler=["pick_folder"],
        manifest=["pick_folder"],
        granted=["allow-pick-folder"],
        remote=["http://127.0.0.1:51234"],
    )
    found = " ".join(problems(read_acl(root)))
    assert "wildcard loopback" in found


def test_wildcard_port_origin_is_accepted(tmp_path: Path) -> None:
    """Guard against an over-eager matcher: the real, correct form must pass."""
    root = _synthetic(
        tmp_path,
        handler=["pick_folder"],
        manifest=["pick_folder"],
        granted=["allow-pick-folder"],
        remote=["http://127.0.0.1:*"],
    )
    assert problems(read_acl(root)) == []


def test_command_in_no_table_at_all_is_caught(tmp_path: Path) -> None:
    """The blind spot this whole dimension exists for.

    All three tables agree (they simply omit the command), so the cross-checks
    above stay quiet. Only scanning the source catches it.
    """
    root = _synthetic(
        tmp_path,
        defined=["pick_folder", "start_dictation"],
        handler=["pick_folder"],
        manifest=["pick_folder"],
        granted=["allow-pick-folder"],
    )
    found = " ".join(problems(read_acl(root)))
    assert "start_dictation" in found
    assert "never be called" in found


def test_registered_command_with_no_definition_is_caught(tmp_path: Path) -> None:
    """The mirror image: a table entry pointing at a function that was renamed."""
    root = _synthetic(
        tmp_path,
        defined=["pick_folder"],
        handler=["pick_folder", "old_name"],
        manifest=["pick_folder", "old_name"],
        granted=["allow-pick-folder", "allow-old-name"],
    )
    found = " ".join(problems(read_acl(root)))
    assert "old_name" in found
    assert "no `#[tauri::command]` fn" in found


def test_spa_calling_unregistered_command_is_caught(tmp_path: Path) -> None:
    """A stale `invoke("...")` name is the same bug from the UI side."""
    root = _synthetic(
        tmp_path,
        handler=["pick_folder"],
        manifest=["pick_folder"],
        granted=["allow-pick-folder"],
    )
    spa = tmp_path / "src"
    spa.mkdir()
    (spa / "tauri.ts").write_text(
        'export const pick = () => invoke<string>("pick_folder");\n', encoding="utf-8"
    )
    assert problems(read_acl(root)) == []

    (spa / "tauri.ts").write_text(
        'export const gone = () => invoke<void>("retired_command");\n', encoding="utf-8"
    )
    found = " ".join(problems(read_acl(root)))
    assert "retired_command" in found
    assert "does not register" in found


def test_renamed_window_is_caught(tmp_path: Path) -> None:
    """`windows` must name the label lib.rs builds, or the capability misses."""
    root = _synthetic(
        tmp_path,
        handler=["pick_folder"],
        manifest=["pick_folder"],
        granted=["allow-pick-folder"],
        windows=["main-window"],
        built_window="main",
    )
    found = " ".join(problems(read_acl(root)))
    assert "builds window 'main'" in found
    assert "main-window" in found
