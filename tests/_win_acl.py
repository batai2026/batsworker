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
_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $env:PROBE_TARGET
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
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $env:PROBE_OUT -Encoding utf8
"""


@dataclass(frozen=True)
class Ace:
    sid: str
    type: str  # "Allow" / "Deny"
    inherited: bool
    rights: str


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
            raise RuntimeError(f"Get-Acl failed for {path}: {stderr}")
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
