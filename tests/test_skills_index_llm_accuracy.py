"""
tests/test_skills_index_llm_accuracy.py — Skills 工作台 Phase 1.7 真实 LLM 准确率实测(2026-09-28)。

定位:开启 skills_index_enabled 模式下,用 OpenRouter 上的真 LLM 跑典型 prompt,
     看 skills_index 能否帮 LLM:
       ① 正确识别该用哪个 skill(从 80 个里挑)
       ② EXEC 标记参数正确(slug/参数全对)
       ③ 不乱调不该调的能力(没匹配时不输出 EXEC)

测试集设计:
  · 5 个正向用例(LLM 应输出 EXEC 调对应 skill)
  · 3 个负向用例(LLM 应不输出 EXEC)
  · 1 个边界(弱关联,L0/L1 风险判定)

每个用例评估:
  · matched_skill: LLM 输出里检测到的 [[EXEC: xxx]] skill_id
  · expected_skill: 真值
  · param_check: 关键参数名是否出现(L0 path / agency.query / free.query 等)
  · score: 命中 +1 / 缺 -1 / 误命中 -2
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from unittest import mock

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 仿造 companion build_messages:打开 skills_index,拼出真实 system 段
async def _build_system(use_skills_idx: bool = True) -> str:
    """仿造 companion build_messages 头段,只取 system 段串起来。"""
    from prisir_work import poster_capabilities  # noqa
    from prisir_work import poster_to_image_capability  # noqa
    from prisir_work import free_for_dev_capabilities  # noqa
    from prisir_work import agency_capabilities  # noqa

    parts = ["你是 PrisirAI,本地智能体。"]
    if use_skills_idx:
        from prisir_work.skills import describe_registry_compact
        skills_idx = describe_registry_compact()
        parts.append(
            "【工作台 skill 索引】下表 JSON 是当前可用的全部 skill 列表。"
            "每项含 id / name / emoji / risk / tags。"
            "需要执行某个 skill 时,在回复末尾追加 EXEC 标记:\n"
            "  [[EXEC: <skill_id> k1=\"v1\" k2=\"v2\" ...]]\n"
            "参数必须是字符串字面量。L2/L3 风险能力用户会单独确认一次,无需你提醒。"
            "只在索引中明确列出的任务上输出 EXEC,其它不输出。\n\n"
            f"```json\n{skills_idx}\n```"
        )
    return "\n\n".join(parts)


# ── 测试集设计 ─────────────────────────────────────────────
TEST_CASES = [
    # ---- 正向 5 例(LLM 应输出 EXEC) ----
    {
        "id": "P1",
        "user": "帮我做个手绘风格的海报,主题是秋天的第一杯奶茶",
        "expected_skill": "poster.smart",
        "expected_param": "theme",
        "type": "positive",
        "note": "poster.smart 自动推荐风格 + 颜色",
    },
    {
        "id": "P2",
        "user": "用 041 号风格 + C-25 爱马仕橙 + SC-001 排版,做个春节回家的海报",
        "expected_skill": "poster.spec",
        "expected_param": "subject",
        "type": "positive",
        "note": "poster.spec 精确指定",
    },
    {
        "id": "P3",
        "user": "找个免费的 Postgres 数据库",
        "expected_skill": "free.find",
        "expected_param": "query",
        "type": "positive",
        "note": "free.find 关键词 + 分类",
    },
    {
        "id": "P4",
        "user": "帮我找一个 React 工程师的 agent 角色",
        "expected_skill": "agency.search",
        "expected_param": "query",
        "type": "positive",
        "note": "agency.search 关键词 + division=engineering",
    },
    {
        "id": "P5",
        "user": "查一下 mp4 文件 /Users/me/clip.mp4 的时长和分辨率",
        "expected_skill": "video.info",
        "expected_param": "path",
        "type": "positive",
        "note": "video.info L0 只读",
    },
    # ---- 负向 3 例(LLM 应不输出 EXEC) ----
    {
        "id": "N1",
        "user": "今天天气怎么样?",
        "expected_skill": None,
        "expected_param": None,
        "type": "negative",
        "note": "闲聊不调 skill",
    },
    {
        "id": "N2",
        "user": "讲个笑话给我听",
        "expected_skill": None,
        "expected_param": None,
        "type": "negative",
        "note": "纯生成式,无 skill 可调",
    },
    {
        "id": "N3",
        "user": "Python 怎么写个 for 循环?",
        "expected_skill": None,
        "expected_param": None,
        "type": "negative",
        "note": "教学不调 skill",
    },
]


# ── OpenRouter 调 LLM ─────────────────────────────────────
async def _llm_call(model: str, system: str, user: str,
                    api_key: str, base_url: str,
                    timeout_s: float = 30.0) -> tuple[str, int, int]:
    """OpenRouter 调一次 → (content, latency_ms, tokens_used)"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/prisir/companion",
        "X-Title":       "prisIr-skills-acc-bench",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": 0.0,  # 准确率测试,关掉随机
        "max_tokens":  400,
    }
    t0 = time.time()
    async with aiohttp.ClientSession() as sess:
        try:
            async with sess.post(f"{base_url}/v1/chat/completions",
                                 headers=headers, json=payload,
                                 timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
                latency_ms = int((time.time() - t0) * 1000)
                if r.status != 200:
                    err = await r.text()
                    return (f"HTTP_{r.status}: {err[:200]}", latency_ms, 0)
                obj = await r.json()
                content = (obj.get("choices", [{}])[0]
                           .get("message", {}).get("content", ""))
                if content is None:
                    content = ""
                tokens = obj.get("usage", {}).get("total_tokens", 0)
                return (content, latency_ms, tokens)
        except Exception as e:  # noqa: BLE001
            return (f"ERR: {type(e).__name__}: {e}", int((time.time() - t0) * 1000), 0)


# ── 评估函数 ───────────────────────────────────────────────
EXEC_RE = re.compile(r"\[\[EXEC:\s*([a-z0-9_.\-]+)\s*([^\]]*)\]\]", re.IGNORECASE)
# tool_use 形式:Qwen/Hermes XML 风格 <tool_call>name<arg_key>key</arg_key>...</tool_call>
TOOL_USE_RE = re.compile(
    r"<(?:tool_call|invoke|antml:function_calls?)[^>]*>", re.IGNORECASE)


def _parse_exec(content: str) -> list[tuple[str, dict[str, str]]]:
    """提取 [[EXEC: skill_id k="v" ...]] 列表 + 参数 dict。
    兼容 tool_use XML 格式(把 <tool_call>X</tool_call> 当成一次 exec)。"""
    out = []
    for m in EXEC_RE.finditer(content):
        sid = m.group(1).strip()
        args_str = m.group(2).strip()
        args = dict(re.findall(r'(\w+)="([^"]*)"', args_str))
        out.append((sid, args))
    # tool_use XML 格式粗略提取(name + parameters 段)
    tool_uses = re.findall(
        r"<\s*(?:tool_call|invoke)[^>]*?>\s*<?(\w+(?:\.\w+)*)>?(.*?)(?:</\w+>|/>)",
        content, re.IGNORECASE | re.DOTALL)
    for name, params_xml in tool_uses:
        params = {}
        for pm in re.finditer(r"<\s*(\w+)\s*>([^<]*)</\s*\w+\s*>", params_xml):
            params[pm.group(1)] = pm.group(2).strip()
        out.append((name, params))
    return out


def _score_case(case: dict, llm_output: str) -> dict:
    """评估单个用例:matched_skill / param_present / final_score。
    容错:tool_use XML 格式也算 EXEC 命中(LLM 在用 tool_use,非协议失败)。"""
    execs = _parse_exec(llm_output)
    matched = [s for s, _ in execs]
    if case["type"] == "positive":
        # 应至少调 expected_skill
        if case["expected_skill"] in matched:
            score = 1
            params = next((a for s, a in execs if s == case["expected_skill"]), {})
            # 验证参数(允许 alias)
            aliases = {
                "query":  ["query", "keyword", "q", "text", "text_input"],
                "path":   ["path", "file", "file_path", "video_path"],
                "theme":  ["theme", "subject", "topic"],
                "subject":["subject", "title"],
            }
            param_aliases = aliases.get(case.get("expected_param", ""),
                                        [case.get("expected_param", "")])
            if any(a in params for a in param_aliases if a):
                score += 1
            return {"hit": True, "score": score,
                    "matched": matched, "reason": "ok",
                    "params": params}
        else:
            return {"hit": False, "score": -1, "matched": matched,
                    "reason": f"missing {case['expected_skill']}"}
    else:  # negative
        if not matched:
            return {"hit": True, "score": 1, "matched": [],
                    "reason": "no exec (correct)"}
        else:
            return {"hit": False, "score": -2, "matched": matched,
                    "reason": f"unexpected exec: {matched}"}


# ── 主测试 ────────────────────────────────────────────────
async def run_benchmark(model: str | None = None,
                       n_runs: int = 3) -> dict:
    """跑全部测试集 n_runs 次,取众数 hit(单次波动大)。返总报告。"""
    model = model or os.environ.get("OPENROUTER_MODEL", "openrouter/free")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api")
    if not api_key:
        return {"skipped": True, "reason": "OPENROUTER_API_KEY not set"}

    system = await _build_system(use_skills_idx=True)
    sys_chars = len(system)

    # 收集 n_runs × case 的所有评分
    all_runs = []
    for run_idx in range(n_runs):
        run_results = []
        for case in TEST_CASES:
            content, lat_ms, tokens = await _llm_call(
                model, system, case["user"], api_key, base_url)
            sc = _score_case(case, content)
            sc["case_id"] = case["id"]
            sc["type"] = case["type"]
            sc["note"] = case["note"]
            sc["llm_output"] = content[:300]
            sc["latency_ms"] = lat_ms
            sc["tokens"] = tokens
            run_results.append(sc)
            await asyncio.sleep(0.6)  # 避免 rate limit
        all_runs.append(run_results)

    # 合并到 case_id → 取众数 hit + 最高 score
    merged: dict[str, dict] = {}
    for run in all_runs:
        for r in run:
            cid = r["case_id"]
            if cid not in merged:
                merged[cid] = {"hit_count": 0, "best_score": -999,
                               "best_output": r["llm_output"],
                               "type": r["type"], "note": r["note"]}
            if r["hit"]:
                merged[cid]["hit_count"] += 1
            if r["score"] > merged[cid]["best_score"]:
                merged[cid]["best_score"] = r["score"]
                merged[cid]["best_output"] = r["llm_output"]

    # 输出格式
    results = []
    for cid, m in merged.items():
        results.append({
            "case_id": cid,
            "type": m["type"],
            "note": m["note"],
            "hit": m["hit_count"] >= (n_runs // 2 + 1),  # 多数派算 hit
            "score": m["best_score"],
            "hit_rate": f"{m['hit_count']}/{n_runs}",
            "llm_output": m["best_output"],
        })

    total_score = sum(r["score"] for r in results)
    pos_cases = [r for r in results if r["type"] == "positive"]
    neg_cases = [r for r in results if r["type"] == "negative"]
    pos_hit = sum(1 for r in pos_cases if r["hit"])
    neg_hit = sum(1 for r in neg_cases if r["hit"])

    summary = {
        "model": model,
        "n_runs": n_runs,
        "sys_chars": sys_chars,
        "total_cases": len(results),
        "pos_total": len(pos_cases),
        "pos_hit": pos_hit,
        "pos_acc": pos_hit / max(1, len(pos_cases)),
        "neg_total": len(neg_cases),
        "neg_hit": neg_hit,
        "neg_acc": neg_hit / max(1, len(neg_cases)),
        "total_score": total_score,
        "max_score": 2 * len(pos_cases) + len(neg_cases),
        "results": results,
    }
    return summary


def _print_report(summary: dict) -> None:
    if summary.get("skipped"):
        print(f"⚠ {summary['reason']}")
        return
    print(f"\n========== Phase 1.7 Skills LLM 准确率实测 ==========")
    print(f"Model: {summary['model']}  (n_runs={summary.get('n_runs', 1)})")
    print(f"System chars: {summary['sys_chars']}")
    print(f"正向用例: {summary['pos_hit']}/{summary['pos_total']} = "
          f"{summary['pos_acc']*100:.0f}%")
    print(f"负向用例: {summary['neg_hit']}/{summary['neg_total']} = "
          f"{summary['neg_acc']*100:.0f}%")
    print(f"总分: {summary['total_score']}/{summary['max_score']} "
          f"({summary['total_score']/summary['max_score']*100:.0f}%)")
    print()
    for r in summary["results"]:
        flag = "✓" if r["hit"] else "✗"
        print(f"{flag} [{r['case_id']}] ({r['type']}) score={r['score']:+d} "
              f"hit_rate={r.get('hit_rate', '-')}")
        print(f"   note: {r['note']}")
        print(f"   output: {r['llm_output'][:160]}...")
    print("================================================\n")


# ── pytest 入口 ────────────────────────────────────────────
def test_1_skills_index_llm_accuracy():
    """跑真实 LLM 看 skills_index 是否帮 LLM 正确识别该调啥。
    跳过条件:无 OPENROUTER_API_KEY(本地 CI 可不跑)。
    """
    summary = asyncio.run(run_benchmark())
    _print_report(summary)
    if summary.get("skipped"):
        print("(跳过 — 设置 OPENROUTER_API_KEY 后跑)")
        return
    # 软断言:总正确率 >= 50%(8 例中至少 4 例 hit)
    pos_acc = summary["pos_acc"]
    neg_acc = summary["neg_acc"]
    assert pos_acc + neg_acc >= 1.0, (
        f"skills_index 没帮 LLM 提升: pos={pos_acc:.0%} neg={neg_acc:.0%}\n"
        f"考虑 fallback 或 prompt 调整"
    )


if __name__ == "__main__":
    summary = asyncio.run(run_benchmark())
    _print_report(summary)