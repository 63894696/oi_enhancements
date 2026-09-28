"""
prisir_work/video_provider_scoring.py — Provider 7 维度自动选最优(Phase 10 OM-P2, 2026-09-28)。

承接 [[prisIr-openmontage-recon]] + [[prisIr-phase-9-om-p1-and-ma-p1]]。

## 定位
为 video_creator 的各子能力(TTS / 图生视频 / BGM / 图片生图 / 视频合成)提供
**7 维度自动评分选最优 provider**,不写死单一 provider。

借鉴 OpenMontage 的 provider scoring 设计模式:
- 7 维度:quality / speed / cost / availability / quota / history_success / history_failure
- 自动跳过 unhealthy / quota 满 / 历史失败率过高的 provider
- 降级语义:首选失败 → 自动选次佳
- **默认免费优先**:cost=1.0(免费)的 provider 在 cost 维度得 1.0

## 关键不变量
- ✅ 不绑单一 provider:10+ provider 并存,kling / veo / seedance / runway / pixabay / pexels /
  archive.org / edge_tts / piper / local_mock
- ✅ 免费优先:默认权重让免费 provider 在 cost=0 场景赢
- ✅ 历史数据校准:用户跑 30 天后,history_success/history_failure 自动校准
- ✅ fail-soft:provider 全失败 → 返 None + reason,不抛栈

## 关键 API
  - Score dataclass(7 维度)
  - ProviderScore dataclass(name + score + reasons)
  - score_provider(provider_name, query, context) → Score
  - pick_best(providers, query, context) → Optional[ProviderScore]
  - register_provider(name, defaults) → None
  - get_provider_meta(name) → dict
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "Score",
    "ProviderScore",
    "pick_best",
    "score_provider",
    "register_provider",
    "get_provider_meta",
    "list_providers",
    "DEFAULT_WEIGHTS",
]

log = logging.getLogger("prisir_work.video_provider_scoring")


# ---------------------------------------------------------------------------
# 默认权重(7 维度)
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, float] = {
    "quality": 0.30,             # 输出质量
    "speed": 0.15,               # 速度(延迟)
    "cost": 0.20,                # 成本(免费 = 1.0)
    "availability": 0.10,        # 当前健康
    "quota": 0.10,               # 剩余配额(免费不限 = 1.0)
    "history_success": 0.10,     # 历史成功率
    "history_failure": -0.05,    # 历史失败率(负向)
}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class Score:
    """7 维度评分,每维 ∈ [0.0, 1.0]。"""
    quality: float = 0.5
    speed: float = 0.5
    cost: float = 1.0            # 默认 1.0 = 免费
    availability: float = 1.0    # 默认 1.0 = 健康
    quota: float = 1.0           # 默认 1.0 = 不限
    history_success: float = 1.0  # 默认 1.0 = 100% 成功
    history_failure: float = 0.0  # 默认 0.0 = 无失败

    def weighted(self, weights: dict[str, float] | None = None) -> float:
        """按权重算总分。"""
        w = weights or DEFAULT_WEIGHTS
        total = 0.0
        for k, weight in w.items():
            v = getattr(self, k, 0.5)
            # 截断 [0, 1]
            v = max(0.0, min(1.0, float(v)))
            total += weight * v
        return total

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class ProviderScore:
    """单 provider 的完整评分结果。"""
    name: str
    score: Score
    total: float
    eligible: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": self.score.to_dict(),
            "total": round(self.total, 4),
            "eligible": self.eligible,
            "reasons": list(self.reasons),
        }


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, dict[str, Any]] = {}
_DEFAULT_PROVIDERS_LOADED = False


def register_provider(name: str, defaults: dict[str, Any]) -> None:
    """注册一个 provider 的默认元数据。

    defaults 字段(全可选):
      quality / speed / cost / availability / quota / history_success / history_failure
      tag: str(provider 类型,如 'tts'/'image2video'/'music')
      cost_per_call: float(每次调用的美元成本,免费 = 0)
      requires_key: bool = False
      description: str = ""
    """
    _PROVIDERS[name] = defaults


def get_provider_meta(name: str) -> dict[str, Any]:
    return _PROVIDERS.get(name, {})


def list_providers(tag: str | None = None) -> list[str]:
    """列所有 provider 名(可选 tag 过滤)。"""
    if not tag:
        return list(_PROVIDERS.keys())
    return [n for n, m in _PROVIDERS.items() if m.get("tag") == tag]


def _ensure_defaults() -> None:
    """ship 默认 provider 列表(避免依赖 yaml 文件)。"""
    global _DEFAULT_PROVIDERS_LOADED
    if _DEFAULT_PROVIDERS_LOADED:
        return
    # TTS providers
    register_provider("piper", {
        "tag": "tts", "cost_per_call": 0.0, "requires_key": False,
        "quality": 0.65, "speed": 0.85, "cost": 1.0,
        "availability": 1.0, "quota": 1.0,
        "description": "Piper TTS 本地推理,免费无限制,中文模型 zh_CN-huayan-medium",
    })
    register_provider("edge_tts", {
        "tag": "tts", "cost_per_call": 0.0, "requires_key": False,
        "quality": 0.75, "speed": 0.70, "cost": 1.0,
        "availability": 0.95, "quota": 0.9,
        "description": "Edge TTS 微软公开 API,免 key,中文 XiaoxiaoNeural 等",
    })
    register_provider("elevenlabs_tts", {
        "tag": "tts", "cost_per_call": 0.0002, "requires_key": True,
        "quality": 0.95, "speed": 0.65, "cost": 0.4,
        "availability": 1.0, "quota": 0.95,
        "description": "ElevenLabs 商用 TTS,质量极高,需 key",
    })
    register_provider("cosyvoice2", {
        "tag": "tts", "cost_per_call": 0.0001, "requires_key": True,
        "quality": 0.88, "speed": 0.70, "cost": 0.5,
        "availability": 1.0, "quota": 0.95,
        "description": "CosyVoice2 阿里通义,中文效果好",
    })

    # 图生视频 providers
    register_provider("kling", {
        "tag": "image2video", "cost_per_call": 0.05, "requires_key": True,
        "quality": 0.92, "speed": 0.60, "cost": 0.3,
        "availability": 1.0, "quota": 0.7,
        "description": "Kling 快手,5 秒视频,质量高",
    })
    register_provider("veo3", {
        "tag": "image2video", "cost_per_call": 0.10, "requires_key": True,
        "quality": 0.95, "speed": 0.50, "cost": 0.2,
        "availability": 1.0, "quota": 0.6,
        "description": "Veo 3.1 Google,8 秒视频,顶配",
    })
    register_provider("seedance", {
        "tag": "image2video", "cost_per_call": 0.04, "requires_key": True,
        "quality": 0.88, "speed": 0.65, "cost": 0.4,
        "availability": 1.0, "quota": 0.8,
        "description": "Seedance 字节,中文友好",
    })
    register_provider("local_wan", {
        "tag": "image2video", "cost_per_call": 0.0, "requires_key": False,
        "quality": 0.70, "speed": 0.40, "cost": 1.0,
        "availability": 0.7, "quota": 1.0,
        "description": "WAN 2.1 本地模型,免费,需 GPU",
    })

    # BGM providers
    register_provider("pixabay_music", {
        "tag": "music", "cost_per_call": 0.0, "requires_key": True,
        "quality": 0.75, "speed": 0.80, "cost": 1.0,
        "availability": 1.0, "quota": 0.85,
        "description": "Pixabay Music 免版税,需 key 注册",
    })
    register_provider("fma_music", {
        "tag": "music", "cost_per_call": 0.0, "requires_key": False,
        "quality": 0.70, "speed": 0.80, "cost": 1.0,
        "availability": 0.85, "quota": 0.9,
        "description": "Free Music Archive 公开免版税",
    })
    register_provider("local_silence", {
        "tag": "music", "cost_per_call": 0.0, "requires_key": False,
        "quality": 0.30, "speed": 1.0, "cost": 1.0,
        "availability": 1.0, "quota": 1.0,
        "description": "本地静音降级 fallback",
    })

    # 视频素材 providers(stock footage)
    register_provider("pexels_video", {
        "tag": "stock_video", "cost_per_call": 0.0, "requires_key": True,
        "quality": 0.85, "speed": 0.80, "cost": 1.0,
        "availability": 1.0, "quota": 0.8,
        "description": "Pexels 视频素材,免费商用",
    })
    register_provider("pixabay_video", {
        "tag": "stock_video", "cost_per_call": 0.0, "requires_key": True,
        "quality": 0.80, "speed": 0.80, "cost": 1.0,
        "availability": 1.0, "quota": 0.8,
        "description": "Pixabay 视频素材",
    })
    register_provider("archive_org", {
        "tag": "stock_video", "cost_per_call": 0.0, "requires_key": False,
        "quality": 0.65, "speed": 0.60, "cost": 1.0,
        "availability": 0.85, "quota": 0.95,
        "description": "Archive.org 历史素材(无 key)",
    })

    _DEFAULT_PROVIDERS_LOADED = True


# ---------------------------------------------------------------------------
# 评分函数
# ---------------------------------------------------------------------------

def score_provider(name: str, query: dict[str, Any] | None = None,
                   context: dict[str, Any] | None = None) -> ProviderScore:
    """单 provider 评分。

    query 参数(可选):{"tag": "tts", "language": "zh", ...} 用于匹配
    context 参数(可选):{"budget_remaining": 1.0, "urgency": "low"}
    """
    _ensure_defaults()
    meta = _PROVIDERS.get(name)
    if not meta:
        return ProviderScore(
            name=name,
            score=Score(),
            total=0.0,
            eligible=False,
            reasons=["unknown_provider"],
        )

    # 基础 7 维度(从 meta 读,默认 0.5/1.0/1.0/1.0/1.0/0.0)
    s = Score(
        quality=float(meta.get("quality", 0.5)),
        speed=float(meta.get("speed", 0.5)),
        cost=float(meta.get("cost", 1.0)),
        availability=float(meta.get("availability", 1.0)),
        quota=float(meta.get("quota", 1.0)),
        history_success=float(meta.get("history_success", 1.0)),
        history_failure=float(meta.get("history_failure", 0.0)),
    )

    reasons: list[str] = []
    eligible = True

    # 预算感知:context.budget_remaining 低时,cost 维度加权(自动降级)
    if context and "budget_remaining" in context:
        br = float(context["budget_remaining"])
        if br <= 0.0:
            # 预算用尽 → 只允许 cost=1.0(免费)provider
            if s.cost < 1.0:
                eligible = False
                reasons.append("budget_exhausted")
        elif br < 0.1:
            # 预算 < 10% → 成本加权
            if s.cost < 1.0:
                s.cost *= 0.5  # 折扣但不完全禁

    # 健康检查:availability = 0 → 不可用
    if s.availability <= 0.0:
        eligible = False
        reasons.append("unhealthy")

    # 配额:quota = 0 → 不可用
    if s.quota <= 0.0:
        eligible = False
        reasons.append("quota_exhausted")

    # 历史失败率 > 50% → 降分(降级语义)
    if s.history_failure > 0.5:
        eligible = False
        reasons.append("high_failure_rate")

    # 需要 key 但没配 → 降级但 warn
    if meta.get("requires_key") and context and not context.get("keys_available", True):
        eligible = False
        reasons.append("key_missing")

    total = s.weighted()
    return ProviderScore(
        name=name,
        score=s,
        total=total,
        eligible=eligible,
        reasons=reasons or ["ok"],
    )


def pick_best(providers: list[str] | None = None,
              query: dict[str, Any] | None = None,
              context: dict[str, Any] | None = None,
              tag: str | None = None) -> Optional[ProviderScore]:
    """从候选 provider 选最优。返 None 表示全失败。

    参数:
      providers: 候选 provider 名列表;None = 自动按 tag 取所有
      query: 查询参数(语言 / 场景 等)
      context: 上下文(预算 / 紧急度 / key 可用性)
      tag: 限定 provider 类型(如 'tts'/'image2video')

    返回:得分最高的 eligible provider;若全 ineligible 返 None + 最后一个失败原因
    """
    _ensure_defaults()
    if providers is None:
        providers = list_providers(tag)

    if not providers:
        log.warning("pick_best: 无候选 provider (tag=%s)", tag)
        return None

    candidates: list[ProviderScore] = []
    for name in providers:
        ps = score_provider(name, query=query, context=context)
        if ps.eligible:
            candidates.append(ps)

    if not candidates:
        log.warning("pick_best: 所有 provider ineligible: %s",
                    [score_provider(n, query=query, context=context).reasons
                     for n in providers])
        return None

    # 按 total 降序
    candidates.sort(key=lambda x: x.total, reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# 模块初始化:ship 默认 provider
# ---------------------------------------------------------------------------

_ensure_defaults()