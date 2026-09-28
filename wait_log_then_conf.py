#!/usr/bin/env python3
# wait_log_then_conf.py — 等 log base 训完,跑 confidence_teacher 准备 conf 训练
import os, paramiko, time
ALIYUN = "43.106.53.242"
PASS = os.environ.get("ALIYUN_SSH_PASS", "PrisirTrain2026!")
LOG = "/workspace/run_train_log.log"
ADAPTER = "/workspace/qwen3guard-log/adapter/adapter_model.safetensors"
DATA = "/workspace/data_log.jsonl"
BASE = "/workspace/models/Qwen3Guard-Gen-0.6B"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username="root", password=PASS, timeout=15)

print("[1/3] 等待 log base 训练完成...")
last_size = -1; last_change = time.time(); start = time.time()
while time.time() - start < 600:
    si, so, se = c.exec_command(f"stat -c '%s' {LOG} 2>/dev/null && tail -1 {LOG} 2>&1")
    out = so.read().decode(errors="replace")
    size = int(out.split()[0]) if out.split() else 0
    tail = out.split("\n")[-1] if "\n" in out else out
    if size != last_size:
        last_size = size; last_change = time.time()
        print(f"  [{int(time.time()-start)}s] log={size}B | {tail[:80]!r}")
    elif time.time() - last_change > 30:
        print(f"  ✅ log stable 30s + tail={tail[:80]!r}")
        break
    time.sleep(8)

# 验证 adapter 产物
si, so, se = c.exec_command(f"ls -la {ADAPTER} 2>&1")
print(f"\n[2/3] adapter 文件:\n{so.read().decode().strip()}")

# 跑 confidence_teacher
print(f"\n[3/3] 跑 confidence_teacher 生成 data_log_conf.jsonl...")
ct_cmd = (f"cd /workspace/companion && python3 confidence_teacher.py "
          f"--data {DATA} --base {BASE} "
          f"--adapter /workspace/qwen3guard-log/adapter "
          f"--schema log "
          f"--output /workspace/data_log_conf.jsonl 2>&1")
si, so, se = c.exec_command(ct_cmd, timeout=600)
print(so.read().decode(errors="replace")[-1500:])

# 检查产物
si, so, se = c.exec_command("ls -la /workspace/data_log_conf.jsonl 2>&1 && wc -l /workspace/data_log_conf.jsonl 2>&1")
print(f"\n  data_log_conf.jsonl:\n{so.read().decode().strip()}")

c.close()