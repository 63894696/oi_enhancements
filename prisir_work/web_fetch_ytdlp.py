"""web_fetch_ytdlp.py — yt-dlp 通用 fetcher(2026-09-26 ship,P3j T21-B)。

把 yt_dlp 从 youtube_bridge.py(只 YouTube)提升成通用 fetcher,处理 yt-dlp 支持的
200+ 网站的元数据 + 字幕探测(B站 / 微博 / Twitter / Reddit / Vimeo / Niconico
/ TikTok 等)。

设计:
  · 用 yt_dlp.YoutubeDL(quiet=True, no_warnings=True, skip_download=True)
    抽 metadata + 探测字幕列表(不下载任何视频流)
  · 超时通过 yt-dlp 的 socket_timeout 选项控制
  · 失败一律返 {ok: False, error: "ytdlp_xxx"} 不 raise
  · 字幕只探测语言列表(不下载字幕内容 — 字幕文件留给 youtube_bridge 单独下载)
  · 单 fetch 限制 30s,markdown ≤ 50000 字符截断

公开 API:
  · ytdlp_meta(url, options) → {content, meta}  (无下载,只取信息)
  · ytdlp_health() → {ok, version, supported_sites}

为什么独立模块:
  · 保持 youtube_bridge.py 专注 YouTube Data API v3 + 上传路径
  · yt-dlp 本身是大库,所有抽取错误隔离在本模块
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

YTDLP_TIMEOUT = 30.0
YTDLP_MAX_CHARS = 50_000


def ytdlp_meta(url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """yt-dlp 抽 URL 的 metadata + 字幕语言列表(无下载)。

    给 web_fetch.fetch() 调用,签名同 register_fetcher 注册的 fetcher。
    失败返 {content: "", meta: {ok: False, error: "ytdlp_xxx"}},永不 raise。
    """
    options = options or {}
    timeout = float(options.get("timeout", YTDLP_TIMEOUT))
    max_chars = int(options.get("max_chars", YTDLP_MAX_CHARS))

    if not url or not isinstance(url, str):
        return {"content": "", "meta": {"fetcher": "ytdlp",
                                        "ok": False, "error": "bad_url"}}

    try:
        import yt_dlp
    except Exception as e:
        return {"content": "", "meta": {"fetcher": "ytdlp", "ok": False,
                                        "error": "ytdlp_not_installed",
                                        "detail": str(e)[:200]}}

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "listsubtitles": True,
        "socket_timeout": timeout,
        "extract_flat": False,
        "ignoreerrors": False,
    }

    t0 = time.monotonic()
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        return {"content": "", "meta": {"fetcher": "ytdlp", "ok": False,
                                        "error": "ytdlp_download_error",
                                        "detail": str(e)[:200],
                                        "elapsed_ms": int((time.monotonic()-t0)*1000)}}
    except Exception as e:
        return {"content": "", "meta": {"fetcher": "ytdlp", "ok": False,
                                        "error": f"ytdlp_{type(e).__name__}",
                                        "detail": str(e)[:200],
                                        "elapsed_ms": int((time.monotonic()-t0)*1000)}}

    if not isinstance(info, dict):
        return {"content": "", "meta": {"fetcher": "ytdlp", "ok": False,
                                        "error": "ytdlp_empty_info",
                                        "elapsed_ms": int((time.monotonic()-t0)*1000)}}

    # 提取关键字段(兼容 playlist / 单视频 / 站点不同返回结构)
    title = (info.get("title") or "").strip()
    uploader = (info.get("uploader") or info.get("channel")
                or info.get("creator") or info.get("uploader_id", "") or "").strip()
    duration = info.get("duration", 0) or 0
    description = (info.get("description") or "").strip()
    webpage_url = info.get("webpage_url") or info.get("url") or url
    extractor = (info.get("extractor") or info.get("extractor_key") or "").strip()
    view_count = info.get("view_count", 0) or 0
    like_count = info.get("like_count", 0) or 0
    upload_date = info.get("upload_date", "") or ""
    webpage_url_domain = info.get("webpage_url_domain") or ""

    # 字幕:取前 10 个(manual 优先 + 自动)
    subs: dict[str, Any] = info.get("subtitles") or {}
    auto_subs: dict[str, Any] = info.get("automatic_captions") or {}
    sub_keys: list[tuple[str, str]] = []
    for lang in subs:
        sub_keys.append((lang, "manual"))
    for lang in auto_subs:
        if lang not in [k for k, _ in sub_keys]:
            sub_keys.append((lang, "auto"))

    # 格式化 markdown
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
    elif title is None:
        lines.append(f"# (no title)")
    meta_bits: list[str] = []
    if extractor:
        meta_bits.append(f"Platform: {extractor}")
    if uploader:
        meta_bits.append(f"Uploader: {uploader}")
    if duration:
        # 转 mm:ss
        if duration >= 3600:
            h, m, s = duration // 3600, (duration % 3600) // 60, duration % 60
            meta_bits.append(f"Duration: {h}:{m:02d}:{s:02d}")
        else:
            m, s = duration // 60, duration % 60
            meta_bits.append(f"Duration: {m}:{s:02d}")
    if view_count:
        meta_bits.append(f"Views: {view_count}")
    if upload_date:
        meta_bits.append(f"Uploaded: {upload_date}")
    if meta_bits:
        lines.append(f"_{' · '.join(meta_bits)}_")
    lines.append(f"_URL: {webpage_url}_")
    lines.append("")

    if description:
        lines.append("## Description")
        lines.append(description[:2000])
        lines.append("")

    if sub_keys:
        lines.append("## Subtitles")
        for lang, kind in sub_keys[:10]:
            lines.append(f"- `{lang}` ({kind})")
        lines.append("")

    content = "\n".join(lines)
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n... [truncated at {max_chars} chars] ..."

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return {
        "content": content,
        "meta": {
            "fetcher": "ytdlp",
            "ok": bool(title),
            "format": "markdown",
            "title": title,
            "uploader": uploader,
            "duration": duration,
            "extractor": extractor,
            "webpage_url": webpage_url,
            "view_count": view_count,
            "like_count": like_count,
            "upload_date": upload_date,
            "subtitle_languages": sub_keys[:10],
            "subtitle_count": len(sub_keys),
            "elapsed_ms": elapsed_ms,
        },
    }


def ytdlp_health() -> dict[str, Any]:
    """检查 yt-dlp 版本 + 支持的 extractor 数量(无需网络)。"""
    try:
        import yt_dlp
        from yt_dlp.extractor import list_extractors
        ext_count = len(list_extractors())
        return {
            "ok": True,
            "version": yt_dlp.version.__version__,
            "supported_sites_count": ext_count,
            "skip_download_by_default": True,
        }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__,
                "detail": str(e)[:200]}


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json
    import sys as _sys

    cmd = _sys.argv[1] if len(_sys.argv) > 1 else "health"
    if cmd == "health":
        print(_json.dumps(ytdlp_health(), ensure_ascii=False, indent=2))
    elif cmd == "meta":
        url = _sys.argv[2] if len(_sys.argv) > 2 else ""
        print(_json.dumps(ytdlp_meta(url), ensure_ascii=False, indent=2))
    else:
        print(f"unknown cmd: {cmd} (use health/meta)")