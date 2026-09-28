"""prisir_work/youtube_bridge.py — YouTube Data API v3 桥接层(P3j T13)。

定位:独立模块 — YouTube 不走 Easel 子进程,因为:
  · YouTube Data API v3 要 google-auth OAuth 流程(浏览器跳转 + token 持久化)
  · yt-dlp 走 youtube-dl 协议,版本与 Easel 端的依赖解耦更稳

范式与 easel_bridge 对齐:真实调用前先 ready 检查,失败 → ok=False + reason。

OAuth 流程:
  1. 首次跑 auth() → 浏览器打开授权页 → 用户登录 → 回调到 localhost:port
  2. token 落盘 ~/.prisIrai/youtube_token.json
  3. 后续 upload/list 走 refresh_token 静默刷新

降级范式:
  · 未装 google-api-python-client → ready=False + reason="google-api-python-client 未装"
  · 无 client_secrets.json → ready=False + reason="缺少 client_secrets.json"
  · 无 token → ok=False + reason="需要先调用 auth()"
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "YoutubeBridge",
    "YoutubeResult",
]


# ---------------------------------------------------------------------------
# 默认路径
# ---------------------------------------------------------------------------

def _token_path() -> Path:
    return Path.home() / ".prisIrai" / "youtube_token.json"


def _client_secrets_path() -> Path:
    return Path.home() / ".prisIrai" / "youtube_client_secrets.json"


def _find_credentials_search_paths() -> list[Path]:
    """按优先级找 client_secrets.json:
    1) ~/.prisIrai/youtube_client_secrets.json
    2) ./youtube_client_secrets.json(项目根,跑测试用)
    """
    return [_client_secrets_path(),
            Path.cwd() / "youtube_client_secrets.json"]


def _find_client_secrets() -> Path | None:
    """Module-level 函数,方便测试 monkeypatch(方法不好 patch)。"""
    for p in _find_credentials_search_paths():
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class YoutubeResult:
    ok: bool
    op: str = ""
    artifact: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "op": self.op,
                "artifact": self.artifact, "error": self.error}


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class YoutubeBridge:
    """YouTube Data API v3 桥接(独立模块,不走 Easel)。

    依赖(可选,缺失时 ready=False):
      · google-api-python-client (youtube upload/list)
      · yt-dlp                  (video info / download 备援)
      · google-auth-oauthlib    (OAuth flow)

    全部缺失 → 仍可 status() / 报具体缺失。
    """

    def __init__(self) -> None:
        self._has_google_api: bool | None = None
        self._has_yt_dlp: bool | None = None
        self._has_oauthlib: bool | None = None

    # ---- 能力探测(惰性) ----

    def _probe_google_api(self) -> bool:
        if self._has_google_api is None:
            try:
                import googleapiclient  # noqa: F401
                self._has_google_api = True
            except ImportError:
                self._has_google_api = False
        return self._has_google_api

    def _probe_yt_dlp(self) -> bool:
        if self._has_yt_dlp is None:
            try:
                import yt_dlp  # noqa: F401
                self._has_yt_dlp = True
            except ImportError:
                self._has_yt_dlp = False
        return self._has_yt_dlp

    def _probe_oauthlib(self) -> bool:
        if self._has_oauthlib is None:
            try:
                import google_auth_oauthlib  # noqa: F401
                self._has_oauthlib = True
            except ImportError:
                self._has_oauthlib = False
        return self._has_oauthlib

    def _find_client_secrets(self) -> Path | None:
        return _find_client_secrets()

    # ---- ready / status ----

    @property
    def ready(self) -> bool:
        return (
            self._probe_google_api()
            and self._probe_oauthlib()
            and self._find_client_secrets() is not None
        )

    def status(self) -> dict[str, Any]:
        secrets = self._find_client_secrets()
        token = _token_path()
        return {
            "ready": self.ready,
            "has_google_api_client": self._probe_google_api(),
            "has_oauthlib": self._probe_oauthlib(),
            "has_yt_dlp": self._probe_yt_dlp(),
            "client_secrets_path": str(secrets) if secrets else None,
            "token_path": str(token),
            "logged_in": token.is_file(),
            "hint": (
                "pip install google-api-python-client google-auth-oauthlib yt-dlp;"
                " 把 Google Cloud Console 申请的 client_secrets.json 放到 "
                "~/.prisIrai/youtube_client_secrets.json,然后跑 auth()"
            ),
        }

    # ---- OAuth flow ----

    def auth(self, *, port: int = 8765,
             scopes: Optional[list[str]] = None) -> YoutubeResult:
        """首次授权:浏览器跳转 → 回调 → token 落盘。

        走 google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file。
        """
        if scopes is None:
            scopes = [
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly",
            ]
        if not self._probe_oauthlib():
            return YoutubeResult(
                ok=False, op="auth",
                error="google-auth-oauthlib 未装 → "
                      "pip install google-auth-oauthlib")
        secrets = self._find_client_secrets()
        if not secrets:
            return YoutubeResult(
                ok=False, op="auth",
                error="client_secrets.json 未找到(候选: "
                      f"{[str(p) for p in _find_credentials_search_paths()]})")
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(
                str(secrets), scopes=scopes)
            creds = flow.run_local_server(port=port)
            _token_path().parent.mkdir(parents=True, exist_ok=True)
            _token_path().write_text(creds.to_json(), encoding="utf-8")
            return YoutubeResult(
                ok=True, op="auth",
                artifact={"token_path": str(_token_path()),
                          "scopes": list(creds.scopes)})
        except Exception as e:  # noqa: BLE001
            return YoutubeResult(
                ok=False, op="auth",
                error=f"{type(e).__name__}: {e}")

    # ---- Upload ----

    def upload(self, *, video: str, title: str = "",
               description: str = "",
               tags: list[str] | None = None,
               category_id: str = "22",  # 22 = People & Blog
               privacy: str = "private",
               exec_real: bool = False) -> YoutubeResult:
        """上传视频到 YouTube。

        exec_real=False(默认)= dry-run:仅校验参数与 token,不发请求。
        exec_real=True → 真发 resumable upload 到 YouTube Data API v3。
        """
        if not title:
            return YoutubeResult(
                ok=False, op="upload", error="title 必填")
        if privacy not in {"public", "private", "unlisted"}:
            return YoutubeResult(
                ok=False, op="upload",
                error=f"privacy 必须是 public/private/unlisted,当前 {privacy}")
        if not Path(video).is_file():
            return YoutubeResult(
                ok=False, op="upload",
                error=f"视频文件不存在: {video}")
        if not _token_path().is_file():
            return YoutubeResult(
                ok=False, op="upload",
                error="需要先调用 auth()(无 token)")
        if not self._probe_google_api():
            return YoutubeResult(
                ok=False, op="upload",
                error="google-api-python-client 未装")

        if not exec_real:
            return YoutubeResult(
                ok=True, op="upload",
                artifact={
                    "dry_run": True,
                    "video_path": video,
                    "title": title,
                    "category_id": category_id,
                    "privacy": privacy,
                    "tags_count": len(tags or []),
                    "hint": "exec_real=False 仅校验;真传请设 exec_real=True",
                })

        # 真上传
        try:
            import googleapiclient.discovery  # noqa: F401
            from googleapiclient.http import MediaFileUpload
            from google.oauth2.credentials import Credentials

            creds = Credentials.from_authorized_user_file(
                str(_token_path()),
                scopes=[
                    "https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube.readonly",
                ])
            service = googleapiclient.discovery.build(
                "youtube", "v3", credentials=creds)
            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags or [],
                    "categoryId": category_id,
                },
                "status": {"privacyStatus": privacy},
            }
            media = MediaFileUpload(video, resumable=True)
            insert = service.videos().insert(
                part="snippet,status", body=body,
                media_body=media)
            response = insert.execute()
            return YoutubeResult(
                ok=True, op="upload",
                artifact={
                    "video_id": response.get("id", ""),
                    "url": f"https://youtu.be/{response.get('id', '')}",
                    "title": response.get("snippet", {}).get("title", title),
                    "privacy": response.get("status", {}).get(
                        "privacyStatus", privacy),
                })
        except Exception as e:  # noqa: BLE001
            return YoutubeResult(
                ok=False, op="upload",
                error=f"{type(e).__name__}: {e}")

    # ---- List ----

    def list_videos(self, *, max_results: int = 10,
                    exec_real: bool = False) -> YoutubeResult:
        """列出当前用户频道的视频。"""
        if not _token_path().is_file():
            return YoutubeResult(
                ok=False, op="list",
                error="需要先调用 auth()(无 token)")
        if not self._probe_google_api():
            return YoutubeResult(
                ok=False, op="list",
                error="google-api-python-client 未装")
        if not exec_real:
            return YoutubeResult(
                ok=True, op="list",
                artifact={
                    "dry_run": True,
                    "max_results": max_results,
                    "hint": "exec_real=False 仅校验;真列请设 exec_real=True",
                })
        try:
            import googleapiclient.discovery  # noqa: F401
            from google.oauth2.credentials import Credentials

            creds = Credentials.from_authorized_user_file(
                str(_token_path()),
                scopes=["https://www.googleapis.com/auth/youtube.readonly"])
            service = googleapiclient.discovery.build(
                "youtube", "v3", credentials=creds)
            req = service.videos().list(
                part="snippet,status",
                myRating=None,
                maxResults=max_results,
                mine=True,
            )
            resp = req.execute()
            videos = []
            for item in resp.get("items", []):
                sn = item.get("snippet", {})
                st = item.get("status", {})
                videos.append({
                    "id": item.get("id", ""),
                    "title": sn.get("title", ""),
                    "published_at": sn.get("publishedAt", ""),
                    "privacy": st.get("privacyStatus", ""),
                    "url": f"https://youtu.be/{item.get('id', '')}",
                })
            return YoutubeResult(
                ok=True, op="list",
                artifact={"videos": videos, "count": len(videos)})
        except Exception as e:  # noqa: BLE001
            return YoutubeResult(
                ok=False, op="list",
                error=f"{type(e).__name__}: {e}")

    # ---- Channel stats ----

    def channel_stats(self, *, exec_real: bool = False) -> YoutubeResult:
        """拉取自己频道的统计(subscriber_count / view_count / video_count)。"""
        if not _token_path().is_file():
            return YoutubeResult(
                ok=False, op="stats",
                error="需要先调用 auth()(无 token)")
        if not self._probe_google_api():
            return YoutubeResult(
                ok=False, op="stats",
                error="google-api-python-client 未装")
        if not exec_real:
            return YoutubeResult(
                ok=True, op="stats",
                artifact={"dry_run": True,
                          "hint": "exec_real=False 仅校验"})
        try:
            import googleapiclient.discovery  # noqa: F401
            from google.oauth2.credentials import Credentials

            creds = Credentials.from_authorized_user_file(
                str(_token_path()),
                scopes=["https://www.googleapis.com/auth/youtube.readonly"])
            service = googleapiclient.discovery.build(
                "youtube", "v3", credentials=creds)
            req = service.channels().list(
                part="statistics,snippet",
                mine=True,
            )
            resp = req.execute()
            items = resp.get("items", [])
            if not items:
                return YoutubeResult(
                    ok=False, op="stats",
                    error="未找到自己的 channel(可能 token 没 youtube.readonly 权限)")
            ch = items[0]
            stats = ch.get("statistics", {})
            return YoutubeResult(
                ok=True, op="stats",
                artifact={
                    "channel_id": ch.get("id", ""),
                    "title": ch.get("snippet", {}).get("title", ""),
                    "subscriber_count": stats.get("subscriberCount", "0"),
                    "view_count": stats.get("viewCount", "0"),
                    "video_count": stats.get("videoCount", "0"),
                })
        except Exception as e:  # noqa: BLE001
            return YoutubeResult(
                ok=False, op="stats",
                error=f"{type(e).__name__}: {e}")


def shared() -> YoutubeBridge:
    """单例 helper。"""
    global _SHARED
    try:
        return _SHARED
    except NameError:
        _SHARED = YoutubeBridge()
    return _SHARED