"""prisir_work/agent_main_chat_hook.py — 主对话窗口接视频能力(P3j T16-A)。

定位:让主对话 LLM 在 ai_done 里输出 [[EXEC: video.create topic="X" script="Y"]]
     → 后处理扫到 → 调 agent_natural_video.execute() → 推 capability_exec_result
     回前端(WebSocket 事件)。

设计:
  · **EXEC 标记协议** — `[[EXEC: <capability_id> k1="v1" k2="v2"]]`
    - capability_id 必须是 12 capability 之一,否则忽略
    - k=v 必须是字符串字面量(LLM 应该输出这种简单 JSON 风格)
    - 多标记可同时输出(LLM 一轮可触发多个能力)
  · **风险门** — L2 / L3 不直接 exec,改推 capability_confirm_request 事件
    让前端弹确认卡 → 用户回复 capability_confirm → 真发
  · **降级而非崩溃** — 标记解析失败 / capability 不存在 / execute 失败 →
    推 capability_exec_result(ok=False, error=...) 给前端,不抛栈
  · **同步** — 主对话是 sync 上下文(没有 event loop);execute 内部已经有
    dry_run=False 同步路径,直接调

用例:
  from prisir_work.agent_main_chat_hook import scan_and_exec, push_hook_events
  events = scan_and_exec(ai_done_text)
  # → [{type: "capability_exec_result", ok: True, ...}, ...]
  for ev in events:
      await ws.send_json(ev)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import agent_natural_video as anv

__all__ = [
    "ExecMarker",
    "parse_exec_markers",
    "scan_and_exec",
    "build_confirm_request",
    "translate_exec_error",
]


# ---------------------------------------------------------------------------
# 错误翻译(P3j T17-C)— 把英文 reason 翻成人话 + 给配置链接
# ---------------------------------------------------------------------------

# 错误 → 人话 + 跳转配置链接
# 优先级:最长前缀先匹配
_TRANSLATIONS: list[tuple[str, str, str]] = [
    # (匹配 key 子串, 中文翻译, link target)
    # P3j T20-E: Agent-Reach 错误(必须装,缺则报错+引导去扩展面板安装)
    ("agent_reach_not_installed",
     "Agent-Reach 未安装(信息源覆盖不全,装上后才能读小红书/B站字幕等)",
     "/extensions"),
    ("reach_unknown_platform",
     "Agent-Reach 不支持此平台(看 🧩 扩展 列出的 14 个)",
     "/extensions"),
    ("reach_platform_disabled",
     "该信息源在 🧩 扩展 面板已关闭,去重新打开",
     "/extensions"),
    ("agent_reach_timeout",
     "Agent-Reach 子进程超时(网络或平台限速,可重试)",
     "/extensions"),
    ("Easel 未装 或 SILICONFLOW_API_KEY 未配置",
     "AI 配图/视频依赖未就绪(需 SILICONFLOW_API_KEY 或装 Easel)",
     "/media-keys"),
    ("Easel 未装", "Easel 项目根目录未找到(AI 配图/视频/合成全挂)",
     "/media-keys"),
    ("url_error",
     "视频后端未启动(wechat-publisher 子服务离线)",
     "/media-keys"),
    ("[WinError 10061]",
     "视频后端连接失败(wechat-publisher 离线)",
     "/media-keys"),
    ("missing_required",
     "必填参数缺失,EXEC 标记字段不全",
     ""),
    ("missing_fields",
     "必填参数缺失,EXEC 标记字段不全",
     ""),
    ("endpoint_not_found",
     "能力 endpoint 未注册(可能是代码版本不匹配)",
     ""),
    ("capability_not_found",
     "能力 ID 不在白名单(LLM 输出了未注册的 capability)",
     ""),
    ("ffmpeg",
     "ffmpeg 未安装,视频合成/字幕烧录无法进行",
     "/media-keys"),
    ("api_key",
     "API key 未配置或无效",
     "/media-keys"),
    ("Whisper",
     "本地 Whisper 模型未就绪(首次自动下载较慢)",
     "/media-keys"),
]


def translate_exec_error(error: str) -> dict[str, str]:
    """把 capability_exec_result.error 翻译成 {zh, hint, link}。

    Args:
        error: 原始错误字串

    Returns:
        dict 含:
            zh:    中文翻译(若无匹配返原文)
            hint:  短提示("建议配置 SILICONFLOW key")
            link:  跳转路径(空串表示无跳转;通常 "/media-keys" 引导去视频 Tab)
    """
    if not error:
        return {"zh": "", "hint": "", "link": ""}
    # 找最长匹配(避免短串误吞)
    best_zh = ""
    best_link = ""
    best_hint = ""
    best_len = 0
    for key, zh, link in _TRANSLATIONS:
        if key in error and len(key) > best_len:
            best_zh = zh
            best_link = link
            best_len = len(key)
    if best_zh:
        best_hint = "💡 前往配置"
    else:
        best_zh = error
        best_link = ""
        best_hint = ""
    return {"zh": best_zh, "hint": best_hint, "link": best_link}


log = logging.getLogger("prisir_work.main_chat_hook")


# ---------------------------------------------------------------------------
# 标记格式: [[EXEC: <capability_id> k1="v1" k2="v2"]]
# ---------------------------------------------------------------------------

# 匹配整段 EXEC 块 — capture group 1 是内部内容
_EXEC_BLOCK_RE = re.compile(r"\[\[EXEC:\s*([^\]]+?)\]\]")

# 解析内部:第一个 token 是 capability_id,剩余是 k="v" 列表
# 注意:group(2) 是带引号的完整 value,group(3)/group(4) 是引号内(但因 * 重复
# 的 capture 语义,只保留最后一个字符 → 不能用)。所以 strip 引号交给 code。
_KV_RE = re.compile(r"""(\w+)\s*=\s*("((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""")


@dataclass
class ExecMarker:
    """一个 EXEC 标记的解析结果。"""
    capability: str
    args: dict[str, str] = field(default_factory=dict)  # 全是 str(LLM 输出字面量)
    raw: str = ""  # 原始内部文本(调试用)

    def to_dict(self) -> dict[str, Any]:
        return {"capability": self.capability, "args": dict(self.args),
                "raw": self.raw}


def parse_exec_markers(text: str) -> list[ExecMarker]:
    """从 LLM 文本里抠所有 EXEC 标记。失败/空 → []。"""
    if not text:
        return []
    out: list[ExecMarker] = []
    for block in _EXEC_BLOCK_RE.findall(text):
        tokens = block.strip().split(None, 1)
        if not tokens:
            continue
        cap = tokens[0]
        rest = tokens[1] if len(tokens) > 1 else ""
        args: dict[str, str] = {}
        for m in _KV_RE.finditer(rest):
            key = m.group(1)
            # group(3) 是带引号的完整 value(重复 capture 语义不可靠,改用 group(2))
            # group(2) 是含引号的完整 value — 手动 strip
            raw = m.group(2)
            if raw.startswith('"') and raw.endswith('"'):
                v = raw[1:-1]
            elif raw.startswith("'") and raw.endswith("'"):
                v = raw[1:-1]
            else:
                v = raw
            # 处理转义(简单: \", \\, \n, \t)
            try:
                v = json.loads(f'"{v}"')
            except (json.JSONDecodeError, ValueError):
                pass
            args[key] = v
        out.append(ExecMarker(capability=cap, args=args, raw=block.strip()))
    return out


# ---------------------------------------------------------------------------
# 风险判断
# ---------------------------------------------------------------------------

def _risk_for(capability_id: str) -> str:
    """读 capability._REGISTRY 的 risk 字段。失败 → L1(中)。"""
    try:
        from . import capability as _cap
        e = _cap.get(capability_id)
        if e:
            return e.get("risk", "L1")
    except Exception:  # noqa: BLE001
        pass
    return "L1"


def _needs_confirm(risk: str) -> bool:
    """L0 不确认,L1 / L2 / L3 都确认(给用户最后机会)。"""
    return risk in ("L1", "L2", "L3")


# ---------------------------------------------------------------------------
# 构造 ws 事件
# ---------------------------------------------------------------------------

def build_confirm_request(marker: ExecMarker) -> dict[str, Any]:
    """构造 capability_confirm_request 事件(T16-C 用)。"""
    risk = _risk_for(marker.capability)
    try:
        from . import capability as _cap
        cap_entry = _cap.get(marker.capability)
        title = (cap_entry or {}).get("title", marker.capability)
        confirm_msg = (cap_entry or {}).get("confirm", "")
    except Exception:  # noqa: BLE001
        title = marker.capability
        confirm_msg = ""
    return {
        "type": "capability_confirm_request",
        "capability": marker.capability,
        "args": dict(marker.args),
        "risk": risk,
        "title": title,
        "confirm": confirm_msg,
        "raw": marker.raw,
    }


def build_exec_result(capability_id: str, ok: bool,
                       result: Optional[dict[str, Any]] = None,
                       error: str = "") -> dict[str, Any]:
    """构造 capability_exec_result 事件(主对话 WS 推送用)。

    P3j T17-C: 失败时附 zh / hint / link 字段(给前端渲染「💡 点此去配置」)。
    """
    ev: dict[str, Any] = {
        "type": "capability_exec_result",
        "capability": capability_id,
        "ok": ok,
        "result": result or {},
        "error": error,
    }
    if not ok and error:
        try:
            tr = translate_exec_error(error)
            ev["zh"] = tr["zh"]
            ev["hint"] = tr["hint"]
            ev["link"] = tr["link"]
        except Exception:  # noqa: BLE001
            pass
    return ev


# ---------------------------------------------------------------------------
# 入口:scan_and_exec(ai_text) → ws 事件 list
# ---------------------------------------------------------------------------

def scan_and_exec(ai_text: str, *, confirm_callback=None) -> list[dict[str, Any]]:
    """扫 LLM 文本里的 EXEC 标记 → 逐个跑 → 返 ws 事件 list。

    confirm_callback: 可选函数(marker: ExecMarker) → bool
      - 传 None + L1/L2/L3 → 触发 build_confirm_request 事件(前端弹卡)
      - 传函数 + 返 True → 真发(给 main dialog 一种回放确认机制)
      - 传函数 + 返 False → 不发,推 user_declined 事件

    主对话 WS 通常不传 confirm_callback — 风险一律走前端确认卡。
    """
    markers = parse_exec_markers(ai_text)
    if not markers:
        return []

    events: list[dict[str, Any]] = []
    for marker in markers:
        # 1) 校验 capability 在白名单
        try:
            from . import capability as _cap
            if not _cap.get(marker.capability):
                events.append(build_exec_result(
                    marker.capability, ok=False,
                    error=f"capability_not_found:{marker.capability}"))
                continue
        except Exception:  # noqa: BLE001
            pass

        # 2) 风险门 — L0 直接发;L1/L2/L3 走 confirm
        #    confirm_callback: 不传 → 推确认请求事件给前端
        #    传函数 → 显式用户授权,函数返 True 才发
        risk = _risk_for(marker.capability)
        if confirm_callback is not None:
            if not confirm_callback(marker):
                events.append(build_exec_result(
                    marker.capability, ok=False,
                    error="user_declined"))
                continue
        elif _needs_confirm(risk):
            # 推确认请求事件,不真发
            events.append(build_confirm_request(marker))
            continue

        # 3) 真发
        try:
            # 用 args 拼 query 文本走 execute;或者直接构造 IntentResult?
            # 走 execute(query) 会再 parse_intent 一遍,可能改 capability —
            # 不理想。直接 dispatch 到 endpoints._REGISTRY 跟 execute 同一路径
            from . import capability as _cap
            from . import endpoints as _ep
            cap_entry = _cap.get(marker.capability)
            ep_entry = _ep._REGISTRY.get(cap_entry["endpoint"])
            if not ep_entry:
                events.append(build_exec_result(
                    marker.capability, ok=False,
                    error=f"endpoint_not_found:{cap_entry['endpoint']}"))
                continue
            full_body = anv.fill_defaults(marker.capability, dict(marker.args))
            payload, _status = ep_entry["handler"](full_body)
            ok = payload.get("ok", False)
            error = "" if ok else payload.get("error", "execute_failed")
            events.append(build_exec_result(
                marker.capability, ok=ok, result=payload, error=error))
        except Exception as e:  # noqa: BLE001
            events.append(build_exec_result(
                marker.capability, ok=False,
                error=f"{type(e).__name__}: {e}"))

    return events


# ---------------------------------------------------------------------------
# 旧版:push_hook_events — 不再直接发 ws,留给上层 caller 推
# ---------------------------------------------------------------------------

def push_hook_events(ai_text: str) -> list[dict[str, Any]]:
    """薄包装:不带 confirm_callback 的 scan_and_exec。"""
    return scan_and_exec(ai_text, confirm_callback=None)