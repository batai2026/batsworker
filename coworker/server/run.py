"""Launch the server with uvicorn. Used by the desktop GUI sidecar and `openworker-server`."""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

from ..config import load_config
from ..permissions import Mode
from ..secrets import state_dir, write_private_text
from .app import create_app
from .manager import SessionManager
from .routes.ws import WS_MAX_FRAME_BYTES


def _exit_when_orphaned() -> None:
    """When launched as a desktop sidecar (`COWORKER_EXIT_WITH_PARENT=1`), exit if the parent
    process dies — even on an abrupt kill (e.g. the Tauri dev watcher restarting the app, or a
    crash) that skips the shell's graceful child-kill. Standalone `openworker-server` runs are
    unaffected.

    The GUI passes its own PID in `COWORKER_PARENT_PID`. Watching that explicit PID (not
    getppid) is what makes this work under PyInstaller onefile, where this process is a
    *grandchild* of the GUI — the bootloader sits in between, so getppid() points at the
    bootloader and a re-parenting check never fires when the GUI dies (the bug that leaked
    a server pair on every app quit).

    POSIX: poll the PID with kill(pid, 0). Windows: no re-parenting semantics at all, so
    block on a process handle and exit the moment it signals (i.e. the parent exited).
    """
    if os.environ.get("COWORKER_EXIT_WITH_PARENT") != "1":
        return
    import threading

    try:
        parent = int(os.environ.get("COWORKER_PARENT_PID") or 0)
    except ValueError:
        parent = 0
    parent = parent or os.getppid()  # standalone fallback: our direct spawner

    if sys.platform == "win32":
        _watch_parent_windows(parent)
        return

    import time

    original_ppid = os.getppid()

    def watch() -> None:
        while True:
            time.sleep(1.5)
            try:
                os.kill(parent, 0)  # liveness probe only; signal 0 delivers nothing
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass  # alive, but owned by someone else (shouldn't happen) — keep waiting
            # Secondary signal: our direct parent died (covers PID-reuse edge cases).
            if os.getppid() != original_ppid:
                os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def _watch_parent_windows(parent: int) -> None:
    """Block on a handle to the parent process; exit only when it actually terminates.

    Best-effort — any failure leaves the parent's RunEvent::ExitRequested kill as the primary
    cleanup path. Two correctness points that bit us before:
      - `OpenProcess` returns a 64-bit HANDLE; ctypes defaults the return type to a 32-bit int,
        which truncates the handle to garbage. Declare restype/argtypes so the handle is valid.
      - Only `os._exit` on WAIT_OBJECT_0 (the parent genuinely died). A bad handle yields
        WAIT_FAILED immediately — treating that as "parent died" would kill a perfectly healthy
        server seconds after startup (exactly the freeze we saw)."""
    import ctypes
    import threading
    from ctypes import wintypes

    SYNCHRONIZE = 0x0010_0000
    INFINITE = 0xFFFF_FFFF
    WAIT_OBJECT_0 = 0x0000_0000

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent)
    if not handle:
        return

    def watch() -> None:
        if kernel32.WaitForSingleObject(handle, INFINITE) == WAIT_OBJECT_0:
            os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def _spa_target(root: Path, rel: str) -> Path | None:
    """Map a URL path to a file inside `root`, or None when it escapes.

    `rel` comes straight from the URL, so it is attacker-controlled even on
    loopback: `..` segments, an absolute path, or (on Windows) backslash
    separators must all resolve back inside the dist root. Without that check
    the fallback route serves any file the sidecar process can read — including
    the state dir's `secrets.json` and the sidecar token.
    """
    try:
        target = (root / rel).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return target if target.is_relative_to(root) else None


def _mount_frontend_spa(app, frontend_dist: str) -> bool:
    """Serve a prebuilt SPA from the sidecar's own loopback origin.

    The desktop shell points its webview at `http://127.0.0.1:<sidecar port>/`,
    which keeps the SPA and the API on one origin and avoids Tauri 2.11's
    embedded-server deadlock on Windows. Returns False when the directory holds
    no usable `index.html`, so the caller can fall back to API-only serving.
    """
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    dist_path = Path(frontend_dist)
    if not dist_path.is_dir() or not (dist_path / "index.html").is_file():
        return False
    root = dist_path.resolve()

    for sub in ("assets", "icons"):
        sub_path = root / sub
        if sub_path.is_dir():
            app.mount(f"/{sub}", StaticFiles(directory=str(sub_path)), name=sub)

    # `{full_path:path}` yields the path WITHOUT its leading slash, so these are
    # compared route-relative. Unknown API paths must 404 rather than answer 200
    # with the SPA shell: a client parsing HTML as JSON reports a silent lie.
    api_prefixes = ("v1", "ws", "healthz", "docs", "openapi.json")

    @app.get("/")
    async def _serve_index():
        return FileResponse(str(root / "index.html"))

    @app.get("/{full_path:path}")
    async def _serve_spa(full_path: str):
        from fastapi import HTTPException

        rel = full_path.lstrip("/")
        if any(rel == p or rel.startswith(p + "/") for p in api_prefixes):
            raise HTTPException(status_code=404)
        target = _spa_target(root, rel)
        if target is None:
            raise HTTPException(status_code=404)
        if target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(root / "index.html"))

    # Tells the token middleware that this app also serves the UI: a navigation
    # cannot carry `x-openworker-token`, so the shell and its assets are exempt.
    app.state.serves_spa = True
    return True


def build_app(workspace: str | None, model: str, mode: str, frontend_dist: str | None = None):
    manager = SessionManager(
        workspace=Path(workspace).expanduser().resolve() if workspace else None,
        data_dir=state_dir(),
        model=model,
        mode=Mode(mode),
    )
    app = create_app(manager)
    # Serve the prebuilt frontend SPA from the same loopback port as the API, so
    # the Tauri webview can load http://127.0.0.1:<port>/index.html and skip
    # Tauri's embedded-server deadlock on Windows (tauri 2.11). A broken dist
    # must not take the API down with it.
    if frontend_dist:
        try:
            _mount_frontend_spa(app, frontend_dist)
        except Exception as e:
            print(f"[sidecar] could not mount frontend dist: {e}", file=sys.stderr)
    return app


def _ensure_ca_bundle() -> None:
    """Point SSL at certifi's CA bundle if the interpreter has none configured. macOS framework
    Python ships without a usable system trust store for `aiohttp` (it builds an `ssl` context with
    no CAs), so the Slack Socket-Mode client fails with CERTIFICATE_VERIFY_FAILED. `httpx`/`requests`
    bundle certifi already; aiohttp honours the SSL_CERT_FILE env var, so set it once at startup.
    """
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        pass


def _ensure_api_token(port: int) -> Path | None:
    """Set launch auth; standalone/dev tokens use a user-only, port-specific file."""
    if os.environ.get("COWORKER_API_TOKEN"):
        return None  # Tauri supplied an in-memory token; never persist it.
    token = secrets.token_hex(32)
    os.environ["COWORKER_API_TOKEN"] = token
    return write_private_text(
        state_dir() / f"sidecar-{port}.token", token + "\n"
    )


def _refuse_state_dir_inside_workspace(cwd: str | None) -> None:
    """Fail closed when the state dir sits inside the session root.

    `read_file` is scoped to the session's directories, so a state dir under the
    workspace would hand `secrets.json` and the sidecar API token to the model. The
    read-side check in `tools.files` is the real guard; this stops a deployment that
    still relies on the dangerous layout instead of silently serving it.
    """
    root = Path(cwd or os.getcwd()).resolve()
    sdir = state_dir().resolve()
    try:
        sdir.relative_to(root)
    except ValueError:
        return
    raise SystemExit(
        f"refusing to start: state dir {sdir} is inside the workspace {root}; "
        "set COWORKER_STATE_DIR to a path outside the session's directories"
    )


def main(argv=None) -> None:
    _ensure_ca_bundle()
    cfg = load_config()  # global config supplies defaults
    parser = argparse.ArgumentParser(prog="openworker-server")
    parser.add_argument("--cwd", default=None, help="optional seed/default workspace")
    parser.add_argument("--model", default=cfg.model)
    parser.add_argument(
        "--mode",
        default=cfg.mode,
        choices=["discuss", "plan", "interactive", "auto", "bypass-approvals", "auto-approve"],
    )
    parser.add_argument("--host", default=cfg.host)
    parser.add_argument("--port", type=int, default=cfg.port)
    parser.add_argument(
        "--frontend-dist",
        default=None,
        help="Directory containing the prebuilt frontend (dist/). If set, the sidecar "
        "serves it at /index.html so the desktop webview can load the SPA from the same "
        "loopback port as the API — avoids Tauri's embedded-server deadlock on Windows.",
    )
    args = parser.parse_args(argv)
    _refuse_state_dir_inside_workspace(args.cwd)

    # Publish the ACTUAL bound port so loopback URLs (the managed-OAuth callback)
    # target this process, not config.port. The desktop shell runs the sidecar on
    # a random free port (to coexist with a hand-run server on 8765), so the
    # managed-connect redirect must follow the real port, not the 8765 default.
    os.environ["COWORKER_PORT"] = str(args.port)
    generated_token_path = _ensure_api_token(args.port)
    try:
        import uvicorn

        _exit_when_orphaned()
        app = build_app(args.cwd, args.model, args.mode, frontend_dist=args.frontend_dist)
        uvicorn.run(
            app, host=args.host, port=args.port, ws_max_size=WS_MAX_FRAME_BYTES
        )
    finally:
        if generated_token_path is not None:
            generated_token_path.unlink(missing_ok=True)
            os.environ.pop("COWORKER_API_TOKEN", None)


if __name__ == "__main__":
    main()
