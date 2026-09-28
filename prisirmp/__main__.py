"""python -m prisir_mp CLI 派发。

用法:
    python -m prisir_mp <cmd> [args]

子命令:
    status    列出账号 / 已索引文章数 / last_decrypt
    extract   调 wcdb-key-tool 拿密钥
    decrypt   解 Message*.db 到本地缓存
    index     解析 Type=49 appmsg 入 FTS5
    search    关键词搜索
    clear     清空索引(保留账号配置)
    verify    跑 E2E 自检
"""
from __future__ import annotations

import sys

from . import cli


def main(argv: list[str]) -> int:
    return cli.run(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))