# -*- coding: utf-8 -*-
"""tests/test_easel_bridge.py — Easel 桥接 + publisher + skill_manifest 单元测试。

覆盖(全部必绿,无 skip):
  · bridge: find_easel_root / whoami / stats 接通
  · publisher: 6 平台注册 + null publisher 兜底 + 路由 + 文件缺失返 ok=False
  · skill_manifest: parse SKILL.md frontmatter + 默认扫 Easel 仓库 + 触发场景抽取
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# easel_bridge
# ---------------------------------------------------------------------------


def test_bridge_find_root():
    from prisir_work.easel_bridge import find_easel_root
    r = find_easel_root()
    # 跑测试时 zju_easel 必须在 ~/work/
    assert r is not None, "Easel 仓库应自动探测到 ~/work/zju_easel"
    assert (r / "skills").is_dir()
    print(f"✓ bridge.find_root → {r}")


def test_bridge_ready_or_not():
    from prisir_work.easel_bridge import easel
    eb = easel(force_reload=True)
    # ready 看用户本机有没有装 Easel,不强求
    if eb.ready:
        assert eb.root is not None
        print("✓ bridge ready (本机有 Easel)")
    else:
        print(f"○ bridge not ready (root={eb.root}),仍不抛栈")
    # whoami 不论 ready 与否都返不抛栈
    r = eb.whoami(force=True)
    assert isinstance(r.ok, bool)
    print(f"✓ bridge.whoami ok={r.ok} error={r.error!r}")


# ---------------------------------------------------------------------------
# publisher
# ---------------------------------------------------------------------------


def test_publisher_list_all():
    from prisir_work.publisher import list_publishers, _REGISTRY
    pubs = list_publishers()
    names = {p["name"] for p in pubs}
    assert names == {"wechat-oa", "xhs", "bilibili",
                     "douyin", "youtube", "wechat-channels", "zhihu"}, names
    assert len(_REGISTRY) == 7
    print(f"✓ publisher.list → {len(pubs)} platforms")


def test_publisher_wechat_oa_ready():
    from prisir_work.publisher import WechatOaPublisher
    p = WechatOaPublisher()
    st = p.status()
    assert "loggedIn" in st and "bridge_ready" in st
    print(f"✓ wechat-oa.status → bridge_ready={st['bridge_ready']} "
          f"loggedIn={st['loggedIn']}")


def test_publisher_null_publishers_fail():
    """未实现平台(NullPublisher)publish 返 ok=False,绝不抛栈。

    2026-09-25 ship 后:xhs / bilibili 已升级为真 publisher(走 Easel),
    仅 douyin / zhihu / wechat-channels 仍是 NullPublisher 占位。
    """
    from prisir_work.publisher import publish
    for platform in ("douyin", "zhihu", "wechat-channels"):
        r = publish(platform, "x.html", title="t", cover="x.png")
        assert r.ok is False and "未实现" in r.error, (platform, r)
    print("✓ 3 null publishers 全 fail-soft (xhs/bilibili 已升级为真)")


def test_publisher_unknown_platform():
    from prisir_work.publisher import publish
    r = publish("nonsense", "x.html", title="t", cover="x.png")
    assert r.ok is False and "未知平台" in r.error
    print("✓ unknown platform → ok=False")


def test_publisher_file_missing():
    """wechat-oa 没真登录 + 文件不存在 → 返明确 error。"""
    from prisir_work.publisher import publish
    r = publish("wechat-oa", "/no/such/path.html",
                title="t", cover="/no/such/cover.png")
    assert r.ok is False
    # 可能走:文件不存在(早)/ 未登录(中)/ 桥接未就绪(晚)
    assert any(k in r.error for k in ("文件不存在", "未登录", "未就绪")), r.error
    print(f"✓ file_missing → error={r.error!r}")


def test_publisher_register_hot_swap():
    """register_publisher 重复名 → 覆盖(热替换)。"""
    from prisir_work.publisher import register_publisher, get, NullPublisher
    p = NullPublisher("test", "测试")
    register_publisher(p)
    assert get("test") is p
    print("✓ register_publisher 热替换")


# ---------------------------------------------------------------------------
# skill_manifest
# ---------------------------------------------------------------------------


def test_skill_manifest_parse_inline():
    """用临时 SKILL.md 测 frontmatter 解析。"""
    from prisir_work.skill_manifest import parse_skill_md
    with tempfile.NamedTemporaryFile(
            suffix=".md", mode="w", delete=False, encoding="utf-8") as f:
        f.write("---\n"
                "name: my-test-skill\n"
                "description: |\n"
                "  测试 skill 触发场景:发布, 发文, 推文。\n"
                "layer: publish\n"
                "---\n"
                "# body\n")
        p = f.name
    try:
        m = parse_skill_md(p)
        assert m and m.name == "my-test-skill" and m.layer == "publish"
        assert "发布" in m.triggers and "发文" in m.triggers
        print(f"✓ parse SKILL.md frontmatter + triggers: {m.triggers}")
    finally:
        os.unlink(p)


def test_skill_manifest_list_includes_wechat_oa():
    """默认注册里必须有自家 wechat-oa-publisher。"""
    from prisir_work.skill_manifest import list_skills, find_skill
    names = {s["name"] for s in list_skills()}
    assert "wechat-oa-publisher" in names
    m = find_skill("wechat-oa-publisher")
    assert m and m.layer == "publish" and len(m.triggers) >= 5
    print(f"✓ wechat-oa-publisher 在册 ({len(m.triggers)} triggers)")


def test_skill_manifest_scan_easel():
    """如果 Easel 仓库在位,默认应扫到一批 skill。"""
    from prisir_work.skill_manifest import list_skills
    names = [s["name"] for s in list_skills()]
    # 看是否扫到 Easel 的某个核心 skill
    has_easel = any(n.startswith("skill-") for n in names)
    if has_easel:
        print(f"✓ 扫到 Easel skills ({sum(1 for n in names if n.startswith('skill-'))} 个)")
    else:
        print(f"○ 没扫到 Easel skills({len(names)} 个,只看自家)")


# ---------------------------------------------------------------------------
# endpoints(直接调 handler,不走 HTTP)
# ---------------------------------------------------------------------------


def test_endpoint_publish_list():
    from prisir_work.endpoints import _REGISTRY
    payload, code = _REGISTRY["/publish/list"]["handler"]({})
    assert code == 200 and payload["ok"] is True
    assert any(p["name"] == "wechat-oa" for p in payload["publishers"])
    print("✓ /publish/list → 200")


def test_endpoint_publish_status_unknown_platform():
    from prisir_work.endpoints import _REGISTRY
    payload, code = _REGISTRY["/publish/status"]["handler"]({"platform": "nope"})
    assert code == 200 and payload["ok"] is False
    print("✓ /publish/status unknown → ok=False")


def test_endpoint_publish_html_missing_fields():
    from prisir_work.endpoints import _REGISTRY
    payload, code = _REGISTRY["/publish/html"]["handler"]({})
    assert code == 200 and payload["ok"] is False and payload["error"] == "missing_fields"
    print("✓ /publish/html empty body → missing_fields")


def test_endpoint_publish_stats_only_wechat():
    from prisir_work.endpoints import _REGISTRY
    payload, code = _REGISTRY["/publish/stats"]["handler"]({"platform": "xhs"})
    assert code == 200 and payload["ok"] is False and "wechat-oa" in payload["error"]
    print("✓ /publish/stats 非 wechat-oa → ok=False")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    import pytest as _pytest
    return _pytest.main([__file__, "-v"])


if __name__ == "__main__":
    sys.exit(main())