#!/usr/bin/env python3
"""run_perf_guard_aliyun.py — aliyun 上跑 perf_guard.py --once 测试"""
import paramiko
HOST = '43.106.53.242'
USER = 'root'
PASS = 'PrisirTrain2026!'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=15, banner_timeout=15, auth_timeout=15)

CMD = r"""cd /workspace && python3 -c "
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

from perf_guard import run_once, log_alert
import json

# 模拟 3 个场景:normal / high / critical
samples = [
    # 1. 正常
    {'ts':'2026-09-23T12:00:00Z','cpu':{'pct':30,'count':6,'freq_mhz':3696},
     'memory':{'used_pct':40,'used_gb':13,'total_gb':32,'available_gb':19},
     'net':{'n_total':9,'n_up':9,'nics':[]},
     'system':{'uptime_s':3000,'user':'root'},
     'crash':{'bugcheck_count':0,'kp41_count':0}},
    # 2. 高 CPU + TAP
    {'ts':'2026-09-23T14:00:00Z','cpu':{'pct':92,'count':6,'freq_mhz':3696},
     'memory':{'used_pct':88,'used_gb':28,'total_gb':32,'available_gb':4},
     'net':{'n_total':9,'n_up':6,'nics':[
        {'nic':'tap0901','isup':False},
        {'nic':'vktap','isup':False},
        {'nic':'tapprotonvpn','isup':False},
     ]},
     'system':{'uptime_s':3500,'user':'root'},
     'crash':{'bugcheck_count':1,'kp41_count':1}},
    # 3. critical — boot<60 + bugcheck>=2 + ndis fingerprint
    {'ts':'2026-09-23T03:26:05Z','cpu':{'pct':5,'count':6,'freq_mhz':3696},
     'memory':{'used_pct':15,'used_gb':5,'total_gb':32,'available_gb':27},
     'net':{'n_total':9,'n_up':6,'nics':[
        {'nic':'tap0901','isup':False},
        {'nic':'vktap','isup':False},
        {'nic':'tapprotonvpn','isup':False},
     ]},
     'system':{'uptime_s':18,'user':'root'},
     'crash':{'bugcheck_count':5,'kp41_count':5}},
]
for s in samples:
    rec = run_once(verbose=True, sample=s)
    if rec['alert']:
        log_alert(rec)
    print(json.dumps(rec, ensure_ascii=False)[:200])
    print()
" 2>&1 | tail -40
"""
si, so, se = c.exec_command(CMD, timeout=180)
print(so.read().decode(errors="replace"))
c.close()