# -*- coding: utf-8 -*-
"""Prisir Findex 端到端自检:建测试目录树 → 索引 → 查询命中 → status → clear。

  python verify.py
"""
import os, shutil, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from shell_findex import Findex  # noqa: E402


def build_tree(root):
    """造一棵带已知文件名的小目录树。"""
    os.makedirs(os.path.join(root, "docs", "reports"), exist_ok=True)
    os.makedirs(os.path.join(root, "pics"), exist_ok=True)
    files = {
        os.path.join(root, "docs", "季度报告2026.docx"): b"docx",
        os.path.join(root, "docs", "reports", "财务报表-八月.xlsx"): b"xlsx",
        os.path.join(root, "docs", "readme.md"): b"md",
        os.path.join(root, "pics", "会议纪要.png"): b"png",
        os.path.join(root, "pics", "photo_final.jpg"): b"jpg",
    }
    for p, data in files.items():
        with open(p, "wb") as f:
            f.write(data)
        # 错开 mtime,让倒序可分辨
    return files


def main():
    tmp = tempfile.mkdtemp(prefix="findex_verify_")
    db = os.path.join(tmp, "test.db")
    tree = os.path.join(tmp, "tree")
    try:
        build_tree(tree)
        fx = Findex(db)
        print("[1] status 未开启:", fx.status())
        assert fx.status()["enabled"] is False, "初始应为未开启"

        t0 = time.perf_counter()
        r = fx.enable([tree])
        dt = time.perf_counter() - t0
        print(f"[2] enable scanned={r.get('scanned')} in {dt*1000:.0f}ms -> {r}")
        assert r.get("ok"), f"enable 失败: {r}"

        st = fx.status()
        print("[3] status 已开启:", st)
        assert st["enabled"] is True and st["indexed_count"] == 5, f"count 错: {st}"

        hits = fx.search("报告")
        names = [h["name"] for h in hits]
        print(f"[4] search('报告') -> {names}")
        assert any("季度报告" in n for n in names), "应命中季度报告(含'报告')"
        assert not any("财务报表" in n for n in names), "财务报表不含'报告'应不命中"

        hits_fin = fx.search("财务")
        print(f"[4b] search('财务') -> {[h['name'] for h in hits_fin]}")
        assert any("财务报表" in h["name"] for h in hits_fin), "应命中财务报表"

        hits2 = fx.search("photo")
        print(f"[5] search('photo') -> {[h['path'] for h in hits2]}")
        assert any("photo_final.jpg" in h["path"] for h in hits2), "path 子串应命中"

        # mtime 倒序校验
        hits3 = fx.search("", 50)
        mt = [h["mtime"] for h in hits3]
        assert mt == sorted(mt, reverse=True), "应按 mtime 倒序"
        print(f"[6] 空查询按 mtime 倒序 OK, 共 {len(hits3)} 条")

        fx.disable()
        st2 = fx.status()
        print("[7] disable 后:", st2)
        assert st2["enabled"] is False and st2["indexed_count"] == 0, "clear 后应空"
        fx.close()
        print("\n[PASS] findex 端到端自检全部通过")
        return 0
    except AssertionError as e:
        print(f"\n[FAIL] {e}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
