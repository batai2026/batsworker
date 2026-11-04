# -*- coding: utf-8 -*-
"""Delta patch: append small-component keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    (" as {account}", "（账号：{account}）"),
    ("30+ more tools on the Connectors page — add or remove anytime. Tokens stay on this computer.", "连接器页面还有 30+ 个工具——随时增删。Token 只保存在这台电脑上。"),
    ("A weekly digest, a morning brief — pick a template, running in two minutes.", "每周摘要、晨报——选个模板，两分钟内跑起来。"),
    ("Board wake", "看板动态"),
    ("Channels this session listens to", "本会话监听的频道"),
    ("Chat can only advise. Connected, your coworker does the actual work:", "纯聊天只能给建议。连接之后，你的协作者能真正干活："),
    ("Check your browser…", "请查看你的浏览器…"),
    ("Coming soon", "即将上线"),
    ("Coming soon — pending Google's app verification.", "即将上线——等待 Google 应用审核。"),
    ("Connect", "连接"),
    ("Connect a tool above with one click — or add them anytime later from the Connectors page.", "在上方一键连接工具——之后也可随时在连接器页面添加。"),
    ("Connect your everyday tools", "连接你的日常工具"),
    ("Connected", "已连接"),
    ("Continue without sign-in", "不登录继续"),
    ("Create your first automation", "创建你的第一个自动化"),
    ("Gmail & Google Calendar", "Gmail 与 Google 日历"),
    ("Models can be enabled or hidden anytime in Settings ▸ Models.", "模型可随时在 设置 ▸ 模型 中启用或隐藏。"),
    ("Next", "下一步"),
    ("Not subscribed to any channel.", "尚未订阅任何频道。"),
    ("Nothing works without a model", "没有模型将无法工作——"),
    ("Open a session and just ask — analyze files, draft, research, build.", "开一个会话直接提需求——分析文件、写草稿、做调研、搭东西。"),
    ("OpenWorker handles the OAuth for 20+ tools — no dev consoles, no pasted keys. Tokens stay on this computer.", "OpenWorker 为 20+ 工具代办 OAuth——无需开发者控制台、无需粘贴密钥。Token 只保存在这台电脑上。"),
    ("Opening browser…", "正在打开浏览器…"),
    ("Pick a model provider to get started — OpenWorker runs on your own key, and your key and your data stay on this computer.", "选择一个模型提供商即可开始——OpenWorker 使用你自己的 key，你的密钥和数据都留在这台电脑上。"),
    ("Replay this setup anytime: Settings ▸ Appearance ▸ Run setup again.", "随时可重放此设置：设置 ▸ 通用 ▸ 重新运行设置。"),
    ("Sign in", "登录"),
    ("Sign in for one-click connections", "登录以启用一键连接"),
    ("Skip setup", "跳过设置"),
    ("Start working with Coworker", "开始使用协作者"),
    ("Two good ways to start:", "两种不错的开始方式："),
    ("Unsubscribe", "退订"),
    ("Waiting…", "等待中…"),
    ("Welcome to OpenWorker", "欢迎使用 OpenWorker"),
    ("You're set up", "设置完成"),
    ("You're signed in", "你已登录"),
    ("invite @ocw to this channel in Slack so it can listen", "请在 Slack 中把 @ocw 邀请进该频道，它才能监听"),
    ("searching your workspace's channels…", "正在搜索你工作区的频道…"),
    ("show hand-off", "查看交接说明"),
    ("show message", "查看消息"),
    ("skip anyway", "仍然跳过"),
    ("via {label}", "经由 {label}"),
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
