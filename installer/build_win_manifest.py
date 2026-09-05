# -*- coding: utf-8 -*-
"""灵犀 Windows 端分发清单生成器。

对齐 installer/build_android_manifest.py 的结构(2026-08-29 决策):
- 输入:  installer/_dist/windows/<channel>/LingxiIME-Windows-x64-*.zip
         installer/_dist/windows/<channel>/<basename>/  (未压缩目录)
- 输出:  installer/_dist/windows/<channel>/manifest.json
         installer/_dist/windows/<channel>/checksums.sha256

VERSION.txt 在 prisIr_ime_tsf/VERSION.txt(Win 端自己的, 与 Android 端 VERSION.txt 同字段)。
产物文件名规则(由 build_win.sh 写出):
    LingxiIME-Windows-x64-<PRIMARY_VERSION>-<RELEASE_CHANNEL>.zip

Win 端没有 versionCode (Windows MSI 用 ProductVersion 4 段整数,
本脚本用 PRIMARY_VERSION 直接拼成 "MAJOR.MINOR.PATCH.0",作为参考)。

manifest.json 字段(对齐 Android 端, 但适配 Win 端的 deliverable 列表):
  - product:        "LingxiIME-Windows"
  - channel:        stable / beta / dev
  - version_name:   完整版本字符串
  - version_full:   MSI 风格 ProductVersion "MAJOR.MINOR.PATCH.0"
  - build_date:     YYYY-MM-DD
  - git_commit:     HEAD 或占位
  - arch:           x64
  - min_os:         "Windows 10 1809+ (T10 用了 WTSRegisterSessionNotification + hidden HWND)"
  - runtimes:       [".NET 不需要", "VC++ Runtime 2015+ (含在 dll 里)", "T10 需要 WTS API"]
  - files[]:        [{name, size_bytes, sha256}]
  - prerequisites:  文字描述
  - install_cmd:    copy + --register 步骤样例
  - changelog[]:    简要变更日志

校验逻辑:
  1) 对每个 channel dir: 找唯一 .zip 算 sha256 + size
  2) 同时检查未压缩目录里的版本号文件是否一致
  3) 写 manifest.json + checksums.sha256
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_ROOT = REPO_ROOT / "installer" / "_dist" / "windows"
WIN_VERSION_FILE = REPO_ROOT / "prisIr_ime_tsf" / "VERSION.txt"
ANDROID_VERSION_FILE = REPO_ROOT / "VERSION.txt"
ZIP_NAME_TEMPLATE = "LingxiIME-Windows-x64-{version}-{channel}.zip"

CHANGELOG = {
    "1.0.0-beta.1": [
        "首个跨平台公开版本(Win 端独立基线, 与 Android 端同 VERSION.txt 字段)",
        "  - prisir_tsfsvc.exe 控制壳 (--version 读 VERSION.txt + 显示 4 段关于链接)",
        "  - prisir_ime_tsf.dll TSF COM 实现 (ITfTextInputProcessor + ITextStoreACP)",
        "  - prisir_ime.dll 拼音查询引擎 (FFI 给 TSF DLL 调)",
        "  - --about <about|privacy|terms|contact> 子命令 (对齐 Android 端 4 段正文)",
        "  - daemon: WTSRegisterSessionNotification + hidden HWND + 真消息循环 (T10)",
        "  - DLL mtime 热重载 (T5)",
        "  - stdio JSON-RPC ipc server (T6, 7 methods)",
        "  - 拼音/五笔切换 (T7)",
        "  - HKCU CTF TIP + COM InprocServer32 注册 (per-user, no admin)",
        "  - 关于/隐私/使用条款/反馈联系 (ABOUT.md)",
    ],
}


def load_win_version() -> dict:
    """读 prisIr_ime_tsf/VERSION.txt 解析成 dict。

    字段对齐 Android 端, 但没有 PRIMARY_VERSION_CODE (Win 端无 versionCode)。
    """
    out = {}
    if not WIN_VERSION_FILE.exists():
        raise SystemExit(f"VERSION.txt 缺失: {WIN_VERSION_FILE}")
    for line in WIN_VERSION_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    required = {"PRIMARY_VERSION", "RELEASE_CHANNEL", "BUILD_DATE"}
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


def msi_product_version(semver: str) -> str:
    """MAJOR.MINOR.PATCH[-PRERELEASE] -> MAJOR.MINOR.PATCH.0 (MSI 4 段整数风格)。"""
    base = re.split(r"[+-]", semver, 1)[0]   # 1.0.0-beta.1 -> 1.0.0
    parts = base.split(".")
    while len(parts) < 3:
        parts.append("0")
    return ".".join(parts[:3]) + ".0"


def build_manifest_for_channel(channel: str, ver: dict):
    """为一个 channel 生成 manifest.json + checksums.sha256。"""
    ch_dir = DIST_ROOT / channel
    if not ch_dir.exists():
        print(f"  [SKIP] {ch_dir} 不存在")
        return None

    zip_name = ZIP_NAME_TEMPLATE.format(version=ver["PRIMARY_VERSION"], channel=channel)
    zip_path = ch_dir / zip_name
    if not zip_path.exists():
        cands = sorted(ch_dir.glob("LingxiIME-Windows-x64-*.zip"))
        if not cands:
            print(f"  [SKIP] {ch_dir} 无 zip")
            return None
        zip_path = cands[0]
        zip_name = zip_path.name

    size = zip_path.stat().st_size
    sha = sha256_of(zip_path)

    # 同时检查未压缩目录里的 VERSION.txt 是否一致 (双源兜底)
    unzipped = ch_dir / zip_name.removesuffix(".zip")
    inner_version = "(no unzipped dir)"
    if unzipped.exists() and (unzipped / "VERSION.txt").exists():
        for line in (unzipped / "VERSION.txt").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PRIMARY_VERSION="):
                inner_version = line.split("=", 1)[1].strip()
                break
        if inner_version != ver["PRIMARY_VERSION"]:
            print(f"  [WARN] unzipped VERSION.txt PRIMARY_VERSION={inner_version} 与 prisIr_ime_tsf/VERSION.txt PRIMARY_VERSION={ver['PRIMARY_VERSION']} 不一致")

    # 安装命令样例(对齐 INSTALL.md, 简化版)
    install_cmd = (
        "1) 解压 zip 到任意目录\n"
        "2) xcopy /E /I /Y <解压目录>\\* \"C:\\Program Files\\PrisirIME\\\"   (含 models/ 子目录, 词库 376MB)\n"
        "3) cd C:\\Program Files\\PrisirIME && prisir_tsfsvc.exe --register\n"
        "4) taskkill /F /IM explorer.exe && start explorer.exe\n"
        "5) Win+Space 切到「Prisir 输入法」"
    )

    changelog = CHANGELOG.get(ver["PRIMARY_VERSION"], [
        f"{ver['PRIMARY_VERSION']}: (无 changelog 条目, 手动补)"
    ])

    manifest = {
        "product": "LingxiIME-Windows",
        "channel": channel,
        "version_name": f"{ver['PRIMARY_VERSION']} {channel}",
        "version_full": msi_product_version(ver["PRIMARY_VERSION"]),
        "build_date": ver["BUILD_DATE"],
        "git_commit": ver.get("GIT_COMMIT", "HEAD"),
        "arch": "x64",
        "min_os": "Windows 10 1809+ (T10 用了 WTSRegisterSessionNotification + hidden HWND)",
        "runtimes": [
            ".NET Runtime: 不需要 (Rust 静态链接)",
            "VC++ Runtime 2015+ (含在 dll 里,无需用户安装)",
            "Windows CTF (CTFMon / msctf.dll, Win10 1809+ 自带)",
        ],
        "files": [
            {
                "name": zip_name,
                "size_bytes": size,
                "sha256": sha,
            }
        ],
        "prerequisites": "Windows 10 1809+ x64, .NET 不需要, 管理员权限 (写 Program Files), HKCU CTF 注册无需管理员",
        "install_cmd": install_cmd,
        "feedback_email": "lsjdlijie@outlook.com",
        "about_links": [
            {"key": "about",    "title": "关于",     "cmd": "prisir_tsfsvc --about about"},
            {"key": "privacy",  "title": "隐私说明", "cmd": "prisir_tsfsvc --about privacy"},
            {"key": "terms",    "title": "使用条款", "cmd": "prisir_tsfsvc --about terms"},
            {"key": "contact",  "title": "反馈联系", "cmd": "prisir_tsfsvc --about contact"},
        ],
        "changelog": changelog,
    }

    manifest_path = ch_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  [OK]  {manifest_path}")

    cksum = ch_dir / "checksums.sha256"
    cksum.write_text(f"{sha}  {zip_name}\n", encoding="utf-8")
    print(f"  [OK]  {cksum}")
    return manifest_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", choices=["stable", "beta", "dev"], help="仅处理单通道, 默认三个都处理")
    args = ap.parse_args()

    ver = load_win_version()
    print(f"=== VERSION: {ver['PRIMARY_VERSION']} ({ver['RELEASE_CHANNEL']}) date={ver['BUILD_DATE']}")

    channels = [args.channel] if args.channel else ["stable", "beta", "dev"]
    written = []
    for ch in channels:
        p = build_manifest_for_channel(ch, ver)
        if p:
            written.append(p)
    print(f"=== DONE, 生成 {len(written)} 份 manifest ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())