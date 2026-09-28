#!/usr/bin/env python3
"""run_classify_perf_aliyun.py — 在 aliyun 上跑 classify_perf 测推理"""
import paramiko
HOST = '43.106.53.242'
USER = 'root'
PASS = 'PrisirTrain2026!'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=15, banner_timeout=15, auth_timeout=15)
CMD = """cd /workspace && python3 -c "
import sys
sys.path.insert(0, '/workspace/companion')
from pathlib import Path
from dataclasses import replace
import adapter_registry
ALIYUN_BASE = Path('/workspace/models/Qwen3Guard-Gen-0.6B')
for name in list(adapter_registry.ADAPTERS.keys()):
    old = adapter_registry.ADAPTERS[name]
    adapter_registry.ADAPTERS[name] = replace(old, base_model=ALIYUN_BASE)
for name, aliyun_dir in [('perf','/workspace/qwen3guard-perf/adapter'),
                          ('perf_conf','/workspace/qwen3guard-perf-conf/adapter')]:
    old = adapter_registry.ADAPTERS[name]
    adapter_registry.ADAPTERS[name] = replace(old, adapter_path=Path(aliyun_dir))

from classify_perf import classify_perf
from adapter_registry import get_adapter
adapter = get_adapter('perf_conf')

# 3 个真实样本
samples = [
    {'ts':'2026-09-23T03:26:05Z','cpu':{'pct':5.0,'count':6,'freq_mhz':3696},
     'memory':{'used_pct':15,'used_gb':5,'total_gb':32,'available_gb':27},
     'net':{'n_total':9,'n_up':6,'nics':[
        {'nic':'VMware VMnet1','isup':True},{'nic':'win-vps','isup':True},
        {'nic':'vEthernet (WSL)','isup':True},{'nic':'以太网 4','isup':False},
        {'nic':'以太网 2','isup':False},{'nic':'以太网','isup':False},
     ]},
     'system':{'uptime_s':18,'user':'Administrator'},
     'crash':{'bugcheck_count':5,'kp41_count':5}},
    {'ts':'2026-09-23T12:00:00Z','cpu':{'pct':30,'count':6,'freq_mhz':3696},
     'memory':{'used_pct':40,'used_gb':13,'total_gb':32,'available_gb':19},
     'net':{'n_total':9,'n_up':6,'nics':[]},
     'system':{'uptime_s':3000,'user':'Administrator'},
     'crash':{'bugcheck_count':0,'kp41_count':0}},
    {'ts':'2026-09-23T14:00:00Z','cpu':{'pct':85,'count':6,'freq_mhz':3696},
     'memory':{'used_pct':70,'used_gb':22,'total_gb':32,'available_gb':10},
     'net':{'n_total':9,'n_up':6,'nics':[]},
     'system':{'uptime_s':3500,'user':'Administrator'},
     'crash':{'bugcheck_count':1,'kp41_count':1}},
]
for s in samples:
    out = classify_perf(adapter, s)
    print(s['ts'], '→', out['risk'], out['action'], 'conf=', out['action_conf'], 'latency=', out['latency_ms'],'ms')
    print('  raw:', repr(out['raw'][:80]))
" 2>&1 | tail -30
"""
si, so, se = c.exec_command(CMD, timeout=180)
print(so.read().decode(errors="replace"))
c.close()