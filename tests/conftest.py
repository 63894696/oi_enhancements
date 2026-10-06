# -*- coding: utf-8 -*-
"""tests/conftest.py — pytest 共享 fixture(2026-10-06 batch 3 ship)。

核心目的:防止 test 之间通过 sys.modules 注入 mock 污染后续 test。

污染链路示例(2026-10 排查):
  test_poster_capabilities.py:27 / test_free_for_dev_capabilities.py:24
    sys.modules["prisiragent_web"] = mock.MagicMock(name="prisiragent_web")
  ...
  test_preset_travel.py 看到 sys.modules["prisiragent_web"] 仍是 MagicMock
    → import prisiragent_web as W 拿到 MagicMock 实例
    → W._PRESET_KEYWORDS = [] (mock 默认 attribute)
    → W._preset_priority_block() = MagicMock
    → 8+2 个 fail 误报为真 fail。

修复:每次 test 结束后(autouse + yield 后 cleanup),从 sys.modules 弹出 mock 注入的
prisiragent_web / prisIragent_web,让下一个 test 走真实 import(或测试自己的 stub)。

注意:不能简单「每次 test 前 reimport 真 prisiragent_web」— 会拖大链路
(prisiragent_web.py 顶 import 30+ module) 进入不需要它的 test。
正确做法是「测试结束清理」:让污染源 test 在结束时显式还原,
但既然它们都没 try/finally,这里用 autouse fixture 兜底。

新策略:session 级 fixture 记录初始 sys.modules["prisiragent_web"] 状态,
每个 test 后强制 pop 让后续 test 自己重新 import(走 case-compat shim 拿到真模块)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest  # noqa: E402  # pytest fixture 装饰器需要

# 让 pytest 收集到 ROOT 路径
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 主动 import 大小写兼容 shim — 后续 test fixture 清理 MagicMock 后,
# import prisiragent_web / prisIragent_web 会通过 shim 拿到真模块。
import prisir_case_compat  # noqa: E402,F401
# 同样 import 真模块,锁定 session-scope 引用,fixture 清污染后能重建 sys.modules。
import prisiragent_web as _real_prisiragent_web  # noqa: E402,F401
import prisIragent_web as _real_prisIragent_web  # noqa: E402,F401


# 已知会被 mock 注入污染的 module 名(key 别名二选一)。
_POLLUTED_MODULES = ("prisiragent_web", "prisIragent_web")

# 这些 file 自身在 module 顶部就 stub prisiragent_web = MagicMock,
# 用 `mock.patch("prisiragent_web._ext_rpc_call", ..., create=True)` 模式跑。
# 它们不希望被 fixture 清理,否则下一个 test 在 sys.modules 找不到真模块。
# 解决:fixture 只清理 pollution 链源头之外(file 已显式注入的保留)。
# 用 request.node.parent.name 拿所属 file,白名单跳过。
_SKIP_CLEANUP_FILES = {
    "test_poster_capabilities.py",
    "test_free_for_dev_capabilities.py",
    # poster_to_image 顶部 stub prisiragent_cli,不是 prisiragent_web,不冲突
}


@pytest.fixture(autouse=True)
def _restore_prisiragent_web_after_test(request):
    """污染源 file(test_poster_capabilities / test_free_for_dev_capabilities)
    顶部 stub MagicMock 到 sys.modules["prisiragent_web"]。其他 test 看到
    MagicMock 会被误以为 _PRESET_KEYWORDS=[] 等。

    策略:
      - 每个 test 启动前(setup),如果 pollution file 留下 MagicMock,且本次
        test 不是 pollution file 本身,就 pop 掉 → 让 import 走真模块。
      - pollution file 自己保留 MagicMock(因为它们用 mock.patch(create=True))。
    """
    test_file = Path(request.node.location[0]).name
    is_pollution_source = test_file in _SKIP_CLEANUP_FILES
    # setup: 清掉 MagicMock 注入(除非本次 test 就是 pollution source),
    # 然后用 session 锁定的真 module 引用重建 sys.modules 别名。
    if not is_pollution_source:
        for mod_name in _POLLUTED_MODULES:
            cached = sys.modules.get(mod_name)
            if cached is not None and cached.__class__.__name__ == "MagicMock":
                sys.modules.pop(mod_name, None)
        # 重建真模块映射 — prisiragent_web ↔ prisIragent_web 是同一对象。
        sys.modules.setdefault("prisiragent_web", _real_prisiragent_web)
        sys.modules.setdefault("prisIragent_web", _real_prisiragent_web)
    yield
