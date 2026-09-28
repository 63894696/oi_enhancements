#!/usr/bin/env python3
"""patch_return.py — final fix."""
import sys
PATH = "companion/train_step1.py"
with open(PATH, "rb") as f:
    raw = f.read()

# Find empty return base + ""  (with raw escape pattern in file)
# File has actual bytes: return base + ""  (1 byte for "")
# Use 0x22 (") to avoid escape issues
old_ret = b"return base + " + b'"' + b'"'  # b'return base + ""' but with explicit bytes
new_ret = b"return base + " + b'"' + b"<|im_end|>" + b'"'
print(f"old count: {raw.count(old_ret)}")
print(f"new already in: {raw.count(new_ret)}")
if raw.count(new_ret) >= 1:
    print("Already fully patched")
    sys.exit(0)
if raw.count(old_ret) != 1:
    print("Pattern not found exactly")
    sys.exit(1)
raw = raw.replace(old_ret, new_ret, 1)
with open(PATH, "wb") as f:
    f.write(raw)
print("PATCHED OK")

with open(PATH, "rb") as f:
    raw = f.read()
i = raw.find(b"return base +")
print("--- RETURN ---")
print(raw[i-100:i+150].decode("utf-8"))