#requires -Version 5.1
<#
.SYNOPSIS
  Build the Coworker Windows desktop app + NSIS (.exe) and MSI installers.

.DESCRIPTION
  The Windows counterpart to build_dmg.sh:
    1. PyInstaller-bundle the server into a standalone onedir folder (no venv at runtime).
    2. Stage it at binaries\sidecar\ for Tauri's `resources` slot.
    3. `tauri build --bundles nsis,msi` -> Coworker NSIS setup .exe + .msi (resources copied in).

  Prerequisites (see the toolchain notes in the PR/plan):
    - Rust (rustup) with the x86_64-pc-windows-msvc target + the MSVC C++ build tools (link.exe).
    - Node + npm (frontend build).
    - A Python venv at platform\.venv with this package installed editable, plus pyinstaller.
      `typer` is needed only at build time: PyInstaller walks the `mcp` package and `mcp.cli`
      calls sys.exit() at import if typer is absent, which aborts the freeze.
        py -m venv .venv ; .\.venv\Scripts\pip install -e ".[bedrock]" pyinstaller tzdata typer

  The result is UNSIGNED — first launch shows a SmartScreen warning ("More info" -> "Run anyway").
  Authenticode signing is a later step.

  AUTO-UPDATE ARTIFACTS (the .sig the updater verifies) need TAURI_SIGNING_PRIVATE_KEY and
  TAURI_SIGNING_PRIVATE_KEY_PASSWORD: exported by the caller/CI, or listed in `.ocw-updater.env`
  one directory ABOVE the repo (same convention as `.ocw-notary.env`, never committed). The key
  entry may be the key material itself or a path to the key file. No key -> installers without
  .sig, i.e. NOT releasable.

  Experimental (use-at-your-own-risk) connectors are EXCLUDED from this build by default —
  the spec strips coworker.connectors.experimental. Self-builders can opt in with:
    $env:COWORKER_EXPERIMENTAL = "1"; .\build_windows.ps1
#>
[CmdletBinding()]
param(
    # Which installer bundles to produce. Both by default.
    [string]$Bundles = "nsis,msi"
)
$ErrorActionPreference = "Stop"

$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Platform = Split-Path -Parent $Here
$Gui      = Join-Path $Platform "surfaces\gui"
$Venv     = Join-Path $Platform ".venv"
$PyInst   = Join-Path $Venv "Scripts\pyinstaller.exe"

function Require-Cmd($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Required tool '$name' not found on PATH. See the prerequisites in this script's header."
    }
}

Require-Cmd rustc
Require-Cmd npm
if (-not (Test-Path $PyInst)) {
    throw "PyInstaller not found at $PyInst. Create the venv and install deps (see header)."
}

# Host target triple, e.g. x86_64-pc-windows-msvc — Tauri's externalBin suffix.
$Triple = (& rustc -vV | Select-String '^host:').ToString().Split()[-1]
$Arch   = $Triple.Split('-')[0]

# A running openworker-server.exe (e.g. a prior sidecar/smoke test) locks the output exe and
# makes PyInstaller's overwrite fail with Access-is-denied. Stop any before bundling.
$running = Get-Process -Name "openworker-server" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "==> stopping $($running.Count) running openworker-server process(es) holding the output exe"
    $running | Stop-Process -Force
    Start-Sleep -Seconds 1
}

Write-Host "==> [1/3] PyInstaller: bundling openworker-server ($Triple)" -ForegroundColor Cyan
& $PyInst --noconfirm --clean `
    --distpath (Join-Path $Here "dist") --workpath (Join-Path $Here "build") `
    (Join-Path $Here "openworker-server.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

# The shell (src-tauri/src/lib.rs) passes flags to this binary that only the Python CLI
# defines. A bundle older than a CLI change still starts, then dies at launch with
# "unrecognized arguments" -- nothing else in the build catches that, so compare the two
# CLIs here and fail before the stale binary is staged.
Write-Host "==> [1.5/3] checking the bundled CLI against the Python CLI" -ForegroundColor Cyan
& (Join-Path $Venv "Scripts\python.exe") (Join-Path $Platform "scripts\check_sidecar_cli.py") `
    (Join-Path $Here "dist\openworker-server\openworker-server.exe")
if ($LASTEXITCODE -ne 0) { throw "bundled sidecar CLI is stale (rebuild after CLI changes)" }

Write-Host "==> [2/3] staging sidecar resources" -ForegroundColor Cyan
# Onedir bundle (exe + _internal\) ships via Tauri `resources`, landing at <install>\sidecar\
# next to the app exe — onefile's per-launch self-extraction cost seconds of boot splash.
$BinDir = Join-Path $Gui "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$Src = Join-Path $Here "dist\openworker-server"
$Dst = Join-Path $BinDir "sidecar"
if (Test-Path $Dst) { Remove-Item -Recurse -Force $Dst }
# Clear any stale onefile binary from pre-onedir builds.
Remove-Item -Force (Join-Path $BinDir "openworker-server-$Triple.exe") -ErrorAction SilentlyContinue
Copy-Item -Recurse -Force $Src $Dst
Write-Host "    -> $Dst"

Write-Host "==> [3/3] tauri build (--bundles $Bundles)" -ForegroundColor Cyan
# Auto-update artifacts (NSIS setup .exe + minisign .sig): produced only when the updater
# signing key is present -- exported by CI (secret TAURI_SIGNING_PRIVATE_KEY) or by the
# caller, else read from `.ocw-updater.env` one directory ABOVE the repo (same convention
# as `.ocw-notary.env`: shared by every clone/worktree, never committed). Keyless builds
# skip the overlay so dev builds keep working; keyless RELEASES strand installs without
# auto-update, hence the loud warning rather than silence.
function Read-DotEnv([string]$Path) {
    # Minimal `.env` reader -- NOT a general parser. Handles `export` prefixes, `#` comments,
    # 'single'/"double" quotes, and a double-quoted value spanning lines (a minisign key is
    # two lines, so TAURI_SIGNING_PRIVATE_KEY="untrusted comment: ...
    # RW..." has to survive). Values may also be a bare path to the key file.
    $lines = ((Get-Content -LiteralPath $Path -Raw) -replace "`r`n", "`n").Split("`n")
    $out = @{}
    $open = $null; $buf = $null
    foreach ($line in $lines) {
        if ($null -ne $open) {
            $buf += "`n" + $line
            # Closing line ends WITH the quote (base64 bodies never contain one).
            if ($line -match '"\s*$') { $out[$open] = ($buf -replace '"\s*$', ''); $open = $null }
            continue
        }
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#')) { continue }
        $t = $t -replace '^export\s+', ''
        if ($t -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { continue }
        $name = $Matches[1]; $value = $Matches[2]
        if ($value.StartsWith('"')) {
            if ($value.Length -gt 1 -and $value.Substring(1).TrimEnd().EndsWith('"')) {
                $out[$name] = $value.Substring(1, $value.LastIndexOf('"') - 1)
            } else { $open = $name; $buf = $value.Substring(1) }
        } else {
            # Unquoted: a `#` preceded by whitespace starts a comment (quoted values keep theirs,
            # so a password containing # survives as long as it is quoted).
            $out[$name] = ($value -replace '\s+#.*$', '').Trim().Trim("'").Trim()
        }
    }
    if ($null -ne $open) { throw "$Path`: unterminated quoted value for $open" }
    return $out
}

$UpdaterEnvFile = if ($env:OCW_UPDATER_ENV) { $env:OCW_UPDATER_ENV } else { Join-Path $Platform "..\.ocw-updater.env" }
if (-not $env:TAURI_SIGNING_PRIVATE_KEY -and (Test-Path -LiteralPath $UpdaterEnvFile)) {
    Write-Host "    updater signing env: $UpdaterEnvFile"
    $dotenv = Read-DotEnv -Path $UpdaterEnvFile
    foreach ($name in 'TAURI_SIGNING_PRIVATE_KEY', 'TAURI_SIGNING_PRIVATE_KEY_PASSWORD') {
        if ($dotenv[$name]) {
            [Environment]::SetEnvironmentVariable($name, $dotenv[$name])
            # minisign key material always starts with "untrusted comment:" -- anything else is
            # meant as a path, so fail here instead of late inside the bundler.
            if ($name -eq 'TAURI_SIGNING_PRIVATE_KEY' -and
                $dotenv[$name] -notmatch 'untrusted comment:' -and
                -not (Test-Path -LiteralPath $dotenv[$name])) {
                throw "TAURI_SIGNING_PRIVATE_KEY in $UpdaterEnvFile is neither key content nor an existing file."
            }
        }
    }
}

$UpdaterArgs = @()
if ($env:TAURI_SIGNING_PRIVATE_KEY) {
    # Pass the overlay as a FILE: inline JSON loses its quotes through the
    # PowerShell -> npm.cmd -> cmd hop ("key must be a string", v0.1.3 run).
    $Overlay = Join-Path ([IO.Path]::GetTempPath()) "ocw-updater-overlay.json"
    Set-Content -Path $Overlay -Value '{"bundle":{"createUpdaterArtifacts":true}}' -Encoding ascii
    $UpdaterArgs = @("--config", $Overlay)
} else {
    Write-Host "    WARNING: no updater signing key - building WITHOUT auto-update artifacts (not releasable)." -ForegroundColor Yellow
}
Push-Location $Gui
try {
    & npm run tauri build -- --bundles $Bundles @UpdaterArgs
    if ($LASTEXITCODE -ne 0) { throw "tauri build failed (exit $LASTEXITCODE)" }
}
finally {
    Pop-Location
}

$BundleDir = Join-Path $Gui "src-tauri\target\release\bundle"
Write-Host ""
Write-Host "Done. Installers under: $BundleDir" -ForegroundColor Green
Get-ChildItem -Path $BundleDir -Recurse -Include *.exe, *.msi -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host "  $($_.FullName)" }
