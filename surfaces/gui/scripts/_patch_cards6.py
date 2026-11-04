# -*- coding: utf-8 -*-
"""Delta patch: append RightRail keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    (" Showing first {max} of {n} rows.", " 仅显示前 {max} 行（共 {n} 行）。"),
    (" with {n} tool calls so far.", "，已进行 {n} 次工具调用。"),
    (" · missing", " · 缺失"),
    ("Back to {root}", "返回 {root}"),
    ("Board", "看板"),
    ("Browse", "浏览"),
    ("Close", "关闭"),
    ("Close the viewer", "关闭查看器"),
    ("Copy contents", "复制内容"),
    ("Copy path", "复制路径"),
    ("Could not parse spreadsheet: {error}", "无法解析表格：{error}"),
    ("Could not render PDF: {error}", "无法渲染 PDF：{error}"),
    ("Empty file.", "空文件。"),
    ("Empty sheet.", "空工作表。"),
    ("Expand the board", "展开看板"),
    ("Files", "文件"),
    ("For longer multi-step tasks, progress will appear here while OpenWorker plans, uses tools, waits for approval, and produces artifacts.", "较长的多步任务执行时，这里会显示进度——OpenWorker 规划、调用工具、等待批准并产出文件。"),
    ("Journal", "日志簿"),
    ("Loading...", "加载中…"),
    ("More", "更多"),
    ("More actions", "更多操作"),
    ("No previewable files yet.", "暂无可预览的文件。"),
    ("Open in browser", "在浏览器中打开"),
    ("Open in default app", "用默认应用打开"),
    ("Open {actor}'s session", "打开 {actor} 的会话"),
    ("Parsing spreadsheet…", "正在解析表格…"),
    ("Progress", "进度"),
    ("Refresh artifacts", "刷新产物"),
    ("Reload", "重新加载"),
    ("Reload preview", "重新加载预览"),
    ("Rendering PDF…", "正在渲染 PDF…"),
    ("Reveal in Finder", "在文件管理器中显示"),
    ("Show the folder where these files are saved", "显示这些文件所在的文件夹"),
    ("Team", "团队"),
    ("This folder is empty.", "此文件夹为空。"),
    ("This {kind} file can't be previewed here.", "此 {kind} 文件无法在此预览。"),
    ("Working on this task", "正在处理此任务"),
    ("Working...", "工作中…"),
    ("open ↗", "打开 ↗"),
    ("read-only", "只读"),
    ("read-write", "读写"),
    ("sleeping", "休眠中"),
    ("team chat", "团队聊天"),
    ("{n} active", "{n} 项进行中"),
    ("{n} blocked", "{n} 项受阻"),
    ("{n} entries", "{n} 条记录"),
    ("{n} review", "{n} 项待审"),
    ("{n} tool calls so far.", "已进行 {n} 次工具调用。"),
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
