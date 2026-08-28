"""prisir_work(Prisir 工坊 PrisirWork)— 智能整合包本地常驻协作组件。

定位:浏览器 MV3 扩展(沙箱内)做不了的事,由它在本地做:
  ① 托管本地 CLI 进程(首个:Electrum daemon → 钱包)
  ② 跨 tab 多步 web 自动化(白名单 + 分级确认)

扩展经 127.0.0.1 本地 HTTP + X-OI-Token 调它。

本地安全红线(不可删,与扩展 M7a §5.0.2 同源):
  1. 只监听 127.0.0.1 + 每个敏感 endpoint 校验 X-OI-Token(无/错 → 401)。
  2. 口令不落盘:解锁口令只在签名瞬间经手即弃,不写日志、不持久化。
  3. endpoint 白名单:只暴露注册表列名的端点,不开放任意 shell / Electrum 全量 RPC。
"""

__version__ = "0.1.0"  # P0 / CW-1 骨架
