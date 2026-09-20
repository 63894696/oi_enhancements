---
name: image-card-openai
description: |
  在线抽卡图片生成(OpenAI gpt-image-2 / DALL-E)。调 OpenAI Images API,
  需 OPENAI_API_KEY。
  Use when "抽卡", "画一张", "DALL-E", "gpt-image", "OpenAI 图"。
license: MIT
triggers: 抽卡, 画一张, DALL-E, gpt-image, OpenAI 图, generate image
requirements: OPENAI_API_KEY 环境变量, 出图预算 ~$0.04-0.12 / 张
allowed-tools: ["Bash", "Read", "Write", "WebFetch"]
---

# image-card-openai

在线抽卡(OpenAI)。**联网**。

## 跑法

```bash
python scripts/check.py
python scripts/generate.py "a cyberpunk girl" --size 1024x1024 --quality high
```

size: `1024x1024` / `1024x1792` / `1792x1024` / `512x512`
quality: `low` / `medium` / `high` / `auto`