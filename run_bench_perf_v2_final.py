#!/usr/bin/env python3
"""run_bench_perf_v2_final.py — 跑 perf_conf_v2 adapter bench"""
import paramiko
HOST = '43.106.53.242'
USER = 'root'
PASS = 'PrisirTrain2026!'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=15, banner_timeout=15, auth_timeout=15)

CMD = r"""cd /workspace/companion && python3 -c "
import sys, json
sys.path.insert(0, '/workspace/companion')

from pathlib import Path
from dataclasses import replace
import adapter_registry

ALIYUN_BASE = Path('/workspace/models/Qwen3Guard-Gen-0.6B')
for name in list(adapter_registry.ADAPTERS.keys()):
    old = adapter_registry.ADAPTERS[name]
    adapter_registry.ADAPTERS[name] = replace(old, base_model=ALIYUN_BASE)
# perf_conf_v2 用 v2 输出目录
old = adapter_registry.ADAPTERS['perf_conf_v2']
adapter_registry.ADAPTERS['perf_conf_v2'] = replace(
    old, adapter_path=Path('/workspace/qwen3guard-perf-conf-v2/adapter'))

import bench_perf_local
from bench_perf_local import TEST_CASES, run_bench
from adapter_registry import get_adapter
adapter = get_adapter('perf_conf_v2')
report = run_bench(adapter, TEST_CASES)
Path('/workspace/bench_perf_v2_final.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('ACC', report['accuracy'], 'PF', report['parse_fail_rate'], 'P50', report['latency_ms']['p50'])
for cls, m in report['per_class'].items():
    print(' ', cls, f'{m[\"correct\"]}/{m[\"total\"]}', f'{m[\"accuracy\"]*100:.0f}%')
" 2>&1 | tail -20
"""
si, so, se = c.exec_command(CMD, timeout=900)
print(so.read().decode(errors="replace"))
c.close()