#!/usr/bin/env python3
import paramiko, time
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('43.106.53.242', username='root', password='PrisirTrain2026!', timeout=10, banner_timeout=10, auth_timeout=10)

LOG = "/workspace/run_train_intents_conf_v1.log"
last_size = -1
last_change = time.time()
start = time.time()
while time.time() - start < 900:
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
        print(f"  ✅ done | {tail[:120]!r}")
        break
    time.sleep(8)

si, so, se = c.exec_command("ls -la /workspace/qwen3guard-intents-conf/adapter/adapter_model.safetensors 2>&1 | head -1")
print("[adapter]:", so.read().decode())
c.close()