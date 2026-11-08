---
name: github-rest-push
description: Push git commits to GitHub through the REST API (blob, tree, commit, PATCH ref) when direct git push over HTTPS fails from network blocking or DNS pollution. Use when git push times out, returns empty reply from server, connection refused, or getaddrinfo errors against github.com while api.github.com remains reachable. Do not use when normal git push works.
---

# GitHub REST Push

Bypass `git push` over HTTPS by pushing the current HEAD through the GitHub
REST API. This works when `github.com:443` is blocked or DNS-polluted but
`api.github.com` answers.

## When to use

- `git push origin ...` returns `Connection timed out`, `Empty reply from
  server`, `Connection refused`, `getaddrinfo failed`, or `schannel: failed to
  receive handshake`.
- `curl https://api.github.com/rate_limit` returns HTTP 200.

## When not to use

- Direct `git push` succeeds.
- `api.github.com` is also unreachable — fix the network first (e.g. restart
  the WLAN adapter via an elevated `Restart-NetAdapter`).

## Prerequisites

- A stored GitHub OAuth token: `protocol=https` / `host=github.com` in the git
  credential store. If missing, run `git credential approve` with a token once
  or log in via Git Credential Manager.
- The local `origin` remote points at a GitHub repo (the script parses the slug
  from `git remote get-url origin`).
- A commit is ready locally (`git rev-parse HEAD`).

## Steps

1. Confirm `api.github.com` is reachable:
   ```
   curl -sS -o NUL -w "%{http_code}" https://api.github.com/rate_limit
   ```
   Expect `200`.

2. Run the script from the repo root:
   ```
   . .trae/skills/github-rest-push/scripts/push_api_generic.ps1
   ```
   Pass a target branch as the first argument (defaults to `main`):
   ```
   . .trae/skills/github-rest-push/scripts/push_api_generic.ps1 my-branch
   ```

3. Read the output. Expected:
   ```
   repo=<owner>/<repo> branch=<branch> changed=<n>
   local=<local-sha>
   remote=<remote-sha>
   tree_match=True
   ```
   `tree_match=True` means the content on the remote matches the local commit
   exactly.

## Notes

- The remote commit SHA usually differs from the local SHA because GitHub
  normalizes the author/committer timezone fields. `tree_match=True` is the
  correctness signal, not SHA equality.
- Non-ASCII commit messages use UTF-8 body encoding in the API request so
  Chinese text is not corrupted to `?`.
- This is a non-fast-forward-free push: it updates the ref to a commit whose
  parent is the current remote tip, so it is effectively a fast-forward push.
- After pushing, if the local branch diverges from `origin` due to the
  timezone-normalized SHA, run `git fetch && git rebase origin/<branch>` to
  realign (the local patch gets skipped as empty).
