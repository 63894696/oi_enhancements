#!/usr/bin/env python3
"""_aliyun_param_sweep.py — 找最佳 inference 参数"""
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
import random
random.seed(7); random.shuffle(rows)
sample = rows[0]
text = sample["text"]
prompt = f"user\n{text}\nassistant\n"

# param sweep
configs = [
    # (name, max_new, eos_includes_nl, rep_penalty, ngram)
    ("orig(rep1.2,ngram4,mxw80)", 80, False, 1.2, 4),
    ("B1(rep1.5,ngram6,mxw30,NL)", 30, True, 1.5, 6),
    ("B1b(rep1.2,ngram4,mxw30,NL)", 30, True, 1.2, 4),
    ("B1c(rep1.5,ngram4,mxw30,NL)", 30, True, 1.5, 4),
    ("B1d(rep1.2,ngram6,mxw40)", 40, False, 1.2, 6),
    ("B1e(rep1.5,ngram4,mxw40)", 40, False, 1.5, 4),
]
for name, mxw, nl_eos, rp, ng in configs:
    inp = tok(prompt, return_tensors="pt").to("cuda")
    t0 = time.time()
    eos = [tok.eos_token_id, newline_id] if nl_eos else tok.eos_token_id
    with torch.inference_mode():
        out = model.generate(
            **inp, max_new_tokens=mxw, do_sample=False,
            pad_token_id=tok.pad_token_id,
            eos_token_id=eos,
            repetition_penalty=rp,
            no_repeat_ngram_size=ng)
    dt = (time.time() - t0) * 1000
    gen = out[0][inp["input_ids"].shape[1]:]
    raw = tok.decode(gen, skip_special_tokens=True).strip()
    has_safety = "Safety:" in raw
    has_jailbreak = "Jailbreak:" in raw
    has_action = "Action:" in raw
    print(f"{name:35s} ({dt:.0f}ms, {len(gen)}tok)")
    print(f"  raw: {raw[:120]!r}")
    print(f"  parsed: Safety={has_safety} Jailbreak={has_jailbreak} Action={has_action}")
    print()