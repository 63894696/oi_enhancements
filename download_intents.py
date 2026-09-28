#!/usr/bin/env python3
"""download_intents.py — 把 aliyun 两个 intents adapter 下载到本地"""
import paramiko, os
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"
LOCAL = "D:/prisir-train-assets/trained"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username="root", password=PASS, timeout=10, banner_timeout=10, auth_timeout=10)
sftp = c.open_sftp()

for name in ["intents", "intents_conf"]:
    remote = f"/workspace/qwen3guard-{name}/adapter"
    local_dir = f"{LOCAL}/{name}/adapter"
    os.makedirs(local_dir, exist_ok=True)
    print(f"[download] {remote} -> {local_dir}")
    for f in sftp.listdir(remote):
        if f.endswith(".safetensors") or f in (
            "adapter_config.json", "tokenizer.json", "tokenizer_config.json",
            "special_tokens_map.json", "merges.txt", "vocab.json",
        ):
            print(f"  {f}", end=" ... ")
            try:
                sftp.get(f"{remote}/{f}", f"{local_dir}/{f}")
                print("OK")
            except Exception as e:
                print(f"FAIL: {e}")

# 验证
for name in ["intents", "intents_conf"]:
    p = f"{LOCAL}/{name}/adapter/adapter_model.safetensors"
    print(f"\n[verify] {p}: {os.path.exists(p)}, size={os.path.getsize(p) if os.path.exists(p) else 0}")

sftp.close()
c.close()