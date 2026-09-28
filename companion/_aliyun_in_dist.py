#!/usr/bin/env python3
"""_aliyun_in_dist.py — 用训练集原文本 + bench 加速参数 + 加大 max_new_tokens"""
import sys, json, time
sys.path.insert(0, "/workspace/companion")
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base = AutoModelForCausalLM.from_pretrained(
    "/workspace/models/Qwen3Guard-Gen-0.6B", trust_remote_code=True,
    torch_dtype=torch.float16, device_map="auto")
tok = AutoTokenizer.from_pretrained(
    "/workspace/qwen3guard-disk-cleanup-conf/adapter", trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
model = PeftModel.from_pretrained(base, "/workspace/qwen3guard-disk-cleanup-conf/adapter")
model.eval()
newline_id = tok.encode("\n", add_special_tokens=False)[-1]

rows = []
with open("/workspace/data_disk_cleanup_conf.jsonl") as f:
    for line in f:
        rows.append(json.loads(line))

# 用训练集前 5 条,in-distribution
import random
random.seed(7)
random.shuffle(rows)
for r in rows[:5]:
    text = r["text"]
    inp = tok(f"user\n{text}\nassistant\n", return_tensors="pt").to("cuda")
    for max_new in [30, 60]:
        t0 = time.time()
        with torch.inference_mode():
            out = model.generate(
                **inp, max_new_tokens=max_new, do_sample=False,
                pad_token_id=tok.pad_token_id,
                eos_token_id=[tok.eos_token_id, newline_id],
                repetition_penalty=1.5,
                no_repeat_ngram_size=6)
        dt = (time.time() - t0) * 1000
        gen = out[0][inp["input_ids"].shape[1]:]
        raw = tok.decode(gen, skip_special_tokens=True).strip()
        print(f"GOLD: {r['risk_label']}/{r['_action']} conf={r.get('_conf')}")
        print(f"  max_new={max_new} ({dt:.0f}ms, {len(gen)}tok): {raw!r}")
        print()
    print("---")