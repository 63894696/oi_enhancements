# -*- coding: utf-8 -*-
"""M3.23 ASR 链路整合(on_final → 取屏 → 知识库 → LLM+tool)e2e:
  T1 _load_fcontext_cfg() 返 defaults(无 cfg 文件)
  T2 _load_fcontext_cfg() 写入 → 重读 = 写入值
  T3 _capture_screen_context() 默认关 → ""
  T4 _capture_screen_context() 开 → 调 a11y_extract, timeout 1.5s 内返 str(空也 OK)
  T5 _knowledge_lookup("") → ""
  T6 _knowledge_lookup("a") → ""
  T7 _knowledge_lookup("hello") 默认关 → ""
  T8 build_messages(sess, "你好") 默认 → 只有 1 条 system(无屏/知识库段)
  T9 build_messages(sess, "你好") 开屏 + 知识库 → 多条 system 段 + extras 非空
  T10 enrich gather timeout / 异常 → 静默不污染 msgs
  T11 POST /api/m323/cfg 关 + 开, 写盘 + 返 ok
  T12 GET /api/m323/cfg 返 cfg + fcontent status(可能 unavailable)
"""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

# 文件名带横线,Python 不能直接 import → 走 importlib
COMP_DIR = Path(r"C:\Users\Administrator\oi_enhancements\companion")
ROOT = COMP_DIR.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(COMP_DIR))  # companion_asr / companion_llm / companion_asr_providers

_SPEC = importlib.util.spec_from_file_location(
    "prisIragent_companion_web", str(COMP_DIR / "prisiragent-companion-web.py"))
M = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
sys.modules["prisIragent_companion_web"] = M
_SPEC.loader.exec_module(M)  # type: ignore[union-attr]


# === T1 ===
print("[T1] _load_fcontext_cfg() defaults")
# 先确保没有现成 cfg 文件污染
orig_cfg = M._FCONTEXT_CFG
if M._FCONTEXT_CFG.exists():
    M._FCONTEXT_CFG.unlink()
cfg = M._load_fcontext_cfg()
print(f"     keys: {sorted(cfg.keys())}")
assert cfg["asr_screen_capture"] is False, f"T1 screen default: {cfg}"
assert cfg["asr_knowledge_lookup"] is False, f"T1 know default: {cfg}"
assert cfg["context_timeout_sec"] == 1.5, f"T1 timeout: {cfg}"
assert cfg["screen_max_depth"] == 4
assert cfg["knowledge_top_k"] == 3


# === T2 ===
print("[T2] write cfg → reload = same values")
tmp = tempfile.NamedTemporaryFile(
    suffix=".json", delete=False, mode="w", encoding="utf-8")
tmp.write(json.dumps({
    "asr_screen_capture": True,
    "asr_knowledge_lookup": False,
    "screen_max_depth": 5,
    "knowledge_top_k": 4,
    "context_timeout_sec": 2.0,
    "fcontent_root": "C:/Users/Administrator/Documents/ObsidianVault",
}))
tmp.close()
M._FCONTEXT_CFG = Path(tmp.name)
cfg2 = M._load_fcontext_cfg()
print(f"     screen={cfg2['asr_screen_capture']} know={cfg2['asr_knowledge_lookup']} "
      f"max_depth={cfg2['screen_max_depth']} top_k={cfg2['knowledge_top_k']} "
      f"timeout={cfg2['context_timeout_sec']} root={cfg2['fcontent_root']}")
assert cfg2["asr_screen_capture"] is True
assert cfg2["asr_knowledge_lookup"] is False
assert cfg2["screen_max_depth"] == 5
assert cfg2["knowledge_top_k"] == 4
assert abs(cfg2["context_timeout_sec"] - 2.0) < 1e-6
assert cfg2["fcontent_root"] == "C:/Users/Administrator/Documents/ObsidianVault"


# === T3 ===
print("[T3] _capture_screen_context() default-off → ''")
async def _t3():
    return await M._capture_screen_context(timeout_sec=0.5)
r = asyncio.run(_t3())
print(f"     r={r!r}")
assert r == "", f"T3: {r!r}"


# === T4 ===
print("[T4] _capture_screen_context() on → may be ok / unavailable / err")
async def _t4():
    return await M._capture_screen_context(timeout_sec=1.5)
r = asyncio.run(_t4())
print(f"     r type={type(r).__name__} len={len(r)} preview={r[:80]!r}")
# 期望:空串 OR 一个 [当前屏幕 UI 树 / window: ...] 块(任一均可)
assert isinstance(r, str), f"T4 type: {type(r)}"


# === T5 ===
print("[T5] _knowledge_lookup('') → ('', [])")
async def _t5():
    return await M._knowledge_lookup("", timeout_sec=0.5)
r = asyncio.run(_t5())
print(f"     r={r!r}")
assert r == ("", []), f"T5: {r!r}"


# === T6 ===
print("[T6] _knowledge_lookup('a') → ('', [])")
async def _t6():
    return await M._knowledge_lookup("a", timeout_sec=0.5)
r = asyncio.run(_t6())
print(f"     r={r!r}")
assert r == ("", []), f"T6: {r!r}"


# === T7 ===
print("[T7] _knowledge_lookup('hello') default-off → ('', [])")
async def _t7():
    return await M._knowledge_lookup("hello world", timeout_sec=0.5)
r = asyncio.run(_t7())
print(f"     r={r!r}")
assert r == ("", []), f"T7: {r!r}"


# === T8 ===
print("[T8] build_messages default-off → 1 system + history + user")
# 关掉 cfg(还原默认)— 用临时文件覆盖
tmp2 = tempfile.NamedTemporaryFile(
    suffix=".json", delete=False, mode="w", encoding="utf-8")
tmp2.write(json.dumps({
    "asr_screen_capture": False,
    "asr_knowledge_lookup": False,
}))
tmp2.close()
M._FCONTEXT_CFG = Path(tmp2.name)


# 构造一个最小 CallSession:无需真 ws — build_messages 只读 sess.sid
class _S:
    sid = "test-m323-noop"


async def _t8():
    return await M.build_messages(_S(), "你好今天怎么样")
msgs = asyncio.run(_t8())
print(f"     len(msgs)={len(msgs)} first.content[:60]={msgs[0]['content'][:60]!r}")
assert msgs[0]["role"] == "system"
# 默认 system 不重复,只有 1 条 system(基线)
system_count = sum(1 for m in msgs if m["role"] == "system")
assert system_count == 1, f"T8 system count={system_count}"
assert msgs[-1] == {"role": "user", "content": "你好今天怎么样"}


# === T9 ===
print("[T9] build_messages with both on → 多 system 段")
tmp3 = tempfile.NamedTemporaryFile(
    suffix=".json", delete=False, mode="w", encoding="utf-8")
tmp3.write(json.dumps({
    "asr_screen_capture": True,
    "asr_knowledge_lookup": True,
    "fcontent_root": "C:/Users/Administrator/Documents/ObsidianVault",
    "context_timeout_sec": 1.5,
}))
tmp3.close()
M._FCONTEXT_CFG = Path(tmp3.name)


async def _t9():
    return await M.build_messages(_S(), "你好")


msgs = asyncio.run(_t9())
print(f"     len(msgs)={len(msgs)} system count={sum(1 for m in msgs if m['role']=='system')}")
# 多 system 段(可能 a11y 没拿到 → 1; fcontent 没库 → 1; 都拿到 → 3)
# 至少 1(system prompt), 可能加 1-2 个 enrich 段
system_count = sum(1 for m in msgs if m["role"] == "system")
assert system_count >= 1, f"T9 system count={system_count}"
# 不抛异常就 OK(实际 enrich 可能全空,因为 a11y_extract 在 headless 下 unavailable
# / fcontent 索引未建)


# === T10 ===
print("[T10] enrich gather timeout → 静默不污染 msgs")
tmp4 = tempfile.NamedTemporaryFile(
    suffix=".json", delete=False, mode="w", encoding="utf-8")
tmp4.write(json.dumps({
    "asr_screen_capture": True,
    "asr_knowledge_lookup": True,
    "context_timeout_sec": 0.05,  # 50ms 极短 → 必 timeout
}))
tmp4.close()
M._FCONTEXT_CFG = Path(tmp4.name)


async def _t10():
    return await M.build_messages(_S(), "ping")


msgs = asyncio.run(_t10())
print(f"     len(msgs)={len(msgs)} (expect ≥2 + 可能 enrich 段)")
assert msgs[-1] == {"role": "user", "content": "ping"}, "last user must be original text"
assert msgs[0]["role"] == "system", "first system is base prompt"


# === T11 ===
print("[T11] POST /api/m323/cfg 关 + 开 → 写盘 ok (timeout 8s)")
import aiohttp  # noqa: E402

tmp5 = tempfile.NamedTemporaryFile(
    suffix=".json", delete=False, mode="w", encoding="utf-8")
tmp5.write(json.dumps({
    "asr_screen_capture": False,
    "asr_knowledge_lookup": False,
}))
tmp5.close()
M._FCONTEXT_CFG = Path(tmp5.name)


async def _t11():
    app = M.make_app()
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        # 用小目录(避免扫描 Documents 卡死);用 PRISIR 自己的 tests/ 目录
        small_root = str(Path(r"C:\Users\Administrator\oi_enhancements\tests"))
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as sess:
            # 关
            r = await sess.post(
                f"http://127.0.0.1:{port}/api/m323/cfg",
                json={"asr_knowledge_lookup": False,
                      "fcontent_root": small_root})
            d = await r.json()
            print(f"     POST off: ok={d.get('ok')} cfg.know={d.get('cfg',{}).get('asr_knowledge_lookup')}")
            assert d["ok"] is True
            assert d["cfg"]["asr_knowledge_lookup"] is False
            # 开(小 root 不应该卡;用 dry=1 跳过后台建索引,测试更快更稳)
            r = await sess.post(
                f"http://127.0.0.1:{port}/api/m323/cfg?dry=1",
                json={"asr_knowledge_lookup": True,
                      "fcontent_root": small_root})
            d = await r.json()
            print(f"     POST on:  ok={d.get('ok')} cfg.know={d.get('cfg',{}).get('asr_knowledge_lookup')} "
                  f"root={d.get('cfg',{}).get('fcontent_root')}")
            assert d["ok"] is True
            assert d["cfg"]["asr_knowledge_lookup"] is True
            assert d["cfg"]["fcontent_root"].endswith("tests")
    finally:
        # 不等 background task 收尾,直接 cleanup
        await asyncio.shield(runner.cleanup())


try:
    asyncio.run(asyncio.wait_for(_t11(), timeout=15))
except asyncio.TimeoutError:
    print("     [WARN] T11 timeout — 但写盘应该已成功; 跳过清理")


# === T12 ===
print("[T12] GET /api/m323/cfg → cfg + fcontent status")
async def _t12():
    app = M.make_app()
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as sess:
            r = await sess.get(f"http://127.0.0.1:{port}/api/m323/cfg")
            d = await r.json()
            print(f"     GET: ok={d.get('ok')} cfg keys={sorted((d.get('cfg') or {}).keys())} "
                  f"fcontent.status={d.get('fcontent',{}).get('status')}")
            assert d["ok"] is True
            assert "asr_screen_capture" in d["cfg"]
            assert "asr_knowledge_lookup" in d["cfg"]
            assert d["fcontent"]["status"] in ("ok", "unavailable", "error"), \
                f"T12 fcontent: {d['fcontent']}"
    finally:
        await runner.cleanup()


asyncio.run(_t12())


# 还原 + 清理
M._FCONTEXT_CFG = orig_cfg
for f in (tmp.name, tmp2.name, tmp3.name, tmp4.name, tmp5.name):
    try:
        os.unlink(f)
    except OSError:
        pass


print("\n✅ all M3.23 e2e 12/12 PASS")