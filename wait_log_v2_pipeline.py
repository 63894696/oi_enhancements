#!/usr/bin/env python3
"""wait_log_v2_pipeline.py — 等 log base v2 训完 → 自动起 log_conf → 自动 bench"""
import paramiko, time, json, io

ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"

# Wait for log base v2 to complete
print("[1/5] 等 log base v2 训练...")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username='root', password=PASS, timeout=15)

LOG = "/workspace/run_train_log_v2.log"
last_size = -1
last_change = time.time()
start = time.time()
while time.time() - start < 600:
    si, so, se = c.exec_command(f"stat -c '%s' {LOG} 2>/dev/null && tail -1 {LOG} 2>&1")
    out = so.read().decode(errors="replace")
    size = int(out.split()[0]) if out.split() else 0
    tail = out.split("\n")[-1] if "\n" in out else out
    if size != last_size:
        last_size = size
        last_change = time.time()
        print(f"  [{int(time.time()-start)}s] log={size}B | {tail[:80]!r}")
    elif time.time() - last_change > 30:
        print(f"  ✅ log base v2 done | {tail[:80]!r}")
        break
    time.sleep(8)

# Verify
si, so, se = c.exec_command('ls -la /workspace/qwen3guard-log/adapter/adapter_model.safetensors 2>&1')
print(f"\n[2/5] log base v2 adapter:\n{so.read().decode().strip()}")

# Run confidence_teacher on log base v2 → /workspace/data_log_conf_v2.jsonl
print(f"\n[3/5] 跑 confidence_teacher 生成 v2 conf data...")
ct_cmd = ("cd /workspace/companion && python3 confidence_teacher.py "
          "--data /workspace/data_log.jsonl "
          "--base /workspace/models/Qwen3Guard-Gen-0.6B "
          "--adapter /workspace/qwen3guard-log/adapter "
          "--schema log "
          "--output /workspace/data_log_conf_v2.jsonl 2>&1 | tail -3")
si, so, se = c.exec_command(ct_cmd, timeout=600)
print(so.read().decode(errors="replace"))

# Verify
si, so, se = c.exec_command('ls -la /workspace/data_log_conf_v2.jsonl 2>&1 | tail -1 && head -c 200 /workspace/data_log_conf_v2.jsonl')
print(so.read().decode(errors="replace")[:300])

# Launch log_conf v2 training in background
print(f"\n[4/5] 启 log_conf v2 训练...")
si, so, se = c.exec_command('cd /workspace && setsid nohup bash run_train_log_conf_v2.sh > /workspace/run_train_log_conf_v2.log 2>&1 < /dev/null & echo PID=$!')
print(so.read().decode(errors="replace").strip())

# Wait for log_conf v2 to complete
LOG2 = "/workspace/run_train_log_conf_v2.log"
last_size = -1
last_change = time.time()
start = time.time()
while time.time() - start < 600:
    si, so, se = c.exec_command(f"stat -c '%s' {LOG2} 2>/dev/null && tail -1 {LOG2} 2>&1")
    out = so.read().decode(errors="replace")
    size = int(out.split()[0]) if out.split() else 0
    tail = out.split("\n")[-1] if "\n" in out else out
    if size != last_size:
        last_size = size
        last_change = time.time()
        print(f"  [{int(time.time()-start)}s] log={size}B | {tail[:80]!r}")
    elif time.time() - last_change > 30:
        print(f"  ✅ log_conf v2 done | {tail[:80]!r}")
        break
    time.sleep(8)

# Bench
print(f"\n[5/5] 跑 bench...")
# bench_log.py already uploaded. Run it on v2 conf data
bench_cmd = ('cd /workspace/companion && python3 bench_log.py '
             '--data /workspace/data_log_conf_v2.jsonl '
             '--base-model /workspace/models/Qwen3Guard-Gen-0.6B '
             '--adapter /workspace/qwen3guard-log-conf/adapter '
             '--output /workspace/bench_log_conf_v2.json 2>&1 | tail -10')
si, so, se = c.exec_command(bench_cmd, timeout=600)
print(so.read().decode(errors="replace"))

# Summary
si, so, se = c.exec_command('cat /workspace/bench_log_conf_v2.json 2>&1')
report = so.read().decode(errors="replace")
try:
    j = json.loads(report)
    wa = j["with_adapter"]
    cal = wa.get("calibration", {})
    print(f"\n[v2 bench summary]:")
    print(f"  risk_acc={wa.get('risk_accuracy'):.3f}")
    print(f"  action_acc={wa.get('action_accuracy'):.3f}")
    print(f"  parse_fail={wa.get('parse_fail_rate'):.3f}")
    rc = cal.get('risk_conf_mean_correct') or 0
    rw = cal.get('risk_conf_mean_wrong') or 0
    print(f"  risk_conf correct={rc} wrong={rw} delta={rc-rw:.4f}")
    print(f"  latency p50/p95={wa.get('latency_ms',{}).get('p50')}/{wa.get('latency_ms',{}).get('p95')}ms")
except Exception as e:
    print(f"parse err: {e}")
    print(report[:2000])

c.close()