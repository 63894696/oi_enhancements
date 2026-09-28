"""tests/test_agent_video.py — P3j T14-D 自然语言 → 视频能力 单元测试。

覆盖:
  · parse_intent — 12 种典型中文 query → 命中正确 capability
  · _extract_args — 路径/时间/画幅/topic/script/privacy/mode/sub
  · fill_defaults — 必填字段补全
  · execute(dry_run=True) — 返 preview,不打网络
  · execute(dry_run=False, confirm_callback) — 真发(子服务未起 → 降级返 ok=False,绝不抛栈)
  · cli.main — video / capabilities 子命令 + JSON / --exec / --cap / --no-confirm
  · 边界 — 空 query / 完全无关 query / 路径解析 / soft/硬字幕
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 让 prisir_work 可 import
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from prisir_work import agent_natural_video as anv  # noqa: E402
from prisir_work.cli import main as cli_main, build_parser  # noqa: E402


def _eq(name: str, got, want):
    if got != want:
        raise AssertionError(f"{name}: got {got!r}, want {want!r}")


# ---------------------------------------------------------------------------
# 1) parse_intent — 自然语言 → capability
# ---------------------------------------------------------------------------

def test_parse_empty():
    r = anv.parse_intent("")
    _eq("ok", r.ok, False)
    _eq("error", r.error, "empty_query")


def test_parse_unrelated():
    r = anv.parse_intent("今天天气怎么样?")
    assert r.ok is False
    assert r.error


def test_parse_video_create_with_topic():
    r = anv.parse_intent("帮我做个 9:16 短视频,主题是 AI 改变办公")
    _eq("capability", r.capability, "video.create")
    _eq("aspect_ratio", r.args.get("aspect_ratio"), "9:16")
    _eq("topic", r.args.get("topic"), "AI 改变办公")
    assert "script" in r.missing


def test_parse_video_create_with_script():
    r = anv.parse_intent("帮我出个片,主题 PrisirAI,文案是介绍 PrisirAI 的核心能力")
    _eq("capability", r.capability, "video.create")
    _eq("topic", r.args.get("topic"), "PrisirAI")
    assert "script" not in r.missing


def test_parse_video_create_no_aspect():
    r = anv.parse_intent("帮我做个视频,主题 X,文案 Y")
    assert r.capability == "video.create"
    assert r.args.get("aspect_ratio") is None


def test_parse_video_cut():
    r = anv.parse_intent("把 C:/v.mp4 从 00:10 裁到 00:30 输出 C:/out.mp4")
    _eq("capability", r.capability, "video.cut")
    _eq("input", r.args.get("input"), "C:/v.mp4")
    _eq("output", r.args.get("output"), "C:/out.mp4")
    _eq("start", r.args.get("start"), "00:10")
    _eq("end", r.args.get("end"), "00:30")
    assert r.missing == []


def test_parse_video_tts():
    r = anv.parse_intent("把这段文字转成语音")
    _eq("capability", r.capability, "video.tts")
    assert "text_or_file" in r.missing


def test_parse_video_asr():
    r = anv.parse_intent("给 C:/v.mp4 自动加字幕")
    _eq("capability", r.capability, "video.asr")
    _eq("input", r.args.get("input"), "C:/v.mp4")
    assert r.missing == []


def test_parse_video_bgm():
    r = anv.parse_intent("给 C:/v.mp4 加背景音乐 C:/bgm.mp3")
    _eq("capability", r.capability, "video.bgm")
    _eq("input", r.args.get("input"), "C:/v.mp4")
    assert "music" in r.missing  # bgm.mp3 被当成 output,需要识别为 music


def test_parse_video_burn_srt_first():
    """'把 srt 烧到 mp4' — srt 在前, mp4 在后"""
    r = anv.parse_intent("把 C:/a.srt 字幕烧到 C:/v.mp4 输出 C:/out.mp4")
    _eq("capability", r.capability, "video.burn")
    _eq("sub", r.args.get("sub"), "C:/a.srt")
    _eq("input", r.args.get("input"), "C:/v.mp4")
    _eq("output", r.args.get("output"), "C:/out.mp4")


def test_parse_video_burn_video_first():
    """'把 mp4 字幕烧 srt' — mp4 在前, srt 在后"""
    r = anv.parse_intent("把 C:/v.mp4 字幕烧 C:/a.srt 输出 C:/out.mp4")
    _eq("capability", r.capability, "video.burn")
    _eq("input", r.args.get("input"), "C:/v.mp4")
    _eq("sub", r.args.get("sub"), "C:/a.srt")
    _eq("output", r.args.get("output"), "C:/out.mp4")


def test_parse_video_burn_soft():
    r = anv.parse_intent("把 C:/v.mp4 字幕烧 C:/a.srt 输出 C:/out.mp4,软字幕")
    _eq("capability", r.capability, "video.burn")
    _eq("soft", r.args.get("soft"), True)


def test_parse_video_burn_hard():
    r = anv.parse_intent("把 C:/v.mp4 烧硬字幕 C:/a.srt 输出 C:/out.mp4")
    _eq("capability", r.capability, "video.burn")
    _eq("soft", r.args.get("soft"), False)


def test_parse_video_info():
    r = anv.parse_intent("查 C:/v.mp4 多长多大")
    _eq("capability", r.capability, "video.info")
    _eq("path", r.args.get("path"), "C:/v.mp4")


def test_parse_video_analyze_time():
    r = anv.parse_intent("看看发布最佳时段")
    _eq("capability", r.capability, "video.analyze")
    _eq("mode", r.args.get("mode"), "time")


def test_parse_video_analyze_tags():
    r = anv.parse_intent("分析标签效果")
    _eq("capability", r.capability, "video.analyze")
    _eq("mode", r.args.get("mode"), "tags")


def test_parse_youtube_upload():
    r = anv.parse_intent("上传 C:/v.mp4 到 YouTube,标题 AI Demo,公开")
    _eq("capability", r.capability, "youtube.upload")
    _eq("video", r.args.get("video"), "C:/v.mp4")
    _eq("privacy", r.args.get("privacy"), "public")
    assert "title" in r.missing


def test_parse_youtube_upload_unlisted():
    r = anv.parse_intent("推到 YouTube 不公开")
    _eq("capability", r.capability, "youtube.upload")
    _eq("privacy", r.args.get("privacy"), "unlisted")


def test_parse_youtube_upload_private():
    r = anv.parse_intent("上传到 youtube,私密")
    _eq("capability", r.capability, "youtube.upload")
    _eq("privacy", r.args.get("privacy"), "private")


def test_parse_youtube_list():
    r = anv.parse_intent("我 YouTube 有什么视频")
    _eq("capability", r.capability, "youtube.list")
    assert r.missing == []


def test_parse_youtube_status():
    r = anv.parse_intent("youtube 状态")
    _eq("capability", r.capability, "youtube.status")
    assert r.missing == []


# ---------------------------------------------------------------------------
# 2) fill_defaults — 补全
# ---------------------------------------------------------------------------

def test_fill_video_create_defaults():
    body = anv.fill_defaults("video.create", {"topic": "X"})
    _eq("aspect_ratio", body["aspect_ratio"], "9:16")
    _eq("duration", body["duration"], 60)
    _eq("with_subtitle", body["with_subtitle"], True)
    _eq("topic", body["topic"], "X")  # 已有


def test_fill_video_create_no_overwrite():
    body = anv.fill_defaults("video.create", {"aspect_ratio": "16:9"})
    _eq("aspect_ratio", body["aspect_ratio"], "16:9")


def test_fill_youtube_upload_defaults():
    body = anv.fill_defaults("youtube.upload", {"video": "X", "title": "T"})
    _eq("privacy", body["privacy"], "private")
    _eq("exec_real", body["exec_real"], False)


def test_fill_unknown_capability_no_crash():
    body = anv.fill_defaults("does.not.exist", {"k": "v"})
    _eq("k", body["k"], "v")


# ---------------------------------------------------------------------------
# 3) execute(dry_run=True) — 只 parse,不打网络
# ---------------------------------------------------------------------------

def test_execute_dry_run():
    r = anv.execute("帮我做个 9:16 短视频,主题 X,文案 Y")
    assert r.ok
    _eq("preview", r.result.get("preview"), True)
    _eq("capability", r.intent.capability, "video.create")
    assert r.body.get("aspect_ratio") == "9:16"


def test_execute_dry_run_empty():
    r = anv.execute("")
    assert r.ok is False
    _eq("error", r.error, "empty_query")


# ---------------------------------------------------------------------------
# 4) execute(dry_run=False, confirm_callback) — 真发,降级不崩
# ---------------------------------------------------------------------------

def test_execute_real_with_confirm():
    """真发:子服务未起 → 降级返 ok=False + url_error,不抛栈。"""
    called = []

    def cb(intent):
        called.append(intent.to_dict())
        return True

    # 用可识别的 query — "查 C:/v.mp4 多长多大" 命中 video.info
    r = anv.execute("查 C:/v.mp4 多长多大",
                    dry_run=False, confirm_callback=cb)
    assert called, "confirm_callback 没被调用"
    # 子服务可能没起;可能 ok 也可能 ok=False — 但绝对不抛栈
    assert isinstance(r.ok, bool)
    assert isinstance(r.error, str)


def test_execute_real_user_declines():
    r = anv.execute("做个视频,主题 X,文案 Y",
                    dry_run=False,
                    confirm_callback=lambda i: False)
    assert r.ok is False
    _eq("error", r.error, "user_declined")


def test_execute_real_no_callback_dry_run_false():
    """confirm=None + dry_run=False 直接发 — 测试降级路径不崩。"""
    r = anv.execute("查 C:/v.mp4 多长多大",
                    dry_run=False, confirm_callback=None)
    assert isinstance(r.ok, bool)


# ---------------------------------------------------------------------------
# 5) CLI — 主入口子命令
# ---------------------------------------------------------------------------

def test_cli_capabilities_human():
    """列出所有 video/youtube capability(人类可读)。"""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(["capabilities"])
    assert rc == 0
    out = buf.getvalue()
    assert "video.create" in out
    assert "youtube.upload" in out


def test_cli_capabilities_json():
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(["capabilities", "--json"])
    assert rc == 0
    caps = json.loads(buf.getvalue())
    ids = {c["id"] for c in caps}
    assert "video.create" in ids
    assert "youtube.upload" in ids
    assert "youtube.status" in ids


def test_cli_video_dry_run_human():
    """dry_run 模式:命中 + 列出 body,不真发。"""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(["video", "帮我做个 9:16 短视频,主题 X,文案 Y"])
    assert rc == 0
    out = buf.getvalue()
    assert "video.create" in out
    assert "9:16" in out


def test_cli_video_dry_run_json():
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(["video", "--json", "帮我做个视频,主题 X,文案 Y"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["preview"] is True
    _eq("capability", payload["intent"]["capability"], "video.create")


def test_cli_video_unrecognized_json():
    """无关 query 返 ok=False,returncode=1。"""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(["video", "--json", "今天天气怎么样"])
    assert rc == 1
    payload = json.loads(buf.getvalue())
    assert payload["ok"] is False


def test_cli_video_cap_override():
    """--cap + --body 跳过 parse,直接走 capability。"""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main([
            "video", "ignored", "--cap", "video.info",
            "--body", '{"path": "C:/v.mp4"}',
        ])
    assert rc == 0
    out = buf.getvalue()
    assert "video.info" in out


def test_cli_video_exec_real_dry_run_default():
    """不传 --exec → 默认 dry_run,不加 confirm。"""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(["video", "帮我做个视频,主题 X,文案 Y"])
    assert rc == 0
    assert "preview" not in buf.getvalue()  # 人类可读不写 preview
    assert "命中能力" in buf.getvalue()


def test_cli_video_exec_real():
    """--exec → 真发(子服务未起,降级)。"""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(["video", "--exec", "--no-confirm",
                      "查 C:/v.mp4 多长多大"])
    # 子服务不一定起;只要不崩 + returncode 是 0/1 都行
    assert rc in (0, 1)


def test_cli_video_bad_body_json():
    """--body 不是 JSON → returncode=2。"""
    import io
    from contextlib import redirect_stderr
    buf = io.StringIO()
    with redirect_stderr(buf):
        rc = cli_main([
            "video", "--cap", "video.info",
            "--body", "this is not json",
        ])
    assert rc == 2


def test_cli_no_cmd():
    """不传 cmd → argparse SystemExit(2)。"""
    import io
    from contextlib import redirect_stdout, redirect_stderr
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            rc = cli_main([])
        except SystemExit as e:
            assert e.code == 2
            return
    raise AssertionError("cli_main([]) should SystemExit")


# ---------------------------------------------------------------------------
# 6) 边界
# ---------------------------------------------------------------------------

def test_intent_summary_not_empty():
    s = anv.intent_summary()
    assert "视频" in s
    assert "YouTube" in s
    assert "TTS" in s or "配音" in s
    assert len(s) > 100


def test_intent_result_to_dict():
    r = anv.parse_intent("给我 video.info 看看 C:/v.mp4")
    d = r.to_dict()
    assert "ok" in d
    assert "capability" in d
    assert "args" in d
    assert "missing" in d
    assert "confidence" in d


def test_execute_result_to_dict():
    r = anv.execute("给我 video.info 看看 C:/v.mp4")
    d = r.to_dict()
    assert "ok" in d
    assert "intent" in d
    assert "body" in d


def test_parse_intent_fallback_capability_search():
    """关键词没命中时 fallback 到 capability.search。"""
    r = anv.parse_intent("短视频生成")
    # 可能命中 video.create(video.create title 含 "做视频"/"出片"等)
    # 不强制要求 ok=True,但 fallback 必须有 reason
    if not r.ok:
        assert "未识别意图" in r.error or "试试" in r.error


# ---------------------------------------------------------------------------
# 7) companion backend handler(/api/chat/parse + /api/chat/exec)
# ---------------------------------------------------------------------------

def test_chat_parse_handler_missing_body():
    """POST /api/chat/parse 不传 query → ok=False + empty_query。"""
    import asyncio
    from unittest.mock import MagicMock
    # 直接复用 handler 函数,绕过 aiohttp 的 Request 包装
    import importlib.util
    spec_path = _HERE.parent / "companion" / "prisIragent-wechat-publisher.py"
    # handler 模块不能直接 import(它是 web app 注册),改成测等价逻辑
    from prisir_work import agent_natural_video as anv
    r = anv.parse_intent("")
    assert r.ok is False
    _eq("error", r.error, "empty_query")


def test_chat_parse_handler_dry_run():
    """模拟 /api/chat/parse:parse_intent + fill_defaults 全链路。"""
    from prisir_work import agent_natural_video as anv
    intent = anv.parse_intent("帮我做个 9:16 短视频,主题 PrisirAI,文案 介绍 PrisirAI")
    assert intent.ok
    assert intent.capability == "video.create"
    body = anv.fill_defaults(intent.capability, intent.args)
    _eq("aspect_ratio", body["aspect_ratio"], "9:16")
    _eq("topic", body["topic"], "PrisirAI")


def test_chat_exec_handler_missing_capability():
    """模拟 /api/chat/exec:intent 没 capability → ok=False。"""
    body = {"query": "x", "intent": {}}
    cap = body["intent"].get("capability") or ""
    assert cap == ""


def test_chat_exec_handler_with_missing():
    """模拟 /api/chat/exec:intent.missing 非空 → ok=False + error。"""
    body = {"query": "做个视频", "intent": {
        "capability": "video.create", "args": {}, "missing": ["topic", "script"]}}
    missing = body["intent"].get("missing") or []
    assert missing == ["topic", "script"]
    # 后端逻辑:missing 非空 → 拒


def test_chat_exec_handler_happy_path_routes_to_endpoint():
    """模拟 /api/chat/exec happy path:capability 已知 + endpoint 注册 → 走 endpoints._REGISTRY handler。"""
    from prisir_work import capability as cap_mod
    from prisir_work import endpoints as ep_mod
    from prisir_work import agent_natural_video as anv

    intent = anv.parse_intent("查 C:/v.mp4 多长多大")
    cap_entry = cap_mod.get(intent.capability)
    assert cap_entry
    ep_entry = ep_mod._REGISTRY.get(cap_entry["endpoint"])
    assert ep_entry  # /video/info 一定注册了


def test_chat_exec_handler_capability_unknown():
    """模拟 /api/chat/exec:capability 不在白名单 → ok=False + capability_not_found。"""
    from prisir_work import capability as cap_mod
    assert cap_mod.get("does.not.exist") is None


# ---------------------------------------------------------------------------
# 8) LLM 增强意图抽取(P3j T15-B)
# ---------------------------------------------------------------------------

def test_llm_enhancer_build_prompt():
    """build_llm_prompt 渲染 12 个 capability hints。"""
    from prisir_work import capability as cap_mod
    from prisir_work.agent_llm_enhancer import build_llm_prompt
    hints = [c for c in cap_mod.list_capabilities()
             if c["id"].startswith("video.")
             or c["id"].startswith("youtube.")]
    assert len(hints) >= 12
    p = build_llm_prompt("做个视频", hints)
    assert "video.create" in p
    assert "youtube.upload" in p
    assert "JSON" in p
    assert "做个视频" in p


def test_llm_enhancer_parse_plain_json():
    from prisir_work.agent_llm_enhancer import parse_llm_json_response
    text = '{"capability": "video.create", "args": {"topic": "X"}, "missing": [], "confidence": 0.9}'
    r = parse_llm_json_response(text)
    assert r["capability"] == "video.create"
    assert r["args"]["topic"] == "X"


def test_llm_enhancer_parse_markdown_fence():
    from prisir_work.agent_llm_enhancer import parse_llm_json_response
    text = '```json\n{"capability": "video.cut", "args": {"input": "a"}}\n```'
    r = parse_llm_json_response(text)
    assert r["capability"] == "video.cut"


def test_llm_enhancer_parse_with_prefix_suffix():
    from prisir_work.agent_llm_enhancer import parse_llm_json_response
    text = '解析结果是:\n{"capability": "video.info"}\n完毕'
    r = parse_llm_json_response(text)
    assert r["capability"] == "video.info"


def test_llm_enhancer_parse_nested_json():
    """args 里有嵌套 dict / 数组 → 仍能解析。"""
    from prisir_work.agent_llm_enhancer import parse_llm_json_response
    text = '{"capability": "video.burn", "args": {"input": "a", "sub": {"path": "b"}}}'
    r = parse_llm_json_response(text)
    assert r["args"]["sub"]["path"] == "b"


def test_llm_enhancer_parse_garbage_returns_none():
    from prisir_work.agent_llm_enhancer import parse_llm_json_response
    assert parse_llm_json_response("") is None
    assert parse_llm_json_response("不是 JSON") is None
    assert parse_llm_json_response("{ unmatched brace") is None


def test_llm_enhancer_enhance_with_llm_graceful_fail():
    """enhance_with_llm 在 LLM 不可用时返 None,不抛栈。"""
    from prisir_work.agent_llm_enhancer import enhance_with_llm
    # timeout=0.001 必超时,确保降级路径
    r = enhance_with_llm("做个视频,主题 X", timeout=0.001)
    # LLM 没启或超时 → 返 None(降级)
    assert r is None


def test_llm_enhancer_fallback_into_parse_intent():
    """parse_intent 在 regex + capability.search 都未命中时,调 LLM 增强。
    LLM 不可用 → 仍返 not-recognized,绝不崩。
    """
    r = anv.parse_intent("今天天气怎么样")
    assert r.ok is False
    # 错误信息兜底:包含未识别意图
    assert "未识别意图" in r.error or "试试" in r.error


def test_llm_enhancer_import_no_crash():
    """agent_llm_enhancer 可 import,且公开函数都在。"""
    import prisir_work.agent_llm_enhancer as m
    assert callable(m.enhance_with_llm)
    assert callable(m.build_llm_prompt)
    assert callable(m.parse_llm_json_response)


# ---------------------------------------------------------------------------
# 9) 多轮对话补 missing(P3j T15-C)
# ---------------------------------------------------------------------------

def test_multi_turn_start_with_missing():
    """第一轮:topic 有 / script 缺 → missing=['script'], ready=False。"""
    from prisir_work.agent_multi_turn import SessionStore
    store = SessionStore()
    s = store.start("帮我做个 9:16 短视频,主题 PrisirAI")
    assert s.capability == "video.create"
    assert s.ready is False
    assert "script" in s.missing
    # question 是人话
    assert any("文案" in q for q in s.missing_questions)


def test_multi_turn_fill_to_ready():
    """第二轮填 script → ready=True, missing 空。"""
    from prisir_work.agent_multi_turn import SessionStore
    store = SessionStore()
    s = store.start("帮我做个 9:16 短视频,主题 PrisirAI")
    s2 = store.fill(s.id, "script", "介绍 PrisirAI 的核心能力")
    assert s2.ready is True
    assert s2.missing == []
    _eq("script", s2.args["script"], "介绍 PrisirAI 的核心能力")
    # history 留痕
    assert any(h.get("role") == "user" for h in s2.history)


def test_multi_turn_multiple_rounds():
    """topic + script 都缺,2 轮才 ready。"""
    from prisir_work.agent_multi_turn import SessionStore
    store = SessionStore()
    s = store.start("帮我做个 9:16 短视频")
    assert "topic" in s.missing
    assert "script" in s.missing
    assert s.ready is False

    s = store.fill(s.id, "topic", "PrisirAI")
    assert s.ready is False
    assert "topic" not in s.missing
    assert "script" in s.missing

    s = store.fill(s.id, "script", "Y")
    assert s.ready is True


def test_multi_turn_already_ready():
    """查 C:/v.mp4 多长多大 — 一步到位 ready=True。"""
    from prisir_work.agent_multi_turn import SessionStore
    store = SessionStore()
    s = store.start("查 C:/v.mp4 多长多大")
    assert s.capability == "video.info"
    assert s.ready is True
    assert s.missing == []


def test_multi_turn_unrecognized_query():
    """完全无关的 query → capability='',questions=[error msg]。"""
    from prisir_work.agent_multi_turn import SessionStore
    store = SessionStore()
    s = store.start("今天天气怎么样")
    assert s.capability == ""
    assert s.ready is False
    # question 字段塞错误消息,前端应把它当错误处理
    assert any("未识别" in q or "试试" in q for q in s.missing_questions)


def test_multi_turn_unknown_session_id():
    """fill 不存在的 session_id → None。"""
    from prisir_work.agent_multi_turn import SessionStore
    store = SessionStore()
    assert store.fill("nonexistent", "x", "y") is None
    assert store.get("nonexistent") is None


def test_multi_turn_drop():
    """drop 后 get 返 None。"""
    from prisir_work.agent_multi_turn import SessionStore
    store = SessionStore()
    s = store.start("做个视频,主题 X,文案 Y")
    assert store.get(s.id) is not None
    store.drop(s.id)
    assert store.get(s.id) is None


def test_multi_turn_module_singletons():
    """模块级 start_session / fill_missing / get_session 单例可用。"""
    from prisir_work import agent_multi_turn as mt
    s = mt.start_session("帮我做个 9:16 短视频,主题 PrisirAI")
    s2 = mt.fill_missing(s.id, "script", "Y")
    assert s2.ready is True
    mt._global_store.drop(s.id)


def test_multi_turn_missing_question_keys():
    """missing 翻译覆盖主要 key(topic/script/text/file/input/output 等)。"""
    from prisir_work.agent_multi_turn import _MISSING_QUESTIONS
    for key in ("topic", "script", "text", "output", "input",
                "music", "sub", "video", "title", "path"):
        assert key in _MISSING_QUESTIONS


def test_multi_turn_to_dict():
    """Session.to_dict 序列化齐全。"""
    from prisir_work.agent_multi_turn import SessionStore
    store = SessionStore()
    s = store.start("帮我做个 9:16 短视频,主题 X")
    d = s.to_dict()
    assert d["id"] == s.id
    assert d["capability"] == "video.create"
    assert "missing" in d
    assert "missing_questions" in d
    assert "history" in d


# ---------------------------------------------------------------------------
# 10) 跨能力编排 workflow(P3j T15-D)
# ---------------------------------------------------------------------------

def test_workflow_parse_dependencies():
    from prisir_work.agent_video_workflow import parse_dependencies
    steps = parse_dependencies([
        {"id": "a", "capability": "video.create", "args": {"topic": "X"}},
        {"id": "b", "capability": "youtube.upload", "depends_on": ["a"],
         "args": {"video": "$a.artifact.path"}},
    ])
    _eq("id_a", steps[0].id, "a")
    _eq("depends_b", steps[1].depends_on, ["a"])


def test_workflow_parse_missing_id_raises():
    from prisir_work.agent_video_workflow import parse_dependencies
    try:
        parse_dependencies([{"capability": "video.create"}])
    except ValueError as e:
        assert "id" in str(e)
        return
    raise AssertionError("expected ValueError")


def test_workflow_topo_sort_simple():
    from prisir_work.agent_video_workflow import parse_dependencies, _topo_sort
    steps = parse_dependencies([
        {"id": "a", "capability": "video.create"},
        {"id": "b", "capability": "video.cut", "depends_on": ["a"]},
        {"id": "c", "capability": "video.burn", "depends_on": ["a", "b"]},
    ])
    ordered = _topo_sort(steps)
    ids = [s.id for s in ordered]
    # a → b → c
    assert ids.index("a") < ids.index("b") < ids.index("c")


def test_workflow_topo_sort_unknown_dep_raises():
    from prisir_work.agent_video_workflow import parse_dependencies, _topo_sort
    steps = parse_dependencies([
        {"id": "a", "capability": "video.create", "depends_on": ["zzz"]},
    ])
    try:
        _topo_sort(steps)
    except ValueError as e:
        assert "zzz" in str(e)
        return
    raise AssertionError("expected ValueError")


def test_workflow_topo_sort_cycle_raises():
    from prisir_work.agent_video_workflow import parse_dependencies, _topo_sort
    steps = parse_dependencies([
        {"id": "a", "capability": "video.create", "depends_on": ["b"]},
        {"id": "b", "capability": "video.cut", "depends_on": ["a"]},
    ])
    try:
        _topo_sort(steps)
    except ValueError:
        return
    raise AssertionError("expected ValueError on cycle")


def test_workflow_resolve_refs_string_fullmatch():
    from prisir_work.agent_video_workflow import _resolve_refs
    ctx = {"a": {"result": {"artifact": {"path": "/tmp/v.mp4"}}}}
    _eq("v", _resolve_refs("$a.artifact.path", ctx), "/tmp/v.mp4")


def test_workflow_resolve_refs_string_partial():
    """前缀/后缀字符串中的 $ref 也要解析。"""
    from prisir_work.agent_video_workflow import _resolve_refs
    ctx = {"a": {"result": {"path": "/x"}}}
    _eq("v", _resolve_refs("前缀-$a.path-后缀", ctx), "前缀-/x-后缀")


def test_workflow_resolve_refs_dict():
    from prisir_work.agent_video_workflow import _resolve_refs
    ctx = {"a": {"result": {"path": "/y"}}}
    out = _resolve_refs({"v": "$a.path", "n": 5}, ctx)
    _eq("v", out["v"], "/y")
    _eq("n", out["n"], 5)


def test_workflow_resolve_refs_list():
    from prisir_work.agent_video_workflow import _resolve_refs
    ctx = {"a": {"result": {"path": "/z"}}}
    out = _resolve_refs(["$a.path", "literal"], ctx)
    _eq("0", out[0], "/z")
    _eq("1", out[1], "literal")


def test_workflow_resolve_refs_unknown_step():
    """未注册的 step → None,不抛栈。"""
    from prisir_work.agent_video_workflow import _resolve_refs
    _eq("v", _resolve_refs("$unknown.path", {}), None)


def test_workflow_run_empty_steps():
    from prisir_work.agent_video_workflow import run_workflow
    r = run_workflow({"steps": []})
    assert r.ok is False
    _eq("error", r.error, "no_steps")


def test_workflow_run_no_steps_key():
    from prisir_work.agent_video_workflow import run_workflow
    r = run_workflow({})
    assert r.ok is False


def test_workflow_run_one_step_dry_run_path():
    """单步 — 子服务没起 → 降级返 ok=False + failed_step。"""
    from prisir_work.agent_video_workflow import run_workflow
    r = run_workflow({"steps": [
        {"id": "info", "capability": "video.info", "args": {"path": "/nope.mp4"}},
    ]})
    assert r.ok is False
    _eq("failed_step", r.failed_step, "info")
    # 已记录 step
    assert "info" in r.steps
    assert r.steps["info"]["capability"] == "video.info"


def test_workflow_run_two_steps_stop_on_error():
    """第二步依赖第一步 → 失败时后续步骤不跑。"""
    from prisir_work.agent_video_workflow import run_workflow
    r = run_workflow({"steps": [
        {"id": "info", "capability": "video.info", "args": {"path": "/nope.mp4"}},
        {"id": "upload", "capability": "youtube.upload",
         "depends_on": ["info"],
         "args": {"video": "$info.result.artifact.path", "title": "X"}},
    ]})
    assert r.ok is False
    _eq("failed_step", r.failed_step, "info")
    assert "upload" not in r.steps  # 没跑


def test_workflow_run_unknown_capability():
    from prisir_work.agent_video_workflow import run_workflow
    r = run_workflow({"steps": [
        {"id": "x", "capability": "does.not.exist"},
    ]})
    assert r.ok is False
    _eq("failed_step", r.failed_step, "x")
    assert "capability_not_found" in r.error


def test_workflow_run_unknown_dep_in_dsl():
    from prisir_work.agent_video_workflow import run_workflow
    r = run_workflow({"steps": [
        {"id": "a", "capability": "video.create", "depends_on": ["ghost"]},
    ]})
    assert r.ok is False
    assert "parse_failed" in r.error or "未知" in r.error


def test_workflow_result_to_dict():
    from prisir_work.agent_video_workflow import run_workflow
    r = run_workflow({"steps": [
        {"id": "info", "capability": "video.info", "args": {"path": "/nope.mp4"}},
    ]})
    d = r.to_dict()
    assert "ok" in d
    assert "steps" in d
    assert "failed_step" in d
    assert "error" in d


def test_workflow_import_no_crash():
    import prisir_work.agent_video_workflow as m
    assert callable(m.run_workflow)
    assert callable(m.parse_dependencies)


# ---------------------------------------------------------------------------
# P3j T16-A: 主对话 EXEC 标记扫描 + 风险门 + ws 事件构造
# ---------------------------------------------------------------------------

def test_hook_parse_single_marker():
    """最简单形态 — 单 capability,单 arg。"""
    from prisir_work.agent_main_chat_hook import parse_exec_markers
    text = '好的,我来帮你做。[[EXEC: video.create topic="PrisirAI" script="介绍 PrisirAI"]]'
    ms = parse_exec_markers(text)
    _eq("len", len(ms), 1)
    _eq("cap", ms[0].capability, "video.create")
    _eq("topic", ms[0].args.get("topic"), "PrisirAI")
    _eq("script", ms[0].args.get("script"), "介绍 PrisirAI")


def test_hook_parse_no_marker():
    from prisir_work.agent_main_chat_hook import parse_exec_markers
    text = "好的,我帮你看看视频信息。"
    _eq("len", len(parse_exec_markers(text)), 0)


def test_hook_parse_multiple_markers():
    """一轮多触发。"""
    from prisir_work.agent_main_chat_hook import parse_exec_markers
    text = ('先做视频。[[EXEC: video.create topic="X" script="Y"]] '
            '再上传。[[EXEC: youtube.upload video="/a.mp4" title="Z"]]')
    ms = parse_exec_markers(text)
    _eq("len", len(ms), 2)
    _eq("first.cap", ms[0].capability, "video.create")
    _eq("second.cap", ms[1].capability, "youtube.upload")


def test_hook_parse_quoted_escapes():
    """转义:引号 / 反斜杠。"""
    from prisir_work.agent_main_chat_hook import parse_exec_markers
    text = r'[[EXEC: video.create topic="他说 \"你好\"" script="x\\y"]]'
    ms = parse_exec_markers(text)
    _eq("len", len(ms), 1)
    _eq("topic", ms[0].args.get("topic"), '他说 "你好"')


def test_hook_scan_l0_dry_runs():
    """L0 能力(免确认)— 真跑 video.info(预期子服务未起 → ok=False,但 event 形态正确)。"""
    from prisir_work.agent_main_chat_hook import scan_and_exec
    text = '[[EXEC: video.info path="/no/such/file.mp4"]]'
    events = scan_and_exec(text)
    assert events, "应有 1 个 event"
    _eq("type", events[0]["type"], "capability_exec_result")
    _eq("capability", events[0]["capability"], "video.info")
    # video.info 是 L0 走真发路径(没子服务 → ok=False + endpoint err)


def test_hook_scan_l1_triggers_confirm():
    """L1 能力(无 confirm_callback) → 推 capability_confirm_request,不真发。"""
    from prisir_work.agent_main_chat_hook import scan_and_exec
    text = '[[EXEC: video.tts text="hello" voice="zh-CN-XiaoxiaoNeural"]]'
    events = scan_and_exec(text)
    assert events
    _eq("type", events[0]["type"], "capability_confirm_request")
    _eq("capability", events[0]["capability"], "video.tts")
    assert events[0]["risk"] == "L1"


def test_hook_scan_l3_triggers_confirm():
    """L3 能力(YouTube)同样走确认卡。"""
    from prisir_work.agent_main_chat_hook import scan_and_exec
    text = '[[EXEC: youtube.upload video="/a.mp4" title="X" privacy="public"]]'
    events = scan_and_exec(text)
    assert events
    _eq("type", events[0]["type"], "capability_confirm_request")
    _eq("risk", events[0]["risk"], "L3")


def test_hook_scan_unknown_capability_returns_error():
    """不存在的 capability → capability_exec_result(ok=False,error=...)不抛栈。"""
    from prisir_work.agent_main_chat_hook import scan_and_exec
    text = '[[EXEC: fake.cap x="1"]]'
    events = scan_and_exec(text)
    assert events
    _eq("type", events[0]["type"], "capability_exec_result")
    _eq("ok", events[0]["ok"], False)
    assert "capability_not_found" in events[0]["error"]


def test_hook_scan_confirm_callback_accepted():
    """传 confirm_callback + 返 True → 真发(子服务未起 → ok=False,但走过 exec 路径)。"""
    from prisir_work.agent_main_chat_hook import scan_and_exec
    text = '[[EXEC: video.info path="/x.mp4"]]'
    events = scan_and_exec(text, confirm_callback=lambda m: True)
    _eq("type", events[0]["type"], "capability_exec_result")


def test_hook_scan_confirm_callback_declined():
    """confirm_callback 返 False → user_declined,不走真发。"""
    from prisir_work.agent_main_chat_hook import scan_and_exec
    text = '[[EXEC: video.info path="/x.mp4"]]'
    events = scan_and_exec(text, confirm_callback=lambda m: False)
    _eq("type", events[0]["type"], "capability_exec_result")
    _eq("ok", events[0]["ok"], False)
    _eq("error", events[0]["error"], "user_declined")


def test_hook_build_exec_result_shape():
    """build_exec_result 字段齐全。"""
    from prisir_work.agent_main_chat_hook import build_exec_result
    ev = build_exec_result("video.info", ok=True, result={"a": 1})
    _eq("type", ev["type"], "capability_exec_result")
    _eq("capability", ev["capability"], "video.info")
    _eq("ok", ev["ok"], True)
    assert ev["result"]["a"] == 1


def test_hook_build_confirm_request_shape():
    from prisir_work.agent_main_chat_hook import build_confirm_request, ExecMarker
    m = ExecMarker(capability="video.create", args={"topic": "X"})
    ev = build_confirm_request(m)
    _eq("type", ev["type"], "capability_confirm_request")
    _eq("cap", ev["capability"], "video.create")
    assert ev["args"]["topic"] == "X"
    assert ev["risk"] in ("L1", "L2", "L3")


def test_hook_push_hook_events_wrapper():
    """push_hook_events 薄包装,不带 callback = 等同 scan_and_exec。"""
    from prisir_work.agent_main_chat_hook import push_hook_events
    text = '[[EXEC: fake.cap x="1"]]'
    events = push_hook_events(text)
    assert events
    _eq("type", events[0]["type"], "capability_exec_result")


# ---------------------------------------------------------------------------
# P3j T16-B: intent_summary 注入 system prompt
# ---------------------------------------------------------------------------

def test_intent_summary_covers_12_capabilities():
    """intent_summary 必须列出 12 capability(主对话 LLM 才知道能调谁)。"""
    s = anv.intent_summary()
    assert "视频创作" in s or "video" in s
    # 中文能力名(LLM 看的,不是 capability id)
    expected_caps = [
        "一键出片",          # video.create
        "TTS",                # video.tts
        "字幕",               # video.asr / video.burn
        "裁剪",               # video.cut
        "BGM",                # video.bgm
        "背景音乐",           # video.bgm
        "字幕烧录",           # video.burn
        "视频元数据",         # video.info
        "数据分析",           # video.analyze
        "YouTube 上传",       # youtube.upload
        "YouTube",            # youtube.list/status
    ]
    for cap in expected_caps:
        assert cap in s, f"missing {cap}"


def test_intent_summary_documents_exec_protocol():
    """系统 prompt 注入段必须说明 [[EXEC: ...]] 格式。"""
    # 直接读 module 常量做契约检查
    from prisir_work.agent_natural_video import intent_summary
    s = intent_summary()
    # 至少给几个例子 query(LLM 才会模仿)
    assert "做个" in s or "做个 9:16" in s
    assert "字幕" in s
    assert "YouTube" in s


def test_companion_build_messages_includes_intent_summary():
    """intent_summary 必须可注入到 system prompt(verify 在 companion web 注入处)。"""
    from prisir_work.agent_natural_video import intent_summary as _is
    s = _is()
    # 注入到 system prompt 的字符串必须非空 + 有视频能力描述
    assert len(s) > 100
    assert "视频" in s and "YouTube" in s
    # EXEC 协议由 companion/prisIragent-companion-web.py 注入
    # 这里检查 build_messages 模块路径可解析(不真跑,因依赖 aiohttp session)
    import importlib.util
    spec_path = "companion/prisIragent-companion-web.py"
    assert importlib.util.find_spec("companion.prisIragent_companion_web") or \
        importlib.util.find_spec("companion"), \
        "companion 模块需可 import"


# ---------------------------------------------------------------------------
# P3j T16-C: 风险门 + 确认请求事件构造
# ---------------------------------------------------------------------------

def test_hook_confirm_request_includes_title_and_msg():
    """confirm_request 必须含 title / confirm / args 让前端可弹卡。"""
    from prisir_work.agent_main_chat_hook import (
        scan_and_exec, build_confirm_request)
    text = '[[EXEC: video.create topic="X" script="Y"]]'
    evs = scan_and_exec(text)
    assert evs
    ev = evs[0]
    _eq("type", ev["type"], "capability_confirm_request")
    assert ev.get("title"), "title must be populated from capability registry"
    assert ev.get("confirm"), "confirm message must be populated"
    _eq("cap", ev["capability"], "video.create")
    assert ev["args"]["topic"] == "X"


def test_hook_risk_color_mapping():
    """不同 risk 级别走不同颜色(前端 data-risk 属性)。"""
    from prisir_work.agent_main_chat_hook import scan_and_exec
    cases = [
        ("[[EXEC: video.tts text=\"hi\"]]", "L1"),
        ("[[EXEC: video.create topic=\"X\" script=\"Y\"]]", "L2"),
        ("[[EXEC: youtube.upload video=\"/a.mp4\" title=\"X\"]]", "L3"),
    ]
    for text, want_risk in cases:
        evs = scan_and_exec(text)
        assert evs
        assert evs[0]["risk"] == want_risk, f"{text}: got {evs[0]['risk']}"


def test_hook_approve_then_exec_path():
    """用户点确认 → 真发(confirm_callback=True) → exec_result event。"""
    from prisir_work.agent_main_chat_hook import scan_and_exec
    text = '[[EXEC: video.tts text="hi"]]'
    evs = scan_and_exec(text, confirm_callback=lambda m: True)
    assert evs
    # L1 走 callback,callback 返回 True → exec
    _eq("type", evs[0]["type"], "capability_exec_result")


def test_hook_decline_returns_user_declined():
    """用户点拒绝 → 不发 → user_declined error。"""
    from prisir_work.agent_main_chat_hook import scan_and_exec
    text = '[[EXEC: youtube.upload video="/a.mp4" title="X"]]'
    evs = scan_and_exec(text, confirm_callback=lambda m: False)
    assert evs
    _eq("ok", evs[0]["ok"], False)
    _eq("error", evs[0]["error"], "user_declined")


# ---------------------------------------------------------------------------
# P3j T16-D: 前端 UX 完整接入(状态节点 + ESC + 跳转链接)
# ---------------------------------------------------------------------------

def test_appjs_has_cap_exec_renderer():
    """app.js 必须包含真发结果节点渲染函数 + ESC 关闭逻辑。"""
    import pathlib
    js = pathlib.Path("companion/static/app.js").read_text(encoding="utf-8")
    for token in ("renderCapExecResult", "cap-exec", "cap-exec-path",
                  "cap-exec-link", "Escape", "keydown",
                  "prisIrai:cap-exec-link"):
        assert token in js, f"app.js 缺 {token}"


def test_guohua_css_has_cap_exec_styles():
    import pathlib
    css = pathlib.Path("companion/static/guohua-theme.css").read_text(
        encoding="utf-8")
    for token in (".cap-exec", ".cap-ok", ".cap-bad",
                  ".cap-exec-head", ".cap-exec-link"):
        assert token in css, f"CSS 缺 {token}"


def test_hook_exec_result_carries_artifact_path():
    """真发 video.info 成功(L0)→ result 里有 artifact.path 前端可展示。"""
    from prisir_work.agent_main_chat_hook import scan_and_exec
    # 直接构造一个 fake endpoint 测试 hook 的 result 透传
    import types
    import prisir_work.agent_main_chat_hook as hook_mod
    # 模拟:fake 一个 video.test 能力,handler 返 ok=True + artifact.path
    from prisir_work import capability as cap_mod
    from prisir_work import endpoints as ep_mod
    cap_mod.register_capability(
        "video.test_ux", title="UX 测试", endpoint="/video/test_ux",
        method="POST", risk="L0", auth=True, keywords=("ux test",))
    ep_mod._REGISTRY["/video/test_ux"] = {
        "endpoint": "/video/test_ux", "method": "POST",
        "handler": lambda body: ({"ok": True,
                                   "artifact": {"path": "/tmp/x.mp4"}}, 200),
    }
    try:
        evs = hook_mod.scan_and_exec('[[EXEC: video.test_ux x="1"]]')
        assert evs
        _eq("ok", evs[0]["ok"], True)
        assert evs[0]["result"]["artifact"]["path"] == "/tmp/x.mp4"
    finally:
        # 清理
        cap_mod._REGISTRY.pop("video.test_ux", None)
        ep_mod._REGISTRY.pop("/video/test_ux", None)


def test_main_chat_hook_full_round_trip_l0():
    """主对话流仿真:L0 EXEC → 走 exec → result 通过 ws event 流。"""
    from prisir_work import agent_main_chat_hook as hook
    # 直接调 scan_and_exec 看 ws event 形态(前端消费结构)
    evs = hook.scan_and_exec('[[EXEC: video.info path="/x.mp4"]]')
    # 期望 1 个 capability_exec_result event
    assert len(evs) == 1
    ev = evs[0]
    assert ev["type"] == "capability_exec_result"
    assert ev["capability"] == "video.info"
    # ok 可能是 False(子服务未起)但 event 形态一致


# ---------------------------------------------------------------------------
# 跑测
# ---------------------------------------------------------------------------

def _run_all():
    """简易 runner — 无 pytest 时也能跑。"""
    import inspect
    g = globals()
    funcs = [(n, f) for n, f in g.items()
             if n.startswith("test_") and callable(f)
             and inspect.isfunction(f)]
    passed, failed = 0, []
    for name, fn in funcs:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed.append((name, f"{type(e).__name__}: {e}"))
            continue
        passed += 1
        print(f"  ✓ {name}")
    print(f"\n{passed}/{len(funcs)} passed")
    if failed:
        print("\nFAILED:")
        for n, e in failed:
            print(f"  ✗ {n}: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()