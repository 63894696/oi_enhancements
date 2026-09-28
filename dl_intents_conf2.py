#!/usr/bin/env python3
"""dl_intents_conf2.py — use listdir_attr for naming"""
import paramiko, os
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"
LOCAL_BASE = "D:/prisir-train-assets/trained"

# Files to download (using attr filename)
WANT_SUFFIX = ".safetensors"
WANT_NAMES = {
    "adapter_config.json", "tokenizer.json",
    "tokenizer_config.json", "special_tokens_map.json",
    "merges.txt", "vocab.json",
}

for name in ["intents_conf"]:
    remote_dir = f"/workspace/qwen3guard-{name.replace('_', '-')}/adapter"
    local_dir = f"{LOCAL_BASE}/{name}/adapter"
    os.makedirs(local_dir, exist_ok=True)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ALIYUN, username="root", password=PASS,
              timeout=10, banner_timeout=10, auth_timeout=10)
    sftp = c.open_sftp()

    print(f"[download] {remote_dir} -> {local_dir}")
    attrs = sftp.listdir_attr(remote_dir)
    for a in attrs:
        fn = a.filename
        if fn.endswith(WANT_SUFFIX) or fn in WANT_NAMES:
            print(f"  {fn} ({a.st_size}B) ... ", end="", flush=True)
            # 每个文件一个 SFTP 会话,避免长连接超时
            try:
                sftp.close()
            except Exception:
                pass
            sftp = c.open_sftp()
            try:
                sftp.get(f"{remote_dir}/{fn}", f"{local_dir}/{fn}")
                print("OK")
            except Exception as e:
                print(f"FAIL: {e}")

    sftp.close(); c.close()

# verify
for name in ["intents", "intents_conf"]:
    p = f"{LOCAL_BASE}/{name}/adapter/adapter_model.safetensors"
    sz = os.path.getsize(p) // 1024 if os.path.exists(p) else 0
    print(f"[verify] {p}: exists={os.path.exists(p)}, size={sz}KB")