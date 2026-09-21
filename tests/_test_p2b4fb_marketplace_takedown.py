"""P2.5+B-4.F.B 运营撤下 + 撤回通知 + 原因模板 + 批量撤下 静态扫 + 编译检查
(2026-09-22)

覆盖:
  S1: marketplace ext operator 身份 helper(_operatorKeyPath / _loadOperatorIdentity)
  S2: marketplace ext market.operator_identity 命令注册
  S3: marketplace ext market.take_down 命令(验运营者 + 早失败 + 拼 canon 签名 + 发 t='takedown')
  S4: marketplace ext market.take_down_batch 命令(批量 + 单条失败不中断)
  S5: marketplace ext market.list include_retracted=true 模式透传 retracted_by / taken_down_by / status
  S6: marketplace ext package.json 新增 3 权限(operator_identity / take_down / take_down_batch)
  S7: cli workflow_market_takedown tool def + handler + dispatch
  S8: cli workflow_market_takedown_batch tool def + handler + dispatch
  S9: cli workflow_market_operator_identity tool def + handler + dispatch
  S10: cli _subagent_tools 剔除 takedown + takedown_batch(operator_identity 保留只读)
  S11: web wfMarketPickReason(5 预设 + 自填输入,retract/takedown 双模式)
  S12: web wfMarketTakedown 单条撤下函数
  S13: web wfMarketTakedownBatch 批量撤下函数
  S14: web wfMarketRefresh 加 opts 参数 + include_retracted + batchMode + isOperator 横幅
  S15: web 2 个新 modal HTML(#wf-market-reason-modal / #wf-market-batch-modal)
  S16: web i18n keys ZH(11 个新 key)
  S17: web i18n keys EN(11 个新 key)
  S18: py_compile cli/web + node --check marketplace ext
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
    print("P2.5+B-4.F.B takedown + notification + reason + batch 静态扫")
    print("=" * 60)
    passes = 0; total = 0

    # ─── S1: operator 身份 helper ───
    print("\n[S1] marketplace ext operator 身份 helper")
    total += 1; passes += check("_operatorKeyPath() 函数定义",
        'function _operatorKeyPath()' in ext_src)
    total += 1; passes += check("env PRISIR_FORUM_OPERATOR_KEY_PATH 支持",
        'PRISIR_FORUM_OPERATOR_KEY_PATH' in ext_src)
    total += 1; passes += check("默认路径 ~/.prisir/forum_operator.key",
        "'forum_operator.key'" in ext_src)
    total += 1; passes += check("_loadOperatorIdentity 函数",
        'function _loadOperatorIdentity()' in ext_src)
    total += 1; passes += check("Ed25519 私钥重建(seed 32 字节 → PKCS8 DER)",
        '302e020100300506032b657004220420' in ext_src.split('function _loadOperatorIdentity')[1][:1500])

    # ─── S2: market.operator_identity 命令 ───
    print("\n[S2] market.operator_identity 命令")
    op_section = ext_src.split("ext.registerCommand('market.operator_identity'")[1][:2500] if "ext.registerCommand('market.operator_identity'" in ext_src else ''
    total += 1; passes += check("命令注册",
        "ext.registerCommand('market.operator_identity'" in ext_src)
    total += 1; passes += check("调 _loadOperatorIdentity()",
        '_loadOperatorIdentity()' in op_section)
    total += 1; passes += check("非运营者返 is_operator=false + hint",
        'is_operator: false' in op_section and 'hint:' in op_section)
    total += 1; passes += check("运营者返 pub + fp",
        'is_operator: true' in op_section and 'pub,' in op_section and 'fp,' in op_section)

    # ─── S3: market.take_down 命令 ───
    print("\n[S3] market.take_down 命令")
    td_section = ext_src.split("ext.registerCommand('market.take_down'")[1][:5000] if "ext.registerCommand('market.take_down'" in ext_src else ''
    total += 1; passes += check("命令注册",
        "ext.registerCommand('market.take_down'" in ext_src)
    total += 1; passes += check("post_id 必填",
        'post_id required' in td_section and '!post_id' in td_section)
    total += 1; passes += check("非运营者返 not_operator",
        "'not_operator'" in td_section)
    total += 1; passes += check("早失败:post_not_found",
        "'post_not_found'" in td_section)
    total += 1; passes += check("早失败:already_taken_down",
        "'already_taken_down'" in td_section)
    total += 1; passes += check("拼 canon({post_id, reason, ts}) 签名",
        '_canon({ post_id, reason, ts })' in td_section and 'crypto.sign' in td_section)
    total += 1; passes += check("发 t='takedown' 帧",
        "type: 'takedown'" in td_section)
    total += 1; passes += check("无 PoW(takedown 是信任根)",
        '_solvePow' not in td_section)

    # ─── S4: market.take_down_batch ───
    print("\n[S4] market.take_down_batch 命令")
    tdb_section = ext_src.split("ext.registerCommand('market.take_down_batch'")[1][:5000] if "ext.registerCommand('market.take_down_batch'" in ext_src else ''
    total += 1; passes += check("命令注册",
        "ext.registerCommand('market.take_down_batch'" in ext_src)
    total += 1; passes += check("post_ids 数组校验",
        'post_ids array required' in tdb_section and 'Array.isArray' in tdb_section)
    total += 1; passes += check("上限 50 条",
        'too_many: max 50' in tdb_section)
    total += 1; passes += check("每条独立 try/catch",
        'try {' in tdb_section and 'catch' in tdb_section)
    total += 1; passes += check("返 succeeded/failed/total/results",
        'succeeded' in tdb_section and 'failed' in tdb_section and 'total' in tdb_section and 'results' in tdb_section)

    # ─── S5: market.list include_retracted 模式 ───
    print("\n[S5] market.list include_retracted 模式")
    ml_section = ext_src.split("ext.registerCommand('market.list'")[1][:5000] if "ext.registerCommand('market.list'" in ext_src else ''
    total += 1; passes += check("include_retracted 参数读取",
        'includeRetracted' in ml_section and 'include_retracted' in ml_section)
    total += 1; passes += check("include_retracted 模式不过滤已撤",
        'includeRetracted || (!m.taken_down && !m.retracted)' in ml_section)
    total += 1; passes += check("status 字段",
        "out.status = 'retracted'" in ml_section and "out.status = 'taken_down'" in ml_section and "out.status = 'live'" in ml_section)
    total += 1; passes += check("retracted_by 字段(自删)",
        'out.retracted_by' in ml_section and 'p.author_fp' in ml_section)
    total += 1; passes += check("taken_down_by 字段(运营)",
        'out.taken_down_by' in ml_section and "'operator'" in ml_section)

    # ─── S6: package.json 新增 3 权限 ───
    print("\n[S6] package.json 新增 3 权限")
    total += 1; passes += check("market.operator_identity 权限",
        'ai.invoke.command:market.operator_identity' in ext_pkg)
    total += 1; passes += check("market.take_down 权限",
        'ai.invoke.command:market.take_down' in ext_pkg)
    total += 1; passes += check("market.take_down_batch 权限",
        'ai.invoke.command:market.take_down_batch' in ext_pkg)

    # ─── S7: cli workflow_market_takedown ───
    print("\n[S7] cli workflow_market_takedown")
    ttd_section = cli_src.split('def _t_workflow_market_takedown')[1][:3000] if 'def _t_workflow_market_takedown' in cli_src else ''
    total += 1; passes += check("handler 函数存在",
        'def _t_workflow_market_takedown' in cli_src)
    total += 1; passes += check("tool def 存在",
        '"name": "workflow_market_takedown"' in cli_src)
    total += 1; passes += check("参数 post_id 必填",
        '"post_id"' in cli_src.split('"name": "workflow_market_takedown"')[1][:1500])
    total += 1; passes += check("调 marketplace market.take_down",
        'market.take_down' in ttd_section and '_ext_rpc_call' in ttd_section)
    total += 1; passes += check("透传 not_operator + key_path",
        'not_operator' in ttd_section and 'key_path' in ttd_section)
    total += 1; passes += check("dispatch 路由",
        'name == "workflow_market_takedown"' in cli_src)

    # ─── S8: cli workflow_market_takedown_batch ───
    print("\n[S8] cli workflow_market_takedown_batch")
    ttdb_section = cli_src.split('def _t_workflow_market_takedown_batch')[1][:3000] if 'def _t_workflow_market_takedown_batch' in cli_src else ''
    total += 1; passes += check("handler 函数存在",
        'def _t_workflow_market_takedown_batch' in cli_src)
    total += 1; passes += check("tool def 存在",
        '"name": "workflow_market_takedown_batch"' in cli_src)
    total += 1; passes += check("post_ids 数组参数",
        '"post_ids"' in cli_src.split('"name": "workflow_market_takedown_batch"')[1][:1500])
    total += 1; passes += check("调 marketplace market.take_down_batch",
        'market.take_down_batch' in ttdb_section)
    total += 1; passes += check("透传 succeeded/failed/results",
        'succeeded' in ttdb_section and 'failed' in ttdb_section and 'results' in ttdb_section)
    total += 1; passes += check("dispatch 路由",
        'name == "workflow_market_takedown_batch"' in cli_src)

    # ─── S9: cli workflow_market_operator_identity ───
    print("\n[S9] cli workflow_market_operator_identity")
    toi_section = cli_src.split('def _t_workflow_market_operator_identity')[1][:1500] if 'def _t_workflow_market_operator_identity' in cli_src else ''
    total += 1; passes += check("handler 函数存在",
        'def _t_workflow_market_operator_identity' in cli_src)
    total += 1; passes += check("tool def 存在",
        '"name": "workflow_market_operator_identity"' in cli_src)
    total += 1; passes += check("调 marketplace market.operator_identity",
        'market.operator_identity' in toi_section)
    total += 1; passes += check("dispatch 路由",
        'name == "workflow_market_operator_identity"' in cli_src)

    # ─── S10: cli _subagent_tools 剔除 ───
    print("\n[S10] cli _subagent_tools 剔除 takedown")
    sub_section = cli_src.split('_subagent_tools')[1][:3000] if '_subagent_tools' in cli_src else ''
    total += 1; passes += check("剔除 workflow_market_takedown",
        '"workflow_market_takedown"' in sub_section)
    total += 1; passes += check("剔除 workflow_market_takedown_batch",
        '"workflow_market_takedown_batch"' in sub_section)
    total += 1; passes += check("operator_identity 不剔除(只读可派子代)",
        '"workflow_market_operator_identity"' not in sub_section)

    # ─── S11: web wfMarketPickReason ───
    print("\n[S11] web wfMarketPickReason(5 预设 + 自填)")
    pr_section = web_src.split('async function wfMarketPickReason')[1][:3000] if 'async function wfMarketPickReason' in web_src else ''
    total += 1; passes += check("函数定义存在",
        'async function wfMarketPickReason(mode)' in web_src)
    total += 1; passes += check("retract 5 预设(中文)",
        "'[误发]'" in pr_section and "'[重复]'" in pr_section and
        "'[已更新到新版本]'" in pr_section and "'[测试]'" in pr_section)
    total += 1; passes += check("takedown 5 预设(英文)",
        "'[spam]'" in pr_section and "'[illegal]'" in pr_section and
        "'[harassment]'" in pr_section and "'[off-topic]'" in pr_section)
    total += 1; passes += check("返回 Promise(可 await)",
        'new Promise((resolve)' in pr_section)
    total += 1; passes += check("自填输入框联动",
        'wfMarketReasonSelect' in web_src)

    # ─── S12: web wfMarketTakedown 单条 ───
    print("\n[S12] web wfMarketTakedown 单条")
    wtd_section = web_src.split('async function wfMarketTakedown')[1][:3000] if 'async function wfMarketTakedown' in web_src else ''
    total += 1; passes += check("函数定义存在",
        'async function wfMarketTakedown(postId)' in web_src)
    total += 1; passes += check("调 wfMarketPickReason('takedown')",
        "wfMarketPickReason('takedown')" in wtd_section)
    total += 1; passes += check("confirm 二次确认",
        'confirm(' in wtd_section)
    total += 1; passes += check("调 ext_id marketplace market.take_down",
        'market.take_down' in wtd_section)
    total += 1; passes += check("失败带 hint 显示",
        'r.result.hint' in wtd_section)

    # ─── S13: web wfMarketTakedownBatch 批量 ───
    print("\n[S13] web wfMarketTakedownBatch 批量")
    wb_section = web_src.split('async function wfMarketTakedownBatch')[1][:3000] if 'async function wfMarketTakedownBatch' in web_src else ''
    total += 1; passes += check("函数定义存在",
        'async function wfMarketTakedownBatch()' in web_src)
    total += 1; passes += check("调 market.take_down_batch",
        'market.take_down_batch' in wb_section)
    total += 1; passes += check("勾选 .wf-market-batch-check 拿 post_ids",
        "querySelectorAll('#wf-market-batch-list .wf-market-batch-check:checked')" in wb_section)
    total += 1; passes += check("成功后 2s 自动关闭 modal",
        "wf-market-batch-modal').classList.remove('open')" in wb_section)

    # ─── S14: web wfMarketRefresh 加 opts ───
    print("\n[S14] web wfMarketRefresh 加 opts 参数")
    rf_section = web_src.split('async function wfMarketRefresh')[1][:5000] if 'async function wfMarketRefresh' in web_src else ''
    total += 1; passes += check("opts 参数",
        'async function wfMarketRefresh(opts)' in web_src)
    total += 1; passes += check("includeRetracted / batchMode 解析",
        'includeRetracted' in rf_section and 'batchMode' in rf_section)
    total += 1; passes += check("include_retracted:true 传给 marketplace",
        'include_retracted: true' in rf_section)
    total += 1; passes += check("查 operator_identity 决定 isOperator",
        'market.operator_identity' in rf_section and 'isOperator' in rf_section)
    total += 1; passes += check("isOperator 横幅",
        '当前是 marketplace 运营者' in rf_section or '⚙️' in rf_section)
    total += 1; passes += check("运营者行 🚫 撤下按钮",
        'wfMarketTakedown(' in rf_section and '🚫 撤下' in rf_section)
    total += 1; passes += check("已撤下横幅 ⚠ 已自删 / ⚠ 运营撤下",
        "⚠ 已自删" in rf_section and "⚠ 运营撤下" in rf_section)
    total += 1; passes += check("status 字段判断",
        "p.status === 'retracted'" in rf_section and "p.status === 'taken_down'" in rf_section)
    total += 1; passes += check("已撤下的行禁用下载/撤下按钮",
        "isRetracted" in rf_section and "isTakenDown" in rf_section and "!isRetracted && !isTakenDown" in rf_section)

    # ─── S15: web 2 个新 modal ───
    print("\n[S15] web 2 个新 modal HTML")
    total += 1; passes += check("reason modal #wf-market-reason-modal",
        'id="wf-market-reason-modal"' in web_src)
    total += 1; passes += check("reason modal select + textarea",
        'id="wf-market-reason-select"' in web_src and 'id="wf-market-reason-text"' in web_src)
    total += 1; passes += check("batch modal #wf-market-batch-modal",
        'id="wf-market-batch-modal"' in web_src)
    total += 1; passes += check("batch modal list + reason + status",
        'id="wf-market-batch-list"' in web_src and 'id="wf-market-batch-reason-text"' in web_src and
        'id="wf-market-batch-status"' in web_src)
    total += 1; passes += check("wfMarketCancel 关 4 个 modal",
        "wf-market-modal').classList.remove('open')" in web_src.split('function wfMarketCancel')[1][:500] and
        "wf-publish-modal" in web_src.split('function wfMarketCancel')[1][:500] and
        "wf-market-reason-modal" in web_src.split('function wfMarketCancel')[1][:500] and
        "wf-market-batch-modal" in web_src.split('function wfMarketCancel')[1][:500])

    # ─── S16: i18n ZH 11 keys ───
    print("\n[S16] web i18n ZH 11 keys")
    zh_idx = web_src.find("wf_market_retract_confirm:'确认自删")
    zh_block = web_src[zh_idx:zh_idx+2500] if zh_idx >= 0 else ''
    zh_keys = ['wf_market_takedown_confirm', 'wf_market_takedown_ok', 'wf_market_takedown_fail',
               'wf_market_takedown_batch', 'wf_market_takedown_batch_title',
               'wf_market_takedown_batch_hint', 'wf_market_takedown_reason',
               'wf_market_takedown_apply', 'wf_market_takedown_reasons',
               'wf_market_retract_reasons']
    for k in zh_keys:
        total += 1; passes += check(f"ZH: {k}", f"{k}:" in zh_block)

    # ─── S17: i18n EN 11 keys ───
    print("\n[S17] web i18n EN 11 keys")
    en_idx = web_src.find("wf_market_retract_confirm:'Confirm self-delete")
    en_block = web_src[en_idx:en_idx+2500] if en_idx >= 0 else ''
    en_keys = ['wf_market_takedown_confirm', 'wf_market_takedown_ok', 'wf_market_takedown_fail',
               'wf_market_takedown_batch', 'wf_market_takedown_batch_title',
               'wf_market_takedown_batch_hint', 'wf_market_takedown_reason',
               'wf_market_takedown_apply', 'wf_market_takedown_reasons',
               'wf_market_retract_reasons']
    for k in en_keys:
        total += 1; passes += check(f"EN: {k}", f"{k}:" in en_block)

    # ─── S18: 编译检查 ───
    print("\n[S18] 编译检查")
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
