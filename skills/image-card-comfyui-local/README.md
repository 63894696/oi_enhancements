# image-card-comfyui-local

本地抽卡(ComfyUI + SDXL/Flux)。**完全离线**。

## 触发词

抽卡 / 画一张 / 生成图片 / draw / generate image / 出图 / ComfyUI / SDXL / Flux

## 依赖

- NVIDIA GPU 12GB+ (SDXL) 或 24GB+ (Flux)
- 用户本机 ComfyUI(默认 http://127.0.0.1:8188)+ 至少一个 workflow API 模板
- 环境变量 `COMFYUI_URL` 可选(默认 127.0.0.1:8188)

## 跑法

```bash
# 前置检查
python scripts/check.py

# 生成(单张,SDXL 默认 1024x1024)
python scripts/generate.py "a cyberpunk girl in rainy tokyo, neon" \
  --output generated/cyberpunk_001.png --seed 42

# 换 Flux workflow
python scripts/generate.py "..." --workflow flux_schnell --width 512 --height 512
```

## Workflow 模板

`workflows/<name>.json` — 用 ComfyUI UI 加载模型后 "Save (API Format)" 导出。
本期 ship 不带 workflow(用户配置,可能几个 G);skill 启动后 check.py 会提示路径。

## 输出

- 默认落到 `./generated/comfyui_<时间戳>.png`
- 文件树自动显示,doc-panel 自动 preview
- 走我们已有的 file_changes registry + doc-panel preview 路径