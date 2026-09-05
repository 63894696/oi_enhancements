# -*- coding: utf-8 -*-
"""灵犀 Android 端分发清单生成器。

输入:  installer/_dist/android/<channel>/LingxiIME-Android-arm64-v8a-*.apk
输出:  installer/_dist/android/<channel>/manifest.json
       installer/_dist/android/<channel>/checksums.sha256

用法:  python installer/build_android_manifest.py [--channel <stable|beta>]
       默认扫两个通道目录, 各生成一份 manifest.json + checksums.sha256

VERSION.txt 必须在仓根; 该文件决定 versionName / versionCode / channel。
APK 文件名规则 (由 build.sh 写出):
    LingxiIME-Android-arm64-v8a-<PRIMARY_VERSION>-<RELEASE_CHANNEL>.apk

manifest.json 字段定义:
  - product:        "LingxiIME-Android"
  - channel:        stable / beta / dev
  - version_name:   完整版本字符串 (与 AndroidManifest versionName 一致)
  - version_code:   Android versionCode
  - build_date:     YYYY-MM-DD
  - git_commit:     提交哈希或 HEAD 占位
  - abi:            arm64-v8a
  - min_sdk / target_sdk: 从 AndroidManifest 取
  - package_name:   com.lingxi.ime
  - signature_keystore: build/debug.keystore (正式分发前需切 release)
  - files[]:        [{name, size_bytes, sha256}]
  - prerequisites:  文字描述
  - install_cmd:    adb install 命令样例
  - changelog[]:    简要变更日志(本端只列基线, 详细由 release 流程补)

校验逻辑:
  1) 对每个 channel dir: 找到唯一 .apk, 算 sha256 + size
  2) 与 AndroidManifest 的 versionName/versionCode 比对 (aapt2 dump badging)
  3) 写 manifest.json + checksums.sha256
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_ROOT = REPO_ROOT / "installer" / "_dist" / "android"
VERSION_FILE = REPO_ROOT / "VERSION.txt"
AAPT2 = Path(r"C:\Users\Administrator\AppData\Local\Android\Sdk\build-tools\37.0.0\aapt2.exe")
APK_NAME_TEMPLATE = "LingxiIME-Android-arm64-v8a-{version}-{channel}.apk"

CHANGELOG = {
    "1.0.0": [
        "首个 Android 端公开版",
        "  - 9 宫格 T9 中文/英文/技术词混合",
        "  - 模糊音 12 组可选 (声母 6 + 韵母 6)",
        "  - 词库面板 (我的词 + 系统词)",
        "  - 学过的词自动推第一页",
        "  - 灵/犀/灵犀品牌词系统词表置顶",
        "  - 关于/隐私/条款/反馈联系 底部链接",
    ],
}


def load_version() -> dict:
    """读 VERSION.txt 解析成 dict (key=value 形式)。"""
    out = {}
    if not VERSION_FILE.exists():
        raise SystemExit(f"VERSION.txt 缺失: {VERSION_FILE}")
    for line in VERSION_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    required = {"PRIMARY_VERSION", "PRIMARY_VERSION_CODE", "RELEASE_CHANNEL", "BUILD_DATE"}
    missing = required - set(out.keys())
    if missing:
        raise SystemExit(f"VERSION.txt 缺字段: {missing}")
    return out


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def aapt2_dump_badging(apk: Path) -> dict:
    """从 APK 抽 versionName / versionCode / sdkVersion / targetSdkVersion 做校验。
    返回 dict, 字段缺失返回 None。
    """
    if not AAPT2.exists():
        return {}
    try:
        out = subprocess.check_output(
            [str(AAPT2), "dump", "badging", str(apk)],
            stderr=subprocess.STDOUT, timeout=30,
        ).decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [WARN] aapt2 dump badging 失败: {e}")
        return {}
    info = {}
    m = re.search(r"package:\s+name='([^']+)'\s+versionCode='(\d+)'\s+versionName='([^']+)'", out)
    if m:
        info["package"] = m.group(1)
        info["version_code"] = int(m.group(2))
        info["version_name"] = m.group(3)
    m = re.search(r"sdkVersion:'(\d+)'", out)
    if m:
        info["min_sdk"] = int(m.group(1))
    m = re.search(r"targetSdkVersion:'(\d+)'", out)
    if m:
        info["target_sdk"] = int(m.group(1))
    return info


def build_manifest_for_channel(channel: str, ver: dict) -> Optional[Path]:
    """为一个 channel 生成 manifest.json + checksums.sha256。"""
    ch_dir = DIST_ROOT / channel
    if not ch_dir.exists():
        print(f"  [SKIP] {ch_dir} 不存在")
        return None
    apk_name = APK_NAME_TEMPLATE.format(version=ver["PRIMARY_VERSION"], channel=channel)
    apk = ch_dir / apk_name
    if not apk.exists():
        # 兼容 beta 用 "1.0.0-beta.1" 形式时, 文件名仍是 1.0.0-beta.1
        cands = list(ch_dir.glob("LingxiIME-Android-arm64-v8a-*.apk"))
        if not cands:
            print(f"  [SKIP] {ch_dir} 无 APK")
            return None
        apk = cands[0]
        apk_name = apk.name

    size = apk.stat().st_size
    sha = sha256_of(apk)

    badging = aapt2_dump_badging(apk)
    if badging:
        apk_vn = badging.get("version_name", "")
        apk_vc = badging.get("version_code", 0)
        if apk_vn and not apk_vn.startswith(ver["PRIMARY_VERSION"]):
            print(f"  [WARN] APK versionName='{apk_vn}' 与 VERSION.txt PRIMARY_VERSION='{ver['PRIMARY_VERSION']}' 不一致")
        if apk_vc and apk_vc != int(ver["PRIMARY_VERSION_CODE"]):
            print(f"  [WARN] APK versionCode={apk_vc} 与 VERSION.txt PRIMARY_VERSION_CODE={ver['PRIMARY_VERSION_CODE']} 不一致")

    changelog = CHANGELOG.get(ver["PRIMARY_VERSION"], [
        f"{ver['PRIMARY_VERSION']}: (无 changelog 条目, 手动补)"
    ])
    install_cmd = f"adb install -r {apk_name}"

    manifest = {
        "product": "LingxiIME-Android",
        "channel": channel,
        "version_name": f"{ver['PRIMARY_VERSION']} {channel}",
        "version_code": int(ver["PRIMARY_VERSION_CODE"]),
        "build_date": ver["BUILD_DATE"],
        "git_commit": ver.get("GIT_COMMIT", "HEAD"),
        "abi": "arm64-v8a",
        "min_sdk": badging.get("min_sdk", 28),
        "target_sdk": badging.get("target_sdk", 28),
        "package_name": badging.get("package", "com.lingxi.ime"),
        "signature_keystore": "build/debug.keystore (正式分发前换 release)",
        "files": [
            {
                "name": apk_name,
                "size_bytes": size,
                "sha256": sha,
            }
        ],
        "prerequisites": "Android 9.0+ (API 28+), arm64-v8a",
        "install_cmd": install_cmd,
        "changelog": changelog,
    }

    manifest_path = ch_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  [OK]  {manifest_path}")

    cksum = ch_dir / "checksums.sha256"
    lines = [f"{sha}  {apk_name}\n"]
    cksum.write_text("".join(lines), encoding="utf-8")
    print(f"  [OK]  {cksum}")
    return manifest_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", choices=["stable", "beta"], help="仅处理单通道, 默认两个都处理")
    args = ap.parse_args()

    ver = load_version()
    print(f"=== VERSION: {ver['PRIMARY_VERSION']} ({ver['RELEASE_CHANNEL']}) code={ver['PRIMARY_VERSION_CODE']} date={ver['BUILD_DATE']}")

    channels = [args.channel] if args.channel else ["stable", "beta"]
    written = []
    for ch in channels:
        p = build_manifest_for_channel(ch, ver)
        if p:
            written.append(p)
    print(f"=== DONE, 生成 {len(written)} 份 manifest ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())