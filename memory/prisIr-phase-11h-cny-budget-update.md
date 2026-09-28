---
name: prisIr-phase-11h-cny-budget-update
description: Phase 11-H 国内 CNY provider + ¥100/集预算拍板 ship(2026-09-28)
metadata:
  type: project
---

# Phase 11-H 国内 CNY provider + ¥100/集预算 ship(2026-09-28)

承接 [[prisIr-phase-11-om-p3-pre-compose]] + 用户「国内短剧成本很高」决策。

## 关键拍板(用户原话)
「关于预算这里算的是美金,那如果跑国内的视频模型用人民币结算,这个预算有用吗?
虽然我不清楚海外视频模型的短剧制作结果,但据我了解国内视频制作短剧成本很高。」

**两个追问回答**:
1. **货币**:**全转 USD**(CNY→USD 换算,汇率 1 USD = 7.2 CNY)
2. **预算档**:**¥100/集 ≈ $14/集**(顶级)— 比 Phase 11 ship 时默认的 $0.10 高 **139 倍**

## 真实场景成本对比(60s 短剧)

| 场景 | 单次成本 | 11 步编排 | 单集成本 | 月 30 集 |
|------|---------|----------|---------|----------|
| **海外顶级**(veo3/kling) | $0.05-0.10 | 5-7 步 | $0.50-0.70 | $15-21 |
| **国内顶级**(kling_cn) | ¥1.0=$0.14 | 7 镜头 | ¥7≈$1 | ¥210≈$30 |
| **国内中等**(jimeng/cogvideox) | ¥0.3-0.5 | 7 镜头 | ¥2-3.5≈$0.3-0.5 | ¥90≈$13 |
| **海外免费**(edge_tts+piper+local_wan+fma) | $0 | 7 步 | **$0** | **$0** |
| **国内开源本地**(wan2.1) | $0(GPU电费) | 7 镜头 | $0(忽略GPU) | $0 |

**用户场景对应**:¥100/集 顶级档 ≈ 月 3 集 = 商业交付;¥10/集 ≈ 月 30 集 = 个人创作者。

## 增量 ship 内容

### 1. 货币换算常量(`video_provider_scoring.py`)
```python
CNY_TO_USD = 0.139   # 1 CNY ≈ 0.139 USD
USD_TO_CNY = 7.20    # 1 USD ≈ 7.2 CNY
USER_DEFAULT_BUDGET_USD = 14.00  # ¥100/集
```

### 2. 6 新国内 provider 注册
| Provider | 原价 | USD | tag | quality |
|----------|------|-----|-----|---------|
| **kling_cn**(可灵 1.5) | ¥1.0 | $0.139 | image2video | 0.90 |
| **jimeng**(即梦) | ¥0.5 | $0.07 | image2video | 0.85 |
| **vidu**(生数) | ¥0.5 | $0.07 | image2video | 0.82 |
| **cogvideox**(智谱开源) | ¥0.3 | $0.04 | image2video | 0.78 |
| **hailuo**(海螺) | ¥0.8 | $0.11 | image2video | 0.88 |
| **wan2.1_local**(阿里本地) | $0 | $0 | image2video | 0.75 |

### 3. 预算常量更新(`video_budget.py`)
```python
DEFAULT_BUDGET_PER_EPISODE = 14.00      # $14/集(¥100 顶级档)
DEFAULT_COST_PER_STEP_HARD_CAP = 2.00   # $2/步
DEFAULT_STEPS_PER_EPISODE = 7           # 60s 短剧典型步数
```

### 4. Bug 修复 — suggest_replacements 字段错位
- 之前用 `meta["cost"]`(0-1 评分)判定是否免费,误判 kling(0.3)为免费
- 现改用 `cost_per_call`(真实美元),veo3 $0.10 / kling $0.05 都能正确识别付费

## 关键文件

| 文件 | 改动 | 用途 |
|------|------|------|
| `prisIr_work/video_provider_scoring.py` | +60 行 | CNY 常量 + 6 国内 provider 注册 |
| `prisIr_work/video_budget.py` | +10 行 / 改预算常量 + bug 修 | $14/集 + cost_per_call 字段修复 |
| `tests/test_phase_11_om_p3_pre_compose.py` | +180 行 | 8 国内 CNY 测试 + 14 原测试更新到 $14 预算 |

## 测试矩阵

### 原 14 测试(更新到 $14 预算)
- #5 含 veo3 $0.10 ≪ $14 → 不过超集预算
- #6 kling 替代 → 现在含 wan2.1_local + local_wan
- #8 默认预算 = $14
- #9 / #12 / #11 用超 cap 测试 provider($5/$3)代替 veo3

### 8 Phase 11-H 新测试
- **#H1** 5 国内 provider 已注册 + cost_per_call 自动转 USD
- **#H2** kling_cn(¥1.0)→ USD $0.139
- **#H3** 默认预算 $14 / 单步 $2
- **#H4** 7 个 kling_cn → $0.97 < $14 ok(国内顶级短剧)
- **#H5** kling_cn 降级 → [wan2.1_local, local_wan]
- **#H6** 真实国内 11 步编排(jimeng+kling_cn+cogvideox+hailuo+piper+fma+edge_tts+assemble)→ $0.54 ≪ $14
- **#H7** 用户临时 budget=$4.17(¥30)→ 7 veo3 / 7 kling_cn 都 ok
- **#H8** workflow + 默认 $14, 无 provider 字段 → ok

## 累计测试 **211**

Phase 11 197 + Phase 11-H 14(原 14 已含) = 211
- Phase 9 OM-P1 + MA-P1: 14/14
- Phase 10 OM-P2: 13/13
- Phase 11 OM-P3 + 11-H: 22/22

## 关键洞察

1. **货币换算不是装饰** — 国内/海外 provider 同时跑必须统一币种,避免跑飞预算
2. **预算档分多档合理** — 个人创作者 ¥10/集 / 中小工作室 ¥30/集 / 商业交付 ¥100/集
3. **wan2.1_local 是关键免费替代** — 国内开源 + 本地推理,GPU 电费忽略 = $0
4. **国内顶级 vs 海外顶级 ≈ 1:1 价格** — kling_cn $0.139 ≈ veo3 $0.10,kling $0.05
5. **bug 修得及时** — suggest_replacements 字段错位会让所有 replace_plan 失效

## 风险登记

1. **汇率不可实时更新** — 1 USD = 7.2 CNY 写死;真实汇率波动 ±5% 需月度校准
2. **国内 provider 价格未实地验证** — kling_cn ¥1.0 / jimeng ¥0.5 / cogvideox ¥0.3 是估算;真实可能更高
3. **wan2.1_local 需硬件** — 24GB+ GPU 才能跑,普通机器只能跑 1.3B 小模型
4. **provider key 未实现** — 注册的 6 个国内 provider 当前是 placeholder,真实 API 调用留 OM-P4

## 后续

- **OM-P4**:免费资源真集成(wan2.1_local + jimeng + kling_cn 真 API 接入)
- **OM-P5**:实时计费(月聚合 + auto-degrade + 月预算上限)
- **预算档 UI**:用户可切换 ¥10 / ¥30 / ¥100 三档

**How to apply:**
- 用户问「国内跑短剧多少钱」→ 7 步 kling_cn ≈ ¥7 ≈ $1
- 用户问「怎么压到 $0」→ 全 wan2.1_local(需 GPU)+ piper + fma + edge_tts
- 用户问「预算够不够」→ `check_budget(steps, budget=14.0)` 默认 $14
- 用户问「怎么换预算」→ `run_workflow(budget=4.17)` 临时 ¥30
- 用户问「CNY 怎么转 USD」→ `cost_per_call = original × 0.139`(注册时已自动算)