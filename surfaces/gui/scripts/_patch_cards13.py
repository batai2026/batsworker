# -*- coding: utf-8 -*-
"""Delta patch: append consent-badge keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    ("Signed", "已签名"),
    ("trusted", "已信任"),
    ("Signature check failed", "签名校验失败"),
    ("Signed · key unverified", "已签名 · 密钥未验证"),
    ("Signed by a key you trust.", "由你信任的密钥签名。"),
    ("Signed, but no key was provided to verify who signed it.", "已签名，但未提供可用于验证签名者身份的密钥。"),
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
