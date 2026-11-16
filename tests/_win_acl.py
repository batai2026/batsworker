"""Read a Windows ACL as data, so tests never parse localized `icacls` text.

Why this exists
---------------
`icacls <path>` prints human-readable output, and the parts the ACL tests care about are
exactly the parts that get localized: principal names (`NT AUTHORITY\\SYSTEM` becomes
`NT AUTHORITY\\SYSTEM` on en-US but a translated name on other locales) and the status
lines. Worse, the byte encoding is not fixed either -- on a simplified-Chinese Windows
the console codepage is GBK, so decoding as UTF-8 silently corrupts hunks of the output
and any assertion built on them passes or fails by accident.

SIDs are the stable identity underneath: they are ASCII, locale-independent, and do not
depend on console encodings. So the ACL is read through .NET, converted to SIDs, and
returned as plain dicts. The tests then assert on structure:

    no inherited ACEs           -- inheritance really was stripped
    every Allow ACE's SID == me -- only the current user is granted

which is what "restricted to this user" means, rather than a substring search that can
be satisfied by the wrong principal.

`powershell.exe` is present on every supported Windows version (Windows PowerShell 5.1),
so this needs no extra dependency. Output goes through a UTF-8 file rather than stdout
pipes for the same encoding reason: one unambiguous channel.

Why not just parse `icacls`: its first output line is the file's PATH, and on Windows
that path lives under the user's profile. So the username is present in the output
regardless of the ACL, which makes `USERNAME in output` a test that cannot fail. Verified
directly -- a file whose only ACE belongs to Everyone still matches it.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# -NonInteractive: never wait on a prompt. -NoProfile: a user profile script cannot
# change the output shape. Output written to a file, encoded utf8 explicitly.
#
# The DACL is read through .NET rather than the `Get-Acl` cmdlet, because `Get-Acl`
# lives in Microsoft.PowerShell.Security and depends on module auto-loading. GitHub's
# Windows runner ships a PSModulePath that does not resolve it, so `Get-Acl` fails there
# with "CouldNotAutoloadMatchingModule" while working fine on a normal desktop -- a test
# helper that only works on the developer's machine is not a test helper. The cmdlet is
# kept as a fallback, imported by explicit path rather than by auto-discovery.
_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$target = $env:PROBE_TARGET

function Read-Dacl([string] $path) {
    # .NET first: no module lookup involved.
    try {
        $fi = New-Object System.IO.FileInfo($path)
        return $fi.GetAccessControl()
    } catch {
        $firstError = $_.Exception.Message
    }
    # Fallback: load the shell's own copy of the module, by explicit path.
    $manifest = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
    if (Test-Path -LiteralPath $manifest) {
        Import-Module -Name $manifest -ErrorAction Stop
        return Get-Acl -LiteralPath $path
    }
    throw "could not read the DACL of ${path}: ${firstError}"
}

$acl = Read-Dacl $target
$aces = @($acl.Access | ForEach-Object {
    [pscustomobject]@{
        sid       = $_.IdentityReference.Translate(
                        [System.Security.Principal.SecurityIdentifier]).Value
        type      = $_.AccessControlType.ToString()
        inherited = [bool]$_.IsInherited
        rights    = $_.FileSystemRights.ToString()
    }
})
$result = [pscustomobject]@{
    me              = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    owner           = $acl.Owner
    inheritance_off = [bool]$acl.AreAccessRulesProtected
    aces            = $aces
}
$json = $result | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($env:PROBE_OUT, $json, (New-Object System.Text.UTF8Encoding($false)))
"""


@dataclass(frozen=True)
class Ace:
    sid: str
    type: str  # "Allow" / "Deny"
    inherited: bool
    rights: str


# Identities that can read any file regardless of its DACL, so their presence is not a
# widening: LocalSystem and BUILTIN\Administrators can take ownership, and S-1-3-4
# (OWNER RIGHTS) describes the owner's own rights rather than granting a third party.
ALWAYS_PRIVILEGED_SIDS = frozenset({
    "S-1-5-18",       # NT AUTHORITY\SYSTEM
    "S-1-5-32-544",   # BUILTIN\Administrators
    "S-1-3-4",        # OWNER RIGHTS
})


@dataclass(frozen=True)
class WindowsAcl:
    me: str  # SID of the identity running the test
    owner: str
    inheritance_off: bool
    aces: tuple[Ace, ...]

    @property
    def allow_sids(self) -> set[str]:
        return {a.sid for a in self.aces if a.type == "Allow"}

    @property
    def inherited_aces(self) -> list[Ace]:
        return [a for a in self.aces if a.inherited]

    @property
    def broad_allow_sids(self) -> set[str]:
        """Grantees other than me and the always-privileged identities.

        The point of the secrets-file check is "no one who could not already read the
        whole disk has access". `LocalSystem` and `BUILTIN\\Administrators` can read any
        file regardless of its DACL -- they can take ownership -- and `OWNER RIGHTS`
        (S-1-3-4) describes the owner's rights rather than granting a third party
        anything. Those appear on a freshly created file under a normal temp directory
        and are not a widening, so treating them as a leak would fail a correctly
        restricted file. The current user is not a third party either.

        Everything else counts. Everyone, Users, Authenticated Users, a specific other
        account -- all of those are real leaks and stay in the set.
        """
        return self.allow_sids - ALWAYS_PRIVILEGED_SIDS - {self.me}

    def describe(self) -> str:
        """Readable dump for assertion messages -- never the assertion itself."""
        lines = [f"me={self.me} inheritance_off={self.inheritance_off}"]
        for a in self.aces:
            lines.append(f"  {a.sid} inherited={a.inherited} {a.type} {a.rights}")
        return "\n".join(lines)


def read_acl(path: Path | str) -> WindowsAcl:
    """Return the DACL of `path` as SIDs. Windows only; raises on any failure.

    Deliberately strict: a failed read must be an error, not an empty ACL that would
    make "nothing is inherited" pass vacuously.
    """
    if os.name != "nt":
        raise RuntimeError("read_acl is Windows-only")

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "acl.json"
        env = {**os.environ, "PROBE_TARGET": str(path), "PROBE_OUT": str(out)}
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
            capture_output=True,
            env=env,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"reading the DACL failed for {path}: {stderr}")
        data = json.loads(out.read_text(encoding="utf-8-sig"))

    raw_aces = data.get("aces") or []
    if isinstance(raw_aces, dict):  # a single ACE serializes as an object
        raw_aces = [raw_aces]
    return WindowsAcl(
        me=data["me"],
        owner=data.get("owner") or "",
        inheritance_off=bool(data.get("inheritance_off")),
        aces=tuple(
            Ace(
                sid=a["sid"],
                type=a["type"],
                inherited=bool(a["inherited"]),
                rights=a.get("rights") or "",
            )
            for a in raw_aces
        ),
    )
