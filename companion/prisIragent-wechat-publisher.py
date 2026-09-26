# -*- coding: utf-8 -*-
"""
prisIragent-wechat-publisher.py — P3j T10 公众号 + 小红书 + B站 发布后端主入口

启动:
  python prisIragent-wechat-publisher.py [--port 0] [--host 127.0.0.1]

绑定 0 → 自动分配端口 → 写 HKCU 注册表 + _prisir_registry/wechat_publisher_port.json
Tauri 壳 + companion 都通过 read_wechat_publisher_port() 探测本服务。

范式对齐(参考 prisiragent-music-web.py):
  · aiohttp + argparse + 端口动态分配 + 注册表双通道
  · WS 状态广播
  · /api/health + /api/state + /api/cmd 范式

路由(2026-09-24):
  GET  /api/health                        - 健康检查
  GET  /api/state                         - 服务状态 + 各 publisher ready
  GET  /api/platforms                     - 列出所有平台 + 登录态
  GET  /api/recall?q=...                  - prisirmp 公众号历史 recall 搜索
  POST /api/login                         - 触发某平台扫码登录(异步)
  POST /api/publish                       - 发草稿(body: platform,html_path,title,cover)
  GET  /api/stats?platform=wechat-oa      - 公众号数据回收
  GET  /ws/state                          - ws 状态广播
  GET  /                                  - 主页(发布 / 数据管理面板)

端口分配 + 跨进程: companion/port_registry.wechat_publisher (待加)

后续(待接):
  - 小红书: 通过 Easel skill-xhs-publisher (Android adb / Playwright 扫码)
  - B站:    通过 Easel skill-bilibili-publisher (biliup + QR 码)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 让 companion/* / oi_enhancements/* 都可被 import
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
for p in (str(_HERE), str(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from aiohttp import web
except ImportError:
    print("[error] 需要 aiohttp。运行: pip install aiohttp", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

LOG = logging.getLogger("wechat_publisher")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


# ---------------------------------------------------------------------------
# 跨进程端口注册表(对齐 music.port_registry)
# ---------------------------------------------------------------------------

REG_VAL = "wechat_publisher_port"
_REG_DIR = _HERE / "_prisir_registry"
_LOCK_PATH = _REG_DIR / "wechat_publisher_port.lock"


def _write_port(port: int, pid: int) -> None:
    """写 HKCU 注册表(Windows) + _prisir_registry/wechat_publisher_port.json(跨平台)。"""
    try:
        _REG_DIR.mkdir(parents=True, exist_ok=True)
        (_REG_DIR / "wechat_publisher_port.json").write_text(
            json.dumps({"port": port, "pid": pid, "ts": time.time()}),
            encoding="utf-8")
        _LOCK_PATH.write_text(str(pid), encoding="utf-8")
    except OSError as e:
        LOG.warning("write port file failed: %s", e)
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  r"Software\PrisirAI") as k:
                winreg.SetValueEx(k, REG_VAL, 0, winreg.REG_DWORD, int(port))
        except OSError as e:
            LOG.warning("write HKCU failed: %s", e)


def _is_alive(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_QUERY_LIMITED = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _find_free_port() -> int:
    """bind 0 拿一个空闲端口。"""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------------------------------------------------------------------------
# 服务状态(单例)
# ---------------------------------------------------------------------------


class ServiceState:
    """服务运行时状态。各 publisher 实时状态缓存在此。"""

    def __init__(self) -> None:
        self.port: int = 0
        self.pid: int = os.getpid()
        self.started_at: float = time.time()
        self.last_publish: Optional[dict] = None

    def snapshot(self) -> dict:
        from prisir_work.publisher import list_publishers
        return {
            "ok": True,
            "port": self.port,
            "pid": self.pid,
            "uptime_sec": round(time.time() - self.started_at, 1),
            "publishers": list_publishers(),
            "last_publish": self.last_publish,
        }


STATE = ServiceState()


# ---------------------------------------------------------------------------
# HTTP handlers — 视频创作(P3j T11)
# ---------------------------------------------------------------------------

async def _h_video_creators(_req: web.Request) -> web.Response:
    from prisir_work.video_creator import list_creators
    return web.json_response({"ok": True, "creators": list_creators()})


async def _h_video_env(_req: web.Request) -> web.Response:
    from prisir_work.video_creator import _load_easel_env
    env = _load_easel_env()
    # 安全:只暴露 key 名 + 是否存在,不打 value
    keys_meta = [{"name": k, "present": True} for k in env.keys()]
    return web.json_response({"ok": True, "env_keys": keys_meta,
                              "total": len(env)})


# ---------------------------------------------------------------------------
# HTTP handlers — 多媒体创作 Key 配置(P3j T17-B)
# ---------------------------------------------------------------------------

# wechat-publisher 自己的 DATA_DIR(独立于 companion,放 media_keys.json)
_DATA_DIR = Path(os.environ.get("PRISIR_DATA_DIR")
                 or Path.home() / ".prisirai")
_DATA_DIR.mkdir(parents=True, exist_ok=True)


async def _h_media_keys_get(_req: web.Request) -> web.Response:
    """返 4 个 provider 的"是否已配 + mask 后值"(key 不外露)。"""
    from companion.media_keys import load_media_keys, public_media_keys
    cfg = load_media_keys(_DATA_DIR)
    pub = public_media_keys(cfg)
    return web.json_response({"ok": True, **pub})


async def _h_media_keys_post(req: web.Request) -> web.Response:
    """保存前端 POST body,空 / mask 不覆盖(对齐 LLM/ASR 范式)。

    Body: {"providers": {"siliconflow": {"api_key": "sk-..."}}}
    """
    from companion.media_keys import apply_post, save_media_keys, public_media_keys
    body = await req.json() if req.body_exists else {}
    if not isinstance(body, dict) or "providers" not in body:
        return web.json_response(
            {"ok": False, "error": "missing_field: providers"},
            status=400)
    merged = apply_post(_DATA_DIR, body)
    save_media_keys(_DATA_DIR, merged)
    pub = public_media_keys(merged)
    return web.json_response({"ok": True, **pub})


async def _h_media_status(_req: web.Request) -> web.Response:
    """返当前依赖状态(粗粒度 3 类:已配 / 免费替代 / 没装)。"""
    from companion.media_keys import media_status
    st = media_status(_DATA_DIR)
    return web.json_response({"ok": True, **st})


async def _h_media_test(req: web.Request) -> web.Response:
    """P3j T18: 真探活某 provider。

    Body: {"provider": "siliconflow",
           "api_key": "sk-...",          # 可省,fallback resolve_media_key
           "base_url": "https://...",     # 可省
           "timeout": 8.0}                # 可省
    返: {ok, status, latency_ms, hint, mode}
    """
    from companion.media_keys import (probe_provider, PROVIDERS,
                                      resolve_media_key)
    body = await req.json() if req.body_exists else {}
    if not isinstance(body, dict):
        body = {}
    provider = (body.get("provider") or "").strip()
    valid = {p["id"] for p in PROVIDERS}
    if provider not in valid:
        return web.json_response(
            {"ok": False, "hint": f"未知 provider: {provider}",
             "status": 0, "latency_ms": 0, "mode": provider},
            status=400)
    api_key = (body.get("api_key") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    timeout = float(body.get("timeout") or 8.0)
    if not api_key and provider != "whisper":
        env_var = {"siliconflow": "SILICONFLOW_API_KEY",
                   "dashscope": "DASHSCOPE_API_KEY",
                   "openai": "OPENAI_API_KEY"}.get(provider, "")
        api_key = resolve_media_key(provider, env_var)
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: probe_provider(provider, api_key, base_url, timeout))
    except Exception as e:  # noqa: BLE001
        return web.json_response(
            {"ok": False, "status": 0, "latency_ms": 0,
             "mode": provider,
             "hint": f"{type(e).__name__}: {e}"})
    return web.json_response({"ok": True, **result})


async def _h_video_create(req: web.Request) -> web.Response:
    body = await req.json() if req.body_exists else {}
    creator = (body.get("creator") or "").strip()
    if not creator:
        return web.json_response({"ok": False, "error": "missing_creator"},
                                 status=400)
    loop = asyncio.get_event_loop()
    try:
        from prisir_work.video_creator import create as _create
        # 把 body 去掉 creator 字段 → 透传给 creator.create()
        kwargs = {k: v for k, v in body.items() if k != "creator"}
        result = await loop.run_in_executor(
            None, lambda: _create(creator, **kwargs))
        d = result.to_dict()
        return web.json_response({"ok": True, **d})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "creator": creator,
                                  "error": f"{type(e).__name__}: {e}"})


async def _h_video_orchestrate(req: web.Request) -> web.Response:
    """编排入口(对齐 auto-short-video SKILL)。

    Body:
      topic        必填
      script       必填(LLM 生成的文案分镜)
      output_dir   可选,默认 ~/work/zju_easel/outputs/<topic>/
      aspect_ratio 9:16 / 16:9 / 1:1 (默认 9:16)
      duration     目标秒数(默认 60)
      voice        TTS 音色(默认 YunxiNeural)
      with_subtitle / with_images  布尔(默认 true / false)
    """
    body = await req.json() if req.body_exists else {}
    topic = body.get("topic", "").strip()
    script = body.get("script", "").strip()
    if not topic or not script:
        return web.json_response(
            {"ok": False, "error": "missing_fields",
             "required": ["topic", "script"]}, status=400)
    loop = asyncio.get_event_loop()
    try:
        from prisir_work.video_creator import Orchestrator
        result = await loop.run_in_executor(
            None, lambda: Orchestrator().create(
                topic=topic, script=script,
                output_dir=body.get("output_dir", ""),
                aspect_ratio=body.get("aspect_ratio", "9:16"),
                duration=int(body.get("duration", 60)),
                voice=body.get("voice", ""),
                with_subtitle=bool(body.get("with_subtitle", True)),
                with_images=bool(body.get("with_images", False))))
        d = result.to_dict()
        return web.json_response({"ok": True, **d})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "error": f"{type(e).__name__}: {e}"})


async def _h_video_publish(req: web.Request) -> web.Response:
    """视频创作闭环 — 出片后一键投稿到 B站。

    Body:
      video_path   必填(assemble 出来的 final.mp4)
      title / partition / tid / desc / tag / cover
      exec_real    默认 False(dry-run)
    """
    body = await req.json() if req.body_exists else {}
    video = body.get("video_path", "").strip()
    if not video or not Path(video).is_file():
        return web.json_response(
            {"ok": False, "error": "missing_or_missing_file",
             "hint": "video_path 必须存在(可走 /api/video/create 出来的 final.mp4)"},
            status=400)
    loop = asyncio.get_event_loop()
    try:
        from prisir_work.publisher import BilibiliPublisher
        tid_val = body.get("tid")
        try:
            tid_val = int(tid_val) if tid_val not in (None, "") else None
        except (TypeError, ValueError):
            tid_val = None
        result = await loop.run_in_executor(
            None, lambda: BilibiliPublisher().publish_video(
                video=video, title=body.get("title", ""),
                partition=body.get("partition", ""), tid=tid_val,
                desc=body.get("desc", ""), tag=body.get("tag", ""),
                cover=body.get("cover", ""),
                exec_real=bool(body.get("exec_real", False))))
        d = result.to_dict()
        STATE.last_publish = d
        return web.json_response({"ok": True, **d})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# HTTP handlers — 发布(原 P3j T10)
# ---------------------------------------------------------------------------


async def _h_health(_req: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "wechat-publisher",
                              "version": "0.1.0"})


async def _h_state(_req: web.Request) -> web.Response:
    return web.json_response(STATE.snapshot())


async def _h_platforms(_req: web.Request) -> web.Response:
    from prisir_work.publisher import _REGISTRY
    out = []
    for name, p in _REGISTRY.items():
        try:
            st = p.status()
        except Exception as e:  # noqa: BLE001
            st = {"error": f"{type(e).__name__}: {e}"}
        out.append({"name": name, "title": p.title, "ready": bool(p.ready),
                    "status": st})
    return web.json_response({"ok": True, "platforms": out})


async def _h_recall(req: web.Request) -> web.Response:
    """prisirmp 公众号历史 recall(本地 FTS5)。"""
    q = req.query.get("q", "").strip()
    limit = int(req.query.get("limit", "20"))
    if not q:
        return web.json_response({"ok": True, "q": q, "hits": [], "total": 0,
                                  "warning": "empty_query"})
    try:
        from prisirmp.engine import Mprecaller
        rc = Mprecaller.shared()
        st = rc.status()
        if not st["enabled"]:
            return web.json_response({"ok": True, "q": q, "hits": [], "total": 0,
                                      "warning": "prisirmp 未启用(先跑 index)"})
        res = rc.search(q, limit=limit)
        return web.json_response({"ok": True, "q": q, **res})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "q": q, "hits": [], "total": 0,
                                  "warning": f"{type(e).__name__}: {e}"})


async def _h_login(req: web.Request) -> web.Response:
    """触发某平台扫码登录(异步 fire-and-forget)。"""
    body = await req.json() if req.body_exists else {}
    platform = (body.get("platform") or req.query.get("platform") or "").strip()
    if not platform:
        return web.json_response({"ok": False, "error": "empty_platform"}, status=400)
    try:
        from prisir_work.publisher import get as _get
        p = _get(platform)
        if p is None:
            return web.json_response({"ok": False, "error": "unknown_platform"})
        # login 通常会阻塞等扫码,放后台任务
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, p.login)
        return web.json_response({"ok": True, "platform": platform,
                                  "note": "扫码窗口已启动,请看浏览器"})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "platform": platform,
                                  "error": f"{type(e).__name__}: {e}"})


async def _h_publish(req: web.Request) -> web.Response:
    """发 HTML 草稿 / 图文 / 视频到指定平台。

    Body 三类:
      A) 公众号: {platform, html_path, title, cover, digest?, author?}
      B) 小红书图文: {platform:"xhs", mode:"text",
                     title, content, images?="img1,img2",
                     tags?, exec_real?=false}
      C) B站视频: {platform:"bilibili", mode:"video",
                   video, title, partition?, tid?, desc?, tag?, cover?,
                   exec_real?=false}
    """
    body = await req.json() if req.body_exists else {}
    platform = (body.get("platform") or "").strip()
    if not platform:
        return web.json_response({"ok": False, "error": "missing_platform"},
                                 status=400)
    mode = body.get("mode", "html")
    loop = asyncio.get_event_loop()
    try:
        if platform == "xhs" and mode == "text":
            from prisir_work.publisher import XhsPublisher
            result = await loop.run_in_executor(
                None, lambda: XhsPublisher().publish_text(
                    title=body.get("title", ""),
                    content=body.get("content", ""),
                    images=body.get("images", ""),
                    tags=body.get("tags", ""),
                    exec_real=bool(body.get("exec_real", False))))
        elif platform == "bilibili" and mode == "video":
            from prisir_work.publisher import BilibiliPublisher
            tid_val = body.get("tid")
            try:
                tid_val = int(tid_val) if tid_val not in (None, "") else None
            except (TypeError, ValueError):
                tid_val = None
            result = await loop.run_in_executor(
                None, lambda: BilibiliPublisher().publish_video(
                    video=body.get("video", ""),
                    title=body.get("title", ""),
                    partition=body.get("partition", ""),
                    tid=tid_val,
                    desc=body.get("desc", ""),
                    tag=body.get("tag", ""),
                    cover=body.get("cover", ""),
                    exec_real=bool(body.get("exec_real", False))))
        else:
            # 公众号(HTML 草稿)— 默认模式
            html_path = body.get("html_path", "").strip()
            title = body.get("title", "").strip()
            cover = body.get("cover", "").strip()
            digest = body.get("digest", "")
            author = body.get("author", "")
            if not html_path or not title or not cover:
                return web.json_response({
                    "ok": False, "error": "missing_fields",
                    "required": ["platform", "html_path", "title", "cover"]},
                    status=400)
            from prisir_work.publisher import publish as _publish
            result = await loop.run_in_executor(
                None, lambda: _publish(platform, html_path,
                                       title=title, cover=cover,
                                       digest=digest, author=author))
        d = result.to_dict()
        STATE.last_publish = d
        return web.json_response({"ok": True, **d})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "platform": platform,
                                  "error": f"{type(e).__name__}: {e}"})


async def _h_stats(req: web.Request) -> web.Response:
    """公众号数据回收。

    GET /api/stats?platform=wechat-oa&count=30                    — 原始拉取(P3j T10)
    GET /api/stats?platform=wechat-oa&mode=time|tags|...&data=...  — 走 publish-analytics
    GET /api/stats?mode=selftest                                  — 自检(无需数据)

    加了 mode 之后自动桥接 PublishAnalyticsCreator(Easel 端确定性分析)。
    """
    platform = req.query.get("platform", "wechat-oa").strip()
    count = int(req.query.get("count", "30"))
    mode = req.query.get("mode", "").strip()
    # 桥接 publish-analytics:有 mode → 走分析管线;没 mode → 原拉取
    if mode:
        data = req.query.get("data", "").strip()
        follower_log = req.query.get("follower_log", "").strip()
        profile = req.query.get("profile", "").strip()
        loop = asyncio.get_event_loop()
        try:
            from prisir_work.video_creator import PublishAnalyticsCreator
            result = await loop.run_in_executor(
                None, lambda: PublishAnalyticsCreator().create(
                    mode=mode, data=data, follower_log=follower_log,
                    profile=profile))
            return web.json_response(
                {"ok": True, "platform": platform, "mode": mode,
                 **result.to_dict()})
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "mode": mode,
                                      "error": f"{type(e).__name__}: {e}"})
    if platform != "wechat-oa":
        return web.json_response({"ok": False, "platform": platform,
                                  "error": "stats 目前只支持 wechat-oa"})
    try:
        from prisir_work.easel_bridge import easel
        br = easel()
        if not br.ready:
            return web.json_response({"ok": False, "error": "Easel 桥接未就绪"})
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: br.stats(count=count))
        return web.json_response({"ok": True, "platform": platform,
                                  "count": count, "data": result.parsed})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "platform": platform,
                                  "warning": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# HTTP handlers — 视频处理(P3j T12-B):视频元数据 / 字幕烧录 / 数据分析
# ---------------------------------------------------------------------------


async def _h_video_info(req: web.Request) -> web.Response:
    """ffprobe 视频元数据(走 VideoOpsCreator.op=info,带 query 参数路径)。
    GET /api/video/info?path=C:/xxx.mp4
    """
    path = req.query.get("path", "").strip()
    if not path:
        return web.json_response({"ok": False, "error": "missing_path"},
                                 status=400)
    if not Path(path).is_file():
        return web.json_response({"ok": False, "error": "path_not_found",
                                  "path": path}, status=404)
    loop = asyncio.get_event_loop()
    try:
        from prisir_work.video_creator import VideoOpsCreator
        result = await loop.run_in_executor(
            None, lambda: VideoOpsCreator().create(op="info", input=path))
        return web.json_response({"ok": True, **result.to_dict()})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "path": path,
                                  "error": f"{type(e).__name__}: {e}"})


async def _h_video_subtitle_burn(req: web.Request) -> web.Response:
    """字幕烧录进视频(走 SubtitleOpsCreator.op=burn)。
    POST /api/video/subtitle/burn
    Body: {input:"video.mp4", sub:"a.srt", output:"out.mp4",
           soft?:bool, lang?, force_style?, font_dir?}
    """
    body = await req.json() if req.body_exists else {}
    if not body:
        return web.json_response({"ok": False, "error": "missing_body"},
                                 status=400)
    input_v = body.get("input", "")
    sub = body.get("sub", "")
    output = body.get("output", "")
    if not input_v or not sub or not output:
        return web.json_response(
            {"ok": False, "error": "missing_fields",
             "required": ["input", "sub", "output"]}, status=400)
    loop = asyncio.get_event_loop()
    try:
        from prisir_work.video_creator import SubtitleOpsCreator
        # 透传软字幕/语言/样式/字体目录
        kwargs = {k: v for k, v in body.items()
                  if k in ("soft", "lang", "force_style", "font_dir")}
        result = await loop.run_in_executor(
            None, lambda: SubtitleOpsCreator().create(
                op="burn", input=input_v, sub=sub, output=output,
                **kwargs))
        return web.json_response({"ok": True, **result.to_dict()})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False,
                                  "error": f"{type(e).__name__}: {e}"})


async def _h_analytics(req: web.Request) -> web.Response:
    """发布数据分析(走 PublishAnalyticsCreator.mode)。
    GET /api/analytics?mode=time|tags|types|growth|all|selftest[&data=&follower_log=&profile=]
    mode 缺省 = selftest(永远 OK,不需数据)。
    """
    mode = req.query.get("mode", "selftest").strip()
    data = req.query.get("data", "").strip()
    follower_log = req.query.get("follower_log", "").strip()
    profile = req.query.get("profile", "").strip()
    loop = asyncio.get_event_loop()
    try:
        from prisir_work.video_creator import PublishAnalyticsCreator
        result = await loop.run_in_executor(
            None, lambda: PublishAnalyticsCreator().create(
                mode=mode, data=data, follower_log=follower_log,
                profile=profile))
        return web.json_response({"ok": True, **result.to_dict()})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "mode": mode,
                                  "error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# HTTP handlers — YouTube(P3j T13)
# ---------------------------------------------------------------------------


async def _h_youtube_status(_req: web.Request) -> web.Response:
    """YouTube 桥接层状态(探测依赖 + token + client_secrets)。"""
    loop = asyncio.get_event_loop()
    try:
        from prisir_work.youtube_bridge import YoutubeBridge
        br = YoutubeBridge()
        st = await loop.run_in_executor(None, br.status)
        return web.json_response({"ok": True, "platform": "youtube",
                                  "ready": br.ready, "status": st})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False, "platform": "youtube",
                                  "error": f"{type(e).__name__}: {e}"})


async def _h_youtube_auth(req: web.Request) -> web.Response:
    """触发 OAuth 授权(浏览器跳转 → 回调 → token 落盘)。

    GET /api/youtube/auth?port=8765
    默认本机端口 8765(google-auth-oauthlib.run_local_server 用)。
    """
    port = int(req.query.get("port", "8765"))
    loop = asyncio.get_event_loop()
    try:
        from prisir_work.youtube_bridge import YoutubeBridge
        br = YoutubeBridge()
        r = await loop.run_in_executor(None, lambda: br.auth(port=port))
        return web.json_response({"ok": True, **r.to_dict()})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False,
                                  "error": f"{type(e).__name__}: {e}"})


async def _h_youtube_upload(req: web.Request) -> web.Response:
    """上传视频到 YouTube。

    POST /api/youtube/upload
    Body: {
      video: "C:/videos/out.mp4",     # 必填
      title: "...",                    # 必填
      description?: "...",
      tags?: ["a", "b"],
      category_id?: "22",
      privacy?: "private|public|unlisted",
      exec_real?: false                # 默认 dry-run
    }
    """
    body = await req.json() if req.body_exists else {}
    if not body:
        return web.json_response({"ok": False, "error": "missing_body"},
                                 status=400)
    video = (body.get("video") or "").strip()
    title = (body.get("title") or "").strip()
    if not video or not title:
        return web.json_response(
            {"ok": False, "error": "missing_fields",
             "required": ["video", "title"]}, status=400)
    loop = asyncio.get_event_loop()
    try:
        from prisir_work.publisher import YoutubePublisher
        pub = YoutubePublisher()
        result = await loop.run_in_executor(
            None, lambda: pub.publish_video(
                video=video, title=title,
                description=body.get("description", ""),
                tags=body.get("tags") or [],
                category_id=str(body.get("category_id", "22")),
                privacy=body.get("privacy", "private"),
                exec_real=bool(body.get("exec_real", False))))
        d = result.to_dict()
        return web.json_response({"ok": True, **d})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False,
                                  "error": f"{type(e).__name__}: {e}"})


async def _h_youtube_list(req: web.Request) -> web.Response:
    """列出自己频道的视频。

    GET /api/youtube/list?max_results=10&exec_real=false
    """
    max_results = int(req.query.get("max_results", "10"))
    exec_real = req.query.get("exec_real", "false").lower() in ("1", "true", "yes")
    loop = asyncio.get_event_loop()
    try:
        from prisir_work.publisher import YoutubePublisher
        pub = YoutubePublisher()
        result = await loop.run_in_executor(
            None, lambda: pub.list_videos(max_results=max_results,
                                          exec_real=exec_real))
        return web.json_response({"ok": True, **result.to_dict()})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False,
                                  "error": f"{type(e).__name__}: {e}"})


async def _h_youtube_stats(_req: web.Request) -> web.Response:
    """拉取 YouTube 频道统计。"""
    loop = asyncio.get_event_loop()
    try:
        from prisir_work.publisher import YoutubePublisher
        pub = YoutubePublisher()
        result = await loop.run_in_executor(
            None, lambda: pub.channel_stats(exec_real=False))
        return web.json_response({"ok": True, **result.to_dict()})
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False,
                                  "error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# P3j T15-A: 💬 一句话 自然语言入口(parse + 真发)
# ---------------------------------------------------------------------------

async def _h_chat_parse(req: web.Request) -> web.Response:
    """POST /api/chat/parse {query: "..."} → parse_intent 结果 + body 预览。

    不真发,仅解析 + 补全 defaults。Web UI 用 confirm 卡让用户确认后才发。
    """
    try:
        body = await req.json() if req.body_exists() else {}
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "error": "invalid_json_body"})
    query = (body.get("query") or "").strip()
    if not query:
        return web.json_response({"ok": False, "error": "empty_query"})

    loop = asyncio.get_event_loop()
    try:
        from prisir_work import agent_natural_video as anv
        intent = await loop.run_in_executor(None, lambda: anv.parse_intent(query))
        if not intent.ok:
            return web.json_response({"ok": False,
                                      "error": intent.error})
        body_full = await loop.run_in_executor(
            None, lambda: anv.fill_defaults(intent.capability, intent.args))
        return web.json_response({
            "ok": True,
            "intent": intent.to_dict(),
            "body": body_full,
        })
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False,
                                  "error": f"{type(e).__name__}: {e}"})


async def _h_chat_exec(req: web.Request) -> web.Response:
    """POST /api/chat/exec {query, intent} → 真发。

    流程:用前端送回的 intent 走 endpoints._REGISTRY 的 handler —
    跟 L0/L1/L2/L3 都同一个执行路径。无 L3 二次确认卡(假设前端已确认过)。
    """
    try:
        body = await req.json() if req.body_exists() else {}
    except Exception:  # noqa: BLE001
        return web.json_response({"ok": False, "error": "invalid_json_body"})

    query = (body.get("query") or "").strip()
    intent = body.get("intent") or {}
    capability_id = intent.get("capability") or ""
    if not capability_id:
        return web.json_response({"ok": False, "error": "missing_capability"})
    missing = intent.get("missing") or []
    if missing:
        return web.json_response({
            "ok": False,
            "error": f"missing_required: {','.join(missing)}",
            "missing": missing,
        })

    loop = asyncio.get_event_loop()
    try:
        from prisir_work import agent_natural_video as anv
        from prisir_work import capability as cap_mod
        from prisir_work import endpoints as ep_mod
        cap_entry = await loop.run_in_executor(None, lambda: cap_mod.get(capability_id))
        if not cap_entry:
            return web.json_response({
                "ok": False, "error": f"capability_not_found:{capability_id}"})
        ep_entry = await loop.run_in_executor(
            None, lambda: ep_mod._REGISTRY.get(cap_entry["endpoint"]))
        if not ep_entry:
            return web.json_response({
                "ok": False, "error": f"endpoint_not_found:{cap_entry['endpoint']}"})

        # 重组 body:用前端送回的 args + 重新补 defaults(以防前端改过)
        args = intent.get("args") or {}
        full_body = await loop.run_in_executor(
            None, lambda: anv.fill_defaults(capability_id, args))

        # 同步跑 handler(可能很慢 — TTS/ASR/视频可能要分钟级)
        def _run():
            payload, http_status = ep_entry["handler"](full_body)
            return payload
        payload = await loop.run_in_executor(None, _run)

        return web.json_response({
            "ok": payload.get("ok", False),
            "query": query,
            "capability": capability_id,
            "body": full_body,
            "result": payload,
            "error": payload.get("error", "") if not payload.get("ok") else "",
        })
    except Exception as e:  # noqa: BLE001
        return web.json_response({"ok": False,
                                  "error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# 静态文件 + WS
# ---------------------------------------------------------------------------


async def _h_ws_state(req: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(req)
    try:
        while not ws.closed:
            try:
                await ws.send_json(STATE.snapshot())
            except Exception:
                break
            await asyncio.sleep(2.0)
    finally:
        await ws.close()
    return ws


async def _h_index(_req: web.Request) -> web.Response:
    """主页:从 static/index.html 加载(若缺失返 404 + 提示)。"""
    static_dir = _HERE / "prisIragent-wechat-publisher" / "static"
    idx = static_dir / "index.html"
    if not idx.is_file():
        return web.Response(
            text=f"前端文件未找到: {idx}", status=404,
            content_type="text/plain; charset=utf-8")
    return web.Response(text=idx.read_text(encoding="utf-8"),
                        content_type="text/html", charset="utf-8")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _build_app() -> web.Application:
    app = web.Application()
    # 健康 + 状态
    app.router.add_get("/api/health", _h_health)
    app.router.add_get("/api/state", _h_state)
    # 发布(P3j T10)
    app.router.add_get("/api/platforms", _h_platforms)
    app.router.add_get("/api/recall", _h_recall)
    app.router.add_post("/api/login", _h_login)
    app.router.add_post("/api/publish", _h_publish)
    app.router.add_get("/api/stats", _h_stats)
    # 视频创作(P3j T11)
    app.router.add_get("/api/video/creators", _h_video_creators)
    app.router.add_get("/api/video/env", _h_video_env)
    app.router.add_post("/api/video/create", _h_video_create)
    app.router.add_post("/api/video/orchestrate", _h_video_orchestrate)
    app.router.add_post("/api/video/publish", _h_video_publish)
    # 视频处理 / 数据分析(P3j T12-B)
    app.router.add_get("/api/video/info", _h_video_info)
    app.router.add_post("/api/video/subtitle/burn", _h_video_subtitle_burn)
    app.router.add_get("/api/analytics", _h_analytics)
    # YouTube(P3j T13)
    app.router.add_get("/api/youtube/status", _h_youtube_status)
    app.router.add_get("/api/youtube/auth", _h_youtube_auth)
    app.router.add_post("/api/youtube/upload", _h_youtube_upload)
    app.router.add_get("/api/youtube/list", _h_youtube_list)
    app.router.add_get("/api/youtube/stats", _h_youtube_stats)
    # P3j T15-A: 「💬 一句话」自然语言入口(parse + 真发)
    app.router.add_post("/api/chat/parse", _h_chat_parse)
    app.router.add_post("/api/chat/exec", _h_chat_exec)
    # P3j T17-B: 多媒体创作 Key 配置
    app.router.add_get("/api/media/keys", _h_media_keys_get)
    app.router.add_post("/api/media/keys", _h_media_keys_post)
    app.router.add_get("/api/media/status", _h_media_status)
    app.router.add_post("/api/media/test", _h_media_test)
    # WS
    app.router.add_get("/ws/state", _h_ws_state)
    # 静态
    app.router.add_get("/", _h_index)
    static_dir = _HERE / "prisIragent-wechat-publisher" / "static"
    if static_dir.is_dir():
        app.router.add_static("/static/", path=str(static_dir),
                              show_index=False)
    return app


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _cli() -> int:
    p = argparse.ArgumentParser(prog="prisIragent-wechat-publisher",
                                description="PrisirAI 多平台发布后端")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=0,
                   help="0 = 自动分配;也可写死(对齐 music/calendar 范式)")
    args = p.parse_args()
    port = args.port or _find_free_port()
    STATE.port = port
    app = _build_app()
    LOG.info("starting prisIragent-wechat-publisher on %s:%d (pid=%d)",
             args.host, port, STATE.pid)
    _write_port(port, STATE.pid)
    try:
        web.run_app(app, host=args.host, port=port, print=lambda *a, **k: None)
    finally:
        LOG.info("shutting down, clearing registry")
        try:
            if _LOCK_PATH.exists():
                _LOCK_PATH.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(_cli())