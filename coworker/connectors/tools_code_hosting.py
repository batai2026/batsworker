"""GitHub and GitLab tools (batch B of the connector tool split).

Moved out of `integration_tools.py` verbatim — same names, schemas, gating and registration
order; `tests/test_integration_tool_catalog.py` freezes that catalog.

The GitHub side is a little different from the REST-only platforms: `github_clone` /
`github_pull` shell out to `git` (`_run_git`), and the credential for those URLs is minted
per-host through `_github_git_auth_args`, so the token never appears in an argv the model can
read. `_github_call` is the REST helper (headers + base URL + error shaping).
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

import base64
import re

from ..secrets import SecretStore
from . import toolkit
from .toolkit import _attach, _clamp, _profile, _schema


def _github_base() -> str:
    import os

    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def _github_auth(
    secrets: SecretStore, install: str = "", *, force: bool = False
) -> tuple[Optional[dict[str, str]], Optional[dict[str, str]]]:
    """(headers, err). A manual PAT (`github:default.token`) wins, untouched;
    a managed relay profile mints a short-lived installation token instead —
    memory-cached, never stored (github-relay-spec §4). `install` picks the
    installation by account login (pass the repo owner) or id; unknown values
    fall back to the default installation."""
    profile = secrets.get("github:default") or {}
    if profile.get("token"):
        return _github_headers(profile["token"]), None
    if profile.get("mode") == "relay":
        from ..cloud import github_installation_token
        from ..config import load_config
        from . import github_installs

        installation_id, _prof = github_installs.resolve(secrets, install)
        if not installation_id and install:
            installation_id, _prof = github_installs.resolve(secrets, "")
        if not installation_id:
            return None, {"error": "github is not connected; no App installation"}
        token = github_installation_token(
            secrets, load_config(), installation_id, force=force
        )
        if not token:
            return None, {
                "error": "github installation token unavailable "
                "(sign in to OpenWorker Cloud and retry)"
            }
        return _github_headers(token), None
    return None, {"error": "github is not connected; missing token"}

def _github_git_auth_args(secrets: SecretStore, owner: str) -> list[str]:
    """Per-invocation git auth: the token rides an HTTP header on the command
    line only — it must NEVER land in .git/config or a credential store (the
    no-token-at-rest rule; github-relay-spec §4). Empty for the tokenless case
    (public repos clone fine without auth)."""

    headers, err = _github_auth(secrets, owner)
    if err:
        return ["-c", "credential.helper="]
    token = headers["Authorization"].split(" ", 1)[1]
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return [
        "-c",
        f"http.extraHeader=AUTHORIZATION: basic {basic}",
        "-c",
        "credential.helper=",
    ]

def _run_git(
    args: list[str], *, cwd: Any = None, timeout: int = 600
) -> tuple[str, str]:
    """(stdout, error). Never raises; the error string is capped and carries no
    auth material (git never echoes header values)."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except FileNotFoundError:
        return "", "git is not installed"
    except subprocess.TimeoutExpired:
        return "", "git timed out"
    if proc.returncode != 0:
        return "", (proc.stderr or proc.stdout or "").strip()[-500:]
    return (proc.stdout or "").strip(), ""

def _github_git_base() -> str:
    import os

    return os.environ.get("GITHUB_GIT_URL", "https://github.com").rstrip("/")

def _github_call(
    secrets: SecretStore, method: str, path: str, *, install: str = "", **kw: Any
) -> dict[str, Any]:
    """A GitHub API call that works on either auth path. A 401 on the managed
    path re-mints once (the cached installation token may have just expired)."""
    headers, err = _github_auth(secrets, install)
    if err:
        return err
    out = toolkit._request(method, _github_base() + path, headers=headers, **kw)
    managed = not (secrets.get("github:default") or {}).get("token")
    if managed and out.get("error") == "HTTP 401":
        headers, err = _github_auth(secrets, install, force=True)
        if err:
            return out
        out = toolkit._request(method, _github_base() + path, headers=headers, **kw)
    return out

def _gitlab_api(profile: dict[str, Any]) -> str:
    base = str(profile.get("base_url") or "https://gitlab.com").rstrip("/")
    return f"{base}/api/v4"


def add_github_tools(
    tools: list, secrets: SecretStore, roots: Optional[list] = None
) -> None:
    """Register the GitHub tools on `tools` (order preserved)."""
    def github_search(
        query: str, search_type: str = "issues", max_results: int = 10
    ) -> dict[str, Any]:
        kind = "repositories" if search_type == "repositories" else "issues"
        out = _github_call(
            secrets,
            "GET",
            f"/search/{kind}",
            params={"q": query, "per_page": max(1, min(int(max_results or 10), 20))},
        )
        if "error" in out:
            return out
        items = out["data"].get("items", [])
        return {"results": items}

    github_search.__name__ = "github_search"
    tools.append(
        _attach(
            github_search,
            _schema(
                "github_search",
                "Search GitHub issues, pull requests, or repositories.",
                {
                    "query": {"type": "string"},
                    "search_type": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                ["query"],
            ),
            caps=["github", "read"],
        )
    )

    def github_get_issue(owner: str, repo: str, issue_number: int) -> dict[str, Any]:
        return _github_call(
            secrets,
            "GET",
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            install=owner,
        )

    github_get_issue.__name__ = "github_get_issue"
    tools.append(
        _attach(
            github_get_issue,
            _schema(
                "github_get_issue",
                "Read a GitHub issue or pull request by number.",
                {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "issue_number": {"type": "integer"},
                },
                ["owner", "repo", "issue_number"],
            ),
            caps=["github", "read"],
        )
    )

    def github_create_issue(
        owner: str, repo: str, title: str, body: str = ""
    ) -> dict[str, Any]:
        return _github_call(
            secrets,
            "POST",
            f"/repos/{owner}/{repo}/issues",
            install=owner,
            json={"title": title, "body": body},
        )

    github_create_issue.__name__ = "github_create_issue"
    tools.append(
        _attach(
            github_create_issue,
            _schema(
                "github_create_issue",
                "Create a GitHub issue. Requires user approval.",
                {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                ["owner", "repo", "title"],
            ),
            approval=True,
            caps=["github", "write"],
        )
    )

    # Wave-1 relay write tools (github-relay-spec §8). The write ceiling is
    # enforced by what exists here: comments, reviews, issues — no push,
    # branch-delete, or repo-settings tools on any auth path.
    def github_reply(owner: str, repo: str, number: int, body: str) -> dict[str, Any]:
        return _github_call(
            secrets,
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            install=owner,
            json={"body": body},
        )

    github_reply.__name__ = "github_reply"
    tools.append(
        _attach(
            github_reply,
            _schema(
                "github_reply",
                "Comment on a GitHub issue or pull request (as the agent's bot "
                "identity on the managed path). Requires user approval.",
                {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "number": {"type": "integer"},
                    "body": {"type": "string"},
                },
                ["owner", "repo", "number", "body"],
            ),
            approval=True,
            caps=["github", "write"],
        )
    )

    def github_review(
        owner: str, repo: str, pull_number: int, event: str = "COMMENT", body: str = ""
    ) -> dict[str, Any]:
        event = (event or "COMMENT").upper()
        if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
            return {"error": "event must be APPROVE, REQUEST_CHANGES or COMMENT"}
        return _github_call(
            secrets,
            "POST",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
            install=owner,
            json={"event": event, **({"body": body} if body else {})},
        )

    github_review.__name__ = "github_review"
    tools.append(
        _attach(
            github_review,
            _schema(
                "github_review",
                "Submit a pull-request review (approve / request changes / "
                "comment). Requires user approval.",
                {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "pull_number": {"type": "integer"},
                    "event": {"type": "string"},
                    "body": {"type": "string"},
                },
                ["owner", "repo", "pull_number"],
            ),
            approval=True,
            caps=["github", "write"],
        )
    )

    def github_list_commits(
        owner: str,
        repo: str,
        since: str = "",
        until: str = "",
        author: str = "",
        max_results: int = 30,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"per_page": max(1, min(int(max_results or 30), 100))}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        if author:
            params["author"] = author
        out = _github_call(
            secrets,
            "GET",
            f"/repos/{owner}/{repo}/commits",
            install=owner,
            params=params,
        )
        if "error" in out:
            return out
        commits = [
            {
                "sha": (c.get("sha") or "")[:12],
                "author": ((c.get("commit") or {}).get("author") or {}).get("name")
                or (c.get("author") or {}).get("login", ""),
                "date": ((c.get("commit") or {}).get("author") or {}).get("date", ""),
                "message": ((c.get("commit") or {}).get("message") or "")[:500],
            }
            for c in (out["data"] if isinstance(out["data"], list) else [])
        ]
        return {"commits": commits, "count": len(commits)}

    github_list_commits.__name__ = "github_list_commits"
    tools.append(
        _attach(
            github_list_commits,
            _schema(
                "github_list_commits",
                "List a repository's commits (newest first), optionally filtered "
                "by ISO-8601 since/until dates or author — the raw material for "
                "activity summaries.",
                {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "since": {
                        "type": "string",
                        "description": "ISO-8601, e.g. 2026-07-06T00:00:00Z",
                    },
                    "until": {"type": "string"},
                    "author": {"type": "string", "description": "GitHub login"},
                    "max_results": {"type": "integer"},
                },
                ["owner", "repo"],
            ),
            approval=False,
            caps=["github", "read"],
        )
    )

    def _writable_target(
        raw: str, *, default_name: str = ""
    ) -> tuple[Any, dict[str, Any] | None]:
        """Resolve a directory inside a WRITABLE granted root — clones and pulls
        never touch anything the user hasn't shared with the session."""
        from pathlib import Path as _Path

        writable = [r.path for r in (roots or []) if r.writable]
        if not writable:
            return None, {"error": "no writable session directory to clone into"}
        path = (
            _Path(str(raw)).expanduser().resolve()
            if raw
            else (writable[0] / default_name).resolve()
        )
        if not any(path.is_relative_to(root) for root in writable):
            return None, {
                "error": f"{path} is outside the session's writable directories"
            }
        return path, None

    def github_clone(owner: str, repo: str, directory: str = "") -> dict[str, Any]:
        target, err = _writable_target(directory, default_name=repo)
        if err:
            return err
        if target.exists() and any(target.iterdir()):
            return {
                "error": f"{target} already exists and is not empty (use github_pull?)"
            }
        url = f"{_github_git_base()}/{owner}/{repo}.git"
        _out, git_err = _run_git(
            [*_github_git_auth_args(secrets, owner), "clone", url, str(target)]
        )
        if git_err:
            return {"error": f"clone failed: {git_err}"}
        # Belt and braces for the no-token-at-rest rule: header auth is
        # process-only, so nothing secret can be in the clone's config — verify.
        config = (target / ".git" / "config").read_text()
        if "AUTHORIZATION" in config or "x-access-token" in config:
            import shutil

            shutil.rmtree(target)
            return {"error": "clone aborted: credentials would have persisted"}
        head, _ = _run_git(["rev-parse", "--short", "HEAD"], cwd=target)
        return {"ok": True, "path": str(target), "head": head}

    github_clone.__name__ = "github_clone"
    tools.append(
        _attach(
            github_clone,
            _schema(
                "github_clone",
                "Clone a GitHub repository into a session folder so the agent can "
                "explore the code locally. Private repos use a short-lived token "
                "that is never written to disk. Requires user approval.",
                {
                    "owner": {"type": "string"},
                    "repo": {"type": "string"},
                    "directory": {
                        "type": "string",
                        "description": "target path inside a granted folder (default: <primary>/<repo>)",
                    },
                },
                ["owner", "repo"],
            ),
            approval=True,
            caps=["github", "read"],
        )
    )

    def github_pull(directory: str) -> dict[str, Any]:
        target, err = _writable_target(directory)
        if err:
            return err
        if not (target / ".git").exists():
            return {"error": f"{target} is not a git repository"}
        remote, git_err = _run_git(["remote", "get-url", "origin"], cwd=target)
        if git_err:
            return {"error": f"no origin remote: {git_err}"}
        m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", remote)
        owner = m.group(1) if m else ""
        _out, git_err = _run_git(
            [
                *_github_git_auth_args(secrets, owner),
                "-C",
                str(target),
                "pull",
                "--ff-only",
            ]
        )
        if git_err:
            return {"error": f"pull failed: {git_err}"}
        head, _ = _run_git(["rev-parse", "--short", "HEAD"], cwd=target)
        return {"ok": True, "path": str(target), "head": head}

    github_pull.__name__ = "github_pull"
    tools.append(
        _attach(
            github_pull,
            _schema(
                "github_pull",
                "Fast-forward an existing clone in a session folder to the latest "
                "upstream commits. Requires user approval.",
                {"directory": {"type": "string"}},
                ["directory"],
            ),
            approval=True,
            caps=["github", "read"],
        )
    )


def add_gitlab_tools(tools: list, secrets: SecretStore) -> None:
    """Register the GitLab tools on `tools` (order preserved)."""
    def gitlab_search(
        query: str, scope: str = "issues", max_results: int = 10
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "gitlab", "token")
        if err:
            return err
        kind = scope if scope in ("projects", "issues", "merge_requests") else "issues"
        return toolkit._request(
            "GET",
            f"{_gitlab_api(profile)}/search",
            headers={"PRIVATE-TOKEN": profile["token"]},
            params={"scope": kind, "search": query, "per_page": _clamp(max_results)},
        )

    gitlab_search.__name__ = "gitlab_search"
    tools.append(
        _attach(
            gitlab_search,
            _schema(
                "gitlab_search",
                "Search GitLab projects, issues, or merge_requests (scope).",
                {
                    "query": {"type": "string"},
                    "scope": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                ["query"],
            ),
            caps=["gitlab", "read"],
        )
    )

    def gitlab_get_issue(project: str, issue_iid: int) -> dict[str, Any]:
        profile, err = _profile(secrets, "gitlab", "token")
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{_gitlab_api(profile)}/projects/{quote(project, safe='')}/issues/{issue_iid}",
            headers={"PRIVATE-TOKEN": profile["token"]},
        )

    gitlab_get_issue.__name__ = "gitlab_get_issue"
    tools.append(
        _attach(
            gitlab_get_issue,
            _schema(
                "gitlab_get_issue",
                "Read a GitLab issue. project is an ID or full path like group/repo.",
                {"project": {"type": "string"}, "issue_iid": {"type": "integer"}},
                ["project", "issue_iid"],
            ),
            caps=["gitlab", "read"],
        )
    )

    def gitlab_get_merge_request(project: str, mr_iid: int) -> dict[str, Any]:
        profile, err = _profile(secrets, "gitlab", "token")
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{_gitlab_api(profile)}/projects/{quote(project, safe='')}/merge_requests/{mr_iid}",
            headers={"PRIVATE-TOKEN": profile["token"]},
        )

    gitlab_get_merge_request.__name__ = "gitlab_get_merge_request"
    tools.append(
        _attach(
            gitlab_get_merge_request,
            _schema(
                "gitlab_get_merge_request",
                "Read a GitLab merge request. project is an ID or full path like group/repo.",
                {"project": {"type": "string"}, "mr_iid": {"type": "integer"}},
                ["project", "mr_iid"],
            ),
            caps=["gitlab", "read"],
        )
    )

    def gitlab_create_issue(
        project: str, title: str, description: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "gitlab", "token")
        if err:
            return err
        return toolkit._request(
            "POST",
            f"{_gitlab_api(profile)}/projects/{quote(project, safe='')}/issues",
            headers={"PRIVATE-TOKEN": profile["token"]},
            json={"title": title, "description": description},
        )

    gitlab_create_issue.__name__ = "gitlab_create_issue"
    tools.append(
        _attach(
            gitlab_create_issue,
            _schema(
                "gitlab_create_issue",
                "Create a GitLab issue. Requires user approval.",
                {
                    "project": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                },
                ["project", "title"],
            ),
            approval=True,
            caps=["gitlab", "write"],
        )
    )
