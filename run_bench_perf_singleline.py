#!/usr/bin/env python3
"""run_bench_perf_singleline.py — 用单行紧凑格式跑 bench,验证修复 hypothesis"""
import paramiko
HOST = '43.106.53.242'
USER = 'root'
PASS = 'PrisirTrain2026!'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=15, banner_timeout=15, auth_timeout=15)

# 在 aliyun 上跑:monkey-patch classify_perf._build_text 为单行格式,
# 然后跑 bench
CMD = r"""cd /workspace && python3 -c "
import sys, re, json
sys.path.insert(0, '/workspace/companion')

# --- 临时 monkey-patch:把 classify_perf._build_text 改成单行紧凑格式 ---
import classify_perf as cp

def _build_singleline(sample):
    cpu = sample.get('cpu', {})
    mem = sample.get('memory', {})
    net = sample.get('net', {})
    crash = sample.get('crash', {})
    sys_ = sample.get('system', {})
    parts = []
    # CPU
    parts.append(f'cpu {cpu.get(\"pct\",0):.0f}%')
    # 内存
    parts.append(f'内存 {mem.get(\"used_pct\",0):.0f}% / standby list {mem.get(\"available_gb\",0)}GB')
    # NIC - 检测 TAP disconnected
    disconnected_taps = []
    for nic in net.get('nics', []):
        if nic.get('isup') is False:
            name = (nic.get('nic') or '').lower()
            if any(t in name for t in ('tap0901','tapprotonvpn','vktap','tap','tun')):
                disconnected_taps.append(name)
    n_down = net.get('n_total',0) - net.get('n_up',0)
    parts.append(f'{len(disconnected_taps)} 个 TAP disconnected, {net.get(\"n_up\",0)}/{net.get(\"n_total\",0)} NIC up')
    # boot
    if sys_.get('uptime_s',0) < 60:
        parts.append(f'boot 后 {sys_.get(\"uptime_s\",0)}s')
    # crash
    if crash.get('bugcheck_count',0) > 0:
        parts.append(f'BugCheck fingerprint: {crash.get(\"bugcheck_count\",0)} 次 蓝屏')
    # 时间戳 - 凌晨
    ts = sample.get('ts','')
    m = re.search(r'T(\d{2}):', ts)
    if m and int(m.group(1)) in (0,1,2,3,4,5):
        parts.append(f'时间戳 {m.group(1)}:XX UTC')
    hint = ', '.join(parts)
    return f'性能采样: {hint}\n问: 这个性能快照的风险等级和推荐处理动作?'

cp._build_text = _build_singleline

# 重写 classify_perf 让它调用 patched _build_text
def classify_perf_patched(adapter, sample):
    import time
    text = cp._build_text(sample)
    t0 = time.time()
    res = adapter.classify(text)
    dt_ms = int((time.time() - t0) * 1000)
    raw = res['raw']
    risk, jb, action, rc, jc, ac = cp._parse_output(raw)
    if risk not in cp.VALID_RISKS:
        risk = 'medium'
    if action not in cp.VALID_ACTIONS:
        action = {'safe':'keep','low':'keep'}.get(risk, 'review')
    return {'risk': risk, 'action': action, 'jailbreak': 'yes' if jb else 'no',
            'risk_conf': rc, 'action_conf': ac, 'raw': raw,
            'latency_ms': dt_ms, 'parse_fail': rc is None}

cp.classify_perf = classify_perf_patched

# --- 注册 + bench ---
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

import bench_perf_local
from bench_perf_local import TEST_CASES, run_bench
from adapter_registry import get_adapter
adapter = get_adapter('perf_conf')

# 打印前 2 个 sample 看 _build_text 形态
for i, case in enumerate(TEST_CASES[:2] + TEST_CASES[40:42]):
    txt = cp._build_text(case['sample'])
    print(f'--- case[{i}] truth={case[\"risk\"]} ---')
    print(txt[:200])

report = run_bench(adapter, TEST_CASES)
Path('/workspace/bench_perf_singleline.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('ACC', report['accuracy'], 'PF', report['parse_fail_rate'], 'P50', report['latency_ms']['p50'])
for cls, m in report['per_class'].items():
    print(' ', cls, f'{m[\"correct\"]}/{m[\"total\"]}', f'{m[\"accuracy\"]*100:.0f}%')
" 2>&1 | tail -40
"""
si, so, se = c.exec_command(CMD, timeout=900)
print(so.read().decode(errors="replace"))
c.close()