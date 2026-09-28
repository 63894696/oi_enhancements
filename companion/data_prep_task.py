#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# data_prep_task.py — M3.58 任务分类训练集准备(2026-09-23)
#
# 目的:
#   - 6 类任务分类 = fastlane/providers/llm_prisir.py:classify_task
#     返回 code_call / code_qa / creative / long / fast / general
#   - 给每类一个独立的 _action label,target 输出时 .capitalize()
#   - 与 M3.57 拆出的 code_call/code_qa 对齐(落不同的模型平台)
#
# 设计:
#   - 每类 30-40 个文本模板 + cross-intent mixing 边界样本
#   - 关键词 anchor + 模板,跟 data_prep_intents.py 同款
#   - 输出 schema 对齐 train_step1.py 已有 fields:
#       text / risk_label / jailbreak_label / _action
#       任务分类无风险维度,risk=safe,jailbreak=False
#
# 6 类关键词规则(对齐 classify_task):
#   code_call — 写代码/修代码/实现/编译/debug/报错 (有 ``` 或强动作)
#   code_qa   — 概念问答/原理/区别/为什么/解释 (聊 code 概念)
#   creative  — 写诗/故事/文案/起名/翻译剧本
#   long      — 长上下文(>3000 字符)文档/复审/长邮件
#   fast      — 短查询/天气/定义/翻译/缩写
#   general   — 兜底闲聊/咨询/情感/建议
#
# 用法:
#   python data_prep_task.py --output data_task.jsonl [--limit 600] [--mix-count 60]
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

# ------------------------------------------------------------
# 6 类任务常量(对齐 fastlane/providers/llm_prisir.py:classify_task)
# ------------------------------------------------------------
TASK_CODE_CALL = "code_call"
TASK_CODE_QA = "code_qa"
TASK_CREATIVE = "creative"
TASK_LONG = "long"
TASK_FAST = "fast"
TASK_GENERAL = "general"

# action label(与 TASK_* 同名,target 输出时 .capitalize())
TASK_TO_ACTION: dict[str, str] = {
    TASK_CODE_CALL: "code_call",
    TASK_CODE_QA: "code_qa",
    TASK_CREATIVE: "creative",
    TASK_LONG: "long",
    TASK_FAST: "fast",
    TASK_GENERAL: "general",
}


# ------------------------------------------------------------
# 关键词 anchor 集合(每类一组,_tier_of 按顺序匹配)
# ------------------------------------------------------------
ANCHORS: dict[str, list[str]] = {
    # code_qa 优先(聊概念 vs 写代码,问比写更易混淆)
    TASK_CODE_QA: [
        "什么是", "怎么理解", "原理", "区别", "为什么", "解释",
        "讲讲", "介绍", "优缺点", "对比", "怎么解决", "怎么用",
        "怎么看", "有什么", "是不是", "聊聊",
    ],
    # code_call 第二(写代码动作:实现/debug/修/编译)
    TASK_CODE_CALL: [
        "```", "写个", "写一个", "帮我写", "帮我写个",
        "实现", "实现一个", "debug", "修一个 bug", "编译",
        "跑报错", "报错信息", "异常处理", "实现一下", "写一下",
        "帮我实现", "写个 bash", "写个 dockerfile", "写个正则",
    ],
    # creative 第三(创造内容:诗/故事/文案/起名)
    TASK_CREATIVE: [
        "写首诗", "写首词", "编个故事", "讲个童话",
        "起个名", "起个标题", "起个口号", "想个 slogan",
        "文案", "广告语", "剧本", "台词", "续写",
        "创作一首", "帮我编", "故事续集",
    ],
    # fast 第四(短查询:天气/翻译/缩写/几岁)
    TASK_FAST: [
        "今天天气", "翻译", "缩写", "身高", "几岁", "多大",
        "今天日期", "汇率", "时间", "几点了",
    ],
    # general 兜底(闲聊/咨询/情感/建议)
    TASK_GENERAL: [
        "你好", "在吗", "聊聊", "心情", "累", "烦",
        "怎么办", "建议", "推荐", "怎么选", "能帮我",
    ],
    # long (单独靠长度触发,keyword 不显著;_tier_of 里优先)
}


# ------------------------------------------------------------
# 数据模板(每类 30+ 个种子,每次随机挑 + 拼上下文)
# ------------------------------------------------------------
TEMPLATES: dict[str, list[str]] = {
    TASK_CODE_CALL: [
        "帮我写个快速排序函数",
        "写一个 Python 装饰器",
        "实现一个链表反转",
        "debug 一下报 null 的 bug",
        "写个 bash 脚本批量备份",
        "帮我写个正则匹配邮箱",
        "写个 dockerfile 部署 node 应用",
        "实现一个 LRU 缓存",
        "帮我写个 React 组件渲染列表",
        "写一个 sql 查询最近 7 天登录的用户",
        "实现一个生产者消费者模型",
        "帮我写个爬虫抓取豆瓣电影 Top250",
        "写个 Go HTTP server 监听 8080",
        "实现一个二分查找函数",
        "帮我写个 Python 类,支持 __repr__",
        "debug 这个 null pointer 错误",
        "写个 TypeScript 泛型工具 Partial",
        "帮我写一个 docker-compose 多服务编排",
        "实现一个简单的 TCP echo server",
        "写个 Rust 函数读取文件所有行",
        "帮我写个 Kotlin 协程并发任务",
        "写一个 Java 单例模式",
        "实现一个 Python with 语句管理器",
        "帮我写一个 bash 一键部署脚本",
        "写个 perl 脚本解析日志",
        "实现一个 hash 表的插入和查询",
        "帮我写个 Vue3 setup 语法糖组件",
        "写一个 C++ 智能指针包装类",
        "实现一个最小栈(O(1) getMin)",
        "帮我写一段 SQL 联表查询",
        "写一个 Python 异步 HTTP 客户端",
        "实现一个简单的线程池",
        "帮我写个 redis pub/sub 订阅",
        "写个 webpack 自定义 loader",
        "实现一个 JS 防抖函数",
        "帮我写一个 GitHub Action workflow",
        "写个 protobuf 消息定义文件",
        "实现一个有限状态机",
        "帮我写个 Prometheus exporter",
        "写一个 Terraform module 部署 S3",
    ],
    TASK_CODE_QA: [
        "什么是装饰器?",
        "Python GIL 怎么解决?",
        "def 和 function 有什么区别?",
        "闭包的原理是什么?",
        "async 跟 await 怎么用?",
        "sql 索引 explain 怎么看?",
        "解释一下 Git rebase 的原理",
        "docker 容器怎么停止重写镜像",
        "Python 装饰器和闭包有什么区别?",
        "什么是协程?跟线程比有什么优劣?",
        "进程线程协程的区别是什么?",
        "什么是 SQL 索引的最左前缀原则?",
        "Promise 的三种状态怎么流转?",
        "TCP 三次握手四次挥手过程是怎样的?",
        "Python 内存管理机制是什么?",
        "什么是 CAP 定理?",
        "HTTP/1.1 HTTP/2 HTTP/3 有什么区别?",
        "虚拟内存的工作原理是什么?",
        "docker volume 和 bind mount 区别",
        "什么是 SSL/TLS 握手流程?",
        "Python 多线程为什么慢?GIL 是元凶?",
        "什么是 OAuth 2.0 授权流程?",
        "RESTful API 设计原则有哪些?",
        "什么是分布式锁?Redis 怎么实现?",
        "解释一下 B+ 树索引原理",
        "什么是 Kafka 的 ISR 机制?",
        "Go goroutine 的调度模型 GMP 怎么理解?",
        "什么是 Raft 一致性算法?",
        "React Hooks 为什么不能在条件里调用?",
        "什么是 JWT 跟 session 区别?",
        "解释下 MySQL MVCC 机制",
        "什么是 CDN 回源?原理?",
        "Python 中 __init__ 和 __new__ 区别",
        "什么是 ACID 事务?实现原理?",
        "Python 装饰器带参数怎么写?为什么这样?",
        "什么是 WebSocket?跟 HTTP 区别?",
        "Dockerfile 的 CMD 和 ENTRYPOINT 区别",
        "K8s 的 Pod 跟容器有什么区别?",
        "Python async for 怎么用?",
        "什么是零拷贝 sendfile?",
    ],
    TASK_CREATIVE: [
        "写首诗关于秋天的落叶",
        "编个故事哄孩子睡觉",
        "起个产品名叫'智能便签'",
        "想个 slogan 关于云端笔记",
        "写首词赞颂桂花",
        "写一段产品宣传文案",
        "编一个穿越小说开头",
        "起个公司名叫'快数据'",
        "写个广告语卖无线耳机",
        "写一首七律咏雪",
        "编一个鬼故事",
        "想个游戏名叫'勇者试炼'",
        "写个电影剧本开场",
        "起个 APP 名关于情绪管理",
        "写一段品牌故事关于咖啡",
        "编一个童话故事讲月亮",
        "想个标题关于时间管理",
        "写一首英文诗关于友谊",
        "编一个科幻故事讲时间旅行",
        "起个咖啡店名叫'静夜思'",
        "写个宣传文案推广瑜伽课程",
        "想个宠物店名字",
        "写一段产品介绍关于智能手表",
        "编一个侦探故事开头",
        "写一首rap歌词关于加班",
        "起个化妆品牌名叫'晨露'",
        "写个公众号开头关于人生规划",
        "编一个爱情短篇故事大纲",
        "写一段视频脚本介绍咖啡机",
        "想个房产中介公司名",
        "写一首儿歌关于春天",
        "编一个武侠小说开场",
        "写个 Slogan 关于新能源汽车",
        "起个奶茶店名叫'云雾山'",
        "写一段品牌slogan关于手工巧克力",
        "编一个末日生存小说开头",
        "想个少儿编程课品牌名",
        "写一段社交媒体推广文案",
        "编一个童话故事讲森林",
        "写首宋词关于元宵",
    ],
    TASK_FAST: [
        "今天天气怎么样",
        "翻译你好",
        "VSCode 的缩写是什么",
        "一米等于几英尺",
        "今天日期",
        "人民币美元汇率",
        "现在几点了",
        "明天是周几",
        "北京到上海多少公里",
        "地球到月球距离",
        "一杯咖啡多少卡路里",
        "一天有几个小时",
        "一年有多少天",
        "Python 是什么的缩写",
        "AI 的英文全称",
        "HTTP 是什么缩写",
        "API 是什么缩写",
        "JSON 是什么缩写",
        "USD 是什么货币",
        "CNY 是什么货币",
        "CEO 是什么缩写",
        "GPU 是什么缩写",
        "CPU 是什么缩写",
        "RAM 是什么缩写",
        "SSD 跟 HDD 区别",
        "WiFi 是什么缩写",
        "VIP 是什么缩写",
        "DIY 是什么缩写",
        "一米几公分",
        "一公斤几斤",
        "现在几点",
        "今天股市开吗",
        "美元汇率",
        "PM 是什么意思",
        "AM 是什么意思",
        "GDP 是什么缩写",
        "海拔最高山",
        "世界上最长河",
        "中国首都是哪里",
        "日本首都是哪里",
    ],
    TASK_GENERAL: [
        "你好呀",
        "在吗",
        "今天心情不太好",
        "工作好累啊",
        "推荐几本好看的书",
        "周末想出去走走",
        "孩子不听话怎么办",
        "推荐一部好看的电影",
        "怎么提高睡眠质量",
        "新能源汽车哪个牌子好",
        "考研需要准备什么",
        "怎么选合适的大学专业",
        "推荐几款好用的耳机",
        "养狗需要注意什么",
        "适合冬天养的花",
        "晚上吃什么好",
        "推荐一家好吃的川菜馆",
        "想找个人说说话",
        "最近压力大怎么办",
        "感觉焦虑怎么缓解",
        "想辞职怎么办",
        "被同事误解怎么处理",
        "怎样提高英语口语",
        "推荐学习方法",
        "理财小白怎么入门",
        "健身新手怎么开始",
        "面试紧张怎么缓解",
        "怎么跟陌生人聊天",
        "如何处理亲密关系",
        "怎么培养小孩的兴趣",
        "老人独居怎么办",
        "异地恋怎么维持",
        "婚姻出现危机怎么办",
        "朋友借钱不还怎么办",
        "搬家怎么处理旧物",
        "装修风格怎么选",
        "猫咪呕吐怎么办",
        "孩子挑食怎么办",
        "家用车怎么选",
        "保险买哪种好",
    ],
}


# ------------------------------------------------------------
# long 类:长文本(>3000 字符)— 用模板拼长文档摘要
# ------------------------------------------------------------
LONG_DOC_TEMPLATE: str = (
    "请帮我审阅以下文档内容,提取关键信息并给出修改建议:\n\n"
    "{body}\n\n"
    "请按以下结构组织回复:\n"
    "1. 文档摘要\n"
    "2. 关键论点\n"
    "3. 逻辑漏洞或不一致之处\n"
    "4. 修改建议\n"
    "5. 结论\n"
)


def _make_long_doc(rng: random.Random, target_chars: int = 3500) -> str:
    """生成 ~3500 字符的模拟长文档。"""
    base_paras = [
        "本章详细讨论了分布式系统的核心理论与实践经验。首先,我们回顾了 CAP 定理的基本概念,"
        "即在一致性(Consistency)、可用性(Availability)和分区容忍性(Partition tolerance)"
        "三者之间,任何分布式系统最多只能同时满足其中两项。这一理论由 Eric Brewer 在 2000 "
        "年提出,并在 2002 年由 Seth Gilbert 和 Nancy Lynch 给出形式化证明。在实际工程中,"
        "由于网络分区不可避免,大多数系统选择在一致性和可用性之间做出权衡。",
        "接下来,我们深入分析了 Raft 一致性算法的实现细节。Raft 通过将一致性分解为三个"
        "子问题:领导者选举(Leader Election)、日志复制(Log Replication)和安全性(Safety),"
        "显著降低了 Paxos 算法的理解和实现难度。Raft 算法的核心是任期(Term)机制,每个任期"
        "最多存在一个领导者,领导者通过心跳机制维持其权威。当领导者失效时,集群会在随机超时"
        "后触发新的选举,确保系统的可用性。",
        "在工程实践方面,我们对比了几种主流的分布式协调服务,包括 Apache ZooKeeper、"
        "etcd 和 Consul。ZooKeeper 采用 ZAB 协议,提供了强一致性的 KV 存储和临时节点"
        "机制,广泛应用于 Hadoop、Kafka 等大数据生态。etcd 基于 Raft 实现,提供了更现代的"
        "API 和更好的性能,被 Kubernetes 用作其后端存储。Consul 则在服务发现和健康检查方面"
        "提供了更丰富的功能,适合微服务架构下的服务网格场景。",
        "性能优化是分布式系统的永恒话题。我们讨论了几种常见的优化策略:读写分离通过将"
        "写操作集中到主节点、读操作分发到从节点来提升系统的读吞吐能力;数据分片(Sharding)"
        "通过将数据按特定规则分散到多个节点来突破单机存储和性能瓶颈;缓存层(Caching)通过"
        "在内存中保存热点数据来减少对后端存储的访问压力。",
        "最后,我们探讨了分布式系统中的故障处理机制。常见的故障类型包括节点故障、网络分区、"
        "脑裂(Split Brain)等。应对这些故障需要设计完善的监控告警体系、自动恢复流程和"
        "人工干预预案。混沌工程(Chaos Engineering)作为一种主动注入故障的方法,可以帮助团队"
        "在生产环境之前发现系统的脆弱点,提高系统的整体韧性。",
    ]
    body = ""
    while len(body) < target_chars:
        body += rng.choice(base_paras) + "\n\n"
    return LONG_DOC_TEMPLATE.format(body=body.strip())


# ------------------------------------------------------------
# 边界样本(cross-task mixing)— 制造主任务歧义
# ------------------------------------------------------------
def _cross_task_mix(count: int = 60, seed: int = 43) -> list[dict]:
    """短文本带多任务信号,选一个主任务。"""
    rng = random.Random(seed)
    pairs = [
        # (主任务, 文本)
        (TASK_CODE_QA, "为什么 Python 不适合写大型项目"),  # qa
        (TASK_CODE_QA, "什么是 LLM?简单说"),  # qa+fast
        (TASK_CODE_CALL, "帮我写个脚本抓网页数据"),  # call+tool
        (TASK_CREATIVE, "帮我想个 Python 课程的 slogan"),  # creative+code
        (TASK_GENERAL, "今天天气怎么样,推荐个出游地"),  # general+fast
        (TASK_FAST, "现在几点了?该睡觉吗?"),  # fast
        (TASK_GENERAL, "心情不好,推荐首歌"),  # general+creative
        (TASK_CODE_QA, "解释一下为什么这个正则匹配不了中文"),
        (TASK_CODE_CALL, "写个 sql 找出最近 30 天活跃用户"),  # call+qa
        (TASK_CREATIVE, "用 Python 给我女朋友写首生日诗"),  # creative+code
        (TASK_GENERAL, "我今天被领导骂了"),
        (TASK_CODE_QA, "Python 装饰器跟 Java 注解有什么区别?"),
        (TASK_FAST, "Python 缩写的全称是什么"),
        (TASK_CODE_CALL, "帮我 debug 这段代码,报 KeyError"),
        (TASK_GENERAL, "你觉得养猫好还是养狗好"),  # general
        (TASK_CREATIVE, "帮我起个名字,关于星际旅行主题"),
    ]
    samples: list[dict] = []
    for _ in range(count):
        primary, text = rng.choice(pairs)
        samples.append({
            "_text": text,
            "_history": "",
            "risk_label": "safe",
            "jailbreak_label": False,
            "_action": TASK_TO_ACTION[primary],
            "_intent": primary,
            "_mix": primary,
        })
    return samples


# ------------------------------------------------------------
# 分类函数
# ------------------------------------------------------------
def _tier_of(text: str) -> str:
    """基于 anchor 关键词判定任务(顺序匹配)。

    关键:long 优先(长度阈值触发),code_qa > code_call(聊优先于写),
    creative > fast > general。
    """
    # long 优先(>3000 字符直接归 long)
    if len(text) > 3000:
        return TASK_LONG
    # code_qa 优先(避免"什么是装饰器"被误归 code_call)
    for kw in ANCHORS[TASK_CODE_QA]:
        if kw in text:
            # 必须同时含 code 词才归 code_qa
            code_words = ("python", "code", "sql", "java", "js", "api", "代码",
                          "装饰", "协程", "闭包", "异步", "线程", "进程",
                          "docker", "git", "json", "tcp", "udp", "http", "算法",
                          "函数", "数组", "字典", "内存", "索引", "锁", "事务",
                          "promise", "async", "await", "callback", "正则")
            if any(w in text.lower() or w in text for w in code_words):
                return TASK_CODE_QA
    # code_call 第二
    for kw in ANCHORS[TASK_CODE_CALL]:
        if kw in text:
            return TASK_CODE_CALL
    # creative 第三
    for kw in ANCHORS[TASK_CREATIVE]:
        if kw in text:
            return TASK_CREATIVE
    # fast 第四
    for kw in ANCHORS[TASK_FAST]:
        if kw in text:
            return TASK_FAST
    # general 兜底
    return TASK_GENERAL


def _build_text(text: str, history: str = "") -> str:
    """组装跟训练数据一致的 prompt 段。"""
    h = history if history else "(无历史)"
    return (f"用户消息: {text}\n"
            f"上下文: {h}")


# ------------------------------------------------------------
# 多样性采样
# ------------------------------------------------------------
TASK_MIN_TARGETS: dict[str, int] = {
    TASK_CODE_CALL: 100,
    TASK_CODE_QA: 100,
    TASK_CREATIVE: 100,
    TASK_LONG: 80,
    TASK_FAST: 100,
    TASK_GENERAL: 100,
}


def _balance_diversity(samples: list[dict], limit: int) -> list[dict]:
    """每类 cap 到 TASK_MIN_TARGETS,总量 ≤ limit。"""
    rng = random.Random(42)
    by_task: dict[str, list[dict]] = {}
    for s in samples:
        by_task.setdefault(s["_intent"], []).append(s)

    picked: list[dict] = []
    for task, items in by_task.items():
        target = TASK_MIN_TARGETS.get(task, 100)
        picked.extend(items[:target])

    rng.shuffle(picked)
    return picked[:limit]


# ------------------------------------------------------------
# 上下文(短历史)生成器
# ------------------------------------------------------------
HISTORY_PHRASES: list[str] = [
    "(无历史)",
    "用户最近问了 2 个问题",
    "用户是开发者,经常问技术问题",
    "用户用 desktop 客户端,Windows",
    "正在陪聊模式,用户心情不错",
    "上一轮用户刚问过推荐电影",
    "用户偏好简短的回复",
    "用户是日语翻译,常问日语相关",
    "用户在调试 Python 项目",
    "用户在写代码,需要陪练",
]


def _random_history(rng: random.Random) -> str:
    return rng.choice(HISTORY_PHRASES)


# ------------------------------------------------------------
# 主生成函数
# ------------------------------------------------------------
def generate(max_raw: int = 5000, seed: int = 42,
             include_long: bool = True) -> list[dict]:
    """生成候选样本。"""
    rng = random.Random(seed)
    samples: list[dict] = []

    # long 类先(否则短任务会占满 max_raw 配额)
    long_count = max_raw // 5 if include_long else 0
    if include_long:
        for i in range(long_count):
            text = _make_long_doc(rng, target_chars=3500 + (i % 5) * 200)
            history = _random_history(rng)
            samples.append({
                "_text": text,
                "_history": history,
                "risk_label": "safe",
                "jailbreak_label": False,
                "_action": TASK_TO_ACTION[TASK_LONG],
                "_intent": TASK_LONG,
            })

    # 短文本任务(code_call/code_qa/creative/fast/general)
    short_tasks = [TASK_CODE_CALL, TASK_CODE_QA, TASK_CREATIVE,
                   TASK_FAST, TASK_GENERAL]
    short_max = max_raw - long_count
    per_task = short_max // len(short_tasks)
    for task in short_tasks:
        templates = TEMPLATES[task]
        for _ in range(per_task):
            text = rng.choice(templates)
            history = _random_history(rng)
            tier = _tier_of(text)
            # 兜底:若 _tier_of 与 task 不一致,以 _tier_of 为准(制造边界 case)
            samples.append({
                "_text": text,
                "_history": history,
                "risk_label": "safe",
                "jailbreak_label": False,
                "_action": TASK_TO_ACTION[tier],
                "_intent": tier,
            })

    return samples


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="M3.58 任务分类训练集准备")
    ap.add_argument("--output", default="data_task.jsonl")
    ap.add_argument("--limit", type=int, default=600)
    ap.add_argument("--max-raw", type=int, default=5000)
    ap.add_argument("--mix-count", type=int, default=60,
                    help="cross-task mixing 边界样本数(默认 60)")
    args = ap.parse_args()

    print(f"[1/4] 规则生成(最多 {args.max_raw} 条)...")
    t0 = time.time()
    samples = generate(max_raw=args.max_raw)
    print(f"  + 候选 {len(samples)} 条,耗时 {time.time()-t0:.1f}s")

    by_task: dict[str, int] = {}
    for s in samples:
        by_task[s["_intent"]] = by_task.get(s["_intent"], 0) + 1
    print(f"  by_task: {by_task}")

    if args.mix_count > 0:
        print(f"[2/4] cross-task mixing 加 {args.mix_count} 条边界样本...")
        mix = _cross_task_mix(count=args.mix_count)
        samples.extend(mix)
        print(f"  + 混合后 {len(samples)} 条")
    else:
        print("[2/4] cross-task mixing 关闭")

    print(f"[3/4] 多样性平衡(各 task cap 到 TASK_MIN_TARGETS,总 ≤ {args.limit})...")
    balanced = _balance_diversity(samples, args.limit)
    print(f"  + {len(balanced)} 条入训练集")

    print(f"[4/4] 写盘: {args.output}")
    out = Path(args.output)
    final_by_task: dict[str, int] = {}
    with out.open("w", encoding="utf-8") as f:
        for s in balanced:
            row = {
                "text": _build_text(s["_text"], s["_history"]),
                "risk_label": s["risk_label"],
                "jailbreak_label": s["jailbreak_label"],
                "_action": s["_action"],
                "_intent": s["_intent"],
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            final_by_task[s["_intent"]] = final_by_task.get(s["_intent"], 0) + 1
    print(f"\n  by_task: {final_by_task}")
    print(f"  ✅ 写盘: {args.output} ({out.stat().st_size//1024} KB, {len(balanced)} 条)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())