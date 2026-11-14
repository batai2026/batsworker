"""Local-API access control: which origins may call the sidecar, which paths the
bundled SPA owns, and the two token checks.

The sidecar binds to 127.0.0.1, but a page in the user's own browser can still reach
loopback. Without an origin gate, any website they visit could read `GET /v1/sessions`
(CORS was `*` once) and drive a session over the WS — which CORS never covers — into
shell/file tools. Requests with NO Origin header (curl, native clients, tests,
server-to-server) pass: the gate targets browsers, which always attach an unforgeable
Origin.

Lives outside app.py because the WebSocket router performs the same two checks; a
router importing them from app.py would tie the module graph in a knot.
"""

from __future__ import annotations

import re
import secrets

from fastapi import Request, WebSocket

# Origins allowed to talk to the local sidecar. It binds to 127.0.0.1, but a page in the
# user's own browser can still reach loopback — so without an origin gate, any website they
# visit could read `GET /v1/sessions` (CORS was `*`) and drive a session over the WS (which
# CORS never covers) into shell/file tools. We pin to the desktop webview's own origins
# (`tauri://localhost`, Windows' `http(s)://tauri.localhost`) and localhost dev/browser
# builds. Requests with NO Origin header (curl, native clients, tests, server-to-server) are
# allowed — the gate targets browsers, which always attach an unforgeable Origin.
ALLOWED_ORIGIN_RE = re.compile(
    r"^(tauri://localhost"
    r"|https?://localhost(:\d+)?"
    r"|https?://127\.0\.0\.1(:\d+)?"
    r"|https?://tauri\.localhost)$"
)


def origin_allowed(origin: str | None) -> bool:
    """True if a browser Origin may use the API. Missing Origin (non-browser) passes."""
    return origin is None or bool(ALLOWED_ORIGIN_RE.match(origin))


# Paths that keep the sidecar token requirement when the SPA is served from the sidecar.
# Everything else belongs to the bundled UI: the shell, `assets/`, `icons/`, the service
# worker and client-side routes. A top-level navigation cannot attach
# `x-openworker-token`, so gating those means the desktop window loads 401 forever.
# The API prefixes stay gated; these paths only serve the app's own build output.
SPA_GATED_PREFIXES = ("/v1", "/ws", "/docs", "/redoc", "/openapi.json")


def spa_owned_path(path: str) -> bool:
    """True for a path the bundled SPA serves (shell, assets, client-side route)."""
    return not path.startswith(SPA_GATED_PREFIXES)




def request_authenticated(request: Request, api_token: str) -> bool:
    """True when the caller presented the sidecar token (`x-openworker-token`)."""
    provided = request.headers.get("x-openworker-token", "")
    return bool(
        api_token
        and provided
        and secrets.compare_digest(provided, api_token)
    )


def websocket_authenticated(ws: WebSocket, api_token: str) -> bool:
    """True when the WS handshake offered the token as a subprotocol.

    A browser cannot set arbitrary headers on a WebSocket, so the token rides the
    `sec-websocket-protocol` list instead of a header.
    """
    if not api_token:
        return True
    protocols = {
        part.strip()
        for part in ws.headers.get("sec-websocket-protocol", "").split(",")
        if part.strip()
    }
    return any(secrets.compare_digest(part, api_token) for part in protocols)
