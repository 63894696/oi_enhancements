#!/usr/bin/env python3
"""run_probe_perf_v2_critical.py — 看 critical 10 条的真实 raw"""
import paramiko
HOST = '43.106.53.242'
USER = 'root'
PASS = 'PrisirTrain2026!'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=15, banner_timeout=15, auth_timeout=15)

CMD = r"""cd /workspace/companion && python3 -c "
import sys
sys.path.insert(0, '/workspace/companion')
from pathlib import Path
from dataclasses import replace
import adapter_registry
ALIYUN_BASE = Path('/workspace/models/Qwen3Guard-Gen-0.6B')
for name in list(adapter_registry.ADAPTERS.keys()):
    old = adapter_registry.ADAPTERS[name]
    adapter_registry.ADAPTERS[name] = replace(old, base_model=ALIYUN_BASE)
old = adapter_registry.ADAPTERS['perf_conf_v2']
adapter_registry.ADAPTERS['perf_conf_v2'] = replace(
    old, adapter_path=Path('/workspace/qwen3guard-perf-conf-v2/adapter'))

import bench_perf_local
from bench_perf_local import TEST_CASES
from adapter_registry import get_adapter
from classify_perf import classify_perf
adapter = get_adapter('perf_conf_v2')

# 看所有 critical
critical_cases = [t for t in TEST_CASES if t['risk'] == 'critical']
for i, case in enumerate(critical_cases):
    out = classify_perf(adapter, case['sample'])
    print(f'[{i}] truth=critical → pred={out[\"risk\"]} action={out[\"action\"]} conf={out[\"risk_conf\"]}')
    print(f'   text:', out['raw'][:100])
" 2>&1 | tail -40
"""
si, so, se = c.exec_command(CMD, timeout=600)
print(so.read().decode(errors="replace"))
c.close()