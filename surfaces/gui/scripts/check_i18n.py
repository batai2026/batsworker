#!/usr/bin/env python3
"""Consistency check for the GUI's zero-dependency i18n.

Extracts every t("...") key from the React sources and compares it against the
merged zh-CN dictionaries. No Node needed — run with plain Python:

    python surfaces/gui/scripts/check_i18n.py

Reports:
  - keys used in source but MISSING from the dictionaries (fall back to
    English at runtime — usually a typo or a not-yet-translated string)
  - dictionary keys never referenced in source (stale or wrapped differently)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

GUI = Path(__file__).resolve().parent.parent
SRC = GUI / "src"
ZH = SRC / "i18n" / "zh"

T_CALL = re.compile(r'''\bt\(\s*("([^"\\]|\\.)*"|'([^'\\]|\\.)*')''')
DICT_ENTRY = re.compile(r'''\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')\s*:''')
BARE_KEY = re.compile(r'''^\s*([A-Za-z_$][\w$]*)\s*:''', re.MULTILINE)
SKIP_TEST = re.compile(r"\.test\.|(^|[/\\])e2e")

# Keys referenced via a variable (e.g. t(s.text) over the SUGGESTIONS array) —
# the scanner can't see the linkage, so don't report them as stale.
KNOWN_VARIABLE_REFS = {
    "Find and fix the failing build.",
    "Read the project and give me a 5-bullet overview.",
    "Run the test suite and summarize any failures.",
    # SessionIntro template prompts (t(FOLDER_PROMPT) etc.)
    "Analyze the files in this folder and summarize what matters.",
    "Create a report on my recent HubSpot leads: sources, stages, and who needs follow-up.",
    "Set up a weekly progress report: summarize activity in my GitHub repos and post it to Slack every Friday morning.",
    # Sidebar group-by label map (t(label))
    "Chronological",
    # AutomationQuickstart template titles/blurbs/cadences + DAYS labels + conn-why
    # (rendered via t(tmpl.title), t(v.label), t(why))
    "GitHub digest", "Merged PRs and commits, posted to your team's Slack.",
    "Pipeline digest", "Deals that moved — and deals going quiet — posted to Slack.",
    "Morning brief", "Calendar and unread email, summarized before your day starts.",
    "Morning news briefing", "A 5-bullet tech & world news digest, saved as markdown.",
    "Inbox digest", "One short digest of your unread email.",
    "Folder cleanup", "Sort recent Downloads into tidy folders by type.",
    "Weekly", "Daily", "Weekdays", "Every day", "Weekends",
    "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays",
    "Where the digest posts", "What the digest summarizes", "Pipeline and deal activity",
    "Today's meetings and gaps", "What arrived overnight", "Your unread email",
}

# t(t.label)-style passes a variable — collect known variable label sources so
# we can require those strings to exist in the dict too.
LABEL_VARS = {
    "SettingsView.tsx": re.compile(r"label:\s*\"([^\"]+)\""),
}


def source_files():
    for pattern in ("**/*.ts", "**/*.tsx"):
        for p in SRC.glob(pattern):
            if "i18n" in p.parts or SKIP_TEST.search(str(p)):
                continue
            yield p


def unescape(s: str) -> str:
    # Interpret \uXXXX / \n / \" escapes without the latin-1 trap of
    # codecs' unicode_escape (which mangles multi-byte UTF-8).
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    return (
        s.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\'", "'")
        .replace("\\\\", "\\")
    )


def main() -> int:
    used: dict[str, str] = {}
    for p in source_files():
        text = p.read_text(encoding="utf-8")
        rel = str(p.relative_to(SRC))
        for m in T_CALL.finditer(text):
            key = unescape(m.group(1)[1:-1])
            used.setdefault(key, rel)
        pat = LABEL_VARS.get(p.name)
        if pat:
            for key in pat.findall(text):
                used.setdefault(key, rel)

    dict_keys: set[str] = set()
    for p in sorted(ZH.glob("*.ts")):
        if p.name == "index.ts":
            continue
        text = p.read_text(encoding="utf-8")
        for m in DICT_ENTRY.finditer(text):
            dict_keys.add(unescape(m.group(1) or m.group(2) or ""))
        for m in BARE_KEY.finditer(text):
            dict_keys.add(m.group(1))

    missing = sorted(k for k in used if k not in dict_keys)
    stale = sorted(
        k for k in dict_keys if k not in used and k not in KNOWN_VARIABLE_REFS
    )

    print(f"t() keys used: {len(used)}  |  dict entries: {len(dict_keys)}")
    if missing:
        print(f"\nMISSING from zh dictionaries ({len(missing)}):")
        for k in missing:
            print(f"  [{used[k]}] {k!r}")
    if stale:
        print(f"\nIn dictionaries but never referenced ({len(stale)}):")
        for k in stale:
            print(f"  {k!r}")
    if not missing and not stale:
        print("OK: all t() keys are translated; no stale entries.")
        return 0
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
