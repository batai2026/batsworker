# -*- coding: utf-8 -*-
"""Delta patch: append AccessSection keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    ("+ Add a source", "+ 添加来源"),
    ("+ Give access to a folder…", "+ 授予文件夹访问权限…"),
    ("Access", "访问权限"),
    ("Add a channel", "添加频道"),
    ("Back to sources", "返回来源列表"),
    ("Channels · {n}", "频道 · {n}"),
    ("Connect {title}", "连接 {title}"),
    ("Connecting makes {title} available to all your coworkers — the toggle in this list controls just this session.", "连接后 {title} 将对所有协作者可用——此列表中的开关仅控制本会话。"),
    ("Couldn't add that channel.", "无法添加该频道。"),
    ("Folders", "文件夹"),
    ("Manage →", "管理 →"),
    ("No connectors enabled for this session.", "本会话未启用任何连接器。"),
    ("No match — see all on the Connectors page below.", "无匹配——请在下方连接器页面查看全部。"),
    ("Not listening to any {label} channel yet.", "尚未监听任何 {label} 频道。"),
    ("On for this session. Off mutes it for this session only — the connector stays connected.", "对本会话开启。关闭仅在本会话静音——连接器本身保持连接。"),
    ("Search connectors…", "搜索连接器…"),
    ("Session access", "会话访问权限"),
    ("Sources", "来源"),
    ("Stop listening", "停止监听"),
    ("Subscribed channels · {n}", "已订阅频道 · {n}"),
    ("The agent receives messages posted to these channels. Removing one stops this session from listening — the connector stays connected.", "agent 会接收发布到这些频道的消息。移除一个频道即停止本会话监听——连接器本身保持连接。"),
    ("This channel is also this session's Inbox-routing target — inbound and outbound collide.", "该频道同时是本会话的收件箱路由目标——收发会互相冲突。"),
    ("core", "核心"),
    ("no sources", "无来源"),
    ("{label} channels", "{label} 频道"),
    ("{n} folders", "{n} 个文件夹"),
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
