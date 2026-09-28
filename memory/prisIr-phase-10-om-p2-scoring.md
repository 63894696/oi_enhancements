---
name: prisIr-phase-10-om-p2-scoring
description: Phase 10 OM-P2 Provider 7 维度评分 + 自动选最优 + 预算降级(2026-09-28)
metadata:
  type: project
---

# Phase 10 OM-P2 Provider Scoring ship(2026-09-28)

承接 [[prisIr-openmontage-recon]] + [[prisIr-phase-9-om-p1-and-ma-p1]] + 用户「最小 agent 团队,spawn on-demand」决策。

## 关键洞察(用户拍板)
最小 agent 团队**不指定固定角色**,跟主 agent 一样按需配置;
**Provider 也是一样**:不绑单一 provider,按需按预算按健康度自动选最优。

## OM-P2 ship 内容

### 7 维度评分(借鉴 OpenMontage provider_scoring.py)
| 维度 | 权重 | 范围 | 说明 |
|------|------|------|------|
| quality | 0.30 | [0,1] | 输出质量 |
| speed | 0.15 | [0,1] | 延迟 |
| cost | 0.20 | [0,1] | 1.0 = 免费 |
| availability | 0.10 | [0,1] | 当前健康(0 = unhealthy → ineligible) |
| quota | 0.10 | [0,1] | 剩余配额(0 = quota_exhausted → ineligible) |
| history_success | 0.10 | [0,1] | 历史成功率 |
| history_failure | -0.05 | [0,1] | 失败率(负向加权;>0.5 → ineligible) |

### 关键文件
| 文件 | 改动 | 用途 |
|------|------|------|
| `prisIr_work/video_provider_scoring.py` | NEW ~370 行 | Score / ProviderScore / pick_best / 默认 16 provider 注册 |
| `prisIr_work/video_creator.py` | +30 行 | `pick_provider_for_creator(tag, context, candidates)` helper |
| `tests/test_phase_10_om_p2_provider_scoring.py` | NEW ~220 行 | **13/13 测绿** |

### 默认 ship 16 provider(4 类)
- **TTS** (4):piper / edge_tts / elevenlabs_tts / cosyvoice2
- **图生视频** (4):kling / veo3 / seedance / local_wan
- **BGM** (3):pixabay_music / fma_music / local_silence(fallback)
- **视频素材** (3):pexels_video / pixabay_video / archive_org

## 关键设计

### 1. 不绑单一 provider
不写死 kling / veo3 / edge_tts 等;`pick_best` 从候选列表按权重选。
未来用户加 provider → `register_provider(name, meta)` 即可,**0 行改 video_creator**。

### 2. 预算自动降级
`context={"budget_remaining": 0.0}` 时:
- 所有 cost<1.0(付费)provider → ineligible + reason="budget_exhausted"
- 自动选免费 provider(piper / edge_tts / local_silence / fma_music 等)
- 真实生产环境可接 Easel / PrisirAI 预算系统

### 3. fail-soft
- 全 ineligible → 返 None,creator 走自己的 fallback
- 未知 provider / tag → 返 None,绝不抛栈
- 写文件失败 / 解析失败 → log error,继续

### 4. 不破坏向后兼容
- video_creator.py 加 helper,不删任何 creator / endpoint
- 现有 `create(creator_name)` 调用 0 改动
- TtsCreator 等内部没改,旧 `VOICE_PROVIDER` env 路径照常工作

## 测试矩阵(13/13 全绿)

1. Score dataclass 默认值 + weighted 计算精度
2. register_provider / get_provider_meta / list_providers(tag)
3. 未知 provider → ineligible + reason="unknown_provider"
4. 免费 provider(budget 充足)cost=1.0
5. availability=0 → ineligible + unhealthy
6. quota=0 → ineligible + quota_exhausted
7. history_failure > 0.5 → ineligible + high_failure_rate
8. pick_best 选总分最高
9. budget=0 自动降级免费
10. 全 ineligible → None
11. video_creator.pick_provider_for_creator('tts', budget=0) → piper
12. video_creator.pick_provider_for_creator('music') → pixabay_music
13. 未知 tag → None(fail-soft)

## 累计测试 **183**

Phase 9 170 + Phase 10 13 = 183

## 后续 OM-P3 / P4 计划

| Phase | 内容 | 状态 |
|-------|------|------|
| **OM-P3** | pre-compose 校验(分镜合理 / 角色卡一致 / 风格统一)| pending |
| **OM-P4** | 免费资源接入(Piper / Pixabay / Pexels / FMA 真集成) | pending |
| **OM-P5** | 成本预算治理(实时算 token + 限额 + 超限 auto-degrade) | pending |

## 关键洞察(2026-09-28)

1. **provider 也是 spawn-on-demand 资源** — 不绑单一,跟 agent 团队范式一致
2. **预算感知可降级** — budget_remaining 是关键上下文,跟 RAG / WebSearch 一样需要
3. **历史数据可校准** — 用户跑 30 天后,history_success/failure 自动校准 provider 评分
4. **不绑单一** — 不重蹈 Phase 8 重写的覆辙(用户拍板「不做去重」精神一致)

## 与已有 ship 路径的关系

```
Skills 工作台(8 阶段 ship):
  P0-P8 ✓
视频 OpenMontage 借鉴:
  P0  决策 ✓
  P1  checkpoint ✓(Phase 9)
  P2  provider scoring ✓(本 commit)
  P3-P5 pending
多 agent OpenAI Agents SDK 借鉴:
  P1  handoff schema ✓(Phase 9)
  P2-P6 pending
```

**How to apply:**
- 用户问「视频用哪个 TTS」→ `pick_provider_for_creator("tts", context)` 自动选
- 用户问「预算用完了怎么办」→ cost 维度自动降级免费
- 用户问「provider 全挂怎么办」→ 返 None + reason,creator 走 fallback
- 用户问「provider 评分怎么校准」→ 未来 hook 进 history_success/failure 后自学习
- 用户问「加新 provider 要改什么」→ `register_provider(name, meta)` 一行