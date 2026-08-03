#!/usr/bin/env python3
"""Patch team_lead_tools.py to add auto-experience-saving to Obsidian."""

import os
import re
import shutil
from pathlib import Path

TEAM_LEAD = Path(__file__).parent / "team_lead_tools.py"
BACKUP = TEAM_LEAD.with_suffix(".py.patched.bak")

# Read original
content = TEAM_LEAD.read_text(encoding="utf-8")

if "_save_team_experience_to_obsidian" in content:
    print("Already patched, skipping.")
    exit(0)

# Add constants after TRACE_DIR
constants = '''
# v0.48 OIagent 团队协作经验保存
OBSIDIAN_VAULT = Path(os.environ.get(
    "OBSIDIAN_VAULT",
    r"C:/Users/Administrator/Documents/ObsidianVault",
))
OBSIDIAN_EXPERIENCES_DIR = OBSIDIAN_VAULT / "experiences"
OBSIDIAN_EXPERIENCES_DIR.mkdir(parents=True, exist_ok=True)
'''

content = content.replace(
    'TRACE_DIR = Path.home() / ".claude" / "oiagent_harness_training"',
    'TRACE_DIR = Path.home() / ".claude" / "oiagent_harness_training"' + constants
)

# Add save function after _append_trace
save_func = '''

# =============================================================================
# v0.48 — 团队协作 Agent 经验保存
# =============================================================================
def _save_team_experience_to_obsidian(event: str, payload: dict) -> None:
    """保存团队协作经验到 Obsidian。

    触发条件:
      1. dispatch 事件 + agent_config 存在
      2. race 事件 + winner 存在
      3. trace 事件 (手动触发)
    """
    try:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d")
        ts_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        # 提取关键信息
        agent = payload.get("agent", "unknown")
        pool = payload.get("pool", "unknown")
        task = payload.get("task", "")
        intent = payload.get("intent", "")
        matched_kw = payload.get("matched_keyword", "")

        # 构建标题
        title = f"OIagent 团队协作经验 ({now_str})"
        if intent:
            title = f"OIagent {intent} 经验 ({now_str})"

        # 提取核心经验
        tl_dr_parts = []
        core_exp = []
        gotchas = []

        # 从 payload 中提取经验
        if agent_config := payload.get("agent_config"):
            role = agent_config.get("role", "")
            goal = agent_config.get("goal", "")
            backstory = agent_config.get("backstory", "")
            if role:
                tl_dr_parts.append(f"角色: {role}")
            if goal:
                core_exp.append(f"目标: {goal[:80]}")
            if backstory:
                core_exp.append(f"背景: {backstory[:80]}")

        # 从 task 中提取关键词
        if task:
            keywords = [kw for kw in ["总结", "经验", "教训", "踩坑", "结论"] if kw in task]
            if keywords:
                tl_dr_parts.extend(keywords)

        # 构建 frontmatter
        tags = ["经验", "团队协作", agent]
        if gotchas:
            tags.append("踩坑")

        frontmatter = (
            "---\\n"
            f"title: {title}\\n"
            f"domain: OIagent/团队协作\\n"
            f"date: '{now_str}'\\n"
            f"created_at: '{ts_str}'\\n"
            "tags:\\n"
            "  - 经验\\n"
            + "\\n".join(f"  - {t}" for t in tags) + "\\n"
            "status: 已存档\\n"
            "source_skill: team-lead-experience\\n"
            "related: [[note-to-obsidian]]\\n"
            "---\\n"
        )

        # 构建正文
        body_lines = [
            f"# {title}", "",
            "## TL;DR",
            *(f"- {p}" for p in tl_dr_parts[:5]) or ["<!-- agent 可后续补 -->"], "",
            "## 核心经验",
            *core_exp[:8] or ["<!-- agent 可后续补 -->"], "",
            "## 配置信息",
            f"- Agent: {agent}",
            f"- Pool: {pool}",
            f"- Intent: {intent}",
            *(f"- 匹配关键词: {matched_kw}" if matched_kw else ""),
            "",
            "## 关联",
            "- [[note-to-obsidian]]",
            "- [[mcp_oiagent_routing]]",
            "",
            "## 原始事件",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2)[:2000],
            "```", "",
        ]

        # 写入文件
        import re as _re
        safe_title = _re.sub(r'[<>:"/\\|?*]', "", title)
        safe_title = _re.sub(r"\\s+", " ", safe_title).strip()
        filename = f"{safe_title}.md"
        filepath = OBSIDIAN_EXPERIENCES_DIR / filename

        if filepath.exists():
            base, ext = os.path.splitext(filename)
            filename = f"{base}_{datetime.now().strftime('%H%M%S')}{ext}"
            filepath = OBSIDIAN_EXPERIENCES_DIR / filename

        filepath.write_text(frontmatter + "\\n".join(body_lines), encoding="utf-8")
        print(f"[team-lead] Experience saved: {filepath} (agent={agent}, event={event})")

    except Exception as e:
        print(f"[team-lead] Experience save failed (non-fatal): {type(e).__name__}: {e}")
'''

# Insert after _append_trace function
pattern = r'(def _append_trace.*?(?=\\n\\ndef |\\n# ---|$))'
match = re.search(pattern, content, re.DOTALL)
if match:
    insert_pos = match.end()
    content = content[:insert_pos] + save_func + content[insert_pos:]
    print("✓ 添加经验保存函数")
else:
    print("✗ 未找到插入位置")

# Call in _append_trace
if '_save_team_experience_to_obsidian(event' not in content:
    content = content.replace(
        '    # P0-2: 异步 fire-and-forget ingest 到 cognee',
        '    # v0.48: 保存经验到 Obsidian\n    _save_team_experience_to_obsidian(event, payload)\n\n    # P0-2: 异步 fire-and-forget ingest 到 cognee'
    )
    print("✓ 在 _append_trace 中添加经验保存调用")
else:
    print("经验保存调用已存在")

# Write backup and new file
shutil.copy2(TEAM_LEAD, BACKUP)
TEAM_LEAD.write_text(content, encoding="utf-8")
print(f"✓ 已更新 {TEAM_LEAD}")

# Verify syntax
import ast
ast.parse(content)
print("✓ 语法有效")

# Show stats
print(f"\n=== 补丁统计 ===")
print(f"函数定义: {content.count('def _save_team_experience_to_obsidian')} 次")
print(f"函数调用: {content.count('_save_team_experience_to_obsidian(event')} 次")
print(f"配置常量: {'OBSIDIAN_VAULT' in content}")
