#!/usr/bin/env python3
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained(
    "/workspace/qwen3guard-disk-cleanup/adapter", trust_remote_code=True)
for s in ["Safety: Critical", "Safety: Low",
          " Critical", " Critical", "Low", "Critical",
          " Safety: Critical", "Jailbreak: No", "Action: Keep"]:
    ids = tok(s, add_special_tokens=False)["input_ids"]
    toks = tok(s, add_special_tokens=False).tokens()
    print(repr(s), "->", ids)
    print("   tokens:", toks)
    print()