"""prisir_work/rules.py — P2-Rules AGENTS.md 解析与注入(2026-10-01)。

设计:
- 借鉴 ECC(Everything Claude Code)的 Rules 原语,把「项目根 AGENTS.md + 家目录 AGENTS.md」
  作为持久化的项目/用户级规范,用户改 AGENTS.md 即可生效,无需改 schema。
- frontmatter 解析:沿用 prisir_work/skill_manifest.py 的极简 key:value 模式,
  不引 PyYAML,支持两层嵌套(standards: { style/testing/commit/deps })。
- 注入预算:超 rules_max_chars(默认 4000)截断 + 追加 [truncated]。
- 优先级:用户级 < 项目级(项目级 standards[key] 非空才覆盖)。
- 失败 fallback:任何步骤异常 → 返回 None(不抛),调用方静默。

完整设计文档:C:\\Users\\Administrator\\.claude\\plans\\stateless-launching-locket.md(P2-Rules 段)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ProjectRules",
    "parse_agents_md",
    "load_rules_for_project",
    "merge_rules",
    "format_rules_for_prompt",
    "DEFAULT_RULES_ENABLED",
    "DEFAULT_RULES_MAX_CHARS",
]

log = logging.getLogger("prisir_work.rules")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_RULES_ENABLED: bool = True
DEFAULT_RULES_MAX_CHARS: int = 4000

# AGENTS.md 文件名(完全 ECC 对齐,也是 Cursor / Codex / OpenCode 通用)
AGENTS_MD_FILENAME: str = "AGENTS.md"

# frontmatter 极简解析:同 skill_manifest._FRONTMATTER_RE 模式
# 容忍 frontmatter 全空(`---\n---\nbody`),所以 \n--- 之前允许 0 个字符
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<fm>.*?)\n?---\s*\n?(?P<body>.*)$",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class ProjectRules:
    """从一份 AGENTS.md 解析出的规则集合。

    Attributes:
        project: 项目名(frontmatter project 字段,缺失时退化为 source_path 的目录名)
        standards: dict[str,str],典型 key: style/testing/commit/deps
        priority_keywords: list[str],frontmatter 的优先级关键词(影响后续 inject 排序)
        body: 原始 markdown 正文(frontmatter 之后的全文)
        source_path: 这份规则来自哪个文件(绝对路径字符串)
        scope: "user" | "project" 标识(merge 后填 "merged")
    """
    project: str = ""
    standards: dict[str, str] = field(default_factory=dict)
    priority_keywords: list[str] = field(default_factory=list)
    body: str = ""
    source_path: str = ""
    scope: str = ""  # "user" | "project" | "merged"


# ---------------------------------------------------------------------------
# frontmatter 极简解析
# ---------------------------------------------------------------------------


def _parse_frontmatter_lines(fm_text: str) -> dict[str, Any]:
    """极简解析 frontmatter 文本,支持一层嵌套(standards: { style: ... })和列表。

    返回 dict:
      - 顶层 key:str → str  或  str → list[str]  或  str → dict[str,str]
      - 不识别的行 → 忽略(降级,不抛)
    """
    out: dict[str, Any] = {}
    cur_key: str | None = None
    cur_kind: str = ""  # "list" | "dict" | "scalar"
    list_buf: list[str] = []
    dict_buf: dict[str, str] = {}

    def _flush() -> None:
        nonlocal cur_key, cur_kind, list_buf, dict_buf
        if cur_key is None:
            return
        if cur_kind == "list":
            out[cur_key] = list_buf
        elif cur_kind == "dict":
            out[cur_key] = dict_buf
        # scalar 不需要 flush(已被逐行收齐,在顶层 key: value 直接 write)
        cur_key = None
        cur_kind = ""
        list_buf = []
        dict_buf = {}

    lines = fm_text.splitlines()
    for line in lines:
        stripped = line.strip()
        # 空行 / 注释行 → 跳过
        if not stripped or stripped.startswith("#"):
            continue
        # 计算缩进(空格数,非 tab)
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            # 顶层 key: value
            _flush()
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                k = k.strip()
                v = v.strip()
                if not v:
                    # 后面是嵌套块(列表 or 字典),先猜 dict,后面 list 标志纠正
                    cur_key = k
                    cur_kind = "dict"
                    dict_buf = {}
                    list_buf = []
                else:
                    # 简单 scalar:可能包含 list 字面量 [a, b, c]
                    if v.startswith("[") and v.endswith("]"):
                        inner = v[1:-1].strip()
                        items = [x.strip().strip('"').strip("'")
                                 for x in inner.split(",") if x.strip()]
                        out[k] = items
                    else:
                        out[k] = v
                    cur_key = k
                    cur_kind = "scalar"
        else:
            # 缩进行 — 属于 cur_key 的子内容
            if cur_key is None:
                continue
            if stripped.startswith("- "):
                cur_kind = "list"
                list_buf.append(stripped[2:].strip().strip('"').strip("'"))
            elif ":" in stripped:
                cur_kind = "dict"
                k2, _, v2 = stripped.partition(":")
                dict_buf[k2.strip()] = v2.strip()
            else:
                cur_kind = "list"
                list_buf.append(stripped)
    _flush()
    return out


def parse_agents_md(path: Path) -> ProjectRules | None:
    """读一份 AGENTS.md,极简解析 + frontmatter 降级。

    Returns:
        ProjectRules:成功时(含 frontmatter 全空 + body 全文兜底)
        None:文件不存在 / 读取失败 / 文件完全空白
    """
    p = Path(path)
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("[rules] read %s 失败: %s", p, e)
        return None
    if not text.strip():
        return None

    # 默认值
    project = p.parent.name  # fallback:目录名
    standards: dict[str, str] = {}
    priority_keywords: list[str] = []
    body = text

    # 尝试 frontmatter 解析
    m = _FRONTMATTER_RE.match(text)
    if m:
        fm_text = m.group("fm")
        body = m.group("body") or ""
        try:
            fm = _parse_frontmatter_lines(fm_text)
            if fm.get("project"):
                project = str(fm["project"]).strip()
            pk = fm.get("priority_keywords")
            if isinstance(pk, list):
                priority_keywords = [str(x) for x in pk]
            elif isinstance(pk, str) and pk:
                # 容错:有人写成 "priority_keywords: a, b, c"
                priority_keywords = [x.strip() for x in pk.split(",") if x.strip()]
            st = fm.get("standards")
            if isinstance(st, dict):
                for k, v in st.items():
                    if v is None:
                        continue
                    sv = str(v).strip()
                    if sv:
                        standards[str(k)] = sv
        except Exception as e:  # noqa: BLE001
            log.warning("[rules] frontmatter 解析失败(%s): %s — 走 body 全文兜底",
                        p, e)
            # 解析失败 → body 仍是全文(降级)
            body = text

    # scope 由调用方决定(load 时根据文件路径设 user/project)
    return ProjectRules(
        project=project,
        standards=standards,
        priority_keywords=priority_keywords,
        body=body.strip(),
        source_path=str(p.resolve()),
        scope="",  # 调用方填
    )


# ---------------------------------------------------------------------------
# 极简 yaml 读(不引 PyYAML)
# ---------------------------------------------------------------------------


def _read_yaml_scalar(key: str, default: Any) -> Any:
    """极简 yaml 读取:仅识别 `key: value` 顶层行(值是 bool/int/str)。

    不引 PyYAML;找到第一个候选配置文件 → 正则 → 命中返 value,否则 default。
    候选:prisIrai_config.yaml / prisirmp.config.yaml,先 cwd 再 prisir_work 上两级。
    """
    candidates: list[Path] = []
    try:
        cwd = Path.cwd()
        candidates.append(cwd / "prisIrai_config.yaml")
        candidates.append(cwd / "prisirmp.config.yaml")
    except Exception:  # noqa: BLE001
        pass
    try:
        here = Path(__file__).resolve().parent
        proj_root = here.parent
        candidates.append(proj_root / "prisIrai_config.yaml")
        candidates.append(proj_root / "prisirmp.config.yaml")
    except Exception:  # noqa: BLE001
        pass

    # 不同 key 用不同正则
    if isinstance(default, bool):
        pat = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(true|false)\s*$",
                         re.IGNORECASE | re.MULTILINE)
    elif isinstance(default, int):
        pat = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(\d+)\s*$",
                         re.MULTILINE)
    else:
        pat = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(.+?)\s*$",
                         re.MULTILINE)

    seen: set[Path] = set()
    for c in candidates:
        try:
            cr = c.resolve()
        except Exception:  # noqa: BLE001
            continue
        if cr in seen:
            continue
        seen.add(cr)
        if not cr.is_file():
            continue
        try:
            text = cr.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        m = pat.search(text)
        if not m:
            continue
        v = m.group(1)
        if isinstance(default, bool):
            return v.strip().lower() == "true"
        if isinstance(default, int):
            try:
                return int(v.strip())
            except ValueError:
                return default
        return v.strip()
    return default


def _rules_enabled() -> bool:
    """读 prisIrai_config.yaml 的 rules_enabled(默认 True)。失败 → True。"""
    val = _read_yaml_scalar("rules_enabled", DEFAULT_RULES_ENABLED)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return DEFAULT_RULES_ENABLED


def _rules_max_chars() -> int:
    """读 prisIrai_config.yaml 的 rules_max_chars(默认 4000)。失败 → 默认。"""
    val = _read_yaml_scalar("rules_max_chars", DEFAULT_RULES_MAX_CHARS)
    try:
        n = int(val)  # type: ignore[arg-type]
        if n < 100:
            return DEFAULT_RULES_MAX_CHARS
        return n
    except (TypeError, ValueError):
        return DEFAULT_RULES_MAX_CHARS


# ---------------------------------------------------------------------------
# 加载 / 合并
# ---------------------------------------------------------------------------


def load_rules_for_project(cwd: Path) -> tuple[ProjectRules | None,
                                                  ProjectRules | None]:
    """读用户级 + 项目级 AGENTS.md。

    Returns:
        (user_rules, project_rules) — 任一缺失则对应位置为 None
        任何 IO / 解析异常 → 静默返 None,不抛

    配置项 rules_enabled=False → 直接返 (None, None)。

    注意:返参顺序固定为 (user, project),调用方写成 `u, p = ...`
    再 `merge_rules(u, p)`(merge_rules 第一个参 User 第二个参 Project)。
    """
    if not _rules_enabled():
        return (None, None)
    project_rules: ProjectRules | None = None
    user_rules: ProjectRules | None = None

    try:
        cwd = Path(cwd)
        proj_md = cwd / AGENTS_MD_FILENAME
        project_rules = parse_agents_md(proj_md)
        if project_rules:
            project_rules.scope = "project"
    except Exception as e:  # noqa: BLE001
        log.warning("[rules] load project AGENTS.md 失败: %s", e)
        project_rules = None

    try:
        home_md = Path.home() / AGENTS_MD_FILENAME
        user_rules = parse_agents_md(home_md)
        if user_rules:
            user_rules.scope = "user"
    except Exception as e:  # noqa: BLE001
        log.warning("[rules] load user AGENTS.md 失败: %s", e)
        user_rules = None

    return (user_rules, project_rules)


def merge_rules(user_a: ProjectRules | None,
                project_a: ProjectRules | None) -> ProjectRules | None:
    """项目级覆盖用户级(项目级 standards[key] 非空才覆盖)。

    Args:
        user_a: 用户级 rules(load 时 scope=user)
        project_a: 项目级 rules(load 时 scope=project)

    Returns:
        合并后的 ProjectRules(scope="merged")或 None(都为空时)
    """
    if not user_a and not project_a:
        return None
    merged_standards: dict[str, str] = {}
    if user_a:
        for k, v in user_a.standards.items():
            if v:
                merged_standards[k] = v
    if project_a:
        for k, v in project_a.standards.items():
            if v:  # 项目级非空才覆盖
                merged_standards[k] = v

    # priority_keywords:用户级 + 项目级(项目级优先,放前)
    pk: list[str] = []
    if project_a and project_a.priority_keywords:
        pk.extend(project_a.priority_keywords)
    if user_a and user_a.priority_keywords:
        pk.extend(user_a.priority_keywords)
    # 去重保序
    seen: set[str] = set()
    dedup_pk: list[str] = []
    for x in pk:
        if x in seen:
            continue
        seen.add(x)
        dedup_pk.append(x)

    # project 字段:项目级 > 用户级
    project_name = ""
    if project_a and project_a.project:
        project_name = project_a.project
    elif user_a and user_a.project:
        project_name = user_a.project

    # body:项目级 + 用户级(项目在前)
    body_parts: list[str] = []
    if project_a and project_a.body:
        body_parts.append(f"# 项目级 ({project_a.source_path})\n{project_a.body}")
    if user_a and user_a.body:
        body_parts.append(f"# 用户级 ({user_a.source_path})\n{user_a.body}")
    merged_body = "\n\n".join(body_parts).strip()

    # source_path 拼两个
    src_parts: list[str] = []
    if project_a and project_a.source_path:
        src_parts.append(project_a.source_path)
    if user_a and user_a.source_path:
        src_parts.append(user_a.source_path)

    return ProjectRules(
        project=project_name,
        standards=merged_standards,
        priority_keywords=dedup_pk,
        body=merged_body,
        source_path=";".join(src_parts),
        scope="merged",
    )


# ---------------------------------------------------------------------------
# 格式化输出(注入 build_messages)
# ---------------------------------------------------------------------------


def format_rules_for_prompt(rules: ProjectRules) -> str:
    """把 ProjectRules 序列化成 user prompt 段(给 LLM 看的人话)。

    格式:
        [Rules — AGENTS.md — {scope}]
        项目:{project}
        规范:
          - style: ...
          - testing: ...
        优先级关键词:[...]
        正文:{body}

    超 rules_max_chars → 截断 + 末尾追加 [truncated]。
    """
    if not rules:
        return ""
    max_chars = _rules_max_chars()
    # 空 rules(全字段为空)→ 不产生任何输出,避免注入空 system 段
    has_content = bool(
        rules.project or rules.standards or rules.priority_keywords or rules.body
    )
    if not has_content:
        return ""
    scope_zh = {"user": "用户级", "project": "项目级",
                "merged": "合并级"}.get(rules.scope, rules.scope or "合并级")

    lines: list[str] = []
    lines.append(f"[Rules — AGENTS.md — {scope_zh}]")
    if rules.project:
        lines.append(f"项目:{rules.project}")
    if rules.standards:
        lines.append("规范:")
        for k, v in rules.standards.items():
            single = f"  - {k}: {v}"
            if len(single) > max_chars // 2:
                single = single[: max_chars // 2] + "..."
            lines.append(single)
    if rules.priority_keywords:
        kw = ", ".join(rules.priority_keywords)
        if len(kw) > 200:
            kw = kw[:200] + "..."
        lines.append(f"优先级关键词:{kw}")
    if rules.body:
        lines.append("正文:")
        lines.append(rules.body)

    text = "\n".join(lines).rstrip()
    if len(text) > max_chars:
        text = text[: max_chars].rstrip() + "\n[truncated]"
    return text