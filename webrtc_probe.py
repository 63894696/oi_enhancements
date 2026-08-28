"""webrtc_probe.py — SecureDM 通话信令嗅探功能块(L2,面向 agent 开发)

定位:把"抓 WebRTC offer/answer 的 SDP 方向"这件事从"真机手动点 + 肉眼看 console"
变成 agent 可调用的功能块。这正是 securedm-webrtc-dev-status 排查方向 #1 缺失的工具。

干什么:
  以**被动第三者**身份 join 一个通话房间(默认 call-bob__oiagent),静默监听
  服务器转发的 offer/answer 信令,解析每个 m-line 的媒体方向
  (a=sendrecv/sendonly/recvonly/inactive)与 mid,输出**结构化 JSON 断言**。

失败可诊断(架构原则 #2):
  - 连不上 WS → ok=false, error 含阶段(connect/join/timeout)与线索
  - join 未授权 → ok=false, error=unauthorized(查 token)
  - 超时没抓到 answer → ok=true 但 answers=0, hint 提示"房间当前无通话,需在通话进行中跑"

用法:
  python webrtc_probe.py                          # 抓 call-bob__oiagent,等 30s
  python webrtc_probe.py --room call-bob__oiagent --wait 45 --json
  python webrtc_probe.py --assert-answer-video sendrecv   # 断言应答方 video 方向

退出码: 0=抓到并完成断言(或无断言仅抓取成功); 2=连接/授权失败; 3=断言失败。

依赖: websockets (ws_signal_server 同款)。仅监听,不发任何 signal,不干扰通话。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

try:
    import websockets
except ImportError:  # noqa: BLE001
    print(json.dumps({"ok": False, "stage": "import",
                      "error": "websockets 未安装", "hint": "pip install websockets"},
                     ensure_ascii=False))
    sys.exit(2)

DEFAULT_URL = os.environ.get("WS_SIGNAL_URL", "wss://signal.dreamproject.qzz.io/ws")
_TOKEN_FILE = Path.home() / ".local" / "share" / "aureon" / "l4_token"

# m-line: m=<kind> ... ; 方向属性 a=sendrecv/sendonly/recvonly/inactive
_M_LINE = re.compile(r"^m=(audio|video|application)\b", re.M)
_DIR = re.compile(r"^a=(sendrecv|sendonly|recvonly|inactive)\s*$", re.M)
_MID = re.compile(r"^a=mid:(\S+)\s*$", re.M)


def _token() -> str:
    try:
        if _TOKEN_FILE.exists():
            return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("L4_TOKEN", "")


def parse_sdp_mlines(sdp: str) -> list[dict]:
    """把 SDP 拆成每个 m-section 的方向与 mid。

    返回 [{kind, mid, direction}],direction 缺省视为 sendrecv(RFC3264 默认)。
    """
    lines = sdp.replace("\r\n", "\n").split("\n")
    sections: list[dict] = []
    cur: dict | None = None
    for ln in lines:
        m = _M_LINE.match(ln)
        if m:
            cur = {"kind": m.group(1), "mid": None, "direction": None}
            sections.append(cur)
            continue
        if cur is None:
            continue
        d = _DIR.match(ln)
        if d and cur["direction"] is None:  # m-section 级方向(取第一个,会话级在前被跳过因 cur=None 处理)
            cur["direction"] = d.group(1)
        md = _MID.match(ln)
        if md and cur["mid"] is None:
            cur["mid"] = md.group(1)
    for s in sections:
        if s["direction"] is None:
            s["direction"] = "sendrecv"  # RFC 默认
    return sections


def summarize_mlines(sections: list[dict]) -> dict:
    """压成 {audio: dir, video: dir} 便于断言。"""
    out = {}
    for s in sections:
        if s["kind"] in ("audio", "video"):
            out[s["kind"]] = s["direction"]
    return out


async def probe(url: str, room: str, wait: float, peer_id: str) -> dict:
    token = _token()
    if not token:
        return {"ok": False, "stage": "token", "error": "无 l4_token",
                "hint": f"检查 {_TOKEN_FILE} 或设 L4_TOKEN"}
    result = {
        "ok": True, "room": room, "url": url,
        "offers": [], "answers": [], "others": [],
        "answer_mlines": None, "answer_dirs": None,
        "offer_mlines": None, "offer_dirs": None,
    }
    got_answer = asyncio.Event()
    stop = asyncio.Event()

    async def _run() -> None:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20,
                                          max_size=4 * 1024 * 1024) as ws:
                await ws.send(json.dumps(
                    {"type": "join", "room": room, "peer": peer_id, "token": token},
                    ensure_ascii=False))
                # 等 joined / error
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=wait)
                    except asyncio.TimeoutError:
                        result.setdefault("hint", f"{wait}s 内未抓到足够信令;房间当前可能无活跃通话")
                        stop.set()
                        break
                    try:
                        msg = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        continue
                    mt = msg.get("type")
                    if mt == "error":
                        result["ok"] = False
                        result["stage"] = "join"
                        result["error"] = msg.get("error", "unknown")
                        result["hint"] = ("unauthorized → token 不符" if msg.get("error") == "unauthorized"
                                          else "检查 room/peer")
                        stop.set()
                        break
                    if mt == "joined":
                        result["joined"] = True
                        result["peers_in_room"] = msg.get("peers", [])
                        continue
                    if mt == "signal":
                        data = msg.get("data", {}) or {}
                        st = data.get("type")
                        sdp = data.get("sdp", "") or ""
                        entry = {"from": msg.get("from"), "kind": data.get("kind"),
                                 "video": data.get("video")}
                        if st == "offer" and sdp.startswith("v="):
                            secs = parse_sdp_mlines(sdp)
                            entry["mlines"] = secs
                            result["offers"].append(entry)
                            result["offer_mlines"] = secs
                            result["offer_dirs"] = summarize_mlines(secs)
                        elif st == "answer" and sdp.startswith("v="):
                            secs = parse_sdp_mlines(sdp)
                            entry["mlines"] = secs
                            result["answers"].append(entry)
                            result["answer_mlines"] = secs
                            result["answer_dirs"] = summarize_mlines(secs)
                            got_answer.set()
                        else:
                            result["others"].append({"from": msg.get("from"), "type": st})
        except OSError as e:  # 连接失败
            result["ok"] = False
            result["stage"] = "connect"
            result["error"] = str(e)
            result["hint"] = "连不上 WS;查域名 DNS 是否直连(不走 CF 代理)/VPS 防火墙 443/nginx wss"
        except asyncio.CancelledError:
            raise  # 正常取消,往外抛由调用方吞掉
        except Exception as e:  # noqa: BLE001
            result["ok"] = False
            result["stage"] = "runtime"
            result["error"] = f"{type(e).__name__}: {e}"

    task = asyncio.create_task(_run())
    try:
        # 抓到 answer 后再宽限 2s 收 ICE/后续,或直到 wait 超时
        while not stop.is_set():
            try:
                await asyncio.wait_for(got_answer.wait(), timeout=wait)
                await asyncio.sleep(2.0)  # 宽限收后续
                stop.set()
                break
            except asyncio.TimeoutError:
                stop.set()
                break
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="SecureDM 通话 SDP 方向嗅探功能块")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--room", default="call-bob__oiagent")
    ap.add_argument("--wait", type=float, default=30.0, help="监听秒数")
    ap.add_argument("--peer", default="probe", help="probe 在房间里的 peer 名")
    ap.add_argument("--json", action="store_true", help="只输出 JSON(默认也输出)")
    ap.add_argument("--assert-answer-video", choices=["sendrecv", "sendonly", "recvonly", "inactive"],
                    help="断言应答方 video 方向;不符则退出码 3")
    ap.add_argument("--assert-answer-audio", choices=["sendrecv", "sendonly", "recvonly", "inactive"],
                    help="断言应答方 audio 方向")
    args = ap.parse_args()

    res = asyncio.run(probe(args.url, args.room, args.wait, args.peer))

    # 断言
    failures = []
    for kind, want in (("video", args.assert_answer_video), ("audio", args.assert_answer_audio)):
        if not want:
            continue
        dirs = res.get("answer_dirs") or {}
        got = dirs.get(kind)
        if got is None:
            failures.append(f"未抓到 answer 的 {kind} m-line(answer 数={len(res.get('answers', []))})")
        elif got != want:
            failures.append(f"answer {kind} 方向={got},期望={want}")
    if failures:
        res["ok"] = False
        res["assert_failures"] = failures

    print(json.dumps(res, ensure_ascii=False, indent=2))

    if not res.get("ok") and res.get("stage") in ("connect", "token", "join", "runtime", "import"):
        return 2
    if failures:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
