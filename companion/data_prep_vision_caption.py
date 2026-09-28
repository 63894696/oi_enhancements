#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_prep_vision_caption.py — M3.81 视觉 captioner 4 分类训练集准备(2026-09-25)

目的:
  - 为 vision_query race_impl 准备 4 分类数据集:
    app     — 桌面应用窗口(浏览器、文件管理器、IDE 整体框架)
    focused — 当前焦点/输入(终端命令、搜索框、文本输入)
    code    — 源代码/技术文本内容(编程、脚本、配置)
    dialog  — 模态对话框、alert、popup、系统消息

  - 输入:屏幕截图描述(由 qwen-vl-max 产生 ~50-200 字文本)
  - 标注:规则模板生成 100+ 描述/类 + laya zero-shot 校核 + 手工修正

约束:
  - 不存图像(图像太大)
  - 存 description 文本 + laya 4 维 answers (confidence 跟踪)
  - 训练 laya captioner head 不实际可行(laya 是冻结 ModernBERT),
    但 laya zero-shot 本身已能输出 4 类概率 → 直接当 captioner
  - 数据集用作:ACC 评测 + 与 qwen-vl-max race 对照

用法:
  python data_prep_vision_caption.py --output data_vision_caption.jsonl [--limit 1500]

4 类规则(关键词 + 上下文):
  app     — 标题栏、菜单栏、工具栏、tab、URL 地址栏、文件树、侧边栏
  focused — 光标闪烁、输入框、命令行 prompt、搜索框高亮
  code    — 关键字(def/class/import/function/<script/>)、行号、语法高亮
  dialog  — 确定/取消、alert、popup、确认窗口、tooltip、通知中心
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

# ------------------------------------------------------------
# 4 类规则模板
# ------------------------------------------------------------

# 每类 20+ 模板描述(中文 + 英文混合,模拟 qwen-vl-max 输出)

APP_TEMPLATES = [
    "屏幕上显示 Google Chrome 浏览器窗口,顶部有标签页、地址栏和书签栏",
    "用户打开了 VS Code 编辑器,左侧是文件资源管理器面板,中间是工作区",
    "桌面上有 Outlook 邮件客户端,显示收件箱列表,右侧是邮件预览",
    "屏幕上显示 Windows 文件资源管理器,左侧导航栏显示文件夹树",
    "用户打开了 Spotify 音乐应用,显示专辑封面和播放列表",
    "屏幕上显示 Slack 桌面客户端,左侧是频道列表,中间是消息流",
    "Chrome 浏览器打开多个标签页: GitHub、Stack Overflow 和本地开发文档",
    "显示 Visual Studio IDE,包含 Solution Explorer 和 Properties 窗口",
    "桌面显示 Adobe Photoshop,左侧是工具栏,中间是画布",
    "屏幕上显示微信桌面版,左侧是聊天列表,右侧是会话窗口",
    "用户打开了 Finder 浏览 macOS 文件系统,显示多个文件夹图标",
    "Chrome 浏览器显示 Stack Overflow 主页,顶部是导航栏",
    "打开了 Telegram 桌面客户端,显示频道列表和消息预览",
    "屏幕上显示 PowerPoint,左侧是幻灯片缩略图,主区域是当前幻灯片",
    "显示 Word 文档,顶部有 Ribbon 工具栏,显示段落样式和字体选项",
    "用户打开了 7-Zip 文件管理器,显示压缩包内容列表",
    "屏幕上显示 Discord 应用,左侧是服务器列表,中间是频道消息",
    "Edge 浏览器打开 Microsoft 365 主页,显示应用图标网格",
    "显示 Terminal 应用的偏好设置窗口(不是命令本身,是设置 UI)",
    "屏幕上显示 IntelliJ IDEA 主界面,左侧 Project 面板,顶部菜单栏完整",
    "用户打开了 Thunderbird 邮件客户端,显示邮件文件夹树和邮件列表",
    "Chrome 浏览器打开了多个 GitHub 仓库的标签页",
    "显示 macOS 系统偏好设置窗口,多个分类图标排列",
    "用户打开了 Adobe Illustrator,工具栏在左侧,主画布在中央",
    "屏幕上显示 1Password 桌面应用,显示密码库列表",
]

FOCUSED_TEMPLATES = [
    "屏幕上显示一个搜索框,光标在闪烁,用户正在输入'companion laya'",
    "终端窗口里有一个 bash prompt $ 等待输入命令",
    "Chrome 地址栏被选中高亮,用户正在输入 URL",
    "VS Code 命令面板被打开(Ctrl+Shift+P),显示命令列表等待选择",
    "Slack 消息输入框被选中,光标在文本末尾闪烁",
    "屏幕上显示 Gmail 撰写邮件窗口,正文区域光标闪烁等待输入",
    "Terminal 显示 zsh prompt,等待用户输入命令",
    "VS Code 中文件名是 main.py,光标在第 42 行第 8 列位置",
    "浏览器打开了 Google 搜索框,正在输入搜索关键词",
    "Slack 中用户点击了 reply,文本框获得焦点显示光标",
    "Windows 搜索栏被打开,显示最近搜索和应用建议",
    "屏幕上显示 VS Code 终端面板,prompt 等待输入 git commit 命令",
    "PyCharm 中 Run Configuration 下拉框被展开,等待用户选择",
    "Outlook 中搜索邮件框被选中,显示输入光标",
    "终端显示 $ python main.py < 等待 stdin 输入",
    "Chrome 打开了 Notion,光标停留在新建页面标题上等待输入",
    "VS Code 中 Quick Open 文件选择框打开,显示文件列表等待键盘输入",
    "Windows 11 开始菜单被打开,搜索框获得焦点显示光标",
    "屏幕上显示 PowerShell prompt PS C:\\Users\\admin>,等待命令",
    "Slack 中 thread reply 框被点击,光标在空白输入区等待文本",
    "VS Code 用户按了 Ctrl+F 显示查找框,光标在搜索输入区",
    "浏览器打开了 GitHub 新建 issue 页面,标题输入框等待输入",
    "PyCharm 调试控制台显示 > prompt,等待 Python REPL 输入",
    "屏幕上显示 Windows 运行对话框(R + 输命令),光标在输入框",
    "终端 ssh 连接后显示 user@server:~$ 等待输入",
]

CODE_TEMPLATES = [
    "屏幕上显示 Python 代码,函数 def classify_log(content: str): 然后缩进的代码体",
    "VS Code 显示 JavaScript 文件,包含 const express = require('express') 和路由代码",
    "代码片段: SELECT * FROM users WHERE id = ? LIMIT 1,SQL 高亮显示",
    "屏幕上显示 Rust 代码,fn main() -> Result<(), Error> { } 函数体",
    "显示 TypeScript 代码,interface User { id: string; name: string } 类型定义",
    "VS Code 显示 Go 代码,func HandleRequest(w http.ResponseWriter, r *http.Request) {",
    "屏幕上显示 HTML 模板,<div class='container'> 标签包裹内容",
    "显示 Bash 脚本,#! /bin/bash 开头,后面是 if [ -f file ]; then ... fi",
    "代码片段: import asyncio, async def fetch(url): await aiohttp.get(url)",
    "屏幕上显示 CSS 样式,.container { display: flex; justify-content: center }",
    "VS Code 显示 Dockerfile,FROM python:3.12, COPY . /app, RUN pip install",
    "显示 JSON 配置文件,{\"name\": \"companion\", \"version\": \"0.6.0\", ...}",
    "屏幕上显示 YAML 配置,database: host: localhost, port: 5432",
    "代码片段: <script> const data = await fetch('/api'); const json = await data.json(); </script>",
    "VS Code 显示 C++ 代码,#include <iostream>, int main() { std::cout << \"hello\"; }",
    "显示 Markdown 文档,# Title, ## Section, 然后 - bullet point 列表",
    "屏幕上显示 Lua 代码,local function greet(name) return 'Hello ' .. name end",
    "VS Code 显示 PHP 代码,<?php function sum(int $a, int $b): int { return $a + $b; }",
    "显示 shell 命令链,grep -r 'TODO' src/ | head -20 | xargs -I {} sed -i 's/TODO/DONE/g' {}",
    "代码片段: import { useState, useEffect } from 'react'; const App = () => { ... }",
    "屏幕上显示 SQL DDL,CREATE TABLE users (id INT PRIMARY KEY, email VARCHAR(255))",
    "VS Code 显示 Python dataclass,@dataclass class User: id: int name: str",
    "屏幕上显示 TOML 配置,[tool.poetry], name = \"package\", version = \"0.1.0\"",
    "代码片段: regex pattern /^[a-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}$/ 用于 email 验证",
    "VS Code 显示 Java 代码,public static void main(String[] args) { System.out.println(\"hi\"); }",
]

DIALOG_TEMPLATES = [
    "屏幕上弹出一个确认对话框,标题是 'Delete file?',有 OK 和 Cancel 两个按钮",
    "Chrome 浏览器显示 'This site can't be reached' 错误页,带错误代码 ERR_CONNECTION_REFUSED",
    "Windows Defender 显示安全警告弹窗,'Threat detected: Trojan.Win32.Generic'",
    "VS Code 显示更新提示弹窗,'A new version is available. Download now?'",
    "屏幕上显示 'Save changes?' 对话框,三个按钮 Save/Don't Save/Cancel",
    "Outlook 显示 'Do you want to allow this website to access your calendar?' 弹窗",
    "Chrome 显示 'Allow notifications from this site?' 询问框",
    "屏幕上显示 Git 合并冲突对话框,'Conflict detected. Choose: Use mine / Use theirs'",
    "VS Code 显示扩展推荐对话框,'Install recommended extension Python?'",
    "Windows 显示 'App needs your permission to access photos' UAC 弹窗",
    "屏幕上显示 PowerPoint 'Do you want to recover unsaved changes?' 弹窗",
    "PyCharm 显示 'New Python interpreter detected. Configure?' 弹窗",
    "Chrome 弹窗 'This file is dangerous. Keep / Discard?'",
    "Windows 防火墙弹窗 'Windows Defender Firewall has blocked some features'",
    "VS Code 显示 'Do you trust the authors of the files in this folder?' 信任对话框",
    "屏幕上显示 Slack 通知 toast 浮窗,'John mentioned you in #general'",
    "Adobe 显示 'Save as PDF?' 导出选项对话框",
    "Windows 显示 'You have apps open. Restart anyway?' 重启确认",
    "Chrome 提示 'Password compromised. Change password?' 安全建议弹窗",
    "VS Code 显示 'There is an available update. Restart to update?' 提示",
    "屏幕底部显示系统通知 'Download complete: file.zip'",
    "Slack 显示 'Mark all as read?' 确认对话框",
    "VS Code 显示 'Workspace trust enabled' 横幅(banner)",
    "Chrome 显示 'Disable extensions in incognito mode?' 询问弹窗",
    "屏幕上显示 macOS 'App is from an unidentified developer' 警告",
]

# 每类 ≥ 25 个模板,目标 ~250 条 → 加 laya 校核后 ~200-250 条

# ------------------------------------------------------------
# 标注生成(规则:模板 + 少量合成扰动)
# ------------------------------------------------------------

ALL_TEMPLATES = {
    "app": APP_TEMPLATES,
    "focused": FOCUSED_TEMPLATES,
    "code": CODE_TEMPLATES,
    "dialog": DIALOG_TEMPLATES,
}


def _generate_descriptions(rng: random.Random) -> list[dict]:
    """从模板生成 N 条样本。"""
    samples = []
    for label, templates in ALL_TEMPLATES.items():
        # 每模板 × 5-8 个变体(顺序扰动 + 前后加 1-2 句噪声)
        noise_prefixes = [
            "",
            "用户正在操作电脑,",
            "截图显示,",
            "Capture 视图,",
            "屏幕上看到,",
            "Screenshot shows,",
            "视觉观察:",
        ]
        noise_suffixes = [
            "",
            "背景模糊",
            "时间戳 14:23",
            "分辨率 1920x1080",
            "颜色主题: dark",
            "放大倍率 100%",
            "屏幕部分内容被截断",
        ]
        for t in templates:
            for _ in range(5):
                prefix = rng.choice(noise_prefixes)
                suffix = rng.choice(noise_suffixes)
                desc = prefix + t + (suffix if suffix else "")
                samples.append({
                    "text": desc,
                    "risk_label": label,
                    "jailbreak_label": False,
                    "_source": "rule_template",
                })
    rng.shuffle(samples)
    return samples


# ------------------------------------------------------------
# laya zero-shot 校核(可选;真推理耗 CPU 但 ~30-50ms/条)
# ------------------------------------------------------------

def _laya_verify(samples: list[dict], use_laya: bool = True) -> tuple[list[dict], dict]:
    """对每条样本用 laya zero-shot 校核;返回 (verified_samples, stats)。

    Args:
        samples: 候选样本
        use_laya: 是否真调 laya;False 时只标 _laya_skip=True

    Returns:
        verified_samples: 校核过的样本(增加 _laya_choice / _laya_conf 字段)
        stats: {agree, disagree, total, parse_fail}
    """
    if not use_laya:
        for s in samples:
            s["_laya_choice"] = None
            s["_laya_conf"] = None
            s["_laya_skip"] = True
        return samples, {"agree": 0, "disagree": 0, "total": len(samples), "parse_fail": 0}

    from laya import Router
    router = Router(default="multilingual", preload=True)
    qs = {
        "screen_type": {
            "type": "choice",
            "instructions": "Classify the screen content described in `desc`.",
            "criteria": {
                "app": "a desktop application window with menus/toolbars (browser, file manager, IDE overall framework)",
                "focused": "a focused text input, terminal command prompt, or search box in progress",
                "code": "source code, technical text content (programming, scripts, markup)",
                "dialog": "a modal dialog, alert, popup, or system message overlay",
            },
        }
    }

    agree = disagree = parse_fail = 0
    t0 = time.time()
    for i, s in enumerate(samples, 1):
        try:
            res = router.predict({"desc": s["text"]}, qs)
            answers = res.get("answers", {})
            st = answers.get("screen_type", {})
            choice = st.get("choice")
            conf = st.get("answer_confidence") or st.get("confidence") or 0
            if not choice:
                parse_fail += 1
                s["_laya_choice"] = None
                s["_laya_conf"] = None
                continue
            s["_laya_choice"] = choice
            s["_laya_conf"] = round(conf, 4)
            if choice == s["risk_label"]:
                agree += 1
            else:
                disagree += 1
        except Exception as e:
            parse_fail += 1
            s["_laya_choice"] = None
            s["_laya_conf"] = None
            s["_laya_error"] = f"{type(e).__name__}: {str(e)[:80]}"
        if i % 50 == 0:
            elapsed = time.time() - t0
            print(f"    laya 校核 {i}/{len(samples)} ({elapsed:.1f}s, "
                  f"agree={agree} disagree={disagree} parse_fail={parse_fail})")

    return samples, {
        "agree": agree,
        "disagree": disagree,
        "total": len(samples),
        "parse_fail": parse_fail,
        "agree_pct": round(agree / len(samples) * 100, 1) if samples else 0,
    }


def _balance(samples: list[dict], limit: int) -> list[dict]:
    """每类 cap → 总 ≤ limit。"""
    by_risk: dict[str, list[dict]] = {}
    for s in samples:
        by_risk.setdefault(s["risk_label"], []).append(s)
    per_class = max(limit // 4, 50)
    picked = []
    for risk, items in by_risk.items():
        picked.extend(items[:per_class])
    random.Random(42).shuffle(picked)
    return picked[:limit]


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="M3.81 vision captioner 4 分类数据集准备")
    ap.add_argument("--output", default="data_vision_caption.jsonl")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--no-laya", action="store_true",
                    help="跳过 laya zero-shot 校核(纯规则)")
    args = ap.parse_args()

    rng = random.Random(42)

    print(f"[1/4] 规则生成 4 类样本...")
    t0 = time.time()
    samples = _generate_descriptions(rng)
    print(f"  + 候选 {len(samples)} 条,耗时 {time.time()-t0:.1f}s")
    by_label = {}
    for s in samples:
        by_label[s["risk_label"]] = by_label.get(s["risk_label"], 0) + 1
    print(f"  by_label: {by_label}")

    print(f"[2/4] laya zero-shot 校核 (--no-laya={'yes' if args.no_laya else 'no'})...")
    t0 = time.time()
    samples, stats = _laya_verify(samples, use_laya=not args.no_laya)
    print(f"  + 校核完成 {stats} 耗时 {time.time()-t0:.1f}s")

    print(f"[3/4] 多样性平衡(每类 cap {args.limit // 4}, 总 ≤ {args.limit})...")
    balanced = _balance(samples, args.limit)
    print(f"  + 入训练集 {len(balanced)} 条")
    by_label2 = {}
    for s in balanced:
        by_label2[s["risk_label"]] = by_label2.get(s["risk_label"], 0) + 1
    print(f"  by_label: {by_label2}")

    print(f"[4/4] 写盘: {args.output}")
    out = Path(args.output)
    with out.open("w", encoding="utf-8") as f:
        for s in balanced:
            row = {
                "text": s["text"],
                "risk_label": s["risk_label"],
                "jailbreak_label": False,
                "_laya_choice": s.get("_laya_choice"),
                "_laya_conf": s.get("_laya_conf"),
                "_source": s["_source"],
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"  ✅ 写盘: {out} ({out.stat().st_size/1024:.1f} KB, {len(balanced)} 条)")
    print()
    print("下一步:")
    print("  python bench_laya_captioner.py --input data_vision_caption.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())