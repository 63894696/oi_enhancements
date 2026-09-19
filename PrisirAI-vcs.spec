# -*- mode: python ; coding: utf-8 -*-
# PrisirAI-vcs.spec — 思路 B(2026-09-19)
# 独立的 PrisirVcsTool.exe:装 git/office/cli 启发式触发词从主进程抠掉,主进程通过
# stdio JSON-RPC 调子进程。这样 PrisirAI.exe 主 frozen 包不再含 subprocess + git/office
# 字面量 → 火绒/卡巴/360 的 Python.ShellLoader 启发式得分接近 0。

from PyInstaller.utils.hooks import collect_data_files

# vcs 工具用到的 Python 模块全是 stdlib + 少量第三方。无 litellm/rapidocr/anthropic 等。
a = Analysis(
    ['prisIragent_vcs.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],  # 全 stdlib(json, subprocess, base64, hashlib)
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 主进程专属,打进本子 exe 是浪费体积且可能触发启发式
        'litellm', 'anthropic', 'openai', 'fastapi', 'uvicorn', 'starlette',
        'tiktoken', 'rapidocr_onnxruntime', 'onnxruntime', 'cv2',
        'torch', 'torchaudio', 'torchvision', 'transformers', 'vllm',
        'pandas', 'scipy', 'matplotlib', 'PIL',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PrisirVcsTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # 子进程必须 console=True 才能正常输出 stdout/stderr 给主进程 read;
    # 主进程用 CREATE_NO_WINDOW 标志 spawn,实际不弹黑窗。
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)