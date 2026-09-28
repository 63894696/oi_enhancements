# -*- coding: utf-8 -*-
# bench_intents.py — M3.45 P0-1 Jev 意图分发评测 harness(2026-09-22)
#
# 目的:
#   - 在不启动整个 companion 服务的情况下,直接跑 companion_jev.ask_intent 真 API
#   - 用固定标签的样本集,算意图分类精度 + 混淆矩阵 + 置信度分桶
#   - 出 JSON 报告,供接入前后对比
#
# 用法:
#   cd companion
#   python bench_intents.py                          # 跑全样本集(50 条)
#   python bench_intents.py --cases code,search      # 只跑某 intent(逗号分隔)
#   python bench_intents.py --output report.json     # 写报告到文件
#   python bench_intents.py --min-confidence 0.6     # 调路由阈值
#
# 前置:
#   OPENROUTER_API_KEY 已设(主通道)
#   JEV_API_KEY 可选(官方备用通道,未设 = 单通道)
#
# 设计:
#   - 复用 companion_jev.py 的 ask_intent,不开新通道
#   - 测试集是硬编码的小样本(50 条),覆盖 5 类各 10 条:
#       chat/code/search/tool_call/roleplay
#   - 输出 JSON 含每条 case 的 detail + 聚合指标 + 混淆矩阵
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

# 让本文件可单独 python -B bench_intents.py 跑
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from companion_jev import ask_intent, INTENT_LABELS_ZH  # noqa: E402
from test_fixtures import load_fixture  # noqa: E402


# ------------------------------------------------------------
# 测试样本 — M3.60 L4 改:从 test_fixtures/intents_jev_compat.json 读
# (50 条,5 类各 10 条,与 bench_intents_local 共用同一题集,防漂移)
# ------------------------------------------------------------
TEST_CASES: list[dict] = load_fixture("intents_jev_compat")


def _LEGACY_TEST_CASES() -> list[dict]:
    """M3.45 老硬编码备份(若 fixture 不可用时 fallback)。"""
    return [
    {"expect": "chat", "text": "今天天气真好呀"},
    {"expect": "chat", "text": "我刚吃完饭,有点困"},
    {"expect": "chat", "text": "你在干嘛呢?"},
    {"expect": "chat", "text": "哈哈,你真逗"},
    {"expect": "chat", "text": "我心情不太好"},
    {"expect": "chat", "text": "周末想出去走走"},
    {"expect": "chat", "text": "今天心情不太好,有点难过"},
    {"expect": "chat", "text": "你叫什么名字?"},
    {"expect": "chat", "text": "我喜欢你这种说话风格"},
    {"expect": "chat", "text": "陪我聊会儿天吧"},
    {"expect": "code", "text": "帮我写一个 Python 装饰器"},
    {"expect": "code", "text": "解释下 React 的 useEffect"},
    {"expect": "code", "text": "SQL 怎么优化索引?"},
    {"expect": "code", "text": "教我用 git rebase"},
    {"expect": "code", "text": "Python 异步和并发的区别"},
    {"expect": "code", "text": "TypeScript 中 interface 和 type 的区别?"},
    {"expect": "code", "text": "这个报错 'Cannot read property of undefined' 怎么修"},
    {"expect": "code", "text": "帮我写个递归求斐波那契"},
    {"expect": "code", "text": "Docker 怎么配置多阶段构建"},
    {"expect": "code", "text": "Rust 的生命周期怎么理解"},
    {"expect": "search", "text": "为什么天空是蓝色的?"},
    {"expect": "search", "text": "今天股市怎么样"},
    {"expect": "search", "text": "推荐一本好看的小说"},
    {"expect": "search", "text": "推荐几本理财的书"},
    {"expect": "search", "text": "孩子不听话怎么办"},
    {"expect": "search", "text": "怎么跟女朋友道歉比较好"},
    {"expect": "search", "text": "我想学钢琴,几岁开始好"},
    {"expect": "search", "text": "什么是量子计算?"},
    {"expect": "search", "text": "北京的人口有多少"},
    {"expect": "search", "text": "二战是哪一年结束的?"},
    {"expect": "tool_call", "text": "帮我打开浏览器"},
    {"expect": "tool_call", "text": "关闭所有窗口"},
    {"expect": "tool_call", "text": "把音量调到 50%"},
    {"expect": "tool_call", "text": "删除 C 盘下 temp 文件夹"},
    {"expect": "tool_call", "text": "截图当前屏幕"},
    {"expect": "tool_call", "text": "给张三发邮件,主题是周报"},
    {"expect": "tool_call", "text": "重启电脑"},
    {"expect": "tool_call", "text": "打开 VS Code 打开项目 D:/work"},
    {"expect": "tool_call", "text": "搜索我的桌面文件名为 '合同' 的文档"},
    {"expect": "tool_call", "text": "把这个 PDF 转成 Word"},
    {"expect": "roleplay", "text": "讲个鬼故事给我听"},
    {"expect": "roleplay", "text": "假装你是李白,我们来对诗"},
    {"expect": "roleplay", "text": "玩个角色扮演,你是黑骑士"},
    {"expect": "roleplay", "text": "继续讲那个穿越小说的剧情"},
    {"expect": "roleplay", "text": "假设你在中世纪,我是国王,你来当军师"},
    {"expect": "roleplay", "text": "演一段面试官,我要练习面试"},
    {"expect": "roleplay", "text": "扮演我的英语老师,我们用英语对话"},
    {"expect": "roleplay", "text": "讲个童话故事哄孩子睡觉"},
    {"expect": "roleplay", "text": "你是海盗,我也是海盗,我们抢宝藏"},
    {"expect": "roleplay", "text": "模仿苏东坡的语气给我写首词"},
    ]


# Fallback:若 fixture 不存在,回退到老硬编码(防御性)
if not TEST_CASES:
    TEST_CASES = _LEGACY_TEST_CASES()


# ------------------------------------------------------------
# 主评测
# ------------------------------------------------------------
async def run_bench(cases: list[dict], cfg: dict) -> dict:
    results: list[dict] = []
    print(f"=== 评测 {len(cases)} 条意图样本 ===")
    print(f"配置:timeout={cfg['intent_timeout_sec']}s "
          f"min_confidence={cfg['intent_min_confidence']}")
    print()

    t_start = time.time()
    for i, case in enumerate(cases, 1):
        text = case["text"]
        t0 = time.time()
        err_msg = ""
        try:
            intent = await ask_intent(
                text,
                history_len=0,
                user_tier="free",
                timeout_s=float(cfg.get("intent_timeout_sec", 1.0)),
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            ok = intent is not None
        except Exception as e:  # noqa: BLE001
            intent = None
            elapsed_ms = int((time.time() - t0) * 1000)
            ok = False
            err_msg = f"{type(e).__name__}: {e}"

        actual = "unknown"
        confidence = 0.0
        probs: dict = {}
        if ok and intent:
            actual = intent.get("choice") or "unknown"
            confidence = float(intent.get("confidence", 0.0))
            probs = intent.get("probabilities") or {}

        match = (actual == case["expect"])
        routed = ok and actual != "unknown" and confidence >= cfg["intent_min_confidence"]
        rec = {
            "i": i,
            "expect": case["expect"],
            "actual": actual,
            "match": match,
            "routed": routed,
            "confidence": confidence,
            "elapsed_ms": elapsed_ms,
            "ok": ok,
            "text_preview": text[:30],
        }
        if probs:
            rec["probabilities"] = probs
        if not ok:
            rec["err"] = err_msg
        results.append(rec)

        sym = "✓" if match else "✗"
        route_tag = " [路由]" if routed else " [未路由]"
        print(f"  {sym} [{i:>2}/{len(cases)}] expect={case['expect']:<10} "
              f"actual={actual:<10} conf={confidence:.2f}{route_tag} "
              f"({elapsed_ms}ms) \"{text[:24]}...\"")

    total_ms = int((time.time() - t_start) * 1000)
    return _aggregate(results, total_ms, cfg)


def _aggregate(results: list[dict], total_ms: int, cfg: dict) -> dict:
    """聚合:总精度/每类精度/混淆矩阵/置信度分桶/延迟分位。"""
    n = len(results)
    n_ok = sum(1 for r in results if r.get("ok"))
    matches = sum(1 for r in results if r["match"])
    accuracy = matches / n if n else 0.0

    # 每类精度 + 召回(对每类 expect)
    by_class: dict[str, dict] = {}
    for r in results:
        e = r["expect"]
        if e not in by_class:
            by_class[e] = {"total": 0, "match": 0}
        by_class[e]["total"] += 1
        if r["match"]:
            by_class[e]["match"] += 1
    for c, d in by_class.items():
        d["precision_recall"] = round(d["match"] / d["total"], 4) if d["total"] else 0.0

    # 路由精度(只算 routed 的样本里 match 占比)
    routed = [r for r in results if r["routed"]]
    routed_match = sum(1 for r in routed if r["match"])
    routed_acc = routed_match / len(routed) if routed else 0.0

    # 混淆矩阵:rows=expect, cols=actual
    classes = ["chat", "code", "search", "tool_call", "roleplay", "unknown"]
    confusion: dict[str, dict[str, int]] = {c: {a: 0 for a in classes} for c in classes}
    for r in results:
        e = r["expect"] if r["expect"] in classes else "unknown"
        a = r["actual"] if r["actual"] in classes else "unknown"
        confusion[e][a] += 1

    # 置信度分桶(<0.4 / 0.4-0.6 / 0.6-0.8 / >=0.8)
    buckets = {"<0.4": 0, "0.4-0.6": 0, "0.6-0.8": 0, ">=0.8": 0}
    for r in results:
        c = r["confidence"]
        if c < 0.4:
            buckets["<0.4"] += 1
        elif c < 0.6:
            buckets["0.4-0.6"] += 1
        elif c < 0.8:
            buckets["0.6-0.8"] += 1
        else:
            buckets[">=0.8"] += 1

    # 延迟分位(只算成功 case)
    succ = [r["elapsed_ms"] for r in results if r["ok"]]
    p50 = statistics.median(succ) if succ else 0
    p95 = (sorted(succ)[int(len(succ) * 0.95)] if len(succ) >= 20
           else (max(succ) if succ else 0))

    fallback_rate = (n - n_ok) / n if n else 0.0

    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": cfg,
        "n": n,
        "n_ok": n_ok,
        "accuracy": round(accuracy, 4),
        "routed_count": len(routed),
        "routed_accuracy": round(routed_acc, 4),
        "fallback_rate": round(fallback_rate, 4),
        "latency_ms": {
            "p50": int(p50), "p95": int(p95),
            "max": max(succ) if succ else 0,
            "min": min(succ) if succ else 0,
        },
        "confidence_buckets": buckets,
        "by_class": by_class,
        "confusion_matrix": confusion,
        "wrong_samples": [
            {"text": r["text_preview"], "expect": r["expect"],
             "actual": r["actual"], "confidence": r["confidence"]}
            for r in results if not r["match"]
        ],
        "total_ms": total_ms,
        "details": results,
    }


def _print_summary(report: dict) -> None:
    print()
    print("=" * 60)
    print(f"意图分发评测汇总 ({report['n']} 条)")
    print("=" * 60)
    print(f"整体精度:        {report['accuracy']*100:.1f}% "
          f"({report['n_ok']}/{report['n']} 成功调用)")
    print(f"路由命中:        {report['routed_count']}/{report['n']}")
    print(f"路由样本精度:    {report['routed_accuracy']*100:.1f}%")
    print(f"fallback 率:     {report['fallback_rate']*100:.1f}%")
    print(f"延迟 p50/p95:    {report['latency_ms']['p50']}ms / "
          f"{report['latency_ms']['p95']}ms")
    print()
    print("每类精度(precision-recall 合并):")
    for c, d in report["by_class"].items():
        zh = INTENT_LABELS_ZH.get(c, c)
        print(f"  {c:<10} ({zh:<6})  {d['match']}/{d['total']}  "
              f"({d['precision_recall']*100:.0f}%)")
    print()
    print("置信度分桶:")
    for b, n in report["confidence_buckets"].items():
        print(f"  {b:<10} {n}")
    if report["wrong_samples"]:
        print()
        print("误判样本:")
        for s in report["wrong_samples"]:
            print(f"  - \"{s['text']}\" expect={s['expect']} → "
                  f"actual={s['actual']} conf={s['confidence']:.2f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.45 P0-1 Jev 意图分发评测")
    ap.add_argument("--cases", default=None,
                    help="只跑某 expect intent(逗号分隔),如 code,search")
    ap.add_argument("--output", default=None,
                    help="报告输出文件(默认打印到 stdout)")
    ap.add_argument("--timeout", type=float, default=1.0,
                    help="intent 单次超时秒数(默认 1.0)")
    ap.add_argument("--min-confidence", type=float, default=0.55,
                    help="路由命中最低置信度(默认 0.55)")
    ap.add_argument("--prefer", default="openrouter",
                    choices=["openrouter", "official"])
    args = ap.parse_args()

    cfg = {
        "intent_timeout_sec": args.timeout,
        "intent_min_confidence": args.min_confidence,
        "prefer": args.prefer,
    }

    if not os.environ.get("OPENROUTER_API_KEY") \
            and not os.environ.get("JEV_API_KEY"):
        print("❌ 缺 key:OPENROUTER_API_KEY / JEV_API_KEY 至少要配一个")
        return 2

    cases = TEST_CASES
    if args.cases:
        wanted = set(s.strip() for s in args.cases.split(","))
        cases = [c for c in TEST_CASES if c["expect"] in wanted]
        if not cases:
            print(f"❌ 无匹配 expect:{args.cases}")
            return 2

    report = asyncio.run(run_bench(cases, cfg))
    _print_summary(report)

    if args.output:
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\n✅ 报告已写:{args.output}")
    else:
        print("\n--- JSON 报告 ---")
        # details 太长,省略
        brief = {k: v for k, v in report.items() if k != "details"}
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
