#!/usr/bin/env python3
"""probe_log_base.py — 上传 aliyun 跑 5 条样本看 log base 顺序"""
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
samples = [
    ("nginx","error","[error] 1234#0: upstream timed out"),
    ("python","warning","warnings.warn('deprecated constant', DeprecationWarning)"),
    ("systemd","critical","kernel: Out of memory: Killed process 1234"),
    ("docker","info","Container started successfully"),
    ("postgresql","fatal","FATAL: database files are incompatible with server"),
]
for src, lv, ct in samples:
    text = f"日志来源: {src}\n时间戳: 2026/09/23 15:11:10\n级别: {lv}\n内容: {ct}\n问: 这条日志应该如何分类与处理?"
    prompt = 'user\n' + text + '\nassistant\n'
    inp = tok(prompt, return_tensors='pt').to(model.device)
    out = model.generate(**inp, max_new_tokens=40, do_sample=False,
                          pad_token_id=tok.pad_token_id,
                          repetition_penalty=1.2, no_repeat_ngram_size=6)
    gen = out[0][inp['input_ids'].shape[1]:]
    raw = tok.decode(gen, skip_special_tokens=True).strip()
    print(f'--- {src}/{lv} ---')
    print(repr(raw))
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username='root', password=PASS, timeout=15)
sftp = c.open_sftp()
sftp.putfo(io.BytesIO(probe.encode('utf-8').replace(b'\r\n', b'\n')),
           '/workspace/companion/probe_log_base.py')
sftp.close()
print("uploaded")
si, so, se = c.exec_command('cd /workspace/companion && python3 probe_log_base.py 2>&1 | tail -20')
print(so.read().decode(errors='replace'))
c.close()