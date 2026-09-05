# SPDX-License-Identifier: MIT
"""PrisirAI 工具 → coworker PermissionEngine 适配层(v1.0 权限闸)。

把 dispatch 的工具调用翻译成 coworker 的 Action,过 OIagentCoworkerPermissionEngine
的 SYNC 决策,返回 {allow, requires_approval, risk_level, reason}。

设计要点:
- 懒加载单例:prisiragent_web 启动时 init(workdir, audit_dir) 一次;未 init 则 fail-closed
  (写/执行类一律需确认,只读类放行)。
- 审计:每次 check 落一行 JSONL 到 logs/audit/permission_stream.jsonl(复用 W3 观察窗约定);
  sink 永不抛(引擎已 best-effort 包裹,这里再兜底)。
- v1.0 固定 PermissionMode.SYNC;standing-rule / 多模式留 v1.1。
- 引擎的 SYNC 决策已经内置:read 直接放行;write/exec 出 workspace_root → 需确认;
  命中 _DESTRUCTIVE_PATTERNS(rm -rf / Remove-Item -Recurse / format C: / dd)→ 需确认。
  适配层只做语义翻译,不重写判定。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from prisiragent_coworker.permissions import (
    Action,
    OIagentCoworkerPermissionEngine,
    PermissionContext,
    PermissionMode,
)

_LOGGER = logging.getLogger(__name__)

_engine: OIagentCoworkerPermissionEngine | None = None
_audit_path: Path | None = None

# dispatch 工具名 → coworker Action.kind(驱动 _classify_risk 的默认风险级)。
# run_shell → "shell"(exec 桶,且 target 命令串再过 destructive 模式匹配)。
_KIND = {
    "run_shell": "shell",
    "run_code": "shell",
    "write_file": "write_file",
    "edit_file": "write_file",
    "read_file": "read_file",
    "delete_file": "delete_file",
    "list_files": "list_files",
    "search_files": "search",
    "read_file_head": "read_file",
    "read_file_lines": "read_file",
    "git_status": "read_file",
    "git_diff": "read_file",
    "glob_search": "read_file",
    "web_fetch": "read_file",
    "todo_write": "read_file",
}

# 需要过闸的工具(写/执行/删除)。只读类(read/list/search/file_reputation)直接放行不过闸。
# run_code 虽限定 python/js 片段,但本质仍是任意代码执行 → 与 run_shell 同级管控。
GATED_TOOLS = frozenset({"run_shell", "run_code", "write_file", "edit_file", "delete_file"})

# 引擎缺席时的 fail-closed 白名单:只读类放行,其余一律需确认。
_READONLY_SAFE = frozenset({"read_file", "list_files", "search_files", "read_file_head",
                             "read_file_lines", "grep_search", "local_file_search",
                             "local_content_search", "anytxt_search", "web_search",
                             "file_reputation", "git_status", "git_diff",
                             "glob_search", "web_fetch", "todo_write"})


def _audit_sink(decision) -> None:
    """最小审计:append JSONL。永不抛(引擎已包裹,这里双保险)。

    decision 是 AuditDecision(kind/timestamp/engine_decision/...),无 to_dict;
    engine_decision 是 Verdict(有 to_dict)。序列化成扁平行:
    {kind, ts, allow, mode, risk_level, requires_approval, reason}。
    """
    try:
        if _audit_path is None:
            return
        v = getattr(decision, "engine_decision", None)
        row = {
            "kind": getattr(decision, "kind", "permission"),
            "ts": getattr(decision, "timestamp", None),
        }
        if v is not None:
            row.update(v.to_dict() if hasattr(v, "to_dict") else {"verdict": str(v)})
        row["ts"] = str(row["ts"])  # datetime → str,保 JSON 可序列化
        _audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(_audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def init(workdir: str, audit_dir: str) -> None:
    """初始化引擎单例。prisiragent_web 启动时调用一次;重复调用会按新 workdir 重建。"""
    global _engine, _audit_path
    _audit_path = Path(audit_dir) / "permission_stream.jsonl"
    _engine = OIagentCoworkerPermissionEngine(
        workspace_root=Path(workdir), audit_sink=_audit_sink
    )
    _LOGGER.info("perm_gate initialized: workdir=%s audit=%s", workdir, _audit_path)


def rebind_workdir(workdir: str) -> None:
    """workdir 切换时(/api/workdir)重建引擎的 path sandbox 根。审计路径保持不变。"""
    global _engine
    if _audit_path is None:
        return  # 尚未 init,等启动时 init
    _engine = OIagentCoworkerPermissionEngine(
        workspace_root=Path(workdir), audit_sink=_audit_sink
    )
    _LOGGER.info("perm_gate rebound workdir=%s", workdir)


def check(tool_name: str, args: dict, workdir: str) -> dict:
    """过闸。返回 {allow, requires_approval, risk_level, reason}。

    引擎未初始化 → fail-closed:只读类放行,写/执行类 requires_approval=True。
    """
    if _engine is None:
        return {
            "allow": tool_name in _READONLY_SAFE,
            "requires_approval": tool_name in GATED_TOOLS,
            "risk_level": "exec",
            "reason": "permission engine not initialized; fail-closed",
        }
    target = (args.get("command") or args.get("path") or "") if isinstance(args, dict) else ""
    action = Action(
        kind=_KIND.get(tool_name, "shell"),
        target=target,
        metadata={"tool": tool_name, "args_preview": str(args)[:200]},
    )
    ctx = PermissionContext(mode=PermissionMode.SYNC)
    verdict = _engine.check(action, ctx)
    return verdict.to_dict()
