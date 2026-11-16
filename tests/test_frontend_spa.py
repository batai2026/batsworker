"""The sidecar's SPA fallback serves the desktop UI from the API's own origin.

Two properties matter and are easy to lose in a refactor:

* a URL path must never escape the dist root — the fallback route turns a request
  path into a filesystem path, and the sidecar can read the state dir;
* an unknown API path must 404 instead of answering 200 with the SPA shell, or a
  client parsing HTML as JSON reports a silent lie.
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from coworker.server.run import _mount_frontend_spa, _spa_target


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return root


@pytest.fixture
def client(dist: Path) -> TestClient:
    app = FastAPI()
    assert _mount_frontend_spa(app, str(dist)) is True
    return TestClient(app)


def test_serves_index_at_root(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "spa" in r.text


def test_serves_static_asset(client: TestClient) -> None:
    r = client.get("/assets/app.js")
    assert r.status_code == 200
    assert r.text == "console.log(1)"


def test_unknown_route_falls_back_to_index(client: TestClient) -> None:
    r = client.get("/sessions/abc")
    assert r.status_code == 200
    assert "spa" in r.text


def test_unknown_api_path_is_404_not_index(client: TestClient) -> None:
    r = client.get("/v1/definitely-not-a-route")
    assert r.status_code == 404


def test_missing_dist_is_reported_not_mounted(tmp_path: Path) -> None:
    assert _mount_frontend_spa(FastAPI(), str(tmp_path)) is False


def test_dist_without_index_is_reported_not_mounted(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    assert _mount_frontend_spa(FastAPI(), str(tmp_path)) is False


@pytest.mark.parametrize(
    "rel",
    ["../secret.txt", "a/../../secret.txt", "assets/../../secret.txt"],
)
def test_spa_target_refuses_to_escape(dist: Path, tmp_path: Path, rel: str) -> None:
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    assert _spa_target(dist.resolve(), rel) is None


@pytest.mark.skipif(sys.platform != "win32", reason="backslash separators are Windows-only")
def test_spa_target_refuses_windows_backslash_escape(dist: Path, tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    assert _spa_target(dist.resolve(), "..\\..\\secret.txt") is None


def test_spa_target_keeps_inner_path(dist: Path) -> None:
    target = _spa_target(dist.resolve(), "assets/app.js")
    assert target is not None
    assert target.name == "app.js"


def test_spa_target_rejects_absolute_path(dist: Path) -> None:
    absolute = str(Path(dist).parent / "secret.txt")
    assert _spa_target(dist.resolve(), absolute) is None


def test_spa_owned_path_exempts_the_ui_but_not_the_api() -> None:
    """A navigation cannot carry the sidecar header, so the UI must be reachable."""
    from coworker.server.app import _spa_owned_path

    for path in ("/", "/index.html", "/assets/app.js", "/sw.js", "/sessions/abc"):
        assert _spa_owned_path(path) is True, path
    for path in ("/v1", "/v1/sessions", "/ws/chat", "/docs", "/openapi.json", "/redoc"):
        assert _spa_owned_path(path) is False, path


def test_main_forwards_frontend_dist_to_build_app(tmp_path: Path, monkeypatch) -> None:
    """The desktop shell's `--frontend-dist` must reach `build_app`, not just argparse."""
    import sys
    from types import SimpleNamespace

    from coworker.server import run as server_run

    seen: dict = {}
    monkeypatch.setattr(server_run, "_ensure_ca_bundle", lambda: None)
    monkeypatch.setattr(server_run, "_exit_when_orphaned", lambda: None)
    monkeypatch.setattr(
        server_run,
        "build_app",
        lambda *args, **kwargs: seen.update(kwargs=kwargs) or object(),
    )
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=lambda app, **kw: None))

    # --cwd must not contain the state dir: startup refuses that layout on purpose.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    dist = tmp_path / "dist"
    dist.mkdir()

    server_run.main(
        ["--cwd", str(workspace), "--port", "8767", "--frontend-dist", str(dist)]
    )

    assert seen["kwargs"]["frontend_dist"] == str(dist)
