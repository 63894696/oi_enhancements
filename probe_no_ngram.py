#!/usr/bin/env python3
"""probe_no_ngram.py — 测不同 inference 参数"""
import os, paramiko, io
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"

probe = r'''#!/usr/bin/env python3
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel
tok = AutoTokenizer.from_pretrained('/workspace/qwen3guard-log/adapter', trust_remote_code=True)
if tok.pad_token is None: tok.pad_token = tok.eos_token
base = AutoModelForCausalLM.from_pretrained('/workspace/models/Qwen3Guard-Gen-0.6B', trust_remote_code=True, torch_dtype=torch.float16, device_map='auto')
model = PeftModel.from_pretrained(base, '/workspace/qwen3guard-log/adapter'); model.eval()
sample_text = "日志来源: nginx\n时间戳: 2026/09/23 15:11:10\n级别: error\n内容: [error] 1234#0: upstream timed out\n问: 这条日志应该如何分类与处理?"
prompt = 'user\n' + sample_text + '\nassistant\n'
inp = tok(prompt, return_tensors='pt').to(model.device)
configs = [
    {"name": "default(rep=1.2,no_ngram=6)", "max_new_tokens":40, "repetition_penalty":1.2, "no_repeat_ngram_size":6},
    {"name": "rep=1.0,no_ngram=0", "max_new_tokens":40, "repetition_penalty":1.0, "no_repeat_ngram_size":0},
    {"name": "rep=1.1,no_ngram=4", "max_new_tokens":40, "repetition_penalty":1.1, "no_repeat_ngram_size":4},
    {"name": "rep=1.2,no_ngram=0", "max_new_tokens":40, "repetition_penalty":1.2, "no_repeat_ngram_size":0},
    {"name": "rep=1.5,no_ngram=6", "max_new_tokens":40, "repetition_penalty":1.5, "no_repeat_ngram_size":6},
]
for cfg in configs:
    out = model.generate(**inp, max_new_tokens=cfg["max_new_tokens"], do_sample=False,
                          pad_token_id=tok.pad_token_id,
                          repetition_penalty=cfg["repetition_penalty"],
                          no_repeat_ngram_size=cfg["no_repeat_ngram_size"])
    gen = out[0][inp['input_ids'].shape[1]:]
    raw = tok.decode(gen, skip_special_tokens=True).strip()
    print(f'--- {cfg["name"]} ---')
    print(repr(raw))
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username='root', password=PASS, timeout=15)
sftp = c.open_sftp()
sftp.putfo(io.BytesIO(probe.encode('utf-8').replace(b'\r\n', b'\n')),
           '/workspace/companion/probe_no_ngram.py')
sftp.close()
si, so, se = c.exec_command('cd /workspace/companion && python3 probe_no_ngram.py 2>&1 | tail -25')
print(so.read().decode(errors='replace'))
c.close()