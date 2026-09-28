#!/usr/bin/env python3
"""probe_tempfile_conf_v2.py — bench 加速参数下探 v2 输出"""
import json, random, torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base = AutoModelForCausalLM.from_pretrained(
    '/workspace/models/Qwen3Guard-Gen-0.6B', trust_remote_code=True,
    torch_dtype=torch.float16, device_map='auto')
tok = AutoTokenizer.from_pretrained(
    '/workspace/qwen3guard-tempfile-conf-v2/adapter', trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
model = PeftModel.from_pretrained(base, '/workspace/qwen3guard-tempfile-conf-v2/adapter')
model.eval()

rows = []
with open('/workspace/data_tempfile_conf.jsonl') as f:
    for line in f:
        rows.append(json.loads(line))
random.seed(42); random.shuffle(rows)

newline_id = tok.encode('\n', add_special_tokens=False)[-1]
for r in rows[:5]:
    prompt = f"user\n{r['text']}\nassistant\n"
    inp = tok(prompt, return_tensors='pt').to('cuda')
    with torch.inference_mode():
        out = model.generate(
            **inp, max_new_tokens=30, do_sample=False,
            pad_token_id=tok.pad_token_id,
            eos_token_id=[tok.eos_token_id, newline_id],
            repetition_penalty=1.5,
            no_repeat_ngram_size=6)
    gen = out[0][inp['input_ids'].shape[1]:]
    raw = tok.decode(gen, skip_special_tokens=True)
    print(f"GOLD: {r['risk_label']:8s}/{r['_action']:8s}")
    print(f"OUT ({len(gen)} tok): {raw!r}")
    print('---')