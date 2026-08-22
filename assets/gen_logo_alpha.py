# Prisir logo 透明化 + 双向描边保可见 (B方案)
# 母标 assets/prisir-mark-metal-v2.png (1024x1024 RGBA, 深墨蓝近黑底 alpha=255)
# 输出: prisir-logo-{16,32,48,128,256}.png + prisir-logo.ico (真透明 + 亮描边 + 柔影)
# 备份: prisir-logo-solid-* / prisir-logo-solid.ico (覆盖前的带底版)
# 复跑: python assets/gen_logo_alpha.py

import os
import shutil

import numpy as np
from PIL import Image, ImageFilter

ASSETS = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(ASSETS, "prisir-mark-metal-v2.png")

KEY_RGB = np.array([20.0, 23.0, 33.0])  # 背景估计色 (四角中值)
D1, D2 = 26.0, 52.0                      # 原规格: <D1 全透, D1~D2 羽化, >=D2 保留
FEATHER_CUTOFF = 0.30                    # 羽化带下限: 低于此直接归 0 (防尘雾)
FEATHER_CAP = 0.60                       # 羽化带上限: 暗尘羽化最多到此, 防白底灰斑
ERODE = 3                                # MinFilter 收边核
MIN_COMP = 48                            # 保留的最小连通域 (去孤立尘点)
PREMULT_LO, PREMULT_HI = 26.0, 120.0     # 底色预乘还原的 dist 映射区间
PREMULT_FLOOR = 0.35                     # 还原系数下限 (防过曝)
SIZES = [16, 32, 48, 128, 256]
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (256, 256)]

STROKE_COLOR = (232, 238, 242)   # #E8EEF2 亮描边
STROKE_ALPHA = 140               # ~55%: 白底下描边颜色贴近白底, 低不透明度防白圈
SHADOW_COLOR = (11, 14, 20)      # #0B0E14 柔影
SHADOW_ALPHA = 102               # ~40%
SHADOW_BLUR = 2

SYNC_48 = [
    os.path.join(os.path.dirname(ASSETS), "prisIr-browser", "content-cabinet", "prisir-logo-48.png"),
    os.path.join(os.path.dirname(ASSETS), "prisIr-browser", "agent", "assets", "prisir-logo-48.png"),
]


def load_master():
    img = Image.open(MASTER).convert("RGBA")
    assert img.size == (1024, 1024), f"master size unexpected: {img.size}"
    return img


# 按原规格羽化抠底 (D1~D2 渐变), 银河暗尘以 alpha 承载而非整片硬切,
# 再: 收边 + 去小连通域 + 底色预乘还原, 黑白双底都干净。
def keyout(master):
    """每像素独立判: 与键色欧氏色距 D1~D2 线性羽化, >=D2 全保留。
    后处理: 低羽化归 0 -> MinFilter 收边 -> 去小连通域 -> 底色预乘还原真色。"""
    from scipy import ndimage

    arr = np.asarray(master).astype(np.float32)
    rgb = arr[..., :3]
    dist = np.sqrt(((rgb - KEY_RGB) ** 2).sum(axis=-1))
    alpha = np.clip((dist - D1) / (D2 - D1), 0.0, 1.0)
    alpha[alpha < FEATHER_CUTOFF] = 0.0  # 砍低羽化残尘
    alpha = np.minimum(alpha, np.where(alpha < 1.0, FEATHER_CAP, 1.0))  # 羽化带上限压 0.6
    # 保留区里 dist 低且暗的像素 (银河暗尘, 颜色近底) 压到 0.35 半透, 防白底灰斑
    lum = rgb @ np.array([0.299, 0.587, 0.114])
    dim = (dist < 110.0) & (lum < 85.0)
    alpha = np.where(dim, np.minimum(alpha, 0.35), alpha)

    # 收边 + 去小连通域 (按 alpha>0 的支撑集判)
    a_img = Image.fromarray((alpha * 255).astype(np.uint8), "L").filter(
        ImageFilter.MinFilter(ERODE)
    )
    alpha = np.asarray(a_img, dtype=np.float32) / 255.0
    mask = alpha > 0
    lab, n = ndimage.label(mask)
    if n:
        sizes = ndimage.sum(mask, lab, range(1, n + 1))
        kill = np.zeros(n + 1, dtype=bool)
        kill[1:][sizes < MIN_COMP] = True
        alpha[kill[lab]] = 0.0

    out = arr.copy()
    out[..., 3] = alpha * 255.0
    # 底色预乘: 母标暗像素是 主体色*e + 键色*(1-e), e≈(dist-26)/120;
    # 保留区按 dist 估 e 还原主体真色, 否则换底后边缘发黑。
    premult = np.clip((dist - PREMULT_LO) / (PREMULT_HI - PREMULT_LO),
                      PREMULT_FLOOR, 1.0)[..., None]
    out[..., :3] = np.clip(rgb / premult, 0, 255)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def build_layered(keyed):
    """柔影(最底) -> 亮描边 -> 主体(最上)。"""
    size = keyed.size
    alpha = keyed.split()[3]

    # 亮描边: alpha 膨胀 2px 得外轮廓, 只保留外扩环带
    dilated = alpha.filter(ImageFilter.MaxFilter(5))  # 半径2
    ring = np.clip(
        np.asarray(dilated, dtype=np.int32) - np.asarray(alpha, dtype=np.int32),
        0, 255,
    ).astype(np.uint8)
    ring_alpha = Image.fromarray(ring, "L").point(
        lambda v: v * STROKE_ALPHA // 255
    )
    stroke = Image.new("RGBA", size, STROKE_COLOR + (0,))
    stroke.putalpha(ring_alpha)

    # 柔影: 膨胀后的轮廓外扩 + 高斯模糊
    shadow_mask = dilated.filter(ImageFilter.MaxFilter(5)).filter(
        ImageFilter.GaussianBlur(SHADOW_BLUR)
    )
    shadow_mask = shadow_mask.point(lambda v: v * SHADOW_ALPHA // 255)
    shadow = Image.new("RGBA", size, SHADOW_COLOR + (0,))
    shadow.putalpha(shadow_mask)

    out = Image.alpha_composite(shadow, stroke)
    out = Image.alpha_composite(out, keyed)
    return out


def main():
    master = load_master()
    keyed = keyout(master)
    final = build_layered(keyed)

    # 备份原带底版 (只在目标存在且备份不存在时备份, 保证复跑安全)
    for s in SIZES:
        src = os.path.join(ASSETS, f"prisir-logo-{s}.png")
        dst = os.path.join(ASSETS, f"prisir-logo-solid-{s}.png")
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
    ico_src = os.path.join(ASSETS, "prisir-logo.ico")
    ico_dst = os.path.join(ASSETS, "prisir-logo-solid.ico")
    if os.path.exists(ico_src) and not os.path.exists(ico_dst):
        shutil.copy2(ico_src, ico_dst)

    # 五尺寸 (降采样会凭空造出半透像素, 输出前量化到 0/96/255 防灰雾)
    imgs = {}
    for s in SIZES:
        im = final.resize((s, s), Image.LANCZOS)
        arr = np.asarray(im).copy()
        a = arr[..., 3].astype(np.int32)
        arr[..., 3] = np.where(a < 24, 0, np.where(a < 160, 96, 255)).astype(np.uint8)
        im = Image.fromarray(arr, "RGBA")
        p = os.path.join(ASSETS, f"prisir-logo-{s}.png")
        im.save(p)
        imgs[s] = im
        print("wrote", p)

    # 同步 48 到两处引用
    for dst in SYNC_48:
        if os.path.isdir(os.path.dirname(dst)):
            shutil.copy2(os.path.join(ASSETS, "prisir-logo-48.png"), dst)
            print("synced", dst)
        else:
            print("SKIP (dir missing):", dst)

    # ICO
    ico_path = os.path.join(ASSETS, "prisir-logo.ico")
    imgs[256].save(ico_path, format="ICO", sizes=ICO_SIZES)
    print("wrote", ico_path, ICO_SIZES)

    # 预览: 黑底/白底各一张 (256)
    for name, color in (("_preview_on_black.png", (0, 0, 0, 255)),
                        ("_preview_on_white.png", (255, 255, 255, 255))):
        bg = Image.new("RGBA", (256, 256), color)
        bg.alpha_composite(imgs[256])
        bg.save(os.path.join(ASSETS, name))
        print("wrote", os.path.join(ASSETS, name))

    # alpha 校验
    for s in SIZES:
        a = np.asarray(imgs[s].split()[3])
        corners = [a[0, 0], a[0, -1], a[-1, 0], a[-1, -1]]
        center = a[a.shape[0] // 2, a.shape[1] // 2]
        print(f"size {s}: corner alpha={corners} center alpha={center}")


if __name__ == "__main__":
    main()
