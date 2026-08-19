"""_test_groupchat_e2e.py — SecureDM 群聊 E2E 密码学层验证脚本。

纪律:不打印任何密钥真值,只打印布尔/长度。
"""

import securedm_groupchat_e2e as g


def main():
    room = "lobby"

    # 1) 三个成员
    alice, bob, carol = g.GroupE2E(), g.GroupE2E(), g.GroupE2E()
    assert len(alice.public) > 0 and alice.public != bob.public
    print("1. 三实例生成 OK (public 互不相同)")

    # 2) alice 当房主:生成房间钥,逐人分发给 bob/carol
    key = alice.new_room_key()
    assert len(key) == 32
    alice.book.set_key(room, 0, key)  # 房主自己登记 epoch 0
    d_bob = alice.wrap_for_member(room, 0, key, bob.public)
    d_carol = alice.wrap_for_member(room, 0, key, carol.public)
    # body 必须 JSON 可序列化
    import json
    json.dumps(d_bob); json.dumps(d_carol)
    r_b, e_b, k_b = bob.unwrap_key_dist(d_bob)
    r_c, e_c, k_c = carol.unwrap_key_dist(d_carol)
    assert (r_b, e_b) == (room, 0) and (r_c, e_c) == (room, 0)
    assert k_b == key and k_c == key, "分发的钥必须与房主一致"
    assert k_b != alice.new_room_key(), " sanity: 随机钥不等"
    print("2. 房主分发 -> bob/carol 解回同一把房间钥 OK")

    # 3) alice seal_msg -> bob open_msg(默认 AESGCM suite 0x02)
    body = alice.seal_msg(room, "你好群聊")
    assert body["kind"] == "ctext" and body["suite"] == g.SUITE_WEB_AESGCM \
        and body["epoch"] == 0
    json.dumps(body)
    back = bob.open_msg(body)
    assert back == "你好群聊"
    print(f"3. AESGCM 消息加解密 OK (suite={body['suite']})")

    # 4) 没有对应 epoch 钥的一方 open_msg 必须抛错
    dave = g.GroupE2E()  # 从未收到 key-dist
    try:
        dave.open_msg(body)
        raise AssertionError("无钥者应抛错")
    except KeyError:
        print("4. 无对应 epoch 钥 -> open_msg 抛 KeyError OK")

    # 4b) 有钥但密文被篡改 -> AEAD 验签失败
    import base64 as _b
    tampered = dict(body)
    raw = bytearray(_b.b64decode(body["ct"]))
    raw[0] ^= 1
    tampered["ct"] = _b.b64encode(bytes(raw)).decode()
    try:
        bob.open_msg(tampered)
        raise AssertionError("篡改密文应抛错")
    except Exception as e:
        print(f"4b. 篡改密文 -> 解密失败 OK ({type(e).__name__})")

    # 5) carol 离开 -> alice 轮换(epoch+1),只发给 {alice, bob}
    dists = alice.rotate(room, {"alice": alice.public, "bob": bob.public})
    assert len(dists) == 2 and all(d["epoch"] == 1 for d in dists)
    assert {d["to_user_id"] for d in dists} == {"alice", "bob"}
    for d in dists:
        json.dumps(d)
    # bob 收到新钥
    bob_d = next(d for d in dists if d["to_user_id"] == "bob")
    _, e2, k2 = bob.unwrap_key_dist(bob_d)
    assert e2 == 1 and k2 != key, "新 epoch 钥必须不同于旧钥"
    print("5. carol 离开后轮换 -> epoch 1 新钥分发给剩余成员 OK")

    # 6) 新 epoch 上 seal/open 仍通
    body2 = alice.seal_msg(room, "轮换后第一条")
    assert body2["epoch"] == 1
    assert bob.open_msg(body2) == "轮换后第一条"
    print("6. epoch+1 后 seal/open 正常 OK")

    # 7) 前向保密:旧 epoch 钥解不开新 epoch 密文
    #    carol 只有 epoch 0 的钥,且根本没收到 epoch 1 -> KeyError
    try:
        carol.open_msg(body2)
        raise AssertionError("carol 无 epoch1 钥应抛错")
    except KeyError:
        print("7a. carol(已离开)解新密文 -> KeyError OK")
    #    构造一个只有旧钥的实例硬解新密文 -> AEAD 失败
    legacy = g.GroupE2E()
    legacy.book.set_key(room, 0, key)
    # 手动把 body2 的 epoch 改成 0,模拟"拿旧钥解新密文"
    forged = dict(body2); forged["epoch"] = 0
    try:
        legacy.open_msg(forged)
        raise AssertionError("旧钥解新密文应失败")
    except Exception as e:
        print(f"7b. 旧 epoch 钥解新 epoch 密文 -> 失败 OK ({type(e).__name__})")

    # 8) 旧钥缓存:bob 仍能解 epoch 0 的历史消息
    assert bob.open_msg(body) == "你好群聊"
    print("8. 旧 epoch 钥缓存解历史消息 OK")

    # 9) SUITE_RUST(ChaCha20Poly1305)消息套件也通
    body3 = alice.seal_msg(room, "rust 套件", suite=g.SUITE_RUST)
    assert bob.open_msg(body3) == "rust 套件"
    print("9. SUITE_RUST(ChaCha20Poly1305)消息加解密 OK")

    print("ALL GREEN")


if __name__ == "__main__":
    main()
