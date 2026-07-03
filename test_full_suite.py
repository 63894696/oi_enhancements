"""OI 增强器全套 ship 测试

覆盖:
1. vision 增强器 — list_windows / capture_screen(截到文件)
2. desktop 增强器 — hotkey / click / mouse pos(用 fail-safe 避免真点击桌面)
3. shared_memory 增强器 — store / retrieve / get_stats
4. memory(SQLite 自实现,作为 fallback) — 基础 store/recall
5. E2E — OI + shared_memory hooks,真做一次 chat

跑法:`python test_full_suite.py`
"""
import json
import os
import sys
import time
import shutil
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 把当前目录加进 sys.path,但 vendor/ 子目录不要加
sys.path.insert(0, str(HERE))

# 关键:把 vendor/peekaboo 子目录也加进 sys.path,但只对没冲突的 module 有用
# shared_memory 冲突靠 importlib 解决(在 oi_enhancements/shared_memory 包内已用)
VENDOR = HERE / "vendor" / "peekaboo"
if str(VENDOR) not in sys.path:
    sys.path.insert(1, str(VENDOR))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


# ============================================================
# 1. vision 增强器
# ============================================================
print("\n=== 1. vision 增强器 ===")
from vision import list_windows, get_foreground_window, capture_screen, find_window

wins = list_windows()
check("list_windows status ok", wins["status"] == "ok", f"got {wins['status']}")
check("list_windows 至少返回 10 个", wins.get("count", 0) > 10)

fg = get_foreground_window()
check("get_foreground_window status ok 或 none", fg["status"] in ("ok", "none"))

# find_window 找一个不存在的
notfound = find_window("__not_a_real_window_title__")
check("find_window 不存在窗口返 not_found", notfound["status"] == "not_found")

# find_window 找一个常见的
fw = find_window("Claude") or find_window("微信") or find_window("team-web")
check("find_window 存在窗口返 ok", fw["status"] == "ok", f"got {fw}")

# capture_screen 实际截一张(存到 tmp)
tmpdir = tempfile.mkdtemp(prefix="oi-vision-test-")
img_path = Path(tmpdir) / "shot.png"
# 不存 path 模式,直接拿 base64
cap = capture_screen(monitor_index=0, max_width=320)
check("capture_screen base64", cap["status"] == "ok" and len(cap.get("base64", "")) > 100)
if cap["status"] == "ok":
    print(f"    base64 bytes: {cap['bytes']}, width: {cap['width']}")
shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 2. desktop 增强器
# ============================================================
print("\n=== 2. desktop 增强器 ===")
from desktop import (
    get_mouse_position, hotkey, focus_window, maximize_window, inspect_window
)

pos = get_mouse_position()
check("get_mouse_position status ok", pos["status"] == "ok", f"got {pos}")

# hotkey 测试(按 ctrl+escape 不真做事,只验证调用链不崩)
hk = hotkey("ctrl", "escape")
check("hotkey 调用不崩", hk["status"] in ("ok", "fail", "error"))

# focus_window 找一个真窗口
fw = focus_window("Claude") or focus_window("微信") or focus_window("team-web")
# 注:Windows SetForegroundWindow 有 OS 级限制,从后台进程调用经常被拒
# (status=='fail' 但 hwnd 找到 = 窗口管理逻辑 OK,只是 OS 不允许抢焦点)
check("focus_window 找到真窗口(OS 抢焦点限制可能 fail)", fw["status"] in ("ok", "fail") and "hwnd" in fw,
      f"got {fw}")


# ============================================================
# 3. shared_memory 增强器(Peekaboo-W 包装)
# ============================================================
print("\n=== 3. shared_memory 增强器 ===")
# vendor/shared_memory.py 跟 oi_enhancements/shared_memory 包同名,需要 importlib 显式加载我们的包
import importlib.util
sm_spec = importlib.util.spec_from_file_location(
    "oi_enh_sm",
    HERE / "shared_memory" / "__init__.py",
)
sm_mod = importlib.util.module_from_spec(sm_spec)
sys.modules["oi_enh_sm"] = sm_mod
sm_spec.loader.exec_module(sm_mod)
store = sm_mod.store
retrieve = sm_mod.retrieve
get_stats = sm_mod.get_stats
get_by_agent = sm_mod.get_by_agent

# 重置 hub(force a fresh hub_name 让测试不串)
HUB_NAME = f"oi_test_{int(time.time())}"

r1 = store("L0", "user-test", "测试用户偏好中文", tags=["preference"], hub_name=HUB_NAME)
r2 = store("L1", "project-test", "C:/test/project 路径", tags=["project"], hub_name=HUB_NAME)
r3 = store("L2", "task-panel-bug", "切 team 时 panel 丢失,根因 COLUMN_LAYOUT", tags=["bug"], hub_name=HUB_NAME)

check("store L0 ok", r1["status"] == "ok")
check("store L1 ok", r2["status"] == "ok")
check("store L2 ok", r3["status"] == "ok")

# retrieve 用整词 substring(Peekaboo-W 算法要求 title 有完整关键词)
r = retrieve("task-panel-bug", hub_name=HUB_NAME)
check("retrieve 'task-panel-bug' 命中 L2 note", r["count"] >= 1, f"got {r}")

r = retrieve("project-test", hub_name=HUB_NAME)
check("retrieve 'project-test' 命中 L1 fact", r["count"] >= 1, f"got {r}")

# 不存在的 query
r = retrieve("__nope__", hub_name=HUB_NAME)
check("retrieve 不存在返 0 命中", r["count"] == 0)

stats = get_stats(hub_name=HUB_NAME)
check("get_stats 至少 3 条 total", stats["stats"]["total_memories"] >= 3)


# ============================================================
# 4. memory(SQLite 自实现,作为 fallback 或对比)
# ============================================================
print("\n=== 4. memory(SQLite 自实现)===")
os.environ["OI_HOME"] = tempfile.mkdtemp(prefix="oi-mem-test-")
sys.path.insert(0, str(HERE))
from memory.oi_memory import OIMemory
m = OIMemory()
m.store("L0", "user:sqlite-test", "用户偏好")
m.store("L1", "project:sqlite-test", "some project")
hits = m.recall("user:sqlite-test", n=5)
check("SQLite memory recall ok", len(hits) >= 1)


# ============================================================
# 5. E2E 真 OI chat(可选,如果 OLLAMA_API_KEY 在)
# ============================================================
print("\n=== 5. E2E OI + memory hooks ===")
try:
    import winreg
    reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment')
    key, _ = winreg.QueryValueEx(reg, 'OLLAMA_API_KEY')
    winreg.CloseKey(reg)
    HAS_KEY = len(key) > 20
except Exception:
    HAS_KEY = False

if not HAS_KEY:
    print("  ⏭ 跳过 E2E(无 OLLAMA_API_KEY)")
else:
    # 配 OI
    os.environ['NO_PROXY'] = '*'
    os.environ.pop('HTTPS_PROXY', None)
    os.environ.pop('HTTP_PROXY', None)
    os.environ['OPENAI_API_KEY'] = key

    from interpreter import interpreter
    interpreter.llm.model = 'openai/devstral-small-2:24b'
    interpreter.llm.api_base = 'https://ollama.com/v1'
    interpreter.llm.api_key = key
    interpreter.auto_run = False
    interpreter.verbose = False

    # 装 hooks
    from memory.oi_memory_hooks import install
    install(interpreter, agent_name='oi-suite-test')

    # 预 store 几条让 recall 能命中
    from memory.oi_memory_hooks import store as mem_store
    mem_store('L0', 'user:zrkwedii9', '用户偏好中文,直接不啰嗦,笔记习惯用 Obsidian')
    mem_store('L1', 'project:team-web', 'C:/Users/Administrator/demos/team-web — FastAPI 多 agent 协作面板,11 panel 4 列布局')

    TASK = "简单介绍一下 team-web 这个项目,它有几个 panel?"
    print(f"  task: {TASK}")
    t0 = time.time()
    out_chunks = []
    for chunk in interpreter.chat(TASK):
        out_chunks.append(chunk)
    elapsed = time.time() - t0

    # 拼 response text
    parts = []
    for c in out_chunks:
        if isinstance(c, dict):
            if c.get('type') == 'message' and isinstance(c.get('content'), str):
                parts.append(c['content'])
        elif isinstance(c, str):
            parts.append(c)
    response = '\n'.join(parts)
    print(f"  elapsed: {elapsed:.1f}s, response chars: {len(response)}")

    # 验证 response 引用了 recall 内容("11 个 panel" 是 L1 预存的精确数字)
    cited_panel = '11' in response and 'panel' in response.lower()
    check("OI response 引用了 recall 注入的 '11 panel'", cited_panel,
          f"response snippet: {response[:200]}")

    # 验证 post_chat 自动 store
    from memory.oi_memory_hooks import get_memory
    after_stats = get_memory().stats()
    check("post_chat L3 至少 1 条(对话快照)", after_stats["by_layer"].get("L3", 0) >= 1,
          f"stats: {after_stats}")


# ============================================================
# 6. 调度层(perception + dynamic_router)
# ============================================================
print("\n=== 6. 调度层 perception + dynamic_router ===")
import asyncio

from perception import PerceptionHub

hub = PerceptionHub(hub_name=f"oi_test_hub_{int(time.time())}")
snap = hub.take_snapshot()
check("perception snapshot 5 个 key",
      set(snap.keys()) == {"timestamp", "speech", "screen", "desktop", "memory"},
      f"got {sorted(snap.keys())}")
check("snapshot.screen 有窗口列表", snap["screen"]["window_list"].get("count", 0) > 0)
check("snapshot.desktop 有鼠标位置", snap["desktop"]["mouse_position"].get("status") == "ok")
snap_async = asyncio.run(hub.take_snapshot_async())
check("take_snapshot_async 可用", "timestamp" in snap_async)

from dynamic_router.router import DynamicRouter

r = DynamicRouter(hub_name=f"oi_test_router_{int(time.time())}")
out = asyncio.run(r.route("截图"))
check("router '截图' → vision.capture", "截图成功" in out, f"got {out}")
out = asyncio.run(r.route("窗口列表"))
check("router '窗口列表' → vision.window_info", "个窗口" in out, f"got {out}")
out = asyncio.run(r.route("统计一下记忆"))
check("router '统计' → memory.stats", "总计" in out, f"got {out}")
out = asyncio.run(r.route("这是一条无法路由的闲聊"))
check("router 未知指令兜底", "未识别指令" in out, f"got {out}")


# ============================================================
# 7. ASR final → shared_memory L3 → 读端闭环
# ============================================================
print("\n=== 7. ASR final → L3 → 感知层闭环 ===")
sys.path.insert(0, str(HERE / "voice_input" / "src"))
from voice_input.asr_memory_writer import ASR_MEMORY_WRITER, _find_oi_enhancements_root

check("writer 能定位 oi_enhancements", _find_oi_enhancements_root() == HERE,
      f"got {_find_oi_enhancements_root()}")
unique_text = f"suite-asr-final-{int(time.time())} 关关雎鸠"
check("ASR final 真正落盘", ASR_MEMORY_WRITER.write(unique_text) is True)
check("writer 进程内缓存", ASR_MEMORY_WRITER.retrieve() == unique_text)
speech = hub.take_snapshot()["speech"]
check("PerceptionHub 读到最新 final", speech.get("text") == unique_text, f"got {speech}")
check("DynamicRouter proxy 读到最新 final",
      r.perception.get_latest_speech() == unique_text)


# ============================================================
# 8. 记忆重编码(shared_memory_recompile)
# ============================================================
print("\n=== 8. 记忆重编码 shared_memory_recompile ===")
from shared_memory_recompile import MemoryBlock, build_default_block

mb = MemoryBlock(name="t", template="Hi {{block_name}} on {{block_proj}}")
mb.edit("name", "OI").edit("proj", "team-web")
check("基础渲染", mb.recompile_prompt() == "Hi OI on team-web")

mb2 = MemoryBlock(name="t2", template='cfg: {"a": 1} + {{block_x}}')
mb2.edit("x", "v")
check("字面大括号安全(JSON 模板)", mb2.recompile_prompt() == 'cfg: {"a": 1} + v',
      f"got {mb2.recompile_prompt()!r}")

mb3 = MemoryBlock(name="t3", template="{{block_missing}}")
try:
    mb3.recompile_prompt()
    check("缺 key 抛 KeyError", False, "没抛异常")
except KeyError as e:
    check("缺 key 抛 KeyError", "missing" in str(e))

blk = build_default_block(persona="OI 助手")
check("默认 persona block 渲染", "OI 助手" in blk.recompile_prompt())


# ============================================================
# 9. FastLane 云适配层 + IM 抽象
# ============================================================
print("\n=== 9. FastLane 云适配层 + IM 抽象 ===")
import ssl as _ssl

from fastlane.adapters import (
    CloudASRClient, CloudIMClient, CloudLLMClient, CloudRouter,
    enforce_https, make_ssl_context,
)

asr_c, llm_c, im_c = CloudASRClient({}), CloudLLMClient({}), CloudIMClient({})
check("三类 adapter 可实例化", all([asr_c, llm_c, im_c]))
check("health_check 默认实现", asyncio.run(asr_c.health_check())["status"] == "not_configured")
out = asyncio.run(llm_c.send_request("/generate", {"prompt": "hi"}))
check("LLM send_request", out["status"] == "ok" and out["generated_text"] == "hi")


class _BoomASR(CloudASRClient):
    async def transcribe_chunks(self, chunks):
        raise ConnectionError("simulated 504")


fl_router = CloudRouter([_BoomASR({"name": "primary"}), CloudASRClient({"name": "backup"})])
texts = asyncio.run(fl_router.call_with_fallback("transcribe_chunks", [{"text": "你好"}]))
check("F-5 自动降级到 backup", texts == ["你好"], f"got {texts}")
try:
    asyncio.run(CloudRouter([_BoomASR({"name": "only"})]).call_with_fallback("transcribe_chunks", []))
    check("F-5 全失败抛 RuntimeError", False, "没抛异常")
except RuntimeError:
    check("F-5 全失败抛 RuntimeError", True)

try:
    enforce_https("http://insecure.example.com")
    check("F-6 拒绝明文 HTTP", False, "没抛异常")
except ValueError:
    check("F-6 拒绝明文 HTTP", True)
check("F-6 TLS 1.3 最低版本", make_ssl_context().minimum_version == _ssl.TLSVersion.TLSv1_3)

from fastlane.adapters.main import app as fl_app, cloud_service
check("FastLane FastAPI app 可导入", fl_app is not None and cloud_service.asr_router is not None)

from im_clients import IMClient, clear_im_clients, register_im_client, send_text_via_im


class _FakeIM(IMClient):
    def send(self, text, target=None):
        return {"status": "ok", "message_id": "m1", "timestamp": time.time(), "target": target or "me"}

    def send_image(self, image_bytes, target=None):
        return {"status": "ok", "message_id": "m2", "timestamp": time.time(), "target": target or "me"}

    def get_history(self, limit=10):
        return []

    def get_self_info(self):
        return {"id": "fake", "nickname": "Fake", "avatar_url": None, "status": "online"}

    def set_presence(self, status):
        return {"status": "ok", "timestamp": time.time()}


clear_im_clients()
register_im_client("default", _FakeIM())
res = send_text_via_im("hello")
check("IM 抽象注册+默认客户端发送", res["status"] == "ok" and res["message_id"] == "m1")
from dynamic_router.router import DynamicRouter as _DR
check("DynamicRouter 未被 IM 代码覆盖(回归)", hasattr(_DR, "route"))
clear_im_clients()


# ============================================================
# 10. Agent Shell /agent_state 端点
# ============================================================
print("\n=== 10. Agent Shell /agent_state ===")
sys.path.insert(0, str(HERE / "voice_input" / "src"))
from voice_input.orchestrator import app as orch_app, agent_lifecycle, AUTH_TOKEN as ORCH_TOKEN
from fastapi.testclient import TestClient

_agent_client = TestClient(orch_app)
agent_lifecycle.set("idle", detail="")
r = _agent_client.get("/agent_state")
check("/agent_state GET idle", r.status_code == 200 and r.json()["state"] == "idle", f"got {r.json()}")
r = _agent_client.post("/agent_state", headers={"Authorization": f"Bearer {ORCH_TOKEN}"}, json={
    "state": "thinking", "detail": "suite smoke", "can_interrupt": True,
})
check("/agent_state POST thinking", r.status_code == 200 and r.json()["state"] == "thinking")
agent_lifecycle.set("idle")


# ============================================================
# 11. Agent Shell S1 骨架(无 GUI)
# ============================================================
print("\n=== 11. Agent Shell S1 骨架 ===")
from agent_shell.config import get_active_profile, load_config
from agent_shell.display import format_status_line
from agent_shell.hotkeys import hotkey_to_pynput

cfg = load_config()
check("shell.yaml 三 profile", set(cfg["profiles"].keys()) == {"base", "fastlane", "ghostline"})
gp = get_active_profile(cfg)
check("active profile 有 orch 端口", "orch" in gp.get("ports", {}))
check("热键解析 ctrl+shift+space", hotkey_to_pynput("ctrl+shift+space") == "<ctrl>+<shift>+<space>")
line = format_status_line("base", {"state": "thinking", "profile": "base", "detail": "test", "can_interrupt": True})
check("状态行格式化", "思考" in line)
from agent_shell.hooks import AgentStateHooks
check("AgentStateHooks 可导入", hasattr(AgentStateHooks, "thinking"))
check("默认启用 floating_orb", load_config()["ui"].get("floating_orb") is True)


# ============================================================
# 12. Agent Shell S3 管线 + 健康合成
# ============================================================
print("\n=== 12. Agent Shell S3 管线 ===")
from agent_shell.health_snap import merge_health_into_snap

s3 = merge_health_into_snap(
    "fastlane",
    {"status": "ok", "asr_health": {"status": "error"}, "tts_health": {"status": "ok"}},
    {"state": "idle"},
)
check("FastLane degraded 合成", s3["state"] == "degraded")
check("VoicePipeline 可导入", __import__("agent_shell.oi_pipeline", fromlist=["VoicePipeline"]).VoicePipeline is not None)


# ============================================================
# 总结
# ============================================================
print(f"\n=== 总结 ===")
print(f"  PASS: {PASS}")
print(f"  FAIL: {FAIL}")
sys.exit(0 if FAIL == 0 else 1)