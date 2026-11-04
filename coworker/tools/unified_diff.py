"""Minimal unified diff applier (pure Python).

Enough of the unified diff format to apply agent-generated patches (as produced
by the aisuite toolkit's ``apply_patch`` / ``apply_unified_diff``) to file
content: ``---``/``+++`` headers, ``@@ -l,c +l,c @@`` hunks with context (`` ``),
removals (``-``), and additions (``+``). Used by the sandboxed ``apply_patch``
tool: the container-read content is patched here, then written back into the
container.

Hunks are applied **bottom-to-top** so each hunk's line numbers refer to the
original content (earlier indices stay valid after lower hunks are applied).
Context/removal lines are validated against the content; any mismatch fails
loudly instead of corrupting the file.
"""

from __future__ import annotations

import re
from typing import Optional

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def apply_unified_diff(content: str, patch: str) -> tuple[Optional[str], Optional[str]]:
    """Apply a unified diff to ``content``; returns (new_content, error)."""
    lines = content.splitlines(keepends=True)
    patch_lines = (patch or "").splitlines()

    # -- parse hunks ----------------------------------------------------------
    hunks: list[tuple[int, int, list[str]]] = []
    i = 0
    while i < len(patch_lines) and not patch_lines[i].startswith("@@"):
        i += 1
    while i < len(patch_lines):
        m = _HUNK_RE.match(patch_lines[i].strip())
        if not m:
            return None, f"malformed hunk header: {patch_lines[i]!r}"
        old_start = int(m.group(1))
        old_count = int(m.group(2) or 1)
        i += 1
        body: list[str] = []
        while i < len(patch_lines) and not patch_lines[i].startswith("@@"):
            line = patch_lines[i]
            tag = line[:1]
            if tag in (" ", "-", "+"):
                body.append(line if line else " ")
            elif line.startswith(r"\ No newline"):
                body.append(line)
            elif line == "":
                # Tolerate a bare blank separator between lines.
                body.append(" ")
            else:
                return None, f"unexpected hunk line: {line!r}"
            i += 1
        hunks.append((old_start, old_count, body))

    if not hunks:
        return None, "patch contains no hunks"

    # -- apply bottom-to-top --------------------------------------------------
    for old_start, old_count, body in reversed(hunks):
        old_begin = old_start - 1
        old_end = old_begin + old_count
        if old_end > len(lines):
            return None, (
                f"hunk starts at {old_start} but content has only {len(lines)} lines"
            )
        new_segment: list[str] = []
        pos = old_begin
        removed = 0
        for line in body:
            if line.startswith(r"\ No newline"):
                continue
            tag = line[0]
            text = line[1:]
            if tag in (" ", "-"):
                if pos >= len(lines) or lines[pos].rstrip("\n") != text.rstrip("\n"):
                    return None, f"context/removal mismatch at line {pos + 1}"
                if tag == " ":
                    new_segment.append(lines[pos])
                removed += 1
                pos += 1
            elif tag == "+":
                new_segment.append(text + "\n")
        if removed != old_count:
            return None, f"hunk covers {removed} lines but header says {old_count}"
        lines[old_begin:old_end] = new_segment

    return "".join(lines), None