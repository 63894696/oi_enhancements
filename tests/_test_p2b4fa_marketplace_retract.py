"""P2.5+B-4.F.A market.retract + workflow_market_retract 静态扫 + 编译检查
(2026-09-21)

覆盖:
  S1: marketplace ext market.retract 命令注册 + _ensureConnected + 早失败校验
  S2: marketplace ext retract 帧 kind/parent/author_fp/pow 字段
  S3: marketplace ext retract error cases(not_your_post / already_retracted / post_not_found)
  S4: marketplace ext package.json permission
  S5: cli workflow_market_retract tool def(描述 + post_id + reason)
  S6: cli _t_workflow_market_retract handler 存在 + 调 _ext_rpc_call marketplace
  S7: cli _subagent_tools 排除列表含 workflow_market_retract
  S8: cli dispatch 路由 workflow_market_retract
  S9: web wfMarketRetract JS 函数存在(confirm + prompt + RPC + 错误处理)
  S10: web wfMarketRefresh 拿到 myFp 后渲染 🗑️ 按钮 + ★ 标记
  S11: web i18n keys 中文(wf_market_retract_* 4 key)
  S12: web i18n keys 英文(wf_market_retract_* 4 key)
  S13: py_compile cli/web + node --check marketplace ext
"""
import os, re, sys, subprocess, py_compile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, 'prisIragent_cli.py')
WEB = os.path.join(ROOT, 'prisIragent_web.py')
EXT_JS = os.path.join(ROOT, 'extensions', 'marketplace', 'index.js')
EXT_PKG = os.path.join(ROOT, 'extensions', 'marketplace', 'package.json')

def read(p):
    with open(p, 'r', encoding='utf-8') as f:
        return f.read()

def check(name, ok, detail=''):
    mark = '✓' if ok else '✗'
    print(f"  [{mark}] {name}{(': ' + detail) if detail else ''}")
    return 1 if ok else 0

def main():
    cli_src = read(CLI)
    web_src = read(WEB)
    ext_src = read(EXT_JS)
    ext_pkg = read(EXT_PKG)
    print("=" * 60)
    print("P2.5+B-4.F.A market.retract 静态扫")
    print("=" * 60)
    passes = 0; total = 0

    # ─── S1: marketplace ext market.retract 命令注册 + _ensureConnected + 早失败 ─
    print("\n[S1] marketplace ext market.retract 命令注册")
    cmd_section = ext_src.split("ext.registerCommand('market.retract'")[1][:5000]
    total += 1; passes += check("命令注册存在",
        "ext.registerCommand('market.retract'" in ext_src)
    total += 1; passes += check("调 _ensureConnected()",
        "_ensureConnected()" in cmd_section)
    total += 1; passes += check("post_id 必填校验",
        'post_id required' in cmd_section and '!post_id' in cmd_section)
    total += 1; passes += check("reason 截 200 字符",
        '.slice(0, 200)' in cmd_section)
    total += 1; passes += check("早失败:not_your_post",
        "'not_your_post'" in cmd_section and 'author_fp' in cmd_section and '_authorFp' in cmd_section)
    total += 1; passes += check("早失败:already_retracted / already_taken_down",
        "'already_retracted'" in cmd_section and "'already_taken_down'" in cmd_section)
    total += 1; passes += check("早失败:post_not_found",
        "'post_not_found'" in cmd_section)
    total += 1; passes += check("早失败返回 hint + your_fp + post_author_fp",
        'hint:' in cmd_section and 'your_fp:' in cmd_section and 'post_author_fp:' in cmd_section)

    # ─── S2: retract 帧 kind/parent/author_fp/pow 字段 ───
    print("\n[S2] retract 帧 schema")
    total += 1; passes += check("kind='retract'",
        "kind: 'retract'" in cmd_section)
    total += 1; passes += check("parent=post_id",
        'parent: post_id' in cmd_section)
    total += 1; passes += check("body 含 [retracted] 前缀",
        "[retracted]" in cmd_section)
    total += 1; passes += check("author_pub/author_fp 注入",
        'author_pub: _authorPub' in cmd_section and 'author_fp: _authorFp' in cmd_section)
    total += 1; passes += check("PoW 算力调用 _solvePow",
        '_solvePow(signedView, POW_BITS)' in cmd_section)
    total += 1; passes += check("签名调用 crypto.sign",
        'crypto.sign(null' in cmd_section and '_identity' in cmd_section)
    total += 1; passes += check("发 post 帧 _wsRequest",
        "_wsRequest({ type: 'post', post: postObj }, 30000)" in cmd_section)

    # ─── S3: retract 错误返回 forum_nack ───
    print("\n[S3] retract 错误返回处理")
    total += 1; passes += check("forum_nack 错误",
        "'forum_nack:'" in cmd_section)
    total += 1; passes += check("返回 retract_post 字段供调试",
        'retract_post: postObj' in cmd_section)
    total += 1; passes += check("成功返 retracted_at + reason",
        'retracted_at: ts' in cmd_section and 'reason: body' in cmd_section)

    # ─── S4: package.json permission ───
    print("\n[S4] package.json permissions")
    total += 1; passes += check("ai.invoke.command:market.retract 在权限列表",
        'ai.invoke.command:market.retract' in ext_pkg)

    # ─── S5: cli workflow_market_retract tool def ───
    print("\n[S5] cli workflow_market_retract tool def")
    # 用第二个 occurrence(第一个是 comment/或别的,tool def 在 line ~3097)
    parts = cli_src.split('"workflow_market_retract"')
    tool_def_section = parts[2][:1500] if len(parts) >= 3 else (parts[1][:1500] if len(parts) >= 2 else '')
    total += 1; passes += check("tool def 存在",
        '"name": "workflow_market_retract"' in cli_src)
    total += 1; passes += check("描述提到 post_id / retract / 自删",
        'retract' in tool_def_section.lower() and 'post_id' in tool_def_section)
    total += 1; passes += check("参数 post_id 必填",
        '"post_id"' in tool_def_section and '"required"' in tool_def_section)
    total += 1; passes += check("参数 reason 可选",
        '"reason"' in tool_def_section)

    # ─── S6: cli _t_workflow_market_retract handler ───
    print("\n[S6] cli _t_workflow_market_retract handler")
    handler_section = cli_src.split('def _t_workflow_market_retract')[1][:3000] if 'def _t_workflow_market_retract' in cli_src else ''
    total += 1; passes += check("handler 函数存在",
        'def _t_workflow_market_retract' in cli_src)
    total += 1; passes += check("post_id 必填校验",
        '需要 post_id' in handler_section or 'post_id required' in handler_section)
    total += 1; passes += check("reason 截 200 字符",
        '[:200]' in handler_section)
    total += 1; passes += check("调 _ext_rpc_call marketplace market.retract",
        '_ext_rpc_call' in handler_section and '"marketplace"' in handler_section and 'market.retract' in handler_section)
    total += 1; passes += check("timeout=30",
        'timeout=30' in handler_section)
    total += 1; passes += check("错误带 hint/your_fp/post_author_fp",
        'hint' in handler_section and 'your_fp' in handler_section)

    # ─── S7: cli _subagent_tools 排除 workflow_market_retract ───
    print("\n[S7] 子代工具集排除 workflow_market_retract")
    subagent_section = cli_src.split('_subagent_tools')[1][:3000] if '_subagent_tools' in cli_src else ''
    total += 1; passes += check("剔除列表含 workflow_market_retract",
        '"workflow_market_retract"' in subagent_section)
    total += 1; passes += check("剔除列表也含 workflow_market_publish(B-4.F)",
        '"workflow_market_publish"' in subagent_section)

    # ─── S8: cli dispatch 路由 ───
    print("\n[S8] cli dispatch 路由")
    total += 1; passes += check('name == "workflow_market_retract" 分支',
        'name == "workflow_market_retract"' in cli_src)

    # ─── S9: web wfMarketRetract JS 函数 ───
    print("\n[S9] web wfMarketRetract JS 函数")
    retract_section = web_src.split('async function wfMarketRetract')[1][:3000] if 'async function wfMarketRetract' in web_src else ''
    total += 1; passes += check("函数定义存在",
        'async function wfMarketRetract(' in web_src)
    total += 1; passes += check("confirm 二次确认",
        'confirm(' in retract_section)
    # P2.5+B-4.F.B(2026-09-22):retract 走 wfMarketPickReason 模板 dropdown,不再用裸 prompt()
    total += 1; passes += check("调 wfMarketPickReason 拿 reason",
        'wfMarketPickReason(' in retract_section)
    total += 1; passes += check("调 ext_id:marketplace / market.retract",
        'ext_id' in retract_section and 'market.retract' in retract_section and 'marketplace' in retract_section)
    total += 1; passes += check("timeout:30",
        'timeout:30' in retract_section)
    total += 1; passes += check("错误带 hint/your_fp/post_author_fp 展示",
        'hint' in retract_section and 'your_fp' in retract_section and 'post_author_fp' in retract_section)
    total += 1; passes += check("成功后 wfMarketRefresh 刷新列表",
        'wfMarketRefresh()' in retract_section)
    total += 1; passes += check("成功后状态文案写 wf_market_retracted",
        'wf_market_retracted' in retract_section)

    # ─── S10: web wfMarketRefresh 拿 myFp + 🗑️ 按钮 + ★ 标记 ───
    print("\n[S10] web wfMarketRefresh 拿 myFp + 🗑️ 按钮")
    refresh_section = web_src.split('async function wfMarketRefresh')[1][:5000] if 'async function wfMarketRefresh' in web_src else ''
    total += 1; passes += check("调用 market.identity 拿自己 fp",
        'market.identity' in refresh_section and 'fp' in refresh_section)
    total += 1; passes += check("isMine 判定 author_fp === myFp",
        'isMine' in refresh_section and 'myFp' in refresh_section)
    total += 1; passes += check("渲染 🗑️ 自删按钮(own only)",
        '🗑️' in refresh_section and 'wfMarketRetract(' in refresh_section)
    total += 1; passes += check("渲染 ★ 标记 own only",
        '★' in refresh_section and 'isMine' in refresh_section)

    # ─── S11: web i18n zh ───
    print("\n[S11] web i18n ZH 4 keys")
    # 找 ZH 锚点 wf_market_title_label:'标题'(前面就有)
    zh_idx = web_src.find("wf_market_title_label:'标题'")
    zh_block_text = web_src[zh_idx:zh_idx+1500] if zh_idx >= 0 else ''
    for k in ['wf_market_retract_confirm', 'wf_market_retract_reason_prompt',
              'wf_market_retracted', 'wf_market_retract_fail']:
        total += 1; passes += check(f"ZH: {k}", f"{k}:" in zh_block_text)

    # ─── S12: web i18n en ───
    print("\n[S12] web i18n EN 4 keys")
    # 找 EN 段(以 "wf_market_list:'🌐 Browse remote" 为锚)
    en_anchor = web_src.find("wf_market_list:'🌐 Browse remote")
    en_block = web_src[en_anchor:en_anchor+2000] if en_anchor >= 0 else ''
    for k in ['wf_market_retract_confirm', 'wf_market_retract_reason_prompt',
              'wf_market_retracted', 'wf_market_retract_fail']:
        total += 1; passes += check(f"EN: {k}", f"{k}:" in en_block)

    # ─── S13: 编译检查 ───
    print("\n[S13] 编译检查")
    try:
        py_compile.compile(CLI, doraise=True); total += 1; passes += check("cli py_compile OK", True)
    except Exception as e:
        total += 1; passes += check(f"cli py_compile FAIL: {e}", False)
    try:
        py_compile.compile(WEB, doraise=True); total += 1; passes += check("web py_compile OK", True)
    except Exception as e:
        total += 1; passes += check(f"web py_compile FAIL: {e}", False)
    try:
        r = subprocess.run(['node', '--check', EXT_JS], capture_output=True, text=True, timeout=10)
        total += 1; passes += check(f"marketplace node --check ({r.returncode})", r.returncode == 0)
    except Exception as e:
        total += 1; passes += check(f"marketplace node --check fail: {e}", False)

    print("\n" + "=" * 60)
    print(f"Result: {passes}/{total} checks passed")
    print("=" * 60)
    return 0 if passes == total else 1

if __name__ == '__main__':
    sys.exit(main())
