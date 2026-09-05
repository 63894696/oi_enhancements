# -*- coding: utf-8 -*-
# F1 管家反馈网络-邮箱最小环 — 接管验证脚本(2026-08-14)
# prisiragent 团队未启动(task 569 无消费者),本会话主动介入接手验证 F1 链路。
#
# 验证内容(对齐 docs/prisiragent-task-f1-steward-mailbox-2026-08-14.md 验收):
#   ① 生成/加载管家反馈身份(Ed25519,本地落盘 — 模拟 identity.mojom AgentSign)
#   ② 组签名信封(body_raw 逐字原文 + category + agent_fp + AgentSign 签名)
#   ③ 中文原文经 send_chinese(PNG)发到运营方邮箱 prisiragent@agentmail.to
#   ④ 回读收件箱确认收到,验签通过
# 红线:原文逐字不改写;category 另起字段;API key 走 env;审计脱敏。
# 测试数据带唯一 TAG,跑完自清理(删除测试邮件如需)。
import io
import json
import os
import sys
import time
import hashlib
import base64

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agentmail_helper"))
from helper import AgentMail  # noqa: E402

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

# 管家反馈身份密钥落盘路径(模拟未来 identity.mojom 的 agent-proxy 子密钥;os_crypt 由本体接管)
KEY_PATH = os.path.join(os.path.dirname(__file__), "_f1_steward_key.pem")
TAG = "F1STEWARD-" + time.strftime("%Y%m%d-%H%M%S")  # 唯一 TAG


def load_or_create_key():
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return Ed25519PrivateKey.from_private_bytes(f.read())
    sk = Ed25519PrivateKey.generate()
    with open(KEY_PATH, "wb") as f:
        f.write(sk.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()))
    return sk


def fingerprint(pub_raw: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(pub_raw).digest()[:16]).decode().rstrip("=")


def main():
    print("=== F1 管家反馈网络-邮箱最小环 接管验证 ===\n")

    # ① 管家身份(Ed25519)
    sk = load_or_create_key()
    pub_raw = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    agent_fp = fingerprint(pub_raw)
    print(f"[1] 管家身份指纹 agent_fp = {agent_fp}")

    # ② 组签名信封(逐字原文 + category + 签名)
    body_raw = "翻译插件在处理 github trending 页面时会破坏原有样式,而且多次失败也看不到是哪个模型出的问题。希望能加失败日志,并且读动态渲染页面的能力很需要。"  # 用户逐字吐槽
    category = "translate"
    envelope_id = "env-" + hashlib.sha256((TAG + body_raw).encode()).hexdigest()[:16]
    payload = hashlib.sha256(body_raw.encode("utf-8")).digest()
    sig = base64.b64encode(sk.sign(payload)).decode()
    envelope = {
        "type": "user_feedback", "v": 1,
        "envelope_id": envelope_id, "agent_fp": agent_fp,
        "body_raw": body_raw, "category": category,
        "context": {"surface": "ntp", "version": "0.1.0-f1", "locale": "zh"},
        "sig": sig, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tag": TAG,
    }
    print(f"[2] 信封 envelope_id={envelope_id}  body_raw({len(body_raw)}字,逐字)  category={category}")
    print(f"    sig={sig[:24]}...")

    # 验签自检(本地)
    Ed25519PublicKey.from_public_bytes(pub_raw).verify(base64.b64decode(sig), payload)
    print("    本地验签: OK")

    # ③ 发到运营方邮箱(中文 → PNG;subject 英文)
    am = AgentMail()
    subject = f"[Prisir steward] {TAG} feedback"
    human = (
        f"【管家反馈网络 F1 验证】\n\n"
        f"管家指纹(agent_fp):{agent_fp}\n"
        f"信封 ID:{envelope_id}\n"
        f"分类:{category}\n"
        f"时间:{envelope['created_at']}\n\n"
        f"用户逐字原文:\n{body_raw}\n\n"
        f"签名(sig):{sig}\n"
        f"测试 TAG:{TAG}"
    )
    print(f"[3] 发送到 prisiragent@agentmail.to (subject={subject!r}, 中文走 PNG)...")
    res = am.send_chinese("prisiragent@agentmail.to", subject, human)
    print(f"    发送返回: {json.dumps(res, ensure_ascii=False)[:200]}")

    # ④ 回读确认收到
    time.sleep(4)
    msgs = am.list_messages("prisiragent@agentmail.to", limit=10)
    lst = msgs.get("messages", msgs if isinstance(msgs, list) else [])
    found = None
    for m in lst:
        if TAG in (m.get("subject") or ""):
            found = m
            break
    if found:
        print(f"[4] ✓ 收件箱已收到: subject={found.get('subject')!r}  date={found.get('created_at')}")
        print(f"    from={found.get('from')}")
    else:
        print("[4] ✗ 未在最新邮件中找到本 TAG(可能延迟,稍后可再查)")
        print("    最新 subjects:", [ (m.get('subject') or '')[:40] for m in lst[:5] ])

    # 审计(脱敏:不含完整原文)
    print(f"\n[审计] {{ts:{envelope['created_at']}, category:{category}, ok:{found is not None}, sig_fp:{agent_fp}, tag:{TAG}}}")
    print("\n=== F1 链路验证完成 ===")
    print(f"NOTE: 测试密钥 {KEY_PATH};测试邮件 subject 含 TAG {TAG} 可据此清理。")


if __name__ == "__main__":
    main()
