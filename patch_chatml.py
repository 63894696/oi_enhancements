#!/usr/bin/env python3
"""patch_chatml.py — CRLF-aware patch."""
import sys

PATH = "companion/train_step1.py"
with open(PATH, encoding="utf-8") as f:
    src = f.read()

# File has CRLF
RN = "\r\n"
NL = RN  # file's line ending

old_safety = (
    r'PROMPT_TEMPLATE_SAFETY = (' + RN +
    r'    "user\n"' + RN +
    r'    "{text}\n"' + RN +
    r'    "assistant\n"' + RN +
    r')'
)
new_safety = (
    r'# **M3.49 L6 fix(2026-09-23)**:Qwen3Guard-Gen-0.6B uses ChatML.' + RN +
    r'PROMPT_TEMPLATE_SAFETY = (' + RN +
    r'    "<|im_start|>user\n"' + RN +
    r'    "{text}<|im_end|>\n"' + RN +
    r'    "<|im_start|>assistant\n"' + RN +
    r')'
)
print(f"SAFETY count: {src.count(old_safety)}")
if src.count(old_safety) != 1:
    sys.exit(1)
src = src.replace(old_safety, new_safety, 1)

old_tempfile = (
    r'PROMPT_TEMPLATE_TEMPFILE = (' + RN +
    r'    "user\n{text}\nassistant\n"' + RN +
    r')'
)
new_tempfile = (
    r'# **M3.49 L6 fix(2026-09-23)**:same ChatML.' + RN +
    r'PROMPT_TEMPLATE_TEMPFILE = (' + RN +
    r'    "<|im_start|>user\n{text}<|im_end|>\n"' + RN +
    r'    "<|im_start|>assistant\n"' + RN +
    r')'
)
print(f"TEMPFILE count: {src.count(old_tempfile)}")
if src.count(old_tempfile) != 1:
    sys.exit(1)
src = src.replace(old_tempfile, new_tempfile, 1)

# build_target return: the previously-applied partial patch had ""
# Let's search both old variants
for old_ret in [
    r'        base += f"\nAction: {act_str}"' + RN + r'    return base + ""',
    r'        base += f"\nAction: {act_str}"' + RN + r'    return base',
]:
    print(f"RETURN ({old_ret[:50]}...) count: {src.count(old_ret)}")
    if src.count(old_ret) == 1:
        new_ret = (
            r'        base += f"\nAction: {act_str}"' + RN +
            r'    # M3.49 L6: ChatML end token' + RN +
            r'    return base + "<|im_end|>"'
        )
        src = src.replace(old_ret, new_ret, 1)
        break
else:
    sys.exit(1)

with open(PATH, "w", encoding="utf-8", newline="") as f:
    f.write(src)
print("PATCHED OK")

with open(PATH, "rb") as f:
    raw = f.read()
i = raw.find(b"PROMPT_TEMPLATE_SAFETY")
print("--- SAFETY ---")
print(raw[i:i+250].decode("utf-8"))
i = raw.find(b"PROMPT_TEMPLATE_TEMPFILE")
print("--- TEMPFILE ---")
print(raw[i:i+250].decode("utf-8"))
i = raw.find(b"return base +")
print("--- RETURN ---")
print(raw[i-50:i+150].decode("utf-8"))