#!/usr/bin/env python3
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('43.106.53.242', username='root', password='PrisirTrain2026!', timeout=10, banner_timeout=10, auth_timeout=10)

# 1. 看 raw 字段 + 错误细节
si, so, se = c.exec_command(
    "cd /workspace/companion && python3 -c \"\nimport json\nfrom classify_intents import classify_intents\nout = classify_intents('帮我打开浏览器', use_conf=True)\nprint(json.dumps(out, ensure_ascii=False, indent=2))\n\" 2>&1 | head -30",
    timeout=60,
)
print(so.read().decode(errors="replace"))
c.close()