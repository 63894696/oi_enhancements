# oi_enhancements / Prisir AI

> **Prisir（湃睿思）AI** — 本地优先的 Agent 运行时：免登录、系统级、默认不上云。
> 一个内核 + 多条产品线（浏览器 / IM / 输入法 / 检索 / 语音 / 移动），见下方模块地图。

---

## 这是什么

`oi_enhancements` 是 Prisir AI 的主仓库。核心是一个**本地 Agent 运行时**（Prisiragent）：

- 常驻 daemon + 本地 HTTP 服务（对话与操作入口）
- 权限闸（白名单动作 + A0 建议 / A1 预授权 / A2 当场确认三级自主权）
- MCP 桥（69 工具，供外部 Agent 与产品线调用）
- LLM 路由（云端多 provider + 本地 llama.cpp 兜底）
- 加密通道（Ed25519 身份 + X25519 换钥，凭证不出原生层）

围绕内核的手和脚分布在本仓多个目录，正在做项目区分，见 `MODULES.md`（区分完成后补入）。

## 子项目概览

| 线 | 目录 | 说明 |
|---|---|---|
| **Agent 内核** | `prisIragent_web.py`、`prisIragent_cli.py`、`policy_engine.py`、`fastlane/`、`mcp_prisiragent_server*/` | daemon / 权限闸 / MCP / 路由 |
| **浏览器** | `prisiragent-browser/`（六子系统）、`prisiragent-shell/`（Electron 壳） | Chromium 源码级改造 + 对话壳 |
| **IM（RIVO 前身）** | `securedm_web.py`、`securedm-shell/`、`crypto_conduit/`、`e2e_share_*/`、`simplex_*.py` | 无账号 E2E 通信 |
| **输入法** | `prisir_ime/`、`prisIragent_ime/`、`fcitx5-prisir/` | Rust 引擎 + Windows TSF + Linux Fcitx5 |
| **检索** | `prisir_findex/`（Rust）、`prisir_fcontent/` | 本机文件搜索（类 Everything）+ 内容索引 / OCR |
| **语音** | `prisir_asr/`、`voice_input/` | 端侧 ASR + VAD |
| **移动** | `prisir_android/` | Capacitor 壳 |
| **端侧核心** | `aureon/`、`wasm_meter/`、`agent_economy/` | 多模型联邦 / WASM 运行时 |
| **能力门面** | `prisir_work/` | wallet + web_auto + 各类外部 bridge |

更详细的架构说明请阅读源码注释与 docstring。内部设计文档归档于私有仓（联系维护者获取访问权限）。

## 快速开始

```bash
# 依赖：Python 3.11+，纯标准库为主，按需装缺失模块
python prisIragent_web.py --port 18802 --lan
# 打开 http://127.0.0.1:18802
```

Electron 壳方式：

```bash
cd prisiragent-shell && npm install && npm start
# 壳会自行拉起后端；托盘常驻，全局热键呼出
```

Windows 安装包与 Linux 装包脚本在 `installer/`。

## 平台

- **Windows x86_64**：NSIS 安装包（Win10/11）
- **Linux x86_64**：Debian/Ubuntu bash 安装脚本（测于 Debian 13 + xfwm4）
- 预编译装包仅支持 x86_64；其他架构需手动编译，未提供支持

## 关键设计

- **默认本地**：数据默认落本地 profile，默认不上云；任何上传明示 + opt-in
- **凭证只进不出**：apiKey 经 os_crypt 加密落盘，页面/扩展/JS 层拿不到明文
- **白名单唯一出口**：模型只能选白名单动作 + 填 schema 参数，不能发明动作
- **人接管优先**：人一接管，agent 立即停止（423），不重试

## License

**OIE-PCS-1.0**（source-available）：个人非商业使用免费；商业使用、组织部署、付费服务需单独书面授权。
对 Core Components（见 `CORE-COMPONENTS.md`）的修改在分发或对外提供服务时须以同许可公开。
品牌与商标（"Prisir AI"、flame logo、`assets/` 图标）不在许可范围内。

- 全文：`LICENSE`（23 节，管辖法：香港特别行政区；仲裁：HKIAC）
- 商业授权与安全漏洞报告：见 `COMMERCIAL-LICENSE.md`
- 版本许可策略（v1 延后 Apache-2.0 等）：见 `LICENSE-POLICY.md`

## Contributing

见 `CONTRIBUTING.md`。所有贡献需 DCO 签核（`git commit -s`）。

## Copyright

Copyright (c) 2026 63894696. All rights reserved.
