#!/usr/bin/env python3
"""dl_intents_conf.py — fresh conn each iteration"""
import paramiko, os
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"
LOCAL_BASE = "D:/prisir-train-assets/trained"

for name in ["intents", "intents_conf"]:
    remote = f"/workspace/qwen3guard-{name}/adapter"
    local_dir = f"{LOCAL_BASE}/{name}/adapter"
    os.makedirs(local_dir, exist_ok=True)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ALIYUN, username="root", password=PASS,
              timeout=10, banner_timeout=10, auth_timeout=10)
    sftp = c.open_sftp()
    print(f"[download] {remote} -> {local_dir}")
    try:
        files = sftp.listdir(remote)
    except FileNotFoundError as e:
        print(f"  ❌ dir not found: {e}")
        sftp.close(); c.close()
        continue
    for f in files:
        if (f.endswith(".safetensors")
            or f in ("adapter_config.json", "tokenizer.json",
                     "tokenizer_config.json", "special_tokens_map.json",
                     "merges.txt", "vocab.json")):
            print(f"  {f} ... ", end="", flush=True)
            try:
                sftp.get(f"{remote}/{f}", f"{local_dir}/{f}")
                print("OK")
            except Exception as e:
                print(f"FAIL: {e}")
    sftp.close(); c.close()

# verify
for name in ["intents", "intents_conf"]:
    p = f"{LOCAL_BASE}/{name}/adapter/adapter_model.safetensors"
    print(f"[verify] {p}: {os.path.exists(p)}, size={os.path.getsize(p)//1024}KB" if os.path.exists(p) else f"[verify] {p}: MISSING")