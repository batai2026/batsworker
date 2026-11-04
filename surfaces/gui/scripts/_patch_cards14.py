# -*- coding: utf-8 -*-
"""Delta patch: append standing-goal keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    ("Active", "启用中"),
    ("Event-triggered — the condition can't be edited here. Recreate the automation to change it.", "事件触发——条件无法在此编辑。如需更改请重建该自动化。"),
]

entries = re.findall(r'^  ("(?:[^"\\]|\\.)*"|[A-Za-z_$][\w$]*): (.+),$', text, re.M)
existing = {k for k, _ in entries}
newcnt = 0
for k, v in add:
    kk = k if k.isidentifier() and not k[0].isdigit() else '"' + k.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if kk in existing:
        continue
    vv = '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    entries.append((kk, vv))
    newcnt += 1
entries.sort(key=lambda kv: kv[0].strip('"').lower())
out = ["// Strings from cards and secondary views: approvals, inbox, scheduled, audit, teams, right rail.",
       "export const zhCards = {"]
out += [f"  {k}: {v}," for k, v in entries]
out.append("} satisfies Record<string, string>;")
io.open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
print("added:", newcnt, "total:", len(entries))
