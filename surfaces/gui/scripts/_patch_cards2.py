# -*- coding: utf-8 -*-
"""Delta patch: append inbox/project-menu keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    ("All coworkers", "全部协作者"),
    ("Also delivered to", "同时投递到"),
    ("Approvals, questions, and notifications from your coworkers — including sessions running unattended.", "来自你协作者的审批、提问和通知——包括无人值守运行的会话。"),
    ("Configure", "配置"),
    ("Delivered here only.", "仅投递到此处。"),
    ("Delivered here only. Connect Slack (Connectors page) to also get these in a channel — more platforms later.", "仅投递到此处。连接 Slack（连接器页面）后也可在频道中收到——后续支持更多平台。"),
    ("Filter…", "筛选…"),
    ("Name current {kind}…", "命名当前{kind}…"),
    ("Name this {kind}…", "为这个{kind}命名…"),
    ("Nothing pending for this filter.", "此筛选条件下暂无待处理项。"),
    ("Nothing pending.", "暂无待处理项。"),
    ("Open “{label}”", "打开“{label}”"),
    ("Pending", "待处理"),
    ("Session unavailable", "会话不可用"),
    ("View & edit…", "查看与编辑…"),
    ("bound", "已绑定"),
    ("could not bind", "绑定失败"),
    ("could not name", "命名失败"),
    ("no matches", "无匹配"),
    ("none", "无"),
    ("this folder", "此文件夹"),
    ("this repo", "此仓库"),
    ("— replies there resolve items here.", "——在那里回复即可处理此处的待办。"),
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
