#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agentjev_runtime.py — M3.66 L3 本地 AgentJev classification-head 推理(2026-09-23)

目的:
  - 不依赖 aliyun,本地推理 perf_ch_v1 classification-head 模型
  - backbone 用 Qwen3-0.6B 原生(无 LM head) + proj_in/set_encoder/proj_out/scorer 加载
  - 单条输入 (state_text + question + candidates) → softmax 概率 → dict

架构对应(/workspace/agent-jev-repo/agentjev/model.py):
  - Qwen3Model (无 LM head, hidden=1024)
  - proj_in    : Linear(1024, 256)
  - set_encoder: 2-layer TransformerEncoder, 256 dim, no positional
  - proj_out   : Linear(256, 1024)
  - scorer     : RMSNorm → Linear(1024,256) → SiLU → Linear(256,1)

推理路径(PathEncoder v1):
  - 每个 (state, question, candidate) 拼成 [state][q][c] 序列,backbone 跑一次
  - 取 cand_end_pos 位置 hidden state → scatter 到 [Bq, Cmax, H]
  - set encoder + scorer → per-candidate logit → softmax → prob

head-only 文件:
  - /workspace/agent-jev-output/perf_smoke_export/perf_ch_head.pt
  - keys: proj_in.* / set_encoder.* / proj_out.* / scorer.*
  - 不含 path_encoder.backbone.* (596M 参数)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn as nn
from transformers import AutoTokenizer, Qwen3Model


# ----------- 与 train 端严格对齐的常量 -----------
STATE_PREFIX = "[STATE] "
QUESTION_PREFIX = "\n[QUESTION] "
CANDIDATE_PREFIX = "\n[CANDIDATE] "
SUP_CODES = {
    "known_distribution": 0,
    "deterministic": 0,
    "binomial_counts": 1,
    "multiclass_counts": 2,
    "empirical": 3,
    "heuristic": 3,
    "teacher": 3,
}


# ----------- 模型组件(与 agentjev/model.py 一致,精简版) -----------
class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(dim))
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        dtype = x.dtype
        x = x.float()
        x = x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + self.eps)
        return (self.weight * x).to(dtype)


class CandidateSetEncoder(nn.Module):
    def __init__(self, d_model: int = 256, nhead: int = 4, num_layers: int = 2,
                 dropout: float = 0.0):
        super().__init__()
        layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead,
            dim_feedforward=4 * d_model, dropout=dropout,
            activation="gelu", batch_first=True, norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers)

    def forward(self, x: torch.Tensor, cand_mask: torch.Tensor) -> torch.Tensor:
        return self.encoder(x, src_key_padding_mask=~cand_mask)


class ScalarScorer(nn.Module):
    def __init__(self, in_dim: int = 1024, hidden: int = 256):
        super().__init__()
        self.norm = RMSNorm(in_dim)
        self.fc1 = nn.Linear(in_dim, hidden)
        self.act = nn.SiLU()
        self.fc2 = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc2(self.act(self.fc1(self.norm(x)))).squeeze(-1)


@dataclass
class AgentJevRuntime:
    """加载 backbone + head-only 后,提供 infer_one(state, candidates) → probs"""

    backbone: Qwen3Model
    head: dict  # {proj_in, set_encoder, proj_out, scorer} (nn.Module)
    tokenizer: object
    device: torch.device
    hidden_size: int
    set_dim: int = 256

    @classmethod
    def load(
        cls,
        backbone_path: str,
        head_path: str,
        device: str = "cuda" if torch.cuda.is_available() else "cpu",
        dtype: torch.dtype = torch.float32,
    ) -> "AgentJevRuntime":
        """backbone 从 HF transformers 加载 Qwen3Model;head_only 从 .pt 加载"""
        tokenizer = AutoTokenizer.from_pretrained(backbone_path)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token_id = tokenizer.eos_token_id

        # backbone 用 Qwen3ForCausalLM → 取 model 属性即 Qwen3Model(无 LM head)
        from transformers import Qwen3ForCausalLM
        full = Qwen3ForCausalLM.from_pretrained(backbone_path, torch_dtype=dtype)
        backbone = full.model  # type: ignore[attr-defined]
        del full
        backbone.to(device).eval()

        # head_only load
        head_ckpt = torch.load(head_path, map_location="cpu", weights_only=False)
        head_sd = head_ckpt["state_dict"]
        hidden = backbone.config.hidden_size

        proj_in = nn.Linear(hidden, 256).to(device).eval()
        set_encoder = CandidateSetEncoder(256, 4, 2).to(device).eval()
        proj_out = nn.Linear(256, hidden).to(device).eval()
        scorer = ScalarScorer(hidden, 256).to(device).eval()

        # 加载 weights(strict 关键)
        proj_in.load_state_dict({k.removeprefix("proj_in."): v
                                 for k, v in head_sd.items()
                                 if k.startswith("proj_in.")})
        proj_out.load_state_dict({k.removeprefix("proj_out."): v
                                  for k, v in head_sd.items()
                                  if k.startswith("proj_out.")})
        scorer.load_state_dict({k.removeprefix("scorer."): v
                                for k, v in head_sd.items()
                                if k.startswith("scorer.")})
        # set_encoder.* 前缀在 sd 里是 "set_encoder.encoder.layers.X.self_attn..."
        se_sd = {k.removeprefix("set_encoder."): v
                 for k, v in head_sd.items() if k.startswith("set_encoder.")}
        set_encoder.load_state_dict(se_sd)

        return cls(
            backbone=backbone,
            head={"proj_in": proj_in, "set_encoder": set_encoder,
                  "proj_out": proj_out, "scorer": scorer},
            tokenizer=tokenizer,
            device=torch.device(device),
            hidden_size=hidden,
        )

    @torch.no_grad()
    def infer_one(
        self,
        state: str,
        question: str,
        candidates: list[str],
        max_len: int = 256,
        max_state_tokens: int = 128,
    ) -> dict:
        """单条推理(state + 1 question + N candidates)→ {candidates, probs, top, latency_ms}"""
        t0 = time.time()
        # prefix
        state_text = state if state.startswith(STATE_PREFIX) else STATE_PREFIX + state
        q_text = QUESTION_PREFIX + question
        cand_texts = [CANDIDATE_PREFIX + c for c in candidates]

        # tokenize
        s_ids = self.tokenizer(state_text, add_special_tokens=False)["input_ids"]
        if len(s_ids) > max_state_tokens:
            s_ids = s_ids[:max_state_tokens]
        q_ids = self.tokenizer(q_text, add_special_tokens=False)["input_ids"]
        cand_ids_list = [
            self.tokenizer(ct, add_special_tokens=False)["input_ids"]
            for ct in cand_texts
        ]

        # pad cand 到最大长度,构建 batch (P, L)
        max_cand = max(len(c) for c in cand_ids_list)
        L = len(s_ids) + len(q_ids) + max_cand
        if L > max_len:
            # 截 q(尾保 prefix)
            budget_sq = max_len - max_cand
            keep_q = max(1, budget_sq - len(s_ids))
            q_ids = q_ids[:keep_q]
            L = len(s_ids) + len(q_ids) + max_cand

        P = len(candidates)
        input_ids = torch.zeros(P, L, dtype=torch.long)
        attn = torch.zeros(P, L, dtype=torch.long)
        cand_end_pos = torch.zeros(P, dtype=torch.long)
        for i, c_ids in enumerate(cand_ids_list):
            seq = s_ids + q_ids + c_ids
            seq = seq[:L]  # 截断保护
            input_ids[i, :len(seq)] = torch.tensor(seq, dtype=torch.long)
            attn[i, :len(seq)] = 1
            cand_end_pos[i] = min(len(seq) - 1, L - 1)

        input_ids = input_ids.to(self.device)
        attn = attn.to(self.device)

        # backbone
        out = self.backbone(input_ids=input_ids, attention_mask=attn, use_cache=False)
        hs = out.last_hidden_state  # [P, L, H]
        # 取 cand_end_pos 位置 hidden
        idx = torch.arange(P, device=self.device)
        cand_vecs = hs[idx, cand_end_pos.to(self.device)]  # [P, H]

        # candidate set encoder + scorer
        # 我们的 wrapper 是 path encoder,scatter 到 [1, Cmax, H]
        Bq, Cmax = 1, P
        cand_mask = torch.ones(Bq, Cmax, dtype=torch.bool, device=self.device)

        x = self.head["proj_in"](cand_vecs).unsqueeze(0)  # [1, P, 256]
        x = x * cand_mask.unsqueeze(-1).to(x.dtype)
        enc = self.head["set_encoder"](x, cand_mask)
        rep = cand_vecs.unsqueeze(0) + self.head["proj_out"](enc)  # [1, P, H]
        logits = self.head["scorer"](rep).squeeze(0)  # [P]
        logits = logits.masked_fill(~cand_mask.squeeze(0), -1e4)

        # softmax → prob
        probs = torch.softmax(logits, dim=-1).cpu().tolist()
        top_idx = int(torch.argmax(logits).item())
        latency_ms = (time.time() - t0) * 1000.0

        return {
            "candidates": list(candidates),
            "probs": probs,
            "top": candidates[top_idx],
            "top_prob": probs[top_idx],
            "latency_ms": round(latency_ms, 1),
        }


def load_perf_ch_v1(
    backbone_path: str = "D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B",
    head_path: str = "D:/prisir-train-assets/trained/perf_ch_v1/adapter/perf_ch_head.pt",
    device: str = "cuda" if torch.cuda.is_available() else "cpu",
) -> AgentJevRuntime:
    """加载 perf_ch_v1 head-only → 推理 perf 风险 5 类

    backbone 用本地 Qwen3Guard-Gen-0.6B(与 Qwen3-0.6B 同构:
    hidden=1024, 28 layers, 16 heads, GQA 8, vocab 151936);
    AgentJev 取其 .model 属性(Qwen3Model,无 LM head)。
    """
    return AgentJevRuntime.load(backbone_path, head_path, device=device)


# ----------- M3.66 L3: unified head(跨 8 scenario) -----------
# 8 个 scenario 的 question + candidates dict
SCENARIO_QUESTIONS = {
    "disk_cleanup": "Windows 系统文件清理风险等级(safe/low/medium/high/critical)",
    "tempfile": "临时文件清理风险等级(safe/low/medium/high/critical)",
    "email": "邮件风险等级(safe/low/medium/high/critical)",
    "log": "日志条目风险等级(safe/low/medium/high/critical)",
    "perf": "本地性能采样风险等级(safe/low/medium/high/critical)",
    "intents": "用户消息意图分类(chat/code/search/tool_call/roleplay)",
    "task": "用户消息任务分类(code_call/code_qa/creative/long/fast/general)",
    "safety": "用户消息是否包含越狱或提示注入攻击(safe/unsafe)",
}

SCENARIO_CANDIDATES = {
    "disk_cleanup": ["safe", "low", "medium", "high", "critical"],
    "tempfile": ["safe", "low", "medium", "high", "critical"],
    "email": ["safe", "low", "medium", "high", "critical"],
    "log": ["safe", "low", "medium", "high", "critical"],
    "perf": ["safe", "low", "medium", "high", "critical"],
    "intents": ["chat", "code", "search", "tool_call", "roleplay"],
    "task": ["code_call", "code_qa", "creative", "long", "fast", "general"],
    "safety": ["safe", "unsafe"],
}


_UNIFIED_RT: AgentJevRuntime | None = None
_UNIFIED_V2_RT: AgentJevRuntime | None = None


def load_unified(
    backbone_path: str = "D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B",
    head_path: str = "D:/prisir-train-assets/trained/unified_ch_v3/adapter/unified_ch_head.pt",
    device: str = "cuda" if torch.cuda.is_available() else "cpu",
    use_cache: bool = True,
) -> AgentJevRuntime:
    """加载 unified v1 head(单 model 跨 8 scenario)。推理时传 per-scenario candidates"""
    global _UNIFIED_RT
    if use_cache and _UNIFIED_RT is not None:
        return _UNIFIED_RT
    rt = AgentJevRuntime.load(backbone_path, head_path, device=device)
    if use_cache:
        _UNIFIED_RT = rt
    return rt


def load_unified_v2(
    backbone_path: str = "D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B",
    head_path: str = "D:/prisir-train-assets/trained/unified_ch_v3_v2/adapter/unified_ch_v2_head.pt",
    device: str = "cuda" if torch.cuda.is_available() else "cpu",
    use_cache: bool = True,
) -> AgentJevRuntime:
    """M3.66+ 加载 unified v2 head(5498 samples × 3000 steps 训出,扩展 safety/tmpfile/email/disk_cleanup 弱项)"""
    global _UNIFIED_V2_RT
    if use_cache and _UNIFIED_V2_RT is not None:
        return _UNIFIED_V2_RT
    rt = AgentJevRuntime.load(backbone_path, head_path, device=device)
    if use_cache:
        _UNIFIED_V2_RT = rt
    return rt


def infer_scenario(
    scenario: str,
    state: str,
    backbone_path: str = "D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B",
    head_path: str = "D:/prisir-train-assets/trained/unified_ch_v3/adapter/unified_ch_head.pt",
    version: str = "v1",
) -> dict:
    """单条推理,自动用 scenario 对应 question + candidates → {top, top_prob, ...}

    Args:
        version: "v1" (M3.66 L3 unified) 或 "v2" (M3.66+ 扩训版)
    """
    if version == "v2":
        head_path = "D:/prisir-train-assets/trained/unified_ch_v3_v2/adapter/unified_ch_v2_head.pt"
        rt = load_unified_v2(backbone_path, head_path)
    else:
        rt = load_unified(backbone_path, head_path)
    question = SCENARIO_QUESTIONS[scenario]
    candidates = SCENARIO_CANDIDATES[scenario]
    return rt.infer_one(state, question, candidates)


# ----------- CLI: 单条 demo 推理 -----------
if __name__ == "__main__":
    import sys
    sample_state = (
        "性能采样: ts=2026-09-23T22:00:00Z cpu 35% mem 50% "
        "nic_status=[{nic:eth0,isup:True},{nic:tap0901,isup:False}] "
        "boot_s=45 bugcheck_count=2 last_bugcheck=0x3b last_offset=0xfffff8000e61b7"
    )
    if len(sys.argv) > 1 and sys.argv[1] == "--sample":
        # 用户机器蓝屏 fingerprint(M3.51 S1 调研)
        pass
    candidates = ["safe", "low", "medium", "high", "critical"]
    question = "本地性能采样风险等级(safe/low/medium/high/critical)"

    print("[perf_ch_v1] loading...")
    t0 = time.time()
    rt = load_perf_ch_v1()
    print(f"[perf_ch_v1] loaded in {time.time()-t0:.1f}s, device={rt.device}")

    print(f"[perf_ch_v1] inferring sample (boot_s=45 + 0x3b + tap0901 disconnected)...")
    out = rt.infer_one(sample_state, question, candidates)
    print(json.dumps(out, ensure_ascii=False, indent=2))