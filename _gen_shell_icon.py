# -*- coding: utf-8 -*-
"""用 agnesai 端点按 Prisir 铜光金属圆环视觉规范生成 prisiragent-shell(F7)对话壳图标候选(2026-08-20)

视觉母版(与灵犀语音/拼音/五笔同一母版,见 voice_input/gen_lingxi_icon.py、
gen_pinyin_icon.py 与 docs/visual-identity-proposal §3.1):
  深墨蓝黑底(#0f1a24) + 铜光金属圆环(copper #c98a4b)带辉光闪现
  + 古铜立体图形 + 萤火粒子,宗教徽章质感。
对话壳主体换成「对话/聊天」元素(对话气泡/灵机星火),与输入法同一视觉语言。
"""
from __future__ import annotations
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1").rstrip("/")
API_KEY = os.environ.get("AGNES_API_KEY", "")
OUT = Path(r"C:\Users\Administrator\oi_enhancements\_agnes_icons")
OUT.mkdir(exist_ok=True)

# 视觉规范(与语音/拼音/五笔同一母版)
STYLE = (
    "circular app icon, deep ink blue-black background (#0f1a24), "
    "a glowing copper metallic ring around the edge (copper #c98a4b) with radiant light flares and sparkling glints, "
    "bronze/copper metallic 3D emblem in the center, "
    "subtle glowing teal accent (#2f8f83), small luminous particles orbiting like fireflies, "
    "rich embossed religious-medallion quality, premium metallic sheen, high detail, square 1:1 icon"
)

# 对话壳主体候选(突出「对话/AI 灵机」,与输入法文字/键盘区分但同一视觉语言)
SUBJECTS = {
    "chat_compass": "a copper drafting compass fused with a glowing chat speech bubble as the central emblem, a bright north star spark at the pivot",
    "dialog_flame": "an embossed copper speech-bubble medallion with a luminous teal flame of insight rising inside as the central emblem",
    "orb_dialog": "a copper orrery ring embracing a glowing teal dialog orb with orbiting spark particles as the central emblem",
    "mirror_chat": "two facing embossed copper speech bubbles forming a circular yin-yang-like dialogue as the central emblem",
}


def gen(name, subject):
    prompt = f"{STYLE}, {SUBJECTS[subject]}, centered composition"
    body = {
        "model": "agnes-image-2.1-flash",
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
    }
    req = urllib.request.Request(
        f"{BASE_URL}/images/generations",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"[{name}] HTTP {e.code}: {e.read().decode('utf-8')[:300]}")
        return None
    except Exception as e:
        print(f"[{name}] 请求失败: {e}")
        return None

    item = (data.get("data") or [{}])[0]
    img_bytes = None
    if item.get("b64_json"):
        img_bytes = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=60) as ir:
            img_bytes = ir.read()
    if not img_bytes:
        print(f"[{name}] 无图像数据: {str(data)[:300]}")
        return None

    path = OUT / f"shell_{name}.png"
    path.write_bytes(img_bytes)
    print(f"[{name}] OK {path.name} ({len(img_bytes)//1024}KB)")
    return path


if not API_KEY:
    print("ERROR: AGNES_API_KEY 未设置", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    only = sys.argv[1:] or list(SUBJECTS)
    for subj in only:
        gen(subj, subj)
    print("完成,候选在 _agnes_icons/shell_*.png")
