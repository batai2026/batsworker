# -*- coding: utf-8 -*-
"""Scan the four zh dict files for entry lines that don't match the key: value, shape."""
import io, re

pat = re.compile(r'^  ("(?:[^"\\]|\\.)*"|[A-Za-z_$][\w$]*): ("(?:[^"\\]|\\.)*"|[A-Za-z_$][\w$]*),$')

for p in [
    "src/i18n/zh/app.ts",
    "src/i18n/zh/shell.ts",
    "src/i18n/zh/settings.ts",
    "src/i18n/zh/cards.ts",
]:
    lines = io.open(p, encoding="utf-8").read().splitlines()
    ok = 0
    bad = []
    for i, l in enumerate(lines):
        if not l or l.startswith(("//", "export", "}")):
            continue
        if pat.match(l):
            ok += 1
        else:
            bad.append((i + 1, l[:150]))
    print(p, "entries:", ok, "BAD:", len(bad))
    for n, l in bad[:8]:
        print(f"  line {n}: {l}")
