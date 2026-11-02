# 研究备忘录：模型编排 / VPS 出口保护 / Tailscale 智能网（2026-11-01）

> 补充 `architecture-master-2026-11-01.md`。三块：① 复刻 Magpie 做监督者-工作者模型编排 ② VPS 出口保护与 ProtonVPN 嵌套可行性评估 ③ Tailscale 智能网定义。
> 资料时间戳：2026-09 ~ 2026-10 公开来源，实施前按官方页面复核。

---

## 一、Magpie 解剖结论

**身份**：yetone/magpie（MIT，Go，2026-09-23 起，~13k stars），本机已装 10-22 版（`magpie-windows-amd64.exe` 57MB + CLI）。

**它做的事（三件）**：

1. **本机网关** `127.0.0.1:3425/v1`，同时说 OpenAI Chat/Responses、Anthropic Messages、Gemini 三种协议，中间互译 streaming/tools/reasoning → 任何 Agent 可以打任何模型
2. **菜单栏/TUI/CLI 切换器**：给每个已装 Agent（Claude Code、Codex、Gemini CLI、OpenCode、Cursor CLI、Goose、Cline…）选 provider/model，只改配置里该改的那一个键
3. **路由组 + 多账号**：`smart / order / rotate / usage` 策略，在订阅额度、限流、欠费之间切换；可选 **intent 分类器按回合选模型**

**额外亮点**：Claude/ChatGPT/Copilot **订阅登录可作 provider**（密钥留在 Magpie，Agent 只指 base URL）；额度打满静默切下一账号。

**复刻到 Prisir 的裁决**：

| Magpie 概念 | Prisir 落点 |
|---|---|
| 本机网关 + 三协议互译 | `fastlane` 升级为 gateway（现有 adapters 已含 Anthropic/OpenAI/兼容，缺 Gemini 互译与订阅 provider） |
| provider 注册表 | `fastlane/providers` 已有雏形，加"订阅登录"类 provider |
| 路由组（smart/order/rotate/usage） | **新**：这是"监督者用付费模型、工作者用免费额度"的载体 |
| intent 按回合分类 | supervisor 的 Eval 层承担（比 intent 分类器更粗：强度分级） |
| 菜单栏切换 | 我们的形态是主对话内切换 + 工单声明，不需要独立菜单栏 |

**为什么不用 Magpie 本身而用 fastlane**：Magpie 是"人肉换模型"的工具（面向开发者桌面），我们要的是"agent 自动按任务分级选模型"——路由逻辑要在我们的 gateway 里可编程，不能依赖它的 UI。

## 二、监督者-工作者模型（落到 dsh 模型）

```
用户指令 → Eval（supervisor：付费高智能模型）
           ├─ 研判任务强度/范围 → Mandate
           ├─ 拆解 → 派 Assignment
           │        └─ worker：免费额度路由组（按任务类型选模型）
           └─ 结果回 supervisor 校验（六态报告）
```

- **supervisor 花大模型的钱只做三件事**：研判、拆解、验收——token 量最小、价值最高
- **worker 花免费额度的钱做体力活**：翻译、摘要、格式化、提取、小修——量最大、单价为零
- 交叉审核（你说的"论文解读 AI 交叉审核"）：同一任务派给两个不同平台的免费模型，结果不一致时 supervisor 介入——这是免费模型可信度的兜底

## 三、免费模型能力摸底表（要建的第一份表）

**目标**：每平台免费模型 × 固定任务集 → 能力分档（可用 / 仅兜底 / 不可用）。

**摸底方法**：标准化题库（按 dsh 报告六态对应任务类型：翻译/摘要/代码/指令遵循/长文/工具调用）+ 自动 runner（调用 → 打分 → 建表）。**交叉审核**：每任务两模型互评 + supervisor 仲裁。

**2026-09/10 免费额度现状（实施时复核）**：

| 平台 | 额度 | 备注 |
|---|---|---|
| 智谱 GLM | 2000 万 Token | 30 并发 |
| 硅基流动 | 2000 万 Token，9B 以下永久免费 | 100+ 模型聚合 |
| 阿里云百炼 | 新人各模型 100 万（90 天），代码模型 100 万/天 | 华北2 |
| 火山引擎（豆包） | 每天 200–500 万 Token | 每日重置 |
| 腾讯混元 | Lite 永久免费 + 100 万/年 | — |
| 百度千帆 | 每模型 100 万 / 3 个月 | — |
| 讯飞星火 | Spark Lite 永久免费不限量 | QPS=2 |
| Google Gemini | 100–1000 RPD / 5–15 RPM | 免费层最稳 |
| Groq | 免费层 1K–14.4K RPD / 30 RPM | LPU 最快 |
| Cerebras | 100 万 Token/天 / 30 RPM | ~3000 tok/s |
| Cloudflare Workers AI | 1 万 请求/天 | 模型最全 |
| OpenRouter | 免费模型 50 req/天；+$10 解锁 1000/天 | 聚合 34+ 免费模型 |
| Kilo Code | 16 个免费模型（Nemotron 550B、MiniMax M2.7、混元 Hy3） | 无 key 无卡 |
| OpenCode | 7 个免费模型（DeepSeek V4 Flash 等） | 装完即用 |

**建表顺序**：先测"中文质量"（我们的主场景），再测"指令遵循/工具调用"（worker 的硬门槛），最后测"长文/代码"。

## 四、VPS 出口保护：ProtonVPN 嵌套评估

**你的设想**：VPS 嵌套 ProtonVPN，用多出口保护主 IP + 扩大全球可达性。

**结论：技术上可行且有生产级验证，但定位要改——VPN 出口是"挡枪的"，不是"洗白的"。**

### 可行性证据

- dev.to ByteGuard（2026）在 Hetzner VPS 上跑 Proton **WireGuard 配置 + 容器级分流** 60 天：下载 `.conf` → `wg-quick up proton-nl` → 只有 scraper 容器挂 `proton-nl` Docker 网络出网，宿主机其他服务直连
- gluetun（qdm12/gluetun）原生支持 ProtonVPN provider（`SERVER_COUNTRIES` / `SERVER_HOSTNAMES`），WireGuard 自定义 provider 也支持
- 多跳出口：`ch-us-01a.protonvpn.com`（入口瑞士、出口美国）
- Proton 付费层 10 设备、可下载 WireGuard 配置；免费层只有 3 国、无端口转发

### 关键风控判断（这是最重要的部分）

**反爬厂商（Cloudflare/Imperva 等）维护 VPN IP 数据库，VPN 出口通常比干净数据中心 IP 更容易被标记。** 所以：

| 出口类型 | 反爬通过率 | 用途 |
|---|---|---|
| 主 IP（数据中心） | 中 | 只出白名单：LLM API、自有服务。**永不直接碰目标站** |
| ProtonVPN 出口 | 低（可牺牲） | 常规网页抓取、地域解锁、一次性探测。被封 = 换节点，主 IP 无伤 |
| 住宅代理（按次购买） | 高 | 高价值/高风险任务，临时 IP |

**原则**：Proton 解决的是"主 IP 不被拖下水 + 地域可达"，不是"绕过反爬"——绕过反爬靠住宅代理和速率控制。两者互补，不互相替代。

### 需要人工核实的两件事

1. **Proton VPN ToS**：商业用途/自动化流量条款（公开资料未直接回答，注册前查官方条款页）
2. **目标站对 VPN 段的态度**：部分站直接 403 所有 VPN IP——Proton 出口拿不到的内容要升级到住宅代理

### 补查结果（2026-11-01 16:50）：同类服务商对比，**推荐 Mullvad 优于 Proton**

| 维度 | Proton | **Mullvad** | IVPN |
|---|---|---|---|
| SOCKS5 | ❌ 无 | ✅ **10.64.0.1:1080，无需整机隧道** | ✅ |
| per-task 切换 | WG 多配置轮换 | ✅ **`mullvad relay set location us nyc`** | 多跳 |
| 账号 | 邮箱 | ✅ **匿名账号（Monero/现金）** | 邮箱 |
| 审计 | Securitum | Leviathan + X41 | Cure53（连续 8 年） |
| 价格 | 免费层 3 国 / 付费 10 设备 | **€5/月 · 5 设备 · 50 国** | $6/月 |
| VPS 案例 | ✅ dev.to 60 天 WG+gluetun | ✅ 有 scraping 专门实践文档 | 少 |

**关键引述**（dataresearchtools 2026，Mullvad for Scraping）：

> "Mullvad IPs are datacenter-flagged in every major IP intelligence database. Cloudflare, Akamai, and Datadome will block them on sight" —— 与我方判断一致：VPN 出口挡枪，不洗白
> "if you run scrapers on cloud VMs, your real datacenter IP is trivially geolocated to your account. routing through Mullvad adds one hop of separation... it breaks the direct reverse-DNS fingerprint" —— 正是我们 egress 网关要的"主 IP 不直接碰目标站"

**裁决**：egress 出口1 改用 **Mullvad SOCKS5 模式**（容器内 HTTP client 直指 `socks5://10.64.0.1:1080`，不建隧道、不碰系统路由；CLI 按任务切国家）。Proton 保留为备选（已有账号）；两条 WG 隧道方案全部退化为兜底。住宅代理层不变。

### 架构落点

```
egress 网关（VPS）：
  出口0 直连（白名单：LLM API / 自有域名）
  出口1 Proton WG 容器组（gluetun，可多国多组轮换）—— scraper/agent fetch
  出口2 住宅代理（按次，token 化，仅高价值任务）
规则：URL 分类 → 出口选择；速率控制；失败自动降级出口0→1→2
```

## 五、Tailscale 智能网

**已验证**：27B 端点已通过 Tailscale 分享给友人设备调用。

**设计含义（纳入总设计）**：

1. **壳的定义扩展到"网内任意设备"**：手机、机器人、任何装了 tailscale 的节点都是 Prisir 的壳。壳=契约的消费端，不限于 Electron
2. **服务发现走 MagicDNS**：`prisir-brain`、`prisir-egress` 等成为 tailnet 内命名服务，壳用服务名而非 IP 寻址
3. **出口统一**：网内设备的外网请求统一经 egress 网关（§四）→ 友人设备不会暴露其家庭 IP 给目标站，全部走受控出口
4. **机器人接入**：未来机器人 = 壳 + 本地执行器（capture service 的传感器版），走同一契约、同一 ACL
5. **ACL 管理**：Tailscale ACL/tag 控制谁能碰 27B、谁能碰 egress；新设备接入 = 邀请 + tag，不裸奔

## 六、对总设计的增量

| 模块 | 落层 | 新增/修改 |
|---|---|---|
| 模型编排 gateway | 脑 + 契约 | `fastlane` 升级：三协议互译 + 订阅 provider + 路由组（smart/order/rotate/usage） |
| 监督者-工作者 | 脑 | dsh Eval/Executor 模型上叠加"模型分级" |
| 免费模型摸底表 | 脑 | 新项目 `D:\Projects\prisir-model-lab`（runner + 题库 + 结果表） |
| egress 网关 | 壳+脑之间 | 新项目 `D:\Projects\prisir-egress`（VPS 上：gluetun 组 + 住宅代理接口 + URL 路由） |
| Tailscale 智能网 | 契约 | 壳定义扩展 + MagicDNS 命名规范 + ACL 规范 |
