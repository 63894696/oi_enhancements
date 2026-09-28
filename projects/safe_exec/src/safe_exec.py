#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# src/safe_exec.py — M3.69 safe_exec 核心(2026-09-24)
#
# 目的:
#   - 解析 shell 命令(动词/路径/URL)→ 3 spec 联合判(safety + tempfile + disk_cleanup)
#   - 决策 policy:
#       任一 spec critical  → deny
#       safety high         → deny
#       safety medium + disk_cleanup medium → ask
#       其它               → allow
#   - **零侵入**:只 import companion 下三个 wrapper,不修改 companion 任何代码
#   - **离线可用**:纯本地 0.6B 三 adapter 推理(每 spec ~2.5s,总 ~7.5s)
#
# 设计:
#   - `_parse_command(cmd)` → {verb, paths, urls, args}
#       verb:首 token(rm/del/cp/mv/curl/wget/...)
#       paths:cmd 里出现的 Windows/Unix 路径(去重保序)
#       urls:cmd 里出现的 http(s)://...
#   - `_classify(cmd, parsed, specs)` → 3 spec 各判 + 合并 risk
#   - `_decide(...)` → allow / ask / deny
#   - `_format_text/json(result)` → 输出
#
# 安全:
#   - 模型加载失败 / 解析失败 → 默认 deny(fail-closed)
#   - admin 模式只 print 不真执行(本期)
r"""
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# 关键:把 companion/ 加入 sys.path,不修改 companion 任何代码
_COMPANION_DIR = Path(
    "C:/Users/Administrator/oi_enhancements/companion").resolve()
if str(_COMPANION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPANION_DIR))

# M3.87 P1-2:复用 PrisirAI team_lead_tools 的 endpoint 映射 + _race_one
# (不走异步,直接走同步 urllib。复用 endpoint 配置但绕开 asyncio)
_PRISIR_SERVER_DIR = Path(
    "C:/Users/Administrator/oi_enhancements/mcp_prisiragent_server").resolve()
if str(_PRISIR_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_PRISIR_SERVER_DIR))

# 关键:把 laya_guard/ 加入 sys.path,用作 fast-path
_LAYA_GUARD_DIR = Path(
    "C:/Users/Administrator/oi_enhancements/projects/laya_guard/src").resolve()
if str(_LAYA_GUARD_DIR) not in sys.path:
    sys.path.insert(0, str(_LAYA_GUARD_DIR))

# 复用现成 wrapper(零侵入)
# 注意:safety 没有现成 classify_safety.py,这里内联实现(inline _classify_safety)
from classify_tempfile import classify_tempfile        # noqa: E402
from classify_disk_cleanup import classify_one as classify_disk_cleanup_one  # noqa: E402

from adapter_registry import get_adapter              # noqa: E402

# laya_guard fast-path(M3.72 新增)
try:
    from laya_guard import guard as _laya_guard_check  # noqa: E402
    _LAYA_GUARD_OK = True
except ImportError as e:  # noqa: BLE001
    _LAYA_GUARD_OK = False
    _LAYA_GUARD_IMPORT_ERR = repr(e)


# ============================================================
# 常量
# ============================================================
VALID_RISKS = {"safe", "low", "medium", "high", "critical"}

# 风险等级数值化(决策用)
_RISK_RANK = {"safe": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


# ============================================================
# 命令解析
# ============================================================
# 已知动词(白名单 + 默认分类)
_DESTRUCTIVE_VERBS = {"rm", "del", "rmdir", "rd", "erase", "format",
                     "drop", "truncate", "shred"}
_NETWORK_VERBS = {"curl", "wget", "fetch", "http", "https", "scp", "rsync",
                 "ssh", "nc", "ncat", "telnet", "ftp"}
_FILE_WRITE_VERBS = {"cp", "copy", "mv", "move", "rename", "ren",
                    "touch", "echo", ">", ">>", "tee"}
_READ_VERBS = {"ls", "dir", "cat", "type", "more", "less", "head", "tail",
              "find", "grep", "rg", "where", "which"}
_ADMIN_VERBS = {"sudo", "runas", "net", "sc", "reg", "regedit", "bcdedit",
               "diskpart", "sfc", "dism", "takeown", "icacls", "attrib",
               "powercfg", "taskkill", "tasklist", "netstat", "systemctl",
               "service"}
_ALL_VERBS = (_DESTRUCTIVE_VERBS | _NETWORK_VERBS | _FILE_WRITE_VERBS
              | _READ_VERBS | _ADMIN_VERBS)

# URL 正则
_URL_RE = re.compile(r"(?:https?|ftp|sftp)://[^\s\"'<>]+", re.IGNORECASE)

# Windows 路径(C:\foo\bar 或 C:/foo/bar 或 UNC \\server\share)
# 允许路径段含 * 或 ? 通配符,不含 : (避免误匹配 URL 段)
# 关键:前面不能是 `://` 这种 URL 段(避免 s://evil.com 被当成盘符路径)
_WIN_PATH_RE = re.compile(
    r"""
    (?<![A-Za-z0-9_/:])                              # 前不能是字母数字/_/:(避免 URL 段)
    (?:                                             # 1. UNC 路径
        \\\\ [^\s"'<>|:]+ (?:\\ [^\s"'<>|:]+ )*
    |
        [A-Za-z]:                                   # 2. 盘符
        (?:[\\/][^\s"'<>|:]+)*                      # 路径段(含 * ?,不含 :)
    )
    """,
    re.VERBOSE,
)

# Unix 路径(/foo/bar、./foo、../foo、~/foo)
# 含通配符,但排除 URL 段(前导不是字母数字)
_UNIX_PATH_RE = re.compile(
    r"""
    (?<![A-Za-z0-9_:/])                              # 前不能是字母数字/_/:(避免 URL 段)
    (?: ~ | \. | \.\. )?                             # 可选 ~/./..
    /
    [^\s"'<>|:]*                                     # 路径内容(允许 * ?,不含 :)
    """,
    re.VERBOSE,
)


def _parse_command(cmd: str) -> dict:
    """解析 shell 命令,提取 verb / paths / urls。

    Args:
        cmd: 完整命令字符串(可能含引号、管道、选项)。

    Returns:
        dict:
          - verb: 首 token(小写),无则空
          - verb_category: destructive/network/write/read/admin/unknown
          - paths: 路径列表(去重,保序)
          - urls: URL 列表
          - args: 除首 verb 外的所有 token(粗略,空格分隔)
          - raw: 原命令
    """
    raw = cmd.strip()
    if not raw:
        return {"verb": "", "verb_category": "unknown",
                "paths": [], "urls": [], "args": [], "raw": raw}

    # 简易 tokenize(空格分隔;保留带引号段)
    tokens = _simple_tokenize(raw)
    verb = tokens[0].lower() if tokens else ""

    # 分类
    if verb in _DESTRUCTIVE_VERBS:
        category = "destructive"
    elif verb in _NETWORK_VERBS:
        category = "network"
    elif verb in _FILE_WRITE_VERBS:
        category = "write"
    elif verb in _READ_VERBS:
        category = "read"
    elif verb in _ADMIN_VERBS:
        category = "admin"
    else:
        category = "unknown"

    # 提 URL
    urls = _URL_RE.findall(raw)
    # 去尾标点(. , ; : ! ? )
    urls = [u.rstrip(".,;:!?\"'>") for u in urls]

    # 提路径(Windows + Unix)
    paths: list[str] = []
    seen: set[str] = set()

    for m in _WIN_PATH_RE.finditer(raw):
        p = m.group(0).rstrip(".,;:!?\"'>")
        # 过滤太短(单字符盘符 C: 这种不算)
        if len(p) <= 2:
            continue
        if p not in seen:
            seen.add(p)
            paths.append(p)

    for m in _UNIX_PATH_RE.finditer(raw):
        p = m.group(0).rstrip(".,;:!?\"'>")
        # 过滤 / 单独的根目录
        if p in ("/", "//"):
            continue
        # 过滤类似 // 开头的(UNC-like)
        if p.startswith("//"):
            continue
        if p not in seen:
            seen.add(p)
            paths.append(p)

    return {
        "verb": verb,
        "verb_category": category,
        "paths": paths,
        "urls": urls,
        "args": tokens[1:],
        "raw": raw,
    }


def _simple_tokenize(s: str) -> list[str]:
    """简单 tokenize:空格分隔,保留单/双引号段,去引号。"""
    tokens: list[str] = []
    cur = []
    in_quote: Optional[str] = None
    i = 0
    while i < len(s):
        c = s[i]
        if in_quote:
            if c == in_quote:
                in_quote = None
                # 闭引号后,如果 cur 非空就 flush
                if cur:
                    tokens.append("".join(cur))
                    cur = []
            else:
                cur.append(c)
        else:
            if c in ("'", '"'):
                in_quote = c
            elif c.isspace():
                if cur:
                    tokens.append("".join(cur))
                    cur = []
            else:
                cur.append(c)
        i += 1
    if cur:
        tokens.append("".join(cur))
    return tokens


# ============================================================
# M3.87 P1-2:用户当前 LLM 兜底(只对 critical 启用)
# ============================================================
# 环境变量驱动,不写死模型名:
#   SAFE_EXEC_LLM_MODEL    — model spec(默认 'm3:minimax')
#   SAFE_EXEC_LLM_TIMEOUT  — 整数秒(默认 3,critical 兜底不能拖太久)
#   SAFE_EXEC_LLM_DISABLE  — "1"/"true" 完全跳过兜底(降级到 heuristic-only)
#
# 设计原则:
#   - 只在 heuristic 报 critical 时启用(safe/low/medium/high 都不调 LLM)
#   - LLM 调用复用 PrisirAI team_lead_tools 的 ENDPOINT_MAP + _do_request 协议
#   - LLM 返 "safe"/"low"/"ok"/"allow" → 降档 heuristic 结果
#   - LLM 返 "critical"/"dangerous"/"deny"/不可信 → 维持 critical
#   - LLM timeout/fail/不可用 → 维持 critical(fail-closed,绝不静默放行)
#   - 失败时返回 None,提示 _classify_safety 走原 critical 路径
def _llm_critical_confirm(text: str, heuristic_reason: str) -> Optional[dict]:
    """critical 类 LLM 二次确认。返回:
       - dict {"verdict": "safe" | "critical", "model": ..., "raw": ...}
         当 verdict=safe 时 caller 可降档;verdict=critical 维持
       - None 表示 LLM 不可用/超时/禁用,caller 应维持原 critical(fail-closed)
    """
    if os.environ.get("SAFE_EXEC_LLM_DISABLE", "").lower() in ("1", "true", "yes"):
        return None
    if not text or not text.strip():
        return None

    model_spec = os.environ.get("SAFE_EXEC_LLM_MODEL", "m3:minimax")
    try:
        timeout_s = float(os.environ.get("SAFE_EXEC_LLM_TIMEOUT", "3"))
    except ValueError:
        timeout_s = 3.0

    # 复用 PrisirAI team_lead_tools 的 endpoint 配置
    try:
        from team_lead_tools import _resolve_endpoint, ENDPOINT_MAP  # noqa: F401
    except Exception as e:  # noqa: BLE001
        print(f"[safe_exec] LLM 兜底:team_lead_tools 不可用:{e}",
              file=sys.stderr)
        return None

    endpoint = _resolve_endpoint(model_spec)
    if "error" in endpoint:
        print(f"[safe_exec] LLM 兜底:{endpoint['error']}",
              file=sys.stderr)
        return None

    # 构造 prompt:让 LLM 当裁判,只回答 safe/critical 二选一
    prompt = (
        "You are a shell command safety adjudicator.\n"
        "The local heuristic flagged this command as critical:\n"
        f"> {text}\n"
        f"Heuristic reason: {heuristic_reason}\n\n"
        "Is the heuristic correct? Consider whether this is actually a destructive\n"
        "system-modifying command (rm -rf /, format C:, dd to disk, fork bomb,\n"
        "modifying Run keys, etc.) or a false positive (a development command that\n"
        "happened to match a generic pattern).\n\n"
        "Reply with EXACTLY one line, in this format:\n"
        "VERDICT: safe   — if the command is actually safe and the heuristic\n"
        "                  was a false positive\n"
        "VERDICT: critical — if the command is genuinely dangerous and the\n"
        "                  heuristic was correct\n"
        "Do not add any other text. Only VERDICT line."
    )

    # 同步调(复用 team_lead_tools._race_one 内的 _do_request 协议)
    try:
        protocol = endpoint["protocol"]
        base_url = endpoint["base_url"]
        api_key = endpoint["api_key"]
        model_id = endpoint["model_id"]

        if protocol == "openai":
            url = f"{base_url}/chat/completions"
            body = json.dumps({
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 32,
                "temperature": 0.0,
                "stream": False,
            }).encode()
            req = urllib.request.Request(
                url, data=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read())
                content = (data.get("choices", [{}])[0]
                           .get("message", {}).get("content", "")).strip()
        elif protocol == "anthropic":
            url = f"{base_url}/messages"
            body = json.dumps({
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 32,
            }).encode()
            req = urllib.request.Request(
                url, data=body,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read())
                content = "".join(
                    b.get("text", "")
                    for b in data.get("content", [])
                    if b.get("type") == "text"
                ).strip()
        else:
            print(f"[safe_exec] LLM 兜底:unknown protocol {protocol}",
                  file=sys.stderr)
            return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[safe_exec] LLM 兜底:network/timeout: {e}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[safe_exec] LLM 兜底失败:{type(e).__name__}: {e}",
              file=sys.stderr)
        return None

    if not content:
        return None

    # 解析 VERDICT
    text_low = content.lower()
    m = re.search(r"verdict\s*[:=]\s*(\w+)", text_low)
    if not m:
        # 兜底:内容里出现 safe 单字即视作降档
        if "safe" in text_low and "critical" not in text_low:
            return {"verdict": "safe", "model": model_spec, "raw": content}
        return {"verdict": "critical", "model": model_spec, "raw": content}

    word = m.group(1).strip().rstrip(".,;")
    if word in ("safe", "allow", "ok", "benign", "low", "medium"):
        return {"verdict": "safe", "model": model_spec, "raw": content}
    # critical / dangerous / deny / block / unsafe / high / 其它一律维持
    return {"verdict": "critical", "model": model_spec, "raw": content}


# ============================================================
# Safety 内联分类(companion/ 没有 classify_safety.py)
# ============================================================
_SAFETY_PARSE_PAT = re.compile(
    r"Safety:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"Jailbreak:\s*(\w+)(?::(\d+(?:\.\d+)?))?",
    re.IGNORECASE | re.MULTILINE,
)


def _try_load_safety_adapter():
    """尝试加载 safety LoRA adapter(M3.45 产物)。
    如果失败(本机观察到 tokenizer.json + safetensors 都损坏,M3.61 已知 bug),
    返回 None。
    """
    from adapter_registry import LoadedAdapter, ADAPTERS, _LOADED

    spec = ADAPTERS["safety"]
    if "safety" in _LOADED and _LOADED["safety"].model is not None:
        return _LOADED["safety"]

    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from peft import PeftModel

    la = LoadedAdapter(spec=spec)
    try:
        # 优先用 base tokenizer(adapter 的 tokenizer.json 可能损坏)
        la.tokenizer = AutoTokenizer.from_pretrained(
            str(spec.base_model), trust_remote_code=True)
        if la.tokenizer.pad_token is None:
            la.tokenizer.pad_token = la.tokenizer.eos_token

        base_model = AutoModelForCausalLM.from_pretrained(
            str(spec.base_model), trust_remote_code=True,
            torch_dtype=torch.float16,
            device_map="auto")
        la.model = PeftModel.from_pretrained(base_model, str(spec.adapter_path))
        la.model.eval()
        _LOADED["safety"] = la
        return la
    except Exception:  # noqa: BLE001
        return None


def _try_load_unified_head():
    """尝试加载 unified classification head(M3.66 L3,覆盖 safety/tempfile 等 8 scenario)。

    如果 unified head 已加载,直接复用;否则懒加载。
    返回 (rt, version) 或 None。
    """
    try:
        from agentjev_runtime import load_unified
        rt = load_unified()
        return rt
    except Exception:  # noqa: BLE001
        return None


# Cache unified head 加载结果
_UNIFIED_RT = None
_UNIFIED_LOAD_TRIED = False


def _classify_safety_unified(text: str) -> Optional[dict]:
    """用 unified classification head 做 safety 分类(2 类:safe/unsafe)。

    返回 None 如果 unified 加载失败。
    """
    global _UNIFIED_RT, _UNIFIED_LOAD_TRIED

    if not _UNIFIED_LOAD_TRIED:
        _UNIFIED_RT = _try_load_unified_head()
        _UNIFIED_LOAD_TRIED = True

    if _UNIFIED_RT is None:
        return None

    try:
        out = _UNIFIED_RT.infer_scenario("safety", text)
    except Exception:
        # fallback 到 module-level 函数
        try:
            from agentjev_runtime import infer_scenario
            out = infer_scenario("safety", text)
        except Exception as e:  # noqa: BLE001
            return {
                "spec": "safety", "text": text,
                "risk": "medium", "risk_conf": 0.0,
                "jailbreak": "no", "jb_conf": 0.0,
                "raw": "", "tokens": 0, "parse_fail": True,
                "error": f"unified infer 失败: {type(e).__name__}: {e}",
            }

    candidates = out.get("candidates", [])
    probs = out.get("probs", [])
    top = out.get("top", "safe")
    top_prob = out.get("top_prob", 0.0)

    # 2-class → 5-class 映射
    # safe → safe; unsafe → high(conf=top_prob)
    if top == "unsafe":
        risk = "high" if top_prob >= 0.7 else "medium"
        risk_conf = top_prob
        jb = "yes"
        jb_conf = top_prob
    else:
        risk = "safe"
        risk_conf = top_prob
        jb = "no"
        jb_conf = 1.0 - top_prob

    return {
        "spec": "safety",
        "text": text,
        "risk": risk,
        "risk_conf": risk_conf,
        "jailbreak": jb,
        "jb_conf": jb_conf,
        "raw": f"unified_head: {top}={top_prob:.3f} (candidates={candidates}, probs={probs})",
        "tokens": 0,  # unified 不返回 token 数
        "parse_fail": False,
        "backend": "unified_head",
    }


# 危险 shell 命令启发式规则(M3.69 safe_exec safety fallback)
_DANGEROUS_PATTERNS = [
    # (regex, risk, reason)
    (re.compile(r"rm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|-rf|-fr)\s+/", re.I),
     "critical", "rm -rf 根目录"),
    (re.compile(r"rm\s+-[a-z]*r[a-z]*f?\s+/(etc|usr|var|boot|sbin|bin|lib|sys)\b", re.I),
     "critical", "删除系统关键目录"),
    (re.compile(r"rm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|-rf|-fr)\s+[A-Z]:[\\/]?(Windows|Program\s*Files|ProgramData|System32)\b", re.I),
     "critical", "删除 Windows 系统目录"),
    (re.compile(r"(del|erase|rmdir|rd)\s+(/s)?\s*/?[sq]?\s*[A-Z]:[\\/]?(Windows|Program\s*Files|ProgramData|System32)\\?", re.I),
     "critical", "del/erase 系统目录"),
    (re.compile(r"format\s+[A-Z]:", re.I), "critical", "格式化磁盘"),
    (re.compile(r"dd\s+if=.*of=/dev/(sd|nvme|hd)", re.I), "critical", "dd 覆盖磁盘"),
    (re.compile(r":\(\)\s*\{.*:\|:&.*\}\s*;:", re.S), "critical", "fork bomb"),
    (re.compile(r"chmod\s+(-R\s+)?777\s+/(etc|usr|var|boot|sbin|bin)", re.I),
     "high", "chmod 777 系统目录"),
    (re.compile(r"(curl|wget)\s+.*\|\s*(bash|sh|zsh|python|perl|ruby)\b", re.I),
     "high", "下载并执行"),
    (re.compile(r"(curl|wget)\s+.*\.(exe|bat|cmd|ps1|sh|scr)\b", re.I),
     "medium", "下载可执行文件"),
    (re.compile(r"reg(?:istry)?\s+(delete|add)\s+.*\\(Run|RunOnce|CurrentVersion)", re.I),
     "high", "修改注册表 Run 项(持久化)"),
    (re.compile(r"taskkill\s+/f\s+/im\s+", re.I), "low", "强制结束进程"),
    (re.compile(r"net\s+user\s+.*\s+/add", re.I), "high", "添加用户"),
    (re.compile(r"(sc|systemctl)\s+(create|delete|stop)\s+", re.I), "medium", "服务管理"),
    (re.compile(r"powershell.*-enc(?:odedcommand)?\s+", re.I), "high", "PowerShell 编码命令"),
    (re.compile(r"del\s+/[sq]\s+[A-Z]:\\?(Windows|System32|Program Files)", re.I),
     "critical", "del /s/q 系统目录"),
]


def _classify_safety_heuristic(cmd: str) -> dict:
    """基于正则的危险 shell 命令模式识别(纯规则,无 ML)。

    用于 safety LoRA 不可用时的兜底。
    """
    matched: list[tuple[str, str]] = []
    for pat, risk, reason in _DANGEROUS_PATTERNS:
        if pat.search(cmd):
            matched.append((risk, reason))

    if not matched:
        return {
            "spec": "safety", "text": cmd,
            "risk": "safe", "risk_conf": 0.7,
            "jailbreak": "no", "jb_conf": 0.85,
            "raw": "heuristic: no dangerous pattern",
            "tokens": 0, "parse_fail": False,
            "backend": "heuristic",
        }

    # 取最严
    top_risk = "safe"
    for r, _ in matched:
        if _RISK_RANK.get(r, 0) > _RISK_RANK.get(top_risk, 0):
            top_risk = r

    top_prob = {"low": 0.6, "medium": 0.75, "high": 0.9, "critical": 0.95}.get(top_risk, 0.7)
    reasons_str = "; ".join(f"{r}({reason})" for r, reason in matched)

    return {
        "spec": "safety", "text": cmd,
        "risk": top_risk, "risk_conf": top_prob,
        "jailbreak": "yes" if top_risk in ("high", "critical") else "no",
        "jb_conf": top_prob,
        "raw": f"heuristic: {reasons_str}",
        "tokens": 0, "parse_fail": False,
        "backend": "heuristic",
    }


def _classify_safety(text: str) -> dict:
    """Safety 分类:四级 fallback。
       0. **laya_guard fast-path**(M3.72 新增)— jailbreak/injection/secrets 命中 → 直接 high
       1. safety LoRA adapter(本机 tokenizer/safetensors 损坏,通常直接失败)
       2. unified classification head(覆盖 safety 2 类,ACC ~20% 太差不实用)
       3. 纯启发式正则规则(本机主用,因为前两个本机都不可用)
       4. **M3.87 P1-2**:heuristic 判 critical → 调用户当前 LLM 兜底
          (LLM 说 safe → 降档;LLM fail/timeout → 维持 critical,fail-closed)

    全部失败 → 默认 medium + parse_fail=True。
    """
    # 0. laya_guard fast-path(M3.72)
    #    对 prompt-style 攻击敏感,jailbreak/secrets 命中立刻 high,
    #    heuristic 后续仍跑(可继续升档 critical)
    if _LAYA_GUARD_OK:
        try:
            lg = _laya_guard_check(text)
            lg_risk = lg.get("risk", "safe")
            lg_jb = lg.get("jailbreak", 0.0)
            lg_inj = lg.get("injection", 0.0)
            lg_sens = lg.get("sensitive", 0.0)
            lg_harm = lg.get("harm", 0.0)
            # laya 判定 critical/high → 直接用 laya 结果返回,跳过 heuristic
            if lg_risk in ("critical", "high"):
                return {
                    "spec": "safety", "text": text,
                    "risk": lg_risk,
                    "risk_conf": max(lg_jb, lg_inj, lg_sens, lg_harm / 3.0),
                    "jailbreak": "yes" if lg_jb >= 0.4 else "no",
                    "jb_conf": lg_jb,
                    "raw": (
                        f"laya_guard_fast_path: jb={lg_jb:.2f} inj={lg_inj:.2f} "
                        f"sens={lg_sens:.2f} harm={lg_harm:.2f} topic={lg.get('topic')!r}"
                    ),
                    "tokens": 0,  # laya 不返回 token
                    "parse_fail": lg.get("parse_fail", False),
                    "backend": "laya_guard",
                }
            # laya medium/low/safe → 让 heuristic 继续(可能升档 critical)
        except Exception:  # noqa: BLE001
            # laya 调用异常 → 静默 fallback 到下一级
            pass

    # 1. 试 LoRA(本机已损坏)
    try:
        adapter = _try_load_safety_adapter()
        if adapter is not None:
            res = adapter.classify(text, max_new_tokens=40)
            raw = res["raw"]
            risk = "unknown"
            risk_conf = 0.0
            jb = False
            jb_conf = 0.0

            idx = raw.lower().find("safety:")
            if idx >= 0:
                seg = raw[idx:]
                second = seg.lower().find("safety:", 8)
                if second > 0:
                    seg = seg[:second]
                m = _SAFETY_PARSE_PAT.search(seg)
                if m:
                    risk = m.group(1).lower().strip()
                    if m.group(2):
                        try:
                            risk_conf = float(m.group(2))
                        except ValueError:
                            pass
                    jb = m.group(3).lower().strip() in ("yes", "true", "1")
                    if m.group(4):
                        try:
                            jb_conf = float(m.group(4))
                        except ValueError:
                            pass

            if risk in VALID_RISKS:
                return {
                    "spec": "safety", "text": text,
                    "risk": risk, "risk_conf": risk_conf,
                    "jailbreak": "yes" if jb else "no", "jb_conf": jb_conf,
                    "raw": raw, "tokens": res["tokens"],
                    "parse_fail": False, "backend": "safety_lora",
                }
            # parse 失败 → fallback 到下一级
    except Exception:  # noqa: BLE001
        pass

    # 2. 启发式(M3.69 主用,因为本机 safety LoRA 损坏,
    #    unified head 把所有 shell 命令都判 unsafe 太激进,启发式更精准)
    h_res = _classify_safety_heuristic(text)

    # M3.87 P1-2:heuristic 报 critical 时,调用户当前 LLM 二次确认。
    # 理由:本机 heuristic 是纯正则,误判率高(尤其 'rm -rf' 任意路径),
    # 但 LoRA/unified 都不可用。LLM 兜底只用于 critical(慢路径)避免拖快路径。
    # LLM 说 safe → 降档;LLM fail/timeout → 维持 critical(fail-closed)。
    if h_res.get("risk") == "critical":
        confirm = _llm_critical_confirm(text, h_res.get("raw", ""))
        if confirm and confirm.get("verdict") == "safe":
            # LLM 兜底说安全 → 降档到 high(不再 critical,但仍让用户确认)
            # 用 high 而非 medium 因为 heuristic 命中说明命令本来就有可疑特征
            h_res = {**h_res,
                     "risk": "high",
                     "risk_conf": 0.6,  # 兜底后置信度比纯 heuristic 低
                     "raw": (h_res.get("raw", "")
                             + f"\nllm_override: {confirm.get('model')} → safe "
                             f"(raw: {confirm.get('raw', '')[:100]})"),
                     "backend": "heuristic+llm_override",
                     "llm_confirm": confirm,
                     }
        else:
            # LLM 不可用 / 维持 critical — 在 raw 里标注已尝试兜底
            h_res = {**h_res,
                     "llm_confirm": confirm,
                     "raw": (h_res.get("raw", "")
                             + f"\nllm_confirm: "
                             f"{'maintained critical (LLM unavailable)' if not confirm else 'maintained critical (LLM agrees)'}"),
                     }
    return h_res


# ============================================================
# 3 spec 联合分类
# ============================================================
def _classify_safety_cmd(cmd: str) -> dict:
    """对整条命令做 safety 分类(把命令当 user 消息送给 safety adapter)。"""
    return _classify_safety(cmd)


# disk_cleanup spec 设计意图:Windows 系统路径(WinSxS/Prefetch/Logs/Installer 等)
# 用户级路径(D:/Temp、D:/Downloads)不应送过去,会被误判。
# 严格匹配:必须出现 Win 系统目录标志(不要单 Temp 太泛)
_DISK_CLEANUP_PATH_RE = re.compile(
    r"(?i)(?:^|[\\/])("
    r"C:[\\/]+Windows|C:[\\/]+Program\s*Files(?:\s*\(x86\))?|"
    r"C:[\\/]+ProgramData|C:[\\/]+Users[\\/]+[^\\/]+[\\/]+AppData|"
    r"Windows|Program\s*Files|Program\s*Files\s*\(x86\)|ProgramData|"
    r"System32|SysWOW64|WinSxS|Prefetch|Installer|"
    r"AppData[\\/]+Local[\\/]+(?:Microsoft|Windows|Temp)|"
    r"drivers|DriverStore|Recovery|PerfLogs|"
    r"\\\\Windows\\\\|\\\\Program\\s*Files"
    r")(?:[\\/]|$)",
    re.IGNORECASE,
)


def _is_system_path(p: str) -> bool:
    """判断路径是否是 Windows 系统路径(用 disk_cleanup 才有意义)。

    严格规则:
      - 必须有 C:\\Windows / C:\\Program Files / C:\\ProgramData 等系统盘路径前缀
      - 或含 \\Windows\\ / \\Program Files\\ 这种 UNC-like 段
      - 或含 WinSxS/Prefetch/Installer/System32 这种系统子目录
      - 或 C:\\Users\\<u>\\AppData 子路径(系统级 AppData,非用户文档)

    反例(不是系统路径,不送 disk_cleanup):
      - D:/Temp、D:/Downloads、E:/projects、/tmp/cache.log
    """
    return bool(_DISK_CLEANUP_PATH_RE.search(p))


def _classify_tempfile_paths(paths: list[str]) -> dict:
    """对 paths 里每条做 tempfile 分类,合并成最严的一档。"""
    if not paths:
        return {"spec": "tempfile", "applied": False,
                "max_risk": None, "max_risk_conf": 0.0,
                "results": [], "parse_fail": False}

    adapter = get_adapter("tempfile_conf")
    results = []
    max_risk = "safe"
    max_conf = 0.0
    parse_fail = False
    for p in paths:
        try:
            # tempfile_conf 期望输入是 "路径|大小|年龄|git" 格式
            # 我们只拿到路径,补占位字段,让 adapter 有上下文但不过拟合
            text = f"{p}|未知|未知|未知"
            r = classify_tempfile(adapter, text, use_conf=True)
            results.append({"path": p, "risk": r["risk"],
                            "risk_conf": r.get("risk_conf", 0.0),
                            "action": r.get("action")})
            # 风险升级
            rk = r.get("risk") or "medium"
            if rk not in VALID_RISKS:
                rk = "medium"
                parse_fail = True
            if _RISK_RANK.get(rk, 0) > _RISK_RANK.get(max_risk, 0):
                max_risk = rk
                max_conf = r.get("risk_conf") or 0.0
        except Exception as e:  # noqa: BLE001
            parse_fail = True
            results.append({"path": p, "error": f"{type(e).__name__}: {e}"})

    return {"spec": "tempfile", "applied": True,
            "max_risk": max_risk, "max_risk_conf": max_conf,
            "results": results, "parse_fail": parse_fail}


def _classify_disk_cleanup_paths(paths: list[str]) -> dict:
    """对 paths 里每条做 disk_cleanup 分类,合并成最严的一档。

    M3.69 过滤:只把 Windows 系统路径送 disk_cleanup LoRA
    (它训练集是 WinSxS/Prefetch/Logs/Installer 等,用户路径会误判)。
    非系统路径跳过 → applied 保持 False,decision 不被它影响。
    """
    # 过滤:只保留系统路径
    sys_paths = [p for p in paths if _is_system_path(p)]
    if not sys_paths:
        return {"spec": "disk_cleanup", "applied": False,
                "max_risk": None, "max_risk_conf": 0.0,
                "results": [], "parse_fail": False,
                "skipped": [p for p in paths],
                "skip_reason": "non-system-path (disk_cleanup 只判 Windows 系统路径)"}

    adapter = get_adapter("disk_cleanup_conf")
    results = []
    max_risk = "safe"
    max_conf = 0.0
    parse_fail = False
    for p in sys_paths:
        try:
            r = classify_disk_cleanup_one(adapter, p)
            results.append({"path": p, "risk": r["risk"],
                            "risk_conf": r.get("risk_conf", 0.0),
                            "action": r.get("action")})
            rk = r.get("risk") or "medium"
            if rk not in VALID_RISKS:
                rk = "medium"
                parse_fail = True
            if _RISK_RANK.get(rk, 0) > _RISK_RANK.get(max_risk, 0):
                max_risk = rk
                max_conf = r.get("risk_conf") or 0.0
        except Exception as e:  # noqa: BLE001
            parse_fail = True
            results.append({"path": p, "error": f"{type(e).__name__}: {e}"})

    return {"spec": "disk_cleanup", "applied": True,
            "max_risk": max_risk, "max_risk_conf": max_conf,
            "results": results, "parse_fail": parse_fail,
            "skipped": [p for p in paths if p not in sys_paths]}


def _classify(cmd: str, parsed: dict) -> dict:
    """3 spec 联合分类。

    Returns:
        dict:
          - input: 原命令
          - parsed: _parse_command 输出
          - safety: dict(risk, risk_conf, jailbreak, ...)
          - tempfile: dict(max_risk, max_risk_conf, results, applied, ...)
          - disk_cleanup: dict(max_risk, max_risk_conf, results, applied, ...)
          - latency_sec
          - parse_fail_count
          - tokens
    """
    t0 = time.time()
    parse_fail_count = 0
    tokens = 0

    # 1. safety(整条命令)
    try:
        safety = _classify_safety_cmd(cmd)
        if safety.get("parse_fail"):
            parse_fail_count += 1
        tokens += safety.get("tokens", 0)
    except Exception as e:  # noqa: BLE001
        safety = {"spec": "safety", "risk": "critical", "risk_conf": 0.0,
                  "jailbreak": "no", "jb_conf": 0.0, "raw": "",
                  "tokens": 0, "parse_fail": True,
                  "error": f"{type(e).__name__}: {e}"}
        parse_fail_count += 1

    # 2. tempfile(逐 path)
    try:
        tempfile_res = _classify_tempfile_paths(parsed["paths"])
        if tempfile_res.get("parse_fail"):
            parse_fail_count += 1
    except Exception as e:  # noqa: BLE001
        tempfile_res = {"spec": "tempfile", "applied": False,
                        "max_risk": "medium", "max_risk_conf": 0.0,
                        "results": [], "parse_fail": True,
                        "error": f"{type(e).__name__}: {e}"}
        parse_fail_count += 1

    # 3. disk_cleanup(逐 path)
    try:
        disk_res = _classify_disk_cleanup_paths(parsed["paths"])
        if disk_res.get("parse_fail"):
            parse_fail_count += 1
    except Exception as e:  # noqa: BLE001
        disk_res = {"spec": "disk_cleanup", "applied": False,
                    "max_risk": "medium", "max_risk_conf": 0.0,
                    "results": [], "parse_fail": True,
                    "error": f"{type(e).__name__}: {e}"}
        parse_fail_count += 1

    elapsed = round(time.time() - t0, 2)

    return {
        "input": cmd,
        "parsed": parsed,
        "safety": safety,
        "tempfile": tempfile_res,
        "disk_cleanup": disk_res,
        "latency_sec": elapsed,
        "parse_fail_count": parse_fail_count,
        "tokens": tokens,
    }


# ============================================================
# 决策 policy
# ============================================================
def _decide(classified: dict) -> dict:
    """3 spec 合并 risk → allow / ask / deny。

    决策树:
        任一 spec critical  → deny
        任一 spec high     → deny
        safety medium + (disk_cleanup medium 或 tempfile medium) → ask
        safety medium 但其它都 ≤ low → allow
        其它               → allow

    Args:
        classified: _classify 输出

    Returns:
        dict: {decision: "allow"|"ask"|"deny", reason: str, ...}
    """
    s = classified["safety"].get("risk") or "medium"
    t = classified["tempfile"].get("max_risk") or "safe"
    d = classified["disk_cleanup"].get("max_risk") or "safe"
    s_rank = _RISK_RANK.get(s, 2)
    t_rank = _RISK_RANK.get(t, 0) if classified["tempfile"].get("applied") else 0
    d_rank = _RISK_RANK.get(d, 0) if classified["disk_cleanup"].get("applied") else 0

    reasons: list[str] = []

    # critical 优先(任一 → deny)
    if s_rank >= 4:
        reasons.append(f"safety=critical ({classified['safety'].get('risk_conf', 0):.2f})")
    if t_rank >= 4:
        reasons.append(f"tempfile=critical ({classified['tempfile'].get('max_risk_conf', 0):.2f})")
    if d_rank >= 4:
        reasons.append(f"disk_cleanup=critical ({classified['disk_cleanup'].get('max_risk_conf', 0):.2f})")

    if reasons:
        return {"decision": "deny",
                "reason": "; ".join(reasons),
                "policy": "rule:any-critical",
                "exit_code": 1}

    # high(任一 → deny)
    high_reasons: list[str] = []
    if s_rank >= 3:
        high_reasons.append(f"safety=high ({classified['safety'].get('risk_conf', 0):.2f})")
    if t_rank >= 3:
        high_reasons.append(f"tempfile=high ({classified['tempfile'].get('max_risk_conf', 0):.2f})")
    if d_rank >= 3:
        high_reasons.append(f"disk_cleanup=high ({classified['disk_cleanup'].get('max_risk_conf', 0):.2f})")
    if high_reasons:
        return {"decision": "deny",
                "reason": "; ".join(high_reasons),
                "policy": "rule:any-high",
                "exit_code": 1}

    # medium:安全中等 + 系统路径/临时文件也中等 → ask
    if s_rank >= 2:
        if d_rank >= 2:
            return {"decision": "ask",
                    "reason": f"safety=medium + disk_cleanup=medium "
                              f"({classified['safety'].get('risk_conf', 0):.2f}/"
                              f"{classified['disk_cleanup'].get('max_risk_conf', 0):.2f})",
                    "policy": "rule:safety-medium+disk-medium",
                    "exit_code": 0}
        if t_rank >= 2:
            return {"decision": "ask",
                    "reason": f"safety=medium + tempfile=medium "
                              f"({classified['safety'].get('risk_conf', 0):.2f}/"
                              f"{classified['tempfile'].get('max_risk_conf', 0):.2f})",
                    "policy": "rule:safety-medium+tempfile-medium",
                    "exit_code": 0}
        # safety medium 但磁盘/临时都低 → allow
        return {"decision": "allow",
                "reason": f"safety=medium 但 disk_cleanup={d}, tempfile={t} 都 ≤ low → allow",
                "policy": "rule:safety-medium-isolated",
                "exit_code": 0}

    # 全部 ≤ low
    return {"decision": "allow",
            "reason": f"无 critical/high 且 safety ≤ low(safety={s}, tempfile={t}, disk={d})",
            "policy": "rule:default-allow",
            "exit_code": 0}


# ============================================================
# 主入口
# ============================================================
def check(cmd: str) -> dict:
    """单条命令全流程:parse + classify + decide。

    Returns:
        dict 包含:
          - input / parsed / classified(safety/tempfile/disk_cleanup)
          - decision(allow/ask/deny)
          - reason / policy / exit_code
          - latency_sec / tokens / parse_fail_count
    """
    parsed = _parse_command(cmd)
    classified = _classify(cmd, parsed)
    decision = _decide(classified)
    return {
        "input": cmd,
        "parsed": parsed,
        "classified": classified,
        "decision": decision,
        "latency_sec": classified["latency_sec"],
        "tokens": classified["tokens"],
        "parse_fail_count": classified["parse_fail_count"],
    }


# ============================================================
# 输出格式化
# ============================================================
_RISK_EMOJI = {
    "safe": "  ",
    "low": "  ",
    "medium": "  ",
    "high": "  ",
    "critical": "  ",
}


def format_text(result: dict) -> str:
    """人类可读文本格式(默认 CLI 输出)。"""
    lines = []
    p = result["parsed"]
    c = result["classified"]
    d = result["decision"]

    lines.append(f"命令: {result['input']}")
    lines.append("")
    lines.append(f"解析: verb={p['verb']!r} ({p['verb_category']})")
    if p["paths"]:
        lines.append(f"      paths({len(p['paths'])}):")
        for path in p["paths"]:
            lines.append(f"        - {path}")
    else:
        lines.append(f"      paths: (无)")
    if p["urls"]:
        lines.append(f"      urls({len(p['urls'])}):")
        for url in p["urls"]:
            lines.append(f"        - {url}")
    else:
        lines.append(f"      urls: (无)")
    lines.append("")

    # safety
    s = c["safety"]
    lines.append(f"[safety]       {s['risk']}:{s.get('risk_conf', 0):.2f}  "
                 f"→ {s['risk']}"
                 + ("  [jb]" if s.get("jailbreak") == "yes" else ""))
    # tempfile
    t = c["tempfile"]
    if t.get("applied"):
        lines.append(f"[tempfile]     {t['max_risk']}:{t.get('max_risk_conf', 0):.2f}  "
                     f"({len(t.get('results', []))} 路径)")
    else:
        lines.append(f"[tempfile]     0 路径涉及")
    # disk_cleanup
    dc = c["disk_cleanup"]
    if dc.get("applied"):
        lines.append(f"[disk_cleanup] {dc['max_risk']}:{dc.get('max_risk_conf', 0):.2f}  "
                     f"({len(dc.get('results', []))} 路径)")
    else:
        lines.append(f"[disk_cleanup] 0 路径涉及")

    lines.append("")

    # 决策
    if d["decision"] == "allow":
        marker = "→决策: allow  ← 不拦截"
    elif d["decision"] == "ask":
        marker = "→决策: ask  ← 需用户确认"
    else:  # deny
        marker = "→决策: deny  ← 拦截"
    lines.append(marker)
    lines.append(f"  原因: {d['reason']}")
    lines.append(f"  策略: {d['policy']}")
    lines.append(f"  退出码: {d['exit_code']}")
    lines.append("")
    lines.append(f"(延迟 {result['latency_sec']}s, "
                 f"{result['tokens']} tokens, "
                 f"parse_fail={result['parse_fail_count']})")
    return "\n".join(lines)


def format_json(result: dict) -> str:
    """JSON 格式(机器可读)。"""
    return json.dumps(result, ensure_ascii=False, indent=2)


# ============================================================
# 扫描脚本模式
# ============================================================
def scan_lines(lines: list[str]) -> list[dict]:
    """批量扫多行命令,每行返回 check() 结果。
    空行 / 注释(#)跳过。"""
    out = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            r = check(line)
            out.append(r)
        except Exception as e:  # noqa: BLE001
            out.append({
                "input": line,
                "decision": {"decision": "deny",
                             "reason": f"check() 异常: {type(e).__name__}: {e}",
                             "policy": "rule:exception-fail-closed",
                             "exit_code": 1},
                "latency_sec": 0.0,
                "tokens": 0,
                "parse_fail_count": 1,
                "error": True,
            })
    return out
