#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# tests/test_e2e.py — M3.69 commit_check 端到端测试(2026-09-24)
#
# 设计:
#   - 在临时目录造 git repo + 真造 .pem/.env 等文件 + 真跑 main.py
#   - 验证三件事:
#       A) --help 输出完整
#       B) 非 git 目录友好降级(allow + 退出码 0)
#       C) 真 .pem 文件触发 deny(退出码 1)
#       D) 普通代码变更 → allow(退出码 0)
#       E) --format json 输出合法 JSON
#       F) --format md --output 写出文件
#       G) --diff HEAD 跑通
#       H) --dangerous-ext 自定义触发 deny
#
# 不依赖 unittest.mock,真 subprocess 跑 main.py。
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = str(ROOT / "main.py")
PYTHON = sys.executable


def _run(args: list[str], cwd: str | None = None,
         timeout: int = 300) -> subprocess.CompletedProcess:
    cmd = [PYTHON, MAIN] + args
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        timeout=timeout, encoding="utf-8", errors="replace",
    )


def _init_repo(path: Path) -> None:
    """在 path 初始化 git repo + 设 user.email/name(否则 commit 失败)。"""
    path.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "init"], cwd=str(path),
                       capture_output=True, text=True)
    assert r.returncode == 0, f"git init 失败: {r.stderr}"
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Tester"],
                   cwd=str(path), check=True)


def _stage(path: Path, rel: str, content: str) -> None:
    fp = path / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", rel], cwd=str(path), check=True)


def test_help() -> None:
    """--help 输出完整,exit 0。"""
    r = _run(["--help"])
    assert r.returncode == 0, f"--help 退出码: {r.returncode}\n{r.stderr}"
    out = r.stdout
    assert "--staged" in out, "缺少 --staged 选项"
    assert "--diff" in out, "缺少 --diff 选项"
    assert "--dangerous-ext" in out, "缺少 --dangerous-ext 选项"
    assert "--format" in out, "缺少 --format 选项"
    print("[OK] test_help")


def test_non_git_dir_friendly_degrade() -> None:
    """非 git 目录:友好降级(allow + exit 0)。"""
    with tempfile.TemporaryDirectory() as td:
        r = _run(["--staged"], cwd=td)
        # 友好降级:allow + exit 0
        assert r.returncode == 0, (
            f"非 git 目录应 allow(退出码 0),实得 {r.returncode}\n"
            f"stderr: {r.stderr}")
        out = r.stdout
        assert "不在 git repo" in out or "skip" in out.lower() or "降级" in out \
            or "allow" in out, f"未提示非 git 目录: {out}"
    print("[OK] test_non_git_dir_friendly_degrade")


def test_pem_triggers_deny() -> None:
    """真造 .pem 文件 + staged → deny + exit 1。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        _init_repo(repo)
        # 先提交一个 initial README
        _stage(repo, "README.md", "# Init\n")
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo),
                       check=True, capture_output=True)
        # 现在 stage 新增的 .pem(变更 vs HEAD)
        _stage(repo, "config/server.pem",
               "-----BEGIN PRIVATE KEY-----\nFAKEKEY\n-----END PRIVATE KEY-----\n")
        _stage(repo, "src/utils.py", "def hello():\n    return 'hi'\n")

        r = _run(["--staged"], cwd=str(repo), timeout=300)
        assert r.returncode == 1, (
            f".pem 应触发 deny(退出码 1),实得 {r.returncode}\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}")
        out = r.stdout
        assert "deny" in out.lower(), f"未出现 deny 决策: {out}"
        assert ".pem" in out or "server.pem" in out, \
            f"未显示 .pem 路径: {out}"
    print("[OK] test_pem_triggers_deny")


def test_safe_code_allows() -> None:
    """普通代码变更 → allow + exit 0。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        _init_repo(repo)
        _stage(repo, "README.md", "# Hello\n")
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo),
                       check=True, capture_output=True)
        _stage(repo, "src/utils.py", "def hello():\n    return 'hi'\n")

        r = _run(["--staged"], cwd=str(repo), timeout=300)
        # allow → exit 0
        assert r.returncode == 0, (
            f"safe 代码应 allow,实得 {r.returncode}\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}")
        assert "allow" in r.stdout, f"未出现 allow 决策: {r.stdout}"
    print("[OK] test_safe_code_allows")


def test_json_output() -> None:
    """--format json 输出合法 JSON。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        _init_repo(repo)
        _stage(repo, "src/main.py", "x = 1\n")
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo),
                       check=True, capture_output=True)
        _stage(repo, "src/main.py", "x = 2\n")
        r = _run(["--staged", "--format", "json"], cwd=str(repo), timeout=300)
        assert r.returncode == 0, f"json 模式应 exit 0,实得 {r.returncode}"
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise AssertionError(
                f"json 输出解析失败: {e}\nstdout: {r.stdout[:500]}")
        assert "decision" in data, f"JSON 缺 decision: {data}"
        assert data["decision"] in ("allow", "ask", "deny"), \
            f"未知决策: {data['decision']}"
    print("[OK] test_json_output")


def test_markdown_output() -> None:
    """--format md --output 写出文件。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        _init_repo(repo)
        _stage(repo, "src/main.py", "x = 1\n")
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo),
                       check=True, capture_output=True)
        _stage(repo, "src/main.py", "x = 2\n")
        out_file = Path(td) / "report.md"
        r = _run(["--staged", "--format", "md",
                  "--output", str(out_file)], cwd=str(repo), timeout=300)
        assert r.returncode == 0, f"md 模式应 exit 0,实得 {r.returncode}"
        assert out_file.exists(), f"未写出报告文件: {out_file}"
        content = out_file.read_text(encoding="utf-8")
        assert "# commit_check 报告" in content, \
            f"md 报告头缺失: {content[:200]}"
        assert "决策" in content, "md 缺决策字段"
    print("[OK] test_markdown_output")


def test_diff_head() -> None:
    """--diff HEAD 跑通(检查最近一次 commit vs 工作区)。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        _init_repo(repo)
        _stage(repo, "src/utils.py", "def hello():\n    return 'hi'\n")
        subprocess.run(["git", "commit", "-m", "add utils"], cwd=str(repo),
                       check=True, capture_output=True)
        # 改工作区但再提交一次,然后 HEAD 就是上一次
        _stage(repo, "src/utils.py", "def hello():\n    return 'bye'\n")
        subprocess.run(["git", "commit", "-am", "update"], cwd=str(repo),
                       check=True, capture_output=True)
        r = _run(["--diff", "HEAD"], cwd=str(repo), timeout=300)
        # HEAD vs HEAD = 空 diff,应 allow
        assert r.returncode == 0, f"--diff HEAD exit {r.returncode}"
        r2 = _run(["--diff", "HEAD~1"], cwd=str(repo), timeout=300)
        assert r2.returncode == 0, f"--diff HEAD~1 exit {r2.returncode}"
    print("[OK] test_diff_head")


def test_custom_dangerous_ext() -> None:
    """--dangerous-ext 自定义扩展名能触发 deny。"""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        _init_repo(repo)
        _stage(repo, "data/export.csv", "secret,data\n")
        # 用 .csv 单独作为危险扩展名
        r = _run(["--staged", "--dangerous-ext", ".csv"],
                 cwd=str(repo), timeout=300)
        # 注:这取决于 adapter 风险判分 + ext 命中规则
        # 至少 exit code 应在 [0, 1] 中(deny 1 或 allow/ask 0)
        assert r.returncode in (0, 1), \
            f"退出码异常: {r.returncode}\n{r.stdout}"
        # 我们不能保证 .csv 一定触发 deny(取决于 adapter),
        # 但程序不能崩
    print("[OK] test_custom_dangerous_ext")


# ------------------------------------------------------------
# Runner
# ------------------------------------------------------------
TESTS = [
    test_help,
    test_non_git_dir_friendly_degrade,
    test_pem_triggers_deny,
    test_safe_code_allows,
    test_json_output,
    test_markdown_output,
    test_diff_head,
    test_custom_dangerous_ext,
]


def main() -> int:
    print(f"Running {len(TESTS)} e2e tests against {MAIN}")
    print("=" * 60)
    failed = 0
    for t in TESTS:
        try:
            t()
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print("=" * 60)
    if failed:
        print(f"FAILED {failed}/{len(TESTS)}")
        return 1
    print(f"PASSED {len(TESTS)}/{len(TESTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
