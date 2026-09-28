#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# adapter_registry.py — M3.45/M3.46/M3.47 自训小模型 adapter 注册表(2026-09-23)
#
# 目的:
#   - 给每个场景一个独立 LoRA adapter(路径 B)
#   - 当前已训:safety(M3.45 Jev 护栏) + tempfile(M3.46 临时文件分类)
#     + disk_cleanup(M3.47 C 盘系统清理)
#   - 未来扩展:email / code / log 等,每加一个 adapter 加一行
#   - 等 ≥ 4 个 adapter 后,加 sklearn 路由(路径 C),由调用方决定调用哪个
#
# 设计:
#   - 注册表只是 dict[sceanrio_name -> {base_model, adapter_path, prompt_schema, ...}]
#   - 推理时由调用方显式指定 scenario:
#       adapter = get_adapter("tempfile")
#       out = adapter.classify(text)
#   - 不内置 sklearn 路由(留接口但暂不实现),避免现在过度设计
#
# 使用:
#   from adapter_registry import get_adapter, list_scenarios
#   list_scenarios()           # ['safety', 'tempfile']
#   get_adapter('tempfile')    # LoadedAdapter(...)
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ------------------------------------------------------------
# Adapter 注册表(每行 = 一个场景)
# ------------------------------------------------------------
# 路径约定:
#   base_model   — Qwen3Guard-Gen-0.6B 本地路径(已 hf-mirror 下好)
#   adapter_path — 训练产物 <out>/adapter/(LoRA safetensors + tokenizer)
#   schema       — train_step1.py 的 --schema 参数
#
_HERE = Path(__file__).resolve().parent
_DEFAULT_BASE = Path("D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B")


@dataclass(frozen=True)
class AdapterSpec:
    name: str
    schema: str                # safety / tempfile / email / code / log / ...
    base_model: Path
    adapter_path: Path
    description: str
    # 调用方需要的额外信息(暂留口子,便于扩展):
    input_hint: str = ""       # "文件路径 + 扩展 + 年龄 + git 状态"
    output_hint: str = ""      # "Safety: X\nAction: Y"


# ------------------------------------------------------------
# 已训 adapter 清单(每加一个新场景,加一行)
# ------------------------------------------------------------
ADAPTERS: dict[str, AdapterSpec] = {
    "safety": AdapterSpec(
        name="safety",
        schema="safety",
        base_model=_DEFAULT_BASE,
        adapter_path=Path("D:/prisir-train-assets/trained/adapter"),
        description="M3.45 Jev 护栏:风险等级 + jailbreak 识别",
        input_hint="自然语言 user 消息",
        output_hint="Safety: Safe/Low/Medium/High/Critical\nJailbreak: Yes/No",
    ),
    "tempfile": AdapterSpec(
        name="tempfile",
        schema="tempfile",
        base_model=_DEFAULT_BASE,
        adapter_path=Path("D:/prisir-train-assets/trained/tempfile/adapter"),
        description="M3.46 临时文件分类:5 类 + delete/review/keep 建议",
        input_hint="文件路径 + 扩展名 + 大小 + 年龄 + git 状态",
        output_hint="Safety: Safe/Low/Medium/High/Critical\n"
                    "Action: Delete/Review/Keep",
    ),
    "disk_cleanup": AdapterSpec(
        name="disk_cleanup",
        schema="disk_cleanup",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/disk_cleanup/adapter"),
        description="M3.47 场景 2:C 盘 Windows 系统清理(WinSxS/Prefetch/Logs/Installer 等)",
        input_hint="Windows 系统子目录文件路径 + 扩展名 + 大小 + 年龄",
        output_hint="Safety: Safe/Low/Medium/High/Critical\n"
                    "Action: Delete/Review/Keep",
    ),
    "disk_cleanup_conf": AdapterSpec(
        name="disk_cleanup_conf",
        schema="disk_cleanup_conf",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/disk_cleanup_conf/adapter"),
        description="M3.45.1 带 confidence 的 disk_cleanup(Jev 风格 threshold gating)",
        input_hint="Windows 系统子目录文件路径 + 扩展名 + 大小 + 年龄",
        output_hint="Safety: Medium:0.6110\nJailbreak: No:0.9995\n"
                    "Action: Review:0.8990",
    ),
    "tempfile_conf": AdapterSpec(
        name="tempfile_conf",
        schema="tempfile_conf",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/tempfile_conf/adapter"),
        description="M3.45.1 带 confidence 的 tempfile(threshold gating 实验性:parse 率不稳)",
        input_hint="文件路径 + 扩展名 + 大小 + 年龄 + git 状态",
        output_hint="Safety: Medium:0.6110\nJailbreak: No:0.9995\n"
                    "Action: Delete:0.8990",
    ),
    "email": AdapterSpec(
        name="email",
        schema="email",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/email/adapter"),
        description="M3.48 邮件分类:5 类风险 + delete/archive/reply/keep 建议",
        input_hint="发件人 + 主题 + 正文摘要 + 距今 + 附件",
        output_hint="Safety: Safe/Low/Medium/High/Critical\n"
                    "Action: Delete/Archive/Reply/Keep",
    ),
    "email_conf": AdapterSpec(
        name="email_conf",
        schema="email_conf",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/email_conf/adapter"),
        description="M3.48 + M3.45.1 邮件带 confidence(calibration 弱:correct 0.40 / wrong 0.36 — 规则器标签信号噪声大)",
        input_hint="发件人 + 主题 + 正文摘要 + 距今 + 附件",
        output_hint="Safety: Medium:0.6110\nJailbreak: No:0.9995\n"
                    "Action: Reply:0.8990",
    ),
    "log": AdapterSpec(
        name="log",
        schema="log",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/log/adapter"),
        description="M3.49 L6 ChatML 重训后(2026-09-23):parse_fail 0.97→0.50, "
                    "输出顺序已修复(Safety→Jailbreak→Action)。"
                    "风险分级偏 High/Medium(Critical 类几乎不预测),后续可加 balanced 训练数据。",
        input_hint="日志来源 + 时间戳 + 级别 + 内容",
        output_hint="Safety: Critical/High/Medium/Low/Safe\n"
                    "Action: Alert/Review/Keep/Drop",
    ),
    "log_conf": AdapterSpec(
        name="log_conf",
        schema="log_conf",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/log_conf/adapter"),
        description="M3.49 L6 + M3.45.1 ChatML 重训后(2026-09-23):"
                    "risk_acc=0.398 / action_acc=0.286 / parse_fail=0.500 / "
                    "calibration delta=+0.163(correct 0.27 / wrong 0.11)。"
                    "比 v1(parse_fail 0.97/risk_acc 0.02)提升 20x,可用但 parse_fail 仍 50%。",
        input_hint="日志来源 + 时间戳 + 级别 + 内容",
        output_hint="Safety: Medium:0.6110\nJailbreak: No:0.9995\n"
                    "Action: Review:0.8990",
    ),
    "intents": AdapterSpec(
        name="intents",
        schema="intents",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/intents/adapter"),
        description="M3.50 ChatML 重训后(2026-09-23):聊天意图 5 类分类 "
                    "(chat/code/search/tool_call/roleplay)。600 条训练集,4 epochs,"
                    "用于 ask_intent() 双通道 fallback 本地通道(Jev 失败时降级)。",
        input_hint="用户消息 + 上下文",
        output_hint="Safety: Safe\nJailbreak: No\nAction: Chat/Code/Search/ToolCall/Roleplay",
    ),
    "intents_conf": AdapterSpec(
        name="intents_conf",
        schema="intents",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/intents_conf/adapter"),
        description="M3.50 + M3.45.1 ChatML 重训后(2026-09-23):聊天意图 5 类 "
                    "+ confidence(Jev 风格 calibrated prob)。同 base,4 epochs,"
                    "bench 见 reports/bench_intents_local_*.json。",
        input_hint="用户消息 + 上下文",
        output_hint="Safety: Safe:0.99\nJailbreak: No:0.99\n"
                    "Action: Chat:0.95",
    ),
    # 注意(2026-09-24 M3.68 清理):base "perf" spec 已删
    #   - 目录 D:/prisir-train-assets/trained/perf/ 仍存在(被 _unified_merge_v2.py / _export_heads.py 反例研究脚本遍历)
    #   - production 路径全走 perf_conf_v2(companion_jev.py:703 + perf_guard.py:132) / perf_conf_v3(classify_perf.py 主用)
    #   - 无代码调 get_adapter("perf"),零运行时引用
    "perf_conf": AdapterSpec(
        name="perf_conf",
        schema="perf",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/perf_conf/adapter"),
        description="M3.51 + M3.45.1 ChatML 重训后(2026-09-23):本地性能快照 5 类 "
                    "+ confidence(Jev 风格 calibrated prob)。同 base,4 epochs。"
                    "Bench 见 reports/bench_perf_local_*.json。"
                    "**M3.51 S9-retry**:v1 22% ACC,critical/high 难识别。",
        input_hint="性能采样 + 时间戳 + NIC 状态 + BugCheck fingerprint",
        output_hint="Safety: Safe:0.99\nJailbreak: No:0.99\n"
                    "Action: Keep|Review|Alert:0.95",
    ),
    "perf_conf_v2": AdapterSpec(
        name="perf_conf_v2",
        schema="perf",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/perf_conf_v2/adapter"),
        description="M3.51 S9-retry(2026-09-23):v1 base 645 + abstract 400 = 1045 条"
                    "重训,critical/high 加 boot_s+bugcount 抽象语义。"
                    "Loss final=0.21;bench 期望 ACC ≥ 0.70。",
        input_hint="性能采样 + boot 后秒数 + bugcheck_count + TAP disconnected 数",
        output_hint="Safety: Critical:0.95\nJailbreak: No:0.99\n"
                    "Action: Alert:0.92",
    ),
    "perf_conf_v3": AdapterSpec(
        name="perf_conf_v3",
        schema="perf",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/perf_conf_v3/adapter"),
        description="M3.62(2026-09-23):perf_conf_v2 基础上补 _action 字段 + "
                    "+ ~150 条 abstract critical 模式(boot_s ≤60 + bugcount, "
                    "ndis_compliance + disconnected_tap, 0x3b e61b7 "
                    "用户机器 fingerprint, KERNEL_SECURITY_CHECK_FAILURE "
                    "+ Driver Verifier)。Loss final ≈ 0.14。"
                    "**关键修 v2 隐藏 bug**:v2 train_step1 build_target 时 "
                    "action=None,模型没学到 Action 输出字段 → v3 补全。",
        input_hint="性能采样 + boot 后秒数 + bugcheck_count + TAP disconnected 数",
        output_hint="Safety: Critical:0.95\nJailbreak: No:0.99\n"
                    "Action: Alert:0.92",
    ),
    "task": AdapterSpec(
        name="task",
        schema="task",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/task/adapter"),
        description="M3.58 ChatML 重训后(2026-09-23):任务分类 6 类 "
                    "(code_call/code_qa/creative/long/fast/general)。580 条训练集 "
                    "(long 80 条 >3000 字符 + 短文本各 100 条 + 边界 mix 60 条),"
                    "4 epochs + max-len 4096 + batch=1(OOM 修复)。Loss final=0.065。"
                    "用于 classify_task_local 双通道 fallback(本机离线可用,远端 fastlane regex 兜底)。",
        input_hint="用户消息 + 上下文",
        output_hint="Safety: Safe\nJailbreak: No\n"
                    "Action: Code_call/Code_qa/Creative/Long/Fast/General",
    ),
    "task_conf": AdapterSpec(
        name="task_conf",
        schema="task",
        base_model=_DEFAULT_BASE,
        adapter_path=Path(
            "D:/prisir-train-assets/trained/task_conf/adapter"),
        description="M3.58 + M3.45.1 ChatML 重训后(2026-09-23):任务分类 6 类 "
                    "+ confidence(Jev 风格 calibrated prob)。同 base,4 epochs,Loss final=0.262。"
                    "Bench 见 reports/bench_task_local_*.json。",
        input_hint="用户消息 + 上下文",
        output_hint="Safety: Safe:0.99\nJailbreak: No:0.99\n"
                    "Action: Code_call:0.95",
    ),
}


# ------------------------------------------------------------
# 加载缓存(lazy load,首次 classify 时才占显存)
# ------------------------------------------------------------
_LOADED: dict[str, "LoadedAdapter"] = {}


@dataclass
class LoadedAdapter:
    spec: AdapterSpec
    model: object = None        # transformers AutoModelForCausalLM
    tokenizer: object = None    # transformers AutoTokenizer
    peft_model: object = None   # peft PeftModel

    def classify(self, text: str, max_new_tokens: int = 40) -> dict:
        """推理一次,返 {"raw": str, "tokens": int}。

        **M3.45.1 B1 加速**(2026-09-23 修正):max_new_tokens 80→40 +
        repetition_penalty=1.2 + no_repeat_ngram_size=6。
        注意:早期版本 rep_penalty=1.5 + eos=\n 会在 confidence 版
        adapter 上塌缩到 `SafetyJailbreakRating:...` 串字符串,
        parse 率 0%。参数回退到 rep=1.2 后保持格式正确,延迟 7s→2.5s。

        **M3.58 注**:不 import train_step1(它会在 module-level 调
        ap.parse_args() 干扰命令行解析),prompt 模板内联
        """
        if self.model is None:
            _lazy_load(self)
        # 内联 ChatML 模板(对齐 train_step1.py:M3.49 L6)
        # safety schema → Safety/Jailbreak 双标签;其他 schema → 加 Action 字段
        if self.spec.schema in ("safety", "safety_conf"):
            prompt = (
                "<|im_start|>user\n"
                f"{text}<|im_end|>\n"
                "<|im_start|>assistant\n"
            )
        else:
            prompt = (
                f"<|im_start|>user\n{text}<|im_end|>\n"
                "<|im_start|>assistant\n"
            )
        inputs = self.tokenizer(prompt, return_tensors="pt").to(
            self.model.device)
        out = self.model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            # M3.61 修:pad_token 训练产物可能为 "" 空字符串,会让 generate 立即停止。
            # 用 eos_token_id 当 fallback 至少能跑到 max_new_tokens。
            pad_token_id=(self.tokenizer.pad_token_id
                          if self.tokenizer.pad_token_id
                          else self.tokenizer.eos_token_id),
            repetition_penalty=1.2,
            no_repeat_ngram_size=6,
        )
        gen = out[0][inputs["input_ids"].shape[1]:]
        raw = self.tokenizer.decode(gen, skip_special_tokens=True).strip()
        return {"raw": raw, "tokens": int(gen.shape[0])}


def _lazy_load(la: LoadedAdapter) -> None:
    """首次调用时加载模型到内存。"""
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from peft import PeftModel

    base = la.spec.base_model
    adapter = la.spec.adapter_path

    if not adapter.exists():
        raise FileNotFoundError(
            f"adapter 不存在: {adapter}\n"
            f"  → 先训练或下好 adapter 到该路径")

    # M3.61 临时补丁:老训练产物 tokenizer_config.json 的 extra_special_tokens 是 list,
    # 新版 transformers 要求 dict(name -> count)。我们在 from_pretrained 前修正 config。
    _patch_tokenizer_extra_special_tokens(adapter)

    la.tokenizer = AutoTokenizer.from_pretrained(
        str(adapter), trust_remote_code=True)
    if la.tokenizer.pad_token is None:
        la.tokenizer.pad_token = la.tokenizer.eos_token

    base_model = AutoModelForCausalLM.from_pretrained(
        str(base), trust_remote_code=True,
        torch_dtype=torch.float16,
        device_map="auto")
    la.model = PeftModel.from_pretrained(base_model, str(adapter))
    la.model.eval()


def _patch_tokenizer_extra_special_tokens(adapter_path) -> None:
    """把 list 形式的 extra_special_tokens 转成 dict(name->count),in-place 写回 tokenizer_config.json。

    老版本 transformers 训练产物可能是 list(如 ["", "<|im_start|>", ...]);
    新版 transformers 加载时直接调用 `list(special_tokens.keys())`,要求是 dict。
    该补丁保证本机能用旧 adapter 而不需要重训(训时修复才彻底,见 train_step1.py)。
    """
    import json
    cfg_path = Path(adapter_path) / "tokenizer_config.json"
    if not cfg_path.exists():
        return
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    est = cfg.get("extra_special_tokens")
    if isinstance(est, list):
        # list → dict,每个 token 默认 count=1
        cfg["extra_special_tokens"] = {tok: 1 for tok in est}
        cfg_path.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8")
    elif isinstance(est, dict):
        # M3.61 二次修:新版 transformers 要求 dict 的 value 是 str(AddedToken) 不是 int
        # 把所有 int value 改回 str("1") 或直接清空字段(transformers 似乎不需要它)
        # 安全做法:把 int value 换成 "1" 字符串
        bad = False
        for k, v in list(est.items()):
            if isinstance(v, int):
                bad = True
                est[k] = "1"
        if bad:
            cfg["extra_special_tokens"] = est
            cfg_path.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2),
                encoding="utf-8")


def get_adapter(name: str) -> LoadedAdapter:
    """取一个 adapter(name 必须在 ADAPTERS 里)。"""
    if name not in ADAPTERS:
        raise KeyError(
            f"未知 adapter: {name}\n"
            f"  可用: {list(ADAPTERS.keys())}")
    if name not in _LOADED:
        _LOADED[name] = LoadedAdapter(spec=ADAPTERS[name])
    return _LOADED[name]


def list_scenarios() -> list[str]:
    """列已注册的所有场景名。"""
    return list(ADAPTERS.keys())


def register(spec: AdapterSpec) -> None:
    """注册新 adapter(供训练脚本训完回调)。"""
    ADAPTERS[spec.name] = spec
    # 不主动 invalidate 缓存,允许运行时 hot-load


# ------------------------------------------------------------
# CLI:查看注册表
# ------------------------------------------------------------
def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="adapter 注册表查询")
    ap.add_argument("--list", action="store_true",
                    help="列所有 adapter")
    ap.add_argument("--probe", metavar="NAME",
                    help="探测某个 adapter 路径是否就位")
    args = ap.parse_args()

    if args.list or not args.probe:
        print(f"已注册 adapter({len(ADAPTERS)} 个):")
        for name, spec in ADAPTERS.items():
            ok = "✅" if spec.adapter_path.exists() else "❌"
            print(f"  {ok} {name:12s} schema={spec.schema:8s} "
                  f"path={spec.adapter_path}")
            print(f"     {spec.description}")
        return 0

    if args.probe:
        spec = ADAPTERS.get(args.probe)
        if not spec:
            print(f"❌ 未知 adapter: {args.probe}")
            return 1
        print(f"scenario: {spec.name}")
        print(f"schema:   {spec.schema}")
        print(f"base:     {spec.base_model}")
        print(f"adapter:  {spec.adapter_path}")
        print(f"  exists: {spec.adapter_path.exists()}")
        return 0 if spec.adapter_path.exists() else 2


if __name__ == "__main__":
    raise SystemExit(main())