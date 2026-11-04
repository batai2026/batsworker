# -*- coding: utf-8 -*-
"""Delta patch: append checkpoint keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    ("Checkpoints", "检查点"),
    ("{n} files", "{n} 个文件"),
    (" · {n} skipped", " · {n} 个跳过"),
    ("Confirm rollback?", "确认回滚？"),
    ("Restore files to the end of this turn", "把文件恢复到本回合结束时的状态"),
    ("Rolled back: {restored} restored, {deleted} removed.", "已回滚：恢复 {restored} 个，删除 {deleted} 个。"),
    ("Nothing to roll back.", "没有可回滚的内容。"),
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
