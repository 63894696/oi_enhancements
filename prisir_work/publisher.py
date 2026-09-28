"""prisir_work/publisher.py — 多平台发布抽象层。

定位:F2「发布」门面 — PrisirAI 主对话 / 扩展 / shell 在不知道后端实现的前提下
发起「把这段 HTML 发到 X 平台」请求。

每个 PlatformPublisher 暴露:
  name        : 平台标识("wechat-oa" / "xhs" / "bilibili" / "douyin" …)
  title       : 人话展示名
  ready       : 是否就绪(依赖探测 / 登录态)
  status      : 返登录态细节 {loggedIn, name, ...}
  publish_html: 把 HTML + 标题 + 封面发到平台;返 PlatformPublishResult
  login       : 触发登录(异步 fire-and-forget;真状态靠 status() 轮询)

当前实现:
  · WechatOaPublisher   — 走 Easel 子进程(Easel 默认扫码会话,免 AppID/IP白名单)
  · XhsPublisher        — 走 Easel skill-xhs-publisher (Playwright + 持久化登录态)
  · BilibiliPublisher   — 走 Easel skill-bilibili-upload 包装 biliup CLI
  · NullPublisher       — 其他平台的占位(暂时 ready=False,接入时再实现)

设计参考:对齐 prisir_work/web_search.py 的 register_provider 范式 + RRF 降级
(「降级而非崩溃」:publisher 不就绪 → publish() 返 ok=False + reason,绝不抛栈)。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "PlatformPublishResult",
    "PlatformPublisher",
    "WechatOaPublisher",
    "XhsPublisher",
    "BilibiliPublisher",
    "YoutubePublisher",
    "NullPublisher",
    "register_publisher",
    "publish",
    "list_publishers",
]


@dataclass
class PlatformPublishResult:
    """统一发布结果。"""
    ok: bool
    platform: str = ""
    title: str = ""
    error: str = ""
    # 平台特定的产物(草稿 id / 推送 url / media_id 等)
    artifact: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)  # 后端原始返值

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "platform": self.platform,
            "title": self.title,
            "error": self.error,
            "artifact": self.artifact,
            "raw": self.raw,
        }


class PlatformPublisher(Protocol):
    """发布器协议。任何实现都能 register_publisher() 注册。"""

    name: str
    title: str

    @property
    def ready(self) -> bool: ...

    def status(self) -> dict[str, Any]: ...

    def login(self) -> PlatformPublishResult: ...

    def publish_html(self, html_path: str, *, title: str, cover: str,
                     digest: str = "", author: str = "") -> PlatformPublishResult: ...


# ---------------------------------------------------------------------------
# Wechat OA — 走 Easel
# ---------------------------------------------------------------------------

class WechatOaPublisher:
    """微信公众号发布(Easel 子进程桥接)。"""

    name = "wechat-oa"
    title = "微信公众号"

    def __init__(self) -> None:
        # 延迟 import,避免没装 Easel 时 prisir_work/publisher.py 就崩
        self._bridge = None
        self._status_cache: dict[str, Any] = {}
        self._status_ts = 0.0

    def _eb(self):
        if self._bridge is None:
            from . import easel_bridge
            self._bridge = easel_bridge.easel()
        return self._bridge

    @property
    def ready(self) -> bool:
        try:
            return bool(self._eb().ready)
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        now = time.time()
        if (now - self._status_ts) < 5.0 and self._status_cache:
            return self._status_cache
        try:
            r = self._eb().whoami(force=True)
            self._status_cache = {
                "loggedIn": bool(r.parsed.get("loggedIn")),
                "name": r.parsed.get("name", ""),
                "avatar": r.parsed.get("avatar", ""),
                "bridge_ready": self.ready,
            }
        except Exception as e:  # noqa: BLE001
            self._status_cache = {
                "loggedIn": False, "name": "", "avatar": "",
                "bridge_ready": self.ready,
                "error": f"{type(e).__name__}: {e}",
            }
        self._status_ts = now
        return self._status_cache

    def login(self) -> PlatformPublishResult:
        r = self._eb().login()
        return PlatformPublishResult(
            ok=r.ok, platform=self.name,
            error=r.error, raw=r.to_dict(),
        )

    def publish_html(self, html_path: str, *, title: str, cover: str,
                     digest: str = "", author: str = "") -> PlatformPublishResult:
        if not self.ready:
            return PlatformPublishResult(
                ok=False, platform=self.name,
                error="Easel 桥接未就绪(检查 EASEL_ROOT 或 ~/work/zju_easel/)")
        if not Path(html_path).is_file():
            return PlatformPublishResult(
                ok=False, platform=self.name,
                error=f"HTML 文件不存在: {html_path}")
        if not Path(cover).is_file():
            return PlatformPublishResult(
                ok=False, platform=self.name,
                error=f"封面图不存在: {cover}")
        # 登录态快检查(不发请求 — 只看缓存)
        st = self.status()
        if not st.get("loggedIn"):
            return PlatformPublishResult(
                ok=False, platform=self.name,
                error="mp 后台未登录(先跑 weixin_mp_stats.py login 扫码)")
        r = self._eb().publish_html(
            html_path, title=title, cover=cover,
            digest=digest, author=author)
        return PlatformPublishResult(
            ok=r.ok, platform=self.name, title=title,
            error=r.error,
            artifact={"media_id": r.parsed.get("media_id", "")},
            raw=r.to_dict(),
        )


# ---------------------------------------------------------------------------
# 占位 Null 实现(小红书/B站/抖音等接入时再补)
# ---------------------------------------------------------------------------

class XhsPublisher:
    """小红书发布(走 Easel skill-xhs-publisher via shared/xhs_publish.py)。

    注:小红书接收的不是 HTML 文件,而是 {title, content, images[], video}。
    publish_html 接受 html_path,内部做兜底(content 走空 + images 从 html 派生
    不现实),所以 XhsPublisher 不支持 HTML 入参 — 调用方传 title/content/images 即可。
    """

    name = "xhs"
    title = "小红书"

    def __init__(self) -> None:
        self._bridge = None
        self._status_cache: dict[str, Any] = {}
        self._status_ts = 0.0

    def _eb(self):
        if self._bridge is None:
            from . import easel_bridge
            self._bridge = easel_bridge.easel()
        return self._bridge

    @property
    def ready(self) -> bool:
        try:
            return bool(self._eb().ready)
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        now = time.time()
        if (now - self._status_ts) < 5.0 and self._status_cache:
            return self._status_cache
        try:
            r = self._eb().xhs_whoami()
            self._status_cache = {
                "loggedIn": r.ok and bool(r.parsed.get("loggedIn", True)),
                "bridge_ready": self.ready,
                "raw_ok": r.ok, "error": r.error,
            }
        except Exception as e:  # noqa: BLE001
            self._status_cache = {
                "loggedIn": False, "bridge_ready": self.ready,
                "error": f"{type(e).__name__}: {e}",
            }
        self._status_ts = now
        return self._status_cache

    def login(self) -> PlatformPublishResult:
        r = self._eb().xhs_login()
        return PlatformPublishResult(
            ok=r.ok, platform=self.name, error=r.error, raw=r.to_dict())

    def publish_html(self, html_path: str, *, title: str, cover: str,
                     digest: str = "", author: str = "") -> PlatformPublishResult:
        """小红书不接 HTML — 调用方应用 XhsPublisher.publish_text(...) 直发。

        为保持 Protocol 兼容,这里把 title 当 content + digest 当 tags 兜底 dry-run。
        真发请用 publish_text / publish_images 走 title/content/images。
        """
        if not self.ready:
            return PlatformPublishResult(
                ok=False, platform=self.name,
                error="Easel 桥接未就绪")
        r = self._eb().xhs_publish(
            title=title, content=digest, tags=author, exec_real=False)
        return PlatformPublishResult(
            ok=r.ok, platform=self.name, title=title,
            error=r.error if not r.ok else "",
            artifact={"note": "xhs 是 dry-run,需走 publish_text/images 真发"},
            raw=r.to_dict())

    # 小红书原生接口(用户传 title/content/images)
    def publish_text(self, *, title: str, content: str,
                     images: str = "", tags: str = "",
                     exec_real: bool = False) -> PlatformPublishResult:
        if not self.ready:
            return PlatformPublishResult(
                ok=False, platform=self.name,
                error="Easel 桥接未就绪")
        r = self._eb().xhs_publish(
            title=title, content=content, images=images, tags=tags,
            exec_real=exec_real)
        return PlatformPublishResult(
            ok=r.ok, platform=self.name, title=title,
            error=r.error if not r.ok else "",
            artifact={"exec_real": exec_real}, raw=r.to_dict())


class BilibiliPublisher:
    """B站视频投稿(走 Easel skill-bilibili-upload 包装 biliup CLI)。

    B站是视频,不走 HTML 入参 — 调用方传 video + title + partition。
    publish_html 兜底为 dry-run,真发请走 publish_video。
    """

    name = "bilibili"
    title = "哔哩哔哩"

    def __init__(self) -> None:
        self._bridge = None
        self._status_cache: dict[str, Any] = {}
        self._status_ts = 0.0

    def _eb(self):
        if self._bridge is None:
            from . import easel_bridge
            self._bridge = easel_bridge.easel()
        return self._bridge

    @property
    def ready(self) -> bool:
        try:
            return bool(self._eb().ready)
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        # B站 cookie 文件存在即认为已登录
        now = time.time()
        if (now - self._status_ts) < 5.0 and self._status_cache:
            return self._status_cache
        try:
            cookies_path = Path.home() / "cookies.json"
            logged_in = cookies_path.is_file()
            self._status_cache = {
                "loggedIn": logged_in,
                "cookies": str(cookies_path),
                "bridge_ready": self.ready,
            }
        except Exception as e:  # noqa: BLE001
            self._status_cache = {
                "loggedIn": False, "bridge_ready": self.ready,
                "error": f"{type(e).__name__}: {e}",
            }
        self._status_ts = now
        return self._status_cache

    def login(self) -> PlatformPublishResult:
        r = self._eb().bili_login()
        return PlatformPublishResult(
            ok=r.ok, platform=self.name, error=r.error, raw=r.to_dict())

    def publish_html(self, html_path: str, *, title: str, cover: str,
                     digest: str = "", author: str = "") -> PlatformPublishResult:
        """B站是视频,不支持 HTML — 兜底 dry-run + 明确提示走 publish_video。"""
        if not self.ready:
            return PlatformPublishResult(
                ok=False, platform=self.name,
                error="Easel 桥接未就绪")
        return PlatformPublishResult(
            ok=False, platform=self.name, title=title,
            error="B站只接视频,请用 publish_video(video, title, partition)")

    def publish_video(self, *, video: str, title: str = "",
                      partition: str = "", tid: int | None = None,
                      desc: str = "", tag: str = "",
                      cover: str = "", exec_real: bool = False) -> PlatformPublishResult:
        if not self.ready:
            return PlatformPublishResult(
                ok=False, platform=self.name,
                error="Easel 桥接未就绪")
        r = self._eb().bili_upload(
            video=video, title=title, partition=partition, tid=tid,
            desc=desc, tag=tag, cover=cover, exec_real=exec_real)
        return PlatformPublishResult(
            ok=r.ok, platform=self.name, title=title,
            error=r.error if not r.ok else "",
            artifact={"exec_real": exec_real}, raw=r.to_dict())


class YoutubePublisher:
    """YouTube Data API v3 发布(P3j T13)。

    独立模块 youtube_bridge.py — 不走 Easel 子进程。
    OAuth token 落 ~/.prisIrai/youtube_token.json;client_secrets 在
    ~/.prisIrai/youtube_client_secrets.json(GCP Console 申请的 OAuth client)。

    publish_html 兜底为 ok=False + 提示走 publish_video;
    真上传/列表/统计用 publish_video() / list_videos() / channel_stats()。
    """

    name = "youtube"
    title = "YouTube"

    def __init__(self) -> None:
        self._bridge = None
        self._status_cache: dict[str, Any] = {}
        self._status_ts = 0.0

    def _yb(self):
        if self._bridge is None:
            from . import youtube_bridge
            self._bridge = youtube_bridge.YoutubeBridge()
        return self._bridge

    @property
    def ready(self) -> bool:
        try:
            return bool(self._yb().ready)
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        now = time.time()
        if (now - self._status_ts) < 5.0 and self._status_cache:
            return self._status_cache
        try:
            self._status_cache = self._yb().status()
        except Exception as e:  # noqa: BLE001
            self._status_cache = {"error": f"{type(e).__name__}: {e}"}
        self._status_ts = now
        return self._status_cache

    def login(self) -> PlatformPublishResult:
        """触发 OAuth 授权(浏览器跳转 → 回调 → token 落盘)。
        注意:这个是阻塞调用,会在 HTTP handler 里跑在 executor。
        """
        r = self._yb().auth()
        return PlatformPublishResult(
            ok=r.ok, platform=self.name, error=r.error,
            artifact=r.artifact, raw=r.to_dict())

    def publish_html(self, html_path: str, *, title: str, cover: str,
                     digest: str = "", author: str = "") -> PlatformPublishResult:
        """YouTube 只接视频,不支持 HTML — 兜底明确提示走 publish_video。"""
        return PlatformPublishResult(
            ok=False, platform=self.name, title=title,
            error="YouTube 只接视频,请用 publish_video(video, title, ...)")

    def publish_video(self, *, video: str, title: str = "",
                      description: str = "", tags: list[str] | None = None,
                      category_id: str = "22",
                      privacy: str = "private",
                      exec_real: bool = False) -> PlatformPublishResult:
        """YouTube 上传视频。

        exec_real=False(默认)= dry-run:仅校验参数 + token 存在性,不发请求。
        exec_real=True → 真发 resumable upload。
        """
        if not self.ready and exec_real:
            # ready=False 时 dry-run 仍可(只校验参数)— 仅真发挡掉
            st = self.status()
            return PlatformPublishResult(
                ok=False, platform=self.name, title=title,
                error=f"YouTube 未就绪:{st.get('hint', 'see status')}")
        r = self._yb().upload(
            video=video, title=title, description=description,
            tags=tags or [], category_id=category_id,
            privacy=privacy, exec_real=exec_real)
        return PlatformPublishResult(
            ok=r.ok, platform=self.name, title=title,
            error=r.error if not r.ok else "",
            artifact=r.artifact, raw=r.to_dict())

    def list_videos(self, *, max_results: int = 10,
                    exec_real: bool = False) -> PlatformPublishResult:
        r = self._yb().list_videos(max_results=max_results,
                                   exec_real=exec_real)
        return PlatformPublishResult(
            ok=r.ok, platform=self.name,
            error=r.error if not r.ok else "",
            artifact=r.artifact, raw=r.to_dict())

    def channel_stats(self, *, exec_real: bool = False) -> PlatformPublishResult:
        r = self._yb().channel_stats(exec_real=exec_real)
        return PlatformPublishResult(
            ok=r.ok, platform=self.name,
            error=r.error if not r.ok else "",
            artifact=r.artifact, raw=r.to_dict())


class NullPublisher:
    """未实现平台的占位。ready 永远 False;调用直接返失败原因。"""

    def __init__(self, name: str, title: str) -> None:
        self.name = name
        self.title = title

    @property
    def ready(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {"loggedIn": False, "name": "", "avatar": "",
                "bridge_ready": False,
                "error": f"{self.name} 发布器尚未实现"}

    def login(self) -> PlatformPublishResult:
        return PlatformPublishResult(ok=False, platform=self.name,
                                     error=f"{self.name} 尚未实现")

    def publish_html(self, html_path: str, *, title: str, cover: str,
                     digest: str = "", author: str = "") -> PlatformPublishResult:
        return PlatformPublishResult(ok=False, platform=self.name,
                                     error=f"{self.name} 尚未实现")


# ---------------------------------------------------------------------------
# 注册表 + 路由
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, PlatformPublisher] = {}


def register_publisher(p: PlatformPublisher) -> None:
    """注册一个发布器。重复名 → 覆盖(用于热替换)。"""
    _REGISTRY[p.name] = p


def get(name: str) -> PlatformPublisher | None:
    return _REGISTRY.get(name)


def list_publishers() -> list[dict[str, Any]]:
    return [
        {"name": p.name, "title": p.title, "ready": bool(p.ready)}
        for p in _REGISTRY.values()
    ]


def publish(platform: str, html_path: str, *, title: str, cover: str,
            digest: str = "", author: str = "") -> PlatformPublishResult:
    """按平台名路由发布。平台不在/未就绪 → 返 ok=False + reason,绝不抛栈。"""
    p = _REGISTRY.get(platform)
    if p is None:
        return PlatformPublishResult(
            ok=False, platform=platform,
            error=f"未知平台: {platform}(已注册: {[x.name for x in _REGISTRY.values()]})")
    return p.publish_html(
        html_path, title=title, cover=cover, digest=digest, author=author)


# ---------------------------------------------------------------------------
# 默认注册(Easel wechat-oa + 占位)
# ---------------------------------------------------------------------------

def _register_defaults() -> None:
    register_publisher(WechatOaPublisher())
    register_publisher(XhsPublisher())
    register_publisher(BilibiliPublisher())
    register_publisher(YoutubePublisher())
    # 占位(抖音 / 知乎 / 视频号)— 等接 Easel 对应 skill 再替换
    for nm, ti in [
        ("douyin", "抖音"),
        ("zhihu", "知乎"),
        ("wechat-channels", "微信视频号"),
    ]:
        register_publisher(NullPublisher(nm, ti))


_register_defaults()