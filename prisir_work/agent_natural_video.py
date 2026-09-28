"""prisir_work/agent_natural_video.py — 自然语言 → 视频/YouTube 能力路由(P3j T14-C)。

定位:主对话 LLM 看不懂 creator/op/endpoint 这种技术命名,但懂人话 —
本模块提供:

  · parse_intent(query) → 命中哪个能力 + 缺哪些必填参数
  · fill_defaults(capability_id, partial_body) → 用 sensible defaults 补全
  · execute(query, *, dry_run=True, confirm_callback=None)
      → 全流程:意图解析 → 参数补全 → (确认) → execute
  · intent_summary() → 用户聊天时一段话介绍可做什么

设计原则:
  · **降级而非崩溃** — 解析失败 → ok=False + reason,不抛栈
  · **confirm_callback 可选** — LLM/扩展侧若传,真执行前调用;
    传 None → 默认 dry_run=True(只 parse + fill,不真发)
  · **不替用户拍板** — 缺必填参数(如 video path)返 reason,不瞎猜

用例:
  from prisir_work.agent_natural_video import parse_intent, fill_defaults, execute
  r = parse_intent("帮我做个 9:16 短视频,主题是 AI 改变办公")
  print(r)  # → {capability: "video.create", args: {topic:..., script:...}, missing: [...]}
  body = fill_defaults("video.create", r["args"])
  result = execute("帮我做个 9:16 短视频,主题是 AI 改变办公", confirm_callback=lambda title: True)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

__all__ = [
    "IntentResult",
    "parse_intent",
    "fill_defaults",
    "execute",
    "intent_summary",
]


# ---------------------------------------------------------------------------
# Intent 数据类
# ---------------------------------------------------------------------------

@dataclass
class IntentResult:
    """自然语言意图解析结果。"""
    ok: bool
    capability: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    confidence: float = 0.0  # 0..1
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "capability": self.capability,
            "args": self.args,
            "missing": self.missing,
            "confidence": round(self.confidence, 3),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# 关键词→能力 映射(降级于 capability.search;此处加 LLM-friendly 抽取)
# ---------------------------------------------------------------------------

# 优先级序:更具体的排前(让 "上传到 youtube" 命中 youtube.upload 而非 video.upload)
_INTENT_PATTERNS: list[tuple[str, str, list[str]]] = [
    # (pattern_re, capability_id, list_of_capturing_groups → args)
    # 排序原则:更具体的必须在前(YouTube > 视频创作 > 字幕烧录 > 裁剪/ASR > TTS)
    # YouTube 上传(最优先) — 接受 "上传 / 发 / 推 / 投" + 间隔 + youtube / 油管
    (r"(?:上传|发|推|投).{0,15}(?:youtube|油管)|(?:youtube|油管).{0,8}(?:上传|发|推|投稿|投)|youtube.{0,3}upload",
     "youtube.upload", []),
    # YouTube 列表 — "我 YouTube 视频" / "我的 youtube 视频" / "我 YouTube 有什么"
    (r"我.{0,3}youtube|我的.{0,3}(?:youtube|油管)|(?:youtube|油管).{0,6}(?:视频|频道).{0,6}(?:列|有什么|都有)|my.{0,3}youtube|list.{0,3}my.{0,3}youtube",
     "youtube.list", []),
    # YouTube 状态
    (r"(?:youtube|油管).{0,3}(?:状态|装没装|配置|授权没|ready|准备好了|能用吗)|youtube.{0,3}status",
     "youtube.status", []),
    # 视频创作 — 必须宽松匹配 "帮我做个 9:16 短视频" 等中间有数字/比例的情况
    (r"做.{0,12}视频|出.{0,12}片|生成.{0,3}视频|一键.{0,3}(?:视频|出片)|做个?视频|出片|拍视频|帮我做.{0,3}短视频",
     "video.create", []),
    # 字幕烧录(必须在 cut/asr 前面 — "烧字幕" 不能命中 asr)
    (r"烧.{0,6}(?:字幕|硬字幕|软字幕)|字幕.{0,3}(?:烧|嵌)|burn.{0,3}subtitle|硬字幕|软字幕",
     "video.burn", []),
    # 裁剪 — "从 00:10 裁到 00:30" 也要命中
    (r"裁.{0,3}到|从.{0,8}(?:裁|切|剪|截取).{0,8}到|截.{0,6}视频|剪.{0,6}(?:视频|片段)|cut.{0,3}(?:video|视频)|trim.{0,3}(?:video|视频)",
     "video.cut", []),
    # TTS / 配音
    (r"配.{0,2}音|朗读|念.{0,3}出来|tts|text.{0,3}to.{0,3}speech|文字.{0,3}转.{0,3}语音",
     "video.tts", []),
    # ASR / 字幕识别(必须在 burn 后面,且避开"烧")
    (r"自动.{0,3}字幕|出字幕|加字幕|字幕识别|听写|转写|字幕.{0,3}识别|asr",
     "video.asr", []),
    # BGM
    (r"加.{0,3}(?:背景音乐|配乐|bgm|音乐|歌)|配乐|bgm",
     "video.bgm", []),
    # 视频信息 — 宽松:含 .mp4/.mov/.avi 等视频扩展名 + "查/看" 动词也可命中
    (r"视频.{0,3}(?:信息|元数据|多大|多长|分辨率|参数|属性)|ffprobe|video.{0,3}info|info.{0,3}video|什么.{0,3}视频|(?:查|看).{0,15}(?:\.mp4|\.mov|\.avi|\.mkv)",
     "video.info", []),
    # 数据分析
    (r"发布.{0,3}(?:数据|分析|统计)|最佳.{0,3}时段|标签.{0,3}效果|增长.{0,3}归因|数据回收|analyz",
     "video.analyze", []),
]


def parse_intent(query: str) -> IntentResult:
    """自然语言 → 命中能力 + 抽取参数(轻量 regex,够用,不替 LLM)。

    流程:
      1) 在 _INTENT_PATTERNS 里顺序匹配 → 命中第一个 capability
      2) 调 _extract_args(capability, query) → 抽取文件路径/时间等
      3) 缺必填 → missing 列出(不返错,留给 fill_defaults/execute 决定)
    """
    if not query or not query.strip():
        return IntentResult(ok=False, error="empty_query")
    q = query.strip()

    # 1) 关键词匹配
    for pat, cap, _ in _INTENT_PATTERNS:
        if re.search(pat, q, re.IGNORECASE):
            args = _extract_args(cap, q)
            missing = _check_required(cap, args)
            # 0..1 confidence:有命中 + 无 missing → 0.9;有 missing → 0.7;只命中不匹配的 → 0.5
            conf = 0.9 if not missing else 0.7
            return IntentResult(
                ok=True, capability=cap, args=args,
                missing=missing, confidence=conf)

    # 2) 没命中 — 兜底调 capability.search(query)
    try:
        from . import capability
        hits = capability.search(q)
        # 命中且有 title 中含 "video"/"youtube" 的优先
        video_hits = [h for h in hits if h["id"].startswith("video.")
                       or h["id"].startswith("youtube.")]
        if video_hits:
            return IntentResult(
                ok=True, capability=video_hits[0]["id"],
                args=_extract_args(video_hits[0]["id"], q),
                missing=_check_required(video_hits[0]["id"],
                                        _extract_args(video_hits[0]["id"], q)),
                confidence=0.5,
                error="fallback_capability_search",
            )
    except Exception:  # noqa: BLE001
        pass

    # 3) 还不行 — 调 LLM 增强意图抽取(P3j T15-B)
    # 失败 / 超时 / 不可用 → 返 None,不替 regex 拍板,继续走 not-recognized 兜底
    try:
        from . import agent_llm_enhancer as llm_enh
        enhanced = llm_enh.enhance_with_llm(q, timeout=20.0)
        if enhanced and enhanced.ok:
            return enhanced
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).debug("LLM enhance failed: %s", e)

    return IntentResult(
        ok=False,
        error=f"未识别意图:{q[:50]}…(试试 '做个 9:16 视频' / '上传 YouTube' / '加字幕')")


# ---------------------------------------------------------------------------
# 参数抽取(轻量 regex)
# ---------------------------------------------------------------------------

# Windows / Linux / macOS 路径:C:/xx 或 C:\\xx 或 /Users/xx
_PATH_RE = re.compile(
    r"([A-Za-z]:[\\\\/][^\s,，]+|[~/][^\s,，]+)")

# 时间:HH:MM:SS 或 MM:SS 或 秒数 / 30s / 1m
_TIME_RE = re.compile(
    r"(\d{1,2}:\d{1,2}(?::\d{1,2})?|\d+\s*(?:秒|s|分|m|分种))",
    re.IGNORECASE)

# 9:16 / 16:9 / 1:1
_ASPECT_RE = re.compile(r"(\d+:\d+)")


def _extract_args(capability: str, query: str) -> dict[str, Any]:
    """从 query 里轻量抽参数。"""
    out: dict[str, Any] = {}

    # 路径
    paths = _PATH_RE.findall(query)
    if paths:
        if capability in ("video.cut", "video.info", "video.bgm",
                          "video.asr", "video.burn"):
            out["input"] = paths[0]
            if capability == "video.info":
                # video.info endpoint 收 path 字段
                out["path"] = paths[0]
            if len(paths) >= 2 and capability != "video.info":
                out["output"] = paths[1]
        elif capability in ("youtube.upload",):
            out["video"] = paths[0]
        elif capability == "video.tts":
            # TTS 可以接 file,但用户更多直接给 text
            if paths:
                # 区分扩展名:.txt → file;否则忽略
                p = paths[0]
                if p.lower().endswith((".txt", ".md")):
                    out["file"] = p
        # video.create 不需要路径

    # 时间(cut / burn 子命令的 start/end)
    times = _TIME_RE.findall(query)
    if capability == "video.cut" and times:
        # 默认顺序:start, end
        if "start" not in out:
            out["start"] = times[0]
        if "end" not in out and len(times) >= 2:
            out["end"] = times[1]

    # 画幅
    aspect = _ASPECT_RE.findall(query)
    if capability == "video.create":
        for a in aspect:
            if a in ("9:16", "16:9", "1:1"):
                out["aspect_ratio"] = a
                break

    # topic / script 关键词(video.create)
    if capability == "video.create":
        m = re.search(r"主题[是为]?\s*[\"「]?([^\"」\n,，。]{2,40})", query)
        if m:
            out["topic"] = m.group(1).strip()
        # "文案是..." / "内容是..." / "脚本是..."
        m = re.search(
            r"(?:文案|内容|脚本|口播|稿子)[是为]?\s*[\"「]?([^\"」\n]{2,200})",
            query)
        if m:
            out["script"] = m.group(1).strip()

    # YouTube 上传:privacy
    if capability == "youtube.upload":
        if re.search(r"\b(?:public|公开)\b", query, re.IGNORECASE):
            out["privacy"] = "public"
        elif re.search(r"\b(?:unlisted|不公开|链接)\b", query, re.IGNORECASE):
            out["privacy"] = "unlisted"
        elif re.search(r"\b(?:private|私密|自己)\b", query, re.IGNORECASE):
            out["privacy"] = "private"

    # 数据分析 mode
    if capability == "video.analyze":
        if re.search(r"最佳.{0,3}时段|时间|when", query, re.IGNORECASE):
            out["mode"] = "time"
        elif re.search(r"标签.{0,3}效果|tag", query, re.IGNORECASE):
            out["mode"] = "tags"
        elif re.search(r"类型|content.{0,3}type|types", query, re.IGNORECASE):
            out["mode"] = "types"
        elif re.search(r"增长|growth|粉丝.{0,3}涨", query, re.IGNORECASE):
            out["mode"] = "growth"

    # burn:字幕路径(query 例 "把 C:/a.srt 字幕烧到 C:/v.mp4 输出 C:/out.mp4")
    # — paths 顺序可能 (srt, 视频, 输出) 或 (视频, srt, 输出);按扩展名判
    if capability == "video.burn" and len(paths) >= 2:
        exts = {".srt", ".vtt", ".ass", ".ssa"}
        if paths[0].lower().endswith(tuple(exts)):
            out["sub"] = paths[0]
            if len(paths) >= 2:
                out["input"] = paths[1]
            if len(paths) >= 3:
                out["output"] = paths[2]
        else:
            out["input"] = paths[0]
            out["sub"] = paths[1]
            if len(paths) >= 3:
                out["output"] = paths[2]
        if "soft" in query or "软字幕" in query:
            out["soft"] = True
        elif "硬字幕" in query:
            out["soft"] = False

    return out


# ---------------------------------------------------------------------------
# 必填参数清单
# ---------------------------------------------------------------------------

_REQUIRED: dict[str, list[str]] = {
    "video.create": ["topic", "script"],
    "video.tts": ["text", "output"],   # text / file 二选一
    "video.asr": ["input"],
    "video.cut": ["input", "output"],
    "video.bgm": ["input", "output", "music"],
    "video.burn": ["input", "sub", "output"],
    "video.info": ["path"],
    "video.analyze": [],
    "video.list": [],
    "youtube.upload": ["video", "title"],
    "youtube.list": [],
    "youtube.status": [],
}


def _check_required(capability: str, args: dict[str, Any]) -> list[str]:
    """返缺失的必填参数。TTS 允许 text 或 file。"""
    req = _REQUIRED.get(capability, [])
    missing = []
    if capability == "video.tts":
        # text/file 二选一
        if not (args.get("text") or args.get("file")):
            missing.append("text_or_file")
        if not args.get("output"):
            missing.append("output")
        return missing
    for k in req:
        if not args.get(k):
            missing.append(k)
    return missing


# ---------------------------------------------------------------------------
# Defaults 补全
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, dict[str, Any]] = {
    "video.create": {
        "aspect_ratio": "9:16",
        "duration": 60,
        "with_subtitle": True,
        "with_images": False,
    },
    "video.tts": {
        "voice": "zh-CN-YunxiNeural",
        "output": "/tmp/tts.mp3",
    },
    "video.asr": {
        "model": "base",
        "format": "srt",
    },
    "video.bgm": {
        "volume": 0.3,
    },
    "video.analyze": {
        "mode": "selftest",
    },
    "youtube.upload": {
        "privacy": "private",
        "category_id": "22",
        "exec_real": False,
    },
}


def fill_defaults(capability: str, partial: dict[str, Any]) -> dict[str, Any]:
    """用 _DEFAULTS 补全缺失字段(不替用户拍必填)。"""
    out = dict(partial or {})
    for k, v in _DEFAULTS.get(capability, {}).items():
        out.setdefault(k, v)
    return out


# ---------------------------------------------------------------------------
# 端到端 execute(query)
# ---------------------------------------------------------------------------

@dataclass
class ExecuteResult:
    """execute() 的统一返回。"""
    ok: bool
    intent: IntentResult = field(default_factory=IntentResult)
    body: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "intent": self.intent.to_dict(),
            "body": self.body,
            "result": self.result,
            "error": self.error,
        }


def execute(query: str, *, dry_run: bool = True,
            confirm_callback: Optional[Callable[[IntentResult], bool]] = None
            ) -> ExecuteResult:
    """自然语言 → 全流程:parse → fill_defaults → (confirm) → execute。

    dry_run=True(默认)→ 仅解析,不真发,返完整 intent + 补全后的 body 供上层确认。
    confirm_callback: 传函数,真发前调用,返 True 才发,False 取消。
                      传 None + dry_run=False → 直接发(不推荐,生产用 confirm)
    """
    intent = parse_intent(query)
    if not intent.ok:
        return ExecuteResult(ok=False, intent=intent, error=intent.error)

    body = fill_defaults(intent.capability, intent.args)

    if dry_run:
        return ExecuteResult(
            ok=True, intent=intent, body=body,
            error="dry_run_dry_run_only",
            result={"preview": True})

    # 真发
    if confirm_callback and not confirm_callback(intent):
        return ExecuteResult(ok=False, intent=intent, body=body,
                             error="user_declined")

    try:
        from . import capability as _cap
        cap_entry = _cap.get(intent.capability)
        if not cap_entry:
            return ExecuteResult(ok=False, intent=intent, body=body,
                                 error=f"capability_missing:{intent.capability}")
        # 调 execute:通过 HTTP 走端口代理 — 走 endpoints._REGISTRY[endpoint].handler
        from . import endpoints as _ep
        ep_entry = _ep._REGISTRY.get(cap_entry["endpoint"])
        if not ep_entry:
            return ExecuteResult(ok=False, intent=intent, body=body,
                                 error=f"endpoint_missing:{cap_entry['endpoint']}")
        payload, http_status = ep_entry["handler"](body)
        return ExecuteResult(
            ok=payload.get("ok", False),
            intent=intent, body=body,
            result=payload,
            error="" if payload.get("ok") else
                   payload.get("error", "execute_failed"))
    except Exception as e:  # noqa: BLE001
        return ExecuteResult(
            ok=False, intent=intent, body=body,
            error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 用户聊天时介绍可做什么(给 LLM 上下文用)
# ---------------------------------------------------------------------------

_INTENT_SUMMARY = """\
我可以帮你做这些视频相关的事(用自然语言告诉我):

📹 视频创作:
  · "做个 9:16 短视频,主题是 X,文案是 Y"           → 一键出片(L2)
  · "给这段文字配音,音色用云希"                       → TTS 文字转语音
  · "给 C:/v.mp4 自动加字幕"                          → ASR 识别出字幕

✂️ 视频处理:
  · "把 C:/v.mp4 从 00:10 裁到 00:30 输出 C:/out.mp4" → 裁剪
  · "给 C:/v.mp4 加背景音乐 C:/bgm.mp3"               → BGM
  · "把 C:/a.srt 字幕烧到 C:/v.mp4 输出 C:/out.mp4"   → 字幕烧录
  · "查 C:/v.mp4 多长多大"                             → 视频元数据

📈 数据分析:
  · "看看发布最佳时段" / "分析标签效果"                → 发布数据分析

📺 YouTube 海外:
  · "上传 C:/v.mp4 到 YouTube,标题 XXX,公开"          → YouTube 上传(L3,需授权)
  · "我 YouTube 有什么视频"                           → 列我的视频
"""


def intent_summary() -> str:
    return _INTENT_SUMMARY