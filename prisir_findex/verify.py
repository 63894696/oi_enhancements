# -*- coding: utf-8 -*-
"""Prisir Findex 端到端自检:建测试目录树 → 索引 → 查询命中 → status → clear。

  python verify.py
"""
import os, shutil, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from shell_findex import Findex  # noqa: E402


def build_tree(root):
    """造一棵带已知文件名的小目录树(含一个关键词目录,验目录条目)。"""
    os.makedirs(os.path.join(root, "docs", "reports"), exist_ok=True)
    os.makedirs(os.path.join(root, "pics"), exist_ok=True)
    os.makedirs(os.path.join(root, "报告归档"), exist_ok=True)  # 目录名含关键词
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
        # 5 文件 + 4 目录(docs, docs/reports, pics, 报告归档)= 9
        print("[3] status 已开启:", st)
        assert st["enabled"] is True and st["indexed_count"] == 9, f"count 错: {st}"

        res = fx.search("报告")
        hits, names = res["hits"], [h["name"] for h in res["hits"]]
        print(f"[4] search('报告') total={res['total']} -> {names}")
        assert any("季度报告" in n for n in names), "应命中季度报告(含'报告')"
        assert any("报告归档" in n for n in names), "应命中目录 报告归档"
        # 目录条目带 is_dir
        dirhit = [h for h in hits if h["name"] == "报告归档"]
        assert dirhit and dirhit[0]["is_dir"] is True, "目录条目应 is_dir=True"
        filehit = [h for h in hits if "季度报告" in h["name"]]
        assert filehit and filehit[0]["is_dir"] is False, "文件条目应 is_dir=False"

        res_fin = fx.search("财务")
        print(f"[4b] search('财务') -> {[h['name'] for h in res_fin['hits']]}")
        assert any("财务报表" in h["name"] for h in res_fin["hits"]), "应命中财务报表"

        res2 = fx.search("photo")
        print(f"[5] search('photo') -> {[h['path'] for h in res2['hits']]}")
        assert any("photo_final.jpg" in h["path"] for h in res2["hits"]), "path 子串应命中"

        # 匹配度排序:精确名应排在子串命中前
        res_r = fx.search("readme")
        if res_r["hits"]:
            assert res_r["hits"][0]["name"] == "readme.md", "精确名应排最前"
        print("[5b] 匹配度排序 OK(readme 精确名置顶)")

        # 分页:limit=2 offset=2 与 offset=0 不重叠
        allr = fx.search("", 50)
        p1 = fx.search("", 2, 0)
        p2 = fx.search("", 2, 2)
        ids1 = {h["path"] for h in p1["hits"]}
        ids2 = {h["path"] for h in p2["hits"]}
        assert not (ids1 & ids2), "分页 offset 不应重叠"
        assert allr["total"] == 9, f"空查询 total 应=9, 实 {allr['total']}"
        print(f"[6] 分页 offset 不重叠 OK;空查询 total={allr['total']}")

        # mtime 倒序(同匹配度桶内)
        mt = [h["mtime"] for h in allr["hits"] if not h["is_dir"]]
        assert mt == sorted(mt, reverse=True), "文件应按 mtime 倒序"
        print("[6b] mtime 倒序 OK")

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
