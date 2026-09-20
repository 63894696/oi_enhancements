---
name: image-card-comfyui-local
description: |
  本地抽卡图片生成(ComfyUI + SDXL/Flux)。把用户消息里的"画 / 抽卡 / 生成图片 / draw / generate image"翻译成
  ComfyUI workflow prompt,落到 <workdir>/generated/。
  Use when user says "抽卡", "画一张", "生成图片", "draw", "generate image", "出图", "SD", "SDXL", "Flux", "ComfyUI"。
  本 skill 是离线首选 — 需用户本机跑 ComfyUI(默认 127.0.0.1:8188)+ 12GB+ NVIDIA GPU。
license: MIT
triggers: 抽卡, 画一张, 生成图片, draw, generate image, 出图, ComfyUI, SDXL, Flux
requirements: COMFYUI_URL=http://127.0.0.1:8188, NVIDIA GPU 12GB+ (SDXL) 或 24GB+ (Flux)
allowed-tools: ["Bash", "Read", "Write"]
---

# image-card-comfyui-local

本地抽卡(ComfyUI)。**完全离线** — 跟用户已装的 ComfyUI + SDXL/Flux 模型交互,不联网。

## 工作流

1. 解析用户消息中的 prompt(中文 / 英文 / 混合)
2. 调 ComfyUI `/prompt` 端点(POST JSON workflow)
3. 等 WebSocket 通知完成,下载 `/view?filename=...` 输出图
4. 写到 `<workdir>/generated/<时间戳>.png`
5. 返路径给 agent,前端自动进 doc-panel 预览

## 调用方式

```bash
# 前置检查(VRAM / ComfyUI 是否就位)
python scripts/check.py

# 生成(单张)
python scripts/generate.py "a cyberpunk girl in rainy tokyo, neon" \
  --output generated/cyberpunk_001.png --seed 42 --width 1024 --height 1024
```

## 参数

- `prompt` — 必填,1-2000 字
- `--output` — 输出路径,默认 `generated/output_<时间戳>.png`
- `--seed` — 固定 seed 复现,默认随机
- `--width / --height` — 1024x1024 (SDXL) 或 512x512 (Flux schnell)
- `--workflow` — `sdxl.json`(默认) / `flux_dev.json` / `flux_schnell.json`(需用户先 Save (API Format) 到 `workflows/`)

## Workflow 模板

`workflows/sdxl.json` 是 "Save (API Format)" 导出的纯 API JSON(无 UI 坐标)。用户改了 workflow
直接覆盖同路径即可。**workflow 文件不进 skill 仓**(用户配置,可能几个 G)。

## 安全

- 不联网下载模型/资源
- ComfyUI 默认 `127.0.0.1`,外网暴露时用户自担
- 输出图写到 workdir 内,不写到 skill 仓
- 不动 ComfyUI profile(用 `-env:UserInstallation` 时由 ComfyUI 自管)

## 故障排查

| 现象 | 原因 | 修 |
|---|---|---|
| VRAM OOM | 模型太大 | 换 fp8 量化,或 `--lowvram` 启动 ComfyUI |
| text encoder mismatch | FLUX 配套 clip 没装 | 装 `comfyanonymous/flux_text_encoders` |
| 串行排队 | ComfyUI 单 GPU 限制 | 默认串行(同 skill 顺序跑) |
| soffice 慢 | LO 跟 ComfyUI 抢 GPU | 错峰跑 |