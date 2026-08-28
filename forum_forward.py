"""forum_forward.py — 生产 relay → 本机盯梢镜像 单向转发(F-3 补,2026-08-22)

为什么需要:生产(127.0.0.1:18812)与盯梢镜像(18813)是两个独立 relay 实例、state 独立。
镜像存在的唯一意义是「本机盯梢第一时间看到用户反馈」——所以生产侧收到帖,镜像必须跟着有。
转发器在 VPS 内部跑:WS 读生产(hello → read 全量补历史 → 订阅广播),把每条已验签帖
原样转发进镜像(post 帧)。镜像 relay 重走 validate_post(验签/PoW/post_id),等同生产口径。

只转发 post/reply(takedown/retract 也转,镜像同步标记)。确认计数由镜像 relay 自己算,
与生产天然一致(同一份创世根环境变量)。断线指数退避重连,重连后 read since_seq 补漏。
"""
import asyncio
import json
import os
import sys

import websockets

PROD = os.environ.get("FORWARD_FROM", "ws://127.0.0.1:18812")
MIRROR = os.environ.get("FORWARD_TO", "ws://127.0.0.1:18813")
# 镜像 relay 有「1帖/2s」硬间隔限流;批量补历史时每帖间隔 2.1s 才不会吃 nack。
BACKFILL_GAP = float(os.environ.get("FORWARD_BACKFILL_GAP", "2.1"))


def log(msg):
    print(f"[forward] {msg}", flush=True)


async def mirror_send(post, wait_gap=False):
    """把一条已验签 post 原样塞进镜像。镜像自己验签,失败只是被 nack,不影响转发器。
    返回 True=ack,False=nack/异常。wait_gap=True 时发完睡 BACKFILL_GAP(批量补历史用)。"""
    ok = False
    try:
        async with websockets.connect(MIRROR, max_size=128 * 1024) as m:
            await m.send(json.dumps({"type": "post", "post": post}, ensure_ascii=False))
            try:
                raw = await asyncio.wait_for(m.recv(), timeout=8)
                resp = json.loads(raw)
                ok = resp.get("type") == "ack"
                if not ok:
                    log(f"  nack {resp.get('reason')} kind={post.get('kind')} fp={post.get('author_fp','')[:6]}")
            except Exception as e:
                log(f"  mirror recv 超时: {e}")
    except Exception as e:
        log(f"  mirror send 失败: {e}")
    if wait_gap:
        await asyncio.sleep(BACKFILL_GAP)
    return ok


async def run():
    backoff = 2
    since = 0
    while True:
        try:
            log(f"连生产 {PROD} (since_seq={since})")
            async with websockets.connect(PROD, max_size=128 * 1024) as ws:
                backoff = 2
                await ws.send(json.dumps({"type": "hello"}))
                await ws.recv()  # welcome
                await ws.send(json.dumps({"type": "read", "since_seq": since, "include_takedown": True}))
                # ① 先 drain 到 history 帧(期间可能夹着广播 post,缓存起来补完历史再按序处理)
                pending = []
                history = None
                async for raw in ws:
                    try:
                        m = json.loads(raw)
                    except Exception:
                        continue
                    if m.get("type") == "history":
                        history = m
                        break
                    pending.append(m)  # 补历史期间到达的实时帧,先存
                # ② 批量补历史(镜像 1帖/2s 限流,逐帖间隔)
                msgs = history.get("msgs", [])
                done = 0
                for rec in msgs:
                    p = rec.get("post")
                    if p and p.get("kind") in ("post", "reply"):
                        if await mirror_send(p, wait_gap=True):
                            done += 1
                    s = rec.get("seq", 0)
                    if s > since:
                        since = s
                log(f"历史补齐至 seq={since}(共 {len(msgs)} 条,成功入镜像 {done});缓存实时帧 {len(pending)} 条")
                # ③ 处理缓存的实时帧 + 进入持续流
                queue = pending
                while True:
                    if queue:
                        m = queue.pop(0)
                    else:
                        try:
                            m = json.loads(await ws.recv())
                        except Exception:
                            continue
                    t = m.get("type")
                    if t == "post":
                        p = m.get("post")
                        if p and p.get("kind") in ("post", "reply") and m.get("seq", 0) > since - 1000:
                            await mirror_send(p)
                            log(f"实时转发 seq={m.get('seq')} board={p.get('board')} fp={p.get('author_fp','')[:6]}")
                        s = m.get("seq", 0)
                        if s > since:
                            since = s
                    elif t in ("takedown", "retract"):
                        try:
                            async with websockets.connect(MIRROR, max_size=128 * 1024) as mm:
                                await mm.send(json.dumps(m, ensure_ascii=False))
                                await asyncio.wait_for(mm.recv(), timeout=8)
                        except Exception:
                            pass
        except Exception as e:
            log(f"生产连接断开: {e};{backoff}s 后重连")
            await asyncio.sleep(backoff)
            backoff = min(30, backoff * 2)


if __name__ == "__main__":
    asyncio.run(run())
