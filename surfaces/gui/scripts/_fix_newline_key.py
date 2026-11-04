# -*- coding: utf-8 -*-
"""Fix the newline-containing dict key: real newlines -> \\n escapes (valid TS)."""
import io

path = "src/i18n/zh/cards.ts"
text = io.open(path, encoding="utf-8").read()

broken_key = (
    "Delete everything that's been remembered about you?\n\n"
    "This can't be undone. Conversations you already have open still know what they knew — "
    "new conversations start with a clean slate."
)
broken_val = (
    "删除所有关于你的记忆？\n\n此操作无法撤销。已打开的会话仍保留它们开始时的知识——新会话将白纸起步。"
)
fixed_key = broken_key.replace("\n", "\\n")
fixed_val = broken_val.replace("\n", "\\n")

if broken_key in text:
    text = text.replace(
        '"' + broken_key + '": "' + broken_val + '"',
        '"' + fixed_key + '": "' + fixed_val + '"',
    )
    io.open(path, "w", encoding="utf-8").write(text)
    print("fixed")
else:
    print("no real-newline entry found (already fixed?)")
