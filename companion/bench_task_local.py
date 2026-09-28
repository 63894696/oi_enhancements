# -*- coding: utf-8 -*-
# bench_task_local.py — M3.58 本地 0.6B 任务分类评测 harness(2026-09-23)
#
# 目的:
#   - 跑 classify_task_local.classify_task_local 真 API
#   - 用固定标签样本集,算任务分类精度 + 混淆矩阵 + 延迟分位
#   - 对比 fastlane/providers/llm_prisir.py:classify_task 的纯正则版本
#   - 出 JSON 报告,供接入前后对比
#
# 设计:
#   - 6 类任务各 10-15 条,共 ~70 条样本:
#       code_call / code_qa / creative / long / fast / general
#   - 复用 classify_intents.py 的 _parse_output 模式
#   - 输出 JSON 含每条 case 的 detail + 聚合指标 + 混淆矩阵
#   - 长上下文(long):用 1 条 > 3000 字符样本即可(其他类都 < 100 字符)
#   - **对比对象**:除了跑本地 0.6B,还并行跑 fastlane regex,看精度差异
#
# 用法:
#   cd companion
#   python bench_task_local.py                              # 跑全样本集
#   python bench_task_local.py --cases code_call,creative   # 只跑某 task
#   python bench_task_local.py --no-fastlane                # 跳过 regex 对比
#   python bench_task_local.py --output report.json         # 写报告到文件
#
# 前置:
#   本地 task_conf adapter 已下载 + 注册到 adapter_registry
#   fastlane/providers/llm_prisir.py:classify_task 已就位(对比基线)
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))


# ------------------------------------------------------------
# 测试样本(70 条)— 硬编码,标签明确
# 每条 = {"text":..., "expect":"code_call"|"code_qa"|"creative"|"long"|"fast"|"general"}
# 长上下文(long)只放 1 条 > 3000 字符(避免 bench 输入太长)
# ------------------------------------------------------------
LONG_SAMPLE = (
    "请帮我审阅以下文档内容,提取关键信息并给出修改建议:\n\n"
    "本章详细讨论了分布式系统的核心理论与实践经验。首先,我们回顾了 CAP 定理的基本概念,"
    "即在一致性(Consistency)、可用性(Availability)和分区容忍性(Partition tolerance)"
    "三者之间,任何分布式系统最多只能同时满足其中两项。这一理论由 Eric Brewer 在 2000 "
    "年提出,并在 2002 年由 Seth Gilbert 和 Nancy Lynch 给出形式化证明。在实际工程中,"
    "由于网络分区不可避免,大多数系统选择在一致性和可用性之间做出权衡。\n\n"
    "接下来,我们深入分析了 Raft 一致性算法的实现细节。Raft 通过将一致性分解为三个"
    "子问题:领导者选举(Leader Election)、日志复制(Log Replication)和安全性(Safety),"
    "显著降低了 Paxos 算法的理解和实现难度。Raft 算法的核心是任期(Term)机制,每个任期"
    "最多存在一个领导者,领导者通过心跳机制维持其权威。当领导者失效时,集群会在随机超时"
    "后触发新的选举,确保系统的可用性。\n\n"
    "在工程实践方面,我们对比了几种主流的分布式协调服务,包括 Apache ZooKeeper、"
    "etcd 和 Consul。ZooKeeper 采用 ZAB 协议,提供了强一致性的 KV 存储和临时节点"
    "机制,广泛应用于 Hadoop、Kafka 等大数据生态。etcd 基于 Raft 实现,提供了更现代的"
    "API 和更好的性能,被 Kubernetes 用作其后端存储。Consul 则在服务发现和健康检查方面"
    "提供了更丰富的功能,适合微服务架构下的服务网格场景。\n\n"
    "性能优化是分布式系统的永恒话题。我们讨论了几种常见的优化策略:读写分离通过将"
    "写操作集中到主节点、读操作分发到从节点来提升系统的读吞吐能力;数据分片(Sharding)"
    "通过将数据按特定规则分散到多个节点来突破单机存储和性能瓶颈;缓存层(Caching)通过"
    "在内存中保存热点数据来减少对后端存储的访问压力。\n\n"
    "最后,我们探讨了分布式系统中的故障处理机制。常见的故障类型包括节点故障、网络分区、"
    "脑裂(Split Brain)等。应对这些故障需要设计完善的监控告警体系、自动恢复流程和"
    "人工干预预案。混沌工程(Chaos Engineering)作为一种主动注入故障的方法,可以帮助团队"
    "在生产环境之前发现系统的脆弱点,提高系统的整体韧性。\n\n"
    "请按以下结构组织回复:\n"
    "1. 文档摘要\n"
    "2. 关键论点\n"
    "3. 逻辑漏洞或不一致之处\n"
    "4. 修改建议\n"
    "5. 结论\n"
)

TEST_CASES: list[dict] = []
try:
    from test_fixtures import load_fixture as _load_fixture
    TEST_CASES = _load_fixture("task_jev_compat")
except Exception:
    # Fallback:fixture 不可用时回退到内嵌(下面 _LEGACY 列表)
    TEST_CASES = _LEGACY_TEST_CASES()


def _LEGACY_TEST_CASES() -> list[dict]:
    """M3.58 老硬编码 70 条备份(若 fixture 不可用时 fallback)。"""
    return [
    {"expect": "code_call", "text": "帮我写个快速排序函数"},
    {"expect": "code_call", "text": "写一个 Python 装饰器"},
    {"expect": "code_call", "text": "实现一个链表反转"},
    {"expect": "code_call", "text": "debug 一下报 null 的 bug"},
    {"expect": "code_call", "text": "写个 bash 脚本批量备份"},
    {"expect": "code_call", "text": "实现一个 LRU 缓存"},
    {"expect": "code_call", "text": "帮我写个 React 组件渲染列表"},
    {"expect": "code_call", "text": "实现一个生产者消费者模型"},
    {"expect": "code_call", "text": "帮我写个爬虫抓取豆瓣电影 Top250"},
    {"expect": "code_call", "text": "写一个 sql 查询最近 7 天登录的用户"},
    {"expect": "code_call", "text": "帮我写一个 docker-compose 多服务编排"},
    {"expect": "code_call", "text": "实现一个最小栈(O(1) getMin)"},
    {"expect": "code_qa", "text": "什么是装饰器?"},
    {"expect": "code_qa", "text": "Python GIL 怎么解决?"},
    {"expect": "code_qa", "text": "def 和 function 有什么区别?"},
    {"expect": "code_qa", "text": "闭包的原理是什么?"},
    {"expect": "code_qa", "text": "async 跟 await 怎么用?"},
    {"expect": "code_qa", "text": "sql 索引 explain 怎么看?"},
    {"expect": "code_qa", "text": "解释一下 Git rebase 的原理"},
    {"expect": "code_qa", "text": "Python 装饰器和闭包有什么区别?"},
    {"expect": "code_qa", "text": "什么是协程?跟线程比有什么优劣?"},
    {"expect": "code_qa", "text": "进程线程协程的区别是什么?"},
    {"expect": "code_qa", "text": "Python 多线程为什么慢?GIL 是元凶?"},
    {"expect": "code_qa", "text": "RESTful API 设计原则有哪些?"},
    {"expect": "creative", "text": "写首诗关于秋天的落叶"},
    {"expect": "creative", "text": "编个故事哄孩子睡觉"},
    {"expect": "creative", "text": "起个产品名叫'智能便签'"},
    {"expect": "creative", "text": "想个 slogan 关于云端笔记"},
    {"expect": "creative", "text": "写首词赞颂桂花"},
    {"expect": "creative", "text": "写一段产品宣传文案"},
    {"expect": "creative", "text": "编一个穿越小说开头"},
    {"expect": "creative", "text": "写个广告语卖无线耳机"},
    {"expect": "creative", "text": "编一个侦探故事开头"},
    {"expect": "creative", "text": "写首宋词关于元宵"},
    {"expect": "long", "text": LONG_SAMPLE},
    {"expect": "fast", "text": "今天天气怎么样"},
    {"expect": "fast", "text": "翻译你好"},
    {"expect": "fast", "text": "VSCode 的缩写是什么"},
    {"expect": "fast", "text": "今天日期"},
    {"expect": "fast", "text": "现在几点了"},
    {"expect": "fast", "text": "北京到上海多少公里"},
    {"expect": "fast", "text": "Python 是什么的缩写"},
    {"expect": "fast", "text": "AI 的英文全称"},
    {"expect": "fast", "text": "USD 是什么货币"},
    {"expect": "fast", "text": "WiFi 是什么缩写"},
    {"expect": "fast", "text": "中国首都是哪里"},
    {"expect": "fast", "text": "一杯咖啡多少卡路里"},
    {"expect": "general", "text": "你好呀"},
    {"expect": "general", "text": "在吗"},
    {"expect": "general", "text": "今天心情不太好"},
    {"expect": "general", "text": "工作好累啊"},
    {"expect": "general", "text": "推荐几本好看的书"},
    {"expect": "general", "text": "周末想出去走走"},
    {"expect": "general", "text": "孩子不听话怎么办"},
    {"expect": "general", "text": "推荐一部好看的电影"},
    {"expect": "general", "text": "新能源汽车哪个牌子好"},
    {"expect": "general", "text": "考研需要准备什么"},
    {"expect": "general", "text": "推荐几款好用的耳机"},
    {"expect": "general", "text": "晚上吃什么好"},
    ]


# ------------------------------------------------------------
# 评测主循环
# ------------------------------------------------------------
def _run_local(cases: list[dict]) -> list[dict]:
    """跑本地 0.6B task_conf adapter。"""
    from classify_task_local import classify_task_local

    results: list[dict] = []
    print(f"\n=== 本地 0.6B 评测 {len(cases)} 条 ===")
    for i, case in enumerate(cases, 1):
        text = case["text"]
        t0 = time.time()
        err = ""
        try:
            out = classify_task_local(text, use_conf=True)
            elapsed = int((time.time() - t0) * 1000)
            actual = out.get("task_type", "unknown")
            conf = out.get("action_conf", 0.0)
            ok = True
        except Exception as e:  # noqa: BLE001
            elapsed = int((time.time() - t0) * 1000)
            actual = "error"
            conf = 0.0
            ok = False
            err = f"{type(e).__name__}: {e}"
        match = (actual == case["expect"])
        rec = {
            "i": i,
            "expect": case["expect"],
            "actual": actual,
            "match": match,
            "conf": conf,
            "elapsed_ms": elapsed,
            "ok": ok,
            "text_preview": text[:40],
        }
        if err:
            rec["err"] = err
        results.append(rec)
        sym = "✓" if match else "✗"
        print(f"  {sym} [{i:>2}/{len(cases)}] expect={case['expect']:<11} "
              f"actual={actual:<11} conf={conf:.2f} ({elapsed}ms) "
              f"\"{text[:24]}...\"")
    return results


def _run_fastlane(cases: list[dict]) -> list[dict]:
    """跑 fastlane regex 基线(对齐 M3.57 后的 classify_task)。"""
    # 走包路径 import(fastlane/producers 里 llm_prisir 用 from .base)
    _here_path = Path(__file__).resolve().parent
    _fastlane_root = _here_path.parent
    if str(_fastlane_root) not in sys.path:
        sys.path.insert(0, str(_fastlane_root))
    from fastlane.providers.llm_prisir import classify_task  # noqa: E402

    results: list[dict] = []
    print(f"\n=== Fastlane regex 基线评测 {len(cases)} 条 ===")
    for i, case in enumerate(cases, 1):
        text = case["text"]
        t0 = time.time()
        try:
            actual = classify_task(text)
            elapsed = int((time.time() - t0) * 1000)
            ok = True
            err = ""
        except Exception as e:  # noqa: BLE001
            actual = "error"
            elapsed = int((time.time() - t0) * 1000)
            ok = False
            err = f"{type(e).__name__}: {e}"
        match = (actual == case["expect"])
        rec = {
            "i": i,
            "expect": case["expect"],
            "actual": actual,
            "match": match,
            "elapsed_ms": elapsed,
            "ok": ok,
            "text_preview": text[:40],
        }
        if err:
            rec["err"] = err
        results.append(rec)
        sym = "✓" if match else "✗"
        print(f"  {sym} [{i:>2}/{len(cases)}] expect={case['expect']:<11} "
              f"actual={actual:<11} ({elapsed}ms) \"{text[:24]}...\"")
    return results


def _aggregate(results: list[dict], label: str) -> dict:
    """聚合精度/混淆矩阵/延迟分位。"""
    n = len(results)
    matches = sum(1 for r in results if r["match"])
    accuracy = matches / n if n else 0.0

    # 每类精度
    by_class: dict[str, dict] = {}
    for r in results:
        e = r["expect"]
        by_class.setdefault(e, {"total": 0, "match": 0})
        by_class[e]["total"] += 1
        if r["match"]:
            by_class[e]["match"] += 1
    for c, d in by_class.items():
        d["accuracy"] = round(d["match"] / d["total"], 4) if d["total"] else 0.0

    # 混淆矩阵
    classes = ["code_call", "code_qa", "creative", "long", "fast", "general", "unknown", "error"]
    confusion = {c: {a: 0 for a in classes} for c in classes}
    for r in results:
        e = r["expect"] if r["expect"] in classes else "unknown"
        a = r["actual"] if r["actual"] in classes else "error"
        confusion[e][a] += 1

    # 延迟分位
    succ = [r["elapsed_ms"] for r in results if r["ok"]]
    p50 = int(statistics.median(succ)) if succ else 0
    p95 = (int(sorted(succ)[int(len(succ) * 0.95)]) if len(succ) >= 20
           else (max(succ) if succ else 0))

    return {
        "label": label,
        "n": n,
        "accuracy": round(accuracy, 4),
        "latency_ms": {"p50": p50, "p95": p95, "max": max(succ) if succ else 0},
        "by_class": by_class,
        "confusion_matrix": confusion,
        "wrong_samples": [
            {"text": r["text_preview"], "expect": r["expect"],
             "actual": r["actual"]}
            for r in results if not r["match"]
        ],
    }


def _print_summary(local: dict, fastlane: dict | None) -> None:
    print()
    print("=" * 60)
    print(f"任务分类评测汇总 ({local['n']} 条)")
    print("=" * 60)
    print(f"本地 0.6B 精度:   {local['accuracy']*100:.1f}% "
          f"延迟 p50/p95: {local['latency_ms']['p50']}ms / "
          f"{local['latency_ms']['p95']}ms")
    if fastlane:
        print(f"Fastlane regex 精度: {fastlane['accuracy']*100:.1f}% "
              f"延迟 p50/p95: {fastlane['latency_ms']['p50']}ms / "
              f"{fastlane['latency_ms']['p95']}ms")
    print()
    print("本地每类精度:")
    for c, d in sorted(local["by_class"].items()):
        print(f"  {c:<11} {d['match']}/{d['total']}  ({d['accuracy']*100:.0f}%)")
    if local["wrong_samples"]:
        print()
        print("误判样本(本地):")
        for s in local["wrong_samples"][:10]:
            print(f"  - \"{s['text']}\" expect={s['expect']} → actual={s['actual']}")
    if fastlane and fastlane["wrong_samples"]:
        print()
        print("误判样本(fastlane):")
        for s in fastlane["wrong_samples"][:10]:
            print(f"  - \"{s['text']}\" expect={s['expect']} → actual={s['actual']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.58 本地任务分类评测")
    ap.add_argument("--cases", default=None,
                    help="只跑某 expect task(逗号分隔),如 code_call,creative")
    ap.add_argument("--output", default=None,
                    help="报告输出文件(默认打印到 stdout)")
    ap.add_argument("--no-fastlane", action="store_true",
                    help="跳过 fastlane regex 对比(节省时间)")
    args = ap.parse_args()

    cases = TEST_CASES
    if args.cases:
        wanted = set(s.strip() for s in args.cases.split(","))
        cases = [c for c in TEST_CASES if c["expect"] in wanted]
        if not cases:
            print(f"❌ 无匹配 expect:{args.cases}")
            return 2

    local_results = _run_local(cases)
    local_agg = _aggregate(local_results, "local_0.6b")

    fastlane_agg = None
    if not args.no_fastlane:
        try:
            fl_results = _run_fastlane(cases)
            fastlane_agg = _aggregate(fl_results, "fastlane_regex")
        except Exception as e:  # noqa: BLE001
            print(f"\n⚠️ Fastlane regex 评测跳过:{e}")

    _print_summary(local_agg, fastlane_agg)

    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n": len(cases),
        "local": local_agg,
        "fastlane": fastlane_agg,
        "details": {
            "local": local_results,
            "fastlane": (fastlane_agg is not None),
        },
    }

    if args.output:
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\n✅ 报告已写:{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
