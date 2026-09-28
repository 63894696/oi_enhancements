#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_prep_dispatch_route.py — M3.84 team_lead 8-intent 训练集(2026-09-25)

目的:
  - 从 prisiragent_harness_training/*.jsonl 抽真实 trace task
  - 合成 8 类意图(chat / code / search / tool_call / roleplay / plan / security / ops)
  - 给 team_lead_tools._laya_route_to_prisir() 的二次分流换 head 提供训练语料

数据来源:
  - 主:C:/Users/Administrator/.claude/oiagent_harness_training/*.jsonl (~260 条 trace)
  - 辅:oiagent 真实任务 + 模板合成(给 0 覆盖类别补样本)

8 类目标(对齐 routing.yaml 主流 intent 子集):
  chat       — 闲聊/陪伴/问候(M3.50 intents)
  code       — 代码实现/Python/算法(M3.57 code_call)
  search     — 论文/学术/常识(M3.50 search)
  tool_call  — 帮我打开/关闭/重启/截图(M3.50 tool_call)
  roleplay   — 扮演/讲个故事/模仿(M3.50 roleplay)
  plan       — 计划/路线图/拆解/阶段
  security   — 漏洞/注入/secret/auth(M3.73 OPS_FORCE_OVERRIDE)
  ops        — 进程/kill/清理/性能/端口(M3.73 process_control)

复用:
  - data_prep_intents.py 的 chat/code/search/tool_call/roleplay 5 类(companion/)
  - data_prep_deadlock.py 的轨迹/快照生成风格(本脚本用纯文本模板,不是轨迹)

输出:
  C:/Users/Administrator/oi_enhancements/mcp_prisiragent_server/data/data_dispatch_route_train.jsonl
  C:/Users/Administrator/oi_enhancements/mcp_prisiragent_server/data/data_dispatch_route_eval.jsonl

用法:
  python data_prep_dispatch_route.py [--limit 800] [--mix-count 80]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_HARNESS_DIR = Path("C:/Users/Administrator/.claude/oiagent_harness_training")
_OUT_DIR = _HERE / "data"


# ============================================================
# 8 类意图常量
# ============================================================
INTENT_CHAT = "chat"
INTENT_CODE = "code"
INTENT_SEARCH = "search"
INTENT_TOOL_CALL = "tool_call"
INTENT_ROLEPLAY = "roleplay"
INTENT_PLAN = "plan"
INTENT_SECURITY = "security"
INTENT_OPS = "ops"

ALL_INTENTS = (
    INTENT_CHAT, INTENT_CODE, INTENT_SEARCH, INTENT_TOOL_CALL,
    INTENT_ROLEPLAY, INTENT_PLAN, INTENT_SECURITY, INTENT_OPS,
)

# intent → routing.yaml intent name(确保与现有 yaml 对齐)
INTENT_TO_ROUTING_KEY = {
    INTENT_CHAT: "default",           # 闲聊 → Explore default
    INTENT_CODE: "code_implement",    # 代码 → code_implement
    INTENT_SEARCH: "search",          # 搜索 → search
    INTENT_TOOL_CALL: "default",      # 工具 → Explore(默认动作)
    INTENT_ROLEPLAY: "content",       # 角色扮演 → content
    INTENT_PLAN: "plan",              # 计划 → plan
    INTENT_SECURITY: "security",      # 安全 → security
    INTENT_OPS: "process_control",    # 运维 → process_control
}


# ============================================================
# routing.yaml intent → 我们的 8 类(逆向映射)
# ============================================================
_ROUTING_TO_INTENT = {v: k for k, v in INTENT_TO_ROUTING_KEY.items()}
# 多个 routing intent 可能映射到同一类 → 反向查表会丢,显式列举
_ROUTING_TO_INTENT.update({
    "python_review": INTENT_CODE,
    "code_review": INTENT_CODE,
    "architecture": INTENT_CODE,
    "llm_integration": INTENT_CODE,
    "harness_code_review": INTENT_CODE,
    "harness_multi_agent": INTENT_CODE,
    "deepseek_harness": INTENT_CODE,
    "explore": INTENT_SEARCH,
    "vision": INTENT_TOOL_CALL,        # 看图本质是 tool_call
})


# ============================================================
# 关键词 anchor 词典(每类一组,与 M3.50 intents 思路一致)
# ============================================================
ANCHORS: dict[str, list[str]] = {
    INTENT_OPS: [
        "进程", "kill ", "kill-", "kill进程",
        "watchdog", "调度", "probalance", "ananicy", "processlasso",
        "清理", "盘", "垃圾", "卸载", "删除文件", "磁盘",
        "rm ", "rm-", "del ", "del-",
        "性能", "监控", "cpu", "内存", "端口", "内存泄漏",
        "registry", "注册表", "防火墙", "网络", "开机", "蓝屏", "重启", "关机",
        "备份", "还原", "服务",
    ],
    INTENT_SECURITY: [
        "漏洞", "注入", "vulnerability", "secret", "auth", "xss", "csrf",
        "token", "密码", "ssl", "审计", "rbac", "encrypt", "decrypt",
        "权限", "威胁", "威胁建模", "渗透", "pentest", "ctf",
    ],
    INTENT_PLAN: [
        "计划", "plan", "路线图", "步骤", "拆解", "阶段",
        "roadmap", "规划", "方案", "策略", "sprint", "里程碑",
        "里程碑", "milestone", "todo", "checklist",
    ],
    INTENT_CODE: [
        "Python", "装饰器", "SQL", "Docker", "TypeScript",
        "interface", "useEffect", "git rebase", "异步和并发",
        "Cannot read property", "斐波那契", "多阶段构建",
        "生命周期", "TypeError", "KeyError", "ValueError",
        "代码", "报错", "算法", "怎么写", "优化索引",
        "递归", "RUST", "Rust", "API", "函数",
        "写一个", "写代码", "实现", "代码实现", "bug",
        "调试", "debug", "log", "stacktrace", "exception",
    ],
    INTENT_TOOL_CALL: [
        "帮我打开", "帮我关闭", "帮我重启", "帮我删除",
        "帮我截图", "帮我设置", "帮我调到", "帮我发",
        "打开浏览器", "关闭所有窗口", "重启电脑", "删除 C 盘",
        "截图当前屏幕", "把音量调到", "VS Code", "PDF 转 Word",
        "给张三发邮件", "桌面文件", "调亮度", "调音量",
        "发短信", "发邮件", "打电话", "连接 wifi", "扫描",
    ],
    INTENT_ROLEPLAY: [
        "扮演", "假装", "讲个", "讲故事", "继续讲", "演一段",
        "模仿", "当军师", "角色扮演", "海盗", "李白", "苏东坡",
        "鬼故事", "童话故事", "穿越小说", "面试官",
    ],
    INTENT_SEARCH: [
        "为什么", "是什么", "什么是", "怎么办",
        "怎么道歉", "几岁", "几岁开始", "有多少", "哪一年",
        "推荐", "推荐几", "故事",
        "天气", "股票", "理财", "钢琴",
        "人口", "二战", "量子计算", "育儿", "听不听话",
        "论文", "paper", "文献", "sciverse", "学术", "citation",
    ],
    INTENT_CHAT: [
        "你好", "今天", "周末", "心情", "哈哈", "真逗", "陪",
        "聊聊", "想你", "喜欢", "叫啥", "叫什么",
        "想你了", "最近", "过得", "辛苦", "开心", "难过",
        "早安", "晚安", "吃饭", "睡觉",
    ],
}


# ============================================================
# 模板库(每类 30-40 条 × 2-3 变体)
# ============================================================
TEMPLATES: dict[str, list[str]] = {
    INTENT_OPS: [
        "帮我清理 D 盘",
        "清理一下 C 盘垃圾",
        "卸载这个程序",
        "删除桌面所有文件",
        "kill 掉进程 chrome",
        "查看 CPU 占用最高的进程",
        "重启 Windows 服务",
        "kill-9 12345",
        "调度一下 watchdog",
        "开个 probalance 配置文件",
        "查看 18814 端口是否被占用",
        "内存泄漏怎么排查",
        "蓝屏了 0x3b 怎么定位",
        "防火墙挡住了端口 8443",
        "开机自启项太多了",
        "registry 启动项怎么删",
        "做个磁盘备份",
        "还原系统到上周的快照",
        "把端口 3306 留给 mysql",
        "监控这个进程的 CPU 曲线",
        "调度策略 probalance 还是 ananicy",
        "processlasso 怎么用",
        "禁用一下 USB 端口",
        "查看进程打开的文件句柄",
        "杀进程前先 dump 内存",
        "网络连接断了怎么诊断",
        "开机启动服务太多卡顿",
        "把 D 盘移到 SSD",
        "给 chrome 限制 CPU 到 50%",
        "rm -rf 误操作怎么恢复",
    ],
    INTENT_SECURITY: [
        "审计一下 API key 是否泄露",
        "扫描代码里的 secret",
        "检查 SQL 注入漏洞",
        "XSS 风险评估",
        "CSRF token 配置",
        "JWT token 过期怎么处理",
        "加 RBAC 权限模型",
        "审计 git 历史泄露的密码",
        "SSL 证书即将过期",
        "权限提升漏洞怎么修",
        "威胁建模怎么画",
        "渗透测试报告分析",
        "OWASP Top 10 解读",
        "数据加密 AES 还是 RSA",
        "密码哈希用 argon2 还是 bcrypt",
        "OAuth2 flow 选哪个",
        "VPN 鉴权机制升级",
        "审计 nginx 配置的 CORS",
        "数据脱敏怎么处理身份证号",
        "审计日志 ELK 怎么部署",
        "DDoS 防护方案",
        "中间人攻击怎么防御",
        "Docker 容器逃逸风险",
        "WebSocket 鉴权怎么做",
        "CORS 配置 strict-origin",
        "审计 SAML 集成",
        "密码策略要不要加 2FA",
        "Session 过期时间多少合理",
        "审计 LDAP 注入",
        "威胁狩猎用什么工具",
    ],
    INTENT_PLAN: [
        "计划一下这个 sprint",
        "拆解成几个阶段",
        "做 roadmap 路线图",
        "列个 checklist",
        "怎么分步实现这个功能",
        "给我一个学习计划",
        "明天上午先做什么",
        "步骤一二三列出来",
        "里程碑怎么划分",
        "拆分一下需求",
        "做一个 TODO list",
        "先做哪一块",
        "拆解成 P0/P1/P2",
        "排期 5 天能做完吗",
        "优先级怎么排",
        "Stage 1 / Stage 2 分工",
        "先验证还是先实现",
        "项目分几个 phase",
        "哪几个是关键路径",
        "做一次架构选型方案",
        "决策表怎么画",
        "推荐一个技术栈",
        "可行性评估怎么做",
        "影响范围分析",
        "风险登记册",
        "应急预案有哪些",
        "怎么跟上下游对齐",
        "里程碑验收标准",
        "阶段交付物清单",
        "策略调整方案",
    ],
    INTENT_CODE: [
        "Python 装饰器怎么写",
        "帮我写一个 TypeScript 接口",
        "这段代码有 bug 帮我看一下",
        "SQL 索引怎么优化",
        "Docker 多阶段构建",
        "useEffect 依赖项怎么配",
        "Python 异步和并发的区别",
        "Cannot read property of undefined",
        "斐波那契数列递归实现",
        "TypeError: cannot import name",
        "KeyError 怎么定位",
        "git rebase 冲突怎么解",
        "帮我实现一个红黑树",
        "写一个 LRU cache",
        "改一个 React 组件 bug",
        "优化这段 SQL 查询",
        "实现一个事件循环",
        "改一个 Python 装饰器 bug",
        "调试一个 stacktrace",
        "写一个 REST API",
        "code review 一下这个 PR",
        "实现贪心算法解决背包问题",
        "改一个 Rust 生命周期 bug",
        "Python RUST 互操作",
        "写一个 Dockerfile",
        "写一个 GitHub Action",
        "重构一个长函数",
        "写一个 Bash 脚本",
        "改一个 hook 函数",
        "写一个简单的 hello 函数",
        "算法时间复杂度分析",
        "函数式编程柯里化",
        "TypeScript 泛型怎么写",
        "K8s Pod 调度策略",
        "Promise.all 失败处理",
        "API 限流算法",
    ],
    INTENT_TOOL_CALL: [
        "帮我打开浏览器",
        "关闭所有窗口",
        "重启电脑",
        "删除桌面上 1.txt",
        "帮我截图当前屏幕",
        "把音量调到 50",
        "打开 VS Code",
        "PDF 转 Word",
        "给张三发一封邮件",
        "桌面文件整理",
        "调亮度到 80",
        "发短信给 13800138000",
        "连接 wifi Prisir",
        "扫一下当前目录",
        "打开 Steam",
        "重启 explorer.exe",
        "打开任务管理器",
        "关闭 chrome 标签页",
        "打开计算器",
        "截图保存到桌面",
        "给文件改名",
        "调时区到上海",
        "打开 OBS 录屏",
        "给 chrome 加启动参数",
        "关掉显示器",
        "切换默认打印机",
        "打开设备管理器",
        "卸载已装的应用",
        "把文件移动到 D 盘",
        "重启 nginx 服务",
    ],
    INTENT_ROLEPLAY: [
        "扮演一个海盗船长",
        "假装你是李白给我写首诗",
        "讲个鬼故事",
        "继续讲这个故事",
        "演一段面试官",
        "模仿一下苏东坡的口吻",
        "当我的军师",
        "角色扮演一个产品经理",
        "扮演明朝的锦衣卫",
        "你是 CEO 我是 CTO",
        "假装你是我爸 跟我说说话",
        "演一段医生和患者",
        "讲个童话故事给小朋友",
        "扮演一个 Linux 老师",
        "讲个穿越小说开头",
        "演一个面试官问我算法题",
        "模仿爱因斯坦讲相对论",
        "扮演三国演义里的诸葛亮",
        "演一段客服对话",
        "扮演明朝东厂",
        "给我讲个睡前故事",
        "讲个笑话",
        "编一首儿歌",
        "当 5 分钟英语老师",
        "角色扮演英文面试官",
        "演一段情景剧",
        "假装你是个翻译",
        "讲个冷笑话",
        "继续昨天的故事",
        "演一段辩论赛",
    ],
    INTENT_SEARCH: [
        "为什么天空是蓝色的",
        "Python 是谁发明的",
        "什么是量子计算",
        "怎么办理护照",
        "怎么道歉",
        "推荐几本书",
        "论文:transformer 架构",
        "sciverse 搜 reinforcement learning",
        "二战是哪一年结束的",
        "中国人口多少",
        "推荐几本育儿书",
        "kimi-k2.6 是什么模型",
        "特斯拉 Model Y 价格",
        "推荐几个音乐 app",
        "上海天气怎么样",
        "为什么人会做梦",
        "股票 A 股怎么开户",
        "钢琴入门教程",
        "量子计算能破解 RSA 吗",
        "推荐几本历史书",
        "什么是 RAG",
        "为什么大模型会 hallucinate",
        "怎么处理代码冲突",
        "听不听话的小孩怎么教育",
        "推荐几本投资书",
        "什么叫 LLM agent",
        "北京今年下雪了吗",
        "怎么学英语最快",
        "GitHub Trending 怎么看",
        "推荐一个 Notion 替代品",
    ],
    INTENT_CHAT: [
        "你好",
        "今天心情怎么样",
        "周末过得开心吗",
        "哈哈 真逗",
        "陪我聊聊",
        "最近想你",
        "你喜欢什么",
        "你叫什么名字",
        "想你了",
        "吃饭了没",
        "早安",
        "晚安",
        "今天过得怎么样",
        "谢谢你",
        "你辛苦了",
        "最近在干嘛",
        "想跟你聊聊",
        "你今天开心吗",
        "你是怎么看待生活的",
        "我今天有点难过",
        "你推荐什么电影",
        "今天天气不错",
        "你平时听什么歌",
        "我睡不着",
        "晚上吃什么",
        "推荐个餐厅",
        "我有点累",
        "你心情好吗",
        "今天有进步",
        "你最近怎么样",
    ],
}


# ============================================================
# 真实 trace 抽取
# ============================================================
def _extract_real_tasks() -> list[dict]:
    """从 oiagent_harness_training/*.jsonl 抽 task 文本 + intent。

    字段映射:
      - task → "task" 字段
      - intent → "intent" 字段(粗映射到 8 类)
      - 来源 → _source = "real_trace"
    """
    out: list[dict] = []
    if not _HARNESS_DIR.exists():
        print(f"[warn] harness dir 不存在: {_HARNESS_DIR}")
        return out

    for path in sorted(_HARNESS_DIR.glob("*.jsonl")):
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("event") != "dispatch":
                        continue
                    task_text = (rec.get("task") or "").strip()
                    intent_raw = rec.get("intent") or ""
                    if not task_text or len(task_text) < 4:
                        continue
                    # 反向映射到 8 类
                    intent = _ROUTING_TO_INTENT.get(intent_raw)
                    if intent is None:
                        # 不在我们 8 类范围 → 用关键词重新打
                        intent = _classify_text(task_text)
                        if intent is None:
                            continue
                    out.append({
                        "text": task_text[:400],
                        "label": intent,
                        "_source": "real_trace",
                        "_routing_intent": intent_raw,
                        "_file": path.name,
                    })
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 读 {path.name} 失败: {e}")

    print(f"[real] 从 {len(list(_HARNESS_DIR.glob('*.jsonl')))} 个 jsonl 抽出 {len(out)} 条真实 trace")
    return out


def _classify_text(text: str) -> str | None:
    """用关键词回退分类(8 类)。"""
    text_lower = text.lower()
    # 按特定顺序匹配(ops / security / plan 优先避免被 code 误吃)
    for intent in (INTENT_OPS, INTENT_SECURITY, INTENT_PLAN,
                   INTENT_TOOL_CALL, INTENT_ROLEPLAY,
                   INTENT_CODE, INTENT_SEARCH, INTENT_CHAT):
        for kw in ANCHORS[intent]:
            if kw.lower() in text_lower:
                return intent
    return None


# ============================================================
# 模板合成样本
# ============================================================
def _gen_from_templates(rng: random.Random, n_per_intent: int = 80) -> list[dict]:
    """从 8 类模板生成 n_per_intent 条合成样本。

    每条 = template + 1-3 个变体(空格/标点微扰,无 paraphrase 凑数)
    """
    out: list[dict] = []
    for intent in ALL_INTENTS:
        templates = TEMPLATES[intent]
        for i in range(n_per_intent):
            tmpl = rng.choice(templates)
            # 轻微变体:首字母大小写 + 句末标点
            variants = [
                tmpl,
                tmpl.rstrip("。.?!") + "?",
                tmpl.rstrip("。.?!") + "。",
                tmpl.rstrip("。.?!") + " 帮我一下",
                "请" + tmpl if not tmpl.startswith(("帮", "请", "把")) else tmpl,
                tmpl + " 谢谢",
            ]
            text = rng.choice(variants)
            out.append({
                "text": text,
                "label": intent,
                "_source": "template",
                "_template_idx": i,
            })
    print(f"[tmpl] 8 类 × {n_per_intent} = {len(out)} 条合成样本")
    return out


# ============================================================
# Cross-intent mixing 边界样本
# ============================================================
def _gen_mix_samples(rng: random.Random, n: int = 80) -> list[dict]:
    """生成 n 条边界样本(主意图 + 噪声意图)。

    例:code + 闲聊 → "帮我写个 Python 装饰器哈"  → 主 code
        ops + code → "kill 掉那个 Python 进程"  → 主 ops
    """
    pairs = [
        (INTENT_CODE, INTENT_CHAT),
        (INTENT_TOOL_CALL, INTENT_CODE),
        (INTENT_OPS, INTENT_CODE),
        (INTENT_PLAN, INTENT_SEARCH),
        (INTENT_SEARCH, INTENT_CHAT),
        (INTENT_SECURITY, INTENT_CODE),
        (INTENT_ROLEPLAY, INTENT_SEARCH),
        (INTENT_OPS, INTENT_PLAN),
    ]
    out: list[dict] = []
    for _ in range(n):
        primary, noise = rng.choice(pairs)
        t_primary = rng.choice(TEMPLATES[primary])
        t_noise = rng.choice(TEMPLATES[noise])
        # 主在前 / 主在后 各半
        if rng.random() < 0.5:
            text = f"{t_primary} 还有 {t_noise}"
        else:
            text = f"{t_noise} 顺便 {t_primary}"
        out.append({
            "text": text,
            "label": primary,
            "_source": "mix",
            "_primary": primary,
            "_noise": noise,
        })
    print(f"[mix] {len(out)} 条边界样本")
    return out


# ============================================================
# 主数据集生成
# ============================================================
def generate_dataset(n_target: int = 800, seed: int = 42,
                     holdout: bool = False) -> list[dict]:
    """生成训练/eval 集。

    组成(目标 ~800):
      - 真实 trace:全部(~80-100 条,看数据集大小)
      - 模板合成:8 × 80 = 640 条
      - 边界 mix:80 条
    """
    rng = random.Random(seed + (1 if holdout else 0))

    real = _extract_real_tasks()
    # holdout 模式下从 real 划走 20%
    if holdout and real:
        cut = int(len(real) * 0.2)
        real = real[:cut] if not holdout else real[-cut:]

    tmpl = _gen_from_templates(rng, n_per_intent=80)
    mix = _gen_mix_samples(rng, n=80)

    all_samples = real + tmpl + mix
    rng.shuffle(all_samples)

    # 截断/扩到目标
    if len(all_samples) > n_target:
        all_samples = all_samples[:n_target]
    print(f"[gen] holdout={holdout} → {len(all_samples)} 条 (real={len(real)}, tmpl={len(tmpl)}, mix={len(mix)})")
    return all_samples


# ============================================================
# CLI
# ============================================================
def main() -> int:
    p = argparse.ArgumentParser(description="M3.84 8-intent dispatch_route 训练集")
    p.add_argument("--limit", type=int, default=800, help="目标条数(默认 800)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out-dir", type=Path, default=_OUT_DIR)
    args = p.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    train = generate_dataset(n_target=args.limit, seed=args.seed, holdout=False)
    eval_ = generate_dataset(n_target=200, seed=args.seed, holdout=True)

    train_path = args.out_dir / "data_dispatch_route_train.jsonl"
    eval_path = args.out_dir / "data_dispatch_route_eval.jsonl"

    with train_path.open("w", encoding="utf-8") as f:
        for s in train:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with eval_path.open("w", encoding="utf-8") as f:
        for s in eval_:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # 统计
    train_dist = Counter(s["label"] for s in train)
    eval_dist = Counter(s["label"] for s in eval_)

    print(f"\n[done] train → {train_path} ({len(train)} 条)")
    print(f"[done] eval  → {eval_path} ({len(eval_)} 条)")
    print("\n[dist] train intent 分布:")
    for intent in ALL_INTENTS:
        print(f"  {intent:12s}: {train_dist.get(intent, 0):4d}")
    print("\n[dist] eval intent 分布:")
    for intent in ALL_INTENTS:
        print(f"  {intent:12s}: {eval_dist.get(intent, 0):4d}")

    # 数据质量自检:每类至少 30 条
    for intent in ALL_INTENTS:
        n = train_dist.get(intent, 0)
        if n < 30:
            print(f"[warn] {intent} 仅 {n} 条(< 30,可能影响训练)")

    # 数据不足提示
    if len(train) < 800:
        print(f"\n[warn] 数据不足,实际 {len(train)} 条(< {args.limit}),接受现状")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())