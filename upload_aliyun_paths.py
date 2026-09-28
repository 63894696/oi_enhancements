#!/usr/bin/env python3
"""run_bench_aliyun.py — 在 aliyun 上跑 bench,override adapter paths"""
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('43.106.53.242', username='root', password='PrisirTrain2026!', timeout=10, banner_timeout=10, auth_timeout=10)

# Override adapter paths 用 monkey patch(adapter_registry 用绝对 Windows 路径)
# aliyun 上有 Linux 路径,/workspace/qwen3guard-*/
si, so, se = c.exec_command(
    "cd /workspace/companion && python3 -c \"\nimport sys\nsys.path.insert(0, '/workspace/companion')\nfrom pathlib import Path\nimport adapter_registry\n# Override paths for aliyun\nadapter_registry.ADAPTERS['intents'].adapter_path = Path('/workspace/qwen3guard-intents/adapter')\nadapter_registry.ADAPTERS['intents_conf'].adapter_path = Path('/workspace/qwen3guard-intents-conf/adapter')\nimport bench_intents_local\nbench_intents_local.run_bench = bench_intents_local.run_bench.__wrapped__ if hasattr(bench_intents_local.run_bench, '__wrapped__') else bench_intents_local.run_bench\nfrom classify_intents import classify_intents\nfrom bench_intents_local import TEST_CASES\nimport json, time\nreport = bench_intents_local.run_bench(TEST_CASES)\nPath('/workspace/bench_intents_local_2026-09-23.json').write_text(\n    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')\nprint('REPORT WRITTEN')\n\" 2>&1 | tail -50",
    timeout=600,
)
print(so.read().decode(errors="replace"))
c.close()