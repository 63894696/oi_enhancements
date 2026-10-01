"""test_swarm_tldr.py — P5-SwarmTLDR(2026-10-02)单测

借鉴 jcode-swarm-core 派单协议,验证 dev_dispatch.py 新增的:
- parse_swarm_tldr(content) -> str | None
- make_swarm_tldr(tldr) -> str
- validate_swarm_tldr(content, tldr=None) -> (bool, reason)
- validate_completion_report(report) -> (bool, reason)
- build_completion_skeleton(tldr, summary, files_touched) -> str
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dev_dispatch import (  # noqa: E402
    MAX_SWARM_COMPLETION_REPORT_CHARS,
    MAX_SWARM_TLDR_CHARS,
    SWARM_COMPLETION_REPORT_MARKER,
    SWARM_TLDR_REQUIRED_OVER_CHARS,
    build_completion_skeleton,
    make_swarm_tldr,
    parse_swarm_tldr,
    validate_completion_report,
    validate_swarm_tldr,
)


# ───────── parse_swarm_tldr ─────────
def test_parse_swarm_tldr_simple():
    """基本 case:行首 `tldr: 简短摘要`"""
    content = "tldr: 简短摘要\n这是任务详情"
    assert parse_swarm_tldr(content) == "简短摘要"


def test_parse_swarm_tldr_multiline_anchor():
    """多行 content:行首锚定(不应误命中正文里的 tldr)"""
    content = "任务标题\n第一段详情\ntldr: 第二行\n更后面的内容"
    assert parse_swarm_tldr(content) == "第二行"


def test_parse_swarm_tldr_no_decl():
    """无 tldr 行 → None"""
    assert parse_swarm_tldr("只是一段描述,没有 tldr") is None
    assert parse_swarm_tldr("") is None
    assert parse_swarm_tldr(None) is None


def test_parse_swarm_tldr_inline_ignored():
    """正文里行中提到 tldr 不算(行首锚定语义)"""
    content = "请写一份 tldr: 摘要 的报告"
    assert parse_swarm_tldr(content) is None


def test_parse_swarm_tldr_chinese_colon():
    """中文冒号也支持"""
    content = "tldr:简短摘要"
    assert parse_swarm_tldr(content) == "简短摘要"


def test_parse_swarm_tldr_normalize_whitespace():
    """解析时自动 normalize(行内多空白压单空格)"""
    # tldr 是单行;行内的 tab/多空格压成单空格,但不跨行。
    content = "tldr:  关键\t摘要   内容\n这是任务详情"
    assert parse_swarm_tldr(content) == "关键 摘要 内容"


def test_parse_swarm_tldr_case_insensitive():
    """大小写不敏感"""
    assert parse_swarm_tldr("TLDR: 大写也行") == "大写也行"
    assert parse_swarm_tldr("Tldr: 混合写") == "混合写"


# ───────── make_swarm_tldr ─────────
def test_make_swarm_tldr_normalize_whitespace():
    """trim + 多空白压单空格"""
    assert make_swarm_tldr("  hi\n\n  world  ") == "hi world"


def test_make_swarm_tldr_truncate_to_200():
    """超 200 chars 截到 200"""
    long = "a" * 300
    out = make_swarm_tldr(long)
    assert len(out) == MAX_SWARM_TLDR_CHARS == 200
    assert out == "a" * 200


def test_make_swarm_tldr_truncate_after_normalize():
    """截断在 normalize 之后(空格压单后再截)"""
    # normalize 前看起来 < 200,normalize 后可能 > 200 也好,反正最后 ≤ 200
    weird = "  " + ("x " * 150)  # normalize 后 150x + 单空格 = 约 150
    out = make_swarm_tldr(weird)
    assert len(out) <= MAX_SWARM_TLDR_CHARS


def test_make_swarm_tldr_empty():
    """空串 / None → 空串"""
    assert make_swarm_tldr("") == ""
    assert make_swarm_tldr("   ") == ""


# ───────── validate_swarm_tldr ─────────
def test_validate_short_body_no_tldr_ok():
    """短 body(<240)即使没 tldr 也 OK"""
    short = "just a short task"
    ok, reason = validate_swarm_tldr(short)
    assert ok is True
    assert "可选" in reason or "short" in reason.lower() or "≤" in reason


def test_validate_long_body_no_tldr_fail():
    """长 body(>240)无 tldr → fail 且 reason 明确说「缺 tldr」"""
    long = "x" * 500
    ok, reason = validate_swarm_tldr(long)
    assert ok is False
    assert "缺 tldr" in reason
    assert "240" in reason


def test_validate_long_body_with_tldr_ok():
    """长 body + 合法 tldr → ok"""
    long_with_tldr = "x" * 500
    tldr = "关键摘要"
    ok, reason = validate_swarm_tldr(long_with_tldr, tldr)
    assert ok is True
    assert "ok" in reason.lower()


def test_validate_long_body_tldr_too_long_fail():
    """长 body + tldr > 200 chars → fail"""
    long_with_tldr = "x" * 500
    big_tldr = "y" * 300
    ok, reason = validate_swarm_tldr(long_with_tldr, big_tldr)
    assert ok is False
    assert "超" in reason and "200" in reason


def test_validate_long_body_auto_parses_tldr():
    """长 body 没传 tldr 时,自动 parse_swarm_tldr(content)"""
    long = "x" * 500 + "\ntldr: 简短摘要\n" + "y" * 100
    ok, reason = validate_swarm_tldr(long)  # 不传 tldr,自动从 content 抽
    assert ok is True, reason


def test_validate_long_body_whitespace_only_tldr_fail():
    """长 body + tldr 仅空白 → fail(防止骗过去)"""
    long = "x" * 500
    ok, reason = validate_swarm_tldr(long, "    ")
    assert ok is False
    assert "空白" in reason or "无效" in reason


# ───────── validate_completion_report ─────────
def test_validate_completion_report_ok():
    """以 marker 开头的报告 → ok"""
    report = SWARM_COMPLETION_REPORT_MARKER + "\n做了 X Y Z"
    ok, reason = validate_completion_report(report)
    assert ok is True
    assert "ok" in reason.lower()


def test_validate_completion_report_no_marker_fail():
    """缺 marker → fail 且 reason 给前 60 字符片段"""
    ok, reason = validate_completion_report("not marker here")
    assert ok is False
    assert "缺 marker" in reason
    assert "SWARM COMPLETION REPORT REQUIRED" in reason


def test_validate_completion_report_too_long_fail():
    """超 4000 chars → fail"""
    long = SWARM_COMPLETION_REPORT_MARKER + "\n" + ("z" * 4500)
    ok, reason = validate_completion_report(long)
    assert ok is False
    assert "4000" in reason
    assert "超" in reason


def test_validate_completion_report_empty_fail():
    """空报告 → fail"""
    ok, reason = validate_completion_report("")
    assert ok is False
    assert "空" in reason


def test_validate_completion_report_bom_tolerated():
    """BOM 容差(Windows 复制粘贴易带 BOM)"""
    report = "﻿" + SWARM_COMPLETION_REPORT_MARKER + "\n正文"
    ok, reason = validate_completion_report(report)
    assert ok is True, reason


# ───────── build_completion_skeleton ─────────
def test_build_completion_skeleton_full():
    """完整三段 → 以 marker 开头 + 含 tldr + 含 summary + 含文件列表"""
    skel = build_completion_skeleton(
        tldr="核心改动",
        summary="做了 A、B、C",
        files_touched=["dev_dispatch.py", "prisIragent_dev_consumer.py"],
    )
    assert skel.startswith(SWARM_COMPLETION_REPORT_MARKER)
    assert "tldr: 核心改动" in skel
    assert "summary: 做了 A、B、C" in skel
    assert "files_touched: dev_dispatch.py, prisIragent_dev_consumer.py" in skel


def test_build_completion_skeleton_only_tldr():
    """仅 tldr(纯文本/调研任务)→ 不含 summary/files_touched"""
    skel = build_completion_skeleton(tldr="调研结论", summary="", files_touched=[])
    assert skel.startswith(SWARM_COMPLETION_REPORT_MARKER)
    assert "tldr: 调研结论" in skel
    assert "summary:" not in skel
    assert "files_touched:" not in skel


def test_build_completion_skeleton_validates_pass():
    """skeleton 自身的 validate_completion_report 应 pass"""
    skel = build_completion_skeleton("关键摘要", "简要说明", ["a.py"])
    ok, reason = validate_completion_report(skel)
    assert ok is True, reason


def test_build_completion_skeleton_normalizes_tldr():
    """build_completion_skeleton 内部 normalize tldr"""
    skel = build_completion_skeleton("  关键\n\n摘要  ", "", [])
    assert "tldr: 关键 摘要" in skel


# ───────── 常量边界 ─────────
def test_constants_match_jcode_swarm_core():
    """常量值与 jcode-swarm-core lib.rs 顶部常量段一致(借鉴不抄 1000 上限)"""
    assert SWARM_TLDR_REQUIRED_OVER_CHARS == 240
    assert MAX_SWARM_TLDR_CHARS == 200
    assert SWARM_COMPLETION_REPORT_MARKER == "SWARM COMPLETION REPORT REQUIRED"
    assert MAX_SWARM_COMPLETION_REPORT_CHARS == 4000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
