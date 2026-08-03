"""SKILL.md format validator and standardizer.

对标 moonbit-agent-guide SKILL.md 格式：
- 必填字段: name, description
- 可选字段: version, allowed-tools, argument-hint, user-invocable, homepage, metadata
- 统一输出标准化的 frontmatter

v0.38: 参考 moonbitlang/moonbit-agent-guide 的跨平台兼容格式
"""
from __future__ import annotations

import re
import yaml
from pathlib import Path
from typing import Any


# ── SKILL.md 格式规范 (v0.38) ──────────────────────────────────

REQUIRED_FIELDS = ["name", "description"]

OPTIONAL_FIELDS = [
    "version",           # semver
    "allowed-tools",     # list of tool names
    "argument-hint",     # usage examples
    "user-invocable",    # true/false
    "homepage",          # file:// or https:// URL
    "metadata",          # nested: type, emoji, source_vendor, claude_compat, etc.
]

VALID_METADATA_KEYS = [
    "type",              # project | skill | reference | user-built
    "emoji",             # single emoji char
    "source_vendor",     # claude | codex | cursor | gemini | custom
    "source_path",       # relative path to source
    "claude_compat",     # A | B | C
    "author",            # human-readable author
    "version",           # skill version (overrides top-level)
    "complementary_to",  # list of skill names
    "homepage",          # URL
]


def parse_frontmatter(content: str) -> dict[str, Any]:
    """解析 SKILL.md 的 YAML frontmatter."""
    match = re.match(r"^---\n(.*?)\n---\n?", content, re.DOTALL)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}


def _json_safe(obj):
    """递归将对象转为 JSON 可序列化格式."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (int, float, bool, str)):
        return obj
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return str(obj)


def validate_skill(skill_path: Path) -> dict[str, Any]:
    """验证 SKILL.md 格式，返回验证结果。"""
    content = skill_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(content)

    issues = []
    warnings = []

    # 检查必填字段
    for field in REQUIRED_FIELDS:
        if field not in fm or not fm[field]:
            issues.append(f"Missing required field: {field}")

    # 检查 version 格式 (semver)
    version = fm.get("version", "")
    if version and not re.match(r"^\d+\.\d+(\.\d+)?(-\w+)?$", str(version)):
        warnings.append(f"Version '{version}' doesn't look like semver")

    # 检查 allowed-tools 格式
    tools = fm.get("allowed-tools", [])
    if tools and not isinstance(tools, list):
        issues.append("allowed-tools must be a list")

    # 检查 metadata 格式
    metadata = fm.get("metadata", {})
    if metadata and not isinstance(metadata, dict):
        issues.append("metadata must be a dict")
    elif metadata:
        for key in metadata:
            if key not in VALID_METADATA_KEYS:
                warnings.append(f"Unknown metadata key: {key}")

    return {
        "path": str(skill_path),
        "name": fm.get("name", "(unknown)"),
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "frontmatter": _json_safe(fm),
    }


def standardize_frontmatter(fm: dict[str, Any]) -> str:
    """生成标准化的 frontmatter。"""
    lines = ["---"]
    for key in REQUIRED_FIELDS:
        if key in fm:
            lines.append(f"{key}: {fm[key]!r}")
    for key in OPTIONAL_FIELDS:
        if key in fm and fm[key]:
            lines.append(f"{key}: {yaml.dump(fm[key], default_flow_style=False).strip()}")
    lines.append("---")
    return "\n".join(lines)


def scan_skills(directory: Path, recursive: bool = True) -> list[dict[str, Any]]:
    """扫描目录下的所有 SKILL.md 文件并验证。"""
    pattern = "**/SKILL.md" if recursive else "SKILL.md"
    results = []
    for skill_file in sorted(directory.glob(pattern)):
        result = validate_skill(skill_file)
        results.append(result)
    return results


def generate_report(results: list[dict[str, Any]]) -> str:
    """生成验证报告。"""
    valid = sum(1 for r in results if r["valid"])
    invalid = len(results) - valid

    lines = [
        f"SKILL.md Format Validation Report",
        f"{'='*40}",
        f"Total: {len(results)}",
        f"Valid: {valid}",
        f"Issues: {invalid}",
        "",
    ]

    for r in results:
        status = "OK" if r["valid"] else "ISSUE"
        lines.append(f"  [{status}] {r['name']}: {r['path']}")
        for w in r.get("warnings", []):
            lines.append(f"    ⚠ {w}")
        for i in r.get("issues", []):
            lines.append(f"    ✗ {i}")

    return "\n".join(lines)


# ── MCP Tool Handlers ──────────────────────────────────────────

def skill_validate_impl(skill_path: str = "") -> str:
    """验证单个 SKILL.md 文件。"""
    if not skill_path:
        return "error: skill_path required"
    p = Path(skill_path)
    if not p.exists():
        return f"error: file not found: {skill_path}"
    result = validate_skill(p)
    import json
    return json.dumps(result, ensure_ascii=False, indent=2)


def skill_scan_impl(directory: str = "~/.claude/skills", max_files: int = 50) -> str:
    """扫描目录下的 SKILL.md 文件。"""
    p = Path(directory).expanduser()
    if not p.exists():
        return f"error: directory not found: {directory}"
    results = scan_skills(p)[:max_files]
    import json
    return json.dumps({
        "total": len(results),
        "valid": sum(1 for r in results if r["valid"]),
        "invalid": sum(1 for r in results if not r["valid"]),
        "results": results[:20],  # First 20 for summary
    }, ensure_ascii=False, indent=2)


def skill_standardize_impl(skill_path: str) -> str:
    """标准化 SKILL.md 的 frontmatter。"""
    p = Path(skill_path)
    if not p.exists():
        return f"error: file not found: {skill_path}"
    content = p.read_text(encoding="utf-8")
    fm = parse_frontmatter(content)
    if not fm:
        return "error: no frontmatter found"
    standardized = standardize_frontmatter(fm)
    return standardized


# ── Dynamic Registry Exports ───────────────────────────────────

TOOL_DEFS = [
    {
        "name": "skill_validate",
        "description": "验证单个 SKILL.md 文件的格式 — 检查必填字段、version semver、allowed-tools 类型等",
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_path": {"type": "string", "description": "SKILL.md 文件路径"},
            },
            "required": ["skill_path"],
        },
    },
    {
        "name": "skill_scan",
        "description": "扫描目录下的所有 SKILL.md 文件并生成验证报告",
        "inputSchema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "default": "~/.claude/skills"},
                "max_files": {"type": "integer", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "skill_standardize",
        "description": "标准化 SKILL.md 的 frontmatter — 统一格式为必填(name+description) + 可选(version/allowed-tools/metadata)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_path": {"type": "string"},
            },
            "required": ["skill_path"],
        },
    },
]

HANDLERS = {
    "skill_validate": skill_validate_impl,
    "skill_scan": skill_scan_impl,
    "skill_standardize": skill_standardize_impl,
}
