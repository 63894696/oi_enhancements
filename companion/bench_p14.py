# -*- coding: utf-8 -*-
# bench_p14.py — M3.45 P1-4 Jev 阶段成果评估评测 harness(2026-09-22)
#
# 目的:
#   - 在不启动整个 companion 服务的情况下,直接跑 companion_jev.eval_stage_outcome 真 API
#   - 用固定标签的样本集,算 has_outcome 精度 + value_score 准确度
#   - 出 JSON 报告,供接入前后对比
#
# 用法:
#   cd companion
#   python bench_p14.py                          # 跑全样本集(40 条)
#   python bench_p14.py --cases with_outcome     # 只跑某 category(逗号分隔)
#   python bench_p14.py --output report.json     # 写报告到文件
#   python bench_p14.py --min-value 2            # 调入库阈值
#
# 前置:
#   OPENROUTER_API_KEY 已设(主通道)
#   JEV_API_KEY 可选(官方备用通道,未设 = 单通道)
#
# 设计:
#   - 复用 companion_jev.py 的 eval_stage_outcome,不开新通道
#   - 测试集 40 条,覆盖 4 类:
#       with_outcome      阶段成果(预期 has=yes + value≥2)
#       without_outcome   无成果闲聊(预期 has=no)
#       low_value         琐碎记录(预期 has=yes + value<2)
#       duplicate_test    重复段检测(给后续 _p14_extract_segments 验证用)
#   - 输出 JSON 含每条 case 的 detail + 聚合指标
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

# 让本文件可单独 python -B bench_p14.py 跑
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from companion_jev import eval_stage_outcome, value_index_to_zh  # noqa: E402


# ------------------------------------------------------------
# 测试样本(40 条)— 硬编码,标签明确
# 每条 = {"user_text":..., "assistant_text":..., "expect":"with_outcome"|
#                                            "without_outcome"|"low_value",
#         "min_value": int}
# ------------------------------------------------------------
TEST_CASES: list[dict] = [
    # ===== with_outcome(强阶段成果)— 15 条 ——
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "我们决定用 Litestar 替代 FastAPI 做这个项目,理由呢?",
     "assistant_text": (
         "确认这个决策有三个核心理由:第一,Litestar 原生支持 SSE 中间件,"
         "FastAPI 需要手写 StreamingResponse;第二,实测性能高 30% "
         "(RPS 9.2k vs 7.1k);第三,类型注解更严格,可以在启动期发现 "
         "FastAPI 只能在运行时暴露的 bug。建议下一周迁移 demo。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "这个 bug 的根因到底是什么?",
     "assistant_text": (
         "根因是 ITfTextInputProcessorEx::ActivateEx 未实现,"
         "explorer.exe 在切换输入法时调它,函数指针为 NULL 直接 c0000005。"
         "修法:在 Activate() 里 fallback 到 ActivateEx 自己实现,"
         "或干脆在 ITfInputProcessorProfileMgr 里注册时声明不实现 Ex。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 3,
     "user_text": "M3.45 P1-1 接下来怎么做?",
     "assistant_text": (
         "P1-1 是关键转折点:从「每轮手动评估」进化到「自动评估+入库」。"
         "这一步决定了后续 P1-2/P1-3 能不能复用同样的 hook 模式。"
         "建议先做 P1-4 段级 sha256 去重,不要一上来就上向量检索。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "Python 装饰器怎么写能保留原函数签名?",
     "assistant_text": (
         "用 functools.wraps 即可,它会把 __name__/__doc__/__wrapped__ "
         "都复制过去。但要注意:对带参数的装饰器(decorator factory)需要 "
         "三层嵌套,中间层返回真正的 wrapper。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "要不要给 PrisirAI 加上 RAG?",
     "assistant_text": (
         "建议分阶段:第一阶段用 SQLite FTS5 做关键词检索(零成本,即时可用);"
         "第二阶段引入 bge-small-zh 做向量检索(150MB,本地能跑);"
         "第三阶段上 reranker。不要一上来就上混合检索,工程量爆炸。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "Docker 镜像构建怎么优化体积?",
     "assistant_text": (
         "三板斧:多阶段构建(distroless 最终层)+ .dockerignore "
         "(排除 .git / node_modules)+ 层缓存(把不常变的 COPY 放前面)。"
         "实测一个 Node 项目从 1.2GB 压到 180MB。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "PrisIr-companion 应该用哪个 ASR 厂商?",
     "assistant_text": (
         "结论:默认走讯飞流式(中文准确率最高),本地兜底走 sherpa-onnx "
         "(70MB,无 torch 依赖)。原因:sherpa-onnx 在华为平板上验证过,"
         "FunASR 因 c10.dll segfault 不能进子进程。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 3,
     "user_text": "怎么看待 TypeSafe Jev 在我们架构里的位置?",
     "assistant_text": (
         "Jev 定位是「System One 决策器」,不是 LLM 替代品。它和 PrisirAI "
         "的任务路由是互补:Jev 做前置 safety/intent/stage 三类结构化判断,"
         "PrisirAI 的 router 做 LLM 故障转移。两者不要混淆。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "React useEffect 依赖数组该怎么写?",
     "assistant_text": (
         "三个原则:一,所有在 effect 里用到的外部变量都要列;二,函数用 "
         "useCallback 包;三,对象用 useMemo 包。漏一个就 stale closure "
         "警告,React 18 strict mode 会双调一次暴露这类问题。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "Obsidian 怎么避免重复梳理经验?",
     "assistant_text": (
         "用增量入库:每次讨论结束自动 sha256 去重写入 _incremental/ 目录。"
         "sha256(段内容) 作为 key,已存在段跳过,新段追加。"
         "这样避免大段重复总结,也不漏。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "SQL 索引怎么选?",
     "assistant_text": (
         "高基数 + 高选择性列才需要索引;性别 / 状态 / 是否删除 这种低基数列"
         "建索引反而拖慢写入。联合索引遵循最左前缀,(a,b,c) 等价于 (a) 和 "
         "(a,b) 和 (a,b,c),不能跳过 a。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "TypeScript 中 interface 和 type 怎么选?",
     "assistant_text": (
         "团队规范定一个就行,不要混。interface 适合声明对象结构,可 extends;"
         "type 适合做联合 / 交叉 / 工具类型映射。两个 90% 场景可互换。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 3,
     "user_text": "下一步产品的方向是什么?",
     "assistant_text": (
         "三个方向:一,从单设备 (Windows) 扩到 macOS/Linux;"
         "二,接入手机端(华为平板已在跑);三,把 PrisirAI 拆成 SDK + 多端壳。"
         "优先级建议先做 macOS,投入产出比最高。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "Git rebase 和 merge 怎么选?",
     "assistant_text": (
         "个人 feature 分支用 rebase(保持线性);共享主分支用 merge(保留 "
         "合并节点的真实历史)。绝对禁止对已推送到公共分支的提交 rebase,"
         "会改 hash 让别人 pull 失败。"
     )},
    {"category": "with_outcome", "expect": "with_outcome", "min_value": 2,
     "user_text": "PrisIr 怎么同时支持 Win/Mac/Linux?",
     "assistant_text": (
         "三层架构:Python core(跨平台)+ Qt/Web 前端(跨平台)+ 系统特定 "
         "适配层(IME 注册 / 通知 / TTS)。不要在 core 里调任何平台特定 API。"
     )},

    # ===== without_outcome(无成果闲聊)— 15 条 ——
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "今天天气真好呀",
     "assistant_text": "是啊,适合出去走走 ☀️"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "我刚吃完饭,有点困",
     "assistant_text": "哈哈 吃饱了确实容易犯困 休息一下"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "你在干嘛呢?",
     "assistant_text": "等你呀 陪你聊天"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "哈哈,你真逗",
     "assistant_text": "谢谢夸奖 😄"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "周末想出去走走",
     "assistant_text": "想去哪呢?爬山还是逛街?"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "你叫什么名字?",
     "assistant_text": "我是 Prisir 语伴,可以叫我小语"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "我心情不太好",
     "assistant_text": "怎么了?愿意说说吗?"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "你几岁?",
     "assistant_text": "我没有年龄,但我可以陪你很久"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "陪我聊会儿天吧",
     "assistant_text": "好呀,聊什么都行"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "你会什么?",
     "assistant_text": "聊天、答疑、讲故事、帮你写东西都行"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "讲个笑话听听",
     "assistant_text": "为什么程序员总穿黑衣?因为他们不想被 debug"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "晚上吃什么好",
     "assistant_text": "想清淡还是重口味?"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "推荐个电影",
     "assistant_text": "你喜欢什么类型?悬疑?喜剧?科幻?"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "你好",
     "assistant_text": "你好呀 ☺"},
    {"category": "without_outcome", "expect": "without_outcome", "min_value": 0,
     "user_text": "我喜欢你",
     "assistant_text": "我也喜欢你~"},

    # ===== low_value(琐碎/弱价值)— 10 条 ——
    {"category": "low_value", "expect": "low_value", "min_value": 0,
     "user_text": "Python 怎么换行写长字符串?",
     "assistant_text": (
         "用三引号或括号。三引号保留格式,括号拼接最常用。"
     )},
    {"category": "low_value", "expect": "low_value", "min_value": 0,
     "user_text": "VSCode 怎么改字体?",
     "assistant_text": (
         "settings.json 里改 editor.fontFamily 字段,推荐 JetBrains Mono。"
     )},
    {"category": "low_value", "expect": "low_value", "min_value": 1,
     "user_text": "Windows 怎么看端口占用?",
     "assistant_text": "netstat -ano | findstr :端口号"},
    {"category": "low_value", "expect": "low_value", "min_value": 0,
     "user_text": "Python 怎么打印 list 元素?",
     "assistant_text": "print(*list) 直接展开,或者 for 循环"},
    {"category": "low_value", "expect": "low_value", "min_value": 0,
     "user_text": "JSON 怎么格式化?",
     "assistant_text": "Python json.dumps(obj, indent=2, ensure_ascii=False)"},
    {"category": "low_value", "expect": "low_value", "min_value": 0,
     "user_text": "今天星期几?",
     "assistant_text": "今天星期三"},
    {"category": "low_value", "expect": "low_value", "min_value": 0,
     "user_text": "Python list 怎么去重?",
     "assistant_text": "list(set(lst)) 保留顺序用 dict.fromkeys(lst)"},
    {"category": "low_value", "expect": "low_value", "min_value": 0,
     "user_text": "北京时间现在几点?",
     "assistant_text": "现在是上午 11 点"},
    {"category": "low_value", "expect": "low_value", "min_value": 0,
     "user_text": "Ctrl+C 复制不了?",
     "assistant_text": "试一下右键复制,或者看看是不是快捷键被占了"},
    {"category": "low_value", "expect": "low_value", "min_value": 0,
     "user_text": "1+1 等于几?",
     "assistant_text": "等于 2"},
]


# ------------------------------------------------------------
# 主评测
# ------------------------------------------------------------
async def run_bench(cases: list[dict], cfg: dict) -> dict:
    results: list[dict] = []
    print(f"=== 评测 {len(cases)} 条阶段成果样本 ===")
    print(f"配置:min_value={cfg['min_value']} timeout={cfg['timeout_sec']}s")
    print()

    t_start = time.time()
    for i, case in enumerate(cases, 1):
        user_text = case["user_text"]
        asst_text = case["assistant_text"]
        t0 = time.time()
        err_msg = ""
        try:
            res = await eval_stage_outcome(
                user_text, asst_text,
                history_len=0, user_tier="free",
                timeout_s=float(cfg["timeout_sec"]),
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            ok = res is not None
        except Exception as e:  # noqa: BLE001
            res = None
            elapsed_ms = int((time.time() - t0) * 1000)
            ok = False
            err_msg = f"{type(e).__name__}: {e}"

        has_prob = float((res or {}).get("has_probability", 0.0))
        has = bool((res or {}).get("has_outcome", False))
        value = (res or {}).get("value_score", "none")
        value_idx = int((res or {}).get("value_index", 0))
        value_zh = value_index_to_zh(value_idx)

        # 业务判定:value_idx ≥ min_value → 触发入库
        triggered = has and value_idx >= cfg["min_value"]

        # 期望判定:基于 category
        expected_triggered = (case["expect"] == "with_outcome")
        match_trigger = (triggered == expected_triggered)

        rec = {
            "i": i,
            "category": case["category"],
            "expect": case["expect"],
            "has_prob": has_prob,
            "has": has,
            "value": value,
            "value_index": value_idx,
            "value_zh": value_zh,
            "triggered": triggered,
            "expected_triggered": expected_triggered,
            "match_trigger": match_trigger,
            "elapsed_ms": elapsed_ms,
            "ok": ok,
            "text_preview": user_text[:30],
        }
        if not ok:
            rec["err"] = err_msg
        results.append(rec)

        sym = "✓" if match_trigger else "✗"
        print(f"  {sym} [{i:>2}/{len(cases)}] {case['category']:<16} "
              f"expect={case['expect']:<16} triggered={str(triggered):<5} "
              f"has={has_prob:.2f} value={value_zh:<4}(idx={value_idx}) "
              f"({elapsed_ms}ms) \"{user_text[:20]}...\"")

    total_ms = int((time.time() - t_start) * 1000)
    return _aggregate(results, total_ms, cfg)


def _aggregate(results: list[dict], total_ms: int, cfg: dict) -> dict:
    """聚合:整体精度/每类精度/value 准确度/延迟分位。"""
    n = len(results)
    n_ok = sum(1 for r in results if r["ok"])
    matches = sum(1 for r in results if r["match_trigger"])
    accuracy = matches / n if n else 0.0

    # 误触发:expect ≠ with_outcome 但 triggered(过度入库)
    false_trigger = [r for r in results
                     if r["expect"] != "with_outcome" and r["triggered"]]
    # 漏触发:expect == with_outcome 但未 triggered(漏入库)
    miss_trigger = [r for r in results
                    if r["expect"] == "with_outcome" and not r["triggered"]]

    # 每类 trigger 率
    by_class: dict[str, dict] = {}
    for r in results:
        c = r["category"]
        if c not in by_class:
            by_class[c] = {"total": 0, "triggered": 0, "correct": 0}
        by_class[c]["total"] += 1
        if r["triggered"]:
            by_class[c]["triggered"] += 1
        if r["match_trigger"]:
            by_class[c]["correct"] += 1
    for c, d in by_class.items():
        d["accuracy"] = round(d["correct"] / d["total"], 4) if d["total"] else 0.0
        d["trigger_rate"] = round(d["triggered"] / d["total"], 4) if d["total"] else 0.0

    # 延迟分位
    succ = [r["elapsed_ms"] for r in results if r["ok"]]
    p50 = statistics.median(succ) if succ else 0
    p95 = (sorted(succ)[int(len(succ) * 0.95)] if len(succ) >= 20
           else (max(succ) if succ else 0))
    fallback_rate = (n - n_ok) / n if n else 0.0

    # 价值评分偏差(只看 with_outcome 类别)
    val_diff: list[int] = []
    for r in results:
        if r["expect"] == "with_outcome":
            # 期望:critical 3 / archivable 2;value ≥ 2 就算对
            # 偏差用 |predicted - 2| 计算(预期平均 2-3)
            val_diff.append(abs(r["value_index"] - 2))

    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": cfg,
        "n": n,
        "n_ok": n_ok,
        "accuracy": round(accuracy, 4),
        "false_trigger_count": len(false_trigger),
        "miss_trigger_count": len(miss_trigger),
        "fallback_rate": round(fallback_rate, 4),
        "latency_ms": {
            "p50": int(p50), "p95": int(p95),
            "max": max(succ) if succ else 0,
            "min": min(succ) if succ else 0,
        },
        "by_class": by_class,
        "value_score_avg_for_outcomes": (
            round(sum(r["value_index"] for r in results
                      if r["expect"] == "with_outcome") /
                  sum(1 for r in results if r["expect"] == "with_outcome"), 2)
            if any(r["expect"] == "with_outcome" for r in results) else 0
        ),
        "false_trigger_samples": [
            {"text": r["text_preview"], "category": r["category"],
             "has_prob": r["has_prob"], "value": r["value"],
             "value_index": r["value_index"]}
            for r in false_trigger[:5]
        ],
        "miss_trigger_samples": [
            {"text": r["text_preview"], "has_prob": r["has_prob"],
             "value": r["value"], "value_index": r["value_index"]}
            for r in miss_trigger[:5]
        ],
        "total_ms": total_ms,
        "details": results,
    }


def _print_summary(report: dict) -> None:
    print()
    print("=" * 60)
    print(f"阶段成果评测汇总 ({report['n']} 条)")
    print("=" * 60)
    print(f"整体精度:        {report['accuracy']*100:.1f}% "
          f"({report['n_ok']}/{report['n']} 成功调用)")
    print(f"误触发(无成果误入库): {report['false_trigger_count']} 条")
    print(f"漏触发(有成果漏入库): {report['miss_trigger_count']} 条")
    print(f"fallback 率:     {report['fallback_rate']*100:.1f}%")
    print(f"延迟 p50/p95:    {report['latency_ms']['p50']}ms / "
          f"{report['latency_ms']['p95']}ms")
    print()
    print("每类:")
    for c, d in report["by_class"].items():
        print(f"  {c:<16} 触发率={d['trigger_rate']*100:>5.1f}%  "
              f"精度={d['accuracy']*100:>5.1f}%  ({d['correct']}/{d['total']})")
    if "value_score_avg_for_outcomes" in report:
        print(f"\n有成果类的 value_score 平均: "
              f"{report['value_score_avg_for_outcomes']}")
    if report["false_trigger_samples"]:
        print()
        print("误触发样例:")
        for s in report["false_trigger_samples"]:
            print(f"  - [{s['category']}] \"{s['text']}\" "
                  f"has={s['has_prob']:.2f} value={s['value']}({s['value_index']})")
    if report["miss_trigger_samples"]:
        print()
        print("漏触发样例:")
        for s in report["miss_trigger_samples"]:
            print(f"  - \"{s['text']}\" "
                  f"has={s['has_prob']:.2f} value={s['value']}({s['value_index']})")


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.45 P1-4 Jev 阶段成果评测")
    ap.add_argument("--cases", default=None,
                    help="只跑某 category(逗号分隔),如 with_outcome,without_outcome")
    ap.add_argument("--output", default=None,
                    help="报告输出文件(默认打印到 stdout)")
    ap.add_argument("--timeout", type=float, default=1.2,
                    help="单次超时秒数(默认 1.2)")
    ap.add_argument("--min-value", type=int, default=2,
                    help="value≥此值才计触发(0-3,默认 2)")
    ap.add_argument("--prefer", default="openrouter",
                    choices=["openrouter", "official"])
    args = ap.parse_args()

    cfg = {
        "timeout_sec": args.timeout,
        "min_value": args.min_value,
        "prefer": args.prefer,
    }

    if not os.environ.get("OPENROUTER_API_KEY") \
            and not os.environ.get("JEV_API_KEY"):
        print("❌ 缺 key:OPENROUTER_API_KEY / JEV_API_KEY 至少要配一个")
        return 2

    cases = TEST_CASES
    if args.cases:
        wanted = set(s.strip() for s in args.cases.split(","))
        cases = [c for c in TEST_CASES if c["category"] in wanted]
        if not cases:
            print(f"❌ 无匹配 category:{args.cases}")
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
