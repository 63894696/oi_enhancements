"""`python -m prisir_work` 入口。

用法:
  python -m prisir_work [port]
默认 host=127.0.0.1(不可改,红线①),port 缺省读配置/12450。
"""
from __future__ import annotations

import sys

from . import handlers  # noqa: F401  —— 导入即登记白名单端点
from . import config, server


def main(argv: list[str]) -> int:
    port = None
    if len(argv) > 1:
        if argv[1].isdigit():
            port = int(argv[1])
        else:
            print("用法: python -m prisir_work [port]", file=sys.stderr)
            return 2
    server.run(host=config.HOST, port=port)  # host 锁死回环
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
