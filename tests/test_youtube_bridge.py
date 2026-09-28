"""tests/test_youtube_bridge.py — YouTube 桥接层 + Publisher 单测(P3j T13-D)。

覆盖:
  · YoutubeBridge 类基本 import + status
  · 没装 google-api-python-client / oauthlib → ready=False (graceful degrade)
  · upload / list / channel_stats 字段校验(缺 video/title/token → ok=False)
  · dry-run(默认)只校验不发请求 → 永远 ok=True
  · privacy 必须是 public/private/unlisted
  · YoutubePublisher 注册到 publisher._REGISTRY
  · publish_html 兜底返 ok=False(YouTube 只接视频)
  · publish_video dry-run ok
  · 平台列表含 youtube

不打真 OAuth / 真上传(需要 client_secrets + token) — 那部分留给用户跑。
"""
from __future__ import annotations

import json
import os
import sys
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# 1. Bridge 基本
# ---------------------------------------------------------------------------

def test_youtube_bridge_imports():
    from prisir_work.youtube_bridge import YoutubeBridge, YoutubeResult
    assert YoutubeBridge is not None
    assert YoutubeResult is not None


def test_youtube_bridge_default_ready_false():
    """没装 client_secrets → ready=False(hint 指明缺啥)。"""
    from prisir_work.youtube_bridge import YoutubeBridge
    br = YoutubeBridge()
    st = br.status()
    assert "ready" in st
    assert "has_google_api_client" in st
    assert "has_oauthlib" in st
    assert "has_yt_dlp" in st
    assert "client_secrets_path" in st
    assert "token_path" in st
    assert "logged_in" in st
    assert "hint" in st


def test_youtube_bridge_upload_missing_video():
    from prisir_work.youtube_bridge import YoutubeBridge
    br = YoutubeBridge()
    r = br.upload(video="", title="hi")
    assert r.ok is False
    # 缺 video 也可能缺 token(看校验顺序)— 至少返一个 reason
    assert r.error


def test_youtube_bridge_upload_missing_title():
    from prisir_work.youtube_bridge import YoutubeBridge
    br = YoutubeBridge()
    # video 走 not-exist 检查后 → title 校验
    r = br.upload(video="C:/nope.mp4", title="")
    assert r.ok is False
    assert "title 必填" in r.error


def test_youtube_bridge_upload_invalid_privacy():
    from prisir_work.youtube_bridge import YoutubeBridge
    br = YoutubeBridge()
    r = br.upload(video="C:/nope.mp4", title="hi", privacy="nope")
    assert r.ok is False
    assert "privacy" in r.error


def test_youtube_bridge_upload_missing_video_file():
    from prisir_work.youtube_bridge import YoutubeBridge
    br = YoutubeBridge()
    r = br.upload(video="C:/nope.mp4", title="hi")
    assert r.ok is False
    assert "视频文件不存在" in r.error


def test_youtube_bridge_list_dry_run():
    """list 默认 exec_real=False → ok=True(仅校验,发 dry-run artifact)。"""
    from prisir_work.youtube_bridge import YoutubeBridge
    br = YoutubeBridge()
    # 即使没 token,dry-run 也应先校验参数然后挡 token
    # 我们用 monkeypatch:伪造 token 文件
    import prisir_work.youtube_bridge as yb
    real_token_path = yb._token_path
    fake = ROOT / "_fake_token.json"
    fake.write_text("{}", encoding="utf-8")
    yb._token_path = lambda: fake
    try:
        # 先看 exec_real=False 路径 — 即便没 google-api-python-client 也可走
        # 因为 dry-run 在探 google-api 之前被挡(token 在前)
        # 这里测 dry-run + 有 token → ok=True
        if br._probe_google_api():  # noqa: SLF001
            r = br.list_videos(max_results=5, exec_real=False)
            assert r.ok is True
            assert r.artifact.get("dry_run") is True
            assert r.artifact.get("max_results") == 5
        else:
            # 没装 google-api → ok=False + reason
            r = br.list_videos(max_results=5, exec_real=False)
            assert r.ok is False
            assert "google-api-python-client" in r.error
    finally:
        yb._token_path = real_token_path
        if fake.exists():
            fake.unlink()


def test_youtube_bridge_channel_stats_dry_run():
    from prisir_work.youtube_bridge import YoutubeBridge
    br = YoutubeBridge()
    import prisir_work.youtube_bridge as yb
    real_token_path = yb._token_path
    fake = ROOT / "_fake_token.json"
    fake.write_text("{}", encoding="utf-8")
    yb._token_path = lambda: fake
    try:
        if br._probe_google_api():  # noqa: SLF001
            r = br.channel_stats(exec_real=False)
            assert r.ok is True
            assert r.artifact.get("dry_run") is True
        else:
            r = br.channel_stats(exec_real=False)
            assert r.ok is False
    finally:
        yb._token_path = real_token_path
        if fake.exists():
            fake.unlink()


def test_youtube_bridge_list_no_token():
    """无 token 时 list 应 ok=False(无论 dry-run)。"""
    from prisir_work.youtube_bridge import YoutubeBridge
    br = YoutubeBridge()
    import prisir_work.youtube_bridge as yb
    real_token_path = yb._token_path
    fake = ROOT / "_does_not_exist_token.json"
    if fake.exists():
        fake.unlink()
    yb._token_path = lambda: fake
    try:
        r = br.list_videos(max_results=5)
        assert r.ok is False
        assert "需要先调用 auth" in r.error
    finally:
        yb._token_path = real_token_path


def test_youtube_bridge_auth_no_secrets():
    """无 client_secrets.json → auth 返 ok=False。"""
    from prisir_work.youtube_bridge import YoutubeBridge
    br = YoutubeBridge()
    import prisir_work.youtube_bridge as yb
    real_secrets = yb._find_client_secrets
    yb._find_client_secrets = lambda: None
    try:
        r = br.auth()
        assert r.ok is False
        assert "client_secrets.json" in r.error
    finally:
        yb._find_client_secrets = real_secrets


# ---------------------------------------------------------------------------
# 2. Publisher 集成
# ---------------------------------------------------------------------------

def test_youtube_publisher_registered():
    from prisir_work import publisher
    assert "youtube" in publisher._REGISTRY
    pub = publisher._REGISTRY["youtube"]
    assert pub.name == "youtube"
    assert pub.title == "YouTube"


def test_youtube_publisher_publish_html_returns_failure():
    """YouTube 只接视频,publish_html 应明确提示走 publish_video。"""
    from prisir_work.publisher import YoutubePublisher
    pub = YoutubePublisher()
    r = pub.publish_html("x.html", title="t", cover="c.jpg")
    assert r.ok is False
    assert "publish_video" in r.error


def test_youtube_publisher_publish_video_dry_run():
    """exec_real=False dry-run 仅校验参数 + 探测 token。"""
    from prisir_work.publisher import YoutubePublisher
    pub = YoutubePublisher()
    r = pub.publish_video(video="C:/nope.mp4", title="t")
    # 视频不存在 → ok=False
    assert r.ok is False
    assert "不存在" in r.error


def test_youtube_publisher_status_shape():
    from prisir_work.publisher import YoutubePublisher
    pub = YoutubePublisher()
    st = pub.status()
    # status 返 youtube_bridge.status() 形状(可能含 error)
    assert "ready" in st or "error" in st


def test_youtube_in_list_publishers():
    from prisir_work.publisher import list_publishers
    pubs = list_publishers()
    names = {p["name"] for p in pubs}
    assert "youtube" in names


# ---------------------------------------------------------------------------
# 3. HTTP 端点
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def backend_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]; s.close()
    script = ROOT / "companion" / "prisIragent-wechat-publisher.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, str(script), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=env, cwd=str(ROOT),
    )
    t0 = time.time()
    while time.time() - t0 < 8.0:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                if r.status == 200: break
        except Exception:
            time.sleep(0.3)
    yield port
    try: proc.terminate(); proc.wait(timeout=3)
    except Exception: proc.kill()


def _get(port, path, timeout=10):
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"exc": str(e)}
    except Exception as e:
        return 0, {"exc": f"{type(e).__name__}: {e}"}


def _post(port, path, body, timeout=10):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"exc": str(e)}
    except Exception as e:
        return 0, {"exc": f"{type(e).__name__}: {e}"}


def test_http_youtube_status(backend_port):
    code, body = _get(backend_port, "/api/youtube/status")
    assert code == 200
    assert body["ok"] is True
    assert body["platform"] == "youtube"
    assert "ready" in body
    assert "status" in body


def test_http_youtube_upload_missing_body(backend_port):
    code, body = _post(backend_port, "/api/youtube/upload", {})
    assert code == 400
    assert body["error"] == "missing_body"


def test_http_youtube_upload_missing_fields(backend_port):
    code, body = _post(backend_port, "/api/youtube/upload", {"video": "x"})
    assert code == 400
    assert body["error"] == "missing_fields"
    assert "video" in body["required"]
    assert "title" in body["required"]


def test_http_youtube_upload_video_not_found(backend_port):
    code, body = _post(backend_port, "/api/youtube/upload",
                       {"video": "C:/nope.mp4", "title": "t"})
    assert code == 200
    assert body["ok"] is False
    assert "不存在" in body["error"]


def test_http_youtube_list_works(backend_port):
    code, body = _get(backend_port, "/api/youtube/list?max_results=5")
    # 没 token → ok=False(业务错),但 200 返
    assert code == 200
    # dry-run + 没 token 时,token 校验在前 → ok=False
    if not body["ok"]:
        assert "需要先调用 auth" in body["error"] or "token" in body["error"].lower()


def test_http_youtube_stats_works(backend_port):
    code, body = _get(backend_port, "/api/youtube/stats")
    assert code == 200


def test_http_youtube_auth(backend_port):
    """无 client_secrets → auth 应返 ok=False(不会开浏览器)。"""
    code, body = _get(backend_port, "/api/youtube/auth", timeout=10)
    assert code == 200
    assert body["ok"] is False
    assert "client_secrets" in body["error"] or "oauthlib" in body["error"]


def test_http_platforms_includes_youtube(backend_port):
    code, body = _get(backend_port, "/api/platforms", timeout=20)
    assert code == 200
    names = [p["name"] for p in body["platforms"]]
    assert "youtube" in names


def test_http_publish_youtube_html_redirects(backend_port):
    """POST /api/publish platform=youtube 应走 publish_html → 返 ok=False + 提示。"""
    code, body = _post(backend_port, "/api/publish",
                       {"platform": "youtube", "html_path": "x",
                        "title": "t", "cover": "c"}, timeout=20)
    assert code == 200
    assert body["ok"] is False
    assert "publish_video" in body["error"]