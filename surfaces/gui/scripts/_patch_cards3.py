# -*- coding: utf-8 -*-
"""Delta patch: append Audit/Memory keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    ("Delete everything that's been remembered about you?\n\nThis can't be undone. Conversations you already have open still know what they knew — new conversations start with a clean slate.", "删除所有关于你的记忆？\n\n此操作无法撤销。已打开的会话仍保留它们开始时的知识——新会话将白纸起步。"),
    ("Delete this memory", "删除这条记忆"),
    ("Details you share from future conversations will be remembered, so I can be more helpful over time.", "你在后续对话中分享的信息会被记住，这样我可以随时间变得更贴心。"),
    ("Everything I remembered has been deleted. New conversations start fresh; ones you already have open still know what they knew when they started.", "我记住的所有内容已删除。新会话从头开始；已打开的会话仍保留它们开始时的知识。"),
    ("Filter", "筛选"),
    ("Fix this", "修正"),
    ("Forget everything…", "全部忘记…"),
    ("I use a screen reader — no tables, describe any image", "我使用屏幕阅读器——不要用表格，描述任何图片"),
    ("I'll stop remembering new things about you. What I already know is kept and still used — delete anything below you'd rather I forget.", "我将不再记住你的新信息。已有的记忆会保留并继续使用——下面若有你不想让我记住的内容，请自行删除。"),
    ("Lasting preferences you mention in chat get saved and used in future conversations — you'll see a small note each time, with one-tap Undo. Turning this off stops new saves; anything already below is still used until you delete it.", "你在聊天中提到的长期偏好会被保存并在未来对话中使用——每次保存都会出现小提示，可一键撤销。关闭后将不再保存新内容；下方已有的内容在被删除前仍会使用。"),
    ("No audit events yet.", "暂无审计事件。"),
    ('Nothing yet. When you mention a lasting preference in chat — or say "remember that…" — it will show up here.', "暂无内容。当你在聊天中提到长期偏好——或说“记住…”——就会显示在这里。"),
    ("Recent connector and browser tool activity. Arguments are sanitized before storage.", "最近的连接器与浏览器工具活动。参数在存储前会脱敏。"),
    ("Remember new things about me", "记住关于我的新信息"),
    ("Remember new things about you", "记住关于你的新信息"),
    ("Saved automatically from your conversations. Fix anything that's wrong — or delete it. Edits and deletions apply to new conversations; ones you already have open keep what they knew when they started.", "自动从你的对话中保存。有误可直接修改或删除。编辑和删除只影响新会话；已打开的会话保留它们开始时的知识。"),
    ("Saved — applies to new conversations. Ones you already have open keep the instructions they started with.", "已保存——对新会话生效。已打开的会话沿用它们开始时的指令。"),
    ("Use DD-MM-YYYY for dates", "日期使用 DD-MM-YYYY 格式"),
    ("What I've learned about you", "我了解到的关于你的信息"),
    ("Your coworkers can remember useful things about you between conversations. Everything they know is listed here.", "你的协作者可以在对话之间记住关于你的有用信息。它们知道的一切都列在这里。"),
    ("Your coworkers follow these in every conversation.", "你的协作者在每次对话中都会遵守这些规则。"),
    ("Your instructions", "你的指令"),
    ("cancel", "取消"),
    ("connector", "连接器"),
    ("event", "事件"),
    ("resource:", "资源："),
    ("session", "会话"),
    ("session id", "会话 ID"),
    ("tool", "工具"),
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
