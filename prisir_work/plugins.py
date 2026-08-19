"""plugin 能力包格式(F6):把一个目录/包声明为一组能力,加载进能力注册表。

定位(见 prisirwork-foundation-integration-design §6 / F6):
- 能力包 = 一个目录,内含 plugin.json 声明 + 可选实现模块。
- 复用 F1 能力注册表:加载即调 capability.register_capability,不新造注册机制。
- 借 openwork Anthropic 兼容插件导入思路:声明式清单(manifest)+ 受控加载。

plugin.json schema(最小):
  {
    "name": "my-pack",              // 包名(唯一)
    "version": "0.1.0",
    "capabilities": [               // 每个能力声明
      {
        "id": "pack.thing",         // 能力 id(建议带包名前缀,防撞)
        "title": "一句话人话",
        "endpoint": "/pack/thing",  // 绑定的白名单端点(须已在 endpoints 注册)
        "method": "POST",           // 缺省 GET
        "risk": "L1",               // 缺省 L0
        "auth": true,               // 缺省 true
        "keywords": ["关键词"],      // 缺省 []
        "confirm": "确认卡提示语"     // risk>=L1 时给前端渲染用
      }
    ]
  }

红线:
- 只注册「endpoint 已在 endpoints 白名单」的能力;声明了未登记端点 → 跳过该条并记入 errors。
- 加载不执行任意代码:本格式只解析 plugin.json 声明,不 import 实现模块(实现仍走 endpoints)。
- 恶意/损坏 manifest 不拖垮常驻进程:单包失败只记错误,继续加载其余。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import capability, endpoints


def _valid_cap(c: dict) -> tuple[bool, str]:
    """校验单条能力声明。返回 (ok, error_reason)。"""
    if not isinstance(c, dict):
        return False, "cap_not_dict"
    if not c.get("id"):
        return False, "missing_id"
    if not c.get("endpoint"):
        return False, "missing_endpoint"
    # 红线:endpoint 必须已在白名单(不开放未登记路径)
    entry = endpoints.lookup(c["endpoint"])
    if not entry:
        return False, "endpoint_not_whitelisted"
    method = (c.get("method") or "GET").upper()
    if entry["method"] != method:
        return False, "method_mismatch"
    return True, ""


def load_plugin(plugin_dir: str | Path) -> dict:
    """加载一个能力包:解析 plugin.json,把合法能力注册进门面。

    返回 {ok, name, loaded: [cap_id...], errors: [str...]}。单条失败不阻断整包。
    """
    d = Path(plugin_dir)
    manifest = d / "plugin.json"
    if not manifest.exists():
        return {"ok": False, "error": "manifest_not_found", "dir": str(d)}
    try:
        spec = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": "manifest_bad_json", "detail": type(e).__name__}

    name = spec.get("name", d.name)
    caps = spec.get("capabilities", [])
    if not isinstance(caps, list):
        return {"ok": False, "error": "capabilities_not_list", "name": name}

    loaded, errors = [], []
    for c in caps:
        ok, why = _valid_cap(c)
        if not ok:
            errors.append(f"{c.get('id', '?')}: {why}")
            continue
        capability.register_capability(
            c["id"], title=c.get("title", c["id"]),
            endpoint=c["endpoint"], method=(c.get("method") or "GET").upper(),
            risk=c.get("risk", "L0"), auth=bool(c.get("auth", True)),
            keywords=tuple(c.get("keywords", [])), confirm=c.get("confirm", ""),
        )
        loaded.append(c["id"])
    return {"ok": True, "name": name, "loaded": loaded, "errors": errors}


def load_plugins_dir(plugins_root: str | Path) -> dict:
    """扫描一个目录,加载其下每个含 plugin.json 的子目录。返回汇总。"""
    root = Path(plugins_root)
    results, errors = [], []
    if not root.is_dir():
        return {"ok": False, "error": "plugins_root_not_dir", "root": str(root)}
    for sub in sorted(root.iterdir()):
        if sub.is_dir() and (sub / "plugin.json").exists():
            r = load_plugin(sub)
            results.append(r)
            if not r.get("ok"):
                errors.append(f"{sub.name}: {r.get('error')}")
    total = sum(len(r.get("loaded", [])) for r in results)
    return {"ok": True, "count": total, "packs": results, "errors": errors}
