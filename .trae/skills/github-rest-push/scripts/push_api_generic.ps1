# Push the current HEAD to GitHub via the REST API (blob -> tree -> commit -> PATCH ref).
# Use when direct `git push` over HTTPS fails (github.com:443 blocked, DNS pollution,
# empty reply, getaddrinfo errors) but api.github.com is reachable.
#
# Usage:
#   .\push_api_generic.ps1 [targetBranch]
#   targetBranch defaults to "main".
#
# Requires a stored GitHub OAuth token (git credential fill from
# protocol=https / host=github.com). The pushed tree matches the local commit;
# the remote commit SHA may differ from local because GitHub normalizes the
# author/committer timezone fields.
param(
    [string]$Branch = "main"
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.ASCIIEncoding]::new()

function Resolve-RepoSlug {
    $url = git remote get-url origin
    if (-not $url) { throw "no origin remote" }
    if ($url -match 'github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$') { return $Matches[1] }
    throw "cannot parse repo slug from origin url: $url"
}

$cred = (@("protocol=https", "host=github.com", "") -join "`n") | git credential fill
$tok = ($cred -split "`n" | Where-Object { $_ -match '^password=' }) -replace '^password=', ''
if (-not $tok) { throw "no GitHub OAuth token in credential store" }
$repo = Resolve-RepoSlug
$base = "https://api.github.com/repos/$repo"

function GhApi($method, $path, $bodyFile) {
    $args = @('-sS', '-f', '-X', $method,
        '-H', "Authorization: Bearer $tok",
        '-H', 'User-Agent: trae-skill',
        '-H', 'Accept: application/vnd.github+json',
        '-H', 'Content-Type: application/json')
    if ($bodyFile) { $args += @('--data-binary', "@$bodyFile") }
    $args += "$base$path"
    $raw = & curl.exe @args
    if ($LASTEXITCODE -ne 0) { throw "curl $method $path failed: $raw" }
    return ($raw | ConvertFrom-Json)
}

$sha = (git rev-parse HEAD).Trim()
$parent = (git rev-parse "$sha^").Trim()
$parentTree = (git rev-parse "$sha^^{tree}").Trim()
$localTree = (git rev-parse "$sha^{tree}").Trim()
$msg = ((git show -s --format=%B $sha) -join "`n").TrimEnd()
$aParts = ((git show -s --format="%an|%ae|%aI" $sha) -split '\|')
$cParts = ((git show -s --format="%cn|%ce|%cI" $sha) -split '\|')

$files = git diff --name-only --diff-filter=ACMRT "$parent" "$sha"
if (-not $files) { throw "no changed files between parent and HEAD" }
"repo=$repo branch=$Branch changed=$($files.Count)"

$entries = @()
foreach ($f in $files) {
    $blobSha = (git rev-parse "${sha}:$f").Trim()
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "git"; $psi.Arguments = "cat-file blob $blobSha"
    $psi.RedirectStandardOutput = $true; $psi.UseShellExecute = $false
    $p = [System.Diagnostics.Process]::Start($psi)
    $ms = New-Object System.IO.MemoryStream
    $p.StandardOutput.BaseStream.CopyTo($ms); $p.WaitForExit()
    $b64 = [Convert]::ToBase64String($ms.ToArray())
    $bodyFile = Join-Path $env:TEMP "gh_blob_body.json"
    [IO.File]::WriteAllText($bodyFile, (@{ content = $b64; encoding = "base64" } | ConvertTo-Json), [Text.Encoding]::ASCII)
    $blob = GhApi "POST" "/git/blobs" $bodyFile
    $entries += @{ path = $f; mode = "100644"; type = "blob"; sha = $blob.sha }
}

$treeFile = Join-Path $env:TEMP "gh_tree_body.json"
[IO.File]::WriteAllText($treeFile, (@{ base_tree = $parentTree; tree = $entries } | ConvertTo-Json -Depth 5), [Text.Encoding]::ASCII)
$tree = GhApi "POST" "/git/trees" $treeFile

# UTF-8 (not ASCII) so non-ASCII commit messages survive.
$commitFile = Join-Path $env:TEMP "gh_commit_body.json"
[IO.File]::WriteAllText($commitFile, (@{
    message = $msg
    tree = $tree.sha
    parents = @($parent)
    author = @{ name = $aParts[0]; email = $aParts[1]; date = $aParts[2] }
    committer = @{ name = $cParts[0]; email = $cParts[1]; date = $cParts[2] }
} | ConvertTo-Json -Depth 5), [Text.Encoding]::UTF8)
$commit = GhApi "POST" "/git/commits" $commitFile

$refFile = Join-Path $env:TEMP "gh_ref_body.json"
[IO.File]::WriteAllText($refFile, (@{ sha = $commit.sha; force = $false } | ConvertTo-Json), [Text.Encoding]::ASCII)
GhApi "PATCH" "/git/refs/heads/$Branch" $refFile | Out-Null

"local=$sha"
"remote=$($commit.sha)"
"tree_match=$($tree.sha -eq $localTree)"
