#!/usr/bin/env python3
"""probe_train_row.py — 看训练样本推理输出(应该完全匹配 target)"""
import os, paramiko, io
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"

probe = r'''#!/usr/bin/env python3
import torch, json
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel
tok = AutoTokenizer.from_pretrained('/workspace/qwen3guard-log/adapter', trust_remote_code=True)
if tok.pad_token is None: tok.pad_token = tok.eos_token
base = AutoModelForCausalLM.from_pretrained('/workspace/models/Qwen3Guard-Gen-0.6B', trust_remote_code=True, torch_dtype=torch.float16, device_map='auto')
model = PeftModel.from_pretrained(base, '/workspace/qwen3guard-log/adapter'); model.eval()

# Use FIRST training row
with open('/workspace/data_log.jsonl') as f:
    row = json.loads(f.readline())
print('TRAIN TEXT:', row['text'][:100])
print('EXPECTED TARGET: Safety:', row['risk_label'], '| Jailbreak: No | Action:', row['_action'])
prompt = 'user\n' + row['text'] + '\nassistant\n'
inp = tok(prompt, return_tensors='pt').to(model.device)
for cfg in [{"name":"rep=1.0", "rep":1.0, "ngram":0},
            {"name":"rep=1.2 ngram=6", "rep":1.2, "ngram":6}]:
    out = model.generate(**inp, max_new_tokens=40, do_sample=False,
                          pad_token_id=tok.pad_token_id,
                          repetition_penalty=cfg["rep"],
                          no_repeat_ngram_size=cfg["ngram"])
    gen = out[0][inp['input_ids'].shape[1]:]
    raw = tok.decode(gen, skip_special_tokens=True).strip()
    print(f'--- TRAIN ROW {cfg["name"]} ---')
    print(repr(raw))
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username='root', password=PASS, timeout=15)
sftp = c.open_sftp()
sftp.putfo(io.BytesIO(probe.encode('utf-8').replace(b'\r\n', b'\n')),
           '/workspace/companion/probe_train_row.py')
sftp.close()
si, so, se = c.exec_command('cd /workspace/companion && python3 probe_train_row.py 2>&1 | tail -15')
print(so.read().decode(errors='replace'))
c.close()