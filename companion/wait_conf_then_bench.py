#!/usr/bin/env python3
# wait_conf_then_bench.py — 轮询 email_conf v2 训练,完了跑 bench_email.py
import os, paramiko, time, json
from pathlib import Path

ALIYUN = "43.106.53.242"
PASS = os.environ.get("ALIYUN_SSH_PASS", "PrisirTrain2026!")
LOG = "/workspace/run_train_email_conf_v2.log"
ADAPTER = "/workspace/qwen3guard-email-conf/adapter/adapter_model.safetensors"
DATA = "/workspace/data_email_conf.jsonl"
BASE = "/workspace/models/Qwen3Guard-Gen-0.6B"
OUT = "/workspace/bench_email_conf_v2.json"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username="root", password=PASS, timeout=15)

print("[1/3] 等待 email_conf v2 训练完成...")
last_size = -1
last_change = time.time()
start = time.time()
while time.time() - start < 600:
    si, so, se = c.exec_command(f"stat -c '%s' {LOG} 2>/dev/null && tail -1 {LOG} 2>&1")
    out = so.read().decode(errors="replace")
    size = int(out.split()[0]) if out.split() else 0
    tail = out.split("\n")[-1] if "\n" in out else out
    if size != last_size:
        last_size = size; last_change = time.time()
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] log={size}B | {tail[:80]!r}")
    elif time.time() - last_change > 30:
        print(f"  ✅ log stable 30s + tail={tail[:80]!r}")
        break
    time.sleep(10)

# 验证产物
si, so, se = c.exec_command(f"ls -la {ADAPTER} 2>&1")
out = so.read().decode()
print(f"\n[2/3] adapter 文件:\n{out.strip()}")

# 跑 bench_email.py
print(f"\n[3/3] 跑 bench_email.py (skip-base 加速)...")
bench_cmd = (f"cd /workspace/companion && python3 bench_email.py "
             f"--data {DATA} --base-model {BASE} "
             f"--adapter /workspace/qwen3guard-email-conf/adapter "
             f"--output {OUT} --skip-base 2>&1")
si, so, se = c.exec_command(bench_cmd, timeout=900)
out = so.read().decode(errors="replace")
print(out[-3000:])

# 看 bench json
si, so, se = c.exec_command(f"cat {OUT} 2>&1")
report = so.read().decode(errors="replace")
print(f"\n[bench json {OUT}]:")
try:
    j = json.loads(report)
    if "with_adapter" in j:
        wa = j["with_adapter"]
        cal = wa.get("calibration", {})
        print(f"  risk_acc={wa.get('risk_accuracy'):.3f}  "
              f"action_acc={wa.get('action_accuracy'):.3f}  "
              f"parse_fail={wa.get('parse_fail_rate'):.3f}")
        print(f"  risk_conf correct={cal.get('risk_conf_mean_correct')}  "
              f"wrong={cal.get('risk_conf_mean_wrong')}  "
              f"delta={(cal.get('risk_conf_mean_correct') or 0) - (cal.get('risk_conf_mean_wrong') or 0):.3f}")
        print(f"  latency p50/p95={wa.get('latency_ms',{}).get('p50')}/{wa.get('latency_ms',{}).get('p95')}ms")
except Exception as e:
    print(f"  parse err: {e}")
    print(report[:2000])

c.close()