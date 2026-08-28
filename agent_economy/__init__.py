"""agent_economy — 组织内部 agent 经济三件套:身份 + 授权 + 计量。

背景:外部 agent 支付/身份标准(x402 / AP2 / Web Bot Auth)仍在收敛,但组织内部
没有经济利益冲突,agent 身份/授权/计量可先行闭环。本包把三件套落到现有
Agent-First OS(L1–L4)架构的 L2 能力层,作为"内部先行、未来对接外部标准"的 PoC。

核心原则:内部实现严格对齐正在收敛的外部标准格式,让内部系统成为预演而非孤岛:
  - identity : RFC 9421 HTTP Message Signatures + Ed25519(Web Bot Auth 对齐)
  - authz    : W3C Verifiable Credentials 精简格式(AP2 Mandate 对齐)
  - meter    : HTTP 402 + 配额记账(x402 语义对齐,内部用配额代替真结算)

三件服务均为 stdlib-only HTTP(ThreadingHTTPServer),复用 policy_engine 的
SQLite 线程安全模式与 l4_web 的 token/目录约定,不引入新依赖。

端口规划(本机 127.0.0.1):
  :18901 identity  :18902 authz  :18903 meter
"""

from __future__ import annotations

__version__ = "0.1.0"

# 端口(本机回环;VPS 只放只读公钥目录,经 l4_remote_relay 隧道暴露 identity)
PORT_IDENTITY = 18901
PORT_AUTHZ = 18902
PORT_METER = 18903

HOST = "127.0.0.1"
