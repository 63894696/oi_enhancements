---
name: image-card-replicate
description: |
  在线抽卡图片生成(Replicate API,统一 100+ 开源模型)。把用户消息里的"画 / 抽卡 / 生成图片"翻译成 Replicate prediction,
  落到 <workdir>/generated/。需 REPLICATE_API_TOKEN 环境变量。
  Use when user says "抽卡", "画一张", "生成图片", "Replicate", "Flux online", "SDXL online"。
  本 skill 是云端路线 — 离线不可用,但无需 GPU。
license: MIT
triggers: 抽卡, 画一张, 生成图片, Replicate, Flux online, SDXL online
requirements: REPLICATE_API_TOKEN 环境变量, 出图预算 $0.05-0.15 / 张
allowed-tools: ["Bash", "Read", "Write", "WebFetch"]
---

# image-card-replicate

在线抽卡(Replicate)。**联网 — 走云端 API**。

适合:用户没本地 GPU、又想要 Flux / SDXL 质量。

## 工作流

1. 解析 prompt
2. POST https://api.replicate.com/v1/predictions(model=owner/name, version pinned)
3. poll GET /predictions/{id} 直到 succeeded
4. download output[0] 写到 <workdir>/generated/

## 跑法

```bash
python scripts/check.py   # 验证 REPLICATE_API_TOKEN
python scripts/generate.py "a cyberpunk girl" --model black-forest-labs/flux-schnell --seed 42
```

## 模型选择(自动按 prompt 长度猜)

| 长度 | 默认模型 |
|---|---|
| < 50 字 | `black-forest-labs/flux-schnell`(快) |
| >= 50 字 | `black-forest-labs/flux-dev`(质量) |

用户 `--model` 可覆盖。