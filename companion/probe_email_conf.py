#!/usr/bin/env python3
# probe_email_conf.py — M3.48 email_conf 5 条样本 probe
import json, random, torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base = AutoModelForCausalLM.from_pretrained(
    "/workspace/models/Qwen3Guard-Gen-0.6B",
    trust_remote_code=True, torch_dtype=torch.float16, device_map="auto")
tok = AutoTokenizer.from_pretrained(
    "/workspace/qwen3guard-email-conf/adapter", trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
model = PeftModel.from_pretrained(base, "/workspace/qwen3guard-email-conf/adapter")
model.eval()

rows = []
with open("/workspace/data_email_conf.jsonl") as f:
    for line in f:
        rows.append(json.loads(line))
random.seed(42)
random.shuffle(rows)
test = rows[:5]

for r in test:
    text = r["text"]
    prompt = f"user\n{text}\nassistant\n"
    inp = tok(prompt, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        out = model.generate(
            **inp, max_new_tokens=80, do_sample=False,
            pad_token_id=tok.pad_token_id,
            repetition_penalty=1.2,
            no_repeat_ngram_size=4)
    gen = out[0][inp["input_ids"].shape[1]:]
    raw = tok.decode(gen, skip_special_tokens=True).strip()
    print(f"GOLD: {r['risk_label']:8s} | {r['_action']:8s} | conf={r.get('_conf')}")
    print(f"OUT : {raw!r}")
    print("---")