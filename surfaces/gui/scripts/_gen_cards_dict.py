# -*- coding: utf-8 -*-
"""One-shot generator for src/i18n/zh/cards.ts (approval/question/gate-card strings)."""
import io

entries = [
    (" (currently: {provider})", "（当前：{provider}）"),
    (" ({n})", "（{n}）"),
    (" from {source}", "，来自 {source}"),
    (" · as a PNG screenshot", " · 以 PNG 截图形式"),
    (" · overwrites the existing file", " · 将覆盖现有文件"),
    (" — always allowed once you approve", "——批准后始终允许"),
    (" — or install it yourself and continue.", "——你也可以自行安装后继续。"),
    (" — read-only", "——只读"),
    ("# team chat", "# 团队聊天"),
    (".", "。"),
    ("A group channel for questions and consensus — @mentions wake the mentioned coworker. Status stays on the board either way.", "用于提问和达成共识的群聊频道——@提及会唤醒对应的协作者。无论开关，状态都记录在看板上。"),
    ("Add to my skills", "添加到我的技能"),
    ("Allow", "允许"),
    ("Allow every time", "始终允许"),
    ("Allow once", "允许一次"),
    ("Allow read-only commands", "允许只读命令"),
    ("Allow writing (read-write)", "允许写入（读写）"),
    ("Always allow", "始终允许"),
    ('Always allow against {target} for "{task}" — revoke any time on its Automations page', "始终允许 {target}（用于“{task}”）——随时可在其自动化页面撤销"),
    ("Always allow searches this session", "本会话内始终允许搜索"),
    ("Always allow this command", "始终允许此命令"),
    ("Always allow {action} for this session", "本会话内始终允许{action}"),
    ("Always allow {host} this session", "本会话内始终允许 {host}"),
    ('Always allow {tool} → {target} for "{task}" — revoke any time on its Automations page', "始终允许 {tool} → {target}（用于“{task}”）——随时可在其自动化页面撤销"),
    ("Approve", "批准"),
    ("Approve & run", "批准并执行"),
    ("Approve items", "批准条目"),
    ("Approve — ask per step", "批准——每步询问"),
    ("Approved: {decision}", "已批准：{decision}"),
    ("Approving adds it to your skills on this computer — usable in every conversation from then on.", "批准后会添加到这台电脑上的你的技能中——之后所有会话都可用。"),
    ("Approving grants the lead create, assign & steer — this team only, revocable.", "批准将授予 lead 创建、分派与督导权限——仅限本团队，可撤销。"),
    ("Auto-allow read-only commands (local reads and pipelines only — no network, writes, or interpreters) for the rest of this session", "本会话内自动放行只读命令（仅本地读取与管道——不含网络、写入或解释器）"),
    ("Back", "返回"),
    ("Continue without it", "不安装继续"),
    ("Create team & start", "创建团队并开始"),
    ("Decline", "拒绝"),
    ("Deny", "拒绝"),
    ("Done when:", "完成标准："),
    ("Enable", "启用"),
    ("Every fetch to {host} (and its subdomains) runs without asking for the rest of this session", "本会话内对 {host}（及其子域）的抓取不再询问"),
    ("Every web search runs without asking for the rest of this session — the grant ends if you change the search provider", "本会话内所有网页搜索不再询问——更换搜索提供商后授权失效"),
    ("Grant", "授予"),
    ("Grant (no folder)", "授予（无文件夹）"),
    ("Grant access", "授予访问"),
    ("Granting makes this folder the session's primary working directory (read-write); the scratch directory stays available for temporary files and artifacts.", "授予后该文件夹将成为本会话的主工作目录（读写）；临时文件与产物仍存放在暂存目录中。"),
    ("Install", "安装"),
    ("Make workspace", "设为工作区"),
    ("No folder was suggested", "未建议文件夹"),
    ("Not now", "暂不"),
    ("OpenWorker has no verified build for this machine — install it yourself if you want this check, or continue and the coworker will note the gap.", "OpenWorker 没有适用于本机的已验证构建——如需此检查请自行安装，或继续，协作者会在结果中注明缺失。"),
    ("OpenWorker installs its own verified copy", "OpenWorker 安装自己的已验证副本"),
    ("Or type your own answer…", "或输入你自己的回答…"),
    ("Previous question", "上一个问题"),
    ("Proposed team — {n} workers", "提议的团队——{n} 名 worker"),
    ("Proposed work items — {n}", "提议的工作条目——{n} 项"),
    ("Queries go to your configured search provider", "搜索请求将发送到你配置的搜索提供商"),
    ("Question {n}", "问题 {n}"),
    ("Reason:", "原因："),
    ("Recommended", "推荐"),
    ("Reject", "退回"),
    ("Reply to edit the split; approval creates these on the board.", "回复可修改拆分方式；批准后将在看板上创建这些条目。"),
    ("Request changes", "要求修改"),
    ("Send feedback", "提交反馈"),
    ("Show full criteria", "显示完整标准"),
    ("The agent asks to make a folder this session's workspace", "agent 请求将一个文件夹设为本次会话的工作区"),
    ("The agent is requesting access to a folder", "agent 正在请求访问一个文件夹"),
    ("The agent proposed a plan", "agent 提出了一份计划"),
    ("The coworker needs", "协作者需要"),
    ("Tool: {name}", "工具：{name}"),
    ("What should change about the plan?", "计划需要怎么调整？"),
    ("With the message", "随附消息"),
    ("Your answer…", "你的回答…"),
    ("a connected chat", "已连接的聊天"),
    ("acts on a connected service", "作用于已连接的服务"),
    ("leaves this computer → your search provider", "离开本机 → 你的搜索提供商"),
    ("leaves this computer → {dest}", "离开本机 → {dest}"),
    ("leaves this computer → {host}", "离开本机 → {host}"),
    ("preview", "预览"),
    ("question", "提问"),
    ("reviewer wasn't sure: {note}", "评审员不确定：{note}"),
    ("saves to Settings ▸ Skills", "保存到 设置 ▸ 技能"),
    ("show all {n} lines", "显示全部 {n} 行"),
    ("show less", "收起"),
    ("show the full message", "显示完整消息"),
    ("stays on this computer", "仅在本机"),
    ("the web", "网络"),
    ("this automation", "此自动化"),
    ("{n} more items", "还有 {n} 项"),
]


def ts_key(k: str) -> str:
    if k.isidentifier() and not k[0].isdigit():
        return k
    return '"' + k.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ts_val(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


lines = [
    "// Strings from cards and secondary views: approvals, inbox, scheduled, audit, teams, right rail.",
    "export const zhCards: Record<string, string> = {",
]
seen = set()
for k, v in sorted(entries):
    if k in seen:
        continue
    seen.add(k)
    lines.append(f"  {ts_key(k)}: {ts_val(v)},")
lines.append("};")
io.open("src/i18n/zh/cards.ts", "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("entries:", len(seen))
