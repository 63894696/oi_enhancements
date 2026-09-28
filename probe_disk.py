#!/usr/bin/env python3
"""probe_disk.py — 比较 disk_cleanup 输出"""
import os, paramiko, io
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"

probe = r'''#!/usr/bin/env python3
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel
for adp in ["/workspace/qwen3guard-disk-cleanup/adapter",
            "/workspace/qwen3guard-disk-cleanup-conf/adapter"]:
    tok = AutoTokenizer.from_pretrained(adp, trust_remote_code=True)
    if tok.pad_token is None: tok.pad_token = tok.eos_token
    base = AutoModelForCausalLM.from_pretrained('/workspace/models/Qwen3Guard-Gen-0.6B', trust_remote_code=True, torch_dtype=torch.float16, device_map='auto')
    model = PeftModel.from_pretrained(base, adp); model.eval()
    text = "路径: C:\\Windows\\Temp\\test.log\n扩展名: log\n大小: 12KB\n年龄: 30天\n问: 该文件如何处理?"
    prompt = 'user\n' + text + '\nassistant\n'
    inp = tok(prompt, return_tensors='pt').to(model.device)
    out = model.generate(**inp, max_new_tokens=40, do_sample=False,
                          pad_token_id=tok.pad_token_id,
                          repetition_penalty=1.2, no_repeat_ngram_size=6)
    gen = out[0][inp['input_ids'].shape[1]:]
    raw = tok.decode(gen, skip_special_tokens=True).strip()
    print(f'--- {adp.split("/")[-2]} ---')
    print(repr(raw))
    del model; del base
    import gc; gc.collect()
    torch.cuda.empty_cache()
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username='root', password=PASS, timeout=15)
sftp = c.open_sftp()
sftp.putfo(io.BytesIO(probe.encode('utf-8').replace(b'\r\n', b'\n')),
           '/workspace/companion/probe_disk.py')
sftp.close()
si, so, se = c.exec_command('cd /workspace/companion && python3 probe_disk.py 2>&1 | tail -15')
print(so.read().decode(errors='replace'))
c.close()