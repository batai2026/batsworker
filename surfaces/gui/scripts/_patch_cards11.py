# -*- coding: utf-8 -*-
"""Fix apostrophe/plural keys in zh/cards.ts: curly-apostrophe swaps + singular variants."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

# 1) Straight -> curly apostrophe keys (restores the original EN typographic rendering)
swap = [
    ('"reviewer wasn\'t sure: {note}"', '"reviewer wasn\u2019t sure: {note}"'),
    ('"You\'re signed in"', '"You\u2019re signed in"'),
]
for old, new in swap:
    assert old in text, old
    text = text.replace(old, new, 1)

# 2) Add singular variants + new fragments
add = [
    ("1 folder", "1 个文件夹"),
    ("1 run", "1 次运行"),
    ("1 new run", "1 次新运行"),
    (" — the latest failed", "——最近一次失败"),
    ("1 entry", "1 条记录"),
    ("1 tool call so far.", "已进行 1 次工具调用。"),
    (" with 1 tool call so far.", "，已进行 1 次工具调用。"),
    ("1 more item", "还有 1 项"),
    ("Proposed team — 1 worker", "提议的团队——1 名 worker"),
    ("1 project command allowance", "1 项项目命令许可"),
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
       "export const zhCards: Record<string, string> = {"]
out += [f"  {k}: {v}," for k, v in entries]
out.append("};")
io.open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
print("added:", newcnt, "total:", len(entries))
