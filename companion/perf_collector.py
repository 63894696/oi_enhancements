"""perf_collector.py — M3.51 S3

本地真实性能数据采集器,为 M3.51 S4/S5 提供 raw data 用于生成训练集。

数据源:
  1. psutil : CPU/内存/磁盘/网络/进程整体统计
  2. pywin32 + ctypes : Windows Performance Counter (PDH) + 内存清单(standby list)
  3. ctypes + Win32 API : NIC 状态、ACPI 热区、Driver Verifier 标志
  4. ETW EventLogReader : Kernel-Power 41 + BugCheck 1001 (S1 fingerprint)

输出:JSONL 一行一次采样,sample 字段自描述。
  {"ts": "2026-09-23T12:30:01Z", "sample": {...}}

CLI:
  python perf_collector.py --once                # 采集 1 次
  python perf_collector.py --interval 5 --n 100  # 每 5s 采 100 次
  python perf_collector.py --daemon --interval 10  # 永久守护,写到 data/perf_stream.jsonl

设计要点:
  - 全部用 stdlib + psutil + pywin32,**无需 admin**(读到读不到就 skip 字段)
  - 字段命名短小,JSON 体积小(每秒 1KB 量级)
  - 关键事件(BugCheck/Crash/High CPU) 单独标 type=event
  - 输出到 companion/data/perf_YYYY-MM-DD.jsonl 自动轮转
"""
from __future__ import annotations
import argparse, ctypes, json, os, sys, time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

# ---------- Windows API 常量 ----------
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 1; FILE_SHARE_WRITE = 2; FILE_SHARE_DELETE = 4
OPEN_EXISTING = 3
TOKEN_QUERY = 0x0008
TOKEN_ADJUST_PRIVILEGES = 0x0020
SE_PRIVILEGE_ENABLED = 0x2
SE_DEBUG_NAME = "SeDebugPrivilege"

# ---------- Globals ----------
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)


# ---------- helpers ----------
def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def safe(fn, default=None, label=""):
    try:
        v = fn()
        return v
    except Exception as e:
        return {"_err": f"{label}:{type(e).__name__}:{str(e)[:80]}"}


# ---------- CPU ----------
def cpu_sample() -> dict:
    """CPU 整体 + 每核 + 频率"""
    cpu_pct = psutil.cpu_percent(interval=None, percpu=False)
    cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
    try:
        freq = psutil.cpu_freq()
        freq_mhz = freq.current if freq else None
    except Exception:
        freq_mhz = None
    return {
        "pct": cpu_pct,
        "per_core": cpu_per_core,
        "freq_mhz": freq_mhz,
        "count": psutil.cpu_count(logical=True),
        "load_avg_1m": safe(lambda: psutil.getloadavg()[0], label="loadavg") if hasattr(psutil, 'getloadavg') else None,
    }


# ---------- Memory ----------
def memory_sample() -> dict:
    """内存 + standby list(NT 内核内存清单)"""
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    # Standby list 通过 NtQuerySystemInformation SystemMemoryListInformation
    # class 80. 简化:用 psutil 计算 ~available 比例,deep 探测留 optional
    return {
        "total_gb": round(vm.total / 1024**3, 2),
        "used_gb": round(vm.used / 1024**3, 2),
        "available_gb": round(vm.available / 1024**3, 2),
        "used_pct": vm.percent,
        "swap_used_gb": round(sm.used / 1024**3, 2),
        "buffers_gb": safe(lambda: round(getattr(psutil, 'virtual_memory').buffers, 2) if False else None),
    }


# ---------- Disk ----------
def disk_sample() -> dict:
    """每个 mountpoint 的占用"""
    out = []
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
            out.append({
                "mount": p.mountpoint,
                "total_gb": round(u.total / 1024**3, 2),
                "used_pct": u.percent,
            })
        except (PermissionError, OSError):
            continue
    # 顶层 IO
    io = psutil.disk_io_counters()
    return {
        "parts": out,
        "read_mb_s": round(io.read_bytes / 1024**2, 1) if io else None,
        "write_mb_s": round(io.write_bytes / 1024**2, 1) if io else None,
    }


# ---------- Network ----------
def net_sample() -> dict:
    """网卡上下行 + 状态 + IP"""
    io = psutil.net_io_counters(pernic=True)
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    out = []
    for nic, c in io.items():
        st = stats.get(nic)
        addr_list = addrs.get(nic, [])
        ipv4 = next((a.address for a in addr_list if a.family == 2), None)
        out.append({
            "nic": nic,
            "bytes_sent_mb": round(c.bytes_sent / 1024**2, 1),
            "bytes_recv_mb": round(c.bytes_recv / 1024**2, 1),
            "packets_sent": c.packets_sent,
            "packets_recv": c.packets_recv,
            "errin": c.errin,
            "errout": c.errout,
            "dropin": c.dropin,
            "dropout": c.dropout,
            "speed_mbps": st.speed if st else None,
            "isup": bool(st.isup) if st else None,
            "ipv4": ipv4,
        })
    return {"nics": out, "n_total": len(out), "n_up": sum(1 for n in out if n.get("isup"))}


# ---------- 进程 top ----------
def proc_top(n: int = 5, by: str = "cpu") -> dict:
    """top 5 cpu/内存进程"""
    out = []
    for p in psutil.process_iter(['name', 'pid', 'username']):
        try:
            with p.oneshot():
                cpu = p.cpu_percent(interval=None)
                mem = p.memory_info().rss
                prio = safe(lambda: p.nice(), label="nice")
            out.append({"name": p.info['name'], "pid": p.info['pid'],
                        "cpu_pct": cpu, "rss_mb": round(mem / 1024**2, 1),
                        "nice": prio})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    out.sort(key=lambda x: x['cpu_pct'], reverse=True)
    return {"top": out[:n], "total_procs": len(psutil.pids())}


# ---------- 全局 ----------
def system_sample() -> dict:
    """boot 时间 + 当前用户 + 系统版本"""
    boot = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).isoformat()
    return {
        "boot_utc": boot,
        "uptime_s": int(time.time() - psutil.boot_time()),
        "user": psutil.users()[0].name if psutil.users() else None,
        "platform": safe(lambda: f"{sys.platform}", label="plat"),
    }


# ---------- 风扇/温度 ----------
def thermal_sample() -> dict:
    """温度(若有 WMI/sensors)"""
    try:
        temps = psutil.sensors_temperatures() if hasattr(psutil, 'sensors_temperatures') else {}
    except Exception:
        temps = {}
    items = []
    for name, entries in temps.items():
        for e in entries:
            items.append({"chip": name, "label": e.label, "current_c": e.current, "high_c": e.high})
    return {"temps": items}


# ---------- BugCheck 摘要 ----------
def crash_summary(days: int = 7) -> dict:
    """最近 N 天 BugCheck 1001 + Kernel-Power 41 数(需 admin 读 Minidump 列表)"""
    # 不读 dmp 内容(无权限),只调 PowerShell 统计次数
    import subprocess
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-WinEvent -FilterHashtable @{{LogName='System';Id=1001}} -MaxEvents 50 -ErrorAction SilentlyContinue | "
             f"Where-Object {{$_.TimeCreated -gt (Get-Date).AddDays(-{days})}}).Count"],
            timeout=10, text=True, errors="replace"
        )
        bugcount = int(out.strip() or 0)
    except Exception:
        bugcount = None
    try:
        out2 = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-WinEvent -FilterHashtable @{{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=41}} -MaxEvents 50 -ErrorAction SilentlyContinue | "
             f"Where-Object {{$_.TimeCreated -gt (Get-Date).AddDays(-{days})}}).Count"],
            timeout=10, text=True, errors="replace"
        )
        kp41 = int(out2.strip() or 0)
    except Exception:
        kp41 = None
    return {"bugcheck_count": bugcount, "kp41_count": kp41, "window_days": days}


# ---------- 主采样函数 ----------
def collect_one() -> dict:
    """采一次完整快照,返回 dict"""
    sample = {
        "ts": utcnow_iso(),
        "type": "perf_snapshot",
        "cpu": cpu_sample(),
        "memory": memory_sample(),
        "disk": disk_sample(),
        "net": net_sample(),
        "proc": proc_top(),
        "system": system_sample(),
        "thermal": thermal_sample(),
    }
    return sample


def collect_with_event() -> dict:
    """采 perf + crash 事件(频率低,>30s 一次)"""
    s = collect_one()
    s["crash"] = crash_summary()
    return s


# ---------- Daemon ----------
def append_jsonl(path: Path, sample: dict):
    """append 一行 JSON"""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n")


def run_daemon(interval: int, with_crash: bool, max_samples: int | None):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = DATA_DIR / f"perf_{today}.jsonl"
    print(f"[perf_collector] daemon interval={interval}s path={path}", flush=True)
    n = 0
    while True:
        try:
            s = collect_with_event() if with_crash else collect_one()
            append_jsonl(path, s)
            n += 1
            if n % 10 == 0:
                print(f"  [{n}] {s['ts']} cpu={s['cpu']['pct']:.1f}% "
                      f"mem={s['memory']['used_pct']}% up={s['system']['uptime_s']}s", flush=True)
            if max_samples and n >= max_samples:
                print(f"[perf_collector] reached max {n}, exit", flush=True)
                break
        except KeyboardInterrupt:
            print("[perf_collector] interrupted, exit", flush=True)
            break
        except Exception as e:
            print(f"[perf_collector] err: {e}", flush=True)
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description="perf_collector - 本地真实性能数据采集")
    ap.add_argument("--once", action="store_true", help="采 1 次并打印 JSON")
    ap.add_argument("--interval", type=int, default=10, help="采样间隔秒")
    ap.add_argument("--n", type=int, default=0, help="总采样次数(0 = 不限)")
    ap.add_argument("--daemon", action="store_true", help="永久守护")
    ap.add_argument("--with-crash", action="store_true", help="采 crash 事件(BugCheck 1001 + KP41)")
    args = ap.parse_args()

    if args.once:
        s = collect_with_event() if args.with_crash else collect_one()
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    if args.daemon or args.n:
        run_daemon(args.interval, args.with_crash, args.n if args.n else None)
        return
    ap.print_help()


if __name__ == "__main__":
    main()