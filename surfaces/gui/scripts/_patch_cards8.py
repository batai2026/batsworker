# -*- coding: utf-8 -*-
"""Delta patch: append TeamChat/ModelChecklist keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    ("Add another model…", "添加其他模型…"),
    ("Back to session", "返回会话"),
    ("Make default", "设为默认"),
    ("Message # team chat…  (posts as you — every member sees it)", "发消息到 # 团队聊天…（以你的身份发送——所有成员可见）"),
    ("No messages yet. Agents post here only when something needs a reply — @mention a coworker to reach it.", "暂无消息。agent 只在有需要回复的内容时才在这里发言——@提及某个协作者即可联系它。"),
    ("The default model is always shown — make another model default first", "默认模型始终显示——请先将其他模型设为默认"),
    ("You", "你"),
    ("default", "默认"),
    ("questions & consensus — status lives on the board", "提问与共识——状态记录在看板上"),
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
