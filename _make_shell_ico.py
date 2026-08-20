# -*- coding: utf-8 -*-
"""把选定的 dialog_flame 对话壳图标切成六档 ICO(禁拉伸,逐档 LANCZOS)。

源:_agnes_icons/shell_dialog_flame.png(1024x1024,母版风格已是圆角徽章铺满)
出:oiagent-shell/icon.ico(16/24/32/48/64/256)+ icon.png(256 供 Electron 用)
"""
from PIL import Image
import os

SRC = r"C:\Users\Administrator\oi_enhancements\_agnes_icons\shell_dialog_flame.png"
SHELL_DIR = r"C:\Users\Administrator\oi_enhancements\oiagent-shell"
ICO = os.path.join(SHELL_DIR, "icon.ico")
PNG = os.path.join(SHELL_DIR, "icon.png")

img = Image.open(SRC).convert("RGBA")
# 母版图已是圆角徽章铺满画面,直接等比缩(不需要裁白边)。居中方形保证。
w, h = img.size
side = min(w, h)
img = img.crop(((w - side)//2, (h - side)//2, (w + side)//2, (h + side)//2))

sizes = [16, 24, 32, 48, 64, 256]
imgs = [img.resize((s, s), Image.LANCZOS) for s in sizes]
imgs[-1].save(ICO, format="ICO", sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
img.resize((256, 256), Image.LANCZOS).save(PNG)
print("ICO:", ICO, os.path.getsize(ICO), "bytes, 档位:", sizes)
print("PNG:", PNG, os.path.getsize(PNG), "bytes")
