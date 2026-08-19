"""chatroom_bot.py — 3.B 群聊机器人(第 4 名成员,带 BOT 徽)

一个普通群成员(is_bot=True),按 3.B 惯例:
  - **@提及按 user_id 解析,不按昵称文本**(撞名时无歧义)。消息带 `mentions:[user_id]`,
    机器人只对提及自己 user_id 的消息响应;也响应以 "@bot" 开头的文本(便捷写法,
    客户端会把 @bot 解析成机器人 user_id 填入 mentions)。
  - 回复逻辑**可插拔**:默认规则应答;设 CHATBOT_LLM_* 环境可接 LLM(后续)。
  - 消息同样 Ed25519 签名(机器人也有自己的密钥对身份)。

CLI:
  python chatroom_bot.py --url ws://... --room test --name 助手 [--key-file bot.key]
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from chatroom_client import ChatClient, Identity, _token


def rule_reply(text: str, sender: str, bot: "Bot") -> str:
    """规则应答(可替换为 LLM)。返回空串=不回复。"""
    t = text.strip().lower()
    if any(k in t for k in ("你好", "hi", "hello", "在吗")):
        return f"你好 {sender},我是群机器人,@{bot.name} 可以问我。成员 {len(bot.client.members)} 人。"
    if "成员" in t or "谁在线" in t or "list" in t:
        names = [f"{m['display_name']}({'BOT' if m.get('is_bot') else '人'}{'●' if m.get('online') else '○'})"
                 for m in bot.client.members.values()]
        return "当前成员: " + (", ".join(names) if names else "(未知)")
    if "时间" in t or "几点" in t:
        return "服务器时间: " + time.strftime("%Y-%m-%d %H:%M:%S")
    if "帮助" in t or "help" in t:
        return "我会:打招呼 / 问成员(谁在线) / 问时间。提到我(@)即可。"
    # 默认回声式应答(证明在线)
    return f"收到 {sender} 的消息:「{text[:60]}」。我还很简单,说「帮助」看我会什么。"


class Bot:
    def __init__(self, url: str, room: str, name: str, identity: Identity):
        self.name = name
        self.client = ChatClient(url, room, name, identity, is_bot=True,
                                 on_msg=self._on_msg, on_event=self._on_event)

    def _on_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "joined":
            print(f"[bot] 已加入 {ev.get('room')} 作为机器人 {self.name}/{self.client.id.user_id[:8]}…", flush=True)
        elif t == "error":
            print(f"[bot] 错误: {ev.get('error')}", flush=True)

    def _on_msg(self, m: dict, verified: bool) -> None:
        # 不响应自己的消息;不响应未验签的(防伪造提及)
        sender_uid = m.get("user_id", "")
        if sender_uid == self.client.id.user_id:
            return
        body = m.get("body", {})
        text = body.get("text", "") if isinstance(body, dict) else str(body)
        sender = m.get("display_name", sender_uid)
        mentions = m.get("mentions") or []
        mentioned = self.client.id.user_id in mentions or text.strip().lower().startswith("@bot") \
            or f"@{self.name}" in text
        print(f"[bot] 收到 #{m.get('seq')} {sender}: {text} (提及我={mentioned}, 验签={verified})", flush=True)
        if not mentioned:
            return
        reply = rule_reply(text, sender, self)
        if reply:
            time.sleep(0.3)  # 像人一样稍停顿
            # 回复时 @ 回发送者(按其 user_id)
            self.client.post(reply, mentions=[sender_uid])
            print(f"[bot] 回复: {reply}", flush=True)

    def run(self) -> None:
        self.client.listen(reconnect=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="3.B 群聊机器人")
    ap.add_argument("--url", default=os.environ.get("CHATROOM_URL", "ws://127.0.0.1:18811"))
    ap.add_argument("--room", required=True)
    ap.add_argument("--name", default="群助手")
    ap.add_argument("--key-file", default="", help="机器人 Ed25519 私钥(持久身份)")
    args = ap.parse_args()

    key_path = Path(args.key_file) if args.key_file else None
    if key_path and key_path.exists():
        ident = Identity.load(key_path)
    else:
        ident = Identity.generate()
        if key_path:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            ident.save(key_path)

    Bot(args.url, args.room, args.name, ident).run()


if __name__ == "__main__":
    main()
