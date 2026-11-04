# -*- coding: utf-8 -*-
"""Delta patch for src/i18n/zh/settings.ts: add missed keys, drop unused."""
import io, re

path = "src/i18n/zh/settings.ts"
text = io.open(path, encoding="utf-8").read()

add = [
    (" · Folder unavailable", " · 文件夹不可用"),
    ("Always-on", "常驻"),
    ("Record a short phrase to enable the composer microphone.", "录制一小段话以启用输入框麦克风。"),
    ("Repair", "修复"),
    ("Save", "保存"),
    ("Skills", "技能"),
    ("Your microphone and local transcription engine are working.", "你的麦克风与本地转写引擎工作正常。"),
]
drop = ['"Mac": "Mac",', '"Windows": "Windows",']

lines = text.splitlines(keepends=True)
# drop
lines = [l for l in lines if not any(d in l for d in drop)]
text = "".join(lines)
# add before closing brace, sorted insert: simplest — append then re-sort by key
entries = re.findall(r'^  ("(?:[^"\\]|\\.)*"|[A-Za-z_$][\w$]*): (.+),$', text, re.M)
existing = {k for k, _ in entries}
for k, v in add:
    if k not in existing:
        kk = k if k.isidentifier() and not k[0].isdigit() else '"' + k.replace("\\", "\\\\").replace('"', '\\"') + '"'
        vv = '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        entries.append((kk, vv))
entries.sort(key=lambda kv: kv[0].strip('"').lower())
out = ["// Strings from settings, onboarding, providers, personas, skills, memory, integrations.",
       "export const zhSettings: Record<string, string> = {"]
out += [f"  {k}: {v}," for k, v in entries]
out.append("};")
io.open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
print("total:", len(entries))
