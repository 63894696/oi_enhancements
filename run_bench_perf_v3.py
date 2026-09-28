#!/usr/bin/env python3
"""run_bench_perf_v3.py — 严重度优先:critical/high 在前,最后是 boot/profile 状态;
去掉了一切正常的混淆"""
import paramiko
HOST = '43.106.53.242'
USER = 'root'
PASS = 'PrisirTrain2026!'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=15, banner_timeout=15, auth_timeout=15)

CMD = r"""cd /workspace && python3 -c "
import sys, re, json
sys.path.insert(0, '/workspace/companion')
import classify_perf as cp

def _detect_taps(net):
    out = []
    for nic in net.get('nics', []):
        if nic.get('isup') is False:
            name = (nic.get('nic') or '').lower()
            if any(t in name for t in ('tap0901','tapprotonvpn','vktap','tap','tun')):
                out.append(name)
    return out

def _build_singleline_v3(sample):
    cpu = sample.get('cpu', {})
    mem = sample.get('memory', {})
    net = sample.get('net', {})
    crash = sample.get('crash', {})
    sys_ = sample.get('system', {})
    boot_s = sys_.get('uptime_s', 0)
    disconnected_taps = _detect_taps(net)
    avail = mem.get('available_gb', 0)
    used_pct = mem.get('used_pct', 0)
    cpu_pct = cpu.get('pct', 0)
    bugcheck = crash.get('bugcheck_count', 0)
    kp41 = crash.get('kp41_count', 0)
    # 严格 critical:boot<60 + bugcheck>=2
    is_critical = (boot_s < 60 and bugcheck >= 2) or (bugcheck >= 5)
    is_high = (boot_s < 60) or (bugcheck >= 1) or (len(disconnected_taps) >= 3 and cpu_pct >= 80)
    parts = []
    # 严重度放第一(决定模型先看到什么)
    if is_critical:
        parts.append(f'boot 后 {boot_s}s, BugCheck fingerprint: 蓝屏 {bugcheck} 次, Kernel-Power 41 {kp41} 次, NIC compliance 夜间')
    elif is_high:
        if boot_s < 60:
            parts.append(f'boot 后 {boot_s}s, 启动 {boot_s} 秒内出现异常')
        elif bugcheck >= 1:
            parts.append(f'BugCheck fingerprint: {bugcheck} 次 蓝屏')
        elif len(disconnected_taps) >= 3 and cpu_pct >= 80:
            parts.append(f'{len(disconnected_taps)} 个 TAP miniport disconnected, cpu {cpu_pct:.0f}% 持续高负载')
    else:
        # low/medium/safe:Profile 状态
        if disconnected_taps:
            parts.append(f'{len(disconnected_taps)} 个 TAP disconnected, NIC compliance')
        else:
            parts.append('0 个 TAP disconnected, ISLC 未启用')
        # CPU
        if cpu_pct >= 85:
            parts.append(f'cpu {cpu_pct:.0f}% 持续 1 分钟, 内存空闲 {avail}GB')
        elif cpu_pct >= 60:
            parts.append(f'cpu {cpu_pct:.0f}% 持续 -1 分钟, 内存空闲 {avail}GB')
        elif cpu_pct >= 30:
            parts.append(f'cpu {cpu_pct:.0f}% 中等负载, 内存空闲 {avail}GB')
        else:
            parts.append(f'cpu {cpu_pct:.0f}%, 内存空闲 {avail}GB, ProBalance 解禁')
        # 内存 + 状态
        if avail >= 20 and used_pct <= 50:
            parts.append(f'内存 {used_pct:.0f}%, 一切正常')
        elif used_pct >= 80:
            parts.append(f'内存 {used_pct:.0f}%, 仅剩 {avail}GB')
    return f'性能采样: {\", \".join(parts)}\n问: 这个性能快照的风险等级和推荐处理动作?'

cp._build_text = _build_singleline_v3

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

# 看 5 个边界 case
for i, idx in enumerate([0, 9, 19, 29, 39, 44, 49]):
    case = TEST_CASES[idx]
    txt = cp._build_text(case['sample'])
    print(f'--- case[{idx}] truth={case[\"risk\"]} ---')
    print(txt)
    print()

report = run_bench(adapter, TEST_CASES)
Path('/workspace/bench_perf_v3.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('ACC', report['accuracy'], 'PF', report['parse_fail_rate'], 'P50', report['latency_ms']['p50'])
for cls, m in report['per_class'].items():
    print(' ', cls, f'{m[\"correct\"]}/{m[\"total\"]}', f'{m[\"accuracy\"]*100:.0f}%')
" 2>&1 | tail -60
"""
si, so, se = c.exec_command(CMD, timeout=900)
print(so.read().decode(errors="replace"))
c.close()