# -*- coding: utf-8 -*-
"""_test_obsidian_ingest.py — A3 实测 p14_ingest 自动写盘(2026-09-23)

用法:
  python _test_obsidian_ingest.py

输入 cfg:companion_asr_settings.json 的 p14_* + fcontent_root 段
输出:真 vault _incremental/ 下新增 1 个 md + _p14_index.json 多 1 个 entry

退出码:
  0 = ok,新增成功
  1 = 没触发(reason 非 ok)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import p14_ingest


async def main() -> int:
    cfg = json.loads(
        (_HERE / "companion_asr_settings.json").read_text(encoding="utf-8"))
    cfg["p14_min_has_prob"] = 0.0  # 测试模式:不卡 has_prob,只要 value ≥ 2 就触发

    user_text = "M3.66 L3 重启 Obsidian 自动写盘验证"
    assistant_text = (
        "本次验证三个并行任务:\n"
        "1. M3.64 critical 专项 — 5 scenario balanced critical 数据\n"
        "2. M3.66 L3 重启 — 8 scenario classification-head 全量训\n"
        "3. Obsidian 自动写盘验证 — 修 fcontent_root 路径从死路径切到真 vault\n"
        "预期效果:真 vault _incremental/ 下新增 1 个 md 文件,index 多 1 entry\n"
    )

    # inject 一个超快 fake eval,确保测试不会被 companion_jev 卡
    async def fake_eval(user_text, assistant_text, **kwargs):
        return {
            "has_probability": 0.95,
            "value_score": "archivable",
            "value_index": 2,
        }

    print(f"[test] cfg.fcontent_root = {cfg.get('fcontent_root')}")
    print(f"[test] cfg.p14_min_value = {cfg.get('p14_min_value')}")
    before = len(p14_ingest.load_index(cfg["fcontent_root"]))
    print(f"[test] BEFORE: load_index = {before}")

    res = await p14_ingest.evaluate_and_ingest(
        user_text, assistant_text, cfg,
        intent="chat",
        eval_fn=fake_eval,
    )
    print(f"[test] result = {json.dumps(res, ensure_ascii=False)}")

    after = len(p14_ingest.load_index(cfg["fcontent_root"]))
    print(f"[test] AFTER:  load_index = {after}")

    if res.get("reason") == "ok" and after > before:
        print(f"[test] OK: 新增 {after - before} entry")
        if res.get("path"):
            print(f"[test] 新文件: {res['path']}")
        return 0
    else:
        print(f"[test] FAIL: reason={res.get('reason')}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))