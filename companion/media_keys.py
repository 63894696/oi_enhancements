# -*- coding: utf-8 -*-
"""
media_keys.py — P3j T17 多媒体创作模块 Key 配置

参考 companion_asr_providers.load_settings/save_settings/public_settings 范式,
仿 llm/asr 分模块页填模式,把 tts / 图片生成 / 视频生成 / 音频生成 模型 key
单独放「🎨 多媒体创作」模块。

持久化位置:`%PRISIR_DATA_DIR%/media_keys.json`(默认 ~/.prisirai/)
回填链路:Easel ~/work/zju_easel/.env > media_keys.json > process env(优先级递减)

为什么独立于 settings.json:
- 不污染 LLM/ASR 设置,前端可独立切换 Tab
- 主对话 EXEC 失败时可定向 hint("去 🎬 视频 Tab 配置 SILICONFLOW key")
- 不替代 Easel .env(它仍由 Easel 自己管)— 我们只是镜像一份便于 UI 配
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

LOG = logging.getLogger("media_keys")

# P3j T18: provider → process env 默认
_PROVIDER_ENV: dict[str, str] = {
    "siliconflow": "SILICONFLOW_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "openai": "OPENAI_API_KEY",
}
# P3j T18: dashscope 兼容端点(/models 必须挂在 compatible-mode/v1 下)
_DASHSCOPE_MODELS_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/models"
)
# P3j T18: faster-whisper 本地模型缓存可能位置(选最先存在的)
_WHISPER_CACHE_DIRS: list[Path] = [
    Path.home() / ".cache" / "huggingface" / "hub",
    Path.home() / ".cache" / "whisper",
]

# ---------------------------------------------------------------------------
# Provider 注册表(对应 LLM/ASR 范式:每个 provider 一组 field 描述)
# ---------------------------------------------------------------------------

PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "siliconflow",
        "title": "SiliconFlow(AI 配图/视频)",
        "env_var": "SILICONFLOW_API_KEY",
        "purpose": "image-gen / video-gen 依赖",
        "hint": "在 ~/work/zju_easel/.env 配 SILICONFLOW_API_KEY=sk-...,此处填写会镜像一份",
        "fields": [
            {"key": "api_key", "secret": True, "required": True,
             "label": "API Key", "placeholder": "sk-..."},
            {"key": "base_url", "secret": False, "required": False,
             "default": "https://api.siliconflow.cn/v1",
             "label": "Base URL", "placeholder": "默认即可"},
        ],
    },
    {
        "id": "dashscope",
        "title": "通义千问 CosyVoice TTS",
        "env_var": "DASHSCOPE_API_KEY",
        "purpose": "高级 TTS(可选,不配走 edge-tts 免费替代)",
        "hint": "阿里云百炼 key,DASHSCOPE_API_KEY=sk-...",
        "fields": [
            {"key": "api_key", "secret": True, "required": True,
             "label": "API Key", "placeholder": "sk-..."},
        ],
    },
    {
        "id": "openai",
        "title": "OpenAI(高级 TTS / 备选配图)",
        "env_var": "OPENAI_API_KEY",
        "purpose": "高级 TTS 备选,可不配",
        "hint": "OPENAI_API_KEY=sk-...;若用中转可改 base_url",
        "fields": [
            {"key": "api_key", "secret": True, "required": True,
             "label": "API Key", "placeholder": "sk-..."},
            {"key": "base_url", "secret": False, "required": False,
             "default": "https://api.openai.com/v1",
             "label": "Base URL", "placeholder": "默认即可,中转可改"},
        ],
    },
    {
        "id": "whisper",
        "title": "本地 Whisper 模型大小",
        "env_var": "",  # 不存 key,只存模型大小
        "purpose": "faster-whisper 本地 ASR(无需 key,首次自动下载)",
        "hint": "base=150MB / small=500MB / medium=1.5GB / large-v3=3GB",
        "fields": [
            {"key": "model", "secret": False, "required": True,
             "default": "base",
             "label": "模型大小",
             "options": ["tiny", "base", "small", "medium", "large-v3"]},
        ],
    },
]


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------


def _media_keys_path(data_dir: Path) -> Path:
    return data_dir / "media_keys.json"


def _default_settings() -> dict[str, Any]:
    """deep copy of PROVIDERS defaults — 不暴露 secret 字串。"""
    out: dict[str, Any] = {}
    for p in PROVIDERS:
        cfg: dict[str, Any] = {}
        for f in p["fields"]:
            cfg[f["key"]] = f.get("default", "")
        out[p["id"]] = cfg
    return out


def load_media_keys(data_dir: Path) -> dict[str, Any]:
    """读 media_keys.json,缺则返默认;旧文件缺字段则补齐。

    返回结构:
        {"providers": {<id>: {<field>: <value>}}}
    """
    p = _media_keys_path(data_dir)
    if not p.is_file():
        return {"providers": _default_settings()}
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        LOG.warning("media_keys.json 解析失败,回退默认")
        return {"providers": _default_settings()}
    if "providers" not in s:
        s["providers"] = {}
    # 补齐缺失的 provider / field
    for prov in PROVIDERS:
        pid = prov["id"]
        if pid not in s["providers"]:
            s["providers"][pid] = {}
        cfg = s["providers"][pid]
        for f in prov["fields"]:
            if f["key"] not in cfg:
                cfg[f["key"]] = f.get("default", "")
    return s


def save_media_keys(data_dir: Path, settings: dict[str, Any]) -> None:
    """写 media_keys.json(原子:写 tmp → rename)。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    p = _media_keys_path(data_dir)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(p)


def mask_secret(v: str) -> str:
    """mask 中间,留首尾 4 字;短则全 mask。"""
    if not v:
        return ""
    if len(v) <= 12:
        return "***"
    return v[:4] + "…" + v[-4:]


def public_media_keys(cfg: dict[str, Any]) -> dict[str, Any]:
    """给前端 GET:展示当前设置,但 secret 字段 mask。"""
    out_providers: dict[str, Any] = {}
    for p in PROVIDERS:
        pid = p["id"]
        c = cfg.get("providers", {}).get(pid, {})
        masked: dict[str, Any] = {}
        for f in p["fields"]:
            v = c.get(f["key"], f.get("default", ""))
            if f.get("secret") and v:
                masked[f["key"]] = mask_secret(str(v))
                masked[f["key"] + "_present"] = True
            else:
                masked[f["key"]] = v
        out_providers[pid] = masked
    return {
        "providers": out_providers,
        "registry": [
            {"id": p["id"], "title": p["title"], "purpose": p["purpose"],
             "hint": p["hint"]}
            for p in PROVIDERS
        ],
    }


# ---------------------------------------------------------------------------
# Post handler 用的"读不到就不覆盖"过滤
# ---------------------------------------------------------------------------


def _looks_like_mask(v: str) -> bool:
    """前端 POST 时 secret 字段可能是 '***' / 'sk-…-xxx' / 空串。
    这类视为占位,不覆盖原值。"""
    if not v:
        return True
    s = v.strip()
    if s == "***":
        return True
    if "…" in s:  # 已 mask
        return True
    return False


def apply_post(data_dir: Path, posted: dict[str, Any]) -> dict[str, Any]:
    """处理前端 POST body:
    - 不存在的 provider 跳过
    - secret 字段若为 mask/空 → 不覆盖原值(从前端 UI 角度:用户没填)
    - 非 secret 字段照写

    返回合并后的 settings(dict 可直接 save_media_keys 落盘)。
    """
    current = load_media_keys(data_dir)
    cur_providers = current.setdefault("providers", {})
    posted_providers = posted.get("providers", {}) or {}
    for prov in PROVIDERS:
        pid = prov["id"]
        if pid not in posted_providers:
            continue
        cur = cur_providers.setdefault(pid, {})
        post = posted_providers[pid]
        for f in prov["fields"]:
            if f["key"] not in post:
                continue
            v = post[f["key"]]
            if f.get("secret") and _looks_like_mask(str(v)):
                # 不覆盖
                continue
            cur[f["key"]] = v
    return current


# ---------------------------------------------------------------------------
# 统一读取(优先级:Easel .env > media_keys.json > process env)
# ---------------------------------------------------------------------------


def resolve_media_key(name: str, env_var: str = "") -> str:
    """读 Easel .env → media_keys.json → process env,返第一个非空值。

    Args:
        name: provider id, ∈ {"siliconflow","dashscope","openai"}
        env_var: 真实的 process env 变量名,缺省从 PROVIDERS 查

    Returns:
        key 字串;空串表示未配。
    """
    # 1) Easel .env(最高优)
    try:
        from prisir_work.video_creator import _load_easel_env
        easel_env = _load_easel_env()
        if env_var:
            v = easel_env.get(env_var, "").strip()
            if v:
                return v
    except Exception:  # noqa: BLE001
        pass

    # 2) media_keys.json
    try:
        from pathlib import Path as _P
        data_dir = _P(os.environ.get("PRISIR_DATA_DIR",
                                     str(Path.home() / ".prisirai")))
        s = load_media_keys(data_dir)
        v = s.get("providers", {}).get(name, {}).get("api_key", "")
        if v:
            return v
    except Exception:  # noqa: BLE001
        pass

    # 3) process env(兜底)
    if env_var:
        v = os.environ.get(env_var, "").strip()
        if v:
            return v
    return ""


def get_whisper_model(data_dir: Path) -> str:
    """读 whisper 模型大小(默认 base)。"""
    s = load_media_keys(data_dir)
    return s.get("providers", {}).get("whisper", {}).get("model", "base") or "base"


# ---------------------------------------------------------------------------
# 依赖状态(供 /api/media/status 用)
# ---------------------------------------------------------------------------


def media_status(data_dir: Optional[Path] = None) -> dict[str, Any]:
    """汇总 4 类依赖的当前状态(粗粒度 3 类:key 已配 / 未配走免费替代 / 没装)。

    返回结构:
        {
          "image_gen": {"ready": bool, "mode": "siliconflow"|"placeholder",
                        "hint": "..."},
          "video_gen": {"ready": bool, "mode": "siliconflow"|"placeholder",
                        "hint": "..."},
          "tts":       {"ready": bool, "mode": "edge"|"closed",
                        "hint": "..."},
          "asr":       {"ready": bool, "model": "base", "hint": "..."},
          "ffmpeg":    {"ready": bool, "path": "...", "hint": "..."},
          "easel":     {"ready": bool, "path": "..."},
        }
    """
    # Easel root
    easel_path = ""
    easel_ready = False
    try:
        from prisir_work.easel_bridge import find_easel_root
        root = find_easel_root()
        if root:
            easel_path = str(root)
            easel_ready = True
    except Exception:  # noqa: BLE001
        pass

    # SILICONFLOW key
    sf_key = resolve_media_key("siliconflow", "SILICONFLOW_API_KEY")
    sf_ready = bool(sf_key)

    # DASHSCOPE key(高级 TTS)
    ds_key = resolve_media_key("dashscope", "DASHSCOPE_API_KEY")
    openai_key = resolve_media_key("openai", "OPENAI_API_KEY")
    tts_closed = bool(ds_key or openai_key)

    # Whisper model
    if data_dir is None:
        data_dir = Path(os.environ.get("PRISIR_DATA_DIR",
                                       str(Path.home() / ".prisirai")))
    whisper_model = get_whisper_model(data_dir)

    # ffmpeg
    import shutil
    ffmpeg_path = shutil.which("ffmpeg") or ""
    ffmpeg_ready = bool(ffmpeg_path)

    # image_gen / video_gen:依赖 SILICONFLOW_API_KEY + Easel + skill script
    from pathlib import Path as _P
    img_ready = easel_ready and sf_ready and _P(
        easel_path) / "skills/shared/scripts/ai_image.py"
    vid_ready = easel_ready and sf_ready and _P(
        easel_path) / "skills/shared/scripts/ai_video.py"

    def _has(p: _P) -> bool:
        try:
            return bool(p and p.is_file())
        except Exception:  # noqa: BLE001
            return False

    return {
        "easel": {"ready": easel_ready, "path": easel_path,
                  "hint": "Easel 项目根目录未找到 — 视频/AI 配图功能不可用"},
        "image_gen": {
            "ready": bool(img_ready and _has(img_ready)),
            "mode": "siliconflow" if sf_ready else "placeholder",
            "key_present": sf_ready,
            "hint": ("AI 配图走 SiliconFlow" if sf_ready
                     else "未配 SILICONFLOW_API_KEY → 走占位图"),
        },
        "video_gen": {
            "ready": bool(vid_ready and _has(vid_ready)),
            "mode": "siliconflow" if sf_ready else "placeholder",
            "key_present": sf_ready,
            "hint": ("AI 视频走 SiliconFlow" if sf_ready
                     else "未配 SILICONFLOW_API_KEY → 走占位视频"),
        },
        "tts": {
            "ready": easel_ready,
            "mode": "closed" if tts_closed else "edge",
            "key_present": tts_closed,
            "hint": ("闭源 TTS(DashScope/OpenAI)" if tts_closed
                     else "默认 edge-tts(免费,需外网)"),
        },
        "asr": {
            "ready": easel_ready,
            "model": whisper_model,
            "hint": f"faster-whisper/{whisper_model}(本地,首次自动下载)",
        },
        "ffmpeg": {
            "ready": ffmpeg_ready,
            "path": ffmpeg_path,
            "hint": "ffmpeg 系统安装" if ffmpeg_ready else "❌ 缺 ffmpeg,视频合成/字幕烧录全挂",
        },
        "whisper_model": whisper_model,
    }


# ---------------------------------------------------------------------------
# P3j T18: provider 真探活(走 GET /models 走 Bearer;whisper 查本地)
# ---------------------------------------------------------------------------


def _http_get(url: str, api_key: str, timeout: float) -> tuple[int, str]:
    """发 GET 请求带 Bearer。返 (status_code, body_text)。
    status_code=-1 表示网络/超时异常(由 hint 区分)。
    """
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {api_key}",
                 "User-Agent": "PrisirAI-media-test/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (resp.status, resp.read(2048).decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        # 401 / 403 / 5xx 走这里,e.code 真实
        try:
            body = e.read(512).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            body = ""
        return (e.code, body)
    except urllib.error.URLError as e:
        return (-1, str(e.reason))
    except (socket.timeout, TimeoutError):
        return (-2, "timeout")
    except Exception as e:  # noqa: BLE001
        return (-3, f"{type(e).__name__}: {e}")


def _probe_siliconflow(api_key: str, base_url: str,
                       timeout: float) -> dict[str, Any]:
    url = (base_url or "https://api.siliconflow.cn/v1").rstrip("/") + "/models"
    t0 = time.time()
    status, body = _http_get(url, api_key, timeout)
    latency_ms = int((time.time() - t0) * 1000)
    if status == 200:
        return {"ok": True, "status": status, "latency_ms": latency_ms,
                "mode": "siliconflow",
                "hint": "✅ key 有效,可达 SiliconFlow /models"}
    if status in (401, 403):
        return {"ok": False, "status": status, "latency_ms": latency_ms,
                "mode": "siliconflow",
                "hint": "❌ key 格式正确但平台鉴权失败(401/403)→ 检查 key 是否被撤销"}
    if status == -2:
        return {"ok": False, "status": -2, "latency_ms": latency_ms,
                "mode": "siliconflow",
                "hint": "❌ 网络超时 → 检查代理/防火墙"}
    if status < 0:
        return {"ok": False, "status": status, "latency_ms": latency_ms,
                "mode": "siliconflow",
                "hint": f"❌ 网络异常 → {body[:80]}"}
    return {"ok": False, "status": status, "latency_ms": latency_ms,
            "mode": "siliconflow",
            "hint": f"❌ status={status} → 平台故障/不是 key 问题"}


def _probe_openai(api_key: str, base_url: str,
                  timeout: float) -> dict[str, Any]:
    url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/models"
    t0 = time.time()
    status, body = _http_get(url, api_key, timeout)
    latency_ms = int((time.time() - t0) * 1000)
    if status == 200:
        return {"ok": True, "status": status, "latency_ms": latency_ms,
                "mode": "openai",
                "hint": "✅ key 有效,可达 OpenAI /models"}
    if status in (401, 403):
        return {"ok": False, "status": status, "latency_ms": latency_ms,
                "mode": "openai",
                "hint": "❌ key 鉴权失败(401/403)→ 中转需改 base_url"}
    if status == -2:
        return {"ok": False, "status": -2, "latency_ms": latency_ms,
                "mode": "openai",
                "hint": "❌ 网络超时 → 检查代理/中转连通性"}
    if status < 0:
        return {"ok": False, "status": status, "latency_ms": latency_ms,
                "mode": "openai",
                "hint": f"❌ 网络异常 → {body[:80]}"}
    return {"ok": False, "status": status, "latency_ms": latency_ms,
            "mode": "openai",
            "hint": f"❌ status={status} → 平台故障/不是 key 问题"}


def _probe_dashscope(api_key: str, _base_url_unused: str,
                     timeout: float) -> dict[str, Any]:
    # DashScope OpenAI-compatible 端点是固定的,不接受 base_url 覆盖
    t0 = time.time()
    status, body = _http_get(_DASHSCOPE_MODELS_URL, api_key, timeout)
    latency_ms = int((time.time() - t0) * 1000)
    if status == 200:
        return {"ok": True, "status": status, "latency_ms": latency_ms,
                "mode": "dashscope",
                "hint": "✅ key 有效,可达 DashScope /compatible-mode/v1/models"}
    if status in (401, 403):
        return {"ok": False, "status": status, "latency_ms": latency_ms,
                "mode": "dashscope",
                "hint": "❌ DashScope key 鉴权失败(401/403)→ 检查是否开通百炼服务"}
    if status == -2:
        return {"ok": False, "status": -2, "latency_ms": latency_ms,
                "mode": "dashscope",
                "hint": "❌ 网络超时 → 检查能否访问 dashscope.aliyuncs.com"}
    if status < 0:
        return {"ok": False, "status": status, "latency_ms": latency_ms,
                "mode": "dashscope",
                "hint": f"❌ 网络异常 → {body[:80]}"}
    return {"ok": False, "status": status, "latency_ms": latency_ms,
            "mode": "dashscope",
            "hint": f"❌ status={status} → 平台故障/不是 key 问题"}


def _probe_whisper(_api_key_unused: str, _base_url_unused: str,
                   _timeout_unused: float) -> dict[str, Any]:
    """whisper 走本地:有 faster-whisper + 模型 cache → ok。"""
    # 1) faster-whisper 是否可导入
    try:
        import faster_whisper  # noqa: F401
        pkg_ok = True
    except Exception as e:  # noqa: BLE001
        pkg_ok = False
        err = f"{type(e).__name__}: {e}"
    # 2) 模型 cache 目录是否存在
    cache_ok = False
    cache_path = ""
    for d in _WHISPER_CACHE_DIRS:
        try:
            if d.is_dir():
                cache_ok = True
                cache_path = str(d)
                break
        except Exception:  # noqa: BLE001
            pass
    # 3) 模型大小
    try:
        data_dir = Path(os.environ.get("PRISIR_DATA_DIR",
                                       str(Path.home() / ".prisirai")))
        model = get_whisper_model(data_dir)
    except Exception:  # noqa: BLE001
        model = "base"
    if pkg_ok and cache_ok:
        return {"ok": True, "status": 200, "latency_ms": 0,
                "mode": "whisper",
                "hint": f"✅ faster-whisper 已装 + 模型 cache @ {cache_path}"}
    if not pkg_ok:
        return {"ok": False, "status": 0, "latency_ms": 0,
                "mode": "whisper",
                "hint": f"❌ faster-whisper 未安装 → {err}"}
    return {"ok": False, "status": 0, "latency_ms": 0,
            "mode": "whisper",
            "hint": ("❌ faster-whisper 已装但模型 cache 未见 → "
                     f"首次会自动下载{model}模型")}


def probe_provider(name: str, api_key: str = "",
                   base_url: str = "",
                   timeout: float = 8.0) -> dict[str, Any]:
    """真探活 provider,返 {ok, status, latency_ms, hint, mode}。

    Args:
        name: provider id,∈ PROVIDERS
        api_key: 真 key;空时 fallback 到 resolve_media_key
        base_url: 仅 openai/siliconflow 生效
        timeout: HTTP 超时秒数
    """
    if name not in {p["id"] for p in PROVIDERS}:
        return {"ok": False, "status": 0, "latency_ms": 0,
                "mode": name,
                "hint": f"未知 provider: {name}"}
    # key fallback
    if not api_key and name != "whisper":
        api_key = resolve_media_key(name, _PROVIDER_ENV.get(name, ""))
    if name == "siliconflow":
        return _probe_siliconflow(api_key, base_url, timeout)
    if name == "openai":
        return _probe_openai(api_key, base_url, timeout)
    if name == "dashscope":
        return _probe_dashscope(api_key, base_url, timeout)
    if name == "whisper":
        return _probe_whisper(api_key, base_url, timeout)
    return {"ok": False, "status": 0, "latency_ms": 0,
            "mode": name,
            "hint": f"unknown probe path for {name}"}