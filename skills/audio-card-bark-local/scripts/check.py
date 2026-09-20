import os, sys, shutil


def main():
    msgs = []
    try:
        import bark  # noqa: F401
        msgs.append(("ok", "bark 已装"))
    except ImportError:
        msgs.append(("fail", "bark 未装:pip install git+https://github.com/suno-ai/bark.git"))
    try:
        import scipy.io.wavfile  # noqa: F401
        msgs.append(("ok", "scipy 已装"))
    except ImportError:
        msgs.append(("fail", "scipy 未装"))
    if shutil.which("nvidia-smi"):
        msgs.append(("ok", "nvidia-smi 存在(可查 GPU)"))
    else:
        msgs.append(("info", "nvidia-smi 不在 PATH"))
    fail = False
    for level, msg in msgs:
        print(f"[{level}] {msg}")
        if level == "fail":
            fail = True
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()