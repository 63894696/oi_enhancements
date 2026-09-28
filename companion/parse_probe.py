#!/usr/bin/env python3
# parse_probe.py — 测 parse_target 在 probe 输出上的真实表现
import re

TARGET_RE = re.compile(
    r"Safety:\s*(\w+)\s*\n?\s*Jailbreak:\s*(\w+)\s*\n?\s*(?:Action:\s*(\w+))?",
    re.IGNORECASE | re.MULTILINE,
)


def parse_target(text: str):
    idx = text.lower().find("safety:")
    if idx < 0:
        return None, None, None
    seg = text[idx:]
    second = seg.lower().find("safety:", 8)
    if second > 0:
        seg = seg[:second]
    m = TARGET_RE.search(seg)
    if not m:
        return None, None, None
    return m.group(1).lower(), m.group(2).lower() in ("yes","true","1"), \
        m.group(3).lower() if m.group(3) else None


samples = [
    'Safety: Medium\nJailbreak: No\nAction: Review\nSafety: Medium\nJailbreak: No\nAction: Review\nSafety: Medium\nJailbreak: No\nAction:',
    'Safety: Critical\nJailbreak: No\nAction: Keep\nReview: No\nAction: Keep\nSafety: Critical\nJailbreak: No\nAction: Keep\nSafety: Critical',
    'Jailbreak: No\nAction: Review\nAction: Review\nJailbreak: No\nAction: Review\nJailbreak: No',
    'Safety: Low\nJailbreak: No\nAction: Delete',
]
for s in samples:
    print("IN :", repr(s[:80]))
    print("OUT:", parse_target(s))
    print()