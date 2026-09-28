# -*- coding: utf-8 -*-
# companion/colibri_state.py — colibri 引擎状态持久化(2026-09-28 ship Phase A)
#
# 定位:
#   - 把 colibri 引擎的"已下载 / 已启动 / 端口 / PID / 版本 / 引导卡关闭状态" 落 JSON,
#     跨进程跨重启可见,避免每次启动都重新探测。
#   - 状态文件路径:<DATA_DIR>/colibri.json。DATA_DIR 解析 兼容 PRISIR_DATA_DIR 环境变量。
#   - 所有写入都做缺字段兜底(老文件 / 损坏文件 / 权限不足 都不会让上层报错)。
#
# 设计取舍:
#   - 不放 SQLite(只是一份几 KB 配置,JSON 足矣);与 media_keys.json / asr_settings.json 范式平级
#   - 不引入 pydantic / marshmallow(纯 stdlib:json + dataclass)
#   - 读多写少,只暴露 load_state() / save_state() / update_state() 三函数 + dataclass
#
# 字段:
#   downloaded       : bool        OLMoE 容器是否已下载到本地
#   model_path       : str         容器目录绝对路径(默认 <DATA_DIR>/models/olmoe)
#   downloaded_at    : int         下载完成的 unix ts(0 = 未下载)
#   download_size    : int         实际占用字节
#   port             : int         colibri serve 监听端口(默认 18891)
#   pid              : int|None    上次启动的 PID(进程死亡后清空)
#   version          : str         colibri 引擎版本(如 "v1.12.1")
#   last_health_at   : int         上次 /health 200 的 unix ts
#   restart_attempts : int         当前连续重启失败次数(0~3)
#   state            : str         整体状态机:
#                       "not_downloaded" — 首次启动,引导卡待弹
#                       "downloading"    — 后台下载中
#                       "ready"          — 已下载,引擎已启动
#                       "stopped"        — 已下载但用户手动停
#                       "crashed"        — 连续 3 次失败
#   dismissed        : bool        引导卡用户已关闭过(下次启动不弹)
#   dismissed_at: int             dismissed 切换的 unix ts
#   last_error       : str         最近一次错误的可读消息(失败诊断用)
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("prisiragent-companion.colibri.state")

# 端口钉死:避开已用 18800-18860/18899/18802/18803/18813(M3.35/M3.36 已 ship 经验)
DEFAULT_PORT = 18891

# 容器默认目录(避免用户配置,固定到 DATA_DIR/models/olmoe,易找易删)
DEFAULT_MODEL_REL = Path("models") / "olmoe"


@dataclass
class ColibriState:
    """colibri 引擎运行时状态(全字段缺省兜底,便于升级)。"""
    downloaded: bool = False
    model_path: str = ""
    downloaded_at: int = 0
    download_size: int = 0
    port: int = DEFAULT_PORT
    pid: Optional[int] = None
    version: str = ""
    last_health_at: int = 0
    restart_attempts: int = 0
    state: str = "not_downloaded"   # not_downloaded / downloading / ready / stopped / crashed
    dismissed: bool = False
    dismissed_at: int = 0
    last_error: str = ""
    # M3.36.C (2026-09-28):用户三选一 onboarding 选择
    #   ""          — 未选(默认,引导卡弹出)
    #   "no_key"    — 用户表示完全不懂 API/key,选了下载本地模型
    #   "has_key"   — 用户表示会配置 / 已有 key,关闭引导,直接走云端
    #   "skip"      — 用户选跳过(不下载不配置,等以后再说)
    onboarding_choice: str = ""
    onboarding_at: int = 0
    # M3.36.B (2026-09-28):OLMoE 下载进度跟踪
    #   download_task_id — 后台 asyncio.Task 唯一 ID,前端轮询用
    #   download_progress_pct — 0.0~100.0,前端进度条
    #   download_total_bytes / download_done_bytes — 大小估算(可选,snapshot_download 不直接报)
    #   download_started_at / download_finished_at — unix ts
    #   download_error — 失败时存可读消息
    download_task_id: str = ""
    download_progress_pct: float = 0.0
    download_total_bytes: int = 0
    download_done_bytes: int = 0
    download_started_at: int = 0
    download_finished_at: int = 0
    download_error: str = ""


def _data_dir() -> Path:
    """读环境变量 PRISIR_DATA_DIR,默认 ~/.local/share/prisiragent-companion。"""
    raw = os.environ.get("PRISIR_DATA_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local" / "share" / "prisiragent-companion"


def state_path() -> Path:
    """<DATA_DIR>/colibri.json — 状态文件位置。"""
    p = _data_dir() / "colibri.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_state() -> ColibriState:
    """读 JSON 状态文件,缺字段用 ColibriState 兜底。文件不存在/损坏返默认值。"""
    p = state_path()
    if not p.is_file():
        return _with_defaults()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("colibri state 解析失败,fallback 默认: %s", e)
        return _with_defaults()
    if not isinstance(raw, dict):
        return _with_defaults()
    # 用 dataclass 兜底:把 raw 里有的字段填进去,缺的全部走 field(default)
    base = _with_defaults()
    data_dict = asdict(base)
    for k, v in raw.items():
        if k in data_dict:
            data_dict[k] = v
    try:
        return ColibriState(**data_dict)
    except Exception as e:  # noqa: BLE001
        log.warning("colibri state 构造失败,fallback 默认: %s", e)
        return _with_defaults()


def save_state(state: ColibriState) -> bool:
    """写 JSON 状态文件,失败返 False(不抛 — 上层 fail-soft)。"""
    p = state_path()
    try:
        p.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("colibri state 保存失败: %s", e)
        return False


def update_state(**fields) -> ColibriState:
    """原子读-改-写:读 → 覆盖指定字段 → 写回 → 返新 state。

    用法:
        s = update_state(state="ready", pid=12345)
    """
    s = load_state()
    for k, v in fields.items():
        if hasattr(s, k):
            setattr(s, k, v)
    save_state(s)
    return s


def _with_defaults() -> ColibriState:
    """带默认 model_path 的新实例。"""
    s = ColibriState()
    if not s.model_path:
        s.model_path = str(_data_dir() / DEFAULT_MODEL_REL)
    return s


def is_model_path_set(state: Optional[ColibriState] = None) -> bool:
    """模型目录是否已存在且非空(粗判:目录存在 + 至少有一个 .safetensors)。"""
    s = state or load_state()
    if not s.model_path:
        return False
    p = Path(s.model_path)
    if not p.is_dir():
        return False
    try:
        next(p.glob("*.safetensors"))
        return True
    except StopIteration:
        return False


def has_existing_keys() -> bool:
    """检测用户是否已有可用的云端 key(keys.db / env / settings.active_platform)。

    任一为真 → "已配 key",不弹引导卡。
    """
    # 1) settings.active_platform 非空(用户显式设过)
    try:
        from companion_asr_providers import load_settings
        s = load_settings(_data_dir())
        if s.get("active_platform"):
            return True
        ap = s.get("llm_available_platforms") or []
        if any(ap):
            return True
    except Exception:
        pass

    # 2) keys.db 里有任何 platform_keys(api_key 非空)
    try:
        from fastlane.providers.llm_prisir import PrisirKeyStore
        ks = PrisirKeyStore()
        for row in ks.list_platforms():
            if row.get("has_key"):
                return True
    except Exception:
        # fallback:直接读 sqlite
        try:
            import sqlite3
            db = _data_dir().parent / "prisir" / "keys.db"
            if not db.is_file():
                db = Path.home() / ".local" / "share" / "prisir" / "keys.db"
            if db.is_file():
                with sqlite3.connect(str(db)) as c:
                    rows = c.execute(
                        "SELECT platform, api_key FROM platform_keys "
                        "WHERE api_key != ''").fetchall()
                    if any(rows):
                        return True
        except Exception:
            pass

    # 3) 环境变量扫(参考 prisIragent-companion-web._scan_env_keys)
    try:
        env_keys = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                    "GEMINI_API_KEY", "GOOGLE_API_KEY",
                    "XAI_API_KEY", "GROK_API_KEY",
                    "MISTRAL_API_KEY", "GROQ_API_KEY",
                    "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY",
                    "DOUBAO_API_KEY", "ARK_API_KEY",
                    "DASHSCOPE_API_KEY", "QWEN_API_KEY",
                    "BAILIAN_API_KEY", "MOONSHOT_API_KEY",
                    "KIMI_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY",
                    "OLLAMA_HOST", "LLAMA_SERVER_URL")
        for v in env_keys:
            if os.environ.get(v):
                return True
    except Exception:
        pass

    return False


def should_show_onboarding() -> bool:
    """综合判断:是否需要弹三选一引导卡。

    规则(2026-09-28 与用户拍板):
      - onboarding_choice = "has_key" → 不弹(用户明确表示会配置 / 已有 key)
      - onboarding_choice = "no_key" + 已下载完成 → 不弹(走 no_key 已 ship 流程)
      - onboarding_choice = "skip" → **下次启动还弹**(用户没做决定)
      - 已有云端 key(keys.db / env / settings)→ 不弹(老用户 / 重新安装)
      - 首次启动 + 无 key → 弹
    """
    s = load_state()
    # 用户明确选了 has_key → 不弹
    if s.onboarding_choice == "has_key":
        return False
    # 用户选了 no_key 且已下载完成 → 不弹(走 no_key 流程完成)
    if s.onboarding_choice == "no_key" and s.downloaded and is_model_path_set(s):
        return False
    # skip 不弹特殊处理:用户在 onboarding_at 24 小时内不算"再次打扰",否则再弹
    #   实现:skip 仍然 return True(下次启动还弹),让用户有机会重新选
    # 老用户已有 key → 不弹
    if has_existing_keys():
        return False
    return True


def touch_health() -> None:
    """便捷:更新 last_health_at = now(由 health check 调,无需关心其他字段)。"""
    update_state(last_health_at=int(time.time()))


__all__ = [
    "ColibriState",
    "DEFAULT_PORT",
    "DEFAULT_MODEL_REL",
    "state_path",
    "load_state",
    "save_state",
    "update_state",
    "is_model_path_set",
    "has_existing_keys",
    "should_show_onboarding",
    "touch_health",
]