# -*- coding: utf-8 -*-
"""video-card-svd-local — Stable Video Diffusion 本地推理。

用法:python generate.py <input.png> [--output PATH] [--frames N] [--fps N] [--model svd|svd-xt]
设 PRISIR_SVD_STUB=1 强制走 stub(本机无 GPU 或只想验落盘时)。
"""
import argparse, os, sys, time


def _svd_stub(args):
    """M3.33 #67 stub:不下载 SVD 模型,写 GIF 占位(mp4 需 ffmpeg,有时缺;GIF 用 Pillow 即可,浏览器 doc-panel 也能播)。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    if not args.output:
        # 默认改 GIF:doc-panel <img> 直接显示,且不依赖 ffmpeg
        args.output = os.path.join("generated", f"svd_{ts}.gif")
    try:
        from PIL import Image
    except ImportError:
        print("[FAIL] Pillow 未装:pip install Pillow", file=sys.stderr)
        sys.exit(5)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    n_frames = max(args.frames or 8, 8)
    h, ww = 256, 256
    frames = []
    for i in range(n_frames):
        v = (i * 255 // n_frames)
        img = Image.new("RGB", (ww, h), (v, v // 2, 255 - v))
        frames.append(img)
    duration_ms = int(1000 / (args.fps or 6))
    frames[0].save(args.output, save_all=True, append_images=frames[1:],
                   duration=duration_ms, loop=0)
    size = os.path.getsize(args.output)
    print(f"[ok-stub] saved → {args.output} ({n_frames} frames @ {args.fps}fps, {size} bytes) "
          f"(stub GIF — 不下载 SVD 模型;真生成需 GPU + unset PRISIR_SVD_STUB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--output", default="")
    ap.add_argument("--frames", type=int, default=25)
    ap.add_argument("--fps", type=int, default=6)
    ap.add_argument("--model", default="svd")
    args = ap.parse_args()
    if not os.path.isfile(args.image):
        print(f"[FAIL] 输入图不存在: {args.image}", file=sys.stderr)
        return 1
    try:
        import torch  # noqa: F401
    except ImportError:
        torch_ok = False
    else:
        torch_ok = True
    try:
        from diffusers import StableVideoDiffusionPipeline  # noqa: F401
        diffusers_ok = True
    except ImportError:
        diffusers_ok = False
    try:
        from PIL import Image
        pil_ok = True
    except ImportError:
        pil_ok = False

    if not (torch_ok and diffusers_ok and pil_ok):
        # M3.33 #67 stub:依赖缺失时写 2 秒黑帧 mp4 占位
        _svd_stub(args)
        return 0
    # 即使依赖齐了,也允许强制 stub(PRISIR_SVD_STUB=1)— 本机没 GPU/CUDA 时避免下 10GB 模型卡住
    if os.environ.get("PRISIR_SVD_STUB", "") in ("1", "true", "yes"):
        print("[info] PRISIR_SVD_STUB=1 → 走 stub(避免真模型下载)", file=sys.stderr)
        _svd_stub(args)
        return 0

    model_id = "stabilityai/stable-video-diffusion-img2vid" if args.model == "svd" \
        else "stabilityai/stable-video-diffusion-img2vid-xt"
    print(f"[info] loading {model_id}", file=sys.stderr)
    pipe = StableVideoDiffusionPipeline.from_pretrained(
        model_id, torch_dtype=torch.float16, variant="fp16"
    )
    pipe.enable_model_cpu_offload()
    print(f"[info] generating {args.frames} frames @ {args.fps}fps", file=sys.stderr)
    image = Image.open(args.image).convert("RGB").resize((1024, 576))
    frames = pipe(image, decode_chunk_size=8, num_frames=args.frames).frames[0]
    if not args.output:
        ts = time.strftime("%Y%m%d_%H%M%S")
        args.output = os.path.join("generated", f"svd_{ts}.mp4")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    try:
        import imageio
    except ImportError:
        print("[FAIL] imageio 未装:pip install imageio[ffmpeg]", file=sys.stderr)
        return 5
    imageio.mimsave(args.output, frames, fps=args.fps)
    print(f"[ok] saved → {args.output}  ({len(frames)} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())