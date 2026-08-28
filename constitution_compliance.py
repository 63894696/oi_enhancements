# -*- coding: utf-8 -*-
# constitution_compliance.py — Prisir 项目宪法「合规检测器」 harness(2026-08-14)
#
# 定位:把 docs/prisir-dev-constitution.md 的硬伤条款(§1 签名 / §5 代码正确性 /
# §5b 安慰剂红线)固化为【确定性、可自动判分】的检测器。
#
# 与既有 harness 的关系(并入,不重复):
#   - harness_test_battery.py   = 自然语言题集(H1-H8),发给 cursor background agent
#                                 做,靠人工/模型读报告判对错 —— 没有确定性判分。
#   - mcp_oiagent_v3_plan_parallel/harness.py = plan→parallel→loop 执行引擎,不是判分器。
#   - 本模块                     = 确定性静态扫描器:给定一段产出文本(方案/代码草稿),
#                                 输出 PASS / FAIL + 命中的宪法条款。是上面两者缺的「判分」层。
#
# 三个用途:
#   1) 质量闸门:dev-consumer / 团队产出进编译收口前,先过 scan_text,FAIL 即打回。
#   2) 回归测试:VERIFIED_CASES 存「本次 570-585 实测确认」的正/反样本,防检测器本身漂移。
#   3) 测试能力评估:评估 oiagent/团队 agent 能否检出已知违宪(见 评估用法)。
#
# 评估用法(测某个 reviewer 的测试能力是否到位):
#   - 检出率(召回) = 被测 reviewer 报出的违宪数 / 注入的已知违宪数(EXPECTED_MIN_HITS)
#   - 精确率        = 真违宪 / 被测 reviewer 报告总数(有没有乱报、安没安慰剂)
#   把KNOWN-BAD 样本隐去答案喂给被测 reviewer,对比它的报告 vs EXPECTED_MIN_HITS。
#
# 红线:本模块只做静态文本/代码模式扫描,不执行被测代码、不联网、不读凭证。
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ─────────────────────────────────────────────────────────────
# 违宪模式库 —— 每条对应宪法一个条款
# ─────────────────────────────────────────────────────────────
# severity: blocker=打回 / warn=带条件放行
# clause:   宪法条款号(便于回溯 + 出放行/打回结论)
# pattern:  编译后的正则(多行,忽略大小写按需)
# why:      命中时的解释(给用户/团队看)
# ─────────────────────────────────────────────────────────────


@dataclass
class Rule:
    rule_id: str
    clause: str
    severity: str  # "blocker" | "warn"
    why: str
    # 命中条件:任一 pattern 命中即算。
    patterns: list[re.Pattern] = field(default_factory=list)
    # 剔除语境:若命中点的「前文」含 suppress_context 里的词,则视为在【修正/引用】
    # 该违宪而非真违宪(如 585 把「1小时有效」改为「一次性邀请」),不计入。
    suppress_context: tuple = ()
    suppress_window: int = 16  # 往前看多少字

    def hits(self, text: str) -> bool:
        for p in self.patterns:
            for m in p.finditer(text):
                if self.suppress_context:
                    ctx = text[max(0, m.start() - self.suppress_window): m.start()]
                    if any(w in ctx for w in self.suppress_context):
                        continue  # 修正/引用语境,剔除
                return True
        return False


def _rx(*pats: str, flags: int = re.IGNORECASE | re.MULTILINE) -> list[re.Pattern]:
    return [re.compile(p, flags) for p in pats]


RULES: list[Rule] = [
    # ── §1 签名体系:Ed25519 唯一,禁 RSA ─────────────────────
    Rule(
        rule_id="sig_rsa_padding",
        clause="§1",
        severity="blocker",
        why="用了 RSA 签名套路(padding.PKCS1v15 / hashes.SHA256)。契约:全项目签名只用 Ed25519,Ed25519 没有 padding/hashes 参数。",
        patterns=_rx(
            r"padding\.PKCS1v15",
            r"hashes\.SHA256\s*\(",          # 作为签名参数传给 sign()
            r"rsa\.generate_private_key",
            r"from\s+cryptography.*\brsa\b",
        ),
    ),
    # ── §5 代码正确性:引用未定义标识符 ─────────────────────
    # helper 函数里用外层才有的 sequence(578 实测)。静态近似:出现 `sequence` 被使用
    # 但全文未对其赋值/定义。
    # ── §5 已弃用 API:MV2 chrome.tabs.executeScript ────────
    Rule(
        rule_id="mv2_execute_script",
        clause="§5",
        severity="blocker",
        why="用了已弃用的 MV2 API chrome.tabs.executeScript。MV3 必须是 chrome.scripting.executeScript。",
        patterns=_rx(r"chrome\.tabs\.executeScript"),
        suppress_context=("原做法", "错误", "替换", "修改为", "识别所有", "改为", "弃用", "❌", "问题"),
        suppress_window=80,
    ),
    # ── §5 文件类型混淆:.mojo 写 Python ────────────────────
    # Mojo 契约是 .mojom(接口语言),不是 .mojo,更不是 Python。
    Rule(
        rule_id="mojo_python_confusion",
        clause="§5",
        severity="blocker",
        why="把 Mojo 契约当成 .mojo 文件 / 在里面写 Python。契约:.mojom 是接口定义语言,handler 是 C++,前端经 .mojom.js。三者别串。",
        patterns=_rx(
            r"\.mojo\b(?!n)",               # 提到 .mojo(非 .mojom)
            r"```mojo\s*\n\s*(import|def|class)\b",  # mojo 代码块里是 Python 语法
        ),
        suppress_context=("错误", "纠正", "原做法", "改为", "违反", "问题", "❌", "识别"),
        suppress_window=80,
    ),
    # ── §5 假自清理:删一个根本没存过的 key ─────────────────
    Rule(
        rule_id="fake_cleanup",
        clause="§5",
        severity="blocker",
        why="假自清理:localStorage.removeItem(TEST_TAG) 删的是一个根本没存过的 key。测试数据要用 TAG 精确删除真存过的项。",
        patterns=_rx(r"localStorage\.removeItem\s*\(\s*[A-Z_]*TAG"),
        suppress_context=("原做法", "错误", "假清理", "改为", "问题", "❌", "改进", "解决"),
        suppress_window=80,
    ),
    # ── §5 os_crypt 臆造(当成 rust crate / JS 直接调 / 错误命名空间)────
    Rule(
        rule_id="os_crypt_fantasy",
        clause="§5",
        severity="blocker",
        why="臆造/误用 os_crypt 接口。os_crypt 是 Chromium C++ 组件,正确用法是 OSCrypt::EncryptString(大写 OSCrypt 命名空间 + #include \"components/os_crypt/...\")。JS/扩展侧不直接碰,也不是 rust crate / npm 包。",
        patterns=_rx(
            r"use\s+os_crypt\s*::",                 # rust: use os_crypt::{encrypt,decrypt} 臆造
            r"os_crypt.*crate",                     # rust crate 臆造
            r"crypto\.encrypt\([^)]*os_crypt",       # JS crypto.encrypt(x,'os_crypt') 臆造
            r"require\(['\"]os_crypt['\"]\)",        # 当成 node 模块
            r"import\s+os_crypt",                    # 当成可 import 的包
            r"\bos_crypt::EncryptString",            # 错误命名空间(应为 OSCrypt::,小写 os_crypt:: 是臆造)
        ),
        suppress_context=("错误", "臆造", "原做法", "改为", "违反", "问题", "❌", "不应", "删除", "审查"),
        suppress_window=80,
    ),
    # ── §5 引用未定义/作用域外标识符(如 helper 用了外层 sequence)────────
    # 静态近似:helper 函数(只接收 state/error 等)体内引用了外层入参 `sequence`。
    Rule(
        rule_id="undefined_identifier",
        clause="§5",
        severity="blocker",
        why="引用了作用域外/未定义的标识符(如 helper 函数体内用了外层才有的 `sequence`,应取 state 持有的)。",
        patterns=_rx(
            # helper(state, error) 这类不含 sequence 的签名,体内却用 sequence.xxx
            r"function\s+\w+\s*\(\s*state[^)]*\)\s*\{[^}]*\bsequence\.",
        ),
    ),
    # ── §5b 安慰剂占位函数:包一层红线就声称满足 ────────────
    Rule(
        rule_id="placebo_validate",
        clause="§5b",
        severity="blocker",
        why="安慰剂占位:validate_rules(){return true} 这类把红线包一层就声称满足的假实现(C++/JS/rust 都算)。安全校验要么真实现要么标 TODO,验收不得据此打 ✅。",
        patterns=_rx(
            r"validate_rules\s*\([^)]*\)\s*\{[^{}]*return\s+true",   # JS/C++ 体
            r"fn\s+validate_rules\s*\([^)]*\)[^{]*\{[^{}]*\btrue\b", # rust: -> bool { ... true }
            r"function\s+\w*validat\w*\s*\([^)]*\)\s*\{\s*return\s+true",
        ),
        suppress_context=("安慰剂", "原做法", "错误", "改为", "违反", "问题", "❌", "审查", "不应", "修改"),
        suppress_window=80,
    ),
    # ── §5b 确认卡自动批准(确认门槛红线被架空)──────────────
    Rule(
        rule_id="auto_approve_confirm",
        clause="§5b",
        severity="blocker",
        why="确认卡用 setTimeout 自动 resolve(true) 自动批准 —— 确认门槛红线被架空。确认必须真等用户点击。",
        patterns=_rx(
            r"setTimeout\s*\(\s*\(\s*\)\s*=>\s*resolve\s*\(\s*true",
            r"假设自动批准",
            r"自动批准",
        ),
        suppress_context=("原先", "原做法", "错误", "修改为", "违反", "改为", "问题", "❌", "删除", "不再", "审查", "红线要求"),
        suppress_window=90,
    ),
    # ── §5b 定时任务 vs NTP 触发红线未调和 ─────────────────
    # 出现「定时/后台自动执行动作」措辞但未声明「执行仍等用户确认」。
    Rule(
        rule_id="timer_vs_ntp",
        clause="§5b",
        severity="warn",
        why="定时任务触发与「动作只能 NTP 用户输入触发」冲突,需先回红线拍板口径(定时只能到点提醒/预填,执行仍等用户确认)。",
        patterns=_rx(
            r"定时(任务|器|执行|触发).{0,40}(自动|自主).{0,20}(执行|触发|动作)",
            r"后台.{0,10}(自动|定时).{0,10}(执行|代行|触发)动作",
        ),
        suppress_context=("不涉及", "禁止", "不得", "不可", "原做法", "错误", "改为", "违反", "问题", "❌", "不能"),
        suppress_window=20,
    ),
    # ── §6 编造契约未定义的约束 ────────────────────────────
    # 用 suppress_context 剔除「在修正/引用该编造约束」的语境(585 是在修正而非编造)。
    Rule(
        rule_id="fabricated_constraint",
        clause="§6",
        severity="warn",
        why="疑似编造契约里没定义的约束(如自造「N 小时有效」)。不确定就标「待确认」,不要现编。",
        patterns=_rx(
            r"(邀请|链接|令牌|token).{0,8}\d+\s*(小时|分钟|天).{0,4}有效",
        ),
        suppress_context=("修改", "澄清", "原文", "改为", "修正", "替换", "删除", "引用", "明确", "不编造", "别编", "编造"),
        suppress_window=30,
    ),

    # ══════════════════════════════════════════════════════════
    # 落地一:anti-phantom 门(借鉴 HarnessBank「改动真执行了吗」)
    # 专检「声称做了 X 其实没做 X」的幻影通过。命中一律 blocker。
    # ══════════════════════════════════════════════════════════
    # phantom:声称加密但函数体是占位(pass/return true/TODO/return data 原样返回)
    Rule(
        rule_id="phantom_encrypt",
        clause="§5b",
        severity="blocker",
        why="幻影加密:声称用 os_crypt/加密,但加密函数体是占位(pass/return true/原样返回/TODO),没真加密。假提升比没做更危险。",
        patterns=_rx(
            r"(def|function|fn)\s+\w*encrypt\w*\s*\([^)]*\)[^{:\n]*[:\{]\s*(pass|return\s+true|return\s+\w+\s*;?\s*#|//\s*TODO)",
            r"EncryptWithOSCrypt\s*\([^)]*\)\s*\{[^{}]*(return\s+\w+|TODO|pass)",
        ),
        suppress_context=("错误", "原做法", "改为", "违反", "问题", "❌", "幻影", "审查", "不应", "修复"),
        suppress_window=80,
    ),
    # phantom:验收清单 ✅ 了某项,但全文该项只有 TODO/占位实现
    # 静态近似:出现「✅ ...加密/校验/清理/确认」且同文档有「TODO ... 该项」→ 幻影 claim
    Rule(
        rule_id="phantom_claim",
        clause="§5b",
        severity="blocker",
        why="幻影验收:验收清单给某项打了 ✅,但代码里该项只有 TODO/占位实现,没真做。验收不得据占位打 ✅。",
        patterns=_rx(
            # 同文档既有「✅ ... (加密|校验|清理|确认|白名单)」又有「(TODO|待补充|待实现|占位) ... 同关键词」
            r"✅[^\n]{0,30}(加密|校验|清理|确认|白名单|签名)[\s\S]{0,400}?(TODO|待补充|待实现|占位)[^\n]{0,30}\1",
        ),
        suppress_context=("错误示范", "反例", "❌"),
        suppress_window=40,
    ),
]


# ─────────────────────────────────────────────────────────────
# 扫描结果
# ─────────────────────────────────────────────────────────────
@dataclass
class Finding:
    rule_id: str
    clause: str
    severity: str
    why: str


@dataclass
class ScanReport:
    verdict: str                      # "PASS" | "FAIL"
    findings: list[Finding]
    blocker_count: int
    warn_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "blocker_count": self.blocker_count,
            "warn_count": self.warn_count,
            "findings": [vars(f) for f in self.findings],
        }


def scan_text(text: str) -> ScanReport:
    """对一段产出文本(方案/代码草稿)做宪法合规静态扫描。

    返回 ScanReport:verdict=FAIL 若有任一 blocker;只有 warn 也算 PASS 但带出条件。

    修正报告豁免:若整篇是「修复/修正报告」(在【引用并批评】错误写法后给出正确方案),
    则命中的「被引用错误代码」不计违宪 —— 否则修得越认真(引原文对比越细)越被误报。
    判定:文档含明确修复结构词(修复报告/修改说明/解决方案/纠正/原做法→新方案 等)即视为修正报告。
    """
    findings: list[Finding] = []
    for rule in RULES:
        if rule.hits(text):
            findings.append(Finding(rule.rule_id, rule.clause, rule.severity, rule.why))

    # ── 修正报告豁免 ─────────────────────────────────────
    # 全文若明显是「修复报告/修正单返工」,把 blocker 级命中降级为 warn(标注为「引用对照」),
    # 因为修正报告的义务就是引用错误写法。真违宪会在「方案正文」而非「修复报告」里。
    if _is_fix_report(text) and findings:
        findings = [
            Finding(f.rule_id, f.clause, "warn",
                    "[修正报告·引用对照] " + f.why) if f.severity == "blocker" else f
            for f in findings
        ]

    blockers = sum(1 for f in findings if f.severity == "blocker")
    warns = sum(1 for f in findings if f.severity == "warn")
    return ScanReport(
        verdict="FAIL" if blockers else "PASS",
        findings=findings,
        blocker_count=blockers,
        warn_count=warns,
    )


# 修正报告结构词:命中 >=2 个即认为是「在修某个违宪」的文档,而非「在写违宪方案」。
_FIX_REPORT_MARKERS = (
    "修复报告", "修改说明", "修正", "纠正", "解决方案", "正确处理",
    "原做法", "错误行为", "问题分析", "问题定位", "修复策略", "修改为",
    "待修改项", "需修改项", "行动项", "审查结论", "违反宪法", "违反",
    "替换为", "旧写法", "新写法", "待确认项", "识别所有",
)

# lesson 回填/schema 示例语境:在「给违宪 lesson 分类/举例」时引用违宪原文(raw_content)。
# 这些词单独太泛(「示例」「tag」真违宪方案里也有),不并进 _FIX_REPORT_MARKERS,
# 而用「显式引用 lesson 原文」的强信号单独判定,避免误豁免真违宪(如 579 含 tag/示例但是真违宪)。
_LESSON_BACKFILL_MARKERS = ("raw_content", "回填", "lesson_id", "映射表")


def _is_fix_report(text: str) -> bool:
    hits = sum(1 for w in _FIX_REPORT_MARKERS if w in text)
    if hits >= 2:
        return True
    # lesson 回填语境:命中 >=2 个「引用 lesson 原文」强信号即算(如 605 回填方案,
    # 不含传统修复词但全是 raw_content/lesson_id/映射表 等回填术语)。
    if sum(1 for w in _LESSON_BACKFILL_MARKERS if w in text) >= 2:
        return True
    return False


# ─────────────────────────────────────────────────────────────
# 回归样本库 —— 本次 570-585 实测确认的正/反例
# 每条: id / 来源 task / 文本 / 期望命中的 rule_id 集合(至少这些要被检出)
# 用于:(a) 检测器自身回归 (b) 当被测 reviewer 的「已知答案」评估其检出率
# ─────────────────────────────────────────────────────────────
VERIFIED_CASES: list[dict[str, Any]] = [
    {
        "id": "case_577_rsa",
        "source_task": 577,
        "label": "KNOWN-BAD",
        "text": "签名: sk.sign(payload, padding.PKCS1v15(), hashes.SHA256())",
        "expect_rules": {"sig_rsa_padding"},
    },
    {
        "id": "case_581_mv2",
        "source_task": 581,
        "label": "KNOWN-BAD",
        "text": "chrome.tabs.executeScript(tabId, {code: '...'})",
        "expect_rules": {"mv2_execute_script"},
    },
    {
        "id": "case_580_fake_cleanup",
        "source_task": 580,
        "label": "KNOWN-BAD",
        "text": "测试后清理: localStorage.removeItem(TEST_TAG);",
        "expect_rules": {"fake_cleanup"},
    },
    {
        "id": "case_582_placebo",
        "source_task": 582,
        "label": "KNOWN-BAD",
        "text": "function validate_rules() { return true; }  // 红线校验",
        "expect_rules": {"placebo_validate"},
    },
    {
        "id": "case_583_os_crypt_fantasy",
        "source_task": 583,
        "label": "KNOWN-BAD",
        "text": "const enc = crypto.encrypt(data, 'os_crypt');",
        "expect_rules": {"os_crypt_fantasy"},
    },
    {
        "id": "case_572_auto_approve",
        "source_task": 572,
        "label": "KNOWN-BAD",
        "text": "setTimeout(() => resolve(true), 1000); // 假设自动批准",
        "expect_rules": {"auto_approve_confirm"},
    },
    {
        "id": "case_585_ed25519_ok",
        "source_task": 585,
        "label": "KNOWN-GOOD",
        "text": (
            "from cryptography.hazmat.primitives.asymmetric.ed25519 import "
            "Ed25519PrivateKey, Ed25519PublicKey\n"
            "sk = Ed25519PrivateKey.generate()\n"
            "sig = sk.sign(payload)\n"
            "Ed25519PublicKey.from_public_bytes(pub).verify(sig, payload)"
        ),
        "expect_rules": set(),  # 正确写法不应命中任何 blocker
    },
    {
        "id": "case_clean_ok",
        "source_task": 573,
        "label": "KNOWN-GOOD",
        "text": "使用 OSCrypt::EncryptString 加密后落本地 profile;补丁顺序见 BUILD.gn。",
        "expect_rules": set(),
    },
    # ── 第二轮补:574/578/582 实测片段(检测器首轮漏检,补规则后须命中)──
    {
        "id": "case_574_os_crypt_ns",
        "source_task": 574,
        "label": "KNOWN-BAD",
        "text": "bool Store(const std::string& p){ return os_crypt::EncryptString(p, &encrypted_data); }",
        "expect_rules": {"os_crypt_fantasy"},   # 小写 os_crypt:: 错误命名空间
    },
    {
        "id": "case_578_undefined_seq",
        "source_task": 578,
        "label": "KNOWN-BAD",
        "text": "function rollbackSequence(state, error) {\n  for (let i=0;i<sequence.actions.length;i++){ executeRollback(sequence.actions[i]); }\n}",
        "expect_rules": {"undefined_identifier"},
    },
    {
        "id": "case_582_rust_os_crypt",
        "source_task": 582,
        "label": "KNOWN-BAD",
        "text": "```rust\nuse os_crypt::{encrypt, decrypt};\nfn validate_rules(&self, rules: &str) -> bool { true }\n```",
        "expect_rules": {"os_crypt_fantasy", "placebo_validate"},
    },
    {
        "id": "case_585_fix_not_fabrication",
        "source_task": 585,
        "label": "KNOWN-GOOD",
        "text": "> 原文:“邀请链接1小时有效”\n> 修改为:“一次性邀请”——使用一次即失效。",
        "expect_rules": set(),   # 在修正编造约束,不算违宪
    },
    # ── 落地一 anti-phantom 样本 ──
    {
        "id": "case_phantom_encrypt",
        "source_task": None,
        "label": "KNOWN-BAD",
        "text": "用 os_crypt 加密存储。def encrypt_data(d):\n    pass  # TODO 待实现",
        "expect_rules": {"phantom_encrypt"},
    },
    {
        "id": "case_phantom_claim",
        "source_task": None,
        "label": "KNOWN-BAD",
        "text": "| ✅ 凭证加密 | 已用 os_crypt 加密落盘 |\n\n### 待补充\n- TODO: 加密 逻辑待实现",
        "expect_rules": {"phantom_claim"},
    },
    {
        "id": "case_real_encrypt_ok",
        "source_task": None,
        "label": "KNOWN-GOOD",
        "text": "#include \"components/os_crypt/os_crypt.h\"\nstd::string enc;\nOSCrypt::EncryptString(plain, &enc);  // 真加密",
        "expect_rules": set(),   # 真实现不报 phantom
    },
]


def run_regression() -> dict[str, Any]:
    """回归:VERIFIED_CASES 每条的检出结果必须 ⊇ expect_rules。

    KNOWN-GOOD(expect_rules=空)要求零 blocker 命中。
    返回 {ok, total, passed, failed, failures:[...]}。
    """
    total = len(VERIFIED_CASES)
    failures = []
    for c in VERIFIED_CASES:
        rep = scan_text(c["text"])
        hit_ids = {f.rule_id for f in rep.findings}
        missing = set(c["expect_rules"]) - hit_ids
        # KNOWN-GOOD:不应有任何 blocker
        unexpected_blockers = (
            {f.rule_id for f in rep.findings if f.severity == "blocker"}
            if not c["expect_rules"] else set()
        )
        if missing or unexpected_blockers:
            failures.append({
                "id": c["id"],
                "missing_expected": sorted(missing),
                "unexpected_blockers": sorted(unexpected_blockers),
                "all_hits": sorted(hit_ids),
            })
    passed = total - len(failures)
    return {
        "ok": not failures,
        "total": total,
        "passed": passed,
        "failed": len(failures),
        "failures": failures,
    }


# ─────────────────────────────────────────────────────────────
# CLI
#   python constitution_compliance.py selftest            # 回归 VERIFIED_CASES
#   python constitution_compliance.py scan <file>          # 扫一个产出文件
#   echo <text> | python constitution_compliance.py scan - # 从 stdin 扫
# ─────────────────────────────────────────────────────────────
def _cli() -> None:
    import json
    import sys

    argv = sys.argv[1:]
    if not argv or argv[0] not in ("selftest", "scan"):
        print(__doc__)
        sys.exit(2)

    if argv[0] == "selftest":
        rep = run_regression()
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        sys.exit(0 if rep["ok"] else 1)

    # scan
    if len(argv) < 2:
        print("usage: constitution_compliance.py scan <file|->")
        sys.exit(2)
    if argv[1] == "-":
        text = sys.stdin.read()
    else:
        text = open(argv[1], encoding="utf-8", errors="replace").read()
    rep = scan_text(text)
    print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    sys.exit(0 if rep.verdict == "PASS" else 1)


if __name__ == "__main__":
    _cli()
