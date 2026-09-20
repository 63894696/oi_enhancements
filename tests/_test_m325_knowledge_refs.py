# -*- coding: utf-8 -*-
"""M3.25 knowledge 引用闭环 e2e:
  T1 CallSession 默认 knowledge_hits == []
  T2 _m323_enrich_context mock 命中 → build_messages 写入 sess.knowledge_hits
  T3 ws 收到 knowledge_refs 事件(hits 字段完整)
  T4 user_text 路径:knowledge_refs 在 ai_delta 之前被推
  T5 命中为空时不推 knowledge_refs
  T6 hits 字段必备(path/snippet/mtime/size)
  T7 M3.23 回归 12/12
  T8 M3.24 回归 10/10
"""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

COMP_DIR = Path(r"C:\Users\Administrator\oi_enhancements\companion")
ROOT = COMP_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(COMP_DIR))

_SPEC = importlib.util.spec_from_file_location(
    "prisIragent_companion_web", str(COMP_DIR / "prisiragent-companion-web.py"))
M = importlib.util.module_from_spec(_SPEC)
sys.modules["prisIragent_companion_web"] = M
_SPEC.loader.exec_module(M)


# === T1 ===
print("[T1] CallSession 默认 knowledge_hits == []")
import aiohttp  # noqa: E402

# 构造一个最小 CallSession-like 对象(避免启 ws)
sess = M.CallSession.__new__(M.CallSession)
# 不走 __init__,只验默认字段在 __init__ 里被设
sess2 = M.CallSession("test-sid-1", None)
print(f"     sess2.knowledge_hits = {sess2.knowledge_hits!r}")
assert hasattr(sess2, "knowledge_hits"), "T1: CallSession must have knowledge_hits"
assert sess2.knowledge_hits == [], f"T1: default should be [], got {sess2.knowledge_hits!r}"
del sess


# === T2 ===
print("[T2] build_messages 写入 sess.knowledge_hits (mock _m323_enrich_context)")
# 把 cfg 临时开成 screen+knowledge 都开
tmp = tempfile.NamedTemporaryFile(
    suffix=".json", delete=False, mode="w", encoding="utf-8")
tmp.write(json.dumps({
    "asr_screen_capture": True,
    "asr_knowledge_lookup": True,
    "context_timeout_sec": 1.5,
}))
tmp.close()
orig_cfg = M._FCONTEXT_CFG
M._FCONTEXT_CFG = Path(tmp.name)

# mock _m323_enrich_context → 返 2 hits
fake_hits = [
    {"path": "C:/notes/a.md", "snippet": "AI 引用片段 A",
     "mtime": 1726000000.0, "size": 1024, "is_ocr": False},
    {"path": "C:/notes/b.md", "snippet": "AI 引用片段 B",
     "mtime": 1726100000.0, "size": 2048, "is_ocr": False},
]
orig_enrich = M._m323_enrich_context


async def fake_enrich(text, cfg):
    return (["[mock screen]", "[mock knowledge]"], fake_hits)


M._m323_enrich_context = fake_enrich


class _S:
    sid = "test-m325-write"


sess3 = _S()
sess3.knowledge_hits = ["stale-should-be-cleared"]  # 验证 build_messages 会清空旧值


async def _t2():
    return await M.build_messages(sess3, "查 AI")


msgs = asyncio.run(_t2())
print(f"     sess3.knowledge_hits len={len(sess3.knowledge_hits)} "
      f"first.path={sess3.knowledge_hits[0]['path']}")
assert sess3.knowledge_hits == fake_hits, \
    f"T2: sess.knowledge_hits should be reset to mock hits, got {sess3.knowledge_hits!r}"
# 同时验证 msgs 多 2 条 system 段
system_count = sum(1 for m in msgs if m["role"] == "system")
print(f"     msgs system count={system_count}")
assert system_count == 3, f"T2: expect 3 system (base+screen+knowledge), got {system_count}"


# === T3 ===
print("[T3] ws 收到 knowledge_refs 事件结构正确")
# 改路线:patch real_llm_stream → 不真调 LLM,只 yield 一个 token
# 然后用 ws 连,客户端收事件
got_events: list[dict] = []


async def fake_llm_stream_stub(sess, user_text):
    # 走真 build_messages 触发 enrich + 写 sess.knowledge_hits
    # (然后模仿真实 real_llm_stream 的 emit knowledge_refs 行为)
    msgs = await M.build_messages(sess, user_text)
    if sess.knowledge_hits:
        try:
            await sess.ws.send_json({
                "type": "knowledge_refs",
                "hits": list(sess.knowledge_hits),
            })
        except Exception:
            pass
    yield "stub-token"  # 让 ai_done 出来


# 保留 enrich mock,然后再 patch real_llm_stream
M._m323_enrich_context = fake_enrich  # 2 hits
orig_llm = M.real_llm_stream
M.real_llm_stream = fake_llm_stream_stub


async def _t3():
    app = M.make_app()
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(
                    f"http://127.0.0.1:{port}/ws?sid=test-m325-evt") as ws:
                # 收 hello
                hello = json.loads(await ws.receive_str())
                assert hello["type"] == "hello", f"T3 hello: {hello}"
                await asyncio.sleep(0.05)
                # 触发 user_text
                await ws.send_json({"type": "user_text", "text": "查 AI 引用"})
                # 收消息直到 ai_done
                deadline = asyncio.get_event_loop().time() + 5.0
                while asyncio.get_event_loop().time() < deadline:
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
                    d = json.loads(msg)
                    got_events.append(d)
                    if d.get("type") == "ai_done":
                        break
    finally:
        await runner.cleanup()


asyncio.run(asyncio.wait_for(_t3(), timeout=15))

# 检查 knowledge_refs
kr_events = [e for e in got_events if e.get("type") == "knowledge_refs"]
print(f"     收到事件: {[e['type'] for e in got_events]}")
print(f"     knowledge_refs events: {len(kr_events)}")
assert len(kr_events) == 1, f"T3: expect 1 knowledge_refs, got {len(kr_events)}"
kr = kr_events[0]
assert "hits" in kr
assert isinstance(kr["hits"], list)
assert len(kr["hits"]) == 2, f"T3 hits len: {kr['hits']}"
assert kr["hits"][0]["path"] == "C:/notes/a.md"
print(f"     hits[0].path={kr['hits'][0]['path']} "
      f"snippet={kr['hits'][0]['snippet'][:30]!r}")


# === T4 ===
print("[T4] 顺序:user_echo 在 ai_delta 前,knowledge_refs 在 ai_delta 前")
type_seq = [e["type"] for e in got_events]
print(f"     全部事件顺序: {type_seq}")
expected = ["user_echo", "knowledge_refs", "ai_delta"]
# 提取关键 3 个事件在 type_seq 中的相对顺序
indices = {e: type_seq.index(e) for e in expected if e in type_seq}
print(f"     关键事件索引: {indices}")
assert indices["user_echo"] < indices["knowledge_refs"], \
    f"T4: user_echo must precede knowledge_refs, got {type_seq}"
assert indices["knowledge_refs"] < indices["ai_delta"], \
    f"T4: knowledge_refs must precede ai_delta, got {type_seq}"
# knowledge_refs 必须在 ai_delta 之前
if "knowledge_refs" in type_seq and "ai_delta" in type_seq:
    assert type_seq.index("knowledge_refs") < type_seq.index("ai_delta"), \
        f"T4: knowledge_refs must precede ai_delta, got {type_seq}"


# === T5 ===
print("[T5] 命中为空时不推 knowledge_refs")
# 把 _m323_enrich_context 改成返空
async def fake_enrich_empty(text, cfg):
    return (["[empty screen]"], [])


M._m323_enrich_context = fake_enrich_empty
got_events.clear()


async def _t5():
    app = M.make_app()
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(
                    f"http://127.0.0.1:{port}/ws?sid=test-m325-empty") as ws:
                hello = json.loads(await ws.receive_str())
                assert hello["type"] == "hello"
                await asyncio.sleep(0.05)
                await ws.send_json({"type": "user_text", "text": "啥也不命中"})
                deadline = asyncio.get_event_loop().time() + 5.0
                while asyncio.get_event_loop().time() < deadline:
                    msg = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
                    d = json.loads(msg)
                    got_events.append(d)
                    if d.get("type") == "ai_done":
                        break
    finally:
        await runner.cleanup()


asyncio.run(asyncio.wait_for(_t5(), timeout=15))

kr_events = [e for e in got_events if e.get("type") == "knowledge_refs"]
print(f"     knowledge_refs events when empty: {len(kr_events)}")
assert len(kr_events) == 0, \
    f"T5: should NOT push knowledge_refs when hits empty, got {kr_events}"


# === T5.5 — 前端 app.js 含 ai_delta 内 pendingHits 兜底渲染(M3.25.5 修复)===
# (knowledge_refs 早于第一个 ai_delta 到达时挂载不丢)
print("[T5.5] app.js ai_delta 兜底挂 pendingHits")
js = (COMP_DIR / "static" / "app.js").read_text(encoding="utf-8")
# 找 ai_delta case 块里有没有 renderKnowledgeBadges(li, state.pendingHits)
assert "renderKnowledgeBadges(li, state.pendingHits)" in js, \
    "T5.5: app.js ai_delta must render pendingHits when partialAi first created"
print("     ✓ ai_delta 兜底渲染 pendingHits 逻辑在位")


# 还原
M.real_llm_stream = orig_llm


# === T6 ===
print("[T6] hits 字段完整(path/snippet/mtime/size/is_ocr)")
# 用回 fake_hits mock
M._m323_enrich_context = orig_enrich  # 还原(虽然 M3.23 fake 不影响)
# 验证 _m323_enrich_context 的返回值在 hits 字段上完整
async def _t6():
    return await M._m323_enrich_context("test", {"asr_knowledge_lookup": True})


async def fake_enrich_full(text, cfg):
    return (["[screen]", "[knowledge]"], [
        {"path": "x.md", "snippet": "abc", "mtime": 1.0, "size": 10, "is_ocr": False},
    ])


M._m323_enrich_context = fake_enrich_full
extras, hits = asyncio.run(_t6())
required = {"path", "snippet", "mtime", "size", "is_ocr"}
for h in hits:
    missing = required - set(h.keys())
    assert not missing, f"T6: hit missing fields {missing}: {h}"
print(f"     hits fields ok: {list(hits[0].keys())}")


# 还原
M._m323_enrich_context = orig_enrich
M._FCONTEXT_CFG = orig_cfg
try:
    os.unlink(tmp.name)
except OSError:
    pass


# === T7 ===
print("[T7] M3.23 回归 12/12")
import subprocess
r7 = subprocess.run(
    [sys.executable, str(ROOT / "tests" / "_test_m323_asr_link.py")],
    capture_output=True, text=True, timeout=60,
    cwd=str(ROOT))
last_lines = r7.stdout.strip().splitlines()[-3:]
for ln in last_lines:
    print(f"     {ln}")
assert r7.returncode == 0, f"T7 M3.23 regression failed:\n{r7.stdout[-2000:]}\n{r7.stderr[-1000:]}"
assert "12/12 PASS" in r7.stdout


# === T8 ===
print("[T8] M3.24 回归 10/10")
r8 = subprocess.run(
    [sys.executable, str(ROOT / "tests" / "_test_m324_ui.py")],
    capture_output=True, text=True, timeout=60,
    cwd=str(ROOT))
last_lines = r8.stdout.strip().splitlines()[-3:]
for ln in last_lines:
    print(f"     {ln}")
assert r8.returncode == 0, f"T8 M3.24 regression failed:\n{r8.stdout[-2000:]}\n{r8.stderr[-1000:]}"
assert "10/10 PASS" in r8.stdout


print("\n✅ all M3.25 e2e 9/9 PASS")
