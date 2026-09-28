#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# email_webhook_daemon.py — M3.48.2 agentmail.to 实时邮件分类 daemon(2026-09-23)
#
# 链路:
#   外部邮箱 → 自动转发 → oiagent@agentmail.to
#                         ↓ (WebSocket 或 Webhook)
#                       本 daemon
#                         ↓ email_conf 分类(本地 adapter,无外网依赖)
#                         ↓ 按 action 落本地日志 + (可选)桌面通知 / 转发回自己
#
# 选 WebSocket 而不是 webhook 的原因:
#   - 本机没公网 IP,Webhook 需要 ngrok/cloudflared 隧道
#   - agentmail 提供 wss://ws.agentmail.to,daemon 主动连,无需公网入口
#   - 内网 SSH 训练时已验证 paramiko SFTP 稳定可用
#
# 用法:
#   # 1. 配环境变量:export AGENTMAIL_API_KEY=...
#   # 2. 启动:
#   python email_webhook_daemon.py --inbox "prisiragent@agentmail.to"
#
# 退出: Ctrl-C
#
# 落盘:
#   _email_classified.jsonl  — 每条: ts / from / subject / risk / action / conf / latency_ms
#   _email_actions/          — 按 action 分子目录存 raw MIME（子类可 json 化）
#
# ⚠ 已知限制:
#   - 免费 plan 只读,不能改 agentmail folder(归档 / 星标)
#   - 真要写操作需要 paid plan 的 IMAP(Developer $20/月 起)
#   - 本 daemon 默认只读 + 本地分类 + 日志,够 M3.48.2 验证端到端
#
# v0.1 — M3.48.2 端到端验证骨架
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# 默认输出位置
LOG_PATH = _HERE / "01_email_classified.jsonl"
ACTIONS_DIR = _HERE / "_email_actions"


# ------------------------------------------------------------
# WebSocket 订阅(本地实现,不依赖外部 SDK)
# ------------------------------------------------------------
async def subscribe_inbox_ws(api_key: str, inbox: str):
    """订阅 agentmail inbox 的 message.received 事件。

    参考:https://agentmail.to/docs/webhook-setup
    WebSocket 端点:wss://ws.agentmail.to/v0/inboxes/{inbox}/subscribe
    头部:Authorization: Bearer <api_key>
    payload: {"event_type":"message.received","message":{...}}
    """
    import websockets
    uri = f"wss://ws.agentmail.to/v0/inboxes/{inbox}/subscribe"
    headers = {"Authorization": f"Bearer {api_key}"}
    print(f"[ws] 订阅 {inbox} ...")
    async with websockets.connect(uri, extra_headers=headers,
                                   ping_interval=30, ping_timeout=10) as ws:
        print(f"[ws] 已连,等待 message.received ...")
        async for msg in ws:
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                print(f"[ws] 非 JSON payload: {msg[:100]!r}")
                continue
            yield payload


# ------------------------------------------------------------
# 分类(本地 adapter,无外网依赖)
# ------------------------------------------------------------
def classify_message(message: dict, adapter) -> dict:
    """从 agentmail message payload 抽取发件人/主题/正文,跑 email_conf。"""
    sender = message.get("from_") or message.get("from") or "unknown@unknown"
    subject = message.get("subject", "")
    body = message.get("text") or message.get("body") or ""
    # body 取前 200 字(跟训练数据对齐)
    body_short = body[:200]
    # received_hours:用 message.created_at 算,fallback 1 小时
    received_hours = 1.0
    if "created_at" in message:
        try:
            from datetime import datetime
            t = datetime.fromisoformat(message["created_at"].replace("Z", "+00:00"))
            received_hours = max(0.1, (time.time() - t.timestamp()) / 3600)
        except Exception:
            pass

    from classify_email import classify_email
    return classify_email(adapter, sender, subject, body_short,
                          received_hours=received_hours,
                          has_attachment=bool(message.get("attachments")))


# ------------------------------------------------------------
# action 落盘 + 桌面通知
# ------------------------------------------------------------
def take_action(record: dict, raw_message: dict) -> None:
    """根据 action 做本地处理:写日志 + 子目录 + (可选)桌面通知。"""
    action = record.get("action") or "_none_"
    risk = record.get("risk") or "_none_"

    # 1) append 主日志
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # 2) 按 action 子目录
    ACTIONS_DIR.mkdir(exist_ok=True)
    action_dir = ACTIONS_DIR / action
    action_dir.mkdir(exist_ok=True)
    ts_str = datetime.fromtimestamp(record["ts"]).strftime("%Y%m%d_%H%M%S")
    fname = f"{ts_str}_{record['sender'].replace('@', '_at_')[:30]}.json"
    with (action_dir / fname).open("w", encoding="utf-8") as f:
        json.dump({"record": record, "raw": raw_message},
                  f, ensure_ascii=False, indent=2)

    # 3) critical / high 桌面通知(Windows only,win10toast)
    if risk in ("critical", "high"):
        try:
            from win10toast import ToastNotifier
            toaster = ToastNotifier()
            toaster.show_toast(
                f"[{risk.upper()}] {action}",
                f"From: {record['sender']}\nSubject: {record['subject']}",
                duration=8,
                threaded=True,
            )
        except ImportError:
            print(f"  ⚠ win10toast 未装,跳过桌面通知(可以 pip install win10toast)")


# ------------------------------------------------------------
# 主 loop
# ------------------------------------------------------------
async def main(inbox: str, log_only: bool = False):
    api_key = os.environ.get("AGENTMAIL_API_KEY")
    if not api_key:
        print("ERROR: AGENTMAIL_API_KEY 环境变量未设", file=sys.stderr)
        return 1

    # 懒加载 adapter
    print("[1/3] 加载 email_conf adapter ...")
    from adapter_registry import get_adapter
    adapter = get_adapter("email_conf")
    print(f"  ✅ {adapter.spec.description[:80]}")

    # 启动 ws 订阅
    print(f"[2/3] 连 agentmail WebSocket ...")
    print(f"  inbox: {inbox}")
    print(f"  日志: {LOG_PATH}")
    print(f"  动作目录: {ACTIONS_DIR}")

    print(f"[3/3] 进入主循环(Ctrl-C 退出)")
    try:
        async for payload in subscribe_inbox_ws(api_key, inbox):
            evt = payload.get("event_type", "")
            if evt != "message.received":
                print(f"  [skip] event_type={evt}")
                continue

            msg = payload.get("message", {})
            print(f"\n[from ] {msg.get('from_','?')} | {msg.get('subject','?')[:50]}")

            t0 = time.time()
            record = classify_message(msg, adapter)
            record["ts"] = t0
            record["agentmail_message_id"] = msg.get("id") or msg.get("message_id")
            record["received_at_alicloud"] = datetime.now().isoformat()

            print(f"  → risk={record.get('risk'):8s} action={record.get('action'):8s} "
                  f"conf={record.get('risk_conf')} latency={record.get('latency_ms')}ms")

            if not log_only:
                take_action(record, msg)

    except KeyboardInterrupt:
        print("\n[exit] Ctrl-C,bye")
    except Exception as e:
        print(f"\n[FATAL] {type(e).__name__}: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="agentmail 实时邮件分类 daemon")
    ap.add_argument("--inbox", default="prisiragent@agentmail.to",
                    help="要监听的 inbox(默认 prisiragent@agentmail.to)")
    ap.add_argument("--log-only", action="store_true",
                    help="只分类 + 写日志,不触发桌面通知 / action 落盘")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.inbox, args.log_only)) or 0)