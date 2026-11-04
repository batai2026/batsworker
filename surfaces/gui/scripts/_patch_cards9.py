# -*- coding: utf-8 -*-
"""Delta patch: append InboxConfigure keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    ("+ Subscribe", "+ 订阅"),
    ("Channel subscriptions", "频道订阅"),
    ("Channel where an Unattended session posts Approve/Deny buttons. Currently mirroring to", "无人值守会话发布“批准/拒绝”按钮的频道。当前镜像到"),
    ("Choose a session…", "选择会话…"),
    ("Choose an approval owner under Integrations → Slack before routing approvals here.", "在将审批路由到此之前，请在 连接器 → Slack 中选择审批负责人。"),
    ("Could not clear Inbox routing.", "无法清除收件箱路由。"),
    ("Could not update Inbox routing.", "无法更新收件箱路由。"),
    ("Direct messages", "私信"),
    ("Inbound messages and background-turn failures nothing claimed — nothing vanishes silently.", "无人认领的入站消息与后台回合失败——不会悄无声息地消失。"),
    ("Inbox routes to", "收件箱路由至"),
    ("Listens to", "监听"),
    ("Message", "消息"),
    ("No channel subscriptions yet — add one below or ask a coworker to watch a channel.", "暂无频道订阅——在下方添加，或让协作者盯一个频道。"),
    ("No session — park DMs", "无会话——私信停靠"),
    ("Nothing here — no dropped messages or failed turns.", "这里空空如也——没有丢失的消息或失败的回合。"),
    ("Reason", "原因"),
    ("Session", "会话"),
    ("Session that handles DMs to the bot. With none, DMs park under Unrouted below.", "处理发给 bot 的私信的会话。未选择时，私信停靠在下方的“未路由”区。"),
    ("Set", "设置"),
    ("Source", "来源"),
    ("This channel is also your Inbox-routing target — inbound and outbound on one channel conflate broadcast with request/reply.", "该频道同时是你的收件箱路由目标——收发共用一个频道会把广播与请求/回复混在一起。"),
    ("Unattended approvals", "无人值守审批"),
    ("Unrouted", "未路由"),
    ("When", "时间"),
    ("clear", "清除"),
    ("collides", "冲突"),
    ("in-app Inbox only", "仅应用内收件箱"),
    ("— sessions that listen to a channel (inbound)", "——监听频道的会话（入站）"),
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
