"""增量补丁包打包器(与 prisir_patch.py 应用器配对)。

把「这次改动的文件」打成一个小 zip,分发给装了 PrisirAI(或灵犀等复用本机制的
产品)的机器,GUI/CLI 一键应用,无需重打几百 MB 的全量安装包。

用法:
  python prisir_patch_build.py --patch-id fix-2026-09-08-banner \
      --product prisir --base-version 2.7.4 \
      --add prisiragent_cli.py --add shell/assets/foo.js \
      --out dist/patches/fix-2026-09-08-banner.zip

--add 的路径规则(与 prisir_patch.apply_patch 的落位规则对应):
  shell/<relpath>  → 落到壳静态目录(assets/prisiragent-shell 安装目录)
  <relpath>        → 落到补丁根(patches/),运行时 sys.path 覆盖 frozen 同名模块
源文件在当前工作目录里按同名相对路径取(--add X 即读 ./X,打进 files/X)。

产物 zip 结构(应用器按此消费):
  patch.json       —— {schema, patch_id, product, base_version, files:[{relpath, sha256}]}
  files/<relpath>  —— 文件字节

校验:每个文件算 sha256 写进清单;应用器落盘前复算比对,不符即拒并整体回滚。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

SCHEMA = 1  # 与 prisir_patch.SCHEMA 保持一致;改结构两边同步递增


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build_patch(patch_id: str, product: str, base_version: str,
                add: list[str], out: str | Path) -> dict:
    """打包。add 元素是源文件相对路径(也作补丁内 relpath)。返回结果 dict。"""
    files_meta = []
    payloads: list[tuple[str, bytes]] = []
    for rel in add:
        rel = rel.replace("\\", "/").lstrip("/")
        src = Path(rel)
        if not src.exists():
            return {"ok": False, "error": f"源文件不存在: {rel}"}
        if src.is_dir():
            return {"ok": False, "error": f"--add 只收文件,不收目录: {rel}"}
        data = src.read_bytes()
        files_meta.append({"relpath": rel, "sha256": _sha256_bytes(data)})
        payloads.append((rel, data))

    manifest = {
        "schema": SCHEMA,
        "patch_id": patch_id,
        "product": product,
        "base_version": base_version,
        "files": files_meta,
    }

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("patch.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for rel, data in payloads:
            zf.writestr("files/" + rel, data)

    return {"ok": True, "out": str(out), "patch_id": patch_id,
            "files": [m["relpath"] for m in files_meta],
            "size": out.stat().st_size}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="打增量补丁包(配对 prisir_patch.apply_patch)")
    ap.add_argument("--patch-id", required=True, help="补丁唯一 ID(应用器据此登记/回滚)")
    ap.add_argument("--product", default="prisir", help="目标产品(prisir/lingxi/...)")
    ap.add_argument("--base-version", required=True, help="基于哪个全量版本打(信息性)")
    ap.add_argument("--add", action="append", default=[], metavar="RELPATH",
                    help="加入一个文件(可多次);shell/ 前缀的进壳目录,其余进补丁根")
    ap.add_argument("--out", required=True, help="输出 zip 路径")
    a = ap.parse_args(argv)

    if not a.add:
        print("错误:至少 --add 一个文件", file=sys.stderr)
        return 2
    res = build_patch(a.patch_id, a.product, a.base_version, a.add, a.out)
    if not res.get("ok"):
        print("打包失败:", res.get("error"), file=sys.stderr)
        return 1
    print(f"OK 补丁包 {res['out']}  patch_id={res['patch_id']}  "
          f"{len(res['files'])} 个文件  {res['size']} 字节")
    for f in res["files"]:
        print("  +", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
