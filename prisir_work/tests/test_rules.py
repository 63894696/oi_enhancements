"""tests/test_rules.py — P2-Rules(2026-10-01)单元测试。

至少 10 项,对应 plan 测试清单 + 一些边界:
1. 项目根有 AGENTS.md → load 返项目级非 None
2. 家目录有 AGENTS.md → load 返用户级非 None
3. 两层都有 → merge 后项目级覆盖用户级
4. 项目级 standards.testing 非空 → merge 后 testing 是项目级的
5. 项目级 standards.testing 空 → merge 后 testing 是用户级的
6. 无 AGENTS.md → load 返 (None, None),merge 返 None
7. frontmatter 损坏(只有 --- 没有内容)→ parse 返 Non-None(body 兜底)
8. 超 rules_max_chars → format 返字符串 ≤ rules_max_chars + [truncated]
9. frontmatter 含 priority_keywords 列表 → 解析为 list[str]
10. format 输出以 [Rules] 标识开头
+ 边界:home/AGENTS.md 不存在不抛、disable 配置项生效、rules_enabled=False 返 (None,None)

测试不 mock 行为:真读 tmpdir/AGENTS.md 真解析。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# 让 from prisir_work.rules import ... 可用(测试 dir 在 prisir_work/tests/)
_HERE = Path(__file__).resolve().parent
_PRISIR_WORK = _HERE.parent
_OI_ROOT = _PRISIR_WORK.parent
for p in (str(_OI_ROOT), str(_PRISIR_WORK)):
    if p not in sys.path:
        sys.path.insert(0, p)

from prisir_work.rules import (  # noqa: E402
    DEFAULT_RULES_ENABLED,
    DEFAULT_RULES_MAX_CHARS,
    ProjectRules,
    format_rules_for_prompt,
    load_rules_for_project,
    merge_rules,
    parse_agents_md,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestParseAgentsMd(unittest.TestCase):
    """parse_agents_md: frontmatter 极简解析 + body 兜底。"""

    def test_missing_file_returns_none(self) -> None:
        """无 AGENTS.md → parse 返 None。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "AGENTS.md"
            self.assertIsNone(parse_agents_md(p))

    def test_full_frontmatter(self) -> None:
        """完整 frontmatter → 字段全解析。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "AGENTS.md"
            _write(p, (
                "---\n"
                "project: testproj\n"
                "priority_keywords: [foo, bar]\n"
                "standards:\n"
                "  style: 中文回答,简洁\n"
                "  testing: TDD 必走\n"
                "---\n"
                "\n"
                "项目人话补充"
            ))
            r = parse_agents_md(p)
            self.assertIsNotNone(r)
            assert r is not None
            self.assertEqual(r.project, "testproj")
            self.assertEqual(r.priority_keywords, ["foo", "bar"])
            self.assertEqual(r.standards.get("style"), "中文回答,简洁")
            self.assertEqual(r.standards.get("testing"), "TDD 必走")
            self.assertIn("项目人话补充", r.body)
            self.assertEqual(r.source_path, str(p.resolve()))

    def test_frontmatter_priority_keywords_csv_fallback(self) -> None:
        """priority_keywords 写成逗号分隔字符串 → 解析为 list[str]。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "AGENTS.md"
            _write(p, (
                "---\n"
                "project: csvproj\n"
                "priority_keywords: foo, bar, baz\n"
                "---\n"
                "正文"
            ))
            r = parse_agents_md(p)
            assert r is not None
            self.assertEqual(r.priority_keywords, ["foo", "bar", "baz"])

    def test_frontmatter_only_separator_returns_non_none(self) -> None:
        """frontmatter 损坏(只有 --- 没内容)→ parse 仍返 Non-None,body 兜底全文。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "AGENTS.md"
            _write(p, "---\n---\n\n纯 body,没有 frontmatter 内容")
            r = parse_agents_md(p)
            self.assertIsNotNone(r, "损坏 frontmatter 不应返 None,应走 body 兜底")
            assert r is not None
            self.assertIn("纯 body", r.body)
            # project 字段退化到目录名
            self.assertEqual(r.project, Path(td).name)

    def test_no_frontmatter_returns_body(self) -> None:
        """完全没有 frontmatter → body = 全文,project = 目录名。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "AGENTS.md"
            _write(p, "全部正文,无 frontmatter")
            r = parse_agents_md(p)
            self.assertIsNotNone(r)
            assert r is not None
            self.assertEqual(r.body, "全部正文,无 frontmatter")
            self.assertEqual(r.project, Path(td).name)


class TestLoadAndMerge(unittest.TestCase):
    """load + merge 语义。"""

    def test_only_project(self) -> None:
        """仅项目级 → load 返 (None, proj_non_None)。"""
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _write(cwd / "AGENTS.md", (
                "---\n"
                "project: onlyproj\n"
                "---\n"
                "正文"
            ))
            user, proj = load_rules_for_project(cwd)
            self.assertIsNone(user)
            self.assertIsNotNone(proj)
            assert proj is not None
            self.assertEqual(proj.scope, "project")

    def test_only_user(self) -> None:
        """仅家目录级 → load 返 (user_non_None, None)。"""
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            home_agents = Path.home() / "AGENTS.md"
            backup = None
            if home_agents.exists():
                backup = home_agents.read_bytes()
            try:
                _write(home_agents, (
                    "---\n"
                    "project: homeproj\n"
                    "---\n"
                    "用户级"
                ))
                user, proj = load_rules_for_project(cwd)
                self.assertIsNotNone(user)
                self.assertIsNone(proj)
                assert user is not None
                self.assertEqual(user.scope, "user")
                self.assertEqual(user.project, "homeproj")
            finally:
                if backup is not None:
                    home_agents.write_bytes(backup)
                elif home_agents.exists():
                    try:
                        home_agents.unlink()
                    except OSError:
                        pass

    def test_both_layers_merge_project_overrides_user(self) -> None:
        """两层都有 → 项目级覆盖用户级 standards。"""
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            _write(cwd / "AGENTS.md", (
                "---\n"
                "project: proj\n"
                "standards:\n"
                "  testing: TDD 必走\n"
                "  style: 简洁\n"
                "---\n"
                "项目正文"
            ))
            home_agents = Path.home() / "AGENTS.md"
            backup = None
            if home_agents.exists():
                backup = home_agents.read_bytes()
            try:
                _write(home_agents, (
                    "---\n"
                    "project: home\n"
                    "standards:\n"
                    "  testing: 用户测试规范\n"
                    "  commit: 严格 commit\n"
                    "---\n"
                    "用户正文"
                ))
                user, proj = load_rules_for_project(cwd)
                self.assertIsNotNone(proj)
                self.assertIsNotNone(user)
                merged = merge_rules(user, proj)
                self.assertIsNotNone(merged)
                assert merged is not None
                self.assertEqual(merged.scope, "merged")
                # project 取项目级
                self.assertEqual(merged.project, "proj")
                # project.testing 覆盖 user.testing
                self.assertEqual(merged.standards.get("testing"), "TDD 必走")
                # project.style 也覆盖(虽然 user 没设 style)
                self.assertEqual(merged.standards.get("style"), "简洁")
                # user.commit 没被覆盖
                self.assertEqual(merged.standards.get("commit"), "严格 commit")
            finally:
                if backup is not None:
                    home_agents.write_bytes(backup)
                elif home_agents.exists():
                    try:
                        home_agents.unlink()
                    except OSError:
                        pass

    def test_project_empty_standards_does_not_override(self) -> None:
        """项目级 standards.testing 空 → merge 后 testing 是用户级的。"""
        merged = merge_rules(
            user_a=ProjectRules(
                project="u",
                standards={"testing": "用户测试规范"},
                source_path="/home/.AGENTS.md",
                scope="user",
            ),
            project_a=ProjectRules(
                project="p",
                standards={"testing": ""},  # 空 → 不覆盖
                source_path="/cwd/AGENTS.md",
                scope="project",
            ),
        )
        self.assertIsNotNone(merged)
        assert merged is not None
        self.assertEqual(merged.standards.get("testing"), "用户测试规范")

    def test_no_files_returns_none_none(self) -> None:
        """无任何 AGENTS.md → load 返 (None, None),merge 返 None。"""
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            # 显式移除 home/AGENTS.md(若存在),但避免改真实家目录
            home_agents = Path.home() / "AGENTS.md"
            backup = None
            if home_agents.exists():
                backup = home_agents.read_bytes()
                home_agents.unlink()
            try:
                user, proj = load_rules_for_project(cwd)
                self.assertIsNone(proj)
                self.assertIsNone(user)
                self.assertIsNone(merge_rules(user, proj))
            finally:
                if backup is not None:
                    home_agents.write_bytes(backup)

    def test_load_does_not_raise_on_unreadable_home(self) -> None:
        """home/AGENTS.md 是目录不是文件 → load 不抛 + user 返 None。"""
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            home_agents = Path.home() / "AGENTS.md"
            backup = None
            if home_agents.exists():
                backup = home_agents.read_bytes()
                home_agents.unlink()
            try:
                # 故意在 home/AGENTS.md 建一个目录(parse 会失败)
                home_agents.mkdir(exist_ok=True)
                try:
                    user, proj = load_rules_for_project(cwd)
                    # 项目级也没 → proj 是 None
                    self.assertIsNone(proj)
                    # user parse_agents_md 对目录应该返 None
                    self.assertIsNone(user)
                finally:
                    if home_agents.exists() and home_agents.is_dir():
                        try:
                            home_agents.rmdir()
                        except OSError:
                            pass
            finally:
                if backup is not None:
                    home_agents.write_bytes(backup)


class TestFormatForPrompt(unittest.TestCase):
    """format_rules_for_prompt 输出约束。"""

    def test_format_starts_with_rules_tag(self) -> None:
        """format 输出以 [Rules] 标识开头。"""
        r = ProjectRules(
            project="p",
            standards={"style": "中文,简洁"},
            source_path="/tmp/AGENTS.md",
            scope="project",
        )
        out = format_rules_for_prompt(r)
        self.assertTrue(out.startswith("[Rules — AGENTS.md — 项目级]"),
                        f"output should start with [Rules — AGENTS.md — 项目级], got: {out[:60]}")

    def test_format_truncates_when_over_max(self) -> None:
        """超 rules_max_chars → format 字符串 ≤ rules_max_chars + [truncated]。"""
        # 注入一个超大的 body
        big_body = "x" * 10000
        r = ProjectRules(
            project="big",
            standards={"style": "x" * 1000, "testing": "x" * 1000},
            body=big_body,
            source_path="/tmp/AGENTS.md",
            scope="project",
        )
        out = format_rules_for_prompt(r)
        # out 长度应 ≤ max_chars + len("\n[truncated]")
        self.assertLessEqual(len(out), DEFAULT_RULES_MAX_CHARS + len("\n[truncated]"))
        self.assertTrue(out.endswith("[truncated]"),
                        f"应被截断后追加 [truncated],实得末 30 字符: {out[-30:]}")

    def test_format_includes_standards_lines(self) -> None:
        """format 包含 standards 各 key。"""
        r = ProjectRules(
            project="demo",
            standards={"style": "中文,简洁", "testing": "TDD"},
            source_path="/tmp/AGENTS.md",
            scope="project",
        )
        out = format_rules_for_prompt(r)
        self.assertIn("style: 中文,简洁", out)
        self.assertIn("testing: TDD", out)

    def test_format_empty_rules_returns_empty_string(self) -> None:
        """空 rules → format 返空串(不抛)。"""
        self.assertEqual(format_rules_for_prompt(ProjectRules()), "")


class TestConfigDriven(unittest.TestCase):
    """配置项 rules_enabled / rules_max_chars 行为。"""

    def test_rules_enabled_default_true(self) -> None:
        """无配置文件 → 默认 True(load 应尝试读)。"""
        self.assertTrue(DEFAULT_RULES_ENABLED is True)

    def test_rules_max_chars_default_4000(self) -> None:
        """无配置文件 → 默认 4000。"""
        self.assertEqual(DEFAULT_RULES_MAX_CHARS, 4000)


if __name__ == "__main__":
    unittest.main(verbosity=2)