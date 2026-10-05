"""
test_seed.py — seed.py 解析器单元测试(2026-10-06 P3.0.1)

覆盖:
- _parse_summary: pytest summary line 多格式
- _parse_failed: short test summary section
- _categorize: pre-broken / ship-sync-gap / real 分类
- _norm: 路径分隔符跨平台

跑法:python -m pytest tests/test_seed.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 把主仓根加进 path 以便 import seed
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from seed import (  # noqa: E402
    _parse_summary,
    _parse_failed,
    _categorize,
    _norm,
    PRE_BROKEN,
    SHIP_SYNC_GAPS,
)


class TestParseSummary:
    """pytest summary line 多格式解析。"""

    def test_standard_format(self):
        """'55 failed, 1109 passed, 5 skipped in 9m29s' 标准格式。"""
        out = "55 failed, 1109 passed, 5 skipped in 9m29s"
        s = _parse_summary(out)
        assert s["failed"] == 55
        assert s["passed"] == 1109
        assert s["skipped"] == 5
        assert s["errors"] == 0
        assert s["deselected"] == 0

    def test_with_errors(self):
        """带 errors: '52 failed, 1109 passed, 4 skipped, 1 warning, 1 error in 491s'"""
        out = "52 failed, 1109 passed, 4 skipped, 1 warning, 1 error in 491s"
        s = _parse_summary(out)
        assert s["failed"] == 52
        assert s["passed"] == 1109
        assert s["skipped"] == 4
        assert s["errors"] == 1

    def test_with_deselected(self):
        """带 deselected(被 -k 过滤掉的)。"""
        out = "6 passed, 12 deselected in 0.42s"
        s = _parse_summary(out)
        assert s["passed"] == 6
        assert s["deselected"] == 12

    def test_no_match_returns_zeros(self):
        """无 summary line → 全 0。"""
        s = _parse_summary("nothing here")
        assert all(v == 0 for v in s.values())


class TestParseFailed:
    """pytest short test summary section 解析。"""

    def test_extracts_failed_tests(self):
        """从标准 pytest 输出提取 FAILED tests。"""
        out = """
=========================== short test summary info ===========================
FAILED tests/test_x.py::test_1 - AssertionError: foo
FAILED tests/test_y.py::test_2 - AttributeError: 'NoneType'
=========================== 2 failed, 100 passed in 1m ============================
"""
        failed = _parse_failed(out)
        assert len(failed) == 2
        assert "tests/test_x.py::test_1" in failed[0]
        assert "tests/test_y.py::test_2" in failed[1]

    def test_extracts_error_tests(self):
        """ERROR 行(模块导入错)也算 fail。"""
        out = """
=========================== short test summary info ===========================
ERROR tests/test_e2e.py - ModuleNotFoundError: foo
=========================== 1 error in 0.1s ============================
"""
        failed = _parse_failed(out)
        assert len(failed) == 1
        assert "tests/test_e2e.py" in failed[0]

    def test_empty_returns_empty(self):
        """无 fail section → 空 list。"""
        assert _parse_failed("all passed") == []


class TestCategorize:
    """pre-broken / ship-sync-gap / real 分类。"""

    def test_pre_broken_detected(self):
        """ModuleNotFoundError 落到 pre_broken 桶。"""
        failed = [
            "tests/prisiragent_cli_db_test.py - ModuleNotFoundError: prisiragent_cli",
            "tests/prisiragent_handoff_test.py - ModuleNotFoundError: prisIragent_web",
        ]
        cats = _categorize(failed)
        assert len(cats["pre_broken"]) == 2
        assert len(cats["real"]) == 0

    def test_ship_sync_gap_detected(self):
        """wfmodal hash + ext_respawn 落到 ship_sync_gap 桶。"""
        failed = [
            "tests/test_electron_subwindows.py::TestPrisirAgentWebWfmodalHash::test_x",
            "tests/test_electron_subwindows.py::TestExtRespawnHotfix::test_y",
        ]
        cats = _categorize(failed)
        assert len(cats["ship_sync_gap"]) == 2
        assert len(cats["real"]) == 0

    def test_real_is_default(self):
        """其他 test 落到 real 桶。"""
        failed = ["tests/test_preset_travel.py::test_T1 - AssertionError: ..."]
        cats = _categorize(failed)
        assert len(cats["real"]) == 1
        assert len(cats["pre_broken"]) == 0
        assert len(cats["ship_sync_gap"]) == 0


class TestNorm:
    """路径分隔符跨平台。"""

    def test_posix_path(self):
        assert _norm("tests/foo/bar.py") == "tests/foo/bar.py"

    def test_windows_path(self):
        """反斜杠统一成正斜杠,跨平台 prefix 匹配用。"""
        assert _norm("tests\\foo\\bar.py") == "tests/foo/bar.py"

    def test_mixed_separators(self):
        """混合斜杠也归一化。"""
        assert _norm("tests/foo\\bar.py") == "tests/foo/bar.py"


class TestConstants:
    """sanity check on declared tables。"""

    def test_pre_broken_files_exist_or_skip(self):
        """PRE_BROKEN 列出的 file 确实在 tests/ 或被归档。
        不强求真实存在(music 已归档后),只保证 list 不空。
        """
        assert len(PRE_BROKEN) >= 3
        assert "tests/prisiragent_cli_db_test.py" in PRE_BROKEN

    def test_ship_sync_gap_classes(self):
        """SHIP_SYNC_GAPS 列了 audit 分支 ship 漏同步的 class。"""
        assert len(SHIP_SYNC_GAPS) >= 2
        classes = [cls for _, cls in SHIP_SYNC_GAPS]
        assert "TestPrisirAgentWebWfmodalHash" in classes
        assert "TestExtRespawnHotfix" in classes
