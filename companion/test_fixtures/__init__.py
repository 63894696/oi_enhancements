"""test_fixtures — M3.60 共享评测题集(2026-09-23)

目的:
  - 给所有 bench_*.py 提供共享题集(消除 bench_intents 与 bench_intents_local
    50 条硬编码两遍漂移风险)
  - Jev 适用场景(intents/task/safety)的题集与 Jev 真测 1:1 对齐
  - 结构化场景(perf/disk_cleanup/tempfile/email/log)的独立 test set
    (30 抽 + 30 手造,与训练集不重叠)

设计:
  - 每个 fixture 是 JSON list,字段统一(见 schema.md)
  - load_fixture(name) → list[dict],内部 cache
  - FIXTURES 索引(场景名 → 文件名)

用法:
    from test_fixtures import load_fixture

    cases = load_fixture("intents_jev_compat.json")
    for c in cases:
        print(c["id"], c["expect"], c["text"])
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent

# 场景 → fixture 文件名(单一索引,加新场景只改这里)
FIXTURES: dict[str, str] = {
    # Jev 适用(Jev 真测题集)
    "intents_jev_compat": "intents_jev_compat.json",
    "task_jev_compat": "task_jev_compat.json",
    "safety_jev_compat": "safety_jev_compat.json",
    # 结构化(独立 test set)
    "perf_independent": "perf_independent.json",
    "disk_cleanup_independent": "disk_cleanup_independent.json",
    "tempfile_independent": "tempfile_independent.json",
    "email_independent": "email_independent.json",
    "log_independent": "log_independent.json",
}

_CACHE: dict[str, list[dict]] = {}


def load_fixture(name: str, use_cache: bool = True) -> list[dict]:
    """加载 fixture(场景名或文件名)。

    Args:
        name: 可以是场景名(如 "intents_jev_compat")或文件名(如 "intents_jev_compat.json")
        use_cache: True(默认)走模块级 cache;False 每次重读文件(测试用)

    Returns:
        list[dict],空列表 = 文件不存在

    Raises:
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: JSON 格式错
    """
    key = name.removesuffix(".json") if name.endswith(".json") else name
    filename = FIXTURES.get(key, f"{key}.json" if not name.endswith(".json") else name)
    if use_cache and filename in _CACHE:
        return _CACHE[filename]
    path = _HERE / filename
    if not path.exists():
        raise FileNotFoundError(
            f"fixture 文件不存在:{path}\n"
            f"  → 检查 test_fixtures/{filename} 是否就位")
    cases = json.loads(path.read_text(encoding="utf-8"))
    if use_cache:
        _CACHE[filename] = cases
    return cases


def list_fixtures() -> list[str]:
    """列已注册的所有 fixture 场景名。"""
    return list(FIXTURES.keys())


def reload_fixture(name: str) -> list[dict]:
    """强制重读文件(清 cache 后再 load)。"""
    filename = FIXTURES.get(name, f"{name}.json")
    _CACHE.pop(filename, None)
    return load_fixture(filename)


def fixture_path(name: str) -> Path:
    """返 fixture 文件绝对路径(不加载内容)。"""
    filename = FIXTURES.get(name, f"{name}.json")
    return _HERE / filename


__all__ = [
    "FIXTURES",
    "load_fixture",
    "list_fixtures",
    "reload_fixture",
    "fixture_path",
]