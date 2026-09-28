#!/usr/bin/env python3
"""wait_intents_train.py — poll until intents base train done"""
import paramiko, time
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username="root", password=PASS, timeout=10, banner_timeout=10, auth_timeout=10)

LOG = "/workspace/run_train_intents_v1.log"
last_size = -1
last_change = time.time()
start = time.time()

while time.time() - start < 900:  # 15min 超时
    si, so, se = c.exec_command(f"stat -c '%s' {LOG} 2>/dev/null; tail -1 {LOG} 2>&1")
    out = so.read().decode(errors="replace")
    parts = out.split("\n", 1)
    size = int(parts[0].strip()) if parts[0].strip().isdigit() else 0
    tail = parts[1] if len(parts) > 1 else ""
    if size != last_size:
        last_size = size
        last_change = time.time()
        print(f"  [{int(time.time()-start)}s] log={size}B | {tail[:100]!r}")
    elif time.time() - last_change > 30:
        print(f"  ✅ train done | {tail[:120]!r}")
        break
    time.sleep(8)

# 验证 adapter 落盘
si, so, se = c.exec_command("ls -la /workspace/qwen3guard-intents/adapter/adapter_model.safetensors 2>&1 | head -3")
print("\n[adapter]:")
print(so.read().decode())
c.close()