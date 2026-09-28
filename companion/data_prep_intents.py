#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# data_prep_intents.py — M3.50 聊天意图分类训练集准备(2026-09-23)
#
# 目的:
#   - 5 类意图 = companion_jev.py 的 INTENT_CHAT/CODE/SEARCH/TOOL_CALL/ROLEPLAY
#   - 用 chat/code/search/tool_call/roleplay 5 个 action label
#   - 与 companion Jev ask_intent 对齐:类名/边界一致
#
# 设计:
#   - 每类 30-40 个文本模板 × 2-3 个变体
#   - 关键词 + 模板锚定;_tier_of() 先看 action 信号词
#   - 加 cross-intent mixing 50 条边界样本(让模型区分主意图)
#   - 输出 schema 对齐 train_step1.py 已有 fields:
#       text / risk_label / jailbreak_label / _action
#       风险等级对所有 intent = safe(意图分类无风险维度)
#
# 5 类关键词规则(由 bench_intents.py:47-107 50 条样本反推):
#   chat      — 你好 / 怎么样 / 心情 / 周末 / 陪 / 今天 / 哈哈 / 真逗
#   code      — 怎么写 / 报错 / 代码 / Python / 算法 / SQL / Docker / 装饰器
#   search    — 为什么 / 多少 / 推荐 / 怎么办 / 什么是 / 几岁 / 怎么道歉
#   tool_call — 帮我打开 / 关闭 / 重启 / 删除 / 截图 / 设置 / 调到 / 发邮件
#   roleplay  — 扮演 / 假装 / 讲个 / 故事 / 继续讲 / 演一段 / 模仿 / 当军师
#
# 噪声边界样本(cross-intent mixing):
#   - search + chat(聊事实但有情感)
#   - tool_call + code(要写代码也要执行)
#   - chat + roleplay(边聊边演)
#
# 用法:
#   python data_prep_intents.py --output data_intents.jsonl [--limit 600] [--mix-count 50]
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

# ------------------------------------------------------------
# 5 类意图常量(对齐 companion_jev.py:115-125)
# ------------------------------------------------------------
INTENT_CHAT = "chat"
INTENT_CODE = "code"
INTENT_SEARCH = "search"
INTENT_TOOL_CALL = "tool_call"
INTENT_ROLEPLAY = "roleplay"

# action label(与 INTENT_* 同名,但 target 输出时 .capitalize())
INTENT_TO_ACTION: dict[str, str] = {
    INTENT_CHAT: "chat",
    INTENT_CODE: "code",
    INTENT_SEARCH: "search",
    INTENT_TOOL_CALL: "tool_call",
    INTENT_ROLEPLAY: "roleplay",
}


# ------------------------------------------------------------
# 关键词 anchor 集合(每类一组,_tier_of 按顺序匹配)
# ------------------------------------------------------------
ANCHORS: dict[str, list[str]] = {
    # roleplay 优先(强信号:扮演/假装/讲个/故事/继续讲/演一段/模仿/当军师)
    INTENT_ROLEPLAY: [
        "扮演", "假装", "讲个", "讲故事", "继续讲", "演一段",
        "模仿", "当军师", "角色扮演", "海盗", "李白", "苏东坡",
            "鬼故事", "童话故事", "穿越小说", "面试官",
    ],
    # tool_call 第二优先(强动词:打开/关闭/重启/删除/截图/设置/调到/发邮件)
    INTENT_TOOL_CALL: [
        "帮我打开", "帮我关闭", "帮我重启", "帮我删除",
        "帮我截图", "帮我设置", "帮我调到", "帮我发",
        "打开浏览器", "关闭所有窗口", "重启电脑", "删除 C 盘",
        "截图当前屏幕", "把音量调到", "VS Code", "PDF 转 Word",
        "给张三发邮件", "桌面文件", "调亮度", "调音量",
    ],
    # code 第三优先(技术问答:Python/算法/装饰器/索引/报错)
    INTENT_CODE: [
        "Python", "装饰器", "SQL", "Docker", "TypeScript",
        "interface", "useEffect", "git rebase", "异步和并发",
        "Cannot read property", "斐波那契", "多阶段构建",
        "生命周期", "TypeError", "KeyError", "ValueError",
        "代码", "报错", "算法", "怎么写", "优化索引",
        "递归", "RUST", "Rust", "API", "函数",
    ],
    # search 第四(常识/事实/推荐/经验:为什么/怎么办/几岁/推荐)
    INTENT_SEARCH: [
        "为什么", "是什么", "什么是", "怎么办",
        "怎么道歉", "几岁", "几岁开始", "有多少", "哪一年",
        "推荐", "推荐几", "故事",
        "天气", "股票", "理财", "钢琴",
        "人口", "二战", "量子计算", "育儿", "听不听话",
    ],
    # chat 最后(兜底,所有不带以上 anchor 的"非明确"中文)
    INTENT_CHAT: [
        "你好", "今天", "周末", "心情", "哈哈", "真逗", "陪",
        "聊聊", "想你", "喜欢", "叫啥", "叫什么",
    ],
}


# ------------------------------------------------------------
# 数据模板(每类 30+ 个种子,每次随机挑 + 拼上下文)
# ------------------------------------------------------------
TEMPLATES: dict[str, list[str]] = {
    INTENT_CHAT: [
        # 问候 / 寒暄
        "你好呀",
        "在吗?",
        "今天怎么样?",
        "周末过得怎么样?",
        "今天心情不太好,有点难过",
        "哈哈,你真逗",
        "我刚吃完饭,有点困",
        "你在干嘛呢?",
        "陪我聊会儿天吧",
        "我心情不太好,能聊聊吗",
        "周末想出去走走",
        "今天天气真好呀",
        "我喜欢你这种说话风格",
        "你叫什么名字?",
        "你是 AI 吗",
        "感觉最近压力有点大",
        "工作好累啊",
        "哈哈,这个有意思",
        "你这么说话真有味道",
        "今天好热啊",
        "明天要早起",
        "我在喝咖啡",
        "下班了",
        "想睡觉了",
        "晚上好",
        "早安",
        "晚安",
        "中秋快乐",
        "新年好",
        "今天被老板夸了,开心",
        "心情有点失落",
        "最近好无聊",
        "想找个人说说话",
    ],
    INTENT_CODE: [
        "帮我写一个 Python 装饰器",
        "解释下 React 的 useEffect",
        "SQL 怎么优化索引?",
        "教我用 git rebase",
        "Python 异步和并发的区别",
        "TypeScript 中 interface 和 type 的区别?",
        "这个报错 'Cannot read property of undefined' 怎么修",
        "帮我写个递归求斐波那契",
        "Docker 怎么配置多阶段构建",
        "Rust 的生命周期怎么理解",
        "Python 里 f-string 怎么格式化数字?",
        "怎么用 SQLAlchemy 写事务?",
        "帮我写个正则匹配邮箱",
        "TypeError: Cannot read property 'map' of undefined 怎么修",
        "Python 的生成器和迭代器有什么区别",
        "JavaScript 闭包怎么理解",
        "Go 语言的 channel 用法",
        "Rust 所有权怎么理解",
        "C++ 智能指针怎么用",
        "写个 Bash 脚本批量重命名文件",
        "SQL JOIN 三种类型区别",
        "Docker Compose 怎么写 depends_on",
        "帮我 review 一段 Python 代码",
        "TypeScript 怎么定义泛型",
        "K8s Pod 和 Deployment 区别",
        "Redis 的 pipeline 怎么用",
        "为什么这个 Python 脚本会内存泄漏",
        "Python 怎么捕获 KeyboardInterrupt",
        "帮我写个 React 组件",
        "Linux 下怎么查找大文件",
        "怎么用 pandas 读 parquet 文件",
        "怎么在 Python 里调 HTTP API",
        "代码里有几个 bug 能帮我看吗",
        "这个算法的时间复杂度是多少",
        "帮我看看这段 SQL 哪里错了",
    ],
    INTENT_SEARCH: [
        "为什么天空是蓝色的?",
        "今天股市怎么样",
        "推荐一本好看的小说",
        "推荐几本理财的书",
        "孩子不听话怎么办",
        "怎么跟女朋友道歉比较好",
        "我想学钢琴,几岁开始好",
        "什么是量子计算?",
        "北京的人口有多少",
        "二战是哪一年结束的?",
        "为什么人会做梦",
        "咖啡喝多了有什么坏处",
        "什么是黑洞",
        "推荐一部好看的电影",
        "学英语有什么技巧",
        "怎么提高睡眠质量",
        "适合冬天养的花",
        "推荐几本育儿书",
        "新能源汽车哪个牌子好",
        "如何快速学会游泳",
        "宇宙是怎么诞生的",
        "推荐几部好看的纪录片",
        "为什么会有四季",
        "猫能活多少年",
        "考研需要准备什么",
        "怎么选合适的大学专业",
        "什么是机器学习",
        "推荐几本历史书",
        "为什么海水是咸的",
        "怎么让孩子爱上阅读",
        "推荐几款好用的耳机",
        "月亮为什么会变圆缺",
        "养狗需要注意什么",
        "哪些国家免签",
        "推荐几个适合周末的去处",
    ],
    INTENT_TOOL_CALL: [
        "帮我打开浏览器",
        "关闭所有窗口",
        "把音量调到 50%",
        "删除 C 盘下 temp 文件夹",
        "截图当前屏幕",
        "给张三发邮件,主题是周报",
        "重启电脑",
        "打开 VS Code 打开项目 D:/work",
        "搜索我的桌面文件名为 '合同' 的文档",
        "把这个 PDF 转成 Word",
        "帮我打开网易云音乐",
        "帮我打开终端",
        "帮我调低屏幕亮度",
        "把窗口最大化",
        "关闭 Chrome",
        "新建一个 Word 文档",
        "帮我打开计算器",
        "打开 D 盘的工作目录",
        "给我截一张全屏图",
        "把这段文字复制到剪贴板",
        "帮我翻译当前页面",
        "打开 Notion",
        "设置定时关机",
        "帮我清空回收站",
        "关闭 Outlook",
        "打开 Postman",
        "帮我备份 D 盘到 E 盘",
        "截图后保存到桌面",
        "帮我新建一个日历提醒",
        "调大声音",
        "帮我打开任务管理器",
        "给李四发一条微信",
        "搜索我电脑里所有 .docx 文件",
        "把 PDF 拆分成单页",
        "帮我关掉所有 Chrome 标签",
        "打开 Git Bash",
        "给我开一个 PowerShell",
    ],
    INTENT_ROLEPLAY: [
        "讲个鬼故事给我听",
        "假装你是李白,我们来对诗",
        "玩个角色扮演,你是黑骑士",
        "继续讲那个穿越小说的剧情",
        "假设你在中世纪,我是国王,你来当军师",
        "演一段面试官,我要练习面试",
        "扮演我的英语老师,我们用英语对话",
        "讲个童话故事哄孩子睡觉",
        "你是海盗,我也是海盗,我们抢宝藏",
        "模仿苏东坡的语气给我写首词",
        "讲个笑话",
        "扮演一个老中医,给我开个方子",
        "假设你是乔布斯,你怎么评价 iPhone",
        "扮演一名律师,我要咨询劳动仲裁",
        "你是我的英语口语陪练,跟我对话",
        "演一段历史老师,讲讲玄武门之变",
        "你扮演外星人,向我介绍你的星球",
        "假装你是我的心理咨询师",
        "扮演一个饭店老板,跟我对账",
        "继续讲那个修仙故事",
        "模仿鲁迅的文风给我写段散文",
        "扮演机器猫,我问哆啦 A 梦问题",
        "你是李白,我是杜甫,我们来对诗",
        "讲个睡前故事",
        "扮演一只猫,跟我聊天",
        "你是大厨,我是食客,推荐今日菜",
        "演一段医生,我要咨询咳嗽",
        "假装你是马斯克,接受采访",
        "扮演一名警察,我要报案",
        "你是侦探福尔摩斯,我请你破案",
        "讲个简短寓言故事",
        "扮演穿越剧里的我,在宫中扮",
        "你扮演红楼梦里的林黛玉",
    ],
}


# ------------------------------------------------------------
# 边界噪声样本(cross-intent mixing)— 制造主意图歧义
# ------------------------------------------------------------
def _cross_intent_mix(count: int = 50, seed: int = 43) -> list[dict]:
    """短文本带多意图信号,选一个主意图。

    设计:把 "search + chat" / "tool_call + code" / "chat + roleplay"
    三种组合的种子混在一起,让模型学到"取主意图"而非"全选"。
    """
    rng = random.Random(seed)
    pairs = [
        # (主意图, 文本)
        (INTENT_SEARCH, "为什么今天心情不好呢"),
        (INTENT_SEARCH, "推荐一本讲 Python 编程的书"),  # search + code
        (INTENT_CHAT, "今天天气怎么样?感觉心情不太好"),  # chat + search
        (INTENT_CHAT, "哈哈你怎么扮演这么像"),  # chat + roleplay
        (INTENT_CODE, "帮我写个 Python 脚本打开指定文件夹"),  # code + tool_call
        (INTENT_TOOL_CALL, "帮我搜索一下怎么写 Python 装饰器"),  # tool_call + code
        (INTENT_ROLEPLAY, "假设你是 Python 老师,教我写装饰器"),  # roleplay + code
        (INTENT_SEARCH, "推荐个编辑器,推荐 VSCode"),  # search + tool_call
        (INTENT_CHAT, "今天讲个笑话呗,心情不好"),
        (INTENT_TOOL_CALL, "给我讲个故事然后关闭应用"),
        (INTENT_CODE, "怎么用 Python 写个爬虫"),  # 纯 code 锚定
        (INTENT_SEARCH, "为什么 Python 不适合写大型项目"),
        (INTENT_ROLEPLAY, "假装你是产品经理,我说需求"),
        (INTENT_CHAT, "今天我被领导骂了"),
        (INTENT_TOOL_CALL, "帮我把这段 Python 代码粘贴到剪贴板"),
    ]
    samples: list[dict] = []
    for _ in range(count):
        primary, text = rng.choice(pairs)
        samples.append({
            "_text": text,
            "_history": "",
            "risk_label": "safe",
            "jailbreak_label": False,
            "_action": INTENT_TO_ACTION[primary],
            "_intent": primary,
            "_mix": primary,
        })
    return samples


# ------------------------------------------------------------
# 分类函数
# ------------------------------------------------------------
def _tier_of(text: str) -> str:
    """基于 anchor 关键词判定意图(顺序匹配,roleplay 最先)。"""
    # roleplay 优先(强信号,避免"讲个笑话"被判 chat)
    for kw in ANCHORS[INTENT_ROLEPLAY]:
        if kw in text:
            return INTENT_ROLEPLAY
    # tool_call 第二
    for kw in ANCHORS[INTENT_TOOL_CALL]:
        if kw in text:
            return INTENT_TOOL_CALL
    # code 第三
    for kw in ANCHORS[INTENT_CODE]:
        if kw in text:
            return INTENT_CODE
    # search 第四
    for kw in ANCHORS[INTENT_SEARCH]:
        if kw in text:
            return INTENT_SEARCH
    # chat 兜底
    return INTENT_CHAT


def _build_text(text: str, history: str = "") -> str:
    """组装跟训练数据一致的 prompt 段。

    设计:训练时固定段名 "用户消息" + "上下文",让模型学会从这 2 个字段取信号。
    """
    h = history if history else "(无历史)"
    return (f"用户消息: {text}\n"
            f"上下文: {h}")


# ------------------------------------------------------------
# 多样性采样
# ------------------------------------------------------------
RISK_MIN_TARGETS: dict[str, int] = {
    INTENT_CHAT: 120,
    INTENT_CODE: 120,
    INTENT_SEARCH: 120,
    INTENT_TOOL_CALL: 120,
    INTENT_ROLEPLAY: 120,
}


def _balance_diversity(samples: list[dict], limit: int) -> list[dict]:
    """每类 cap 到 RISK_MIN_TARGETS,总量 ≤ limit。"""
    rng = random.Random(42)
    by_intent: dict[str, list[dict]] = {}
    for s in samples:
        by_intent.setdefault(s["_intent"], []).append(s)

    picked: list[dict] = []
    for intent, items in by_intent.items():
        target = RISK_MIN_TARGETS.get(intent, 100)
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
def generate(max_raw: int = 5000, seed: int = 42) -> list[dict]:
    """生成候选样本。"""
    rng = random.Random(seed)
    samples: list[dict] = []

    for intent, templates in TEMPLATES.items():
        per_intent = max_raw // len(TEMPLATES)
        for _ in range(per_intent):
            text = rng.choice(templates)
            history = _random_history(rng)
            tier = _tier_of(text)
            # 兜底:若 _tier_of 与意图不一致,以 _tier_of 为准(制造边界 case)
            samples.append({
                "_text": text,
                "_history": history,
                "risk_label": "safe",
                "jailbreak_label": False,
                "_action": INTENT_TO_ACTION[tier],
                "_intent": tier,
            })
            if len(samples) >= max_raw:
                return samples
    return samples


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="M3.50 聊天意图分类训练集准备")
    ap.add_argument("--output", default="data_intents.jsonl")
    ap.add_argument("--limit", type=int, default=600)
    ap.add_argument("--max-raw", type=int, default=5000)
    ap.add_argument("--mix-count", type=int, default=50,
                    help="cross-intent mixing 边界样本数(默认 50)")
    args = ap.parse_args()

    print(f"[1/4] 规则生成(最多 {args.max_raw} 条)...")
    t0 = time.time()
    samples = generate(max_raw=args.max_raw)
    print(f"  + 候选 {len(samples)} 条,耗时 {time.time()-t0:.1f}s")

    by_intent: dict[str, int] = {}
    for s in samples:
        by_intent[s["_intent"]] = by_intent.get(s["_intent"], 0) + 1
    print(f"  by_intent: {by_intent}")

    if args.mix_count > 0:
        print(f"[2/4] cross-intent mixing 加 {args.mix_count} 条边界样本...")
        mix = _cross_intent_mix(count=args.mix_count)
        samples.extend(mix)
        print(f"  + 混合后 {len(samples)} 条")
    else:
        print("[2/4] cross-intent mixing 关闭")

    print(f"[3/4] 多样性平衡(各 intent cap 到 RISK_MIN_TARGETS,总 ≤ {args.limit})...")
    balanced = _balance_diversity(samples, args.limit)
    print(f"  + {len(balanced)} 条入训练集")

    print(f"[4/4] 写盘: {args.output}")
    out = Path(args.output)
    final_by_intent: dict[str, int] = {}
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
            final_by_intent[s["_intent"]] = final_by_intent.get(s["_intent"], 0) + 1
    print(f"\n  by_intent: {final_by_intent}")
    print(f"  ✅ 写盘: {args.output} ({out.stat().st_size//1024} KB, {len(balanced)} 条)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())