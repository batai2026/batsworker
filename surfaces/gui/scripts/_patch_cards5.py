# -*- coding: utf-8 -*-
"""Delta patch: append ScheduledView keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    (" · last {status}", " · 上次 {status}"),
    ("+ New automation", "+ 新建自动化"),
    ("Active · next {time}", "启用中 · 下次 {time}"),
    ("Allowed without asking", "免询问放行"),
    ("At", "时间"),
    ("Create automation", "创建自动化"),
    ("Creating…", "正在创建…"),
    ("Delete automation", "删除自动化"),
    ("Delete {title}", "删除 {title}"),
    ("Each run is a live conversation — open one to see what the agent did and ask a follow-up.", "每次运行都是一条真实会话——打开即可查看 agent 做了什么并继续追问。"),
    ("Edit", "编辑"),
    ("Every day", "每天"),
    ("Instructions", "指令"),
    ("New automation", "新建自动化"),
    ("No runs yet.", "尚无运行记录。"),
    ("No scheduled tasks yet — use a template above, click + New automation, or just ask OpenWorker in a session.", "暂无计划任务——使用上方模板、点击“+ 新建自动化”，或直接在会话中让 OpenWorker 创建。"),
    ("Open this run's conversation", "打开本次运行的会话"),
    ("Open ›", "打开 ›"),
    ("Paused", "已暂停"),
    ("Recurring tasks OpenWorker runs on a schedule.", "OpenWorker 按计划周期执行的任务。"),
    ("Repeat", "重复"),
    ("Run now", "立即运行"),
    ("Runs", "运行记录"),
    ("Runs only while openworker-server is up — a missed schedule catches up once when it next starts.", "仅在 openworker-server 运行时执行——错过的计划会在下次启动时补跑一次。"),
    ("Standing approvals this automation may use — everything else still asks first.", "此自动化可使用的常设批准——其余操作仍会先询问。"),
    ("This automation will ask for approval again", "此自动化将重新请求批准"),
    ("Title", "标题"),
    ("Title (e.g. Daily standup notes)", "标题（例如：每日站会纪要）"),
    ("Weekdays", "工作日"),
    ("Weekends", "周末"),
    ("What should it do each run? (e.g. Summarize today's calendar and open tasks.)", "每次运行要做什么？（例如：总结今天的日程和待办任务。）"),
    ("new", "新"),
    ("next {time}", "下次 {time}"),
    ("{n} file(s)", "{n} 个文件"),
    ("{n} runs", "{n} 次运行"),
    ("← Automations", "← 自动化"),
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
