#!/usr/bin/env python3
# test_3_emails_inline.py — 直接加载 aliyun 上的 adapter(不靠 adapter_registry)
import sys, os, json, time
sys.path.insert(0, "/workspace/agentmail_helper")
from helper import AgentMail

# 手动加载 adapter(aliyun 路径)
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel

BASE = "/workspace/models/Qwen3Guard-Gen-0.6B"
ADAPTER = "/workspace/qwen3guard-email-conf/adapter"

print(f"[1/3] 加载 {BASE} + {ADAPTER} ...")
tok = AutoTokenizer.from_pretrained(ADAPTER, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
base = AutoModelForCausalLM.from_pretrained(
    BASE, trust_remote_code=True,
    torch_dtype=torch.float16, device_map="auto")
model = PeftModel.from_pretrained(base, ADAPTER)
model.eval()
print(f"  ✅ done\n")

# 拉 inbox
print(f"[2/3] 拉 prisiragent@agentmail.to ...")
am = AgentMail()
ms = am.list_messages(inbox="prisiragent@agentmail.to", limit=20)
messages = ms.get("messages", [])
print(f"  {len(messages)} 封\n")

# 分类
print(f"[3/3] 跑分类:")
for m in messages:
    sender = m.get("from", "?")
    subject = m.get("subject", "?")
    body = (m.get("text") or m.get("body") or "")[:200]
    received_hours = 1.0
    if "created_at" in m:
        from datetime import datetime
        try:
            t = datetime.fromisoformat(m["created_at"].replace("Z", "+00:00"))
            received_hours = max(0.1, (time.time() - t.timestamp()) / 3600)
        except Exception:
            pass

    text = (f"发件人: {sender}\n主题: {subject}\n正文摘要: {body}\n"
            f"距今: {received_hours:.0f} 小时\n附件: 否\n问: 这封邮件应该如何分类与处理?")
    t0 = time.time()
    inp = tok(f"user\n{text}\nassistant\n", return_tensors="pt").to(model.device)
    with torch.inference_mode():
        out = model.generate(
            **inp, max_new_tokens=40, do_sample=False,
            pad_token_id=tok.pad_token_id,
            repetition_penalty=1.2, no_repeat_ngram_size=6)
    dt = int((time.time() - t0) * 1000)
    gen = out[0][inp["input_ids"].shape[1]:]
    raw = tok.decode(gen, skip_special_tokens=True).strip()
    print(f"\n---")
    print(f"FROM   : {sender[:50]}")
    print(f"SUBJ   : {subject[:60]}")
    print(f"DECIDE : {raw[:200]}")
    print(f"LATENCY: {dt}ms")