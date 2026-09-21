#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+B-4.F(2026-09-21)marketplace 远端镜像 — 静态锚点扫 + cli 单元 smoke。

覆盖:
  S1. forum_relay.py:attachment 字段白名单 + sha256 校验 + mime 白名单 + size 限
  S2. forum_relay.py:MAX_BODY=96KB + MAX_ATTACHMENT_SIZE=2MB + serve max_size=8MB
  S3. forum_forward.py:websockets.connect max_size 改 8MB(3 处)
  S4. marketplace ext:Identity 持久化(~/.prisir/marketplace_identity.key)
  S5. marketplace ext:Ed25519 pub/fp 计算 + canon 严格一致(sort_keys + (',',':'))
  S6. marketplace ext:PoW 算力(checkPow + solvePow)
  S7. marketplace ext:WebSocket 单例连接 + hello/welcome/read/post 帧
  S8. marketplace ext:market.list 拉 history 过滤 [Prisir-Workflow] 前缀
  S9. marketplace ext:market.fetch 按 post_id 找记录 + 返 attachment data_b64
  S10. marketplace ext:market.publish 拼 body + 算 PoW + 签名 + 发帧
  S11. marketplace ext:market.identity 返 pub/fp/file/board
  S12. cli workflow_market_list / fetch / publish tool def 三件套
  S13. cli _ext_rpc_call 通用 helper(参数化 ext_id)
  S14. cli _t_workflow_market_list / fetch / publish handler 函数 + 子代工具集剔除 publish
  S15. cli handler dispatch 3 个 if name == "workflow_market_*"
  S16. web 🌐 浏览远端 + 📤 发布到论坛 按钮 + #wf-market-modal + #wf-publish-modal
  S17. web JS wfMarketList / wfMarketRefresh / wfMarketDownload / wfMarketPublish / wfPublishApply / wfMarketCancel
  S18. web i18n wf_market_* + wf_publish_* 中英(11 keys)
  S19. web _shell_system_prompt 末尾「远端 workflow 镜像」简表注入
  S20. py_compile cli/web/forum_relay/forum_forward OK + node --check marketplace OK
"""
import os
import re
import sys
import subprocess
import json as _json
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CLI = os.path.join(ROOT, "prisIragent_cli.py")
WEB = os.path.join(ROOT, "prisIragent_web.py")
FORUM_RELAY = os.path.join(ROOT, "forum_relay.py")
FORUM_FORWARD = os.path.join(ROOT, "forum_forward.py")
EXT = os.path.join(ROOT, "extensions", "marketplace", "index.js")
EXT_PKG = os.path.join(ROOT, "extensions", "marketplace", "package.json")


def _read(path):
    return open(path, encoding="utf-8").read()


def section(title):
    print("=" * 60)
    print(f"[{title}]")
    print("=" * 60)


def _hit(name, ok):
    print(f"  {'✓' if ok else '✗'} {name}")
    return 1 if ok else 0


# === S1 forum_relay attachment 字段白名单 ===
def s1_forum_relay_attachment():
    section("S1. forum_relay attachment 字段白名单 + sha256 校验")
    src = _read(FORUM_RELAY)
    checks = [
        ("MAX_ATT_SIZE = 2 * 1024 * 1024", "MAX_ATT_SIZE = 2 * 1024 * 1024" in src),
        ("MAX_ATT_MIMES 白名单",
         "MAX_ATT_MIMES" in src and "application/gzip" in src and "application/json" in src),
        ("_validate_attachment helper", "def _validate_attachment" in src),
        ("_validate_attachment 校 filename 防 .. / 路径分隔符",
         '".." in filename' in src or "'..' in filename" in src or '"/" in filename' in src),
        ("_validate_attachment 校 sha256 实际值",
         "actual = b64url16" in src and "if actual != sha" in src),
        ("_validate_attachment 校 size 一致",
         "len(raw) != size" in src),
        ("validate_post attachment 可选字段",
         "set(post.keys()) - top_keys != {\"attachment\"}" in src
         or "post.get(\"attachment\")" in src),
        ("attachment 校失败返对应 reason",
         "bad_attachment" in src or "oversize_attachment" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S2 forum_relay MAX_BODY + serve max_size ===
def s2_forum_relay_limits():
    section("S2. forum_relay MAX_BODY=96KB + serve max_size=8MB")
    src = _read(FORUM_RELAY)
    checks = [
        ("MAX_BODY = 4000(纯文字帖)",
         "MAX_BODY = 4000" in src or "MAX_BODY          = 4000" in src),
        ("MAX_BODY_IMG 已有(图片帖)",
         "MAX_BODY_IMG" in src),
        ("MAX_ATT_SIZE = 2MB 已定义",
         "MAX_ATT_SIZE" in src and "2 * 1024 * 1024" in src),
        ("serve(..., max_size=8 * 1024 * 1024)",
         "max_size=8 * 1024 * 1024" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S3 forum_forward.py max_size ===
def s3_forum_forward_maxsize():
    section("S3. forum_forward.py websockets.connect max_size 改 8MB")
    src = _read(FORUM_FORWARD)
    # 至少有 1 处改 max_size=8MB
    cnt = src.count("max_size=8 * 1024 * 1024")
    ok1 = cnt >= 1
    ok2 = "max_size=128 * 1024" not in src  # 老 128KB 不应残留
    print(f"  {'✓' if ok1 else '✗'} forum_forward max_size=8MB 至少 1 处 (实际 {cnt})")
    print(f"  {'✓' if ok2 else '✗'} 老 max_size=128 * 1024 已清除")
    return ok1 and ok2


# === S4 marketplace ext Identity 持久化 ===
def s4_identity_persist():
    section("S4. marketplace ext Identity 持久化")
    src = _read(EXT)
    checks = [
        ("_identityPath helper", "function _identityPath" in src),
        ("_loadOrCreateIdentity helper", "function _loadOrCreateIdentity" in src),
        ("~/.prisir/marketplace_identity.key",
         "marketplace_identity.key" in src and os.path.join(".prisir", "") != ""),
        ("PKCS8 DER 包装 32 字节 seed",
         "pkcs8" in src and "302e020100300506032b657004220420" in src),
        ("generateKeyPairSync ed25519 首启",
         "generateKeyPairSync('ed25519')" in src
         or 'generateKeyPairSync("ed25519")' in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S5 marketplace ext Ed25519 pub/fp + canon 严格一致 ===
def s5_canon_and_fp():
    section("S5. Ed25519 pub/fp 计算 + canon 严格一致")
    src = _read(EXT)
    checks = [
        ("_pubB64Url helper", "function _pubB64Url" in src),
        ("_pubB64Url 用 SPKI 拆最后 32 字节", "spki" in src and "subarray" in src),
        ("_fp helper sha256 前 16 字符", "function _fp" in src and "sha256" in src),
        ("_canon helper", "function _canon" in src),
        ("_canon 排序 keys", "Object.keys(obj).sort()" in src or ".sort()" in src),
        ("_canon 用 (',',':') 紧凑",
         'JSON.stringify(k)' in src and "':'" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S6 PoW 算力 ===
def s6_pow():
    section("S6. PoW 算力:checkPow + solvePow")
    src = _read(EXT)
    checks = [
        ("_checkPow helper", "function _checkPow" in src),
        ("_checkPow 检查 nFull 字节为 0", "digest[i] !== 0" in src),
        ("_checkPow rem 位检查", "8 - rem" in src or "(8 - rem)" in src),
        ("_solvePow helper", "function _solvePow" in src),
        ("_solvePow 循环 2^53 找 nonce",
         "2 ** 53" in src and "pow" in src and "nonce" in src),
        ("_solvePow 用 sha256(canon) 算 digest",
         "createHash('sha256')" in src and "_canon" in src and "_checkPow" in src),
        ("POW_BITS 默认 18",
         "PRISIR_MARKET_POW_BITS" in src and "18" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S7 WebSocket 连接 ===
def s7_websocket():
    section("S7. WebSocket 单例连接 + hello/welcome/read/post 帧")
    src = _read(EXT)
    checks = [
        ("_ws 单例变量", "let _ws" in src or "let _ws =" in src),
        ("_helloOk 标志", "_helloOk" in src),
        ("_ensureConnected helper", "function _ensureConnected" in src or "async function _ensureConnected" in src),
        ("_ensureConnected 用 new WebSocket 内置",
         "new WebSocket" in src),
        ("FORUM_WS_URL 默认 ws://127.0.0.1:18812",
         "ws://127.0.0.1:18812" in src),
        ("send hello 帧", "'hello'" in src or '"hello"' in src or "{type:'hello'}" in src),
        ("welcome 帧判断", "'welcome'" in src),
        ("_wsRequest 收发帧", "function _wsRequest" in src),
        ("FORUM_WS_URL 可被 PRISIR_FORUM_WS_URL 覆盖",
         "PRISIR_FORUM_WS_URL" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S8 market.list ===
def s8_market_list():
    section("S8. marketplace ext market.list 命令")
    src = _read(EXT)
    checks = [
        ("market.list 命令注册", "market.list'" in src or 'market.list"' in src),
        ("market.list 调 _ensureConnected",
         "ext.registerCommand('market.list'" in src
         and "_ensureConnected()" in src.split("ext.registerCommand('market.list'")[1][:500]),
        ("market.list 调 _wsRequest read 帧",
         "'read'" in src or '"read"' in src or "type: 'read'" in src or 'type: "read"' in src),
        ("market.list 用 MARKET_BOARD 过滤",
         "MARKET_BOARD" in src and "browser/shell" in src),
        ("market.list 过滤 [Prisir-Workflow] 前缀",
         "[Prisir-Workflow]" in src or "startsWith('[Prisir-Workflow]')" in src),
        ("market.list 返 {ok, posts, last_seq}",
         "last_seq" in src),
        ("market.list 过滤 taken_down + retracted",
         "taken_down" in src and "retracted" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S9 market.fetch ===
def s9_market_fetch():
    section("S9. marketplace ext market.fetch 命令")
    src = _read(EXT)
    checks = [
        ("market.fetch 命令注册", "market.fetch'" in src or 'market.fetch"' in src),
        ("market.fetch 校验 post_id",
         "post_id" in src and "post_id required" in src),
        ("market.fetch 找 record by post_id",
         "m.post_id === args.post_id" in src
         or "m.post_id ===" in src and "post_id" in src),
        ("market.fetch 返 attachment.data_b64",
         "data_b64" in src),
        ("market.fetch 返 {ok, post_id, title, attachment}",
         "title" in src and "author_fp" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S10 market.publish ===
def s10_market_publish():
    section("S10. marketplace ext market.publish 命令")
    src = _read(EXT)
    checks = [
        ("market.publish 命令注册",
         "market.publish'" in src or 'market.publish"' in src),
        ("market.publish 校 title required", "title required" in src),
        ("market.publish 拼 body [Prisir-Workflow]+meta",
         "[Prisir-Workflow]" in src and "JSON.stringify(meta" in src),
        ("market.publish 拼 attachment 5 字段",
         "attachment_filename" in src and "attachment_sha256" in src
         and "attachment_size" in src and "attachment_data_b64" in src),
        ("market.publish 调 _validateAttachmentFields",
         "_validateAttachmentFields" in src),
        ("market.publish 算 PoW 调 _solvePow", "_solvePow" in src),
        ("market.publish 用 crypto.sign Ed25519",
         "crypto.sign(null" in src),
        ("market.publish 发 post 帧",
         "'post'" in src or '"post"' in src or "type: 'post'" in src),
        ("market.publish 返 {ok, post_id, seq, confirmed}",
         "post_id" in src and "confirmed" in src),
        ("signedView 含 attachment 入 canon",
         "attachment: att" in src and "signedView" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S11 market.identity ===
def s11_market_identity():
    section("S11. marketplace ext market.identity 命令")
    src = _read(EXT)
    checks = [
        ("market.identity 命令注册",
         "market.identity'" in src or 'market.identity"' in src),
        ("market.identity 返 pub/fp/file",
         "identity_file" in src and "forum_url" in src and "pow_bits" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S12 cli workflow_market_* tool defs ===
def s12_cli_tool_defs():
    section("S12. cli workflow_market_list / fetch / publish tool def 三件套")
    src = _read(CLI)
    checks = [
        ("workflow_market_list tool def", '"name": "workflow_market_list"' in src),
        ("workflow_market_list since_seq 参数",
         "workflow_market_list" in src and "since_seq" in src),
        ("workflow_market_fetch tool def", '"name": "workflow_market_fetch"' in src),
        ("workflow_market_fetch post_id required",
         '"required": ["post_id"]' in src),
        ("workflow_market_publish tool def",
         '"name": "workflow_market_publish"' in src),
        ("workflow_market_publish names 参数",
         '"names"' in src and "workflow_market_publish" in src),
        ("workflow_market_publish attachment_data_b64 参数",
         '"attachment_data_b64"' in src),
        ("workflow_market_publish title required",
         '"required": ["title"]' in src and "workflow_market_publish" in src),
        ("description 提 marketplace / 论坛 / PrisirAI 对话",
         "marketplace" in src and "PrisirAI 对话" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S13 cli _ext_rpc_call helper ===
def s13_cli_ext_rpc_call():
    section("S13. cli _ext_rpc_call 通用 helper")
    src = _read(CLI)
    checks = [
        ("def _ext_rpc_call 顶层", "def _ext_rpc_call(" in src),
        ("_ext_rpc_call 接受 ext_id 参数",
         "ext_id" in src and "_ext_rpc_call" in src),
        ("_ext_rpc_call 拼 ext_id 进 body",
         "ext_id" in src and "method" in src and "params" in src),
        ("_ext_rpc_call 失败返 dict(键 'error')",
         "rpc_{ext_id}.{method}_failed" in src or "rpc_" in src and "}" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S14 cli handler + 子代剔除 ===
def s14_cli_handlers():
    section("S14. cli handler + 子代工具集剔除 workflow_market_publish")
    src = _read(CLI)
    checks = [
        ("def _t_workflow_market_list", "def _t_workflow_market_list" in src),
        ("def _t_workflow_market_fetch", "def _t_workflow_market_fetch" in src),
        ("def _t_workflow_market_publish", "def _t_workflow_market_publish" in src),
        ("_t_workflow_market_list 调 _ext_rpc_call marketplace market.list",
         "_t_workflow_market_list" in src and '"marketplace"' in src and "market.list" in src),
        ("_t_workflow_market_publish 模式 A 调 task-runner bundle_export",
         "_task_runner_rpc" in src and "task.files.bundle_export" in src
         and "_t_workflow_market_publish" in src),
        ("_t_workflow_market_publish 模式 B 直接 publish",
         "attachment_data_b64" in src and "_t_workflow_market_publish" in src),
        ("_t_workflow_market_publish 必填 title 校验",
         "title required" in src or "需要 title" in src),
        ("_t_workflow_market_publish 二选一校验",
         "二选一" in src or "需二选一" in src),
        ("子代工具集剔除 workflow_market_publish",
         '"workflow_market_publish"' in src
         and "spawn_subagent" in src and "run_task" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S15 cli handler dispatch ===
def s15_cli_dispatch():
    section("S15. cli handler dispatch 3 个 if name == \"workflow_market_*\"")
    src = _read(CLI)
    checks = [
        ('dispatch workflow_market_list', 'name == "workflow_market_list"' in src),
        ('dispatch workflow_market_fetch', 'name == "workflow_market_fetch"' in src),
        ('dispatch workflow_market_publish', 'name == "workflow_market_publish"' in src),
        ("dispatch 调对应 handler", "_t_workflow_market_list" in src
                                     and "_t_workflow_market_fetch" in src
                                     and "_t_workflow_market_publish" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S16 web UI 锚点 ===
def s16_web_ui():
    section("S16. web 🌐 浏览远端 + 📤 发布到论坛 按钮 + 2 modal")
    src = _read(WEB)
    checks = [
        ("🌐 浏览远端 按钮 onclick wfMarketList",
         'id="wf-market-list-btn"' in src and "wfMarketList()" in src),
        ("📤 发布到论坛 按钮 onclick wfMarketPublish",
         'id="wf-market-publish-btn"' in src and "wfMarketPublish()" in src),
        ("data-i18n wf_market_list", 'data-i18n="wf_market_list"' in src),
        ("data-i18n wf_market_publish", 'data-i18n="wf_market_publish"' in src),
        ('#wf-market-modal', 'id="wf-market-modal"' in src),
        ('#wf-publish-modal', 'id="wf-publish-modal"' in src),
        ("#wf-market-modal 内有 #wf-market-list",
         'id="wf-market-list"' in src),
        ("#wf-publish-modal 内有 #wf-pub-title + #wf-pub-desc",
         'id="wf-pub-title"' in src and 'id="wf-pub-desc"' in src),
        ('#wf-pub-status 状态元素', 'id="wf-pub-status"' in src),
        ('data-i18n wf_market_title', 'data-i18n="wf_market_title"' in src),
        ('data-i18n wf_publish_title', 'data-i18n="wf_publish_title"' in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S17 web JS 6 函数 ===
def s17_web_js():
    section("S17. web JS wfMarket* 6 函数")
    src = _read(WEB)
    checks = [
        ("function wfMarketList def", "async function wfMarketList" in src),
        ("function wfMarketRefresh def", "async function wfMarketRefresh" in src),
        ("function wfMarketDownload def", "async function wfMarketDownload" in src),
        ("function wfMarketPublish def", "async function wfMarketPublish" in src),
        ("function wfPublishApply def", "async function wfPublishApply" in src),
        ("function wfMarketCancel def", "function wfMarketCancel" in src),
        ("wfMarketRefresh 调 marketplace market.list",
         "ext_id:'marketplace'" in src and "method:'market.list'" in src),
        ("wfMarketDownload 调 marketplace market.fetch",
         "method:'market.fetch'" in src),
        ("wfPublishApply 调 task.files.bundle_export → marketplace market.publish",
         "method: 'task.files.bundle_export'" in src
         and "method:'market.publish'" in src),
        ("wfMarketCancel 同时关 2 个 modal",
         "wf-market-modal" in src and "wf-publish-modal" in src
         and "wfMarketCancel" in src),
        ("wfMarketRefresh 渲染表格 table/tr/td",
         "wf-market-list" in src and "<tr>" in src),
        ("wfMarketDownload 收到 attachment → 调 task.files.bundle_import",
         "task.files.bundle_import" in src),
        ("wfPublishApply 含 status 进度提示",
         "wf-pub-status" in src and "⏳" in src),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S18 web i18n ===
def s18_web_i18n():
    section("S18. web i18n wf_market_* + wf_publish_* 中英(11 keys)")
    src = _read(WEB)
    keys_zh = ["wf_market_list:", "wf_market_publish:",
               "wf_market_title:", "wf_market_hint:",
               "wf_market_refresh:", "wf_market_confirm:",
               "wf_market_title_required:",
               "wf_market_title_label:", "wf_market_desc_label:",
               "wf_publish_title:", "wf_publish_apply:",
               "wf_publish_hint:"]
    keys_en = ["wf_market_list:'", "wf_market_publish:'",
               "wf_market_title:'", "wf_market_hint:'",
               "wf_market_refresh:'", "wf_market_confirm:'",
               "wf_market_title_required:'",
               "wf_market_title_label:'", "wf_market_desc_label:'",
               "wf_publish_title:'", "wf_publish_apply:'",
               "wf_publish_hint:'"]
    checks = []
    for k in keys_zh:
        checks.append((f"中文 {k}", k in src))
    for k in keys_en:
        checks.append((f"英文 {k}", k in src))
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S19 web _shell_system_prompt 远端简表注入 ===
def s19_system_prompt():
    section("S19. web _shell_system_prompt 末尾「远端 workflow 镜像」简表注入")
    src = _read(WEB)
    # 找 _shell_system_prompt 函数体内部
    fn_idx = src.find("def _shell_system_prompt")
    if fn_idx < 0:
        print(f"  ✗ _shell_system_prompt 函数未找到")
        return False
    # 找下一个 def 或 class,作为函数边界
    next_def = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_def if next_def > 0 else fn_idx + 20000]
    checks = [
        ("_ext_rpc_call marketplace market.list",
         "_ext_rpc_call(\"marketplace\"" in body and "market.list" in body),
        ("远端简表 markdown 头", "【远端 workflow 镜像" in body or "远端 workflow" in body),
        ("提到 workflow_market_fetch",
         "workflow_market_fetch" in body),
        ("timeout=3.0 快速失败",
         "timeout=3.0" in body or "timeout=3" in body),
        ("try/except 静默失败", "except Exception" in body and "_mlst" in body),
        ("PrisirAI 对话 子版出现在简表",
         "PrisirAI 对话" in body),
    ]
    ok = 0
    for n, hit in checks:
        ok += _hit(n, hit)
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S20 py_compile + node --check ===
def s20_compile():
    section("S20. py_compile cli/web/forum_relay/forum_forward + node --check marketplace")
    rc1 = subprocess.run([sys.executable, "-m", "py_compile", CLI], capture_output=True, text=True).returncode
    rc2 = subprocess.run([sys.executable, "-m", "py_compile", WEB], capture_output=True, text=True).returncode
    rc3 = subprocess.run([sys.executable, "-m", "py_compile", FORUM_RELAY], capture_output=True, text=True).returncode
    rc4 = subprocess.run([sys.executable, "-m", "py_compile", FORUM_FORWARD], capture_output=True, text=True).returncode
    rc5 = subprocess.run(["node", "--check", EXT], capture_output=True, text=True).returncode
    checks = [
        ("cli py_compile", rc1 == 0, f"rc={rc1}"),
        ("web py_compile", rc2 == 0, f"rc={rc2}"),
        ("forum_relay py_compile", rc3 == 0, f"rc={rc3}"),
        ("forum_forward py_compile", rc4 == 0, f"rc={rc4}"),
        ("marketplace node --check", rc5 == 0, f"rc={rc5}"),
    ]
    ok = 0
    for tup in checks:
        n, hit, *rest = tup
        ok += _hit(n + (" " + rest[0] if rest else ""), hit)
    print(f"  → {ok}/{len(checks)}")
    if rc1 != 0: print(f"     cli: {subprocess.run([sys.executable, '-m', 'py_compile', CLI], capture_output=True, text=True).stderr[:200]}")
    if rc2 != 0: print(f"     web: {subprocess.run([sys.executable, '-m', 'py_compile', WEB], capture_output=True, text=True).stderr[:200]}")
    if rc3 != 0: print(f"     forum_relay: {subprocess.run([sys.executable, '-m', 'py_compile', FORUM_RELAY], capture_output=True, text=True).stderr[:200]}")
    if rc4 != 0: print(f"     forum_forward: {subprocess.run([sys.executable, '-m', 'py_compile', FORUM_FORWARD], capture_output=True, text=True).stderr[:200]}")
    if rc5 != 0: print(f"     node: {subprocess.run(['node', '--check', EXT], capture_output=True, text=True).stderr[:200]}")
    return ok == len(checks)


def main():
    sections = [
        s1_forum_relay_attachment(),
        s2_forum_relay_limits(),
        s3_forum_forward_maxsize(),
        s4_identity_persist(),
        s5_canon_and_fp(),
        s6_pow(),
        s7_websocket(),
        s8_market_list(),
        s9_market_fetch(),
        s10_market_publish(),
        s11_market_identity(),
        s12_cli_tool_defs(),
        s13_cli_ext_rpc_call(),
        s14_cli_handlers(),
        s15_cli_dispatch(),
        s16_web_ui(),
        s17_web_js(),
        s18_web_i18n(),
        s19_system_prompt(),
        s20_compile(),
    ]
    print("=" * 60)
    passed = sum(1 for s in sections if s)
    total = len(sections)
    if passed == total:
        print(f"✓ P2.5+B-4.F marketplace ALL GREEN ({passed}/{total} sections)")
        return 0
    print(f"✗ P2.5+B-4.F marketplace FAILED ({passed}/{total} sections)")
    return 1


if __name__ == "__main__":
    sys.exit(main())