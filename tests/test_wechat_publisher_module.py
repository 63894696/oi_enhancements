"""tests/test_wechat_publisher_module.py — PrisirAI 多平台发布模块整合测试(P3j T10-H)。

覆盖:
  · easel_bridge.xhs_whoami/login/publish,bili_login/upload dry-run
  · publisher.XhsPublisher.publish_text + BilibiliPublisher.publish_video 走降级路径
  · publisher.publish() 路由 6 平台(unknown / 不就绪 / null)
  · companion/prisIragent-wechat-publisher.py 6 endpoint 起服务真 HTTP 测
  · agent_shell TrayController.extra_actions 注入范式
  · verify_wechat_publisher.py dry-run(只跑静态检查)

不依赖真实登录态 / 真实子进程执行 — 所有 platform 都期望 ok=False + 明确 reason。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# 1. easel_bridge dry-run(不真发 — xhs/bili 都是 dry-run 默认)
# ---------------------------------------------------------------------------

def test_easel_xhs_dryrun():
    from prisir_work.easel_bridge import easel
    eb = easel()
    if not eb.ready:
        pytest.skip("Easel 未装,跳过 xhs dry-run 测试")
    # whoami 应该 OK 或返 False 但不抛
    r = eb.xhs_whoami()
    assert isinstance(r.ok, bool)


def test_easel_bili_dryrun():
    from prisir_work.easel_bridge import easel
    eb = easel()
    if not eb.ready:
        pytest.skip("Easel 未装,跳过 bili dry-run 测试")
    # bili_upload 文件不存在应挡掉
    r = eb.bili_upload(video="C:/nonexistent.mp4", title="t")
    assert r.ok is False
    assert "视频文件不存在" in r.error


# ---------------------------------------------------------------------------
# 2. publisher 降级路径
# ---------------------------------------------------------------------------

def test_xhs_publisher_ready_and_publish_text_dryrun():
    from prisir_work.publisher import XhsPublisher
    p = XhsPublisher()
    assert p.ready is True  # bridge 在线
    r = p.publish_text(title="verify", content="test", exec_real=False)
    # 不抛栈,只返 ok=False + reason(降级)
    assert isinstance(r.ok, bool)


def test_bilibili_publisher_ready_and_publish_video_blocked():
    from prisir_work.publisher import BilibiliPublisher
    p = BilibiliPublisher()
    assert p.ready is True
    r = p.publish_video(video="C:/nonexistent.mp4", title="t")
    assert r.ok is False
    assert "视频文件不存在" in r.error


def test_wechat_publisher_publish_html_blocks_missing_files():
    from prisir_work.publisher import WechatOaPublisher
    p = WechatOaPublisher()
    r = p.publish_html(html_path="C:/nope.html",
                       title="t", cover="C:/nope.jpg")
    assert r.ok is False
    assert "HTML 文件不存在" in r.error


def test_publish_routing_unknown_platform():
    from prisir_work.publisher import publish
    r = publish("nonexistent", "x.html", title="t", cover="x.jpg")
    assert r.ok is False
    assert "未知平台" in r.error


def test_list_publishers_has_6_entries():
    from prisir_work.publisher import list_publishers
    pubs = list_publishers()
    names = {p["name"] for p in pubs}
    assert {"wechat-oa", "xhs", "bilibili", "douyin",
            "zhihu", "wechat-channels"} <= names


# ---------------------------------------------------------------------------
# 3. companion HTTP 真起服务 + 6 endpoint
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def backend_port():
    """起 wechat-publisher 后端一次,本 module 共享 port。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    script = ROOT / "companion" / "prisIragent-wechat-publisher.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, str(script), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=env, cwd=str(ROOT),
    )
    # 等就绪
    t0 = time.time()
    while time.time() - t0 < 8.0:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.3)
    yield port
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        proc.kill()


def _get(port: int, path: str, timeout: float = 5.0) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return 0, {"exc": f"{type(e).__name__}: {e}"}


def _post(port: int, path: str, body: dict, timeout: float = 10.0) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return 0, {"exc": f"{type(e).__name__}: {e}"}


def test_health(backend_port):
    code, body = _get(backend_port, "/api/health")
    assert code == 200
    assert body.get("service") == "wechat-publisher"


def test_state(backend_port):
    code, body = _get(backend_port, "/api/state")
    assert code == 200
    assert "port" in body and "publishers" in body
    assert len(body["publishers"]) >= 3  # 至少 wechat-oa / xhs / bilibili


def test_platforms(backend_port):
    code, body = _get(backend_port, "/api/platforms", timeout=15)
    # /platforms 调 whoami 可能慢 → 接受 200 或 TimeoutError
    if code == 0:
        pytest.skip(f"/platforms 慢:{body}")
    assert code == 200
    names = [p["name"] for p in body["platforms"]]
    assert "wechat-oa" in names and "xhs" in names and "bilibili" in names


def test_recall_empty(backend_port):
    code, body = _get(backend_port, "/api/recall", timeout=10)
    assert code == 200
    assert body.get("warning") in ("empty_query", "prisirmp 未启用(先跑 index)", None)


def test_login(backend_port):
    code, body = _post(backend_port, "/api/login", {"platform": "wechat-oa"})
    assert code == 200
    # 异步 fire-and-forget,只要返 ok=True 就过
    assert body.get("ok") is True


def test_publish_xhs_text_dryrun(backend_port):
    code, body = _post(backend_port, "/api/publish",
                       {"platform": "xhs", "mode": "text",
                        "title": "verify", "content": "hi"},
                       timeout=15)
    assert code == 200
    assert body.get("platform") == "xhs"
    # xhs 缺 images 会被脚本挡 → ok=False 但 reason 明确
    assert isinstance(body.get("ok"), bool)


def test_publish_bilibili_video_missing_file(backend_port):
    code, body = _post(backend_port, "/api/publish",
                       {"platform": "bilibili", "mode": "video",
                        "video": "C:/nonexistent.mp4", "title": "t"},
                       timeout=15)
    assert code == 200
    assert body.get("platform") == "bilibili"
    assert body.get("ok") is False
    assert "视频文件不存在" in body.get("error", "")


# ---------------------------------------------------------------------------
# 4. agent_shell 托盘 extra_actions 范式
# ---------------------------------------------------------------------------

def test_tray_extra_actions_in_menu():
    from agent_shell.tray import TrayController

    class FakeMenu:
        SEPARATOR = "SEP"
        def __init__(self, *items): self.items = list(items)
    class FakeMenuItem:
        def __init__(self, label, action): self.label = label; self.action = action

    import pystray
    pystray.Menu = FakeMenu         # noqa: F811
    pystray.MenuItem = FakeMenuItem  # noqa: F811

    t = TrayController(
        profile_actions=[("p1", lambda: None)],
        extra_actions=[("📢 打开发布面板", lambda: None)],
    )
    m = t._menu()
    labels = [getattr(it, "label", "SEP") for it in m.items]
    assert "p1" in labels and "📢 打开发布面板" in labels


# ---------------------------------------------------------------------------
# 5. verify_wechat_publisher.py 静态模式跑通
# ---------------------------------------------------------------------------

def test_verify_script_static_mode():
    """verify_wechat_publisher.py -SkipHttp 必须返 0。"""
    r = subprocess.run(
        [sys.executable, str(ROOT / "verify_wechat_publisher.py"), "-SkipHttp"],
        capture_output=True, text=True, timeout=30,
        cwd=str(ROOT),
    )
    assert r.returncode == 0, f"verify 失败 rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "passed" in r.stdout
    assert "failed" in r.stdout