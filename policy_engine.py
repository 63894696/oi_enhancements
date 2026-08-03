"""policy_engine.py — OIagent 共享审批仲裁层(单源)

v0.44 从 harness.py 抽出,供 harness(MCP) 与 aureon-oiagent daemon 共用。

背景:harness.py 与 daemon 是两套独立系统。P0-3/P0-4 最初建在 harness.py,
但 daemon 执行任务的工具落地(bash/shell/write_file/edit_file)走自己的
_dispatch_tool_inner(shell=True 全开),导致 PolicyEngine/危险参数审查对
daemon 任务完全不生效(E2E 验证时暴露)。本模块把审批逻辑单源化,两处共用。

三层判定:
  - deny  : 命中危险/越界(不可被批准覆盖,安全底线)
  - allow : 内置只读安全 或 命中已批准规则(approved_rules 持久化)
  - ask   : 有副作用待审批;批准后 policy_remember,同类免重复审批

线程安全:approved_rules 用 check_same_thread=False + 自管 Lock(与 OIMemory 同模式)。
"""
from __future__ import annotations

import json
import os
import shlex
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────
# Sandbox 白名单(单源 — harness/daemon 都以此为唯一事实)
# ─────────────────────────────────────────────────
ALLOWED_ROOTS: tuple[Path, ...] = (
    Path("C:/Users/Administrator/voice_input"),
    Path("C:/Users/Administrator/voice_input_ghostline"),
    Path("C:/Users/Administrator/oi_enhancements"),
)


def is_path_allowed(p: Path) -> bool:
    """is_relative_to 防前缀绕过 + realpath 二级防护(与 harness 原实现一致)。"""
    try:
        resolved = p.resolve(strict=False)
        realpath = resolved.resolve()  # 二次解 symlink
        target = realpath
    except (OSError, RuntimeError):
        return False
    for root in ALLOWED_ROOTS:
        try:
            root_resolved = root.resolve()
        except (OSError, RuntimeError):
            continue
        if target == root_resolved or target.is_relative_to(root_resolved):
            return True
    return False


# ─────────────────────────────────────────────────
# shell 命令白名单 + 危险参数审查(P0-4 单源)
# ─────────────────────────────────────────────────
SHELL_ALLOWED_COMMANDS = frozenset({
    "rg", "ripgrep", "grep", "ls", "dir", "cat", "type",
    "find", "tree", "head", "tail", "wc",
    "git", "diff", "status", "log", "show",
    "echo", "printf",
    "pwd", "whoami", "date", "env",
})

# 危险参数:shell=False+shlex 已挡注入/复合命令,真正的洞是"白名单命令+危险参数"。
SHELL_DANGEROUS_ARGS: dict[str, frozenset[str]] = {
    "git": frozenset({
        "clean", "reset", "push", "checkout", "restore", "rm", "branch",
        "rebase", "commit", "merge", "pull", "fetch", "clone",
    }),
    "rm": frozenset({"*"}),
    "find": frozenset({"-delete", "-exec", "-execdir"}),
    "env": frozenset({"*"}),  # env 泄 API key → 收紧
}


def check_dangerous_args(base_cmd: str, parts: list[str]) -> str | None:
    """命中危险参数返回拒绝原因,否则 None。"*'" 通配表示整命令禁止。"""
    dangerous = SHELL_DANGEROUS_ARGS.get(base_cmd)
    if not dangerous:
        return None
    if "*" in dangerous:
        return f"命令 '{base_cmd}' 已整体禁止(危险/敏感)"
    for a in parts[1:]:
        token = a.lstrip("-")
        if a in dangerous or token in dangerous:
            return f"命令 '{base_cmd}' 的危险参数被拒: '{a}'"
    return None


# ─────────────────────────────────────────────────
# approved_rules 持久化(P0-3 单源)
# ─────────────────────────────────────────────────
_POLICY_DB = Path(os.environ.get(
    "OIAGENT_POLICY_DB",
    str(Path.home() / ".local" / "share" / "aureon" / "policy_rules.db")))
_policy_lock = threading.Lock()
_policy_conn: sqlite3.Connection | None = None


def _policy_get_conn() -> sqlite3.Connection:
    global _policy_conn
    if _policy_conn is None:
        _POLICY_DB.parent.mkdir(parents=True, exist_ok=True)
        _policy_conn = sqlite3.connect(
            str(_POLICY_DB), timeout=10, check_same_thread=False)
        _policy_conn.execute(
            """CREATE TABLE IF NOT EXISTS approved_rules (
                   fingerprint TEXT PRIMARY KEY,
                   tool TEXT NOT NULL,
                   decision TEXT NOT NULL,
                   note TEXT DEFAULT '',
                   created_at REAL NOT NULL
               )""")
        _policy_conn.commit()
    return _policy_conn


def rule_fingerprint(tool: str, args: dict[str, Any]) -> str:
    """对工具调用算稳定指纹(同类调用同一 key)。"""
    if tool in ("shell", "bash", "run_powershell"):
        cmd = args.get("command", args.get("cmd", ""))
        try:
            parts = shlex.split(cmd)
        except ValueError:
            parts = cmd.split()
        head = Path(parts[0]).name if parts else ""
        sub = parts[1] if len(parts) > 1 else ""
        return f"shell:{head}:{sub}"
    if tool in ("read", "read_file", "glob", "grep", "write", "write_file", "edit", "edit_file"):
        p = Path(args.get("path", args.get("pattern", "")) or "")
        for root in ALLOWED_ROOTS:
            try:
                if p.resolve(strict=False).is_relative_to(root.resolve()):
                    return f"{tool}:root:{root.name}"
            except (OSError, RuntimeError):
                continue
        return f"{tool}:other"
    return f"{tool}:default"


def policy_lookup(fingerprint: str) -> str | None:
    """查持久化规则,返回 'allow'/'deny'/None。"""
    try:
        with _policy_lock:
            row = _policy_get_conn().execute(
                "SELECT decision FROM approved_rules WHERE fingerprint=?",
                (fingerprint,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def policy_remember(fingerprint: str, tool: str, decision: str, note: str = "") -> None:
    """把一次审批决策持久化(批准→allow,拒绝→deny)。"""
    try:
        with _policy_lock:
            conn = _policy_get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO approved_rules (fingerprint, tool, decision, note, created_at) VALUES (?,?,?,?,?)",
                (fingerprint, tool, decision, note, time.time()))
            conn.commit()
    except Exception:
        pass


# 工具名规范化:daemon 与 harness 工具名不同,归到同一语义类
_READ_ONLY_TOOLS = {"read", "read_file", "glob", "grep", "everything_query", "list_tools"}
_SHELL_TOOLS = {"shell", "bash", "run_powershell", "adb_shell"}
_WRITE_TOOLS = {"write", "write_file", "edit", "edit_file"}

# 信任扩展工具:改变「我信任谁/谁能连我」的副作用动作,daemon 侧绝不静默放行。
# 与只读/写文件不同——加/删联系人一旦放行即建立/断开 E2E 连接,事后难撤销。
# 默认 ask;仅当 approved_rules 已有人工批准记录(同类指纹)才 allow。
_TRUST_EXTENDING_TOOLS = {
    "simplex_accept_invitation",
    "simplex_delete_contact",
}


def policy_check(tool: str, args: dict[str, Any]) -> tuple[str, str]:
    """审批仲裁主入口:返回 (verdict, reason)。

    verdict ∈ {allow, deny, ask}:
    - deny : 危险/越界(硬拒,不可被批准覆盖)
    - allow: 内置只读安全 或 命中已批准规则
    - ask  : 副作用待审批(查 approved_rules,已批则 allow)
    """
    # 1) shell 类硬拒优先(安全底线)
    if tool in _SHELL_TOOLS:
        cmd = args.get("command", args.get("cmd", ""))
        try:
            parts = shlex.split(cmd)
        except ValueError:
            parts = cmd.split()
        if parts:
            base = Path(parts[0]).name
            if base not in SHELL_ALLOWED_COMMANDS:
                return ("deny", f"shell 命令不在白名单: {base}")
            danger = check_dangerous_args(base, parts)
            if danger:
                return ("deny", danger)

    # 2) 文件类越界硬拒
    if tool in ("read", "read_file", "write", "write_file", "edit", "edit_file", "glob", "grep"):
        p_str = args.get("path", "")
        if p_str and Path(p_str).is_absolute() and not is_path_allowed(Path(p_str)):
            return ("deny", f"路径越白名单: {p_str}")

    # 3) 查持久化批准规则
    fp = rule_fingerprint(tool, args)
    remembered = policy_lookup(fp)
    if remembered == "allow":
        return ("allow", f"命中已批准规则 {fp}")
    if remembered == "deny":
        return ("deny", f"命中已拒绝规则 {fp}")

    # 4) 内置默认:只读 allow,副作用 ask
    if tool in _READ_ONLY_TOOLS:
        return ("allow", "内置只读安全")
    return ("ask", f"副作用操作待审批 (fingerprint={fp})")


# ─────────────────────────────────────────────────
# daemon 专用:宽松危险拦截(不套窄白名单)
# ─────────────────────────────────────────────────
# 背景:daemon 的 bash/run_powershell 是 v0.19.4 有意的 shell=True 全开,
# 日常要跑 ls/find/powershell/Get-ChildItem 等(大多不在 harness 窄白名单)。
# 若把 daemon 也套窄白名单 + shell=False,合法任务会大面积误拒。
# 故 daemon 侧只拦真正的危险操作(纯收益,不误伤),不套窄白名单。
#
# 危险命令模式(命中即 deny):
#   - 文件毁灭: rm -rf / del|rd|rmdir /s / Remove-Item .* -Recurse
#   - 磁盘/系统: format / mkfs / diskpart / shutdown / reboot
#   - 远程执行: curl|wget ... |bash|sh / iex(iwr ...) (下载即执行)
#   - git 危险: clean/reset --hard/push --force
#   - 提权: sudo / runas / net user / sc create(注册服务)
import re as _re

_DANGEROUS_CMD_PATTERNS: tuple[_re.Pattern, ...] = tuple(
    _re.compile(p, _re.IGNORECASE)
    for p in (
        r"\brm\s+-[a-z]*r[a-z]*f\b",                 # rm -rf / -fr
        r"\brm\s+-[a-z]*f[a-z]*r\b",
        r"\b(del|erase)\s+.*/[sq]",                  # del /s /q
        r"\b(rd|rmdir)\s+/s",                        # rd /s
        r"Remove-Item\b.*-Recurse",                  # PowerShell 递归删
        r"\bformat\s+[a-z]:",                        # format C:
        r"\b(mkfs|diskpart|fdisk)\b",
        r"\b(shutdown|reboot|restart-computer)\b",
        r"\|\s*(bash|sh|powershell|pwsh)\b",         # 管道进 shell(下载即执行)
        r"\biex\b.*\biwr\b",                          # PS iex(iwr ...)
        r"Invoke-Expression.*Invoke-WebRequest",
        r"\bgit\s+clean\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+push\b.*--force",
        r"\bgit\s+push\b.*\s-f\b",
        r"\bsudo\b",
        r"\brunas\b",
        r"\bnet\s+user\b",                           # 改账户
        r"\bsc\s+(create|delete|config)\b",          # 注册/删服务
        r"\breg\s+(add|delete)\b.*HKLM",             # 改系统注册表
        r"\b(curl|wget)\b.*\|\s*(bash|sh)\b",
    )
)


def check_dangerous_command(cmd: str) -> str | None:
    """对一条 shell/powershell 命令串做危险模式扫描(daemon 全开 shell 用)。

    命中返回拒绝原因,否则 None。只拦危险,不限命令种类(兼容 daemon 多样命令)。
    """
    for pat in _DANGEROUS_CMD_PATTERNS:
        m = pat.search(cmd)
        if m:
            return f"危险命令模式拦截: 命中 {m.group(0)!r} (规则 {pat.pattern})"
    return None


def policy_check_daemon(tool: str, args: dict[str, Any]) -> tuple[str, str]:
    """daemon 侧审批判定(宽松):返回 (verdict, reason)。

    与 policy_check 的差异:
    - 不套窄白名单(daemon 全开 shell 合法),只拦危险命令模式
    - write_file/edit_file 越出 ALLOWED_ROOTS 才拒(daemon 当前 write 无路径检查,补真实漏洞)
    - read_file 只读放开
    """
    # 1) shell/powershell 危险命令扫描(唯一 shell 拦截,不误伤合法命令)
    if tool in ("bash", "run_powershell", "shell", "adb_shell"):
        cmd = args.get("cmd", args.get("command", ""))
        danger = check_dangerous_command(cmd)
        if danger:
            return ("deny", danger)
        return ("allow", "shell 非危险命令(daemon 宽松放行)")

    # 2) write/edit 路径越界拦截(补 daemon 真实漏洞:write_file 原无路径检查)
    if tool in ("write_file", "edit_file", "write", "edit"):
        p_str = args.get("path", "")
        if p_str:
            p = Path(p_str).expanduser()
            if p.is_absolute() and not is_path_allowed(p):
                return ("deny", f"write/edit 路径越白名单: {p_str}")
        return ("allow", "write/edit 路径在白名单内")

    # 2b) 信任扩展工具(加/删联系人):补 daemon 漏洞——原 branch 3 对一切
    #     内置放行,导致 simplex_accept_invitation 静默加陌生人、scan_and_accept
    #     的 ask 分支不可达。默认 ask;仅当 approved_rules 已有同类批准才 allow。
    if tool in _TRUST_EXTENDING_TOOLS:
        fp = rule_fingerprint(tool, args)
        remembered = policy_lookup(fp)
        if remembered == "allow":
            return ("allow", f"信任扩展工具命中已批准规则 {fp}")
        if remembered == "deny":
            return ("deny", f"信任扩展工具命中已拒绝规则 {fp}")
        return ("ask", f"信任扩展动作需人工批准(加/删联系人,指纹 {fp})")

    # 3) 其余(read_file/everything_query/list_tools 等)只读放行
    return ("allow", "内置放行")
