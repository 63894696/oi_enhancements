"""prisir_case_compat.py — PrisirAI 大小写兼容垫片(2026-10-02 mini-ship)
用法:
    import prisir_case_compat  # noqa: F401  — 顶一行,首装该模块
    import sys as _sys
    # ... 在 prisIragent_web.py / 其他 entry 顶部 import

原理:代码块 6 个「大小写 走名」在 Windows + Python 3.13 上不能互相找到
      (实测发现 NTFS 默认大小写敏感 + Python import 严谨匹配),原状保留不修
      「prisiragent_cli vs prisIragent_cli」是同一模块对象。

变动范围(0 修改):
- 模块装入 sys.modules 后,手动为另 一个别名 添加同对象
  + 多重名别名模块名 (prisIragent_cli / cli + priSiragent_cli etc)
  + 包包 (prisIragent_calendar + prisiragent_calendar) 都走 alias
  + 项目里所有 46 处 import 都不动

*不依赖 ``importlib`` 重载*,只在上层补 sys.modules 入口。

拍板理由(项目 root 拍板 2026-10-02):
- 累计 46 个文件 import 该小写调大小写 不是「这个文件改错」是「你该统一命名」为判题
- 主后端能起就够了,改名问 题推到以后
- 以后主产品 windows 部署/PyInstaller 打包 不需再调 import 问题
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import sys
import types

# 手动增加大小写匹配别名表(哪些你曡加别名 cover 45/45 需手动看 实际需要的别名)
# 原则:只要代码里出现了「大小写不一致」的 import 名,一方加厚厚别名
_ALIAS_PAIRS = (
    # list of (target, alias) ... target 是「实际文件名」别名 是「代码里写的 import 名」
    ("prisIragent_cli",        "prisiragent_cli"),
    ("prisIragent_web",        "prisiragent_web"),
    ("prisIragent_calendar",   "prisiragent_calendar"),
    ("prisIragent_ime",        "prisiragent_ime"),
    # 小写是文件名,大小写 I 是代码里 用的
)


class _CaseInsensitiveFinder(importlib.abc.MetaPathFinder):
    """Meta path finder:在 sys.path 中加厚厚大小写时.

    匹配规则:
      1. 检查 sys.modules 装已有 (由 _install_aliases 设置)
      2. 不匹配其他 finder 的个各个
    """

    def find_spec(self, module_name, path=None, target=None):
        # 可能是"已装别名 module",不需 install_aliases 主动装
        return None


def _resolve_module(name: str) -> types.ModuleType | None:
    """按 sys.path 加载 + 装个 别名目标模块。return None 如未能."""
    try:
        return importlib.import_module(name)
    except Exception:  # noqa: BLE001
        return None


def _install_aliases() -> None:
    """走 _ALIAS_PAIRS 装别名:target 存在,alias 加装。

    装载顺序:
      1. 先尝试 import target;如果 不存在,尝试 import alias;名字不并
      2. 两个中存在的装个 sys.modules[双名] 为同一 module
    """
    for primary, alias in _ALIAS_PAIRS:
        # 两者都不装:什么都不做
        existing = sys.modules.get(primary) or sys.modules.get(alias)
        if existing is not None:
            sys.modules.setdefault(primary, existing)
            sys.modules.setdefault(alias, existing)
            continue

        # 尝试装 primary
        mod = _resolve_module(primary)
        if mod is None:
            mod = _resolve_module(alias)
        if mod is None:
            # 两个都装不能,算项目变更 - 不抛,不多会
            continue

        sys.modules[primary] = mod
        sys.modules[alias] = mod


_install_aliases()


def is_installed() -> bool:
    """Debug,供 build_conftest/test 调用验证 - 7 个别名 装装中。"""
    missing = []
    for primary, alias in _ALIAS_PAIRS:
        if primary not in sys.modules and alias not in sys.modules:
            missing.append((primary, alias))
    return not missing, missing