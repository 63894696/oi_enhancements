# -*- coding: utf-8 -*-
# prisiragent-companion-web.py — 长激活语音陪聊·云端验证版(2026-09-16 M3)
#
# 动机:PrisirAI 主面板(prisiragent_web)是工作向(对话+工具+文件+权限闸),
#       陪聊是陪伴向(长激活+语音+留痕+续接),两者形态完全不同,必须独立服务。
#       本服务只复用底层 LLM 客户端(fastlane/providers/llm_prisir.py),
#       不复用 prisiragent_web 的 web 框架 + session 表。
#
# M3 实施切片:
#   M3.1 对话壳(打字模式 + WebSocket + 留痕 + 续接卡片)— 本文件
#   M3.2 麦克风 Web Audio 流式采集(本文件,前端 js)
#   M3.3 云百炼 Paraformer 流式 ASR — companion_asr.py
#   M3.4 VAD 端点(基于 ASR 自带事件 + 静音 500ms)
#   M3.5 全双工打断(server_vad → 停 TTS + truncate LLM)
#   M3.6 LLM 流式回答(走 fastlane/providers/llm_prisir.py)
#   M3.7 TTS 系统 SAPI(Windows)
#   M3.8 留痕 jsonl + "上次说到哪"续接
#   M3.9 E2E 真机
#
# 跑法:python -B prisiragent-companion-web.py --port 18850
# 设计红线:
#   - 单文件能跑(不污染 prisiragent_web)
#   - WebSocket 是主通道(语音/文字都走它)
#   - jsonl 留痕(append-only,导出=cat)
#   - 失败 fail-open(任何子模块挂了不让壳死)
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

# M3.6:把父目录(oi_enhancements/)加入 sys.path,以 import fastlane/
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from aiohttp import web, WSMsgType

from companion_asr import BailianAsrSession
from companion_llm import stream_chat  # M3.6
from companion_jev import (  # M3.45 P0-2 护栏(2026-09-22)
    try_jev as _jev_try,
    ask_intent as _jev_ask_intent,
    intent_to_zh,
    eval_stage_outcome as _jev_eval_stage,   # M3.45 P1-4 阶段成果评估(2026-09-22)
    risk_index,
    decide_block,
)
# 共享 P1-4 入库核心(陪聊 + Claude Code Stop hook 共用)
from p14_ingest import (  # noqa: E402
    evaluate_and_ingest as _p14_evaluate_and_ingest_shared,
    load_index as _p14_load_index,
    stats as _p14_stats,
)
from companion_asr_providers import (
    PROVIDERS, create_session, list_providers as _list_providers_raw,
    load_settings, save_settings, resolve_provider_cfg, public_settings,
    resolve_fallback_chain, try_start_provider, AsrStartError,
)  # M3.10 / M3.17.3
from companion_llm_providers import (  # M3.18
    list_llm_providers as _list_llm_providers,
    upsert_key_from_form,
)
from fastlane.providers.llm_prisir import PrisirKeyStore  # M3.18
# M3.28 Phase 1 PoC:Node + jsdom shim,跑 LX Music source 协议
from lx_runtime_client import LxRuntimeClient  # M3.28
# M3.15:list_providers 排序版 — 合并 SpeechColab 2025-01 + 2026 新榜单
# (中文 CER 由低到高,越低越准;同分按用户场景/隐私优先排列)
# 2026 新增:字节豆包 Seed-ASR / 阿里 Qwen3-ASR-Flash / 腾讯混元 Hy ASR 3.0
_SORTED_PROVIDER_ORDER = [
    "local-funasr",                              # M3.15 用户指定首位(隐私优先 + 本地模型可换)
    "tencent-hy-asr-v3",                         # 2026 新:中文 CER 2.61% 旗舰
    "azure-speech",                              # 2025 中文 CER 2.99% 第1 / 英文 ~3% WER
    "doubao-seed-asr",                           # 2026 新:LLM 架构推理级,中文 ~2.5%
    "bailian-paraformer-v2",                     # 2025 中文 CER 3.40%(默认 active)
    "qwen3-asr-flash",                           # 2026 新:30 语种 + 22 中文方言
    "openai-whisper-sync",                       # 英文 WER ~2-3% (Whisper-large-v3)
    "gcp-speech-v2",                             # 英文 librispeech WER ~3-4%
    "aws-transcribe-streaming",                  # 英文 ~4-5%
    "tencent-asr",                               # 2025 中文 CER 4.64%
    "xunfei-streaming",                          # 2025 中文 CER 4.80% · 87种方言
    "volcengine-asr",                            # 暂无公开榜单(传统流式)
    "baidu-asr-streaming",                       # 2025 中文 CER 10.10%
    "aliyun-nls",                                # 传统 SDK(老用户迁移)
    "huawei-speech",                             # 暂无公开榜单
    "jd-speech",                                 # 暂无公开榜单
]


def list_providers():
    """按评测分从高到低排的下拉项。本地首位,然后中文→英文。"""
    raw = _list_providers_raw()
    by_name = {p["name"]: p for p in raw}
    ordered = [by_name[n] for n in _SORTED_PROVIDER_ORDER if n in by_name]
    # 末尾追加未在排序表里的新 provider(将来加)
    for p in raw:
        if p["name"] not in _SORTED_PROVIDER_ORDER:
            ordered.append(p)
    return ordered

# ============================================================
# 路径与配置
# ============================================================

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
DATA_DIR = Path(os.environ.get("PRISIR_DATA_DIR")
                or Path.home() / ".local" / "share" / "prisiragent-companion")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHATS_DIR = DATA_DIR / "chats"  # 每通一个 jsonl
CHATS_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR = DATA_DIR / "media"  # 音频文件:media/{sid}/{ts}.{ext}
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

# M3.27 — 派发到 PrisirAI(显式接口,不自动 send)
# M3.27.2:端口从 18800 改为 18802,与 PrisirAI 主面板默认端口对齐
# (prisiragent_web.py:101 PRISIRAGENT_WEB_PORT 默认 18802)
PRISIRAI_INJECT_URL = os.environ.get(
    "PRISIRAGENT_URL", "http://127.0.0.1:18802/prisiragent/api/external_inject")


def _effective_prisirai_url() -> str:
    """M3.29.9 — PrisirAI 端点三级回退:
    settings.prisirai_url_override (用户填的) → env PRISIRAGENT_URL → module 默认值。

    把 _do_dispatch / api_dispatch_test 都改走这个,确保一处改全处生效。
    """
    try:
        s = load_settings(DATA_DIR)
    except Exception:  # noqa: BLE001
        s = {}
    override = (s.get("prisirai_url_override") or "").strip()
    if override:
        return override
    return PRISIRAI_INJECT_URL
DISPATCH_TRIGGER_PHRASES = [
    "交给 PrisirAI", "派过去", "派给 PrisirAI", "让 PrisirAI 做",
    "dispatch to prisirai", "交给主面板",
]
# 派发文本长度上限(避免撑爆 PrisirAI 输入框)
DISPATCH_MAX_CHARS = 8000

# M3.27.1:派发携带 snippet 长度上限(信息无损)
DISPATCH_HIT_SNIPPET_MAX = 500

# M3.27:每通陪聊的派发缓冲 + 增量指针
_DISPATCH_BUF: dict[str, list[dict]] = {}      # sid → [{"role": "user"|"assistant", "text": str, "ts": float}]
_DISPATCH_CURSOR: dict[str, int] = {}          # sid → 已派发到的 index(len)
_DISPATCH_COUNTER: dict[str, int] = {}         # sid → 总派发次数

# M3.27.1:每通陪聊最近 N 轮的 knowledge hits 累积缓冲(派发时塞入)
_DISPATCH_HITS: dict[str, list[dict]] = {}     # sid → [{"path","snippet","mtime","size","turn_ts"},...]
_DISPATCH_HITS_MAX = 20                       # 累积保留最近 20 轮
_DISPATCH_HITS_BUF_CAP = 60                   # 缓冲硬上限(每轮 ≤3 hits × 20 轮,保险起见 cap 60)

# ============================================================
# M3.27.2 — 启动时自动选可用 LLM key + 本地 ASR 优先
# ============================================================
# 三个目标:
#   1. 探测 env + keys.db,选第一个有 key 的平台作为 active_platform(用户未显式设过时)
#   2. 本地 ASR 可达(sherpa-onnx 10096 / local-funasr 10095/10097)→ 切到本地
#   3. GET /api/creds/status 让前端能看到当前探测结果
#
# 设计红线(沿用 M3.27 闭环):
#   - 用户显式设过的(active_platform / active_provider 非空)→ 不覆盖
#   - 探测全部失败 → 不报错,返 "", settings 保持原样
#   - 函数全部 idempotent,启动时调一次即可

# ENV → platform id 映射(标准命名:厂商 API key 的 env 变量约定)
_ENV_KEY_MAP: dict[str, list[str]] = {
    # 国际主流
    "openai":      ["OPENAI_API_KEY"],
    "anthropic":   ["ANTHROPIC_API_KEY"],
    "gemini":      ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "grok":        ["XAI_API_KEY", "GROK_API_KEY"],
    "mistral":     ["MISTRAL_API_KEY"],
    "groq":        ["GROQ_API_KEY"],
    "openrouter":  ["OPENROUTER_API_KEY"],
    # 国内
    "deepseek":    ["DEEPSEEK_API_KEY"],
    "doubao":      ["DOUBAO_API_KEY", "ARK_API_KEY"],
    "qwen":        ["DASHSCOPE_API_KEY", "QWEN_API_KEY"],
    "bailian":     ["BAILIAN_API_KEY", "DASHSCOPE_API_KEY"],
    "moonshot":    ["MOONSHOT_API_KEY", "KIMI_API_KEY"],
    "zhipu":       ["ZHIPU_API_KEY", "GLM_API_KEY"],
    "yunbailian":  ["BAILIAN_API_KEY", "DASHSCOPE_API_KEY"],
    # 本地 / 自建(环境变量 + 端点)
    "ollama":      ["OLLAMA_HOST"],
    "llama":       ["LLAMA_SERVER_URL"],
}


def _scan_env_keys() -> dict[str, str]:
    """扫用户环境变量,返 {platform_id: env_var_name}(找到第一个非空即停)。"""
    found: dict[str, str] = {}
    for plat, vars_ in _ENV_KEY_MAP.items():
        for v in vars_:
            if os.environ.get(v):
                found[plat] = v
                break
    return found


def _scan_keys_db() -> list[str]:
    """从 PrisirKeyStore 读已配置的 platform 列表(优先 has_key=True 的)。

    fallback:用 sqlite3 直接读 ~/.local/share/prisir/keys.db。
    """
    plats: list[str] = []
    try:
        from fastlane.providers.llm_prisir import PrisirKeyStore
        ks = PrisirKeyStore()
        # list_platforms() 返回 [{platform, has_key, ...}, ...]
        if hasattr(ks, "list_platforms"):
            for row in ks.list_platforms():
                p = row.get("platform") if isinstance(row, dict) else None
                has_key = row.get("has_key", True) if isinstance(row, dict) else True
                if p and has_key:
                    plats.append(p)
    except Exception as e:  # noqa: BLE001
        log.warning("[M3.27.2] keys.db scan via PrisirKeyStore err: %s", e)
    if not plats:
        # fallback:直接读 sqlite(keys 表 schema 是 platform_keys)
        try:
            import sqlite3
            db = Path.home() / ".local" / "share" / "prisir" / "keys.db"
            if db.is_file():
                with sqlite3.connect(str(db)) as c:
                    rows = c.execute(
                        "SELECT platform, api_key FROM platform_keys "
                        "WHERE api_key != ''").fetchall()
                plats = sorted({r[0] for r in rows if r[0]})
        except Exception as e:  # noqa: BLE001
            log.warning("[M3.27.2] keys.db sqlite fallback err: %s", e)
    return sorted(set(plats))


def _auto_select_active_platform() -> tuple[str, list[str]]:
    """返回 (selected, available)。
    selected:按优先级匹配到的首个可用平台;available:本次启动所有可用平台。
    优先级:本地 → 国内便宜 → 国外便宜 → 国外主流(便宜 + 本地优先)。
    """
    env_keys = _scan_env_keys()
    db_keys = _scan_keys_db()
    available = sorted(set(env_keys.keys()) | set(db_keys))
    # 优先本地 + 国内便宜(降成本)
    priority = [
        "ollama", "llama",                  # 本地
        "deepseek", "qwen", "bailian", "yunbailian",
        "doubao", "moonshot", "zhipu",      # 国内便宜
        "groq", "openrouter",               # 国外便宜
        "openai", "anthropic", "gemini", "grok", "mistral",  # 国外主流
    ]
    selected = ""
    for p in priority:
        if p in available:
            selected = p
            break
    if not selected and available:
        selected = available[0]
    return (selected, available)


def _apply_auto_creds_to_settings() -> dict:
    """M3.27.2:启动时调一次 — 若 settings 没显式设过 active_platform,
    把探测结果写入 settings(active_platform + llm_available_platforms)。"""
    try:
        settings = load_settings(DATA_DIR)
    except Exception as e:  # noqa: BLE001
        log.warning("[M3.27.2] load_settings err: %s", e)
        return {}
    selected_now = settings.get("active_platform")
    if selected_now:  # 非空 → 用户偏好,跳过
        return settings
    selected, available = _auto_select_active_platform()
    settings["active_platform"] = selected
    settings["llm_available_platforms"] = available
    try:
        save_settings(DATA_DIR, settings)
    except Exception as e:  # noqa: BLE001
        log.warning("[M3.27.2] save_settings err: %s", e)
    log.info("[M3.27.2] auto-selected platform=%s, available=%s",
             selected, available)
    return settings


def _probe_local_asr() -> str:
    """探测本地 ASR 是否就绪 — sherpa-onnx (10096) 或 funasr (10095/10097)。
    全部不可达 → 返 ""(不抛)。"""
    import urllib.request
    candidates = [
        ("sherpa-onnx",  "http://127.0.0.1:10096/health"),
        ("local-funasr", "http://127.0.0.1:10095/health"),
        ("local-funasr", "http://127.0.0.1:10097/health"),
    ]
    for name, url in candidates:
        try:
            urllib.request.urlopen(url, timeout=1).read(1)
            return name
        except Exception:  # noqa: BLE001
            continue
    return ""


def _apply_auto_asr_if_local() -> None:
    """M3.27.2:若本地 ASR 可达,把 active_provider 切到本地(降云端 key 依赖)。
    用户显式设过的(active_provider 非空且不是默认 bailian-paraformer-v2)→ 不动。
    """
    try:
        settings = load_settings(DATA_DIR)
    except Exception as e:  # noqa: BLE001
        log.warning("[M3.27.2] load_settings(asr) err: %s", e)
        return
    # 默认是 bailian-paraformer-v2;若用户已改成别的 → 不动
    cur = settings.get("active_provider", "")
    if cur and cur != "bailian-paraformer-v2":
        return
    local = _probe_local_asr()
    if not local:
        return
    if cur == local:
        return
    settings["active_provider"] = local
    try:
        save_settings(DATA_DIR, settings)
    except Exception as e:  # noqa: BLE001
        log.warning("[M3.27.2] save_settings(asr) err: %s", e)
    log.info("[M3.27.2] auto-switched ASR to %s", local)

# M3.11 闲置超时挂断(类似电话计费保护,避免用户忘记挂断持续扣费)
# 默认 5 分钟无活动 → 警告 → 再 1 分钟 → 自动挂断
IDLE_TIMEOUT_SEC = int(os.environ.get("PRISIR_COMPANION_IDLE_SEC", "300"))
IDLE_WARN_BEFORE_SEC = int(os.environ.get("PRISIR_COMPANION_IDLE_WARN", "60"))

logging.basicConfig(
    level=os.environ.get("PRISIR_COMPANION_LOG", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("prisiragent-companion")

# P3j Phase C(2026-09-27)ext-handraw-style-prompter → 主对话能力注册
# 让 companion 进程启动即注册 3 个 poster capability(import 副作用)
try:
    from prisir_work import poster_capabilities  # noqa: F401
except Exception:  # noqa: BLE001
    log.exception("Phase C: import poster_capabilities failed; poster EXEC will be disabled")

# P3j Phase D(2026-09-27)poster 卡片 → image-gen t2i 闭环
# 让 companion 进程启动即注册 image-gen.from_poster_prompt(import 副作用)
try:
    from prisir_work import poster_to_image_capability  # noqa: F401
except Exception:  # noqa: BLE001
    log.exception("Phase D: import poster_to_image_capability failed; image-gen.from_poster_prompt will be disabled")

# P3j free-for-dev Phase C(2026-09-27)ext-free-for-dev-promo → 主对话能力注册
# 4 capability 全 L0(本地只读免费资源库,无副作用)
try:
    from prisir_work import free_for_dev_capabilities  # noqa: F401
except Exception:  # noqa: BLE001
    log.exception("Phase C free-for-dev: import free_for_dev_capabilities failed; free EXEC will be disabled")

# P3j 5-项目 ship Sprint 1(2026-10-02)ext-public-apis-promo → 主对话能力注册
# 4 capability 全 L0(public-apis/public-apis 51 cat / 1953 条,无副作用)
try:
    from prisir_work import public_apis_capabilities  # noqa: F401
except Exception:  # noqa: BLE001
    log.exception("Phase C public-apis: import public_apis_capabilities failed; api EXEC will be disabled")

# P3j 5-项目 ship Sprint 1(2026-10-02)ext-public-apis-cn-promo → 主对话能力注册
# 4 capability 全 L0(llf007/public-apis-cn 54 cat / 1493 条,国内可访问)
try:
    from prisir_work import public_apis_cn_capabilities  # noqa: F401
except Exception:  # noqa: BLE001
    log.exception("Phase C public-apis-cn: import public_apis_cn_capabilities failed; api_cn EXEC will be disabled")

# P3j 5-项目 ship Sprint 2(2026-10-02)ext-n0shake-public-apis-promo → 主对话能力注册
# 4 capability 全 L0(n0shake/Public-APIs 56 cat / 481 条,免 key/试用/开源)
try:
    from prisir_work import nokeyapi_capabilities  # noqa: F401
except Exception:  # noqa: BLE001
    log.exception("Phase C nokeyapi: import nokeyapi_capabilities failed; nokeyapi EXEC will be disabled")

# P3j 5-项目 ship Sprint 2(2026-10-02)ext-awesome-selfhosted-promo → 主对话能力注册
# 4 capability 全 L0(awesome-selfhosted 95 cat / 1260 条,自部署/自托管/开源替代 SaaS)
try:
    from prisir_work import selfhost_capabilities  # noqa: F401
except Exception:  # noqa: BLE001
    log.exception("Phase C selfhost: import selfhost_capabilities failed; selfhost EXEC will be disabled")

# P3j Phase 1.6(2026-09-28)agency-roles 264 角色查询能力注册
# 3 capability 全 L0(本地 JSON 只读,无子进程无外网)
try:
    from prisir_work import agency_capabilities  # noqa: F401
except Exception:  # noqa: BLE001
    log.exception("Phase 1.6: import agency_capabilities failed; agency EXEC will be disabled")

# ============================================================
# 留痕:jsonl append-only(2026-09-16 M3.8 占位,M3.1 已先跑通文件层)
# ============================================================

def chat_path(sid: str) -> Path:
    return CHATS_DIR / f"{sid}.jsonl"


def delete_session(sid: str) -> bool:
    """删除单个会话(单文件 + 派发缓冲清理)。不存在返 False。"""
    if not sid:
        return False
    p = chat_path(sid)
    removed = False
    try:
        if p.is_file():
            p.unlink()
            removed = True
    except Exception as e:  # noqa: BLE001
        log.warning("delete_session sid=%s unlink err: %s", sid, e)
    # 派发缓冲
    try:
        _DISPATCH_BUF.pop(sid, None)
        _DISPATCH_CURSOR.pop(sid, None)
        _DISPATCH_COUNTER.pop(sid, None)
    except Exception:  # noqa: BLE001
        pass
    return removed


def clear_sessions() -> int:
    """清空所有会话(危险:批量删除)。返删除数。"""
    n = 0
    if not CHATS_DIR.is_dir():
        return 0
    for p in CHATS_DIR.glob("*.jsonl"):
        try:
            p.unlink()
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("clear_sessions %s err: %s", p, e)
    # 派发缓冲一并清
    try:
        _DISPATCH_BUF.clear()
        _DISPATCH_CURSOR.clear()
        _DISPATCH_COUNTER.clear()
    except Exception:  # noqa: BLE001
        pass
    return n


def load_history(sid: str) -> list[dict]:
    p = chat_path(sid)
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    return out


def append_turn(sid: str, role: str, text: str, src: str = "local", **meta) -> None:
    """append 一条 turn。src=local/cloud/asr,meta 放 model/barge_in 等。"""
    rec = {"ts": time.time(), "role": role, "text": text, "src": src, **meta}
    p = chat_path(sid)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # M3.27:同步进派发缓冲(仅 user + assistant,asr 路径也算 user)
    if role in ("user", "assistant") and text:
        buf = _DISPATCH_BUF.setdefault(sid, [])
        buf.append({"role": role, "text": text, "ts": time.time()})


def _should_dispatch(text: str) -> bool:
    """M3.27.1 触发词检测(严格子串匹配)"""
    t = (text or "").strip()
    if not t:
        return False
    for p in DISPATCH_TRIGGER_PHRASES:
        if p in t:
            return True
    return False


def _format_dispatch(sid: str, mode: str) -> tuple[str, int, int, list[dict]]:
    """M3.27.1 把缓冲拼成 markdown 文本。
    返回(text, count, total, knowledge_refs)
    mode="incremental":cursor 之后的全部;mode="all":全部(忽略 cursor,主要用于手动「派发全部」)
    knowledge_refs:[{path, snippet, mtime, size}, ...]给 PrisirAI 端用(settings toggle 可关)"""
    buf = _DISPATCH_BUF.get(sid, [])
    cursor = _DISPATCH_CURSOR.get(sid, 0)
    if mode == "incremental":
        new_turns = buf[cursor:]
    else:  # "all"
        new_turns = list(buf)
    knowledge_refs: list[dict] = []
    if not new_turns:
        return ("", 0, len(buf), knowledge_refs)
    # 拼对话片段
    lines = [
        f"[对话上下文 · 来自 Prisir 陪聊 · {time.strftime('%Y-%m-%d %H:%M')} · 已派发 {_DISPATCH_COUNTER.get(sid, 0) + 1} 次]",
        "",
    ]
    for t in new_turns:
        who = "user" if t["role"] == "user" else "assistant"
        lines.append(f"{who}: {t['text']}")
    # M3.27.1:拼 knowledge hits 段(默认带,settings toggle 可关)
    settings = load_settings(DATA_DIR)
    include_knowledge = settings.get("dispatch_include_knowledge", True)
    if include_knowledge:
        raw_hits = _filter_dispatch_hits(sid)
        seen = set()
        for h in raw_hits:
            if h["path"] in seen:
                continue
            seen.add(h["path"])
            knowledge_refs.append({
                "path": h["path"],
                "snippet": (h["snippet"] or "")[:DISPATCH_HIT_SNIPPET_MAX],
                "mtime": h.get("mtime"),
                "size": h.get("size"),
            })
        if knowledge_refs:
            lines.append(_format_hits_section(knowledge_refs))
    lines.extend(["", "[意图]", "(空,用户可填)"])
    text = "\n".join(lines)
    if len(text) > DISPATCH_MAX_CHARS:
        text = text[:DISPATCH_MAX_CHARS] + "\n\n[已截断]"
    return (text, len(new_turns), len(buf), knowledge_refs)


def _record_dispatch_hits(sid: str, hits: list[dict]) -> None:
    """M3.27.1:把本轮 knowledge hits 写进累积缓冲,超过 cap 丢老。
    保留最近 20 轮(以 hit 计:保险起见直接按 list 长度 cap 60)。"""
    if not hits:
        return
    buf = _DISPATCH_HITS.setdefault(sid, [])
    now = time.time()
    for h in hits:
        buf.append({
            "path": h.get("path", ""),
            "snippet": (h.get("snippet") or "")[:DISPATCH_HIT_SNIPPET_MAX],
            "mtime": h.get("mtime"),
            "size": h.get("size"),
            "turn_ts": now,
        })
    # 保留最近 _DISPATCH_HITS_BUF_CAP 条
    if len(buf) > _DISPATCH_HITS_BUF_CAP:
        _DISPATCH_HITS[sid] = buf[-_DISPATCH_HITS_BUF_CAP:]


def _filter_dispatch_hits(sid: str) -> list[dict]:
    """M3.27.1:按当前 _DISPATCH_BUF 内容过滤 — 用户删/编辑过的对话的 hits 不附。
    策略:hit 的 turn_ts 早于当前 buf 最早 turn ts → 用户删过那轮 → 丢弃。"""
    buf = _DISPATCH_HITS.get(sid, [])
    if not buf:
        return []
    current = _DISPATCH_BUF.get(sid, [])
    if not current:
        return []
    earliest_ts = min((t.get("ts", 0) for t in current), default=0)
    kept: list[dict] = []
    for h in buf:
        ts = h.get("turn_ts", 0)
        if ts < earliest_ts - 0.1:
            continue   # hit 对应的对话被删了
        kept.append(h)
    return kept


def _format_hits_section(hits: list[dict]) -> str:
    """M3.27.1:拼 [本轮对话引用的本地知识] markdown 段"""
    if not hits:
        return ""
    lines = ["", "[本轮对话引用的本地知识(文件路径供查找)]", ""]
    seen = set()
    for h in hits:
        key = h["path"]
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {h['path']}: {h['snippet']}")
    lines.append("")
    return "\n".join(lines)


async def _do_dispatch(sid: str, mode: str) -> dict:
    """M3.27.1 实际派发:POST 到 PrisirAI 端点。
    返回 dict {ok, count, total, cursor, prisirai, err, knowledge_refs}"""
    if not _DISPATCH_BUF.get(sid):
        return {"ok": False, "err": "缓冲区为空,无需派发", "count": 0, "total": 0}
    text, count, total, knowledge_refs = _format_dispatch(sid, mode)
    if count == 0:
        return {"ok": False, "err": "无新增对话可派发", "count": 0, "total": total}
    payload = {
        "text": text,
        "source": "companion",
        "sid": sid,
        "knowledge_refs": knowledge_refs,   # M3.27.1
    }
    try:
        import urllib.request, urllib.error
        url = _effective_prisirai_url()   # M3.29.9:override → env → default
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # 5 秒超时
        resp = urllib.request.urlopen(req, timeout=5)
        body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            return {"ok": False, "err": f"PrisirAI 拒绝: {body.get('err', '?')}",
                    "count": count, "total": total, "prisirai": url,
                    "knowledge_refs": knowledge_refs}
        # 成功 → 更新 cursor + counter
        _DISPATCH_CURSOR[sid] = _DISPATCH_CURSOR.get(sid, 0) + count
        _DISPATCH_COUNTER[sid] = _DISPATCH_COUNTER.get(sid, 0) + 1
        log.info("dispatch sid=%s count=%d cursor=%d knowledge_refs=%d",
                 sid, count, _DISPATCH_CURSOR[sid], len(knowledge_refs))
        return {"ok": True, "count": count, "total": total,
                "cursor": _DISPATCH_CURSOR[sid],
                "dispatches": _DISPATCH_COUNTER[sid],
                "prisirai": url, "id": body.get("id", ""),
                "knowledge_refs": knowledge_refs}
    except urllib.error.URLError as e:
        return {"ok": False, "err": f"PrisirAI 不可达: {e}", "count": count,
                "total": total, "prisirai": url,
                "knowledge_refs": knowledge_refs}
    except Exception as e:
        log.exception("dispatch failed sid=%s", sid)
        return {"ok": False, "err": f"{type(e).__name__}: {e}", "count": count,
                "total": total, "knowledge_refs": knowledge_refs}


def list_recent_sessions(limit: int = 8) -> list[dict]:
    """列最近 N 通对话(用于「上次说到哪」续接卡片)。"""
    files = sorted(CHATS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    out = []
    for p in files[:limit]:
        sid = p.stem
        msgs = load_history(sid)
        last = msgs[-1] if msgs else None
        first_user = next((m for m in msgs if m.get("role") == "user"), None)
        preview = (last.get("text") if last else "")[:60]
        title_src = first_user.get("text", "") if first_user else ""
        title = title_src[:24] + ("…" if len(title_src) > 24 else "") if title_src else "(空)"
        out.append({
            "sid": sid,
            "title": title,
            "preview": preview,
            "turns": len(msgs),
            "mtime": p.stat().st_mtime,
        })
    return out


# ============================================================
# 续接卡片:取某通的最后两轮(用户问 + AI 答),作为「上次说到哪」开头
# ============================================================

def get_continue_card(sid: str) -> dict:
    msgs = load_history(sid)
    if not msgs:
        return {"sid": sid, "preview": [], "hint": "这是新对话"}
    tail = msgs[-2:] if len(msgs) >= 2 else msgs
    last_ts = msgs[-1].get("ts", 0)
    delta_h = (time.time() - last_ts) / 3600 if last_ts else 0
    if delta_h < 1:
        delta_txt = f"{int(delta_h*60)} 分钟前"
    elif delta_h < 24:
        delta_txt = f"{int(delta_h)} 小时前"
    else:
        delta_txt = f"{int(delta_h/24)} 天前"
    return {
        "sid": sid,
        "delta": delta_txt,
        "preview": [{"role": m.get("role"), "text": m.get("text", "")[:80]}
                    for m in tail],
        "hint": f"上次聊到「{(msgs[-1].get('text') or '')[:30]}…」,继续?",
    }


# ============================================================
# LLM 流式调用(2026-09-16 M3.6)— 复用 fastlane keys.db + 自实现 SSE 流
# ============================================================
SYSTEM_PROMPT = (
    "你是 Prisir,一位温暖、善于倾听、略带幽默感的中文陪聊伙伴。"
    "用对话口语,不长篇大论;关心用户当下情绪,不主动建议工具或代码。"
    "对话场景是「陪用户聊日常」,所以避免学术腔;可以共情、可以问轻轻的反问。"
    "记住对话历史,像老朋友一样自然续聊。"
)

# M3.23 上下文注入:控制两个开关(默认关,需用户在 companion_asr_settings.json
# 显式开启 + permission gate 第一次确认后才会启用)。这样:
# - 隐私零侵入(用户没开 → 一行代码不跑)
# - 不污染陪聊默认 SYSTEM_PROMPT 的人设
# - settings 是 companion_asr_settings.json (companion 自己的配置文件,
#   跟 OI 主的 settings.json 隔离)
_FCONTEXT_CFG = _HERE / "companion_asr_settings.json"
_FCONTEXT_DEFAULTS = {
    "asr_screen_capture": False,   # on_final 时调 a11y_extract 拿当前屏 a11y
    "asr_knowledge_lookup": False, # on_final 时查本地知识库(prisIR_fcontent)
    "fcontent_root": "",           # 留空 = 用户配置;默认指向 ~/Documents/ObsidianVault
    "screen_max_depth": 4,         # a11y tree 深度上限(避免太大撑爆 context)
    "knowledge_top_k": 3,          # 知识库返回 snippet 数
    "context_timeout_sec": 1.5,    # a11y + fcontent 总耗时硬上限
    # M3.45 P0-2 护栏(2026-09-22)— TypeSafe Jev 前置安全评估
    # 默认关:隐私零侵入;用户在 settings UI 显式开启
    "jev_enabled": False,
    "jev_risk_threshold": "medium",     # safe/low 直通;medium+ 弹 confirm
    "jev_jailbreak_threshold": 0.7,     # 越狱概率 ≥ 此值 → 硬拦(不弹 confirm)
    "jev_timeout_sec": 1.5,             # 双通道总超时(主+备)
    "jev_fail_open": True,              # 双通道全挂时 → 放行,不恶化现状
    # M3.45 P0-1 意图分发(2026-09-22)— Jev Choice primitive 给消息打标签
    # 独立开关(不依赖 jev_enabled):用户可单独开 intent,但不开 guard
    "intent_enabled": True,             # 默认开(轻量级,只读 message 文本)
    "intent_timeout_sec": 1.0,          # intent 单独超时(比 guard 短 — 不阻塞)
    "intent_min_confidence": 0.55,      # 低于此置信度 → 视作 "unknown",不路由
    "intent_routing": True,             # 是否按 intent 自动调整 system prompt
    # M3.45 P1-4 阶段成果增量入库(2026-09-22)— ai_done 后 Jev 评估 → 入 Obsidian
    "p14_enabled": True,                # 默认开
    "p14_min_value": 2,                 # value_score ≥ 此值才入库(0-3)
    "p14_min_has_prob": 0.5,            # has_outcome prob ≥ 此才入库
    "p14_timeout_sec": 1.2,             # 阶段成果评估超时(不阻塞主对话)
    "p14_dir_name": "_incremental",     # 写入 fcontent_root 下子目录
    "p14_topic_strategy": "auto",       # auto=从 user 文本首 12 字;manual=用 intent;off=用日期
    # P3j T29-c Skills 工作台索引(2026-09-27)+Phase 7(2026-09-28)— 单段 JSON 索引替换 5 处 intent_summary
    # 用户决策"全 skill 给 LLM 看 + 接受成本",默认开;Phase 7 紧凑化后 ~7993c(-38%)
    # 5 处老 intent_summary 仍 fallback(覆盖度 < 全量);实测老 5 处 ~8000+ 字符只覆盖 12 个能力,
    # 新 skills_index 覆盖全部 69 skill。
    "skills_index_enabled": True,
    "skills_index_fallback_intent": True,  # True 时:失败/未开时仍走老 5 处;False 时仅走新索引
    # P3j T29 Phase 3.5 — 两阶段 replan 闸门(2026-09-28,commit 58e8902)+Phase 7:
    # 用户决策"接受 replan 等待时间和成本,确保任务质量降低返工概率",默认开。
    # ai_done 后异步旁路问 LLM「用户这条想调哪些 skill」,L1+ > 阈值推 skill_plan_request 弹卡。
    # 开启后每个用户任务多 1 次 LLM 调,延迟 +1s,成本 +$0.001,但命中率显著提高。
    "skills_replan_enabled": True,
    "skills_replan_auto_l1_threshold": 2,  # L1+ ≤ 阈值自动执行,> 阈值才弹卡
    "skills_replan_timeout_sec": 8.0,      # replan LLM 超时秒数(fail-open)
    # P3j T29 Phase 4 — EXEC ↔ tool_use 兼容 + 灰度切换(2026-09-28,commit 待 ship):
    # mode=both 兼容两种协议;mode=exec 强制老;mode=tool_use 强制新
    "skills_exec_mode": "both",  # "exec" / "tool_use" / "both"
}


def _load_fcontext_cfg() -> dict:
    """读 companion_asr_settings.json 里 M3.23 上下文开关。无文件返 defaults。"""
    try:
        if _FCONTEXT_CFG.exists():
            cfg = json.loads(_FCONTEXT_CFG.read_text(encoding="utf-8"))
            for k, v in _FCONTEXT_DEFAULTS.items():
                cfg.setdefault(k, v)
            return cfg
    except Exception:  # noqa: BLE001
        log.warning("[M3.23] read %s fail", _FCONTEXT_CFG, exc_info=True)
    return dict(_FCONTEXT_DEFAULTS)


async def _capture_screen_context(timeout_sec: float = 1.5) -> str:
    """M3.23.1 异步调 a11y_extract.extract_a11y 拿当前焦点窗口 UI 树。

    返回纯文本片段(供 build_messages 拼进 system 第二段)。失败/超时返空串,
    不污染 LLM 流。"""
    try:
        from a11y_extract import extract_a11y  # noqa: PLC0415
        cfg = _load_fcontext_cfg()
        if not cfg.get("asr_screen_capture"):
            return ""
        max_depth = int(cfg.get("screen_max_depth") or 4)

        def _do_extract() -> str:
            try:
                r = extract_a11y(window_title=None, max_depth=max_depth)
            except Exception as e:  # noqa: BLE001
                log.warning("[M3.23] a11y_extract err: %s", e)
                return ""
            if not isinstance(r, dict) or r.get("status") != "ok":
                return ""
            tree = r.get("a11y") or ""
            title = r.get("title") or ""
            if not tree:
                return ""
            # 截断防爆 context:总长 < 1500 chars(a11y 元素数太多时取前 N 行)
            head = "\n".join(tree.splitlines()[:60])
            block = f"[当前屏幕 UI 树 / window: {title}]\n{head}"
            return block[:1500]

        text = await asyncio.wait_for(
            asyncio.to_thread(_do_extract), timeout=timeout_sec)
        return text or ""
    except asyncio.TimeoutError:
        log.warning("[M3.23] a11y_extract timeout (>%ss)", timeout_sec)
        return ""
    except Exception as e:  # noqa: BLE001
        log.warning("[M3.23] a11y_extract outer err: %s", e)
        return ""


async def _knowledge_lookup(query: str, timeout_sec: float = 1.5) -> tuple[str, list[dict]]:
    """M3.23.2 异步查 prisir_fcontent 索引,返 (system 文本块, hits 列表)。

    - 文本块:拼成 `[本地知识库命中 / top-N]` 段(给 build_messages 用)
    - hits:[{path, snippet, score, mtime}, ...](M3.25 给前端渲染引用列表)
    - 失败/超时/索引未建 → 返 ("", [])
    - query 太短(<2 chars)直接跳
    """
    if not query or len(query.strip()) < 2:
        return "", []
    try:
        cfg = _load_fcontext_cfg()
        if not cfg.get("asr_knowledge_lookup"):
            return "", []
        top_k = int(cfg.get("knowledge_top_k") or 3)

        def _do_search() -> list[dict]:
            try:
                from prisir_fcontent import Fcontent  # noqa: PLC0415
                fc = Fcontent.shared()
                if fc is None:
                    return []
                r = fc.search(query, limit=top_k, offset=0)
                return list(r.get("hits") or []) if isinstance(r, dict) else []
            except Exception as e:  # noqa: BLE001
                log.warning("[M3.23] Fcontent.search err: %s", e)
                return []

        hits = await asyncio.wait_for(
            asyncio.to_thread(_do_search), timeout=timeout_sec)
        if not hits:
            return "", []
        lines = ["[本地知识库命中 / top-%d]" % top_k]
        clean_hits: list[dict] = []
        for h in hits:
            path = h.get("path") or ""
            snip = (h.get("snippet") or "").replace("\n", " ")[:500]   # M3.27.1:240→500
            clean_hits.append({
                "path": path,
                "snippet": snip,
                "mtime": h.get("mtime"),
                "size": h.get("size"),
            })
            lines.append(f"- {path}: {snip}")
        return "\n".join(lines)[:1500], clean_hits
    except asyncio.TimeoutError:
        log.warning("[M3.23] fcontent search timeout (>%ss)", timeout_sec)
        return "", []
    except Exception as e:  # noqa: BLE001
        log.warning("[M3.23] fcontent outer err: %s", e)
        return "", []


async def _m323_enrich_context(user_text: str, cfg: dict) -> tuple[list[str], list[dict]]:
    """M3.23.1+2 + M3.25 拼 system message 第二/三段(屏 + 知识库)+ 提取 knowledge hits。

    返回 (extras, knowledge_hits):
    - extras:拼到 messages 里的 system 段列表(给 LLM 看)
    - knowledge_hits:M3.25 给前端展示的 [{path, snippet, mtime, size}, ...]
      (LLM 不一定引用,前端展示让用户知道命中了哪几个文档)
    """
    extras: list[str] = []
    knowledge_hits: list[dict] = []
    timeout = float(cfg.get("context_timeout_sec") or 1.5)
    # screen 跟 knowledge 并行跑(各自独立,互不阻塞)
    coros = []
    if cfg.get("asr_screen_capture"):
        coros.append(_capture_screen_context(timeout))
    else:
        coros.append(_empty())
    if cfg.get("asr_knowledge_lookup"):
        coros.append(_knowledge_lookup(user_text, timeout))
    else:
        coros.append(_empty())
    try:
        screen, know_tuple = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=timeout + 0.5)
    except asyncio.TimeoutError:
        log.warning("[M3.23] enrich gather timeout")
        return extras, knowledge_hits
    if isinstance(screen, str) and screen:
        extras.append(screen)
    # M3.25:knowledge 现在返 (text, hits) 元组
    if isinstance(know_tuple, tuple) and len(know_tuple) == 2:
        know_text, know_hits = know_tuple
        if know_text:
            extras.append(know_text)
        if know_hits:
            knowledge_hits = list(know_hits)
    return extras, knowledge_hits


async def _empty() -> str:
    return ""


# ============================================================
# M3.45 P1-4 — 阶段成果增量入库(2026-09-22)
# 设计:
#   - ai_done 后调 _p14_evaluate_and_ingest
#   - 用 Jev Noul(has_outcome)+ Score(value_score) 评估
#   - value_index ≥ p14_min_value → 抽段 → 段级 sha256 去重 → 写入 _incremental/
#   - 索引文件 _p14_index.json 存 {hash: path} 在 fcontent_root 下
#   - 主题策略:auto/manual/off 三档
# ============================================================
# P1-4 共享入口 — 复用 p14_ingest 模块,陪聊 + Claude Code hook 共用
# (sync helper: safe_filename / load_index / save_index / extract_segments /
#  derive_topic 都直接 import 自 p14_ingest,_p14_ 前缀保持旧引用兼容)


async def _p14_evaluate_and_ingest(sess: "CallSession",
                                   user_text: str,
                                   assistant_text: str,
                                   cfg: dict) -> dict:
    """P1-4 薄包装:从 sess 取 intent + 累加 p14_added,然后调共享模块。

    Returns: {triggered, value, value_index, has_prob, added_count,
              skipped_count, path, fallback_used, reason}
    """
    intent = (getattr(sess, "jev_intent", {}) or {}).get("intent", "unknown")
    res = await _p14_evaluate_and_ingest_shared(
        user_text, assistant_text, cfg,
        intent=intent,
        history_len=len(getattr(sess, "history", []) or []),
    )
    if res.get("triggered"):
        # 累计到 sess(给前端徽标)
        try:
            added_n = int(res.get("added_count", 0))
            if added_n > 0:
                sess.p14_added = getattr(sess, "p14_added", 0) + added_n
        except Exception:
            pass
    return res


async def _p14_bg_task(sess: "CallSession",
                       user_text: str,
                       assistant_text: str) -> None:
    """ai_done 后台跑 P1-4 评估 + 入库,推 ws 通知前端。
    不抛异常,不阻塞流。
    """
    try:
        cfg = _load_fcontext_cfg()
        if not cfg.get("p14_enabled", True):
            return
        # 简单 intent 过滤:tool_call/roleplay/无 intent 跳过
        intent = (getattr(sess, "jev_intent", {}) or {}).get("intent", "")
        if intent in ("tool_call", "roleplay", "unknown", ""):
            return
        res = await _p14_evaluate_and_ingest(sess, user_text, assistant_text, cfg)
        if res.get("triggered"):
            try:
                await sess.ws.send_json({
                    "type": "incremental_added",
                    "value": res.get("value", "archivable"),
                    "value_zh": _jev_value_to_zh(res.get("value_index", 2)),
                    "value_index": res.get("value_index", 2),
                    "added_count": res.get("added_count", 0),
                    "skipped_count": res.get("skipped_count", 0),
                    "path": res.get("path"),
                    "has_prob": res.get("has_prob", 0),
                    "total_added": getattr(sess, "p14_added", 0),
                    "reason": res.get("reason"),
                })
            except Exception:  # noqa: BLE001
                pass
        elif res.get("reason") not in ("value_too_low", "no_outcome",
                                        "disabled", "empty_text", "skipped",
                                        "fail_soft", "no_fcontent_root",
                                        "no_segments", "all_duplicates"):
            # 异常情况也通知前端(便于调试)
            try:
                await sess.ws.send_json({
                    "type": "incremental_skipped",
                    "reason": res.get("reason"),
                    "has_prob": res.get("has_prob", 0),
                    "value": res.get("value", "none"),
                })
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        log.warning("[p14-bg] outer err: %s: %s",
                    type(e).__name__, str(e)[:120])


async def build_messages(sess: CallSession, current_user_text: str) -> list[dict]:
    """组装 LLM 输入 messages:system + (M3.23 上下文段) + 最近 N 轮历史。"""
    msgs: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    # P2-Rules(2026-10-01): 项目根 AGENTS.md + 家目录 AGENTS.md(完全 ECC 对齐)
    # 优先级最高,载入所有已有 system 段之前。
    # 失败 fallback:任何 IO / 解析异常 → 静默返 None,不抛(主对话不受影响)。
    try:
        from prisir_work.rules import (
            load_rules_for_project, merge_rules, format_rules_for_prompt,
        )
        # sess 没有 workdir 属性 → 用 Path.cwd() 兜底
        rules_cwd = Path(getattr(sess, "workdir", None) or Path.cwd())
        user_rules, project_rules = load_rules_for_project(rules_cwd)
        merged = merge_rules(user_rules, project_rules)
        if merged:
            rblock = format_rules_for_prompt(merged)
            if rblock:
                msgs.append({"role": "system", "content": rblock})
    except Exception:  # noqa: BLE001
        log.exception("rules 注入失败(不影响主对话)")
    # P3j T29-c(2026-09-27): Skills 工作台索引(渐进披露)—
    # 单段 JSON 索引替换 N 处 intent_summary。配置项 skills_index_enabled 切换。
    # 失败/未开启 → fallback 老 5 处(skills_index_fallback_intent=True)。
    fcfg = _load_fcontext_cfg()
    use_skills_idx = bool(fcfg.get("skills_index_enabled"))
    fallback_intent = bool(fcfg.get("skills_index_fallback_intent", True))
    if use_skills_idx:
        try:
            from prisir_work.skills import describe_registry_compact
            skills_idx = describe_registry_compact()
            msgs.append({"role": "system", "content": (
                "【工作台 skill 索引】下表 JSON 是当前可用的全部 skill 列表。"
                "每项含 id / name / emoji / risk / tags。"
                "需要执行某个 skill 时,在回复末尾追加 EXEC 标记:\n"
                "  [[EXEC: <skill_id> k1=\"v1\" k2=\"v2\" ...]]\n"
                "参数必须是字符串字面量。L2/L3 风险能力用户会单独确认一次,无需你提醒。"
                "只在索引中明确列出的任务上输出 EXEC,其它不输出。\n\n"
                f"```json\n{skills_idx}\n```"
            )})
        except Exception:  # noqa: BLE001
            log.exception("T29-c: inject skills_index failed; fall back to legacy intent_summary")
            use_skills_idx = False  # 强制 fallback
    if not use_skills_idx and not fallback_intent:
        # 配置显式要求只用新索引,但注入失败 → 报错给运维
        log.error("T29-c: skills_index_enabled=true but injection failed AND fallback disabled")
    # P3j T16-B: 注入视频/YouTube 能力清单 — LLM 才知道能输出 [[EXEC: ...]]
    if not use_skills_idx:
        try:
            from prisir_work.agent_natural_video import intent_summary
            msgs.append({"role": "system", "content": intent_summary()})
            # 提示格式(EXEC 标记协议)
            msgs.append({"role": "system", "content": (
                "当用户的需求命中上面的能力,且你能拿到所需参数时,"
                "在回复末尾追加一行:\n"
                "  [[EXEC: <capability_id> k1=\"v1\" k2=\"v2\" ...]]\n"
                "参数必须是字符串字面量。L2/L3 能力(做视频 / 上传 YouTube)用户会单独"
                "确认一次,无需你提醒。只在能力清单明确列出的任务上输出 EXEC,其它不输出。"
            )})
        except Exception:  # noqa: BLE001
            log.exception("T16-B: inject intent_summary failed; fall back to plain system")
    # P3j Phase C(2026-09-27): 注入手绘海报能力 — ext-handraw-style-prompter 3 capability
    if not use_skills_idx:
        try:
            from prisir_work.poster_capabilities import intent_summary as poster_intent_summary
            msgs.append({"role": "system", "content": poster_intent_summary()})
        except Exception:  # noqa: BLE001
            log.exception("Phase C: inject poster intent_summary failed; fall back to no-poster mode")
    # P3j Phase D(2026-09-27): 注入 poster 卡片 → image-gen 出图闭环提示(下游动作)
    if not use_skills_idx:
        try:
            from prisir_work.poster_to_image_capability import intent_summary as poster2img_intent_summary
            msgs.append({"role": "system", "content": poster2img_intent_summary()})
        except Exception:  # noqa: BLE001
            log.exception("Phase D: inject poster2img intent_summary failed; fall back to no-image-gen-from-poster mode")
    # P3j free-for-dev Phase C(2026-09-27): 注入免费资源能力清单
    if not use_skills_idx:
        try:
            from prisir_work.free_for_dev_capabilities import intent_summary as free_intent_summary
            msgs.append({"role": "system", "content": free_intent_summary()})
        except Exception:  # noqa: BLE001
            log.exception("Phase C free-for-dev: inject free intent_summary failed; fall back to no-free-resource mode")
    # P3j 5-项目 Sprint 1(2026-10-02): 注入公共 API 资源能力清单
    if not use_skills_idx:
        try:
            from prisir_work.public_apis_capabilities import intent_summary as api_intent_summary
            msgs.append({"role": "system", "content": api_intent_summary()})
        except Exception:  # noqa: BLE001
            log.exception("Phase C public-apis: inject api intent_summary failed; fall back to no-public-api mode")
    # P3j 5-项目 Sprint 1(2026-10-02): 注入国内 API 资源能力清单
    if not use_skills_idx:
        try:
            from prisir_work.public_apis_cn_capabilities import intent_summary as api_cn_intent_summary
            msgs.append({"role": "system", "content": api_cn_intent_summary()})
        except Exception:  # noqa: BLE001
            log.exception("Phase C public-apis-cn: inject api_cn intent_summary failed; fall back to no-cn-api mode")
    # P3j 5-项目 Sprint 2(2026-10-02): 注入免 key API 资源能力清单
    if not use_skills_idx:
        try:
            from prisir_work.nokeyapi_capabilities import intent_summary as nokeyapi_intent_summary
            msgs.append({"role": "system", "content": nokeyapi_intent_summary()})
        except Exception:  # noqa: BLE001
            log.exception("Phase C nokeyapi: inject nokeyapi intent_summary failed; fall back to no-nokeyapi mode")
    # P3j 5-项目 Sprint 2(2026-10-02): 注入自部署软件资源能力清单
    if not use_skills_idx:
        try:
            from prisir_work.selfhost_capabilities import intent_summary as selfhost_intent_summary
            msgs.append({"role": "system", "content": selfhost_intent_summary()})
        except Exception:  # noqa: BLE001
            log.exception("Phase C selfhost: inject selfhost intent_summary failed; fall back to no-selfhost mode")
    # M3.25:清空上一轮的 knowledge hits,本轮重新填(M3.27.1:前端一轮一清,避免误把上一轮的 hits 挂到这轮 ai 气泡上)
    # M3.27.1:累积 hits 由 _DISPATCH_HITS 全局字典记录,派发时从那里取
    if sess is not None:
        sess.knowledge_hits = []
    # M3.23 上下文段:屏 + 知识库(并行,各自 timeout)
    cfg = _load_fcontext_cfg()
    if cfg.get("asr_screen_capture") or cfg.get("asr_knowledge_lookup"):
        try:
            extras, knowledge_hits = await _m323_enrich_context(
                current_user_text, cfg)
            for seg in extras:
                msgs.append({"role": "system", "content": seg})
            # M3.25:把知识库命中写到 sess,前端用
            if sess is not None and knowledge_hits:
                sess.knowledge_hits = list(knowledge_hits)
            # M3.27.1:累积记录 hits(派发时用)— 由 _DISPATCH_HITS 累积最近 20 轮
            if sess is not None and knowledge_hits:
                _record_dispatch_hits(sess.sid, knowledge_hits)
        except Exception:  # noqa: BLE001
            log.exception("[M3.23] enrich failed; fall back to plain messages")
    history = load_history(sess.sid)
    # 取最近 20 轮(40 条)
    tail = history[-40:]
    for h in tail:
        role = h.get("role")
        text = h.get("text", "")
        if not text:
            continue
        if role == "user":
            # 跳过 mic 占位(没真实文本的 turn)
            if text.startswith("[mic"):
                continue
            msgs.append({"role": "user", "content": text})
        elif role == "assistant":
            msgs.append({"role": "assistant", "content": text})
    # M3.45 P0-1 意图分发路由:把本轮 intent 加的 system 段插到 user 之前
    intent_inj = ""
    if sess is not None:
        try:
            intent_inj = getattr(sess, "jev_intent_system_inject", "") or ""
        except Exception:
            intent_inj = ""
    if intent_inj:
        msgs.append({"role": "system", "content": intent_inj})
    # P1-Instincts(2026-10-02): 置信度召回(借鉴 ECC continuous-learning-v2)
    # 阈值 0.5 才注入,top_n=6,在 intent 路由段之后、user prompt 之前。
    # 失败 fallback:任何 IO / 解析异常 → 静默,不抛(主对话不受影响)。
    try:
        from memory.instincts import InstinctStore
        _inst_store = InstinctStore()
        if _inst_store.is_enabled():
            _inst_hits = _inst_store.recall(current_user_text, top_n=6)
            if _inst_hits:
                _inst_block = _inst_store.format_for_prompt(_inst_hits)
                if _inst_block:
                    msgs.append({"role": "system", "content": _inst_block})
    except Exception:  # noqa: BLE001
        log.exception("instincts 注入失败(不影响主对话)")
    msgs.append({"role": "user", "content": current_user_text})
    # P4-Compaction(2026-10-02): 借鉴 jcode-compaction-core
    # 80% 触发 summary 软压, 95% 触发 hard 硬压。只对已拼好的 msgs 长度检查,
    # 不影响已 ship 的 segments 顺序。失败 fallback: 任何异常 → log.exception + 返原 msgs。
    # Step 7 简化设计: 无同步 llm_call 通道, 跳过 summary 软压, 只走紧急 hard 硬压。
    try:
        from memory.compaction import CompactionManager
        _comp_mgr = CompactionManager()
        if _comp_mgr.is_enabled():
            msgs, _action = _comp_mgr.compact(msgs, llm_call=None)
            if _action.value != "none":
                log.info("[compaction] 触发: %s", _action.value)
    except Exception:  # noqa: BLE001
        log.exception("compaction 失败(不影响主对话)")
    return msgs


async def _jev_guard(sess: CallSession, user_text: str) -> dict:
    """M3.45 P0-2 护栏:用户消息进 LLM 前先 Jev 一次。

    返回 decision(dict):{
      "pass": bool,                # True = 直通,调 LLM;False = 不调
      "need_confirm": bool,        # True = 需前端 confirm 后才调 LLM
      "reason": str,               # "jailbreak"|"risk_threshold"|"pass"|"jev_unavailable"|"already_confirmed"
      "risk": str,                 # "low"|"medium"|...
      "jailbreak_prob": float,
      "fallback_used": bool,       # True = 双通道全挂,fail-open 放行
      "judgments": dict|None,      # 原始 Jev 回答(给前端 / 日志用)
    }
    不抛异常 — 任何失败都 fail-open 放行(与无护栏现状一致,不恶化)。
    """
    try:
        cfg = _load_fcontext_cfg()
        if not cfg.get("jev_enabled"):
            return {"pass": True, "need_confirm": False,
                    "reason": "disabled", "risk": "unknown",
                    "jailbreak_prob": 0.0, "fallback_used": False,
                    "judgments": None}

        # 已 confirm 过(同一 sess 在 confirm 后短时间内不再弹)
        if getattr(sess, "jev_confirmed", False):
            return {"pass": True, "need_confirm": False,
                    "reason": "already_confirmed", "risk": "unknown",
                    "jailbreak_prob": 0.0, "fallback_used": False,
                    "judgments": None}

        timeout_s = float(cfg.get("jev_timeout_sec", 1.5))
        state = _jev_build_state(
            user_text,
            history_len=len(getattr(sess, "history", []) or []),
            user_tier="free",
        )
        try:
            judgments = await asyncio.wait_for(
                _jev_try(state, _JEV_GUARD_QUESTIONS, timeout_s=timeout_s),
                timeout=timeout_s + 0.3,  # 再给个 wrapper buffer
            )
        except asyncio.TimeoutError:
            log.warning("[jev-guard] wait_for 超时,fail-open")
            judgments = None
        except Exception as e:  # noqa: BLE001
            log.warning("[jev-guard] err: %s: %s", type(e).__name__, str(e)[:120])
            judgments = None

        decision = _jev_decide(judgments, cfg)
        # 决策翻译成业务语义
        return {
            "pass": not decision["block"] and not decision["need_confirm"],
            "need_confirm": decision["need_confirm"],
            "reason": decision["reason"],
            "risk": decision["risk"],
            "jailbreak_prob": decision["jailbreak_prob"],
            "fallback_used": decision["fallback_used"],
            "judgments": judgments,
        }
    except Exception as e:  # noqa: BLE001
        log.warning("[jev-guard] outer err: %s: %s", type(e).__name__, str(e)[:120])
        return {"pass": True, "need_confirm": False,
                "reason": "outer_err", "risk": "unknown",
                "jailbreak_prob": 0.0, "fallback_used": True,
                "judgments": None}


# ------------------------------------------------------------
# M3.45 P0-1 意图分发(2026-09-22)— 用户消息进 LLM 前先 Jev Choice
# ------------------------------------------------------------
INTENT_ROUTE_SYSTEM: dict[str, str] = {
    "chat":      "",  # 走默认(陪聊人设)
    "code":      (
        "\n\n[模式:技术问答] 用户在问编程/技术问题。"
        "回答要点:简洁准确,直接给代码或步骤;"
        "避免长段情感铺垫,除非用户明确要求解释概念。"
    ),
    "search":    (
        "\n\n[模式:事实查询] 用户在查事实/定义/历史/新闻。"
        "回答要点:简洁直给,优先 1-3 句;"
        "不确定就明说不要编,必要时建议用户核对。"
    ),
    "tool_call": (
        "\n\n[模式:操作请求] 用户希望 AI 执行某操作。"
        "回答要点:当前会话没有工具/插件权限,"
        "礼貌告知用户该走 PrisirAI 主面板(托盘菜单可派发),"
        "不要假装能执行。"
    ),
    "roleplay":  (
        "\n\n[模式:角色扮演] 用户想进入角色/讲故事/游戏剧情。"
        "回答要点:跟着用户设定的世界观走,保持角色一致性,"
        "但仍守住安全底线(不演反派教坏人)。"
    ),
}


async def _intents_guard(sess: CallSession, user_text: str) -> dict:
    """M3.45 P0-1:问 Jev 一次,给用户消息打 intent 标签 + 路由信息。

    Returns (不抛异常,任何失败都返 {"intent":"unknown",...}):
        {
          "intent": "chat"|"code"|"search"|"tool_call"|"roleplay"|"unknown",
          "intent_zh": "闲聊"|"技术"|...,   # 给前端显示
          "confidence": 0.0-1.0,
          "probabilities": {"chat":0.85,...},
          "system_inject": str,           # 按 intent 加的 system 段(空 = 不加)
          "route_applied": bool,          # 是否真的应用了路由(intent_routing)
          "fallback_used": bool,          # Jev 双通道都挂
          "elapsed_ms": int,
        }
    """
    empty = {
        "intent": "unknown", "intent_zh": "未识别",
        "confidence": 0.0, "probabilities": {},
        "system_inject": "", "route_applied": False,
        "fallback_used": True, "elapsed_ms": 0,
    }
    try:
        cfg = _load_fcontext_cfg()
        if not cfg.get("intent_enabled", True):
            return {**empty, "fallback_used": False}
        timeout_s = float(cfg.get("intent_timeout_sec", 1.0))
        t0 = time.time()
        try:
            intent = await asyncio.wait_for(
                _jev_ask_intent(
                    user_text,
                    history_len=len(getattr(sess, "history", []) or []),
                    user_tier="free",
                    timeout_s=timeout_s,
                ),
                timeout=timeout_s + 0.3,
            )
        except asyncio.TimeoutError:
            log.warning("[jev-intent] wait_for 超时,fail to unknown")
            return {**empty, "elapsed_ms": int((time.time()-t0)*1000)}
        except Exception as e:  # noqa: BLE001
            log.warning("[jev-intent] err: %s: %s",
                        type(e).__name__, str(e)[:120])
            return {**empty, "elapsed_ms": int((time.time()-t0)*1000)}
        elapsed_ms = int((time.time() - t0) * 1000)

        if not intent:
            return {**empty, "elapsed_ms": elapsed_ms}

        choice = intent.get("choice", "unknown")
        conf = float(intent.get("confidence", 0.0))
        probs = intent.get("probabilities") or {}
        min_conf = float(cfg.get("intent_min_confidence", 0.55))
        # 置信度低 → 视作 unknown(不路由)
        if conf < min_conf or choice == "unknown":
            choice_out = "unknown"
            sys_inj = ""
            route_applied = False
        else:
            choice_out = choice
            sys_inj = INTENT_ROUTE_SYSTEM.get(choice, "")
            route_applied = bool(cfg.get("intent_routing", True)) and bool(sys_inj)
        # 写到 sess 备用(前端 / 日志可读)
        try:
            sess.jev_intent = {
                "intent": choice_out,
                "intent_zh": _jev_intent_to_zh(choice_out),
                "confidence": conf,
                "probabilities": probs,
                "elapsed_ms": elapsed_ms,
            }
            # M3.45 P0-1:路由段写到独立字段,build_messages 读
            # 清掉上轮,避免污染本轮(意图是 per-message 的)
            sess.jev_intent_system_inject = sys_inj if route_applied else ""
        except Exception:
            pass
        return {
            "intent": choice_out,
            "intent_zh": _jev_intent_to_zh(choice_out),
            "confidence": conf,
            "probabilities": probs,
            "system_inject": sys_inj,
            "route_applied": route_applied,
            "fallback_used": False,
            "elapsed_ms": elapsed_ms,
        }
    except Exception as e:  # noqa: BLE001
        log.warning("[jev-intent] outer err: %s: %s",
                    type(e).__name__, str(e)[:120])
        return empty


async def _replan_llm_call(messages: list[dict]) -> str:
    """replan LLM 二次调用:复用 stream_chat,收集完整文本。

    P3j T29 Phase 3.5(2026-09-28):轻量 adapter,把流式 delta 拼成全文。
    失败 / 超时 → 抛 RuntimeError(给 plan_skill_calls fail-open 接住)。
    """
    from .companion_llm import stream_chat
    buf: list[str] = []
    try:
        async for evt, data in stream_chat(
            messages, strategy="smart", temperature=0.0, max_tokens=512,
        ):
            if evt == "delta":
                buf.append(data)
            # 'meta' / 'err' / 'done' 忽略
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"replan stream_chat: {exc}") from exc
    return "".join(buf).strip()


async def _maybe_skill_plan_replan(
    sess: CallSession, user_text: str, ai_text: str,
) -> None:
    """P3j T29 Phase 3.5:ai_done 后异步旁路问 LLM 该调哪些 skill。

    流程:
      1) 配置项 skills_replan_enabled=False → 直接返回
      2) plan_skill_calls 二次调 LLM(fail-open:任何失败返 [],主对话不受影响)
      3) need_replan=True → emit skill_plan_request ws 事件给前端弹规划卡
      4) need_replan=False 但 auto_executed → emit skill_plan_auto_executed 事件

    不阻塞主对话流(asyncio.create_task 调用)。
    """
    fcfg = _load_fcontext_cfg()
    if not fcfg.get("skills_replan_enabled"):
        return
    # ai_text 太短 / 显然没有 skill 调用意图 → 跳过,降噪
    if not user_text or len(user_text.strip()) < 2:
        return
    if ai_text.count("EXEC:") >= 1:
        # 主对话 LLM 已经写了 EXEC,replan 冗余 → 跳过
        return
    threshold = int(fcfg.get("skills_replan_auto_l1_threshold", 2))
    timeout_s = float(fcfg.get("skills_replan_timeout_sec", 8.0))

    from prisir_work.skills.replan import maybe_replan_and_execute

    async def _plan_llm(messages: list[dict]) -> str:
        return await asyncio.wait_for(
            _replan_llm_call(messages), timeout=timeout_s,
        )

    try:
        out = await maybe_replan_and_execute(
            user_text, _plan_llm,
            replan_enabled=True,
            auto_execute_l1_threshold=threshold,
        )
    except Exception:  # noqa: BLE001
        log.exception("replan_and_execute failed (non-fatal)")
        return

    calls = out.get("calls") or []
    if not calls:
        return  # empty_plan / 无 skill 命中

    if out.get("need_replan"):
        # 弹规划卡:前端复用 capability_confirm_request 卡片
        try:
            await sess.ws.send_json({
                "type": "skill_plan_request",
                "reason": out.get("reason", ""),
                "calls": [
                    {
                        "skill_id": c.skill_id,
                        "args": dict(c.args or {}),
                        "risk": c.risk,
                    }
                    for c in calls
                ],
                "source": "replan",
            })
        except Exception:  # noqa: BLE001
            log.exception("skill_plan_request send failed")
        return

    # auto_executed 路径:把结果也推一份给前端(只展示,前端不强确认)
    try:
        results = out.get("executed") or []
        await sess.ws.send_json({
            "type": "skill_plan_auto_executed",
            "reason": out.get("reason", ""),
            "calls": [
                {
                    "skill_id": c.skill_id,
                    "args": dict(c.args or {}),
                    "risk": c.risk,
                }
                for c in calls
            ],
            "results": [
                {"skill_id": r.skill_id, "ok": r.ok,
                 "payload": r.payload, "error": r.error}
                for r in results
            ],
        })
    except Exception:  # noqa: BLE001
        log.exception("skill_plan_auto_executed send failed")


async def real_llm_stream(sess: CallSession, user_text: str):
    """M3.6:调云端 LLM 流式 → 推 ai_delta/ai_done。
    故障转移中的 err 事件 → 静默不外抛(只在 sys msg 提示一次,避免污染流式气泡)。
    M3.25:build_messages 会写 sess.knowledge_hits,流开始前先 emit knowledge_refs。
    """
    messages = await build_messages(sess, user_text)
    # M3.25:命中知识库则先把 hits 推给前端(挂载在下一个 AI 气泡下)
    if sess.knowledge_hits:
        try:
            await sess.ws.send_json({
                "type": "knowledge_refs",
                "hits": list(sess.knowledge_hits),
            })
        except Exception:  # noqa: BLE001
            log.warning("push knowledge_refs failed")
    err_buf: list[str] = []
    async for evt, data in stream_chat(messages, strategy="smart",
                                         temperature=0.7, max_tokens=1024):
        if evt == "delta":
            yield data
        elif evt == "meta":
            sess.last_meta = data
        elif evt == "err":
            log.warning("LLM stream err: %s", data)
            err_buf.append(data)
            # 给前端一条 err msg(独立通道,不是 ai_delta)
            try:
                await sess.ws.send_json({"type": "err", "err": data})
            except Exception:  # noqa: BLE001
                pass
        # 'done' 不做事
    if err_buf and not (sess.last_meta or {}).get("platform"):
        # 全部平台失败,没拿到 meta → yield 一个 err 提示给 caller(便于 ai_done 显示)
        yield "(LLM 全部平台失败,请检查 keys.db 网络/密钥)"


# ============================================================
# WebSocket 处理(2026-09-16 M3.1)— 双向通道
# ============================================================

class CallSession:
    """一通陪聊 = 一个 sid + 一个 WebSocket 连接。
    字段全是 in-memory,对话内容落 jsonl。"""

    def __init__(self, sid: str, ws: web.WebSocketResponse):
        self.sid = sid
        self.ws = ws
        self.start_ts = time.time()
        self.barge_in = False  # 被打断标志
        self.abort = False  # LLM 流被取消
        # M3.2:当前正在录的音频累积(切到文件)
        self.audio_buf: list[bytes] = []
        self.audio_mime: str = "audio/webm"
        self.audio_recording: bool = False
        self.audio_started_ts: float = 0.0
        # M3.3:百炼 Paraformer 流式 ASR 会话(懒启动:第一次 audio_chunk 时建)
        self.asr = None  # type: ignore[assignment]  # 实际是 ProviderProtocol
        self.asr_provider: str = ""
        self.asr_partial_buf: str = ""
        self.asr_final_text: str = ""
        # M3.6:LLM 流式返回的元数据(供 ai_done 落库)
        self.last_meta: dict = {}
        # M3.11:闲置超时挂断(类似电话计费保护)
        # 用户忘记挂断 → N 秒无交互 → 自动 close ASR + 广播 idle_timeout
        self.last_activity_ts: float = time.time()
        self.idle_warned: bool = False  # 是否已发过警告
        self.idle_timeout_sec: int = IDLE_TIMEOUT_SEC
        # M3.25:本轮 ASR/text 命中的知识库片段(给前端展示「📎 来源 N」)
        # build_messages 在 enrich 时写,前端渲染 ai_done 时按此显示
        self.knowledge_hits: list[dict] = []
        # M3.45:本轮 Jev 风险判定缓存(供前端 confirm 卡显示)+ 一次性 confirm 标记
        self.jev_last_decision: dict = {}
        self.jev_confirmed: bool = False

    def touch(self) -> None:
        """任何用户/AI 活动都打一下戳。"""
        self.last_activity_ts = time.time()
        self.idle_warned = False

    def elapsed_str(self) -> str:
        sec = int(time.time() - self.start_ts)
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def idle_sec(self) -> float:
        return time.time() - self.last_activity_ts


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    sid = request.query.get("sid") or uuid.uuid4().hex[:12]
    sess = CallSession(sid, ws)
    log.info("WS open sid=%s peer=%s", sid, request.remote)
    # 握手:发续接卡片
    card = get_continue_card(sid)
    await ws.send_json({"type": "hello", "sid": sid, "continue": card})
    # 推历史(已落库的部分)
    history = load_history(sid)
    if history:
        await ws.send_json({"type": "history", "messages": history})

    # M3.11 闲置看门狗
    idle_task = asyncio.create_task(_idle_watchdog(sess))

    try:
        async for msg in ws:
            sess.touch()
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:  # noqa: BLE001
                    await ws.send_json({"type": "err", "err": "bad json"})
                    continue
                await handle_msg(sess, data)
            elif msg.type == WSMsgType.BINARY:
                # M3.2 二进制帧:约定 "\n" 切 header(json) + tail(raw bytes)
                raw: bytes = msg.data
                nl = raw.find(b"\n")
                if nl <= 0:
                    await ws.send_json({"type": "err", "err": "bad binary frame"})
                    continue
                try:
                    head = json.loads(raw[:nl].decode("utf-8"))
                    payload = raw[nl + 1:]
                except Exception:  # noqa: BLE001
                    await ws.send_json({"type": "err", "err": "bad binary header"})
                    continue
                await handle_binary(sess, head, payload)
            elif msg.type == WSMsgType.ERROR:
                log.warning("WS error sid=%s %s", sid, ws.exception())
                break
    finally:
        idle_task.cancel()
        try:
            await idle_task
        except (asyncio.CancelledError, Exception):
            pass
        log.info("WS close sid=%s elapsed=%s", sid, sess.elapsed_str())
        if sess.asr is not None:
            try:
                await sess.asr.finish()
            except Exception:  # noqa: BLE001
                pass
    return ws


async def _idle_watchdog(sess):
    """M3.11:每 10s 查 sess.idle_sec()。
    - 超过 (timeout - warn) 且未警告过 → 发 idle_warning(前端可弹提醒)
    - 超过 timeout → 关 ASR ws(停止计费)+ 广播 idle_timeout + 关 ws
    用户任何 ws 消息会 touch() → 自动重置
    """
    try:
        while True:
            await asyncio.sleep(5)  # 5s 间隔 — 确保 15s warning 不会错过
            idle = sess.idle_sec()
            if idle >= sess.idle_timeout_sec:
                log.info("[idle] sid=%s idle=%.0fs timeout,挂断并关 ASR",
                         sess.sid, idle)
                if sess.asr is not None:
                    try:
                        await sess.asr.finish()
                    except Exception as e:  # noqa: BLE001
                        log.warning("[idle] asr.finish err: %s", e)
                try:
                    await sess.ws.send_json({
                        "type": "idle_timeout",
                        "idle_sec": int(idle),
                        "timeout_sec": sess.idle_timeout_sec,
                        "msg": f"已 {int(idle)} 秒无交互,自动挂断停止 ASR 计费",
                    })
                except Exception:
                    pass
                try:
                    await sess.ws.close(code=1000, message=b"idle_timeout")
                except Exception:
                    pass
                return
            elif idle >= sess.idle_timeout_sec - IDLE_WARN_BEFORE_SEC and not sess.idle_warned:
                sess.idle_warned = True
                try:
                    await sess.ws.send_json({
                        "type": "idle_warning",
                        "idle_sec": int(idle),
                        "timeout_sec": sess.idle_timeout_sec,
                        "warn_sec": IDLE_WARN_BEFORE_SEC,
                        "msg": f"已 {int(idle)} 秒无交互,{IDLE_WARN_BEFORE_SEC} 秒后将自动挂断",
                    })
                except Exception:
                    pass
                log.info("[idle] sid=%s warn idle=%.0fs", sess.sid, idle)
    except asyncio.CancelledError:
        return


async def handle_msg(sess: CallSession, data: dict) -> None:
    t = data.get("type")
    if t == "ping":
        await sess.ws.send_json({"type": "pong", "ts": time.time()})
    elif t == "user_text":
        # 打字输入(text mode)→ 落库 + 调 LLM 流(M3.6 替换 echo)
        text = (data.get("text") or "").strip()
        if not text:
            return
        sess.barge_in = False
        sess.abort = False
        sess.last_meta = {}
        # M3.45 P0-2 护栏:Jev 前置安全评估
        guard = await _jev_guard(sess, text)
        if not guard["pass"]:
            await sess.ws.send_json({
                "type": "guard_block",
                "reason": guard["reason"],
                "risk": guard["risk"],
                "jailbreak_prob": guard["jailbreak_prob"],
                "need_confirm": guard["need_confirm"],
            })
            # 仍落 user_echo(用户看得到自己的输入),但不调 LLM
            append_turn(sess.sid, "user", text, src=data.get("src", "text"))
            await sess.ws.send_json({"type": "user_echo", "text": text})
            return
        # M3.45 P0-1 意图分发:Jev Choice 拿本轮 intent,可能改 system prompt
        intent_info = await _intents_guard(sess, text)
        try:
            await sess.ws.send_json({
                "type": "intent",
                "intent": intent_info["intent"],
                "intent_zh": intent_info["intent_zh"],
                "confidence": intent_info["confidence"],
                "probabilities": intent_info["probabilities"],
                "route_applied": intent_info["route_applied"],
                "elapsed_ms": intent_info["elapsed_ms"],
            })
        except Exception:
            pass
        # M3.27.1:触发词检测 → 弹确认卡 → 后端派发
        # (前端 app.js 也要检,这里给后端兜底 + 让 asr 路径也能触发)
        dispatch_auto = _should_dispatch(text)
        append_turn(sess.sid, "user", text, src=data.get("src", "text"))
        await sess.ws.send_json({"type": "user_echo", "text": text})
        # 流式回 AI
        full: list[str] = []
        try:
            async for tok in real_llm_stream(sess, text):
                if sess.abort:
                    break
                full.append(tok)
                await sess.ws.send_json({"type": "ai_delta", "text": tok})
        except Exception as e:  # noqa: BLE001
            log.exception("real_llm_stream failed")
            await sess.ws.send_json({"type": "err",
                                       "err": f"{type(e).__name__}: {e}"})
            return
        ai_text = "".join(full).strip()
        if ai_text:
            meta = sess.last_meta or {}
            append_turn(sess.sid, "assistant", ai_text, src="llm",
                        model=meta.get("model", ""),
                        platform=meta.get("platform", ""),
                        task_type=meta.get("task_type", ""))
            await sess.ws.send_json({"type": "ai_done", "text": ai_text,
                                       "elapsed": sess.elapsed_str(),
                                       "platform": meta.get("platform", ""),
                                       "model": meta.get("model", "")})
            # M3.45 P1-4 阶段成果增量入库(后台跑,不阻塞流)
            asyncio.create_task(_p14_bg_task(sess, text, ai_text))
            # P3j T29 Phase 3.5(2026-09-28):两阶段 replan 闸门—
            # ai_done 后异步旁路问 LLM「用户这条想调哪些 skill」,
            # L1+ > 阈值推 skill_plan_request 给前端弹规划卡(默认关,需配置项开)
            asyncio.create_task(_maybe_skill_plan_replan(sess, text, ai_text))
            # P3j T29 Phase 4(2026-09-28):EXEC ↔ tool_use 兼容 + 灰度切换 —
            # 按配置项 skills_exec_mode 路由(默认 both:EXEC 优先 + tool_use 兜底)
            fcfg_e = _load_fcontext_cfg()
            exec_mode = str(fcfg_e.get("skills_exec_mode", "both"))
            try:
                from prisir_work.skills.exec_compat import route_exec as _route_exec
                routed = _route_exec(ai_text, mode=exec_mode)
                calls = routed.get("calls") or []
                source = routed.get("source", "empty")
                if source == "empty" or not calls:
                    pass  # 空 plan,啥也不发
                elif source == "exec":
                    # EXEC 标记 → 走老 scan_and_exec 推 ws 事件(兼容老前端)
                    from prisir_work import agent_main_chat_hook as _hook
                    hook_events = _hook.scan_and_exec(ai_text)
                    for ev in hook_events:
                        try:
                            await sess.ws.send_json(ev)
                        except Exception:
                            log.exception("hook_event send failed")
                else:
                    # tool_use 路径:把 SkillCall 走老钩子 emit confirm_request 形态
                    # (复用 agent_main_chat_hook.build_confirm_request 兼容)
                    from prisir_work import agent_main_chat_hook as _hook
                    from prisir_work.skills.exec_compat import (
                        exec_marker_to_skill_call,
                    )
                    for c in calls:
                        # 构造 ExecMarker 用老 ws 事件模板(老前端不感知协议差异)
                        em = _hook.ExecMarker(
                            capability=c.skill_id,
                            args={k: str(v) for k, v in (c.args or {}).items()},
                            raw=c.raw or "",
                        )
                        ev = _hook.build_confirm_request(em)
                        try:
                            await sess.ws.send_json(ev)
                        except Exception:
                            log.exception("tool_use→EXEC ws send failed")
            except Exception:
                # 钩子模块不可用 / 解析崩溃 — 主对话流程不能受影响
                log.exception("Phase 4 route_exec failed")
    elif t == "barge_in":
        # 全双工打断(M3.5 占位):用户开说即停 echo 流
        sess.abort = True
        await sess.ws.send_json({"type": "barge_ack"})
    elif t == "jev_confirm":
        # M3.45 P0-2 护栏:前端弹 confirm 后用户点"我同意继续"
        # 标记 sess 后续短时间不再弹同一类风险(简单实现:本次会话永久)
        # 严格做可加 ttl,但护栏默认低频,简化即可
        sess.jev_confirmed = True
        await sess.ws.send_json({"type": "jev_confirm_ack"})
    elif t == "capability_confirm":
        # P3j T16-C:前端确认 capability 执行
        # data: {capability: "video.create", args: {...}, approved: bool}
        approved = bool(data.get("approved", False))
        capability_id = data.get("capability", "")
        args = data.get("args") or {}
        # 构造一个 EXEC 标记,然后走 scan_and_exec 的 confirm_callback 路径
        marker_text = f"[[EXEC: {capability_id} " + " ".join(
            f'{k}="{v}"' for k, v in args.items()) + "]]"
        try:
            from prisir_work import agent_main_chat_hook as _hook
            cb = (lambda m: approved) if approved else (lambda m: False)
            events = _hook.scan_and_exec(marker_text, confirm_callback=cb)
            for ev in events:
                await sess.ws.send_json(ev)
            await sess.ws.send_json({
                "type": "capability_confirm_ack",
                "capability": capability_id,
                "approved": approved,
                "event_count": len(events),
            })
        except Exception:
            log.exception("capability_confirm handler failed")
    elif t == "skill_plan_confirm":
        # P3j T29 Phase 5(2026-09-28):前端确认 skill_plan_request —
        # 顺序 execute_skill → emit capability_exec_result 复用老 UI
        # data: {calls: [{skill_id, args, risk}, ...], approved: bool}
        approved = bool(data.get("approved", False))
        calls = data.get("calls") or []
        if not approved or not calls:
            await sess.ws.send_json({
                "type": "skill_plan_confirm_ack",
                "approved": approved,
                "executed": 0,
                "reason": "user_cancelled" if not approved else "no_calls",
            })
        else:
            try:
                from prisir_work.skills.loader import execute_skill
                from prisir_work.skills.schema import SkillResult
                executed = 0
                for c in calls:
                    sid = str(c.get("skill_id") or "")
                    args = c.get("args") or {}
                    if not sid:
                        continue
                    try:
                        r = execute_skill(sid, dict(args), force=True)
                    except Exception as exc:  # noqa: BLE001
                        r = SkillResult(skill_id=sid, ok=False,
                                        error=f"plan_exec:{type(exc).__name__}:{exc}")
                    # 复用 capability_exec_result ws 事件(前端 0 改动)
                    try:
                        await sess.ws.send_json({
                            "type": "capability_exec_result",
                            "capability": r.skill_id,
                            "ok": r.ok,
                            "error": r.error or "",
                            "result": r.payload or {},
                        })
                    except Exception:
                        log.exception("plan_exec_result send failed")
                    if r.ok:
                        executed += 1
                await sess.ws.send_json({
                    "type": "skill_plan_confirm_ack",
                    "approved": True,
                    "executed": executed,
                    "total": len(calls),
                })
            except Exception:
                log.exception("skill_plan_confirm handler failed")
    elif t == "hangup":
        sess.abort = True
        await sess.ws.send_json({"type": "bye", "sid": sess.sid,
                                   "elapsed": sess.elapsed_str(),
                                   "turns": len(load_history(sess.sid))})
        await sess.ws.close()
    elif t == "list_sessions":
        await sess.ws.send_json({
            "type": "sessions",
            "items": list_recent_sessions(),
        })
    else:
        await sess.ws.send_json({"type": "err",
                                   "err": f"unknown type: {t!r}"})


# ============================================================
# 音频二进制帧处理(M3.2 webm/opus + M3.3 PCM 透传 ASR)
# 协议:text 头 + \n + raw bytes
# M3.3:前端 AudioWorklet 直接产 PCM16k Int16 mono(paraformer 只吃 pcm)
#       落盘保存 wav(magic + s16le + sample_rate=16000,可被任意工具读)
# ============================================================

EXT_BY_MIME = {
    "audio/webm": "webm",
    "audio/webm;codecs=opus": "webm",
    "audio/ogg;codecs=opus": "ogg",
    "audio/mp4": "m4a",
    "audio/pcm": "wav",
    "audio/pcm;rate=16000": "wav",
}

WAV_HEADER_TMPL = (
    b"RIFF" b"\x00\x00\x00\x00"
    b"WAVEfmt "
    b"\x10\x00\x00\x00"     # fmt chunk size 16
    b"\x01\x00"              # PCM
    b"\x01\x00"              # mono
    b"\x80\x3e\x00\x00"      # 16000 Hz
    b"\x00\x7d\x00\x00"      # byte rate = 16000*2
    b"\x02\x00"              # block align
    b"\x10\x00"              # 16 bits/sample
    b"data"
)


def wrap_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """给裸 PCM s16le mono 包一层 WAV header(便于 ffprobe / 任何播放器读)。"""
    import struct
    data_size = len(pcm_bytes)
    total = 36 + data_size
    header = WAV_HEADER_TMPL[:4] + struct.pack("<I", total) + WAV_HEADER_TMPL[8:]
    # 修正 sample_rate / byte_rate
    header = bytearray(header)
    header[24:28] = struct.pack("<I", sample_rate)
    header[28:32] = struct.pack("<I", sample_rate * 2)
    header[34:36] = struct.pack("<H", 16)
    header[40:44] = struct.pack("<I", data_size)
    return bytes(header) + pcm_bytes


async def ensure_asr_started(sess: CallSession):
    """懒启动 ASR(第一次 audio_chunk 时建,复用同一通陪聊里多次录音)。
    M3.3 fix:必须 await 直到 task-started 才返回,避免前端送的 chunk 全被丢。
    M3.10:通过 settings.json 选 provider + 工厂造 session。
    M3.17.3:走 fallback chain — primary 失败 8s 内无响应 → 切下一个 provider;
            失败信息广播给前端(UI toast 提示用户)。
    """
    if sess.asr is not None:
        if sess.asr._started:
            return sess.asr
    # M3.17.3 — 走 fallback chain
    chain = resolve_fallback_chain(DATA_DIR)
    log.info("ASR fallback chain=%s", chain)
    last_err = None
    for idx, name in enumerate(chain):
        provider_name, cfg = resolve_provider_cfg(DATA_DIR, provider_name=name)
        log.info("ASR try[%d/%d] provider=%s", idx + 1, len(chain), provider_name)
        try:
            asr = await try_start_provider(
                provider_name, cfg,
                {
                    "on_started": _make_asr_cb(sess, "started"),
                    "on_partial": _make_asr_cb(sess, "partial"),
                    "on_final": _make_asr_cb(sess, "final"),
                    "on_finished": _make_asr_cb(sess, "finished"),
                    "on_err": _make_asr_cb(sess, "err"),
                },
                started_timeout=8.0,
            )
            sess.asr_provider = provider_name
            sess.asr = asr
            if idx > 0:
                # 兜底成功 → 告诉前端「主 provider 失败,已切到备」
                await sess.ws.send_json({
                    "type": "asr_fallback",
                    "from_provider": chain[0],
                    "to_provider": provider_name,
                    "error": str(last_err) if last_err else "",
                })
            return asr
        except AsrStartError as e:
            log.warning("ASR provider=%s start failed phase=%s: %s",
                        e.provider_name, e.phase, e.cause)
            last_err = e
            continue
    # 全部失败
    msg = f"ASR 全失败:chain={chain} last_err={last_err}"
    log.error(msg)
    try:
        await sess.ws.send_json({"type": "err", "err": msg})
    except Exception:  # noqa: BLE001
        pass
    return None


def _make_asr_cb(sess: CallSession, kind: str):
    async def cb(*args, **kw):
        if kind == "started":
            await sess.ws.send_json({"type": "asr_started"})
        elif kind == "partial":
            text = args[0] if args else ""
            sess.asr_partial_buf = text
            await sess.ws.send_json({"type": "asr_partial", "text": text})
        elif kind == "final":
            text = args[0] if args else ""
            sess.asr_partial_buf = ""
            sess.asr_final_text = text
            await sess.ws.send_json({"type": "asr_final", "text": text})
            # 直接作为 user turn 落库 + 推 user_echo
            append_turn(sess.sid, "user", text, src="asr",
                        model="paraformer-realtime-v2")
            await sess.ws.send_json({"type": "user_echo", "text": text,
                                       "src": "asr"})
            # M3.45 P0-2 护栏(ASR 路径同样要前置)
            guard = await _jev_guard(sess, text)
            if not guard["pass"]:
                await sess.ws.send_json({
                    "type": "guard_block",
                    "reason": guard["reason"],
                    "risk": guard["risk"],
                    "jailbreak_prob": guard["jailbreak_prob"],
                    "need_confirm": guard["need_confirm"],
                })
                return
            # M3.45 P0-1 意图分发(ASR 路径)
            intent_info = await _intents_guard(sess, text)
            try:
                await sess.ws.send_json({
                    "type": "intent",
                    "intent": intent_info["intent"],
                    "intent_zh": intent_info["intent_zh"],
                    "confidence": intent_info["confidence"],
                    "probabilities": intent_info["probabilities"],
                    "route_applied": intent_info["route_applied"],
                    "elapsed_ms": intent_info["elapsed_ms"],
                })
            except Exception:
                pass
            # M3.6:触发 LLM 流式回答(real_llm_stream 内部会推 knowledge_refs)
            sess.last_meta = {}
            full: list[str] = []
            try:
                async for tok in real_llm_stream(sess, text):
                    if sess.abort:
                        break
                    full.append(tok)
                    await sess.ws.send_json({"type": "ai_delta", "text": tok})
            except Exception as e:  # noqa: BLE001
                log.exception("real_llm_stream (asr path) failed")
                await sess.ws.send_json({"type": "err",
                                           "err": f"{type(e).__name__}: {e}"})
                return
            ai_text = "".join(full).strip()
            if ai_text:
                meta = sess.last_meta or {}
                append_turn(sess.sid, "assistant", ai_text, src="llm",
                            model=meta.get("model", ""),
                            platform=meta.get("platform", ""),
                            task_type=meta.get("task_type", ""))
                await sess.ws.send_json({"type": "ai_done", "text": ai_text,
                                           "elapsed": sess.elapsed_str(),
                                           "platform": meta.get("platform", ""),
                                           "model": meta.get("model", "")})
                # M3.45 P1-4 阶段成果增量入库(asr 路径同样挂)
                asyncio.create_task(_p14_bg_task(sess, text, ai_text))
        elif kind == "finished":
            await sess.ws.send_json({"type": "asr_finished",
                                       "final": sess.asr_final_text})
        elif kind == "err":
            msg = args[0] if args else ""
            log.warning("ASR error: %s", msg)
            await sess.ws.send_json({"type": "err", "err": f"asr: {msg}"})
    return cb


async def handle_binary(sess: CallSession, head: dict, payload: bytes) -> None:
    t = head.get("type")
    if t == "audio_chunk":
        # 第一次收到 → 开新文件 + 启 ASR
        if not sess.audio_recording:
            sess.audio_recording = True
            sess.audio_buf = []
            sess.audio_started_ts = time.time()
            sess.audio_mime = head.get("mime", "audio/pcm")
            sess.asr_partial_buf = ""
            sess.asr_final_text = ""
            # 懒启动 ASR
            await ensure_asr_started(sess)
        sess.audio_buf.append(payload)
        # M3.3:实时送 PCM 给百炼(必须是 PCM16k mono,前端已按此规格送)
        if sess.asr is not None:
            try:
                await sess.asr.send_pcm(payload)
            except Exception as e:  # noqa: BLE001
                log.warning("asr.send_pcm failed: %s", e)
    elif t == "audio_end":
        # 收尾:落盘 → ASR finish-task → 落 user turn
        if not sess.audio_recording:
            return
        ext = EXT_BY_MIME.get(sess.audio_mime, "wav")
        sid_dir = MEDIA_DIR / sess.sid
        sid_dir.mkdir(parents=True, exist_ok=True)
        ts_str = f"{sess.audio_started_ts:.3f}".replace(".", "_")
        out = sid_dir / f"{ts_str}.{ext}"
        pcm = b"".join(sess.audio_buf)
        if ext == "wav":
            data = wrap_wav(pcm, sample_rate=16000)
        else:
            data = pcm
        out.write_bytes(data)
        dur_ms = int(head.get("durMs") or ((time.time() - sess.audio_started_ts) * 1000))
        bytes_n = len(data)
        await sess.ws.send_json({
            "type": "audio_saved", "path": str(out.relative_to(DATA_DIR)),
            "durMs": dur_ms, "bytes": bytes_n,
        })
        # M3.3:触发 ASR finish-task
        if sess.asr is not None:
            try:
                await sess.asr.finish()
            except Exception as e:  # noqa: BLE001
                log.warning("asr.finish failed: %s", e)
            sess.asr = None  # 下次录音重新建
        sess.audio_recording = False
        sess.audio_buf = []
    else:
        await sess.ws.send_json({"type": "err",
                                   "err": f"unknown binary type: {t!r}"})


# ============================================================
# HTTP 端点(2026-09-16 M3.1)— 历史 + 续接卡片(WS 已含,这是给 curl 调试用)
# ============================================================

async def handle_history(request: web.Request) -> web.Response:
    sid = request.query.get("sid", "")
    if not sid:
        return web.json_response({"ok": False, "err": "sid 必填"}, status=400)
    return web.json_response({"ok": True, "sid": sid,
                              "messages": load_history(sid)})


async def handle_sessions(request: web.Request) -> web.Response:
    if request.method == "DELETE":
        sid = request.query.get("sid", "")
        try:
            if sid:
                removed = delete_session(sid)
                return web.json_response({"ok": True, "deleted": sid, "removed": removed})
            else:
                n = clear_sessions()
                return web.json_response({"ok": True, "cleared": True, "count": n})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "err": f"{type(e).__name__}: {e}"})
    return web.json_response({"ok": True, "items": list_recent_sessions()})


async def handle_continue(request: web.Request) -> web.Response:
    sid = request.query.get("sid", "")
    if not sid:
        return web.json_response({"ok": False, "err": "sid 必填"}, status=400)
    return web.json_response({"ok": True, "card": get_continue_card(sid)})


# ============================================================
# Settings(M3.10)— ASR provider / API_KEY / 端点读写
# ============================================================

async def handle_settings_get(request: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "providers": list_providers(),
        "active": public_settings(DATA_DIR),
    })


async def handle_settings_post(request: web.Request) -> web.Response:
    """POST {active_provider, providers: {name: {api_key, ...}}} → 落 settings.json。
    api_key 字段若传空字符串 → 不覆盖(保留原值)。
    api_key 字段若传 "***" → 不覆盖(视为 mask 占位)。
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    s = load_settings(DATA_DIR)
    if "active_provider" in body:
        new_active = body["active_provider"]
        if new_active in PROVIDERS:
            s["active_provider"] = new_active
        else:
            return web.json_response({"ok": False, "err": f"未知 provider: {new_active}"},
                                      status=400)
    if "providers" in body and isinstance(body["providers"], dict):
        for name, cfg in body["providers"].items():
            if name not in PROVIDERS:
                continue
            spec = PROVIDERS[name]
            cur = s["providers"].get(name, {})
            for f in spec.fields:
                if f.key not in cfg:
                    continue
                v = cfg[f.key]
                if f.secret:
                    # 空 / "***" / 含 "…" → 都视为 mask 占位,不覆盖
                    sv = str(v).strip() if v is not None else ""
                    if not sv or sv == "***" or "…" in sv:
                        continue
                    cur[f.key] = sv
                else:
                    cur[f.key] = v
            s["providers"][name] = cur
    save_settings(DATA_DIR, s)
    return web.json_response({"ok": True, "active": public_settings(DATA_DIR)})


async def handle_settings_test(request: web.Request) -> web.Response:
    """测试 provider 是否配通:尝试构造一次 ASR 会话(走 start)。
    不发音频,只验证握手(task-started 是否返)。
    body: {provider, form_data} — provider 必填;form_data 与 /api/settings 同结构。
    行为:
      - 未填 provider                → 400 "provider 必填"
      - provider 不在注册表           → 400 "未知 provider"
      - 必填字段缺失(secret/api_key) → 400 "必填字段未填: ..."
      - 走 try_start_provider 8s 超时 → 真握手失败返 err
      - 成功                          → {ok:true, probe:..., version:...}
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    provider = (body.get("provider") or body.get("provider_name") or "").strip()
    if not provider:
        return web.json_response({"ok": False, "err": "provider 必填"}, status=400)
    if provider not in PROVIDERS:
        return web.json_response({"ok": False, "err": f"未知 provider: {provider}"},
                                  status=400)
    form_data = body.get("form_data") or body.get("cfg") or {}
    spec = PROVIDERS[provider]
    # 前端预检:必填字段不能空
    missing = [f.key for f in spec.fields if f.required
               and not str(form_data.get(f.key, "")).strip()]
    if missing:
        return web.json_response({"ok": False, "err": "必填字段未填: " + ", ".join(missing)},
                                  status=400)
    # 走真握手(限 8s,不发音频)
    started_evt = asyncio.Event()
    finished_evt = asyncio.Event()
    err_holder = {"err": None}
    def _on_started(): started_evt.set()
    def _on_err(msg): err_holder["msg"] = msg
    def _on_finished(): finished_evt.set()
    cbs = {"on_started": _on_started, "on_err": _on_err, "on_finished": _on_finished}
    sess = None
    try:
        sess = await try_start_provider(provider, dict(form_data), cbs, started_timeout=8.0)
    except AsrStartError as e:
        msg = str(e) if len(str(e)) < 200 else str(e)[:200] + "..."
        return web.json_response({"ok": False, "err": msg}, status=200)
        # M3.29.9 — local-funasr 端口拒接 → 翻译成人话
        if provider == "local-funasr" and ("ConnectionRefusedError" in msg or "[WinError 1225]" in msg):
            return web.json_response({
                "ok": False,
                "err": ("本地 ASR 服务未启动或端口不对。需先在系统中启动本地 server。\n"
                       "  • sherpa-onnx(轻量): pip install sherpa-onnx;\n"
                       "      下载 zipformer-zh-14M 模型(54MB int8);\n"
                       "      python -m sherpa_onnx.runtime.server --port 10096 --model <模型目录>\n"
                       "  • FunASR(高准度): docker run -p 10095:10095 funasr/funasr:latest\n"
                       "默认端点 ws://127.0.0.1:10096/(可在 endpoint 字段改)。"),
            }, status=200)
    except Exception as e:  # noqa: BLE001
        # M3.29.9 — local-funasr 端口拒接 → 翻译成人话
        raw = f"{type(e).__name__}: {e}"
        if provider == "local-funasr" and ("ConnectionRefusedError" in raw or "[WinError 1225]" in raw):
            return web.json_response({
                "ok": False,
                "err": ("本地 ASR 服务未启动或端口不对。需先在系统中启动本地 server。\n"
                       "  • sherpa-onnx(轻量): pip install sherpa-onnx;\n"
                       "      下载 zipformer-zh-14M 模型(54MB int8);\n"
                       "      python -m sherpa_onnx.runtime.server --port 10096 --model <模型目录>\n"
                       "  • FunASR(高准度): docker run -p 10095:10095 funasr/funasr:latest\n"
                       "默认端点 ws://127.0.0.1:10096/(可在 endpoint 字段改)。"),
            }, status=200)
        return web.json_response({"ok": False, "err": raw[:300]}, status=200)
    # 拿到了 → 立刻关闭
    try:
        if hasattr(sess, "close"):
            await sess.close()
    except Exception:  # noqa: BLE001
        pass
    return web.json_response({
        "ok": True,
        "probe": started_evt.is_set(),
        "provider": provider,
        "display": spec.display,
        "kind": spec.kind,
    })


# ============================================================
# M3.23 — ASR 上下文注入(屏 + 知识库)— settings + permission gate
# ============================================================

async def _fcontent_index_status() -> dict:
    """查 Fcontent 索引状态:已索引文件数 + 索引中的根。失败返 disabled。"""
    try:
        from prisir_fcontent import Fcontent  # noqa: PLC0415
        fc = Fcontent.shared()
        if fc is None:
            return {"status": "unavailable"}
        # 简化:返回库内总文件数 + roots(不查性能,只看是否 ready)
        with fc._mu:  # noqa: SLF001
            row = fc.conn.execute("SELECT COUNT(*) FROM files").fetchone()
            count = int(row[0]) if row else 0
            rows = fc.conn.execute(
                "SELECT root FROM roots ORDER BY root").fetchall()
            roots = [r[0] for r in rows]
        return {"status": "ok", "files": count, "roots": roots}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "err": str(e)}


# ============================================================
# M3.23 — ASR 上下文注入(屏 + 知识库)— settings + permission gate
# ============================================================


async def handle_m323_cfg_get(request: web.Request) -> web.Response:
    """M3.23:GET /api/m323/cfg → 返当前 companion_asr_settings.json 的 M3.23 段。"""
    cfg = _load_fcontext_cfg()
    status = await _fcontent_index_status()
    return web.json_response({"ok": True, "cfg": cfg, "fcontent": status})


async def handle_m323_cfg_post(request: web.Request) -> web.Response:
    """M3.23:POST /api/m323/cfg {asr_screen_capture, asr_knowledge_lookup, fcontent_root?}
    → 落 companion_asr_settings.json。

    - asr_screen_capture 从 False → True 需 permission gate(前端 confirm 后才能 POST)
    - asr_knowledge_lookup 同上
    - fcontent_root 第一次设时触发后台 enable + 建索引
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    cfg = _load_fcontext_cfg()
    new_screen = body.get("asr_screen_capture", cfg.get("asr_screen_capture"))
    new_know = body.get("asr_knowledge_lookup", cfg.get("asr_knowledge_lookup"))
    new_root = body.get("fcontent_root", cfg.get("fcontent_root"))
    cfg["asr_screen_capture"] = bool(new_screen)
    cfg["asr_knowledge_lookup"] = bool(new_know)
    if new_root is not None:
        cfg["fcontent_root"] = str(new_root).strip()
    # 容许其它字段(top_k / max_depth / timeout)也一并更新
    for k in ("screen_max_depth", "knowledge_top_k", "context_timeout_sec"):
        if k in body:
            try:
                cfg[k] = float(body[k]) if k == "context_timeout_sec" else int(body[k])
            except Exception:  # noqa: BLE001
                pass
    try:
        _FCONTEXT_CFG.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "err": f"write fail: {e}"},
                                  status=500)
    # 若知识库开了 + root 变了 → 后台建索引(不阻塞响应)
    # 测试时 ?dry=1 可跳过后台建索引(避免 fixture 卡住)
    if cfg["asr_knowledge_lookup"] and cfg["fcontent_root"] \
            and request.query.get("dry") != "1":
        asyncio.create_task(_fcontent_enable_background(cfg["fcontent_root"]))
    return web.json_response({"ok": True, "cfg": cfg})


async def _fcontent_enable_background(root: str) -> None:
    """后台把 root 加入 Fcontent 索引(不阻塞响应)。

    - 第一次 enable:扫描 root → 建索引
    - 后续 enable(同 root):跳过(幂等)
    - root 不存在 → 写日志,不报错
    """
    def _do_enable() -> dict:
        try:
            from prisir_fcontent import Fcontent  # noqa: PLC0415
            fc = Fcontent.shared()
            if fc is None:
                return {"status": "unavailable"}
            from pathlib import Path as _P
            rp = _P(root).expanduser().resolve()
            if not rp.exists() or not rp.is_dir():
                return {"status": "root_missing", "root": str(rp)}
            fc.enable(roots=[rp], ocr=False)
            return {"status": "ok", "root": str(rp)}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "err": str(e)}

    try:
        result = await asyncio.to_thread(_do_enable)
        log.info("[M3.23] fcontent enable: %s", result)
    except Exception as e:  # noqa: BLE001
        log.warning("[M3.23] fcontent enable outer err: %s", e)


async def handle_m323_fcontent_rebuild(request: web.Request) -> web.Response:
    """POST /api/m323/fcontent/rebuild → 强制重建索引(用于用户改了 root)。"""
    cfg = _load_fcontext_cfg()
    root = cfg.get("fcontent_root") or ""
    if not root:
        return web.json_response({"ok": False, "err": "fcontent_root 未配置"},
                                  status=400)
    asyncio.create_task(_fcontent_enable_background(root))
    return web.json_response({"ok": True, "queued": root})


# ============================================================
# M3.45 — Jev 护栏(2026-09-22)— settings + confirm
# ============================================================
_JEV_KEYS = ("jev_enabled", "jev_risk_threshold", "jev_jailbreak_threshold",
             "jev_timeout_sec", "jev_fail_open")

# M3.45 P0-1 意图分发(2026-09-22)— settings keys
_INTENT_KEYS = ("intent_enabled", "intent_timeout_sec",
                "intent_min_confidence", "intent_routing")

# M3.45 P1-4 阶段成果入库(2026-09-22)— settings keys
_P14_KEYS = ("p14_enabled", "p14_min_value", "p14_min_has_prob",
             "p14_timeout_sec", "p14_dir_name", "p14_topic_strategy")


async def handle_m345_jev_cfg_get(request: web.Request) -> web.Response:
    """GET /api/m345/jev/cfg → 返当前 jev_* + intent_* + p14_* 配置段。"""
    cfg = _load_fcontext_cfg()
    jev_cfg = {k: cfg.get(k, _FCONTEXT_DEFAULTS.get(k)) for k in _JEV_KEYS}
    intent_cfg = {k: cfg.get(k, _FCONTEXT_DEFAULTS.get(k)) for k in _INTENT_KEYS}
    p14_cfg = {k: cfg.get(k, _FCONTEXT_DEFAULTS.get(k)) for k in _P14_KEYS}
    return web.json_response({"ok": True,
                              "cfg": jev_cfg,
                              "intent_cfg": intent_cfg,
                              "p14_cfg": p14_cfg})


async def handle_m345_jev_cfg_post(request: web.Request) -> web.Response:
    """POST /api/m345/jev/cfg {jev_enabled, jev_risk_threshold, ...} → 落盘。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    cfg = _load_fcontext_cfg()
    # jev_enabled 转 bool,其它做类型 coerce
    if "jev_enabled" in body:
        cfg["jev_enabled"] = bool(body["jev_enabled"])
    if "jev_risk_threshold" in body:
        s = str(body["jev_risk_threshold"]).strip().lower()
        if s in ("safe", "low", "medium", "high", "critical"):
            cfg["jev_risk_threshold"] = s
    if "jev_jailbreak_threshold" in body:
        try:
            v = float(body["jev_jailbreak_threshold"])
            cfg["jev_jailbreak_threshold"] = max(0.0, min(1.0, v))
        except (TypeError, ValueError):
            pass
    if "jev_timeout_sec" in body:
        try:
            v = float(body["jev_timeout_sec"])
            cfg["jev_timeout_sec"] = max(0.5, min(10.0, v))
        except (TypeError, ValueError):
            pass
    if "jev_fail_open" in body:
        cfg["jev_fail_open"] = bool(body["jev_fail_open"])
    # M3.45 P0-1 意图分发配置
    if "intent_enabled" in body:
        cfg["intent_enabled"] = bool(body["intent_enabled"])
    if "intent_timeout_sec" in body:
        try:
            v = float(body["intent_timeout_sec"])
            cfg["intent_timeout_sec"] = max(0.3, min(5.0, v))
        except (TypeError, ValueError):
            pass
    if "intent_min_confidence" in body:
        try:
            v = float(body["intent_min_confidence"])
            cfg["intent_min_confidence"] = max(0.0, min(1.0, v))
        except (TypeError, ValueError):
            pass
    if "intent_routing" in body:
        cfg["intent_routing"] = bool(body["intent_routing"])
    # M3.45 P1-4 阶段成果入库配置
    if "p14_enabled" in body:
        cfg["p14_enabled"] = bool(body["p14_enabled"])
    if "p14_min_value" in body:
        try:
            v = int(body["p14_min_value"])
            cfg["p14_min_value"] = max(0, min(3, v))
        except (TypeError, ValueError):
            pass
    if "p14_min_has_prob" in body:
        try:
            v = float(body["p14_min_has_prob"])
            cfg["p14_min_has_prob"] = max(0.0, min(1.0, v))
        except (TypeError, ValueError):
            pass
    if "p14_timeout_sec" in body:
        try:
            v = float(body["p14_timeout_sec"])
            cfg["p14_timeout_sec"] = max(0.3, min(5.0, v))
        except (TypeError, ValueError):
            pass
    if "p14_dir_name" in body:
        s = str(body["p14_dir_name"]).strip() or "_incremental"
        # 防路径穿越
        s = s.replace("..", "_").replace("/", "_").replace("\\", "_")
        cfg["p14_dir_name"] = s
    if "p14_topic_strategy" in body:
        s = str(body["p14_topic_strategy"]).strip().lower()
        if s in ("auto", "manual", "off"):
            cfg["p14_topic_strategy"] = s
    try:
        _FCONTEXT_CFG.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "err": f"write fail: {e}"},
                                  status=500)
    return web.json_response({"ok": True,
                              "cfg": {
                                  k: cfg.get(k, _FCONTEXT_DEFAULTS.get(k))
                                  for k in _JEV_KEYS
                              },
                              "intent_cfg": {
                                  k: cfg.get(k, _FCONTEXT_DEFAULTS.get(k))
                                  for k in _INTENT_KEYS
                              },
                              "p14_cfg": {
                                  k: cfg.get(k, _FCONTEXT_DEFAULTS.get(k))
                                  for k in _P14_KEYS
                              }})


async def handle_m345_jev_test(request: web.Request) -> web.Response:
    """POST /api/m345/jev/test {text} → 跑一次真 Jev,返 judgments(给前端展示)。
    不写盘、不触发 guard,纯调试用。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"ok": False, "err": "empty text"},
                                  status=400)
    cfg = _load_fcontext_cfg()
    state = _jev_build_state(text, history_len=0, user_tier="free")
    try:
        judgments = await asyncio.wait_for(
            _jev_try(state, _JEV_GUARD_QUESTIONS,
                     timeout_s=float(cfg.get("jev_timeout_sec", 1.5))),
            timeout=float(cfg.get("jev_timeout_sec", 1.5)) + 1.0,
        )
    except asyncio.TimeoutError:
        return web.json_response({"ok": False, "err": "timeout"}, status=504)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False,
                                   "err": f"{type(e).__name__}: {e}"},
                                  status=500)
    if judgments is None:
        return web.json_response({"ok": False,
                                   "err": "双通道均失败(fail-open)"}, status=502)
    decision = _jev_decide(judgments, cfg)
    return web.json_response({"ok": True, "judgments": judgments,
                               "decision": decision})


# ------------------------------------------------------------
# M3.45 P0-1 意图分发 endpoint(2026-09-22)
# ------------------------------------------------------------
async def handle_m345_intent_test(request: web.Request) -> web.Response:
    """POST /api/m345/intent/test {text} → 跑一次 ask_intent,返 intent(给前端展示)。
    不写盘、不影响主路径,纯调试用。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"ok": False, "err": "empty text"},
                                  status=400)
    cfg = _load_fcontext_cfg()
    timeout_s = float(cfg.get("intent_timeout_sec", 1.0))
    t0 = time.time()
    try:
        intent = await asyncio.wait_for(
            _jev_ask_intent(text, history_len=0, user_tier="free",
                            timeout_s=timeout_s),
            timeout=timeout_s + 0.5,
        )
    except asyncio.TimeoutError:
        return web.json_response({"ok": False, "err": "timeout"}, status=504)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False,
                                   "err": f"{type(e).__name__}: {e}"},
                                  status=500)
    elapsed_ms = int((time.time() - t0) * 1000)
    if intent is None:
        return web.json_response({"ok": False,
                                   "err": "双通道均失败(fail to unknown)"},
                                  status=502)
    return web.json_response({"ok": True, "intent": intent,
                               "elapsed_ms": elapsed_ms})


# ------------------------------------------------------------
# M3.45 P1-4 阶段成果入库 endpoint(2026-09-22)
# ------------------------------------------------------------
async def handle_m345_p14_test(request: web.Request) -> web.Response:
    """POST /api/m345/p14/test {user_text, assistant_text} → 评估 + 真实入库。
    给前端 settings 测试按钮 + 调试用。
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    user_text = (body.get("user_text") or "").strip()
    assistant_text = (body.get("assistant_text") or "").strip()
    if not user_text or not assistant_text:
        return web.json_response(
            {"ok": False, "err": "user_text / assistant_text 必填"}, status=400)
    cfg = _load_fcontext_cfg()

    class _DummySess:
        pass

    sess = _DummySess()
    sess.history = []
    sess.jev_intent = {"intent": body.get("intent", "unknown")}
    t0 = time.time()
    res = await _p14_evaluate_and_ingest(sess, user_text, assistant_text, cfg)
    elapsed_ms = int((time.time() - t0) * 1000)
    # 测试模式:即使有重复段也强制测试 = 不强制入,直接返回 evaluate 即可;
    # 真实入库逻辑在 _p14_evaluate_and_ingest 内部跑
    if not res.get("triggered") and res.get("reason") in (
            "eval_timeout", "fail_soft", "eval_err"):
        # 把评估原始值也返(给前端调试)— 让 eval 自己管 timeout
        timeout_s = float(cfg.get("p14_timeout_sec", 1.2))
        try:
            raw_eval = await _jev_eval_stage(user_text, assistant_text,
                                             timeout_s=timeout_s)
            res["raw_eval"] = raw_eval
        except Exception:  # noqa: BLE001
            pass
    res["elapsed_ms"] = elapsed_ms
    return web.json_response({"ok": True, "result": res})


async def handle_m345_p14_stats(request: web.Request) -> web.Response:
    """GET /api/m345/p14/stats → 返索引统计(段数 / 文件数)。"""
    cfg = _load_fcontext_cfg()
    root = (cfg.get("fcontent_root") or "").strip()
    if not root:
        return web.json_response({"ok": True, "n_segments": 0, "n_files": 0,
                                   "root": ""})
    idx = _p14_load_index(root)
    files = set(idx.values())
    return web.json_response({"ok": True,
                              "n_segments": len(idx),
                              "n_files": len(files),
                              "root": root,
                              "dir_name": cfg.get("p14_dir_name", "_incremental")})


# ============================================================
# M3.27 — 派发到 PrisirAI(buffer + POST + settings toggle)
# ============================================================

async def api_dispatch(req: web.Request) -> web.Response:
    """M3.27.1 用户手动 / 触发词派发"""
    try:
        body = await req.json()
    except (TypeError, json.JSONDecodeError):
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    sid = (body.get("sid") or "").strip()
    if not sid:
        return web.json_response({"ok": False, "err": "sid 必填"}, status=400)
    mode = body.get("mode", "incremental")
    # 检查 toggle
    settings = load_settings(DATA_DIR)
    if not settings.get("enable_dispatch", True):
        return web.json_response({"ok": False, "err": "派发功能已关闭(在设置里开启)"}, status=403)
    result = await _do_dispatch(sid, mode)
    code = 200 if result.get("ok") else (502 if "不可达" in result.get("err", "") else 400)
    return web.json_response(result, status=code)


async def api_dispatch_buffer(req: web.Request) -> web.Response:
    """M3.27.1 前端轮询显示「待派发 N 条」"""
    sid = req.query.get("sid", "")
    buf = _DISPATCH_BUF.get(sid, [])
    cursor = _DISPATCH_CURSOR.get(sid, 0)
    hits_buf = _DISPATCH_HITS.get(sid, [])
    valid_hits = _filter_dispatch_hits(sid)
    return web.json_response({
        "sid": sid,
        "total": len(buf),
        "dispatched": cursor,
        "pending": max(0, len(buf) - cursor),
        "dispatches": _DISPATCH_COUNTER.get(sid, 0),
        "prisirai_url": PRISIRAI_INJECT_URL,
        "knowledge_refs_total": len(hits_buf),
        "knowledge_refs_valid": len(valid_hits),
    })


async def api_dispatch_settings(req: web.Request) -> web.Response:
    """M3.27.1 GET/POST enable_dispatch + dispatch_include_knowledge toggle
    M3.29.9 +prisirai_url_override:用户可改 PrisirAI 端点(留空走 env/default)"""
    if req.method == "GET":
        settings = load_settings(DATA_DIR)
        return web.json_response({
            "enable_dispatch": settings.get("enable_dispatch", True),
            "dispatch_include_knowledge": settings.get("dispatch_include_knowledge", True),
            "prisirai_url": _effective_prisirai_url(),
            "prisirai_url_override": settings.get("prisirai_url_override", ""),
            "trigger_phrases": DISPATCH_TRIGGER_PHRASES,
        })
    try:
        body = await req.json()
    except (TypeError, json.JSONDecodeError):
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    settings = load_settings(DATA_DIR)
    if "enable_dispatch" in body:
        settings["enable_dispatch"] = bool(body["enable_dispatch"])
    if "dispatch_include_knowledge" in body:
        settings["dispatch_include_knowledge"] = bool(body["dispatch_include_knowledge"])
    # M3.29.9 — PrisirAI URL override(空 = 恢复 env/default)
    if "prisirai_url_override" in body:
        url = (body["prisirai_url_override"] or "").strip()
        if url and not url.startswith(("http://", "https://")):
            return web.json_response(
                {"ok": False, "err": "URL 必须以 http:// 或 https:// 开头"}, status=400)
        settings["prisirai_url_override"] = url
    save_settings(DATA_DIR, settings)
    return web.json_response({"ok": True, "settings": {
        "enable_dispatch": settings["enable_dispatch"],
        "dispatch_include_knowledge": settings["dispatch_include_knowledge"],
        "prisirai_url_override": settings.get("prisirai_url_override", ""),
        "prisirai_url": _effective_prisirai_url(),
    }})


async def api_dispatch_test(req: web.Request) -> web.Response:
    """M3.29.9 真打 PrisirAI 端点(5s timeout)。
    POST /api/dispatch/test — 探测 override → env → default 当前生效的 URL。

    返回:
      {ok, url, status, ms, body_preview[, warning]}
      失败:{ok:false, url, err, ms}
    """
    import urllib.request, urllib.error
    url = _effective_prisirai_url()
    t0 = time.time()
    try:
        # 用空 payload 探测(只要能握手 → 返 4xx 也算"在监听";期望 PrisirAI 返 ok=false 但 HTTP 200)
        req_obj = urllib.request.Request(
            url, data=b'{"text":"", "source":"companion-test", "sid":"__test__"}',
            headers={"Content-Type": "application/json"}, method="POST")
        resp = urllib.request.urlopen(req_obj, timeout=5)
        body_text = resp.read().decode("utf-8", errors="replace")[:200]
        ms = int((time.time() - t0) * 1000)
        return web.json_response({
            "ok": True, "url": url, "status": resp.status, "ms": ms,
            "body_preview": body_text,
        })
    except urllib.error.HTTPError as e:
        # 4xx/5xx 仍视为"可达"(PrisirAI 在监听且能拒)
        ms = int((time.time() - t0) * 1000)
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        return web.json_response({
            "ok": True, "url": url, "status": e.code, "ms": ms,
            "body_preview": body_text, "warning": f"HTTP {e.code}",
        })
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return web.json_response({
            "ok": False, "url": url, "ms": ms,
            "err": f"{type(e).__name__}: {e}",
        })
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    provider_name = body.get("provider") or None
    overrides = body.get("cfg") or {}
    # 合并 overrides 到 resolve_provider_cfg 的结果上
    base_name, base_cfg = resolve_provider_cfg(DATA_DIR, provider_name)
    # UI 上 mask 字段(*** / 含 …)不覆盖 resolved cfg
    def _is_real(v):
        if v is None:
            return False
        s = str(v).strip()
        if not s or s == "***":
            return False
        if "…" in s:
            return False
        return True
    for k, v in overrides.items():
        if _is_real(v):
            base_cfg[k] = v
    log.info("[settings.test] name=%s api_key_len=%d overrides=%s",
             base_name, len(base_cfg.get("api_key", "") or ""),
             {k: ("***" if "key" in k.lower() else v) for k, v in overrides.items()})

    # 临时回调收集事件
    events: list[tuple[str, str]] = []

    async def cb_started():
        events.append(("started", ""))

    async def cb_err(msg):
        events.append(("err", msg))

    async def cb_finished():
        events.append(("finished", ""))

    try:
        asr = create_session(base_name, base_cfg, {
            "on_started": cb_started,
            "on_partial": lambda t: events.append(("partial", t)),
            "on_final": lambda t: events.append(("final", t)),
            "on_finished": cb_finished,
            "on_err": cb_err,
        })
        await asr.start()
        # 等 task-started 12s(百炼服务端偶尔抽风)
        for i in range(120):
            if asr._started or any(e[0] == "err" for e in events):
                log.info("[settings.test] break at %d*0.1s _started=%s events=%s",
                         i, asr._started, events)
                break
            await asyncio.sleep(0.1)
        # 立即关闭
        await asr.close()
        if any(e[0] == "err" for e in events):
            err = next(e[1] for e in events if e[0] == "err")
            return web.json_response({"ok": False, "err": err})
        if not asr._started:
            return web.json_response({"ok": False, "err": "task-started 超时(12s) — 检查网络/防火墙/百炼服务"})
        return web.json_response({"ok": True, "provider": base_name,
                                    "msg": "连接 + task-started 成功"})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "err": f"{type(e).__name__}: {e}"})


# ============================================================
# LLM provider catalog + keys 写入(M3.18)— 套 ASR 「下拉+填 key」模式
# ============================================================
async def handle_llm_providers_get(request: web.Request) -> web.Response:
    """GET /api/llm/providers → 返回所有平台 spec(给前端下拉用)。"""
    return web.json_response({"ok": True, "providers": _list_llm_providers()})


async def handle_llm_providers_post(request: web.Request) -> web.Response:
    """POST /api/llm/providers {platform_id, form_data} → 写 keys.db。

    - api_key 若为 "***" 或 "…" 视为 mask,不写入(保留原值)
    - form_data 里字段名必须与 spec.fields 一致(api_key / model / endpoint 等)
    """
    body = await request.json()
    platform_id = body.get("platform_id") or body.get("platform") or ""
    form_data = body.get("form_data") or body.get("fields") or {}
    if not platform_id:
        return web.json_response({"ok": False, "err": "platform_id 必填"}, status=400)
    # 过滤 mask 占位(M3.10.1 同款)
    clean = {}
    for k, v in form_data.items():
        s = (v or "").strip() if isinstance(v, str) else v
        if k == "api_key" and s in ("***", "…", ""):
            # mask / 空 → 不覆盖(保留原值)
            continue
        clean[k] = s
    try:
        keystore = PrisirKeyStore()
        if "api_key" not in clean:
            cur = keystore.get_key(platform_id)
            clean["api_key"] = (cur or {}).get("api_key", "")
        out = upsert_key_from_form(keystore, platform_id, clean)
        return web.json_response({"ok": True, **out})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "err": f"{type(e).__name__}: {e}"})


async def handle_llm_keys_list(request: web.Request) -> web.Response:
    """GET /api/llm/keys → 返回当前 keys.db 里所有平台(secret mask)。
    复用 list_llm_providers 拿 spec,补 keys.db 当前值。"""
    try:
        ks = PrisirKeyStore()
        out = []
        for p in _list_llm_providers():
            row = ks.get_key(p["platform_id"])
            if not row:
                continue
            ak = row.get("api_key", "")
            out.append({
                "platform_id": p["platform_id"],
                "display": p["display"],
                "base_url": row.get("base_url", p["base_url"]),
                "model": row.get("model", p["default_model"]),
                "api_key_masked": (ak[:4] + "…" + ak[-4:]) if len(ak) > 12 else "***",
                "kind": p["kind"],
                "note": p["note"],
            })
        return web.json_response({"ok": True, "configured": out})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "err": f"{type(e).__name__}: {e}"})


async def handle_index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC_DIR / "index.html")


# ============================================================
# M3.27.2 — GET /api/creds/status(前端查看当前启动探测结果)
# ============================================================
async def api_creds_status(req: web.Request) -> web.Response:
    """返当前启动探测到的 LLM/ASR 状态(给前端 UI 用)。

    字段:
      active_platform: settings.active_platform 或探测出来的首选
      available_platforms: env + keys.db 合并去重后的可用平台列表
      active_asr: settings.active_provider(本地 / 云端)
      local_asr_probe: 此刻探测的本地 ASR(可能是空)
      env_keys: 用户环境变量里实际有值的 key 列表(只返 platform id)
    """
    try:
        settings = load_settings(DATA_DIR)
    except Exception as e:  # noqa: BLE001
        return web.json_response(
            {"ok": False, "err": f"{type(e).__name__}: {e}"}, status=500)
    return web.json_response({
        "ok": True,
        "active_platform": settings.get("active_platform", ""),
        "available_platforms": settings.get("llm_available_platforms", []),
        "active_asr": settings.get("active_provider", ""),
        "local_asr_probe": _probe_local_asr(),
        "env_keys": sorted(_scan_env_keys().keys()),
        "prisirai_url": PRISIRAI_INJECT_URL,
    })


# ============================================================
# M3.36.C (2026-09-28) — colibri 三选一 onboarding 端点
#   GET  /api/colibri/onboarding        → {ok, should_show, reason, choice}
#   POST /api/colibri/onboarding/choose → {ok, choice} body={choice: "no_key"|"has_key"|"skip"}
# 设计原则:零侵入 — 只读 colibri_state,不写其他 settings
# ============================================================
async def api_colibri_onboarding(req: web.Request) -> web.Response:
    """返回前端是否弹引导卡(综合:state.choice / 已下载 / 已有云端 key)。"""
    try:
        from companion.colibri_state import (
            load_state, should_show_onboarding, has_existing_keys, is_model_path_set,
        )
        s = load_state()
        reason = ""
        if s.onboarding_choice in ("has_key", "skip"):
            reason = "user_dismissed"
        elif is_model_path_set(s):
            reason = "model_downloaded"
        elif has_existing_keys():
            reason = "has_existing_keys"
        else:
            reason = "first_run"
        return web.json_response({
            "ok": True,
            "should_show": should_show_onboarding(),
            "reason": reason,
            "choice": s.onboarding_choice,
            "downloaded": is_model_path_set(s),
            "state": s.state,
        })
    except Exception as e:  # noqa: BLE001
        return web.json_response(
            {"ok": False, "err": f"{type(e).__name__}: {e}"}, status=500)


async def api_colibri_onboarding_choose(req: web.Request) -> web.Response:
    """记录用户三选一选择 + 触发对应副作用。

    choice == "no_key"  → 触发后台下载,前端会轮询 /api/colibri/status
    choice == "has_key" → 关闭引导,前端引导用户去设置页
    choice == "skip"    → 关闭引导,下次启动还会弹(因为 onboarding_choice 不持久化 dismiss)
    """
    import json as _json
    try:
        body = await req.json()
    except (TypeError, _json.JSONDecodeError):
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"ok": False, "err": "bad body"}, status=400)
    choice = (body.get("choice") or "").strip()
    if choice not in ("no_key", "has_key", "skip"):
        return web.json_response(
            {"ok": False, "err": f"unknown choice: {choice}"}, status=400)

    try:
        from companion.colibri_state import (
            load_state, save_state, update_state,
        )
        import time as _time
        # "skip" 不持久化 dismiss(下次还会弹);其他两个持久化 choice
        if choice == "skip":
            # 只更新 onboarding_at,onboarding_choice 留空,should_show 仍 True
            update_state(onboarding_at=int(_time.time()))
            log.info("[M3.36.C] onboarding skip (next launch will re-prompt)")
            return web.json_response({"ok": True, "choice": "skip"})
        # no_key / has_key 都持久化
        update_state(onboarding_choice=choice, onboarding_at=int(_time.time()))
        if choice == "no_key":
            # 触发后台下载(Phase B 完整实现;Phase C 这边只占位)
            try:
                from companion.colibri_download import request_download
                request_download()   # 异步执行,不阻塞响应
                log.info("[M3.36.C] onboarding no_key → trigger OLMoE download")
            except ImportError:
                # Phase B 还没 ship — 优雅降级,只记录选择
                log.warning("[M3.36.C] colibri_download 未就绪(Phase B 待 ship);仅记录 choice")
        else:
            log.info("[M3.36.C] onboarding has_key → user will configure key manually")
        return web.json_response({"ok": True, "choice": choice})
    except Exception as e:  # noqa: BLE001
        return web.json_response(
            {"ok": False, "err": f"{type(e).__name__}: {e}"}, status=500)


# ============================================================
# M3.27.4 (2026-09-18) — 主面板 k-platform-pick 切 ASR 调用
# 路径:主面板 18802 /api/asr/active → 转发到此路由
# 行为:更新 settings.active_provider,只读 settings.active_provider
#      + 写 active_provider(不动 providers[name].* 字段,留给 UI 配)
# ============================================================
async def api_asr_active(req: web.Request) -> web.Response:
    """M3.27.4:切换 active ASR provider(主面板 k-platform-pick 调用)。

    body: {"active_provider": "<provider_name>"}
    仅更新 active_provider(完整 key 配置走原有 UI 路径)。
    """
    import json as _json
    try:
        body = await req.json()
    except (TypeError, _json.JSONDecodeError):
        return web.json_response({"ok": False, "err": "bad json"}, status=400)
    name = (body.get("active_provider") or "").strip() if isinstance(body, dict) else ""
    if not name:
        return web.json_response({"ok": False, "err": "active_provider 必填"}, status=400)
    if name not in PROVIDERS:
        return web.json_response({"ok": False, "err": f"未知 ASR provider: {name}"}, status=400)
    try:
        settings = load_settings(DATA_DIR)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "err": f"load_settings: {e}"}, status=500)
    settings["active_provider"] = name
    try:
        save_settings(DATA_DIR, settings)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "err": f"save_settings: {e}"}, status=500)
    log.info("M3.27.4 active_provider switched to %s", name)
    return web.json_response({"ok": True, "active_provider": name})


# ============================================================
# M3.28 Phase 2 Path B:落雪 LX Music Desktop 23330 遥控
# 范围:LXBridge 单例 + /api/music/cmd 转发 + 多类 ws 事件(music_state/progress/lyric/health)
# 不做:jsdom shim 拉直链(并到 Phase 3「agent 自己搜新歌」备用)
# ============================================================

import threading as _threading

# 单例 LxBridge(懒加载,失败返 None 让上层返 503)
_lx_bridge: Optional[Any] = None
_lx_bridge_lock = _threading.Lock()
_lx_bridge_subscribers: "set[Any]" = set()  # 当前 ws 订阅队列集合
_lx_bridge_consumer_task: Optional[Any] = None


def _get_lx_bridge() -> Optional[Any]:
    """懒加载 LxBridge。LX Desktop 没跑时返 None。"""
    global _lx_bridge
    with _lx_bridge_lock:
        if _lx_bridge is not None:
            return _lx_bridge
        try:
            from music.lx_bridge import LxBridge  # type: ignore
            _lx_bridge = LxBridge()
            log.info("[M3.28] LxBridge 实例化完成,等 start()")
            return _lx_bridge
        except Exception as e:  # noqa: BLE001
            log.warning("[M3.28] LxBridge 实例化失败: %s", e)
            _lx_bridge = None
            return None


async def _ensure_lx_bridge_started() -> Optional[Any]:
    """确保 LxBridge.start() 已跑(SSE + poll loop)。"""
    global _lx_bridge, _lx_bridge_consumer_task
    b = _get_lx_bridge()
    if b is None:
        return None
    if not getattr(b, "_started_ok", False):
        try:
            await b.start()
            b._started_ok = True
            log.info("[M3.28] LxBridge started (SSE + poll)")
        except Exception as e:  # noqa: BLE001
            log.warning("[M3.28] LxBridge.start() 失败: %s", e)
            return None
    # 启 ws 事件分发 consumer(只起一次)
    if _lx_bridge_consumer_task is None or _lx_bridge_consumer_task.done():
        _lx_bridge_consumer_task = asyncio.create_task(
            _lx_bridge_consume_loop(), name="lx_bridge.ws_consumer"
        )
    return b


async def _lx_bridge_consume_loop() -> None:
    """后台 task:从 LxBridge.subscribe() queue 取事件 → 广播到所有 ws"""
    b = _get_lx_bridge()
    if b is None:
        return
    q = await b.subscribe()
    try:
        while True:
            msg = await q.get()
            # 序列化 + 广播
            payload = json.dumps(msg, ensure_ascii=False)
            dead: List[Any] = []
            with _lx_bridge_lock:
                subs = list(_lx_bridge_subscribers)
            for ws in subs:
                try:
                    await ws.send_str(payload)
                except Exception:
                    dead.append(ws)
            if dead:
                with _lx_bridge_lock:
                    for d in dead:
                        _lx_bridge_subscribers.discard(d)
    except asyncio.CancelledError:
        return
    except Exception as e:  # noqa: BLE001
        log.warning("[M3.28] consume_loop err: %s", e)


async def api_music_cmd(req: web.Request) -> web.Response:
    """POST /api/music/cmd — M3.28 Phase 2 Path B 单端点

    body: {"action": "play|pause|resume|next|prev|stop|seek|volume|mute|collect|uncollect|status",
           "offset"?: float, "volume"?: 0-100, "on"?: bool}
    action=play 在 Path B = LX /play(恢复暂停状态);不用 keyword。
    """
    body: Dict[str, Any] = {}
    try:
        body = await req.json() if req.body_exists else {}
    except json.JSONDecodeError:
        return web.json_response({"ok": False, "err": "bad json"}, status=400)

    action = (body.get("action") or "").strip()
    if not action:
        return web.json_response({"ok": False, "err": "missing action"}, status=400)

    b = await _ensure_lx_bridge_started()
    if b is None:
        return web.json_response(
            {"ok": False, "err": "LX Music Desktop 未运行或端口 23330 未开;请打开落雪 desktop(设置 → 开启 Open API)"},
            status=503,
        )
    if not b.state.lx_alive and action != "status":
        return web.json_response(
            {"ok": False, "err": f"LX Desktop 不可达: {b.state.last_error or 'unknown'};先确认落雪在跑"},
            status=503,
        )

    kwargs: Dict[str, Any] = {}
    if "offset" in body:
        kwargs["offset"] = body["offset"]
    if "volume" in body:
        kwargs["volume"] = body["volume"]
    if "on" in body:
        kwargs["on"] = body["on"]

    try:
        result = await b.cmd(action, **kwargs)
    except Exception as e:  # noqa: BLE001
        log.warning("[M3.28] cmd %s exception: %s", action, e)
        return web.json_response({"ok": False, "action": action, "err": f"exception: {e}"}, status=500)

    status = 200 if result.get("ok") else 502
    return web.json_response(result, status=status)


async def api_music_state(req: web.Request) -> web.Response:
    """GET /api/music/state — Path B 返 LX bridge 完整 state"""
    b = await _ensure_lx_bridge_started()
    if b is None:
        return web.json_response(
            {"ok": False, "lx_alive": False, "err": "LX Music Desktop 未运行"},
            status=503,
        )
    s = b.state.to_dict()
    return web.json_response({
        "ok": True,
        "playing": s.get("status") == "playing",
        "track": {
            "name": s.get("name", ""),
            "singer": s.get("singer", ""),
            "album": s.get("album", ""),
        },
        "progress": s.get("progress", 0.0),
        "duration": s.get("duration", 0.0),
        "status": s.get("status", ""),
        "lyric_line_text": s.get("lyric_line_text", ""),
        "lyric_current_idx": s.get("lyric_current_idx", -1),
        "lyric_lines_count": len(b.state.lyric_lines),
        "lx_alive": s.get("lx_alive", False),
        "updated_at": s.get("updated_at", 0),
    })


async def api_music_status(req: web.Request) -> web.Response:
    """GET /api/music/status — Path B 诊断:bridge 状态 + LX 连通性 + 歌词行数"""
    b = await _ensure_lx_bridge_started()
    if b is None:
        return web.json_response({"ok": False, "lx_alive": False, "err": "LxBridge not started"})
    s = b.state.to_dict()
    return web.json_response({
        "ok": True,
        "lx_alive": s.get("lx_alive", False),
        "last_error": s.get("last_error", ""),
        "name": s.get("name", ""),
        "singer": s.get("singer", ""),
        "status": s.get("status", ""),
        "progress": s.get("progress", 0.0),
        "duration": s.get("duration", 0.0),
        "lyric_lines_count": len(b.state.lyric_lines),
        "lyric_current_idx": s.get("lyric_current_idx", -1),
        "lyric_line_text": s.get("lyric_line_text", ""),
        "updated_at": s.get("updated_at", 0),
    })


async def api_music_dispatch(req: web.Request) -> web.Response:
    """GET /api/music/dispatch — M3.29.4 dispatch 端点

    返回当前 music web(M3.29 自实现播放器)状态:
      - running: music web 是否在跑
      - port/url/lyrics_url: music web 地址
      - source: 'self' | 'lx' | 'none' — 当前用哪个
      - source_preference: 'auto' | 'self' | 'lx' — 配置偏好
      - can_dispatch: True if self 可启动(LX 不可达 + 配置非 lx)

    companion 前端用这个端点判断:
      - source=self → 显示当前 music web URL(可点)
      - source=lx → 仍走 LX Path B
      - source=none → 顶栏「🎵 启动播放器」按钮 → Tauri tray 唤起
    """
    import os
    # 从 HKCU 注册表读 music web 端口
    music_port = None
    music_alive = False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PrisirAI") as k:
            music_port = int(winreg.QueryValueEx(k, "music_port")[0])
    except Exception:
        pass

    if music_port:
        try:
            import socket
            s = socket.socket()
            s.settimeout(0.5)
            s.connect(("127.0.0.1", music_port))
            s.close()
            music_alive = True
        except Exception:
            music_alive = False

    # LX Desktop alive?
    b = await _ensure_lx_bridge_started()
    lx_alive = bool(b and b.state.lx_alive)
    # source preference (agent-only cfg)
    src_pref = "auto"
    try:
        from music.music_cfg import get_music_source_preference  # type: ignore
        src_pref = get_music_source_preference()
    except Exception:
        pass

    if music_alive and (src_pref in ("auto", "self")):
        source = "self"
    elif lx_alive and src_pref in ("auto", "lx"):
        source = "lx"
    elif music_alive:
        source = "self"
    elif lx_alive:
        source = "lx"
    else:
        source = "none"

    out = {
        "ok": True,
        "source": source,
        "source_preference": src_pref,
        "self_running": music_alive,
        "self_port": music_port,
        "self_url": f"http://127.0.0.1:{music_port}" if music_port else None,
        "self_lyrics_url": f"http://127.0.0.1:{music_port}/lyrics" if music_port else None,
        "lx_alive": lx_alive,
        # M3.29.4 dispatch hint:PrisirAI tray menu 「启动音乐播放器」
        # companion 不能直接 spawn 子进程 — 走 PrisirAI 壳的 `start_music_cmd`
        "tauri_dispatch_hint": {
            "action": "start_music_cmd",
            "note": "通过 PrisirAI Tauri 托盘菜单启动 music web,或通过 /api/music/start 兜底",
        },
    }
    return web.json_response(out)


# ====================================================================
# M3.29.4 music dispatch 兜底:companion 直接 spawn music web(无 Tauri 时)
# ====================================================================
_music_proc = None
_music_proc_lock = _threading.Lock()


async def api_music_start(req: web.Request) -> web.Response:
    """POST /api/music/start — companion 兜底启动 music web(无 Tauri 场景)

    PrisirAI 壳在 → 用 Tauri 托盘
    裸跑 companion → 用这个端点

    注意:这个端点不应该被任何"用户可见设置"调用 — 只是给前端兜底

    响应统一含 port + url,前端可直接打开;若已跑则返 {ok:True, note:'already running', port, url}
    """
    global _music_proc
    # 读端口(总是返 — 已跑或新 spawn 都返)
    import winreg

    def _read_port() -> int | None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PrisirAI") as k:
                return int(winreg.QueryValueEx(k, "music_port")[0])
        except Exception:
            return None

    def _is_alive() -> bool:
        """综合判断 music web 是否真的活着:
        1. HKCU 端口能连通 → 算 alive(任何人启动的 music web 都算数)
        2. _music_proc 句柄还在(且 poll 没返非 None)→ 算 alive
        3. 否则死了

        关键:M3.29.7 教训 — 单纯靠 _music_proc.poll() 不够,因为 Windows 上
        旧 Popen 句柄的 poll() 在无 wait() 时仍可能返 None(僵尸态)。
        必须以端口连通性为准。"""
        p = _read_port()
        port_alive = False
        if p:
            try:
                import socket as _sock
                with _sock.create_connection(("127.0.0.1", p), timeout=1.0):
                    port_alive = True
            except OSError:
                port_alive = False
        if port_alive:
            return True
        # 端口死了 → 即便 _music_proc 还认为活着,也按死处理
        return False

    with _music_proc_lock:
        if _is_alive():
            p = _read_port()
            return web.json_response({
                "ok": True, "note": "already running",
                "port": p, "url": f"http://127.0.0.1:{p}" if p else None,
                "lyrics_url": f"http://127.0.0.1:{p}/lyrics" if p else None,
                "pid": _music_proc.pid if _music_proc else 0,
            })
        # 否则 _music_proc 已死(僵尸 / 假死),重置为 None 走 spawn
        _music_proc = None

    # 找脚本路径
    import subprocess
    candidates = [
        Path(__file__).resolve().parent / "prisiragent-music-web.py",
        Path(__file__).resolve().parent.parent / "companion" / "prisiragent-music-web.py",
    ]
    script = None
    for p in candidates:
        if p.exists():
            script = p
            break
    if script is None:
        return web.json_response({"ok": False, "error": "music_web script not found"})

    log.info("[musicStart] spawning %s", script)
    _music_proc = subprocess.Popen(
        [sys.executable, "-B", str(script), "--port", "0", "--host", "127.0.0.1"],
        cwd=str(script.parent.parent),
        stdin=subprocess.DEVNULL,
        stdout=open(Path(dirs_log()) / "music-stdout.log", "ab", buffering=0) if dirs_log() else subprocess.DEVNULL,
        stderr=open(Path(dirs_log()) / "music-stderr.log", "ab", buffering=0) if dirs_log() else subprocess.DEVNULL,
    )
    # 等 1.5s 看是否注册到端口
    import asyncio
    await asyncio.sleep(1.5)
    p = _read_port()
    if p:
        return web.json_response({
            "ok": True, "port": p, "pid": _music_proc.pid,
            "url": f"http://127.0.0.1:{p}",
            "lyrics_url": f"http://127.0.0.1:{p}/lyrics",
        })
    return web.json_response({"ok": True, "note": "spawned, port not yet registered", "pid": _music_proc.pid})


def dirs_log() -> str:
    """log dir for music stdout/stderr(沿用 companion 已有 dirs)"""
    try:
        import dirs as _dirs
        d = _dirs.user_log_dir("prisirai-companion", "Prisir")
        Path(d).mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return ""


async def ws_music_handler(req: web.Request) -> web.WebSocketResponse:
    """GET /api/music/ws — Path B 多类事件推送

    事件类型:
      music_state   — 状态(name/singer/album/status/progress/duration/lyric_line_text/...)
      music_progress — 进度小步进(节流 1s)
      music_lyric   — 歌词全量/当前行
      music_health  — LX Desktop 连通性变化
    """
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(req)
    with _lx_bridge_lock:
        _lx_bridge_subscribers.add(ws)
    # 立即推一次 state
    b = await _ensure_lx_bridge_started()
    if b is not None:
        try:
            await ws.send_str(json.dumps({
                "type": "music_state",
                **b.state.to_dict(),
                "ts": int(time.time() * 1000),
            }, ensure_ascii=False))
        except Exception:
            pass
    try:
        async for _msg in ws:  # 不处理上行消息
            pass
    finally:
        with _lx_bridge_lock:
            _lx_bridge_subscribers.discard(ws)
    return ws


# ============================================================
# App 工厂
# ============================================================

def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/history", handle_history)
    app.router.add_route("*", "/api/sessions", handle_sessions)
    app.router.add_get("/api/continue", handle_continue)
    # M3.10 settings
    app.router.add_get("/api/settings", handle_settings_get)
    app.router.add_post("/api/settings", handle_settings_post)
    app.router.add_post("/api/settings/test", handle_settings_test)
    # M3.29.8 — 语伴页设置下拉专用(只列 ASR 厂商,非 LLM)
    app.router.add_get("/api/asr/providers", lambda r: web.json_response(
        {"ok": True, "providers": list_providers()}))
    # M3.18 LLM providers(下拉选厂商 + 填 key)
    app.router.add_get("/api/llm/providers", handle_llm_providers_get)
    app.router.add_post("/api/llm/providers", handle_llm_providers_post)
    app.router.add_get("/api/llm/keys", handle_llm_keys_list)
    # M3.23 ASR 上下文注入 settings
    app.router.add_get("/api/m323/cfg", handle_m323_cfg_get)
    app.router.add_post("/api/m323/cfg", handle_m323_cfg_post)
    app.router.add_post("/api/m323/fcontent/rebuild", handle_m323_fcontent_rebuild)
    # M3.45 Jev 护栏(2026-09-22)— settings + 真探测端点
    app.router.add_get("/api/m345/jev/cfg", handle_m345_jev_cfg_get)
    app.router.add_post("/api/m345/jev/cfg", handle_m345_jev_cfg_post)
    app.router.add_post("/api/m345/jev/test", handle_m345_jev_test)
    # M3.45 P0-1 意图分发
    app.router.add_post("/api/m345/intent/test", handle_m345_intent_test)
    # M3.45 P1-4 阶段成果入库
    app.router.add_post("/api/m345/p14/test", handle_m345_p14_test)
    app.router.add_get("/api/m345/p14/stats", handle_m345_p14_stats)
    # M3.27 派发到 PrisirAI
    app.router.add_post("/api/dispatch", api_dispatch)
    app.router.add_get("/api/dispatch/buffer", api_dispatch_buffer)
    app.router.add_route("*", "/api/dispatch/settings", api_dispatch_settings)
    # M3.29.9 真打 PrisirAI 探测端点
    app.router.add_post("/api/dispatch/test", api_dispatch_test)
    # M3.27.2 — 启动探测结果(LLM active/available + ASR local 探测)
    app.router.add_get("/api/creds/status", api_creds_status)
    # M3.36.C 重构:colibri 引导卡路由已转移到主对话窗口(prisIragent_web.py),
    # companion(语音对话扩展)不再挂载。函数定义保留供 review。
    # M3.27.4 — 主面板 k-platform-pick 切 ASR(主面板 18802 /api/asr/active 转发到此)
    app.router.add_post("/api/asr/active", api_asr_active)
    # M3.28 Phase 1 PoC — 落雪 LX Music source 协议 → mp3 直链
    app.router.add_post("/api/music/cmd", api_music_cmd)
    app.router.add_get("/api/music/state", api_music_state)
    app.router.add_get("/api/music/status", api_music_status)
    app.router.add_get("/api/music/dispatch", api_music_dispatch)
    app.router.add_post("/api/music/start", api_music_start)
    app.router.add_get("/api/music/ws", ws_music_handler)

    # M3.23:启动时,如果 companion_asr_settings.json 里知识库开关已开 + root 已配,
    # 后台异步触发 fcontent.enable()(不阻塞端口监听)。
    async def _boot_m323(_app):
        cfg = _load_fcontext_cfg()
        if cfg.get("asr_knowledge_lookup") and cfg.get("fcontent_root"):
            asyncio.create_task(_fcontent_enable_background(cfg["fcontent_root"]))

    app.on_startup.append(_boot_m323)

    async def _cleanup_lx_bridge(_app):
        global _lx_bridge, _lx_bridge_consumer_task
        if _lx_bridge_consumer_task is not None and not _lx_bridge_consumer_task.done():
            _lx_bridge_consumer_task.cancel()
            try:
                await _lx_bridge_consumer_task
            except (asyncio.CancelledError, Exception):
                pass
        _lx_bridge_consumer_task = None
        if _lx_bridge is not None:
            try:
                await _lx_bridge.stop()
            except Exception:
                pass
            _lx_bridge = None
    app.on_cleanup.append(_cleanup_lx_bridge)

    async def _static_handler(req):
        # 自定义 static handler 强制 utf-8 charset(2026-09-16 emoji 渲染修复)
        path = req.match_info["path"]
        full = (STATIC_DIR / path).resolve()
        # 防穿越
        if not str(full).startswith(str(STATIC_DIR.resolve())):
            return web.Response(status=403, text="forbidden")
        if not full.is_file():
            return web.Response(status=404, text="not found")
        ctype = "text/html; charset=utf-8" if path.endswith(".html") else \
                "text/javascript; charset=utf-8" if path.endswith(".js") else \
                "text/css; charset=utf-8" if path.endswith(".css") else \
                None
        return web.FileResponse(full, headers=None if ctype is None
                                else {"Content-Type": ctype})

    app.router.add_get("/static/{path:.*}", _static_handler)
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Prisir Companion (长激活语音陪聊 M3)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--no-auto-creds", action="store_true",
                    help="M3.27.2:跳过启动自动选 LLM key + 本地 ASR 探测")
    # M3.34(2026-09-19)端口统一:CLI > env(PRISIRAGENT_COMPANION_PORT) > 用户设置(HKCU/JSON) > 模块默认。
    try:
        from music.port_config import (  # noqa: PLC0415
            DEFAULT_COMPANION_PORT as _DEFAULT_COMPANION_PORT,
            resolve_start_port,
            notify_port_changed,
        )
        _env_companion = os.environ.get("PRISIRAGENT_COMPANION_PORT") or os.environ.get("OIAGENT_COMPANION_PORT")
        # 占位 None 让 CLI/env 优先;用户设置由 resolve_start_port 在 CLI/env 都没设时回退到 HKCU
        ap.add_argument("--port", type=int,
                        default=resolve_start_port("companion", _env_companion, None, _DEFAULT_COMPANION_PORT))
    except Exception as _pc_err:  # noqa: BLE001 — port_config 不可用时退化到 18850
        log.warning("[M3.34] port_config unavailable, fallback to 18850: %s", _pc_err)
        ap.add_argument("--port", type=int, default=18850)
    args = ap.parse_args()
    if args.data_dir:
        os.environ["PRISIR_DATA_DIR"] = args.data_dir
    log.info("data dir: %s", DATA_DIR)
    log.info("static:   %s", STATIC_DIR)
    log.info("open:     http://%s:%d/", args.host, args.port)
    log.info("prisirai: %s", PRISIRAI_INJECT_URL)
    # M3.27.2 — 启动时自动选 LLM key + 探测本地 ASR
    if not args.no_auto_creds:
        try:
            _apply_auto_creds_to_settings()
        except Exception as e:  # noqa: BLE001
            log.warning("[M3.27.2] _apply_auto_creds err: %s", e)
        try:
            _apply_auto_asr_if_local()
        except Exception as e:  # noqa: BLE001
            log.warning("[M3.27.2] _apply_auto_asr err: %s", e)
    # M3.34(2026-09-19)web.run_app 不便直接拿真端口(server 可能 fallback)。
    # 这里仅"启动时把配置值写回注册表"——下次 Tauri 壳启动读到的是上次配置值,
    # 符合"启动即同步配置"的语义。若后续启用 pick_free_port 走 fallback 路径,
    # 真端口与配置端口不一致由 notify_port_changed 接管,目前 web.run_app 不会
    # 自动改端口,这里用 write_port 同步一次(不带 changed 提示)。
    try:
        from music.port_config import write_port as _pc_write_companion  # noqa: PLC0415
        _pc_write_companion("companion", int(args.port))
    except Exception as _pc_w_err:  # noqa: BLE001
        log.warning("[M3.34] companion port write-back failed: %s", _pc_w_err)
    web.run_app(make_app(), host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()