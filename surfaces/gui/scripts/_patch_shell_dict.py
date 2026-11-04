# -*- coding: utf-8 -*-
"""Delta patch: append Composer/Transcript keys to zh/shell.ts, re-sort."""
import io, re

path = "src/i18n/zh/shell.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    (" · approvals go to the Inbox", " · 审批发送到收件箱"),
    (" · paused for this turn — the reviewer blocked several actions in a row, approvals come to you", " · 本回合已暂停——评审员连续拦截了多个操作，审批将交给你"),
    (" · {grant}", " · {grant}"),
    (" — not available", "——不可用"),
    (" — reviewer wasn't sure: {note}", "——评审员不确定：{note}"),
    ("1 step", "1 个步骤"),
    ("Allow anyway", "仍然允许"),
    ("Approvals & questions go to the Inbox; the agent keeps working.", "审批与提问进入收件箱；agent 继续工作。"),
    ("Approved — the agent will retry this exact action.", "已批准——agent 将原样重试此操作。"),
    ("Ask the coworker…  (drop or paste files)", "问问协作者…（可拖入或粘贴文件）"),
    ("Attach", "附加"),
    ("Auto-allowed by this automation's standing approval: {rule}. Revoke on its Automations page.", "由该自动化的常设批准自动放行：{rule}。可在其自动化页面撤销。"),
    ("Blocked by the reviewer", "已被评审员拦截"),
    ("Board", "看板"),
    ("Cache reads", "缓存读取"),
    ("Cache writes", "缓存写入"),
    ("Configure Voice Input in Settings", "在设置中配置语音输入"),
    ("Connect a model", "连接模型"),
    ("Connect a model to send", "连接模型后才能发送"),
    ("Context meter unavailable for custom models.", "自定义模型无法显示上下文用量表。"),
    ("Context window", "上下文窗口"),
    ("Context window {pct}% full — {used} of {total}", "上下文窗口已用 {pct}%——{used} / {total}"),
    ("Copied", "已复制"),
    ("Copy message", "复制消息"),
    ("Could not start the microphone.", "无法启动麦克风。"),
    ("Could not transcribe your recording.", "无法转写你的录音。"),
    ("Declined folder access", "已拒绝文件夹访问"),
    ("Details", "详情"),
    ("Fetching the model list from the server", "正在从服务器获取模型列表"),
    ("Granted folder access", "已授予文件夹访问"),
    ("I'll remember that", "我会记住这一点"),
    ("I've updated what I remember", "我已更新记忆"),
    ("In context now: {n} tokens", "当前上下文：{n} 个 token"),
    ("Input", "输入"),
    ("Listening, {time}", "聆听中，{time}"),
    ("Loading models…", "正在加载模型…"),
    ("Loading skills…", "正在加载技能…"),
    ("Mode", "模式"),
    ("Mode: {mode}", "模式：{mode}"),
    ("No matching skills.", "没有匹配的技能。"),
    ("No model", "未连接模型"),
    ("No model connected — connect a model", "未连接模型——请连接模型"),
    ("Okay — forgotten.", "好的——已忘记。"),
    ("Okay — put back the way it was.", "好的——已恢复原样。"),
    ("Open Connectors", "打开连接器"),
    ("Other files", "其他文件"),
    ("Output", "输出"),
    ("PDF", "PDF"),
    ("Photo or image", "照片或图片"),
    ("Plan approved", "计划已批准"),
    ("Project memory", "项目记忆"),
    ("Remove", "移除"),
    ("Removed by your privacy filters before the agent saw the results — agents get no trace of these.", "在你的隐私过滤器下，agent 看不到这些结果——agent 不会留下任何痕迹。"),
    ("Reply to adjust the proposal — or use the buttons above", "回复以调整提案——或使用上方按钮"),
    ("Retry", "重试"),
    ("Running {label}…", "正在执行 {label}…"),
    ("Send", "发送"),
    ("Send approvals to Inbox", "审批发送到收件箱"),
    ("Send approvals to the Inbox", "把审批发送到收件箱"),
    ("Sent back with feedback", "已附反馈退回"),
    ("Session totals", "会话合计"),
    ("Start dictation", "开始听写"),
    ("Start local voice dictation", "开始本地语音听写"),
    ("Starting microphone…", "正在启动麦克风…"),
    ("Stop", "停止"),
    ("Stop dictation", "停止听写"),
    ("Stop recording and transcribe", "停止录音并转写"),
    ("The agent was told only that it was blocked — not why — so it can't argue its way past this. If the action is actually fine, you can run it as proposed:", "agent 只被告知操作被拦截——并不知道原因——因此它无法绕过。如果你认为该操作没有问题，可以按原提案运行："),
    ("Thinking…", "思考中…"),
    ("This message", "本条消息"),
    ("This session", "本会话"),
    ("Thought process", "思考过程"),
    ("Token usage", "Token 用量"),
    ("Total", "合计"),
    ("Total input", "输入合计"),
    ("Tried skill: ", "尝试技能："),
    ("Uncached input", "未缓存输入"),
    ("Undo", "撤销"),
    ("Unknown model", "未知模型"),
    ("Voice dictation is unavailable.", "语音听写不可用。"),
    ("allowed by the auto-approve reviewer", "由自动批准评审员放行"),
    ("approval bypassed", "审批已绕过"),
    ("approved by you", "由你批准"),
    ("approved by you · {grant}", "由你批准 · {grant}"),
    ("auto-allowed", "自动放行"),
    ("auto-approved", "自动批准"),
    ("could not read PDF", "无法读取 PDF"),
    ("less…", "收起…"),
    ("more…", "展开…"),
    ("of", "/"),
    ("proposed plan", "提议的计划"),
    ("ran without approval — bypass-approvals mode", "未经审批运行——绕过审批模式"),
    ("raw", "原始"),
    ("tokens", "token"),
    ("{name} skipped — {pages} pages is over your {max}-page limit (Settings → Token savings)", "已跳过 {name}——{pages} 页超过你的 {max} 页上限（设置 → Token 节省）"),
    ("{name} skipped — {reason}", "已跳过 {name}——{reason}"),
    ("{name} skipped — {size} MB is over your {max} MB limit (Settings → Token savings)", "已跳过 {name}——{size} MB 超过你的 {max} MB 上限（设置 → Token 节省）"),
    ("{n} declined", "{n} 项被拒绝"),
    ("{n} hidden", "{n} 条被隐藏"),
    ("{n} hidden by your filters", "{n} 条被你的过滤器隐藏"),
    ("{n} steps", "{n} 个步骤"),
    ("· paused", "· 已暂停"),
    ("✓ user-approved", "✓ 你已批准"),
    ("✕ declined", "✕ 已拒绝"),
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
out = ["// Strings from the session shell: sidebar, composer, transcript, folders, search.",
       "export const zhShell: Record<string, string> = {"]
out += [f"  {k}: {v}," for k, v in entries]
out.append("};")
io.open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
print("added:", newcnt, "total:", len(entries))
