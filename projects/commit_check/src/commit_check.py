#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# src/commit_check.py — M3.69 commit_check 核心库 + M3.74 laya_guard fast-path(2026-09-25)
#
# 目的:
#   - 读 git diff(staged 或 vs commit ref)
#   - 拆 changed files + hunks
#   - 双 spec 联合判:
#       disk_cleanup_conf → 路径风险(识别 .pem/.key/.pfx 等敏感路径)
#       task_conf         → 变更内容意图(是否在跑 destructive 操作)
#   - 输出 allow / ask / deny 决策,供 main.py + git hook 消费
#
# M3.74 升级(2026-09-25):
#   - 加 laya_guard fast-path(M3.72 同款复用):扫 diff 文本检测
#     secrets / API key / jailbreak 注入 / 高风险内容
#   - 三级 fallback:
#       1. laya_guard fast-path(扫 secrets/注入)— critical/high 短路 deny
#       2. disk_cleanup_conf + task_conf 双 spec
#       3. dangerous_ext + regex 兜底
#   - 不修改 companion/,通过 sys.path 复用 laya_guard
#
# 设计原则:
#   - 不修改 companion/ 任何代码
#   - 复用 classify_disk_cleanup._parse_output / classify_task_local._parse_output 的
#     同款解析(本地 reimpl,不 import 它们的 main 避免 argparse 冲突)
#   - 离线可用:无网络
#   - 不自动改 git(只 print 建议)
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# M3.74:让 laya_guard 可 import(safe_exec 同模式)
_PROJECTS = Path(__file__).resolve().parent.parent.parent
_LAYA_GUARD = _PROJECTS / "laya_guard" / "src"
if _LAYA_GUARD.exists() and str(_LAYA_GUARD) not in sys.path:
    sys.path.insert(0, str(_LAYA_GUARD))

try:
    from laya_guard import guard as _laya_guard_fn  # noqa
    _LAYA_GUARD_OK = True
except Exception:  # noqa: BLE001
    _LAYA_GUARD_OK = False
    _laya_guard_fn = None

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------
DEFAULT_DANGEROUS_EXT: tuple[str, ...] = (
    ".pem", ".key", ".pfx", ".p12", ".keystore", ".jks",
    ".env", "aws/credentials", ".htpasswd", "id_rsa",
)

DEFAULT_DANGEROUS_PATTERNS: tuple[str, ...] = (
    # 兜底模式(用于 custom ext 也漏掉时)
    r"\.pem$", r"\.key$", r"\.pfx$", r"\.p12$",
    r"\.keystore$", r"\.jks$", r"id_rsa", r"\.htpasswd$",
    r"/\.aws/", r"\.env$",
)

# Risk 等级排序(数字越大越危险)
RISK_ORDER = {"safe": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

# ------------------------------------------------------------
# Data classes
# ------------------------------------------------------------
@dataclass
class FileChange:
    """一个文件的 diff 摘要。"""
    path: str                  # repo-relative path (a/xxx → xxx)
    change_type: str           # added | modified | deleted | renamed
    added: int = 0             # + lines
    removed: int = 0           # - lines
    hunks: list[str] = field(default_factory=list)  # raw diff 内容片段


@dataclass
class PathRisk:
    """disk_cleanup_conf 对单条路径的判分。"""
    path: str
    risk: str                  # safe/low/medium/high/critical/unknown
    action: Optional[str] = None
    risk_conf: Optional[float] = None
    raw: str = ""
    matched_dangerous: bool = False  # 是否命中危险扩展名/模式


@dataclass
class IntentRisk:
    """task_conf 对变更内容意图的判分。"""
    text: str                  # 喂给 task_conf 的输入
    task_type: str             # code_call / code_qa / creative / long / fast / general
    risk: str                  # safe/low/medium/high/critical
    action_conf: Optional[float] = None
    raw: str = ""


@dataclass
class Decision:
    """最终决策。"""
    decision: str              # allow / ask / deny
    reasons: list[str] = field(default_factory=list)
    suggestion: str = ""
    paths: list[PathRisk] = field(default_factory=list)
    intent: Optional[IntentRisk] = None
    diff_summary: str = ""
    laya_result: Optional[dict] = None  # M3.74 laya_guard fast-path 结果
    backend: str = "lora_only"          # laya_router / lora_fallback / lora_only

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ------------------------------------------------------------
# Git diff 提取
# ------------------------------------------------------------
def _is_git_repo(cwd: Optional[Path] = None) -> bool:
    """判断 cwd 或其父级是否在 git repo 里。"""
    cmd = ["git", "rev-parse", "--is-inside-work-tree"]
    try:
        r = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _get_staged_diff(cwd: Optional[Path] = None) -> str:
    """调 git diff --staged 取 raw diff text。"""
    cmd = ["git", "diff", "--staged", "--no-color"]
    r = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"git diff --staged 失败: {r.stderr.strip() or '未知错误'}")
    return r.stdout


def _get_diff_vs(ref: str, cwd: Optional[Path] = None) -> str:
    """调 git diff <ref> 取 raw diff text。"""
    cmd = ["git", "diff", ref, "--no-color"]
    r = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"git diff {ref} 失败: {r.stderr.strip() or '未知错误'}")
    return r.stdout


# ------------------------------------------------------------
# Diff 解析
# ------------------------------------------------------------
# git diff 一段文件头:
#   diff --git a/path/to/file b/path/to/file
#   index abc..def 100644
#   --- a/path/to/file
#   +++ b/path/to/file
#   @@ -1,3 +1,5 @@
#   ...hunks
_DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)


def _parse_diff(text: str) -> list[FileChange]:
    """从 raw git diff 抽 (path, change_type, added, removed, hunks)。"""
    if not text.strip():
        return []

    changes: list[FileChange] = []
    # 按 diff --git 分割
    parts = re.split(r"^diff --git ", text, flags=re.MULTILINE)
    for part in parts:
        if not part.strip():
            continue
        # 提取路径
        m = re.match(r"a/(.+?) b/(.+?)\n", part, re.DOTALL)
        if not m:
            continue
        path = m.group(2).strip()
        # 判断 change type
        change_type = "modified"
        if part.startswith("new file"):
            change_type = "added"
        elif part.startswith("deleted file"):
            change_type = "deleted"
        elif part.startswith("rename "):
            change_type = "renamed"

        # 提取 hunks(@@...@@ 起头)
        hunk_blocks = re.findall(
            r"^@@.*?@@(?:.*?)(?=^@@|\Z)", part,
            flags=re.MULTILINE | re.DOTALL)
        added = 0
        removed = 0
        for h in hunk_blocks:
            for line in h.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    added += 1
                elif line.startswith("-") and not line.startswith("---"):
                    removed += 1
        changes.append(FileChange(
            path=path, change_type=change_type,
            added=added, removed=removed,
            hunks=hunk_blocks,
        ))
    return changes


# ------------------------------------------------------------
# 危险模式匹配
# ------------------------------------------------------------
def _is_dangerous_path(path: str, dangerous_ext: tuple[str, ...]) -> bool:
    """判断 path 是否命中自定义或默认危险扩展名/模式。"""
    p = path.lower()
    # 先查用户自定义 ext
    for ext in dangerous_ext:
        ext_l = ext.lower()
        if ext_l.startswith("."):
            if p.endswith(ext_l):
                return True
        elif ext_l in p:
            return True
    # 再查默认 regex 模式
    for pat in DEFAULT_DANGEROUS_PATTERNS:
        if re.search(pat, p):
            return True
    return False


# ------------------------------------------------------------
# M3.74 laya_guard fast-path
# ------------------------------------------------------------
# 简版 secrets regex(轻量先筛,命中后再交给 laya_guard 二次确认)
_SECRET_HINTS = (
    # AWS / GCP / Azure keys
    r"AKIA[0-9A-Z]{16}",
    r"AIza[0-9A-Za-z\-_]{35}",
    # GitHub PAT / OpenAI API key / Slack token
    r"gh[pousr]_[A-Za-z0-9]{36,}",
    r"sk-[A-Za-z0-9]{20,}",
    r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    # generic high-entropy assignment
    r"(?i)(api[_\-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}",
)


def _extract_added_lines(diff_text: str, max_lines: int = 200,
                         max_chars: int = 8000) -> str:
    """从 git diff 提取 added lines(去 +++ 头),拼成喂给 laya 的文本。

    截断策略:
      - 最多 max_lines 行 / max_chars 字符(diff 通常很短,无截断必要)
      - 加的代码行 / 配置行都保留(秘密通常在 added 而非 deleted)
    """
    if not diff_text:
        return ""
    out: list[str] = []
    for line in diff_text.splitlines():
        if not line:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            out.append(line[1:].rstrip())
        elif line.startswith("@@") or line.startswith("diff --git"):
            continue
        if len(out) >= max_lines:
            break
    txt = "\n".join(out)
    if len(txt) > max_chars:
        txt = txt[:max_chars] + "\n...[truncated]"
    return txt


def _regex_secret_scan(diff_text: str) -> list[str]:
    """regex 兜底:扫 known secrets 模式,返回命中片段(去 raw text 前 100 字符)。

    用于 laya 不可用时的最弱兜底(只看 known patterns)。
    """
    if not diff_text:
        return []
    hits: list[str] = []
    for pat in _SECRET_HINTS:
        m = re.search(pat, diff_text)
        if m:
            hits.append(m.group(0)[:100])
    return hits


def _laya_guard_scan_diff(diff_text: str, no_laya: bool = False) -> dict:
    """M3.74:用 laya_guard 扫 staged diff 文本,检测 secrets/注入/敏感。

    Returns:
      dict {
        "ok": bool,
        "available": bool,        # laya 是否加载成功
        "risk": "safe"/.../"critical",
        "jailbreak": float,       # 0-1
        "injection": float,
        "sensitive": float,
        "harm": float,            # 0-3
        "regex_hits": [str],      # regex 兜底命中
        "raw_text": str,          # 喂给 laya 的 added lines
        "reason": str,            # 触发风险的具体维度
        "latency_ms": float,
      }
    """
    added = _extract_added_lines(diff_text)

    # regex 兜底(总跑,laya 不可用时也工作)
    regex_hits = _regex_secret_scan(diff_text)

    res = {
        "ok": True,
        "available": False,
        "risk": "safe",
        "jailbreak": 0.0,
        "injection": 0.0,
        "sensitive": 0.0,
        "harm": 0.0,
        "regex_hits": regex_hits,
        "raw_text": added[:500],
        "reason": "",
        "latency_ms": 0.0,
    }

    if no_laya or not _LAYA_GUARD_OK or _laya_guard_fn is None:
        # laya 不可用 → 纯 regex 决策
        if regex_hits:
            res["risk"] = "critical"
            res["reason"] = f"regex 命中 {len(regex_hits)} 条 secret 模式"
            res["available"] = False
        return res

    import time
    t0 = time.time()
    try:
        guard_res = _laya_guard_fn(added)
    except Exception as e:  # noqa: BLE001
        elapsed_ms = (time.time() - t0) * 1000
        res["latency_ms"] = elapsed_ms
        if regex_hits:
            res["risk"] = "critical"
            res["reason"] = (
                f"laya 异常({type(e).__name__}),但 regex 命中 "
                f"{len(regex_hits)} 条 secret")
        return res
    elapsed_ms = (time.time() - t0) * 1000

    res["available"] = True
    res["latency_ms"] = elapsed_ms
    res["risk"] = guard_res.get("risk", "safe")
    res["jailbreak"] = guard_res.get("jailbreak", 0.0)
    res["injection"] = guard_res.get("injection", 0.0)
    res["sensitive"] = guard_res.get("sensitive", 0.0)
    res["harm"] = guard_res.get("harm", 0.0)

    # 决定 reason
    if res["risk"] == "critical":
        if res["jailbreak"] >= 0.7:
            res["reason"] = (
                f"laya 判 critical(jailbreak={res['jailbreak']:.2f}, "
                f"注入攻击)")
        elif res["harm"] >= 2.5:
            res["reason"] = (
                f"laya 判 critical(harm={res['harm']:.2f}, "
                f"高度危险内容)")
        elif res["injection"] >= 0.7:
            res["reason"] = (
                f"laya 判 critical(injection={res['injection']:.2f})")
        else:
            res["reason"] = "laya 判 critical"
    elif res["risk"] == "high":
        if res["sensitive"] >= 0.7:
            res["reason"] = (
                f"laya 判 high(sensitive={res['sensitive']:.2f}, "
                f"疑似 secrets)")
        else:
            res["reason"] = (
                f"laya 判 high(jb={res['jailbreak']:.2f}, "
                f"inj={res['injection']:.2f})")
    elif regex_hits:
        # laya 漏判但 regex 命中 → 升级到 critical
        res["risk"] = "critical"
        res["reason"] = (
            f"laya 未达 critical 但 regex 命中 {len(regex_hits)} 条 secret "
            f"模式: {regex_hits[0][:40]}")

    return res


# ------------------------------------------------------------
# Adapter 接口:重 parse_output,不依赖 companion 的 _parse_output
# 避免 import 触发 companion 的 argparse 默认行为
# ------------------------------------------------------------
_DISK_PARSE = re.compile(
    r"Safety:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"Jailbreak:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"(?:Action:\s*(\w+)(?::(\d+(?:\.\d+)?))?)?",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_disk_output(raw: str) -> dict:
    """从 disk_cleanup_conf raw 抽 (risk, jb, action, rc, jc, ac)。"""
    idx = raw.lower().find("safety:")
    if idx < 0:
        return {"risk": None, "action": None, "risk_conf": None}
    seg = raw[idx:]
    second = seg.lower().find("safety:", 8)
    if second > 0:
        seg = seg[:second]
    m = _DISK_PARSE.search(seg)
    if not m:
        return {"risk": None, "action": None, "risk_conf": None}
    return {
        "risk": m.group(1).lower().strip() if m.group(1) else None,
        "risk_conf": float(m.group(2)) if m.group(2) else None,
        "action": m.group(5).lower().strip() if m.group(5) else None,
    }


_TASK_VALID = {
    "code_call", "code_qa", "creative", "long", "fast", "general",
}


def _parse_task_output(raw: str) -> dict:
    """从 task_conf raw 抽 (risk_label, task_type, action_conf)。"""
    out = {
        "risk_label": "unknown",
        "task_type": "general",
        "action_conf": 0.0,
    }
    action_block = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("safety:") or "safety:" in low:
            payload = line.split(":", 1)[1].strip()
            parts = payload.split(":")
            out["risk_label"] = parts[0].strip().lower()
        elif low.startswith("action:") or "action:" in low:
            action_block += " " + line.split(":", 1)[1].strip()

    if action_block:
        tokens = [t.strip() for t in action_block.split(":")]
        for i, tok in enumerate(tokens):
            tok_clean = tok.lower().strip(" ,.;:!?'\"()[]{}|").rstrip("．。,.;:!?")
            if tok_clean in _TASK_VALID:
                out["task_type"] = tok_clean
                if i + 1 < len(tokens):
                    try:
                        out["action_conf"] = float(
                            re.sub(r"[^\d.]", "", tokens[i + 1]))
                    except ValueError:
                        pass
                break
    return out


def _build_disk_text(path: str) -> str:
    """组装给 disk_cleanup_conf 的 prompt(对齐训练数据格式)。"""
    p = Path(path)
    parts = p.parts
    if len(parts) >= 5:
        ctx = "\\".join(parts[1:4])
        rel = "\\".join(parts[4:])
        loc = f"{ctx}\\{rel}"
    else:
        loc = str(p)
    return (f"文件路径: {loc}\n"
            f"扩展名: {p.suffix.lower() or '(无)'}\n"
            f"大小: ?\n"
            f"年龄: 0 天\n"
            f"问: 这个 Windows 系统文件是否可以安全清理?")


def _build_task_text(diff_excerpt: str) -> str:
    """组装给 task_conf 的 prompt(diff 内容摘要)。"""
    excerpt = diff_excerpt[:1500] if diff_excerpt else "(空 diff)"
    return f"用户消息: {excerpt}\n上下文: (无历史)"


# ------------------------------------------------------------
# 分类:路径风险 + 意图风险
# ------------------------------------------------------------
def _classify_paths(
    paths: list[str], spec, dangerous_ext: tuple[str, ...],
) -> list[PathRisk]:
    """对每条路径跑 disk_cleanup_conf,合成 PathRisk。"""
    results: list[PathRisk] = []
    for p in paths:
        matched = _is_dangerous_path(p, dangerous_ext)
        try:
            text = _build_disk_text(p)
            res = spec.classify(text, max_new_tokens=40)
            parsed = _parse_disk_output(res["raw"])
            risk = parsed["risk"] or "unknown"
        except Exception as e:  # noqa: BLE001
            results.append(PathRisk(
                path=p, risk="unknown", raw=f"ERROR: {type(e).__name__}: {e}",
                matched_dangerous=matched,
            ))
            continue
        results.append(PathRisk(
            path=p, risk=risk,
            action=parsed["action"],
            risk_conf=parsed["risk_conf"],
            raw=res["raw"],
            matched_dangerous=matched,
        ))
    return results


def _classify_intent(diff_text: str, spec) -> IntentRisk:
    """把 diff 喂 task_conf,得 IntentRisk。"""
    text = _build_task_text(diff_text)
    try:
        res = spec.classify(text, max_new_tokens=40)
        parsed = _parse_task_output(res["raw"])
    except Exception as e:  # noqa: BLE001
        return IntentRisk(
            text=text, task_type="unknown", risk="unknown",
            raw=f"ERROR: {type(e).__name__}: {e}",
        )
    return IntentRisk(
        text=text,
        task_type=parsed["task_type"],
        risk=parsed["risk_label"],
        action_conf=parsed["action_conf"],
        raw=res["raw"],
    )


# ------------------------------------------------------------
# 决策合并
# ------------------------------------------------------------
def _decide(paths: list[PathRisk], intent: IntentRisk,
            deleted_seen: bool) -> Decision:
    """决策规则:
       - 任一 spec critical → deny
       - disk_cleanup high + 删除操作 → deny
       - disk_cleanup high + 命中危险扩展名 → deny(密钥文件宁可错杀)
       - 任一 spec medium → ask
       - low/safe → allow
    """
    reasons: list[str] = []
    suggestion = ""

    # 1. 任一 critical
    crit_paths = [p for p in paths if p.risk == "critical"]
    if crit_paths:
        reasons.append(
            f"路径 critical 风险: {', '.join(p.path for p in crit_paths)}")
    if intent.risk == "critical":
        reasons.append(f"变更意图 critical: {intent.task_type}")

    if reasons:
        decision = "deny"
        suggestion = _suggestion_for_deny(paths)
        return Decision(
            decision=decision, reasons=reasons,
            suggestion=suggestion, paths=paths, intent=intent,
        )

    # 2. disk_cleanup high + 任一文件被删除
    high_paths = [p for p in paths if p.risk == "high"]
    high_dangerous = [p for p in high_paths if p.matched_dangerous]
    if high_paths and deleted_seen:
        reasons.append(
            "磁盘清理 high 风险 + 检测到删除操作: "
            f"{', '.join(p.path for p in high_paths)}")
        decision = "deny"
        suggestion = _suggestion_for_deny(paths)
        return Decision(
            decision=decision, reasons=reasons,
            suggestion=suggestion, paths=paths, intent=intent,
        )

    # 3. disk_cleanup high + 命中危险扩展名(密钥文件宁可错杀)
    if high_dangerous:
        reasons.append(
            "高风险路径命中危险扩展名(疑似密钥/敏感配置): "
            f"{', '.join(p.path for p in high_dangerous)}")
        return Decision(
            decision="deny", reasons=reasons,
            suggestion=_suggestion_for_deny(high_dangerous),
            paths=paths, intent=intent,
        )

    # 4. 任一 medium → ask
    med_paths = [p for p in paths if p.risk == "medium"]
    if med_paths:
        reasons.append(
            f"路径 medium 风险(需确认): {', '.join(p.path for p in med_paths)}")
    if intent.risk == "medium":
        reasons.append(f"变更意图 medium(需确认): {intent.task_type}")
    if reasons:
        decision = "ask"
        suggestion = "请人工 review 中等风险文件,确认无敏感数据后再 commit"
        return Decision(
            decision=decision, reasons=reasons,
            suggestion=suggestion, paths=paths, intent=intent,
        )

    # 5. high(但无删除,无危险扩展) → ask(礼貌提醒)
    if high_paths:
        reasons.append(
            f"路径 high 风险: {', '.join(p.path for p in high_paths)}")
        return Decision(
            decision="ask", reasons=reasons,
            suggestion="高风险路径,建议人工 review",
            paths=paths, intent=intent,
        )

    # 6. low / safe → allow
    return Decision(
        decision="allow", reasons=["所有路径 risk ≤ low"],
        suggestion="可正常 commit", paths=paths, intent=intent,
    )


def _suggestion_for_deny(paths: list[PathRisk]) -> str:
    """针对 deny 生成具体建议。"""
    dangerous = [p for p in paths if p.matched_dangerous or p.risk == "critical"]
    if dangerous:
        names = ", ".join(p.path for p in dangerous)
        return (f"git reset HEAD {names}  # 暂存撤回\n"
                f"# 然后: 加密 / 移到 .gitignore / 用 secret manager 替代")
    return "请人工 review 后再 commit"


# ------------------------------------------------------------
# 顶层入口
# ------------------------------------------------------------
def check_diff(
    diff_text: str,
    dangerous_ext: tuple[str, ...] = DEFAULT_DANGEROUS_EXT,
    disk_spec=None, task_spec=None,
    no_laya: bool = False,
) -> Decision:
    """从 raw diff 文本跑完整流程。disk_spec/task_spec = LoadedAdapter 实例。

    不传时延迟 import companion(单测可 mock)。

    M3.74 升级:先跑 laya_guard fast-path(secrets/注入/敏感),critical/high
    短路返回 deny,不再走 LoRA 双 spec(节省 ~7-15s)。
    """
    if disk_spec is None or task_spec is None:
        from adapter_registry import get_adapter  # noqa
        disk_spec = disk_spec or get_adapter("disk_cleanup_conf")
        task_spec = task_spec or get_adapter("task_conf")

    # M3.74:laya_guard fast-path(最前)
    laya_res = _laya_guard_scan_diff(diff_text, no_laya=no_laya)

    # M3.74-fix:仅 laya critical + regex 命中 → 短路 deny
    # (纯 laya critical 可能误伤代码如 "PRIVATE KEY" 字符串,
    #  应让 LoRA 双 spec 继续跑,只在 regex 命中真 secrets 才 deny)
    laya_secrets_confirmed = (
        laya_res["risk"] in ("critical", "high")
        and bool(laya_res["regex_hits"])
    )
    if laya_secrets_confirmed and laya_res["ok"]:
        backend = "laya_router" if laya_res["available"] else "regex_fallback"
        return Decision(
            decision="deny",
            reasons=[
                f"[laya_guard+regex] {laya_res['reason']}",
            ],
            suggestion=(
                "检测到敏感内容(API key/凭据),请人工 review 后"
                "重新 stage。修复方法:\n"
                "  - 移走 secrets 到环境变量或密钥管理服务\n"
                "  - 用 git reset HEAD <file> + 重新 add 干净的版本"),
            paths=[], intent=None, diff_summary="",
            laya_result=laya_res, backend=backend,
        )

    changes = _parse_diff(diff_text)
    paths = list({c.path for c in changes})
    deleted_seen = any(c.change_type == "deleted" for c in changes)

    paths_risks = _classify_paths(paths, disk_spec, dangerous_ext)
    intent = _classify_intent(diff_text, task_spec)

    dec = _decide(paths_risks, intent, deleted_seen)
    # 注入 laya_result(供决策可见)— 仅当 laya 真跑了 或 regex 命中才记录
    if laya_res.get("available") or laya_res.get("regex_hits"):
        dec.laya_result = laya_res
        # laya 判 critical 但 regex 没命中 → 升级 allow→ask(让人 review)
        if dec.decision == "allow" and laya_res["risk"] in ("critical", "high"):
            dec.decision = "ask"
            dec.reasons.insert(0,
                f"[laya_guard] laya 判 {laya_res['risk']} 但 regex 无命中"
                f"(可能是误伤): {laya_res['reason']}")
            dec.suggestion = "laya 判 critical 但 regex 无明确 secrets,请人工 review"
            dec.backend = "lora_fallback"
        elif dec.decision != "allow":
            dec.backend = "lora_fallback"
        else:
            dec.backend = "lora_only"
    return dec


def check_staged(
    cwd: Optional[Path] = None,
    dangerous_ext: tuple[str, ...] = DEFAULT_DANGEROUS_EXT,
    no_laya: bool = False,
) -> Decision:
    """staged diff 入口。"""
    if not _is_git_repo(cwd):
        return Decision(
            decision="allow",
            reasons=["不在 git repo 内,跳过(友好降级)"],
            suggestion="无 git 操作可执行",
            diff_summary="(no git repo)",
        )
    diff = _get_staged_diff(cwd)
    if not diff.strip():
        return Decision(
            decision="allow",
            reasons=["staged 区为空"],
            suggestion="无变更可检查",
            diff_summary="(empty staged diff)",
        )
    dec = check_diff(diff, dangerous_ext=dangerous_ext, no_laya=no_laya)
    dec.diff_summary = _summarize_diff(diff)
    return dec


def check_vs(
    ref: str, cwd: Optional[Path] = None,
    dangerous_ext: tuple[str, ...] = DEFAULT_DANGEROUS_EXT,
    no_laya: bool = False,
) -> Decision:
    """vs commit ref diff 入口。"""
    if not _is_git_repo(cwd):
        return Decision(
            decision="allow",
            reasons=["不在 git repo 内,跳过(友好降级)"],
            suggestion="无 git 操作可执行",
            diff_summary="(no git repo)",
        )
    diff = _get_diff_vs(ref, cwd)
    if not diff.strip():
        return Decision(
            decision="allow",
            reasons=[f"vs {ref} 无 diff"],
            suggestion="无变更可检查",
            diff_summary=f"(empty diff vs {ref})",
        )
    dec = check_diff(diff, dangerous_ext=dangerous_ext, no_laya=no_laya)
    dec.diff_summary = _summarize_diff(diff)
    return dec


def _summarize_diff(diff: str) -> str:
    """生成 diff 摘要:变更 N 文件 (+X -Y)。"""
    changes = _parse_diff(diff)
    n = len(changes)
    plus = sum(c.added for c in changes)
    minus = sum(c.removed for c in changes)
    return f"变更 {n} 文件 (+{plus} -{minus})"
