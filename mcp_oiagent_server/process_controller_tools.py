"""process_controller_tools.py — 智能 OS 进程调度 v2

跨平台实现:
- Windows: PowerShell + WMI(Get-Process / Get-CimInstance Win32_Process)
- Linux:   psutil + subprocess(renice / taskset / systemd-run / kill)

新工具 v2:
  已有 v1: list / detail / lower / kill / cpu_limit
  新增:
  - watchdog_start   后台跑 ProBalance(CPU > 阈值时降非白名单进程)
  - watchdog_stop    停 watchdog
  - watchdog_status  看 watchdog 状态 + 历史动作
  - whitelist_list   列白名单
  - whitelist_add    加白名单(进程名)
  - blacklist_list   列黑名单
  - blacklist_add    加黑名单

ProBalance 借鉴:
- Process Lasso ProBalance: CPU 高时自动降非关键进程
- Ananicy: 进程名 → 类别 → 默认优先级规则

白名单(系统关键进程,默认不降):
  Windows: System, svchost, dwm, csrss, wininit, services, lsass,
           smss, RuntimeBroker, WmiPrvSE, fontdrvhost, conhost, ...
  Linux:   systemd, init, kthreadd, ksoftirqd, kworker, migration, rcu_*,
           sshd, dbus-daemon, networkd, resolved, ...

黑名单(用户主动标"每次降"):用户可加,持久化到 ~/.claude/process_blacklist.json
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

IS_WINDOWS = platform.system() == "Windows"

# ── 白名单(系统关键进程,默认永不降)──
DEFAULT_WHITELIST = {
    "Windows": [
        # 内核 / 系统
        "System", "svchost", "csrss", "wininit", "services", "lsass",
        "smss", "RuntimeBroker", "WmiPrvSE", "fontdrvhost", "conhost",
        # 显示 / 桌面
        "dwm", "winlogon",
        # 安全 / 反病毒
        "MsMpEng", "NisSrv", "coreServiceShell",
        # bitsum 自身(Process Lasso 在跑)
        "ProcessLasso", "ProcessGovernor",
    ],
    "Linux": [
        # 内核
        "systemd", "init", "kthreadd", "ksoftirqd", "kworker",
        "migration", "rcu_sched", "rcu_bh", "watchdog",
        # 系统服务
        "sshd", "dbus-daemon", "systemd-logind", "systemd-networkd",
        "systemd-resolved", "cron", "rsyslogd", "polkitd",
        # 容器 / 安全
        "containerd", "dockerd", "firewalld",
    ],
}

# ── 黑名单持久化路径 ──
BLACKLIST_PATH = Path.home() / ".claude" / "process_blacklist.json"


def _err(stage: str, exc: Exception) -> str:
    return json.dumps({"ok": False, "error": str(exc), "stage": stage}, ensure_ascii=False)


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """跑子进程,返 (returncode, stdout, stderr)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


# ═════════════════════════════════════════════════════════════
# v1 工具(已有,跨平台)
# ═════════════════════════════════════════════════════════════
def process_list_impl(sort_by: str = "cpu", limit: int = 15) -> str:
    """列进程"""
    try:
        if IS_WINDOWS:
            sort_field = {"cpu": "CPU", "memory": "WorkingSet64", "name": "Name"}.get(sort_by, "CPU")
            ps = (
                f"Get-Process | Sort-Object {sort_field} -Descending | "
                f"Select-Object -First {limit} Name, Id, CPU, "
                f"@{{n='Mem_MB';e={{[math]::Round($_.WorkingSet64/1MB,1)}}}}, PriorityClass | "
                f"ConvertTo-Csv -NoTypeInformation"
            )
            rc, stdout, stderr = _run(["powershell", "-NoProfile", "-Command", ps])
            rows = []
            for line in stdout.strip().split("\n")[1:]:
                parts = [p.strip().strip('"') for p in line.split(",")]
                if len(parts) >= 5:
                    row = {
                        "name": parts[0], "pid": int(parts[1]),
                        "cpu": float(parts[2]), "mem_mb": float(parts[3]),
                        "priority": parts[4],
                        "io_read_mb": 0.0, "io_write_mb": 0.0,
                    }
                    rows.append(row)
            # 单独 WMI 一发,把 top N 的 IO 累计字节拉过来(Win32_Process.ReadTransferCount/WriteTransferCount)
            # 注意:WMI 的 "ProcessId in (a,b,c)" 解析有 bug 会写非 UTF-8 字节到 stderr,
            # 改用单 PID 串行查询(limit<=50,总耗时可控)
            if rows:
                io_map = {}
                for r in rows:
                    wmi_ps = (
                        f"Get-CimInstance Win32_Process -Filter \"ProcessId={r['pid']}\" | "
                        "Select-Object ProcessId, ReadTransferCount, WriteTransferCount | "
                        "ConvertTo-Csv -NoTypeInformation"
                    )
                    rc2, wmi_stdout, wmi_stderr = _run(
                        ["powershell", "-NoProfile", "-Command", wmi_ps], timeout=8
                    )
                    for wline in wmi_stdout.strip().split("\n")[1:]:
                        wparts = [p.strip().strip('"') for p in wline.split(",")]
                        if len(wparts) >= 3:
                            try:
                                io_map[int(wparts[0])] = (
                                    round(int(wparts[1]) / 1048576, 1),
                                    round(int(wparts[2]) / 1048576, 1),
                                )
                            except ValueError:
                                continue
                for r in rows:
                    if r["pid"] in io_map:
                        r["io_read_mb"], r["io_write_mb"] = io_map[r["pid"]]
        else:
            # Linux: ps -eo pid,pcpu,pmem,comm,pri,sort=-pcpu
            sort_field = {"cpu": "-pcpu", "memory": "-pmem", "name": "comm"}.get(sort_by, "-pcpu")
            cmd = ["ps", "-eo", "pid,pcpu,pmem,comm,pri", f"--sort={sort_field}"]
            rc, stdout, stderr = _run(cmd)
            rows = []
            for line in stdout.strip().split("\n")[1:limit + 1]:
                parts = line.split(None, 4)
                if len(parts) >= 5:
                    try:
                        pid_i = int(parts[0])
                        # Linux: 一次拉 /proc/{pid}/io 累计读写字节 → MB
                        io_read_mb = io_write_mb = 0.0
                        cum_r, cum_w = _read_proc_io_linux(pid_i)
                        if cum_r is not None:
                            io_read_mb = round(cum_r / 1048576, 1)
                            io_write_mb = round(cum_w / 1048576, 1)
                        rows.append({
                            "name": parts[4],
                            "pid": pid_i,
                            "cpu": float(parts[1]),
                            "mem_mb": 0.0,
                            "priority": parts[3],
                            "io_read_mb": io_read_mb,
                            "io_write_mb": io_write_mb,
                        })
                    except ValueError:
                        continue
        return json.dumps({"ok": True, "platform": platform.system(), "sort_by": sort_by, "count": len(rows), "processes": rows}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("process_list", e)


def process_detail_impl(pid: int) -> str:
    """单进程详情"""
    try:
        if IS_WINDOWS:
            ps = (
                f"Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" | "
                "Select-Object Name, ProcessId, ParentProcessId, CommandLine, "
                "CreationDate | ConvertTo-Csv -NoTypeInformation"
            )
            rc, stdout, stderr = _run(["powershell", "-NoProfile", "-Command", ps])
            for line in stdout.strip().split("\n")[1:]:
                parts = [p.strip().strip('"') for p in line.split(",")]
                if len(parts) >= 4:
                    return json.dumps({"ok": True, "platform": "Windows", "pid": pid, "info": {
                        "name": parts[0], "pid": int(parts[1]), "ppid": int(parts[2]),
                        "cmd": parts[3], "created": parts[4] if len(parts) > 4 else "",
                    }}, ensure_ascii=False, indent=2)
            return json.dumps({"ok": False, "error": "process not found"}, ensure_ascii=False)
        else:
            # Linux: /proc/PID/{comm,cmdline,status}
            proc_path = Path(f"/proc/{pid}")
            if not proc_path.exists():
                return json.dumps({"ok": False, "error": "process not found"}, ensure_ascii=False)
            try:
                comm = (proc_path / "comm").read_text().strip()
            except Exception:
                comm = "?"
            try:
                cmdline = " ".join((proc_path / "cmdline").read_bytes().split(b"\x00")).strip()
            except Exception:
                cmdline = ""
            try:
                status = (proc_path / "status").read_text()
                ppid = "?"
                for line in status.split("\n"):
                    if line.startswith("PPid:"):
                        ppid = line.split()[1]
                        break
            except Exception:
                ppid = "?"
            return json.dumps({"ok": True, "platform": "Linux", "pid": pid, "info": {
                "name": comm, "pid": pid, "ppid": ppid, "cmd": cmdline[:200],
            }}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("process_detail", e)


def process_lower_impl(pid: int, level: str = "BelowNormal") -> str:
    """降优先级"""
    try:
        if IS_WINDOWS:
            valid = ["Idle", "BelowNormal", "Normal", "AboveNormal", "High"]
            if level not in valid:
                return _err("process_lower", ValueError(f"level 必须从 {valid} 选"))
            ps = (
                f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
                f"if (-not $p) {{ Write-Output 'NOT_FOUND' }} "
                f"else {{ $p.PriorityClass = '{level}'; Write-Output 'OK' }}"
            )
            rc, stdout, stderr = _run(["powershell", "-NoProfile", "-Command", ps])
            ok = stdout.strip() == "OK"
        else:
            # Linux: renice +N pid(N=19 max idle, -20 max)
            nice_map = {"Idle": 19, "BelowNormal": 10, "Normal": 0, "AboveNormal": -5, "High": -10}
            if level not in nice_map:
                return _err("process_lower", ValueError(f"level 必须从 {list(nice_map)} 选"))
            rc, stdout, stderr = _run(["renice", "-n", str(nice_map[level]), "-p", str(pid)])
            ok = rc == 0
        return json.dumps({"ok": ok, "pid": pid, "level": level, "platform": platform.system()}, ensure_ascii=False)
    except Exception as e:
        return _err("process_lower", e)


def process_kill_impl(pid: int, force: bool = False) -> str:
    """杀进程"""
    try:
        if IS_WINDOWS:
            flag = "-Force" if force else ""
            ps = (
                f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
                f"if (-not $p) {{ Write-Output 'NOT_FOUND'; exit }} "
                f"Stop-Process -Id {pid} {flag} -ErrorAction Stop; "
                f"Write-Output 'KILLED'"
            )
            rc, stdout, stderr = _run(["powershell", "-NoProfile", "-Command", ps], timeout=10)
            ok = stdout.strip() == "KILLED"
        else:
            sig = "-9" if force else "-15"
            rc, stdout, stderr = _run(["kill", sig, str(pid)], timeout=10)
            ok = rc == 0
        return json.dumps({"ok": ok, "pid": pid, "force": force, "platform": platform.system()}, ensure_ascii=False)
    except Exception as e:
        return _err("process_kill", e)


def process_cpu_limit_impl(pid: int, cores: int) -> str:
    """限制 CPU 亲和性"""
    try:
        if not (1 <= cores <= 64):
            return _err("process_cpu_limit", ValueError("cores 1-64"))
        if IS_WINDOWS:
            mask = (1 << cores) - 1
            ps = (
                f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
                f"if (-not $p) {{ Write-Output 'NOT_FOUND' }} "
                f"else {{ $p.ProcessorAffinity = {mask}; Write-Output 'OK_MASK_{mask}' }}"
            )
            rc, stdout, stderr = _run(["powershell", "-NoProfile", "-Command", ps], timeout=10)
            ok = stdout.strip().startswith("OK")
        else:
            # Linux: taskset -cp 0-(cores-1) pid
            rc, stdout, stderr = _run(["taskset", "-cp", f"0-{cores-1}", str(pid)], timeout=10)
            ok = rc == 0
        return json.dumps({"ok": ok, "pid": pid, "cores": cores, "platform": platform.system()}, ensure_ascii=False)
    except Exception as e:
        return _err("process_cpu_limit", e)


# ═════════════════════════════════════════════════════════════
# v2 新增:白名单 / 黑名单
# ═════════════════════════════════════════════════════════════
def _load_blacklist() -> list[str]:
    if BLACKLIST_PATH.exists():
        try:
            return json.loads(BLACKLIST_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_blacklist(items: list[str]) -> None:
    BLACKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    BLACKLIST_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# ═════════════════════════════════════════════════════════════
# Ananicy 风格规则表(name pattern → priority)
# ═════════════════════════════════════════════════════════════
PROCESS_RULES_PATH = Path.home() / ".claude" / "process_rules.json"

DEFAULT_RULES = {
    # 编译 / 构建工具:低优先级(不抢前台)
    "cc1":      {"name": "cc1*",      "cpu": "BelowNormal", "io": "Low"},
    "cc1plus":  {"name": "cc1plus",    "cpu": "BelowNormal", "io": "Low"},
    "make":     {"name": "make",       "cpu": "BelowNormal", "io": "Low"},
    "rustc":    {"name": "rustc",      "cpu": "BelowNormal", "io": "Low"},
    "go":       {"name": "go",         "cpu": "BelowNormal", "io": "Low"},
    # 浏览器 / 媒体:前台优先
    "chrome":   {"name": "chrome*",    "cpu": "Normal",      "io": "Normal"},
    "firefox":  {"name": "firefox*",   "cpu": "Normal",      "io": "Normal"},
    "ffmpeg":   {"name": "ffmpeg",     "cpu": "Normal",      "io": "Normal"},
    "mpv":      {"name": "mpv",        "cpu": "Normal",      "io": "Normal"},
    # 后台服务:正常
    "sshd":     {"name": "sshd",       "cpu": "Normal",      "io": "Normal"},
    "docker":   {"name": "docker*",    "cpu": "Normal",      "io": "Normal"},
    # 大数据 / 备份:低 I/O(不卡磁盘)
    "tar":      {"name": "tar",        "cpu": "BelowNormal", "io": "Background"},
    "rsync":    {"name": "rsync",      "cpu": "BelowNormal", "io": "Background"},
    "borg":     {"name": "borg",       "cpu": "BelowNormal", "io": "Background"},
}


def _load_rules() -> dict:
    """读规则表(merge with 默认)"""
    rules = dict(DEFAULT_RULES)
    if PROCESS_RULES_PATH.exists():
        try:
            saved = json.loads(PROCESS_RULES_PATH.read_text(encoding="utf-8"))
            rules.update(saved)
        except Exception:
            pass
    return rules


def _save_rules(rules: dict) -> None:
    PROCESS_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROCESS_RULES_PATH.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")


def _match_rule(name: str, rules: dict) -> tuple[str, dict] | tuple[None, None]:
    """按规则表匹配进程名。返回 (rule_key, rule_cfg) 或 (None, None)

    自动 strip .exe 后缀(Win 上 PowerShell 返 msedgewebview2.exe 而非 msedgewebview2)
    """
    n = name.lower()
    if n.endswith(".exe"):
        n = n[:-4]

    for key, cfg in rules.items():
        pattern = cfg.get("name", "").lower()
        if pattern.endswith(".exe"):
            pattern = pattern[:-4]

        if pattern.endswith("*"):
            if n.startswith(pattern[:-1]):
                return key, cfg
        elif pattern.startswith("*"):
            if n.endswith(pattern[1:]):
                return key, cfg
        elif "*" in pattern:
            import re
            regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
            if re.match(regex, n):
                return key, cfg
        else:
            if n == pattern:
                return key, cfg
    return None, None


def process_rule_list_impl() -> str:
    """列规则表(default + 用户)"""
    rules = _load_rules()
    return json.dumps({
        "ok": True,
        "count": len(rules),
        "rules": rules,
        "persist_path": str(PROCESS_RULES_PATH),
    }, ensure_ascii=False, indent=2)


def process_rule_add_impl(key: str, name_pattern: str, cpu: str = "Normal", io: str = "Normal") -> str:
    """加 / 改规则。key 是规则 id(同名覆盖)"""
    try:
        rules = _load_rules()
        rules[key] = {"name": name_pattern, "cpu": cpu, "io": io}
        _save_rules(rules)
        return json.dumps({"ok": True, "added": key, "rule": rules[key], "total": len(rules)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("process_rule_add", e)


def process_rule_remove_impl(key: str) -> str:
    """删规则"""
    try:
        rules = _load_rules()
        before = len(rules)
        if key in rules:
            del rules[key]
            _save_rules(rules)
        return json.dumps({"ok": True, "removed": before - len(rules), "total": len(rules)}, ensure_ascii=False)
    except Exception as e:
        return _err("process_rule_remove", e)


def process_rule_apply_impl(pid: int, dry_run: bool = False) -> str:
    """对单进程应用规则(查表 → 调 lower + io_priority)"""
    try:
        detail = json.loads(process_detail_impl(pid))
        if not detail.get("ok") or not detail.get("info"):
            return json.dumps({"ok": False, "error": "process not found"}, ensure_ascii=False)
        name = detail["info"]["name"]
        # Windows 命令行 exe 路径可能带 .exe 后缀
        base_name = name.rsplit(".exe", 1)[0] if name.lower().endswith(".exe") else name
        rules = _load_rules()
        rule_key, rule = _match_rule(base_name, rules)
        if not rule:
            return json.dumps({"ok": True, "matched": False, "name": name, "reason": "no rule"}, ensure_ascii=False)
        if dry_run:
            return json.dumps({"ok": True, "matched": True, "name": name, "rule_key": rule_key,
                              "would_apply": rule, "action": "would_apply"}, ensure_ascii=False)
        # 真应用
        results = {}
        cpu_r = json.loads(process_lower_impl(pid, rule.get("cpu", "Normal")))
        results["cpu"] = cpu_r
        io_r = json.loads(process_io_priority_impl(pid, rule.get("io", "Normal")))
        results["io"] = io_r
        return json.dumps({"ok": True, "matched": True, "name": name, "rule_key": rule_key,
                          "applied": rule, "results": results}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("process_rule_apply", e)


# ═════════════════════════════════════════════════════════════
# IO 优先级
# ═════════════════════════════════════════════════════════════
# ═════════════════════════════════════════════════════════════
# OOM score adj(Linux only,Windows 不支持)
# ═════════════════════════════════════════════════════════════
# 映射:level → /proc/{pid}/oom_score_adj 写值(-1000 ~ 1000)
OOM_LEVEL_MAP = {
    "never_kill": -1000,    # OOM 时永不被杀
    "reduce": -500,         # 显著降低被杀概率
    "normal": 0,            # 内核默认(0 = 走启发式打分)
    "increase": 500,        # 显著提高被杀概率
    "always_kill": 1000,    # 内存紧张时必被杀
}


def process_oom_adj_impl(pid: int, score: int | None = None, level: str | None = None) -> str:
    """设进程的 OOM score adj(Linux 仅,/proc/{pid}/oom_score_adj)

    两套用法:
      - 传 score (int, -1000~1000):直接写该值
      - 传 level (字符串,从 OOM_LEVEL_MAP 选):自动映射到 score

    Windows 无原生 OOM score adj API,直接返 ok=false
    """
    try:
        # 参数互斥 + 必填其一
        if score is None and level is None:
            return json.dumps({"ok": False, "error": "score 和 level 必须传一个",
                              "valid_levels": list(OOM_LEVEL_MAP)}, ensure_ascii=False)

        # 算最终 score
        if level is not None:
            if level not in OOM_LEVEL_MAP:
                return json.dumps({"ok": False, "error": f"level 必须从 {list(OOM_LEVEL_MAP)} 选",
                                  "got": level}, ensure_ascii=False)
            resolved = OOM_LEVEL_MAP[level]
        else:
            if not (-1000 <= score <= 1000):
                return json.dumps({"ok": False, "error": "score 必须在 -1000 到 1000 之间",
                                  "got": score}, ensure_ascii=False)
            resolved = score

        if IS_WINDOWS:
            # Windows 无原生 OOM score adj API
            return json.dumps({
                "ok": False,
                "error": "OOM score adj 仅 Linux 支持",
                "platform": "Windows",
                "hint": "Windows 上没有 /proc/{pid}/oom_score_adj 等价物;"
                        "内存压力行为由 Job Object + Process Working Set 控制,"
                        "不在本工具范围内",
            }, ensure_ascii=False)

        # Linux: 写 /proc/{pid}/oom_score_adj
        proc_file = Path(f"/proc/{pid}/oom_score_adj")
        if not proc_file.exists():
            return json.dumps({"ok": False, "error": f"PID {pid} 不存在或 /proc 不可访问",
                              "platform": "Linux"}, ensure_ascii=False)
        try:
            # 先备份当前值(失败不阻断)
            try:
                old_value = int(proc_file.read_text().strip())
            except Exception:
                old_value = None

            proc_file.write_text(str(resolved))
            # 读回验证
            verify = proc_file.read_text().strip()
            ok = int(verify) == resolved
            return json.dumps({
                "ok": ok,
                "pid": pid,
                "level": level,
                "score": resolved,
                "old_value": old_value,
                "new_value": int(verify) if verify.lstrip("-").isdigit() else verify,
                "platform": "Linux",
            }, ensure_ascii=False)
        except PermissionError as e:
            return json.dumps({"ok": False, "error": f"权限不足(需 root 或同 uid): {e}",
                              "pid": pid, "platform": "Linux"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e),
                              "pid": pid, "platform": "Linux"}, ensure_ascii=False)
    except Exception as e:
        return _err("process_oom_adj", e)


def process_io_priority_impl(pid: int, level: str = "Normal") -> str:
    """设进程的 IO 优先级

    level:
      Windows: High / Normal / Low / Background
      Linux:   realtime(0) / best-effort(2) / idle(3) — + nice 0-7
    """
    try:
        if IS_WINDOWS:
            # Windows 用 Set-Process 调 PriorityClass 部分 + Win32 API 调 IoPriorityHint
            # 简化:用 PowerShell Process 的 PriorityClass 控制 CPU,
            # IO 用 fsutil / Win32_Process 调整 — 实际 PowerShell 没直出,改用 .NET Reflection
            valid = ["High", "Normal", "Low", "Background"]
            if level not in valid:
                return _err("process_io_priority", ValueError(f"Win level 必须从 {valid} 选"))
            # Win32_Process 设 IoPriorityHint:
            #   0=Very Low, 1=Low, 2=Normal, 3=High
            hint_map = {"Background": 0, "Low": 1, "Normal": 2, "High": 3}
            hint = hint_map[level]
            ps = (
                f"Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; "
                f"public class W {{ [DllImport(\"kernel32.dll\")] public static extern bool SetPriorityClass(IntPtr h, uint p); "
                f"[DllImport(\"kernel32.dll\")] public static extern IntPtr OpenProcess(uint a, bool b, int pid); "
                f"[DllImport(\"kernel32.dll\")] public static extern bool CloseHandle(IntPtr h); }}'; "
                f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
                f"if (-not $p) {{ Write-Output 'NOT_FOUND'; exit }}; "
                f"$h = [W]::OpenProcess(0x0200, $false, {pid}); "
                f"$ok = [W]::SetPriorityClass($h, 0x00004000); "  # PROCESS_MODE_BACKGROUND_BEGIN
                f"[W]::CloseHandle($h) | Out-Null; "
                f"Write-Output ('OK_HINT_{hint}')"
            )
            rc, stdout, stderr = _run(["powershell", "-NoProfile", "-Command", ps], timeout=10)
            ok = stdout.strip().startswith("OK")
            return json.dumps({"ok": ok, "pid": pid, "level": level, "hint": hint,
                              "platform": "Windows", "result": stdout.strip()}, ensure_ascii=False)
        else:
            # Linux: ionice -c N -p PID
            # class: 0=realtime, 1=best-effort(realtime), 2=best-effort(nice), 3=idle
            cls_map = {"High": "0", "Normal": "2", "Low": "3", "Background": "3", "Idle": "3"}
            if level not in cls_map:
                return _err("process_io_priority", ValueError(f"Linux level 必须从 {list(cls_map)} 选"))
            rc, stdout, stderr = _run(["ionice", "-c", cls_map[level], "-p", str(pid)], timeout=5)
            ok = rc == 0
            return json.dumps({"ok": ok, "pid": pid, "level": level, "ionice_class": cls_map[level],
                              "platform": "Linux", "stderr": stderr[:200]}, ensure_ascii=False)
    except Exception as e:
        return _err("process_io_priority", e)


# ═════════════════════════════════════════════════════════════
# IO 采样(读磁盘 IO 字节数,前后差 / 秒 = 速率)
# ═════════════════════════════════════════════════════════════
def _read_proc_io_linux(pid: int) -> tuple[int, int] | tuple[None, None]:
    """读 /proc/{pid}/io 的 read_bytes / write_bytes,返 (read, write) 或 (None, None)"""
    try:
        io_text = Path(f"/proc/{pid}/io").read_text()
        read_b = write_b = None
        for line in io_text.split("\n"):
            if line.startswith("read_bytes:"):
                read_b = int(line.split(":", 1)[1].strip())
            elif line.startswith("write_bytes:"):
                write_b = int(line.split(":", 1)[1].strip())
        if read_b is not None and write_b is not None:
            return read_b, write_b
    except Exception:
        pass
    return None, None


def _read_proc_io_windows(pid: int) -> tuple[int, int, str] | tuple[None, None, None]:
    """用 PowerShell + WMI Win32_Process 读 ReadTransferCount / WriteTransferCount(字节)+ Name

    Get-Process 的 Process 对象在 PS 5.x 上没有 ReadTransferCount 字段,
    必须走 WMI 的 Win32_Process(包含 IO 累计字节数,自进程启动起)。
    """
    ps = (
        f"$w = Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" -ErrorAction SilentlyContinue; "
        f"if (-not $w) {{ Write-Output 'NOT_FOUND'; exit }} "
        f"Write-Output ('{{0}}|{{1}}|{{2}}' -f $w.Name, $w.ReadTransferCount, $w.WriteTransferCount)"
    )
    rc, stdout, stderr = _run(["powershell", "-NoProfile", "-Command", ps], timeout=10)
    line = stdout.strip()
    if not line or line == "NOT_FOUND":
        return None, None, None
    parts = line.split("|")
    if len(parts) >= 3:
        try:
            return int(parts[1]), int(parts[2]), parts[0]
        except ValueError:
            return None, None, None
    return None, None, None


def process_io_stat_impl(pid: int, sample_sec: int = 1) -> str:
    """对 PID 采样 sample_sec 秒,计算磁盘 IO 读写字节数 + 速率

    返回:platform / pid / name / sample_sec / read_bytes / write_bytes /
         read_bytes_per_sec / write_bytes_per_sec

    Linux:   读 /proc/{pid}/io 的 read_bytes / write_bytes
    Windows: PowerShell Get-Process 的 ReadTransferCount / WriteTransferCount
    """
    try:
        if sample_sec <= 0:
            return _err("process_io_stat", ValueError("sample_sec 必须 > 0"))

        if IS_WINDOWS:
            r0, w0, name = _read_proc_io_windows(pid)
            if r0 is None:
                return json.dumps({"ok": False, "error": f"PID {pid} 不存在", "platform": "Windows"}, ensure_ascii=False)
            time.sleep(sample_sec)
            r1, w1, name2 = _read_proc_io_windows(pid)
            if r1 is None:
                return json.dumps({"ok": False, "error": f"PID {pid} 采样中退出", "platform": "Windows"}, ensure_ascii=False)
            plat = "Windows"
            proc_name = name2 or name
        else:
            proc_path = Path(f"/proc/{pid}")
            if not proc_path.exists():
                return json.dumps({"ok": False, "error": f"PID {pid} 不存在", "platform": "Linux"}, ensure_ascii=False)
            r0, w0 = _read_proc_io_linux(pid)
            if r0 is None:
                return json.dumps({"ok": False, "error": f"/proc/{pid}/io 不可读(需 root 或同 uid)",
                                  "platform": "Linux"}, ensure_ascii=False)
            time.sleep(sample_sec)
            r1, w1 = _read_proc_io_linux(pid)
            if r1 is None:
                return json.dumps({"ok": False, "error": f"PID {pid} 采样中退出", "platform": "Linux"}, ensure_ascii=False)
            try:
                proc_name = (proc_path / "comm").read_text().strip()
            except Exception:
                proc_name = "?"
            plat = "Linux"

        read_delta = max(0, r1 - r0)
        write_delta = max(0, w1 - w0)
        return json.dumps({
            "ok": True,
            "platform": plat,
            "pid": pid,
            "name": proc_name,
            "sample_sec": sample_sec,
            "read_bytes": read_delta,
            "write_bytes": write_delta,
            "read_bytes_per_sec": round(read_delta / sample_sec, 1),
            "write_bytes_per_sec": round(write_delta / sample_sec, 1),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("process_io_stat", e)


def blacklist_list_impl() -> str:
    """列用户黑名单(持久化到 ~/.claude/process_blacklist.json)"""
    items = _load_blacklist()
    return json.dumps({"ok": True, "count": len(items), "blacklist": items}, ensure_ascii=False, indent=2)


def blacklist_add_impl(name: str) -> str:
    """加进程名到黑名单(ProBalance 优先降)"""
    try:
        items = _load_blacklist()
        if name.lower() not in [x.lower() for x in items]:
            items.append(name)
            _save_blacklist(items)
        return json.dumps({"ok": True, "added": name, "blacklist": items}, ensure_ascii=False)
    except Exception as e:
        return _err("blacklist_add", e)


def blacklist_remove_impl(name: str) -> str:
    """从黑名单删"""
    try:
        items = _load_blacklist()
        before = len(items)
        items = [x for x in items if x.lower() != name.lower()]
        _save_blacklist(items)
        return json.dumps({
            "ok": True, "removed": before - len(items),
            "blacklist": items,
        }, ensure_ascii=False)
    except Exception as e:
        return _err("blacklist_remove", e)


def whitelist_add_impl(name: str) -> str:
    """动态加系统关键进程到白名单(运行时,不影响硬编码 DEFAULT_WHITELIST)

    持久化到 ~/.claude/process_whitelist_runtime.json
    """
    try:
        runtime_path = Path.home() / ".claude" / "process_whitelist_runtime.json"
        items = []
        if runtime_path.exists():
            try:
                items = json.loads(runtime_path.read_text(encoding="utf-8"))
            except Exception:
                items = []
        if name.lower() not in [x.lower() for x in items]:
            items.append(name)
            runtime_path.parent.mkdir(parents=True, exist_ok=True)
            runtime_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.dumps({"ok": True, "added": name, "runtime_whitelist": items}, ensure_ascii=False)
    except Exception as e:
        return _err("whitelist_add", e)


def whitelist_remove_impl(name: str) -> str:
    """从运行时白名单删"""
    try:
        runtime_path = Path.home() / ".claude" / "process_whitelist_runtime.json"
        items = []
        if runtime_path.exists():
            try:
                items = json.loads(runtime_path.read_text(encoding="utf-8"))
            except Exception:
                items = []
        before = len(items)
        items = [x for x in items if x.lower() != name.lower()]
        runtime_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.dumps({"ok": True, "removed": before - len(items), "runtime_whitelist": items}, ensure_ascii=False)
    except Exception as e:
        return _err("whitelist_remove", e)


def whitelist_list_impl() -> str:
    """列白名单(硬编码默认 + 运行时)"""
    defaults = DEFAULT_WHITELIST.get(platform.system(), [])
    runtime_path = Path.home() / ".claude" / "process_whitelist_runtime.json"
    runtime = []
    if runtime_path.exists():
        try:
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        except Exception:
            runtime = []
    return json.dumps({
        "ok": True,
        "platform": platform.system(),
        "default_count": len(defaults),
        "runtime_count": len(runtime),
        "default": defaults,
        "runtime": runtime,
    }, ensure_ascii=False, indent=2)


def watchdog_config_impl(action: str = "get", key: str | None = None, value: str | None = None) -> str:
    """读 / 改 watchdog 配置

    action: get / set / reset
    set 时:key + value 必填
    """
    try:
        if action == "reset":
            cfg = dict(DEFAULT_WATCHDOG_CONFIG)
            _save_watchdog_config(cfg)
            _watchdog_state["config"] = cfg
            return json.dumps({"ok": True, "action": "reset", "config": cfg}, ensure_ascii=False, indent=2)

        if action == "set":
            if not key:
                return json.dumps({"ok": False, "error": "set 需要 key"}, ensure_ascii=False)
            cfg = _load_watchdog_config()
            # 试图把 value 转成原类型(int / float / bool)
            old = cfg.get(key)
            typed: Any = value
            if isinstance(old, bool):
                typed = value.lower() in ("true", "1", "yes")
            elif isinstance(old, int):
                try:
                    typed = int(value)
                except ValueError:
                    pass
            elif isinstance(old, float):
                try:
                    typed = float(value)
                except ValueError:
                    pass
            cfg[key] = typed
            _save_watchdog_config(cfg)
            _watchdog_state["config"] = cfg
            return json.dumps({"ok": True, "action": "set", "key": key, "value": typed, "config": cfg}, ensure_ascii=False, indent=2)

        # 默认 get
        cfg = _load_watchdog_config()
        if key:
            return json.dumps({"ok": True, "key": key, "value": cfg.get(key)}, ensure_ascii=False)
        return json.dumps({"ok": True, "config": cfg, "persist_path": str(WATCHDOG_CONFIG_PATH)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("watchdog_config", e)


# ═════════════════════════════════════════════════════════════
# v2 新增:ProBalance Watchdog 后台线程
# ═════════════════════════════════════════════════════════════
# 配置持久化路径
WATCHDOG_CONFIG_PATH = Path.home() / ".claude" / "watchdog_config.json"

DEFAULT_WATCHDOG_CONFIG = {
    "interval_sec": 5,
    "cpu_threshold": 80.0,
    "memory_threshold_pct": 0.0,        # 0 = 不启用, > 0 = 启用(单位 %)
    "level": "BelowNormal",
    "max_per_round": 5,
    "dry_run": False,                   # True = 只记录不真降
    "auto_recover": False,              # True = CPU 降下来后自动恢复原优先级
    "recover_threshold": 30.0,          # auto_recover 触发阈值(平均 CPU < 此值恢复)
    "cooldown_sec": 60,                 # 同一进程多久内不重复降权
    "enabled": True,                    # 配置开关(供 watchdog_start 不传参数时用)
}

_watchdog_state = {
    "running": False,
    "thread": None,
    "stop_event": None,
    "config": dict(DEFAULT_WATCHDOG_CONFIG),
    "history": [],         # [{ts, pid, name, from_prio, to_prio, reason}]
    "cooldown": {},        # {pid: last_action_ts} — 同一进程 60s 内不重复
}


def _load_watchdog_config() -> dict:
    """读持久化配置(没就用默认)"""
    if WATCHDOG_CONFIG_PATH.exists():
        try:
            saved = json.loads(WATCHDOG_CONFIG_PATH.read_text(encoding="utf-8"))
            # merge with defaults(支持新版加新字段时老配置自动补默认值)
            cfg = dict(DEFAULT_WATCHDOG_CONFIG)
            cfg.update(saved)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_WATCHDOG_CONFIG)


def _save_watchdog_config(cfg: dict) -> None:
    WATCHDOG_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_protected(name: str, blacklist: list[str]) -> bool:
    """白名单 / 黑名单判定:返回 True 表示被保护不降"""
    n = name.lower()
    for protected in DEFAULT_WHITELIST.get(platform.system(), []):
        if protected.lower() in n or n in protected.lower():
            return True
    return False


def _get_avg_cpu_percent() -> float:
    """系统平均 CPU %(Windows 用 wmic,Linux 用 psutil 优先 / top fallback)"""
    if IS_WINDOWS:
        ps = (
            "(Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average"
        )
        rc, stdout, stderr = _run(["powershell", "-NoProfile", "-Command", ps], timeout=5)
        try:
            return float(stdout.strip())
        except ValueError:
            return 0.0
    else:
        try:
            import psutil  # 可选依赖
            return psutil.cpu_percent(interval=0.1)
        except ImportError:
            rc, stdout, stderr = _run(["top", "-bn1"])
            for line in stdout.split("\n"):
                if "%Cpu" in line or "Cpu(s)" in line:
                    import re
                    m = re.search(r"(\d+\.\d+)\s*id", line)
                    if m:
                        return 100.0 - float(m.group(1))
            return 0.0


def _get_memory_percent() -> float:
    """系统内存使用率 %(Win / Linux 通用 psutil)"""
    if IS_WINDOWS:
        ps = (
            "(Get-CimInstance Win32_OperatingSystem | "
            "Select-Object @{n='Used';e={$_.TotalVisibleMemorySize - $_.FreePhysicalMemory}}, TotalVisibleMemorySize).psobj.Properties | "
            "ConvertTo-Json -Compress"
        )
        # 简化:用 wmic fallback
        ps2 = (
            "(Get-CimInstance Win32_OperatingSystem | "
            "ForEach-Object { [math]::Round((1 - $_.FreePhysicalMemory / $_.TotalVisibleMemorySize) * 100, 1) })"
        )
        rc, stdout, stderr = _run(["powershell", "-NoProfile", "-Command", ps2], timeout=5)
        try:
            return float(stdout.strip())
        except ValueError:
            return 0.0
    else:
        try:
            import psutil
            return psutil.virtual_memory().percent
        except ImportError:
            rc, stdout, stderr = _run(["free"])
            for line in stdout.split("\n"):
                if line.startswith("Mem"):
                    # Mem: total used free shared buff/cache available
                    import re
                    m = re.search(r"(\d+)\s+(\d+)\s+(\d+)", line)
                    if m:
                        total, used = int(m.group(1)), int(m.group(2))
                        if total > 0:
                            return round(used / total * 100, 1)
            return 0.0


def _process_tree_records(pid: int | None = None, name: str | None = None) -> list[dict[str, Any]]:
    """Return the direct children of a selected parent process."""
    if pid is None:
        if not name:
            raise ValueError("pid 或 name 必须传一个")
        if IS_WINDOWS:
            ps = f"Get-Process -Name '{name.replace(chr(39), chr(39) * 2)}' -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Id"
            _, stdout, _ = _run(["powershell", "-NoProfile", "-Command", ps], timeout=10)
            try:
                pid = int(stdout.strip().splitlines()[0])
            except (IndexError, ValueError):
                return []
        else:
            _, stdout, _ = _run(["pgrep", "-f", name], timeout=10)
            try:
                pid = int(stdout.strip().splitlines()[0])
            except (IndexError, ValueError):
                return []

    records: list[dict[str, Any]] = []
    if IS_WINDOWS:
        ps = (
            f'Get-CimInstance Win32_Process -Filter "ParentProcessId={int(pid)}" | '
            "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Csv -NoTypeInformation"
        )
        _, stdout, _ = _run(["powershell", "-NoProfile", "-Command", ps], timeout=15)
        for line in stdout.strip().splitlines()[1:]:
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 3:
                try:
                    records.append({"pid": int(parts[0]), "ppid": int(parts[1]), "name": parts[2], "cmd": parts[3] if len(parts) > 3 else ""})
                except ValueError:
                    continue
    else:
        _, stdout, _ = _run(["ps", "-eo", "pid=,ppid=,comm=,args="], timeout=15)
        for line in stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 3:
                try:
                    child_pid, parent_pid = int(parts[0]), int(parts[1])
                except ValueError:
                    continue
                if parent_pid == int(pid):
                    records.append({"pid": child_pid, "ppid": parent_pid, "name": parts[2], "cmd": parts[3] if len(parts) > 3 else parts[2]})
    return records


def process_tree_impl(pid: int | None = None, name: str | None = None) -> str:
    try:
        if pid is None and not name:
            return json.dumps({"ok": False, "error": "pid 或 name 必须传一个"}, ensure_ascii=False)
        return json.dumps({"ok": True, "platform": platform.system(), "parent_pid": pid, "parent_name": name, "processes": _process_tree_records(pid, name)}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("process_tree", e)


_tree_watch_state = {"running": False, "thread": None, "stop_event": None, "config": {}}


def _tree_watch_loop() -> None:
    cfg = _tree_watch_state["config"]
    while not _tree_watch_state["stop_event"].is_set():
        try:
            children = _process_tree_records(cfg["pid"])
            for child in children:
                detail = json.loads(process_list_impl(sort_by="cpu", limit=100))
                proc = next((p for p in detail.get("processes", []) if p.get("pid") == child["pid"]), None)
                if proc and proc.get("cpu", 0.0) > cfg["cpu_threshold"]:
                    process_lower_impl(child["pid"], cfg["level"])
        except Exception as e:
            print(f"[tree-watch] loop error: {e}", file=sys.stderr)
        _tree_watch_state["stop_event"].wait(cfg["interval_sec"])


def process_tree_watch_impl(pid: int, cpu_threshold: float = 50.0, interval_sec: int = 5, level: str = "BelowNormal") -> str:
    try:
        if interval_sec <= 0 or cpu_threshold < 0:
            return json.dumps({"ok": False, "error": "interval_sec 必须 > 0 且 cpu_threshold 必须 >= 0"}, ensure_ascii=False)
        if _tree_watch_state["running"]:
            return json.dumps({"ok": False, "error": "process_tree_watch 已在运行"}, ensure_ascii=False)
        _tree_watch_state["config"] = {"pid": pid, "cpu_threshold": cpu_threshold, "interval_sec": interval_sec, "level": level}
        _tree_watch_state["stop_event"] = threading.Event()
        _tree_watch_state["thread"] = threading.Thread(target=_tree_watch_loop, daemon=True, name="process-tree-watch")
        _tree_watch_state["thread"].start()
        _tree_watch_state["running"] = True
        return json.dumps({"ok": True, "watching_pid": pid, "config": _tree_watch_state["config"], "interval_sec": interval_sec, "cpu_threshold": cpu_threshold}, ensure_ascii=False)
    except Exception as e:
        return _err("process_tree_watch", e)


    """ProBalance 守护线程(v3):
    - CPU 高 → 降非白名单进程
    - 内存高 → 降内存大户
    - 冷却:同一进程 cooldown_sec 内不重复降
    - 自动恢复:auto_recover=True 时,CPU 降下来后恢复原优先级
    """
    cfg = _watchdog_state["config"]
    interval = cfg["interval_sec"]
    cpu_threshold = cfg["cpu_threshold"]
    mem_threshold = cfg["memory_threshold_pct"]
    level = cfg["level"]
    max_per = cfg["max_per_round"]
    dry_run = cfg["dry_run"]
    auto_recover = cfg["auto_recover"]
    recover_threshold = cfg["recover_threshold"]
    cooldown_sec = cfg["cooldown_sec"]
    blacklist = _load_blacklist()

    print(
        f"[watchdog v3] start, interval={interval}s cpu={cpu_threshold}% "
        f"mem={mem_threshold}% level={level} dry_run={dry_run} auto_recover={auto_recover}",
        file=sys.stderr,
    )

    while not _watchdog_state["stop_event"].is_set():
        try:
            avg_cpu = _get_avg_cpu_percent()
            avg_mem = _get_memory_percent() if mem_threshold > 0 else 0.0

            # 触发条件:CPU 高 OR 内存高
            cpu_trigger = avg_cpu >= cpu_threshold
            mem_trigger = mem_threshold > 0 and avg_mem >= mem_threshold

            if cpu_trigger or mem_trigger:
                sort_by = "memory" if mem_trigger else "cpu"
                reason = (
                    f"avg_cpu={avg_cpu:.1f}%>={cpu_threshold}%" if cpu_trigger
                    else f"avg_mem={avg_mem:.1f}%>={mem_threshold}%"
                )
                plist = json.loads(process_list_impl(sort_by=sort_by, limit=max_per * 3))
                actions = 0
                now = time.time()
                for p in plist.get("processes", []):
                    if actions >= max_per:
                        break
                    if _is_protected(p["name"], blacklist):
                        continue
                    # 冷却:同一进程 cooldown 内不重复降
                    last = _watchdog_state["cooldown"].get(p["pid"], 0)
                    if now - last < cooldown_sec:
                        continue
                    # v4: 规则表优先 — 进程名匹配规则就用规则的 cpu + io
                    rules = _load_rules()
                    rule_key, rule = _match_rule(p["name"], rules)
                    if rule:
                        cpu_target = rule.get("cpu", level)
                        io_target = rule.get("io", "Normal")
                        if dry_run:
                            actions += 1
                            _watchdog_state["history"].append({
                                "ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "pid": p["pid"], "name": p["name"],
                                "from_priority": p.get("priority"),
                                "to_priority": cpu_target,
                                "io_priority": io_target,
                                "reason": f"DRY_RUN rule={rule_key} {reason}",
                                "action": "would_apply_rule",
                            })
                            continue
                        r = json.loads(process_lower_impl(p["pid"], cpu_target))
                        if r.get("ok"):
                            actions += 1
                            _watchdog_state["cooldown"][p["pid"]] = now
                            io_r = json.loads(process_io_priority_impl(p["pid"], io_target))
                            _watchdog_state["history"].append({
                                "ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                "pid": p["pid"], "name": p["name"],
                                "from_priority": p.get("priority"),
                                "to_priority": cpu_target,
                                "io_priority": io_target,
                                "reason": f"rule={rule_key} {reason}",
                                "action": "applied_rule",
                                "rule_key": rule_key,
                            })
                        continue
                    # 跳过已经在低优先级的(dry_run 模式除外,要测策略覆盖)
                    if not dry_run and p.get("priority") in ("Idle", "BelowNormal", "Idle, "):
                        continue
                    if dry_run:
                        actions += 1
                        _watchdog_state["history"].append({
                            "ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "pid": p["pid"], "name": p["name"],
                            "from_priority": p.get("priority"),
                            "to_priority": level,
                            "reason": f"DRY_RUN {reason}",
                            "action": "would_lower",
                        })
                        continue
                    r = json.loads(process_lower_impl(p["pid"], level))
                    if r.get("ok"):
                        actions += 1
                        _watchdog_state["cooldown"][p["pid"]] = now
                        _watchdog_state["history"].append({
                            "ts": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "pid": p["pid"], "name": p["name"],
                            "from_priority": p.get("priority"),
                            "to_priority": level,
                            "reason": reason,
                            "action": "lowered",
                        })
                if actions:
                    tag = "[DRY-RUN] " if dry_run else ""
                    print(f"[watchdog] {tag}{reason} → {actions} procs", file=sys.stderr)

            # 自动恢复:CPU 降到阈值以下 → 恢复之前降过的进程(只在最近 10 分钟内降的)
            elif auto_recover and avg_cpu <= recover_threshold:
                cutoff = time.time() - 600  # 10 min 内降的
                recent = [h for h in _watchdog_state["history"]
                          if h["ts"] > cutoff and h.get("action") == "lowered"
                          and h.get("recovered") is None]
                # 按 pid 去重,只恢复一次
                seen = set()
                recover_targets = []
                for h in reversed(recent):  # 最近降的先恢复
                    if h["pid"] not in seen:
                        recover_targets.append(h)
                        seen.add(h["pid"])
                for h in recover_targets:
                    if dry_run:
                        h["action"] = "would_recover"
                        continue
                    r = json.loads(process_lower_impl(h["pid"], h["from_priority"] or "Normal"))
                    if r.get("ok"):
                        h["recovered"] = True
                        h["recovered_at"] = time.time()
                if recover_targets:
                    print(f"[watchdog] auto_recover: {len(recover_targets)} procs", file=sys.stderr)

        except Exception as e:
            print(f"[watchdog] loop error: {e}", file=sys.stderr)

        _watchdog_state["stop_event"].wait(interval)

    print(f"[watchdog] stopped", file=sys.stderr)


def watchdog_start_impl(
    interval_sec: int = 5,
    cpu_threshold: float = 80.0,
    memory_threshold_pct: float = 0.0,
    level: str = "BelowNormal",
    max_per_round: int = 5,
    dry_run: bool = False,
    auto_recover: bool = False,
    recover_threshold: float = 30.0,
    cooldown_sec: int = 60,
    persist_config: bool = True,
) -> str:
    """启动 ProBalance watchdog v3"""
    try:
        if _watchdog_state["running"]:
            return json.dumps({"ok": False, "error": "watchdog 已在跑,先 stop"}, ensure_ascii=False)

        cfg = dict(DEFAULT_WATCHDOG_CONFIG)
        cfg.update({
            "interval_sec": interval_sec,
            "cpu_threshold": cpu_threshold,
            "memory_threshold_pct": memory_threshold_pct,
            "level": level,
            "max_per_round": max_per_round,
            "dry_run": dry_run,
            "auto_recover": auto_recover,
            "recover_threshold": recover_threshold,
            "cooldown_sec": cooldown_sec,
        })
        _watchdog_state["config"] = cfg
        if persist_config:
            _save_watchdog_config(cfg)

        _watchdog_state["cooldown"] = {}
        _watchdog_state["stop_event"] = threading.Event()
        _watchdog_state["thread"] = threading.Thread(
            target=_watchdog_loop, daemon=True, name="probalance-watchdog-v3"
        )
        _watchdog_state["thread"].start()
        _watchdog_state["running"] = True
        return json.dumps({"ok": True, "running": True, "config": cfg}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("watchdog_start", e)


def watchdog_stop_impl() -> str:
    """停 watchdog"""
    try:
        if not _watchdog_state["running"]:
            return json.dumps({"ok": False, "error": "watchdog 没在跑"}, ensure_ascii=False)
        _watchdog_state["stop_event"].set()
        _watchdog_state["thread"].join(timeout=10)
        _watchdog_state["running"] = False
        return json.dumps({"ok": True, "running": False, "history_count": len(_watchdog_state["history"])}, ensure_ascii=False)
    except Exception as e:
        return _err("watchdog_stop", e)


def watchdog_status_impl() -> str:
    """看 watchdog 状态 + 最近 10 条历史动作"""
    return json.dumps({
        "ok": True,
        "running": _watchdog_state["running"],
        "config": _watchdog_state["config"],
        "history_count": len(_watchdog_state["history"]),
        "history": _watchdog_state["history"][-10:],
        "blacklist_count": len(_load_blacklist()),
    }, ensure_ascii=False, indent=2)


# ═════════════════════════════════════════════════════════════
# Dynamic Registry Exports
# ═════════════════════════════════════════════════════════════
TOOL_DEFS = [
    {"name": "process_list", "description": "列进程(cpu/memory/name),返 name/pid/cpu/mem_mb/priority",
     "inputSchema": {"type": "object", "properties": {
         "sort_by": {"type": "string", "enum": ["cpu", "memory", "name"]},
         "limit": {"type": "integer", "default": 15},
     }}},
    {"name": "process_detail", "description": "单进程 name/pid/ppid/cmd/created",
     "inputSchema": {"type": "object", "properties": {"pid": {"type": "integer"}}, "required": ["pid"]}},
    {"name": "process_lower", "description": "降优先级(Win:PowerShell / Linux:renice)",
     "inputSchema": {"type": "object", "properties": {
         "pid": {"type": "integer"},
         "level": {"type": "string", "enum": ["Idle", "BelowNormal", "Normal", "AboveNormal", "High"], "default": "BelowNormal"},
     }, "required": ["pid"]}},
    {"name": "process_kill", "description": "杀进程(Win:Stop-Process / Linux:kill)",
     "inputSchema": {"type": "object", "properties": {
         "pid": {"type": "integer"}, "force": {"type": "boolean", "default": False},
     }, "required": ["pid"]}},
    {"name": "process_cpu_limit", "description": "限 CPU 亲和性(Win:ProcessorAffinity / Linux:taskset)",
     "inputSchema": {"type": "object", "properties": {
         "pid": {"type": "integer"}, "cores": {"type": "integer", "minimum": 1, "maximum": 64},
     }, "required": ["pid", "cores"]}},

    {"name": "process_whitelist_list", "description": "列白名单(default + 运行时)",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "process_whitelist_add", "description": "加进程名到运行时白名单,持久化",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "process_whitelist_remove", "description": "从运行时白名单删",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},

    {"name": "process_blacklist_list", "description": "列用户黑名单",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "process_blacklist_add", "description": "加进程名到黑名单,持久化",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "process_blacklist_remove", "description": "从黑名单删",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},

    {"name": "process_tree", "description": "查询指定父进程的直接子进程树",
     "inputSchema": {"type": "object", "properties": {
         "pid": {"type": "integer"}, "name": {"type": "string"}
     }}},
    {"name": "process_tree_watch", "description": "后台监控父进程子进程,CPU 超阈值时降优先级",
     "inputSchema": {"type": "object", "properties": {
         "pid": {"type": "integer"}, "cpu_threshold": {"type": "number", "default": 50.0},
         "interval_sec": {"type": "integer", "default": 5},
         "level": {"type": "string", "enum": ["Idle", "BelowNormal", "Normal", "AboveNormal", "High"], "default": "BelowNormal"}
     }, "required": ["pid"]}},

    {"name": "process_watchdog_start", "description": (
        "启动 ProBalance watchdog v3(后台线程)。"
        "可配:cpu_threshold / memory_threshold_pct / dry_run / auto_recover / cooldown_sec 等"
    ),
     "inputSchema": {"type": "object", "properties": {
         "interval_sec": {"type": "integer", "default": 5},
         "cpu_threshold": {"type": "number", "default": 80},
         "memory_threshold_pct": {"type": "number", "default": 0},
         "level": {"type": "string", "enum": ["Idle", "BelowNormal", "Normal", "AboveNormal", "High"], "default": "BelowNormal"},
         "max_per_round": {"type": "integer", "default": 5},
         "dry_run": {"type": "boolean", "default": False, "description": "True=只记录不真降"},
         "auto_recover": {"type": "boolean", "default": False, "description": "True=CPU 降下来后自动恢复原优先级"},
         "recover_threshold": {"type": "number", "default": 30.0},
         "cooldown_sec": {"type": "integer", "default": 60},
     }}},
    {"name": "process_watchdog_stop", "description": "停 watchdog",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "process_watchdog_status", "description": "看 watchdog 状态 + 最近 10 条降权历史",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "process_watchdog_config", "description": (
        "读 / 改 watchdog 配置(持久化到 ~/.claude/watchdog_config.json)。"
        "action=get/set/reset,set 时 key + value 必填"
    ),
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["get", "set", "reset"], "default": "get"},
         "key": {"type": "string"},
         "value": {"type": "string"},
     }}},

    {"name": "process_io_priority", "description": (
        "设 IO 优先级。Win:SetPriorityClass+IoPriorityHint(Background/Low/Normal/High);"
        "Linux:ionice -c class(idle=3 / best-effort=2 / realtime=0)"
    ),
     "inputSchema": {"type": "object", "properties": {
         "pid": {"type": "integer"},
         "level": {"type": "string", "enum": ["High", "Normal", "Low", "Background", "Idle"], "default": "Normal"},
     }, "required": ["pid"]}},

    {"name": "process_io_stat", "description": (
        "对 PID 采样 sample_sec 秒,计算磁盘 IO 读写字节数 + 速率。"
        "Linux:读 /proc/{pid}/io 的 read_bytes/write_bytes;"
        "Windows:Get-Process 的 ReadTransferCount/WriteTransferCount。"
        "返回 read_bytes / write_bytes / read_bytes_per_sec / write_bytes_per_sec"
    ),
     "inputSchema": {"type": "object", "properties": {
         "pid": {"type": "integer", "description": "目标进程 PID"},
         "sample_sec": {"type": "integer", "default": 1, "description": "采样时长(秒)"},
     }, "required": ["pid"]}},

    {"name": "process_oom_adj", "description": (
        "设进程的 OOM score adj(Linux:/proc/{pid}/oom_score_adj 写值,范围 -1000~1000);"
        "Windows 无原生 API,直接返 ok=false。"
        "两套参数二选一:score(int 直接写) 或 level(枚举映射:"
        "never_kill=-1000 / reduce=-500 / normal=0 / increase=500 / always_kill=1000)"
    ),
     "inputSchema": {"type": "object", "properties": {
         "pid": {"type": "integer", "description": "目标进程 PID"},
         "score": {"type": "integer", "minimum": -1000, "maximum": 1000,
                   "description": "直接写值,与 level 二选一"},
         "level": {"type": "string",
                   "enum": ["never_kill", "reduce", "normal", "increase", "always_kill"],
                   "description": "枚举映射(never_kill=-1000/reduce=-500/normal=0/increase=500/always_kill=1000),与 score 二选一"},
     }, "required": ["pid"]}},

    {"name": "process_rule_list", "description": "列 Ananicy 风格规则表(name → cpu/io 优先级)",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "process_rule_add", "description": "加 / 改规则(同名 key 覆盖)",
     "inputSchema": {"type": "object", "properties": {
         "key": {"type": "string", "description": "规则 id,如 'chrome' / 'make'"},
         "name_pattern": {"type": "string", "description": "进程名 pattern,支持前缀/后缀/* 通配"},
         "cpu": {"type": "string", "enum": ["Idle", "BelowNormal", "Normal", "AboveNormal", "High"], "default": "Normal"},
         "io": {"type": "string", "enum": ["High", "Normal", "Low", "Background"], "default": "Normal"},
     }, "required": ["key", "name_pattern"]}},
    {"name": "process_rule_remove", "description": "删规则",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "process_rule_apply", "description": "对单进程应用规则(查表 + 调 lower + io)",
     "inputSchema": {"type": "object", "properties": {
         "pid": {"type": "integer"},
         "dry_run": {"type": "boolean", "default": False},
     }, "required": ["pid"]}},
]


HANDLERS = {
    "process_tree": process_tree_impl,
    "process_tree_watch": process_tree_watch_impl,
    "process_list": process_list_impl,
    "process_detail": process_detail_impl,
    "process_lower": process_lower_impl,
    "process_kill": process_kill_impl,
    "process_cpu_limit": process_cpu_limit_impl,
    "process_whitelist_list": whitelist_list_impl,
    "process_whitelist_add": whitelist_add_impl,
    "process_whitelist_remove": whitelist_remove_impl,
    "process_blacklist_list": blacklist_list_impl,
    "process_blacklist_add": blacklist_add_impl,
    "process_blacklist_remove": blacklist_remove_impl,
    "process_watchdog_start": watchdog_start_impl,
    "process_watchdog_stop": watchdog_stop_impl,
    "process_watchdog_status": watchdog_status_impl,
    "process_watchdog_config": watchdog_config_impl,
    "process_io_priority": process_io_priority_impl,
    "process_io_stat": process_io_stat_impl,
    "process_oom_adj": process_oom_adj_impl,
    "process_rule_list": process_rule_list_impl,
    "process_rule_add": process_rule_add_impl,
    "process_rule_remove": process_rule_remove_impl,
    "process_rule_apply": process_rule_apply_impl,
}


# ============================================================
# Standalone CLI
# ============================================================
def _cli():
    import argparse
    p = argparse.ArgumentParser(description="process_controller v2 — 智能 OS 进程调度(Windows/Linux)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_l = sub.add_parser("list"); p_l.add_argument("--sort", default="cpu", choices=["cpu", "memory", "name"]); p_l.add_argument("-n", type=int, default=15)
    p_d = sub.add_parser("detail"); p_d.add_argument("pid", type=int)
    p_lo = sub.add_parser("lower"); p_lo.add_argument("pid", type=int); p_lo.add_argument("--level", default="BelowNormal")
    p_k = sub.add_parser("kill"); p_k.add_argument("pid", type=int); p_k.add_argument("--force", action="store_true")
    p_c = sub.add_parser("cpulimit"); p_c.add_argument("pid", type=int); p_c.add_argument("cores", type=int)
    p_t = sub.add_parser("tree"); p_t.add_argument("--pid", type=int); p_t.add_argument("--name")
    p_io = sub.add_parser("io-stat"); p_io.add_argument("pid", type=int); p_io.add_argument("--sample", type=int, default=1)
    sub.add_parser("whitelist")
    sub.add_parser("blacklist")
    p_ba = sub.add_parser("blacklist-add"); p_ba.add_argument("name")
    p_ws = sub.add_parser("watchdog-start"); p_ws.add_argument("--interval", type=int, default=5); p_ws.add_argument("--threshold", type=float, default=80.0); p_ws.add_argument("--level", default="BelowNormal"); p_ws.add_argument("--max", type=int, default=5)
    sub.add_parser("watchdog-stop")
    sub.add_parser("watchdog-status")

    args = p.parse_args()

    cmds = {
        "list": lambda: process_list_impl(args.sort, args.n),
        "detail": lambda: process_detail_impl(args.pid),
        "lower": lambda: process_lower_impl(args.pid, args.level),
        "kill": lambda: process_kill_impl(args.pid, args.force),
        "cpulimit": lambda: process_cpu_limit_impl(args.pid, args.cores),
        "tree": lambda: process_tree_impl(args.pid, args.name),
        "io-stat": lambda: process_io_stat_impl(args.pid, args.sample),
        "whitelist": lambda: whitelist_list_impl(),
        "blacklist": lambda: blacklist_list_impl(),
        "blacklist-add": lambda: blacklist_add_impl(args.name),
        "watchdog-start": lambda: watchdog_start_impl(args.interval, args.threshold, args.level, args.max),
        "watchdog-stop": lambda: watchdog_stop_impl(),
        "watchdog-status": lambda: watchdog_status_impl(),
    }
    print(cmds[args.cmd]())


if __name__ == "__main__":
    _cli()