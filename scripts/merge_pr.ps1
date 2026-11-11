#requires -Version 5.1
<#
.SYNOPSIS
  Merge a pull request through the GitHub API, behind guards, with an honest verdict.

.DESCRIPTION
  Used to close out reviewed fixes: the branch was pushed, CI went green, and the merge
  is the one irreversible step. So it refuses rather than guesses -- it stops unless the
  PR is open, its head is the commit that was reviewed, and GitHub reports it clean.

  Success is decided by `merged = true` in the response, NOT by whether a `message`
  field exists. GitHub answers a successful merge with HTTP 200 and
    {"sha": "...", "merged": true, "message": "Pull Request successfully merged"}
  so treating `message` as an error signal reports success as failure -- which is
  exactly the bug this file was written to fix. Failures look like
    {"message": "Pull Request is not mergeable", "errors": [{message: ...}]}

  The token comes from `git credential fill` and is only ever sent to
  api.github.com: any other -ApiBase (the local test double) gets a placeholder, so a
  test run can never carry real credentials to localhost.

.PARAMETER Pr
  Pull request number.

.PARAMETER ExpectedSha
  The head commit that was reviewed. Strongly recommended -- without it the head is not
  checked, and a force-push between review and merge would go unnoticed.

.PARAMETER MergeMethod
  merge (default), squash, or rebase.

.PARAMETER ApiBase
  Override for testing against a local double, e.g. http://127.0.0.1:8731 .
  Only https://api.github.com/... receives the real credential.

.PARAMETER DryRun
  Run every guard, print the pre-merge state, and stop before the PUT.

.PARAMETER SelfTest
  Feed the known response shapes through the decision function and exit. No network,
  no credentials. CI runs this.

.EXAMPLE
  ./scripts/merge_pr.ps1 -Pr 3 -ExpectedSha 2b8cb2a4d622cfdb2724ccbbd9379149bab3efb5 -DryRun

.EXAMPLE
  ./scripts/merge_pr.ps1 -Pr 3 -ExpectedSha 2b8cb2a4d622cfdb2724ccbbd9379149bab3efb5

.NOTES
  Exit codes: 0 merged (or dry run passed the guards), 1 refused or merge failed.

  GitHub computes mergeability lazily, so a PR fetched immediately after a push can
  report mergeable_state=unknown. The script refuses in that case rather than guessing;
  wait a few seconds and run it again.

  Tests: tests/test_merge_pr_script.py runs this file against
  coworker.testing.fake_github. `-SelfTest` covers the verdict alone.
#>
param(
    [Parameter(Mandatory = $true)][int]$Pr,
    [string]$ExpectedSha = "",
    [ValidateSet("merge", "squash", "rebase")][string]$MergeMethod = "merge",
    [string]$Title = "",
    [string]$Message = "",
    [string]$Repo = "batai2026/batsworker",
    [string]$ApiBase = "",
    [switch]$DryRun,
    [switch]$SelfTest
)
$ErrorActionPreference = 'Stop'

# Single source of truth for "did this merge work?", so -SelfTest exercises the exact
# expression a real run uses instead of a paraphrase of it.
function Resolve-MergeOutcome($m) {
    if ($m.merged -eq $true) {
        return @{ ok = $true; text = "merged=True sha=$($m.sha)" }
    }
    $why = if ($m.message) { $m.message } else { "no 'merged' field in response" }
    $detail = ''
    if ($m.errors) { $detail = ($m.errors | ForEach-Object { $_.message }) -join '; ' }
    $text = if ($detail) { "merge_failed=$why | $detail" } else { "merge_failed=$why" }
    return @{ ok = $false; text = $text }
}

if ($SelfTest) {
    $fail = 0
    # The real success payload -- the one that used to be reported as a failure.
    $r = Resolve-MergeOutcome ([pscustomobject]@{
            sha = 'abc123'; merged = $true; message = 'Pull Request successfully merged'
        })
    "success_shape       -> ok=$($r.ok)  $($r.text)"
    if (-not $r.ok) { $fail++ }

    $r = Resolve-MergeOutcome ([pscustomobject]@{
            message = 'Pull Request is not mergeable'; errors = @()
        })
    "conflict_shape      -> ok=$($r.ok)  $($r.text)"
    if ($r.ok) { $fail++ }

    $r = Resolve-MergeOutcome ([pscustomobject]@{
            message = 'Head branch was modified. Review and try the merge again.'
            errors  = @([pscustomobject]@{ message = 'sha did not match' })
        })
    "head_moved_shape    -> ok=$($r.ok)  $($r.text)"
    if ($r.ok) { $fail++ }
    if ($r.text -notmatch 'sha did not match') { "  MISSING detail"; $fail++ }

    # A response without the field must not read as success.
    $r = Resolve-MergeOutcome ([pscustomobject]@{ message = '' })
    "empty_shape         -> ok=$($r.ok)"
    if ($r.ok) { $fail++ }

    if ($fail -gt 0) { "SELFTEST=FAIL ($fail)"; exit 1 }
    "SELFTEST=PASS"
    exit 0
}

if (-not $ApiBase) { $ApiBase = "https://api.github.com/repos/$Repo" }
$U = $ApiBase
$isReal = $U -match '^https://api\.github\.com/'

if ($isReal) {
    $cred = (@("protocol=https", "host=github.com", "") -join "`n") | git credential fill
    $tok = ($cred -split "`n" | Where-Object { $_ -match '^password=' }) -replace '^password=', ''
    if (-not $tok) { throw "no GitHub credential from 'git credential fill'" }
} else {
    $tok = 'not-a-real-token'
}
$A = "Bearer $tok"

# curl.exe on Windows (5.1 aliases `curl` to Invoke-WebRequest, so the extension matters);
# plain curl elsewhere. urllib is deliberately not used: it hangs against api.github.com
# on the machine this was written on, while curl works.
$curl = if (Get-Command curl.exe -ErrorAction SilentlyContinue) { 'curl.exe' } else { 'curl' }

function Api($method, $url, $body) {
    $a = @("-sS", "-m", "60", "-X", $method, "-H", "Authorization: $A",
        "-H", "User-Agent: openworker-posture", "-H", "Accept: application/vnd.github+json")
    $f = $null
    if ($body) {
        $f = [IO.Path]::Combine([IO.Path]::GetTempPath(), "merge-body-$([guid]::NewGuid().ToString('N')).json")
        [IO.File]::WriteAllText($f, $body, (New-Object System.Text.UTF8Encoding($false)))
        $a += @("-H", "Content-Type: application/json", "--data-binary", "@$f")
    }
    $a += $url
    try {
        $raw = & $curl @a
        if ($LASTEXITCODE -ne 0) { throw "curl failed ($LASTEXITCODE): $method $url" }
    } finally {
        if ($f) { Remove-Item $f -Force -ErrorAction SilentlyContinue }
    }
    if (-not $raw) { return [pscustomobject]@{} }
    return $raw | ConvertFrom-Json
}

"--- pre-merge state ---"
$p = Api "GET" "$U/pulls/$Pr" $null
"pr=$($p.number) state=$($p.state) mergeable=$($p.mergeable) mergeable_state=$($p.mergeable_state)"
"head=$($p.head.sha) expected=$(if ($ExpectedSha) { $ExpectedSha } else { '(unchecked)' })"
if ($p.state -ne 'open') { throw "PR state=$($p.state) -- refusing to merge" }
if ($ExpectedSha -and $p.head.sha -ne $ExpectedSha) { throw "head moved since the reviewed run -- refusing to merge" }
if ($p.mergeable_state -ne "clean") { throw "mergeable_state=$($p.mergeable_state) -- refusing to merge" }

if ($DryRun) {
    "--- dry run: guards passed, skipping the PUT ---"
    exit 0
}

"--- merge ---"
$body = @{
    merge_method   = $MergeMethod
    commit_title   = if ($Title) { $Title } else { "Merge pull request #$Pr" }
    commit_message = $Message
    sha            = $p.head.sha
} | ConvertTo-Json
$outcome = Resolve-MergeOutcome (Api "PUT" "$U/pulls/$Pr/merge" $body)
$outcome.text

"--- post-merge ---"
Start-Sleep -Seconds 4
$main = Api "GET" "$U/git/ref/heads/main" $null
"main=$($main.object.sha)"
$after = Api "GET" "$U/pulls/$Pr" $null
"pr_state=$($after.state) merged=$($after.merged) merged_at=$($after.merged_at)"

if (-not $outcome.ok) { exit 1 }
