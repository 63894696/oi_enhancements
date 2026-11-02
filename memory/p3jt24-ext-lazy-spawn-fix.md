---
name: p3jt24-ext-lazy-spawn-fix
description: P3j T24 fix ship — _ext_rpc_call 加 lazy-spawn fallback,修 L0 资源扩展「找不到资源」bug
metadata:
  type: project
---

# P3j T24 — ext lazy-spawn fallback fix

**ship 时间**: 2026-10-03
**commit**: b386c88
**状态**: 全绿 — `tests/test_ext_lazy_spawn.py` 5 passed + 回归 `test_free_for_dev_capabilities.py` 11 passed + `_test_p2b0_ext_rpc_bridge.py` 通过,0.79s

## 用户反馈原话

> 「按你建议现在修」

(承「你的测试问题,回答上『找不到 free-for-dev 扩展的实际路径』这是特意设定的回答还是 bug?」 → 已识别为真 bug → 用户批准修。)

## bug 根因

主对话问「免费的 Postgres」,LLM 推 `[[EXEC: free.find query="postgres"]]` → `scan_and_exec` 调 `free_for_dev_capabilities.py:143` 的 handler → handler 调 `_ext_rpc_call("free-for-dev-promo", "free.find", ...)` → 第 456 行 `_EXT_PROCS` 没记录 → 直接返 `{"error": "ext_not_running: ..."}` → handler 降级 `ok=False` → LLM 答「找不到 free-for-dev 扩展的实际路径」。

L0 资源扩展(free-for-dev / public-apis / awesome-selfhosted / awesome-hub / public-apis-cn / n0shake-public-apis / handraw-style)在用户没显式 enable 时根本不会 spawn,而 `_ext_rpc_call` 见 `_EXT_PROCS` 缺失就直接 ext_not_running,100% 失败。

**为什么不是设计**:`_ext_rpc_call` 唯一职责是「发请求拿结果」。L0 资源扩展不应该让用户手动去 settings 里 enable 才能用 — 那等于「免费资源」类扩展不可用。已有 L14360 / L14387 / L14372 endpoint 各自加了 lock+spray 块(L14360-14378 / L14387 是 endpoint 层处理 `_EXT_PROCS` 缺失时的 spawn),证明这是个已知的「应该自动 spawn 但只做了部分」的状态。

## 修法(单点改 1 函数)

`prisIragent_web.py` `_ext_rpc_call` 第 456 行前插 lazy-spawn 段:

```python
with _EXT_BRIDGE_LOCK:
    st = _EXT_PROCS.get(ext_id)
    # ── lazy-spawn: 没起就尝试 spawn ──
    if not st or not st.get("proc") or st["proc"].poll() is not None:
        entry = _ext_entry(ext_id)
        if os.path.exists(entry):
            try:
                _ext_spawn(ext_id)        # 已有 spawn 函数,内置 lock + reader thread
                st = _EXT_PROCS.get(ext_id)
            except Exception as e:        # noqa: BLE001
                _LOGGER.warning("[ext-bridge] lazy-spawn %s failed: %s", ext_id, e)
    if not st or not st.get("proc") or st["proc"].poll() is not None:
        return {"error": f"ext_not_running: {ext_id}"}
```

**为什么 lock 安全**:`_EXT_BRIDGE_LOCK` 是 RLock(reentrant),`_ext_spawn` 内部 L324 也 `with _EXT_BRIDGE_LOCK:` —— 二次 take OK,不死锁。

**为什么不动 caller**:9 个 caller(L1372/1390/1408/2901/2910/488/14372/14388 + `free_for_dev_capabilities.py:143`)契约不变(返 `{"result":...}` 或 `{"error":...}`),自动受益。

**为什么不动 `_ext_spawn`**:它已经会自己处理 alive check(`if st and st.get("proc") and st["proc"].poll() is None: return`)—— lazy-spawn 重复调是幂等的,不会双 spawn。

## 测试 (5 用例)

`tests/test_ext_lazy_spawn.py`:

1. `test_1_lazy_spawn_when_proc_missing` — happy path: 缺失 → spawn → 拿 result
2. `test_2_lazy_spawn_entry_not_found` — 入口不存在 → 不 spawn → ext_not_running
3. `test_3_lazy_spawn_spawn_exception` — spawn 抛 OSError → graceful ext_not_running
4. `test_4_no_double_spawn_when_alive` — 已 alive → 不再 lazy spawn
5. `test_5_lazy_spawn_after_crash` — 已 dead → lazy-spawn 替代

### 测试基础设施坑

**坑 1: NTFS case-folding + Python 严格 import**
`prisIragent_web.py`(大写 I)是文件名,代码里写 `import prisiragent_web`(小写 i)。Windows 大小写不敏感,但 Python 严格匹配 sys.modules key。`prisir_case_compat` 用 alias 把两个 key 都指向同一 module —— OK。

**坑 2: 别 test 的 module 级 mock 污染**
`tests/test_free_for_dev_capabilities.py` 第 24-25 行:
```python
sys.modules["prisiragent_web"] = mock.MagicMock(name="prisiragent_web")
sys.modules["prisiragent_web"]._ext_rpc_call = _fake_ext_rpc_call
```
粗暴 stub 整个 module,污染小写 key。真 module 仍在 `sys.modules["prisIragent_web"]`,但 alias 关系被打破。

**修法**(在 setUp 里强制重建):
```python
for k in list(sys.modules):
    kl = k.lower()
    if kl in ("prisIragent_web", "prisiragent_web"):
        del sys.modules[k]
import importlib as _il
_il.reload(_il.import_module("prisir_case_compat"))  # alias 重装
import prisiragent_web as W
```

`importleak` reload 是关键 —— 否则 `import prisiragent_web` 拿到 MagicMock。

**坑 3: fake proc reader thread 在 Windows pipe 上不可靠**
`_FakeStdout` 用 `Condition` 阻塞 readline,driver 线程用 `select` 唤醒。Windows pipe `select()` 行为不一致,driver 起不来。

**修法**: 改成同步路径。`stdin.write` 时立刻调用 `on_write` callback,在 callback 内写 box + ev.set —— 不需要 reader 线程 / select / 异步 driver。`box["result"]` 镜像 `_ext_reader_loop` L411:`box["result"] = msg.get("result")`。

## 端到端验证(已跑)

```bash
$ python -m pytest tests/test_ext_lazy_spawn.py tests/test_free_for_dev_capabilities.py \
                  tests/_test_p2b0_ext_rpc_bridge.py -q
16 passed in 0.79s
```

## 复用 vs 新增

**复用**(0 修改):
- `_ext_spawn` (prisIragent_web.py:322) — Popen + env 注入 + reader thread + 崩溃自动重启
- `_ext_entry` (prisIragent_web.py:310) — 入口路径解析
- `_EXT_PROCS`, `_EXT_BRIDGE_LOCK`, `_LOGGER` — 已有

**新增**:
- `prisIragent_web.py` 改 19 行(`_ext_rpc_call` 内 lazy-spawn 段)
- `tests/test_ext_lazy_spawn.py` 新增 296 行(5 用例 + 3 fake 辅助类)

**总计**: 1 处代码改 + 1 测试 / 315 行 / 1 commit。

## 风险与接缝

1. **装包态 spawn 失败**: `_ext_entry` L313-318 已用相对 + abspath 兜底,装包态(sys._MEIPASS)走 abspath 应该 OK
2. **sandbox 不允许 spawn Node**: graceful 降级 `{"error": "ext_not_running: ..."}` 不 crash
3. **并发 spawn 同 ext**: RLock 防重入 + 现有 `_ext_spawn` 内部幂等检查,不会双 spawn
4. **不破坏已有 caller**: `_ext_rpc_call` 返回 schema 没变,所有调用点契约不变

**Why**: 真 bug — L0 资源扩展没手动 enable 就出资源列表能力 100% 失败率。lazy-spawn 一行改全链路 9 个调用点受益,主对话从「找不到资源」变「真出资源卡片」。

**How to apply**: 任何新加的 ext RPC caller 不必再担心「ext 是否已启」,直接 `_ext_rpc_call(ext_id, ...)` 即可。Phase A 是 ship 兜底,Phase B/C 才是 UI/UX 改进(类似 ext-inventory 注 2 + settings 页 ENABLE 按钮)。

## 相关 commit 链

- 2026-10-03 commit b386c88 — fix(ext-bridge): _ext_rpc_call 加 lazy-spawn fallback(本 plan)
- 2026-10-02 commit 65135d2 — ext inventory 加权限/本地数据提示(同 working tree,前序 ship)
- 2026-10-02 commit d087817 — ext modal + system 文案改写(同 working tree,前序 ship)
- 2026-10-02 commit 153e7c9 — ext inventory 注入 system prompt(同 working tree,前序 ship)