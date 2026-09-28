#!/usr/bin/env python3
# wait_train_then_conf.py — 轮询训练日志,完了自动跑 confidence_teacher + 训 email_conf v2
import os, paramiko, time, sys

ALIYUN = "43.106.53.242"
USER = "root"
PASS = os.environ.get("ALIYUN_SSH_PASS", "PrisirTrain2026!")

EMAIL_BASE_LOG = "/workspace/run_train_email_v2.log"
EMAIL_BASE_OUT = "/workspace/qwen3guard-email"
EMAIL_CONF_LOG = "/workspace/run_train_email_conf_v2.log"


def ssh():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ALIYUN, username=USER, password=PASS, timeout=15)
    return c


def run(c, cmd, timeout=60):
    si, so, se = c.exec_command(cmd, timeout=timeout)
    return so.read().decode(errors="replace"), se.read().decode(errors="replace")


def is_done(c):
    """判定训练结束:文件不再变动 30s + log 末尾有 'end' 标记"""
    out, _ = run(c, f"tail -3 {EMAIL_BASE_LOG} 2>&1")
    return "end" in out or "End" in out or "saved" in out.lower()


def wait_until_done(c, max_minutes=15):
    last_size = -1
    last_change = time.time()
    start = time.time()
    while time.time() - start < max_minutes * 60:
        try:
            out, _ = run(c, f"stat -c '%s' {EMAIL_BASE_LOG} 2>/dev/null && tail -1 {EMAIL_BASE_LOG} 2>&1")
            size = int(out.split()[0]) if out.split() else 0
            tail = out.split("\n")[-1] if "\n" in out else out
        except Exception as e:
            print(f"  poll err: {e}")
            time.sleep(10)
            continue
        if size != last_size:
            last_size = size
            last_change = time.time()
            print(f"  [{int(time.time()-start)}s] log={size}B, tail: {tail[:80]!r}")
        elif time.time() - last_change > 30:
            print(f"  log stable 30s + tail = {tail[:80]!r}")
            return True
        time.sleep(10)
    return False


def main():
    c = ssh()
    print("[1/3] 等待 email base v2 训练完成...")
    if not wait_until_done(c, max_minutes=12):
        print("  ⚠ 训练超时未结束 — 退出")
        return
    print("  ✅ email base v2 done")

    # 确认 adapter 写入
    out, _ = run(c, f"ls -la {EMAIL_BASE_OUT}/adapter_model.safetensors 2>&1")
    print(f"[2/3] adapter 文件:\n  {out.strip()}")

    # 跑 confidence_teacher
    print("[3/3] 跑 confidence_teacher 生成 data_email_conf.jsonl...")
    ct_cmd = ("cd /workspace/companion && python3 confidence_teacher.py "
              "--data /workspace/data_email.jsonl "
              "--base-model /workspace/models/Qwen3Guard-Gen-0.6B "
              "--adapter /workspace/qwen3guard-email/adapter "
              "--schema email "
              "--output /workspace/data_email_conf.jsonl 2>&1")
    out, _ = run(c, ct_cmd, timeout=600)
    print(out[-2000:])

    # 检查产物
    out, _ = run(c, "ls -la /workspace/data_email_conf.jsonl 2>&1 && wc -l /workspace/data_email_conf.jsonl 2>&1")
    print(f"\n  data_email_conf.jsonl:\n  {out.strip()}")

    c.close()


if __name__ == "__main__":
    main()