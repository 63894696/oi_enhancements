#!/usr/bin/env python3
"""patch_tempfile.py — patch TEMPFILE template using raw bytes (CRLF aware)."""
import sys
PATH = "companion/train_step1.py"
with open(PATH, "rb") as f:
    raw = f.read()

# Build pattern as raw bytes (CRLF line endings)
old = (
    b'PROMPT_TEMPLATE_TEMPFILE = (\r\n'
    b'    "user\\n{text}\\nassistant\\n"\r\n'
    b')'
)
new = (
    b'# **M3.49 L6 fix(2026-09-23)**:same ChatML.\r\n'
    b'PROMPT_TEMPLATE_TEMPFILE = (\r\n'
    b'    "<|im_start|>user\\n{text}<|im_end|>\\n"\r\n'
    b'    "<|im_start|>assistant\\n"\r\n'
    b')'
)
print(f"TEMPFILE count: {raw.count(old)}")
if raw.count(old) != 1:
    sys.exit(1)
raw = raw.replace(old, new, 1)

# Patch the empty return base + "" -> + "<|im_end|>"
old_ret = b'    return base + ""'
new_ret = b'    return base + "<|im_end|>"'
print(f"RETURN count: {raw.count(old_ret)}")
if raw.count(old_ret) != 1:
    sys.exit(1)
raw = raw.replace(old_ret, new_ret, 1)

with open(PATH, "wb") as f:
    f.write(raw)
print("PATCHED OK")

with open(PATH, "rb") as f:
    raw = f.read()
i = raw.find(b"PROMPT_TEMPLATE_TEMPFILE")
print("--- TEMPFILE ---")
print(raw[i:i+250].decode("utf-8"))
i = raw.find(b"return base +")
print("--- RETURN ---")
print(raw[i-100:i+100].decode("utf-8"))