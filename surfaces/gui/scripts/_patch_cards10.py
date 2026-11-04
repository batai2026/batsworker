# -*- coding: utf-8 -*-
"""Delta patch: append AutomationQuickstart keys to zh/cards.ts, re-sort."""
import io, re

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    (" and ", " 和 "),
    ("Allow this automation to post its digest to", "允许此自动化将摘要发布到"),
    ("Approve it there, then come back — this page updates by itself.", "在浏览器中批准后回来即可——本页面会自动更新。"),
    ("Connect {names} to continue", "先连接 {names} 再继续"),
    ("Connections are brokered by OpenWorker Cloud — your tokens stay on this computer.", "连接由 OpenWorker Cloud 代办——你的 token 只保存在这台电脑上。"),
    ("Connections, delivery & schedule", "连接、投递与计划"),
    ("Day", "日期"),
    ("Deliver to", "投递到"),
    ("Delivery & schedule", "投递与计划"),
    ("Finish connecting {name} in your browser.", "在浏览器中完成 {name} 的连接。"),
    ("Finish signing in in your browser — this page updates by itself.", "在浏览器中完成登录——本页面会自动更新。"),
    ("In the app", "应用内"),
    ("No connections needed", "无需连接"),
    ("One sign-in unlocks every one-click connection", "登录一次，解锁所有一键连接"),
    ("Pick a channel to post to first", "请先选择要发布的频道"),
    ("Post to channel", "发布到频道"),
    ("Repository", "仓库"),
    ("Set up", "设置"),
    ("Slack DM (connect Slack later)", "Slack 私信（稍后连接 Slack）"),
    ("Start from a template", "从模板开始"),
    ("The bot must be a member of the channel — invite @OpenWorker in Slack if it isn't.", "bot 必须是频道成员——如果还不是，请在 Slack 中邀请 @OpenWorker。"),
    ("This automation only", "此自动化只会按计划"),
    ("Time", "时间"),
    ("Waiting for sign-in…", "等待登录…"),
    ("Waiting for {name}…", "等待 {name}…"),
    ("connected", "已连接"),
    ("not connected yet", "尚未连接"),
    ("on schedule — reading never needs approval.", "读取——读取从不需要批准。"),
    ("reads", "读取"),
    ("the channel", "该频道"),
    ("without asking each time. Anything else still asks first.", "而无需每次询问。其他任何操作仍会先询问。"),
    ("{name} — {state}", "{name}——{state}"),
    # DAYS labels (variable refs via t(v.label))
    ("Mondays", "每周一"),
    ("Tuesdays", "每周二"),
    ("Wednesdays", "每周三"),
    ("Thursdays", "每周四"),
    ("Fridays", "每周五"),
    ("Saturdays", "每周六"),
    ("Sundays", "每周日"),
    # Template titles / blurbs / cadences / conn-why (variable refs)
    ("GitHub digest", "GitHub 摘要"),
    ("Merged PRs and commits, posted to your team's Slack.", "已合并的 PR 与提交，发布到你团队的 Slack。"),
    ("Pipeline digest", "销售管道摘要"),
    ("Deals that moved — and deals going quiet — posted to Slack.", "有进展的交易与趋于停滞的交易，发布到 Slack。"),
    ("Morning brief", "晨报"),
    ("Calendar and unread email, summarized before your day starts.", "在你开始一天之前，汇总日历与未读邮件。"),
    ("Morning news briefing", "晨间新闻简报"),
    ("A 5-bullet tech & world news digest, saved as markdown.", "5 条要点的科技与世界新闻摘要，保存为 markdown。"),
    ("Inbox digest", "收件箱摘要"),
    ("One short digest of your unread email.", "将你的未读邮件汇总成一条简报。"),
    ("Folder cleanup", "文件夹整理"),
    ("Sort recent Downloads into tidy folders by type.", "把最近的下载按类型整理进不同文件夹。"),
    ("Weekly", "每周"),
    ("Daily", "每天"),
    ("Where the digest posts", "摘要发布的位置"),
    ("What the digest summarizes", "摘要汇总的内容"),
    ("Pipeline and deal activity", "管道与交易动态"),
    ("Today's meetings and gaps", "今天的会议与空闲时段"),
    ("What arrived overnight", "夜里到达的邮件"),
    ("Your unread email", "你的未读邮件"),
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
