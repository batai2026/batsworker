# -*- coding: utf-8 -*-
"""One-shot generator for src/i18n/zh/shell.ts (session-shell strings)."""
import io

entries = [
    ("A temporary folder is created only when you send, with git ready — you can save it as a project later. Your message sends as soon as you choose.", "临时文件夹只在你发送消息时创建，git 已就绪——之后可以保存为项目。选定后消息立即发出。"),
    ("Account: not signed in", "账户：未登录"),
    ("Account: {email}", "账户：{email}"),
    ("Activity", "活动"),
    ("Add", "添加"),
    ("Allow writes", "允许写入"),
    ("Analyze the files in a directory", "分析目录中的文件"),
    ("Analyze the files in this folder and summarize what matters.", "分析此文件夹中的文件，总结重点内容。"),
    ("Archive", "归档"),
    ("Archived ({n})", "已归档（{n}）"),
    ("Automate a weekly GitHub progress report to Slack", "自动生成每周 GitHub 进度报告并发到 Slack"),
    ("Automations", "自动化"),
    ("Browse…", "浏览…"),
    ("Choose a folder…", "选择文件夹…"),
    ("Choose a project folder", "选择项目文件夹"),
    ("Choose another folder…", "选择其他文件夹…"),
    ("Choose folder", "选择文件夹"),
    ("Choose location", "选择位置"),
    ("Choose or paste a folder path…", "选择或粘贴文件夹路径…"),
    ("Chronological", "按时间排序"),
    ("Clear", "清除"),
    ("Click again to permanently delete", "再次点击以永久删除"),
    ("Code work happens inside a folder — pick your project, or start somewhere temporary.", "代码工作在文件夹内进行——选择你的项目，或从临时目录开始。"),
    ("Collapse sidebar", "收起侧边栏"),
    ("Collapse sidebar (⌘B)", "收起侧边栏 (⌘B)"),
    ("Configure ›", "去配置 ›"),
    ("Connectors", "连接器"),
    ("Could not save workspace trust.", "无法保存工作区信任设置。"),
    ("Create", "创建"),
    ("Create a report from my HubSpot leads", "用我的 HubSpot 线索生成报告"),
    ("Create a report on my recent HubSpot leads: sources, stages, and who needs follow-up.", "为我最近的 HubSpot 线索生成报告：来源、阶段，以及哪些人需要跟进。"),
    ("Delete?", "删除？"),
    ("Dock sidebar", "展开侧边栏"),
    ("Dock sidebar (⌘B)", "展开侧边栏 (⌘B)"),
    ("Downloading…", "正在下载…"),
    ("Filter by coworker", "按协作者筛选"),
    ("From Slack", "来自 Slack"),
    ("Give access to a folder", "授予文件夹访问权限"),
    ("Group & filter conversations", "分组与筛选会话"),
    ("Group and filter conversations", "分组与筛选会话"),
    ("Group by", "分组方式"),
    ("I'll read them and summarize what matters", "我会读取并总结重点"),
    ("Import coworker…", "导入协作者…"),
    ("Inbox", "收件箱"),
    ("Inbox — {n} items need you", "收件箱——{n} 项待处理"),
    ("Keep asking", "保持询问"),
    ("Later", "稍后"),
    ("Manage coworkers…", "管理协作者…"),
    ("New project", "新建项目"),
    ("No chats found.", "未找到会话。"),
    ("No conversations in this project yet.", "此项目暂无会话。"),
    ("No conversations yet.", "暂无会话。"),
    ("No matching conversations.", "没有匹配的会话。"),
    ("No projects yet — start one with the + above.", "暂无项目——点上方 + 新建一个。"),
    ("None checked shows all.", "不勾选则显示全部。"),
    ("Not signed in", "未登录"),
    ("Not signed in — one-click connections need OpenWorker Cloud", "未登录——一键连接需要 OpenWorker Cloud"),
    ("Off = read-only. Tick to let the agent write here.", "默认只读。勾选后允许 agent 在此写入。"),
    ("Open", "打开"),
    ("OpenWorker v{version} is ready to install.", "OpenWorker v{version} 已准备好安装。"),
    ("Pick a folder or enter a path. If the path doesn't exist, it will be created.", "选择文件夹或输入路径。路径不存在时将自动创建。"),
    ("Pick a folder →", "选择文件夹 →"),
    ("Pick a task to start — I'll do the work and save the result. Or just type what you need below.", "选一个任务开始——我来完成工作并保存结果。也可以直接在下方输入你的需求。"),
    ("Pin", "置顶"),
    ("Pinned", "已置顶"),
    ("Pinned chats", "置顶会话"),
    ("Projects", "项目"),
    ("Recent", "最近"),
    ("Recent chats", "最近会话"),
    ("Rename", "重命名"),
    ("Repo activity, summarized and posted every Friday", "仓库动态摘要，每周五自动发送"),
    ("Restart to update", "重启以更新"),
    ("Saving…", "正在保存…"),
    ("Scheduled", "计划任务"),
    ("Search chats", "搜索会话"),
    ("Session actions", "会话操作"),
    ("Set up a weekly progress report: summarize activity in my GitHub repos and post it to Slack every Friday morning.", "设置每周进度报告：汇总我 GitHub 仓库的动态，每周五早上发到 Slack。"),
    ("Show less", "收起"),
    ("Show more ({n})", "显示更多（{n}）"),
    ("Show {n} more", "再显示 {n} 条"),
    ("Sign in to OpenWorker Cloud", "登录 OpenWorker Cloud"),
    ("Sign out", "退出登录"),
    ("Signed in to OpenWorker Cloud", "已登录 OpenWorker Cloud"),
    ("Sleeping (will wake itself)", "休眠中（到点自动唤醒）"),
    ("Sources, stages, and who needs follow-up", "来源、阶段，以及需要跟进的人"),
    ("Start →", "开始 →"),
    ("Tasks", "任务"),
    ("The update couldn't be installed — it will be offered again next launch.", "更新安装失败——下次启动时会再次提供。"),
    ("This coworker needs a workspace to read, edit, and run in.", "该协作者需要一个用于读取、编辑和运行的工作区。"),
    ("This project asks OpenWorker to run the commands below without individual approval. Trust applies to future configuration changes at this exact folder until you revoke it in Settings.", "此项目请求 OpenWorker 免审批运行以下命令。信任对该文件夹今后的配置变更持续有效，直到你在设置中撤销。"),
    ("Trust this workspace's commands?", "信任此工作区的命令？"),
    ("Trust workspace", "信任工作区"),
    ("Unarchive", "取消归档"),
    ("Unpin", "取消置顶"),
    ("Update available", "有可用更新"),
    ("Use temporary folder", "使用临时文件夹"),
    ("What should we produce?", "我们来做点什么？"),
    ("Where should {name} work?", "{name} 应该在哪里工作？"),
    ("Working now", "正在工作"),
    ("could not open that folder", "无法打开该文件夹"),
    ("{n} awaiting your attention", "{n} 项待你处理"),
    ("{n} new runs", "{n} 次新运行"),
    ("{n} new runs — the latest failed", "{n} 次新运行——最近一次失败"),
]


def ts_key(k: str) -> str:
    if k.isidentifier() and not k[0].isdigit():
        return k
    return '"' + k.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ts_val(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


lines = [
    "// Strings from the session shell: sidebar, composer, transcript, folders, search.",
    "export const zhShell: Record<string, string> = {",
]
seen = set()
for k, v in sorted(entries):
    if k in seen:
        continue
    seen.add(k)
    lines.append(f"  {ts_key(k)}: {ts_val(v)},")
lines.append("};")
io.open("src/i18n/zh/shell.ts", "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("entries:", len(seen))
