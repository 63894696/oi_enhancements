import os, sys


def main():
    msgs = []
    try:
        import torch  # noqa: F401
        msgs.append(("ok", "torch 已装"))
    except ImportError:
        msgs.append(("fail", "torch 未装"))
        return _emit(msgs)
    try:
        from diffusers import StableVideoDiffusionPipeline  # noqa: F401
        msgs.append(("ok", "diffusers 已装"))
    except ImportError:
        msgs.append(("fail", "diffusers 未装"))
    try:
        from PIL import Image  # noqa: F401
        msgs.append(("ok", "PIL 已装"))
    except ImportError:
        msgs.append(("fail", "pillow 未装"))
    try:
        import imageio  # noqa: F401
        msgs.append(("ok", "imageio 已装"))
    except ImportError:
        msgs.append(("warn", "imageio 未装(导出 mp4 时会失败)"))
    fail = False
    for level, msg in msgs:
        print(f"[{level}] {msg}")
        if level == "fail":
            fail = True
    sys.exit(1 if fail else 0)


def _emit(msgs):
    for level, msg in msgs:
        print(f"[{level}] {msg}")
    sys.exit(1)


if __name__ == "__main__":
    main()