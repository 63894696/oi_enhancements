# oiagent-coworker(openwork)方案 — 本地常驻协作组件 · 2026-08-12

> 定位:智能整合包里的**本地常驻进程**。浏览器扩展(MV3,沙箱内)做不了的两件事,由它在本地做:
> ① **托管本地 CLI 进程**(首个:Electrum daemon → M7c 钱包);② **跨 tab 多步 web 自动化**(对标 Perplexity Computer,但走白名单 + 确认,不通用放任)。
> 扩展经 **127.0.0.1 本地 HTTP + 一次性 token** 调它。它是「运营方 cli」和「浏览器沙箱」之间的桥。

## 0. 为什么需要它(和纯扩展/纯网页 agent 的边界)

| 能力 | MV3 扩展能做 | coworker 补 |
|---|---|---|
| 网页内单 tab 动作(翻译/配图/书签) | ✅ M7a 已做 | — |
| 起/管本地进程(Electrum 等 cli) | ❌ 沙箱禁止 | ✅ 核心职责 |
| 跨 tab 编排(读 tab A 填 tab B) | ⚠️ 需 tabs 权限,敏感 | ✅ 经 CDP / 调试端口,权限独立 opt-in |
| 凭证/私钥本地持有 + 签名 | ❌ 不该碰 | ✅ 配合 Electrum,口令不落盘 |

对标 Perplexity Comet「Computer」:它是通用 web agent,因 prompt injection 泄本地文件(Zenity PerplexedComet)被迫代码层硬封 `file://`。我们走相反路线:**coworker 不通用,动作白名单 + 分级确认 + 页面内容永不触发动作**(与扩展 M7a 同一套红线)。

## 1. 进程模型

```
┌─ 浏览器(MV3 扩展,沙箱)────────────────┐
│ NTP 输入 → agent 路由 → wallet_*/web_auto 动作 │
│   fetch http://127.0.0.1:<port>/<ep>  (带 token 头) │
└──────────────┬─────────────────────────┘
               │ 仅 127.0.0.1 + X-OI-Token
┌──────────────▼─────────────────────────┐
│ openwork(coworker 本地常驻)            │
│  ├─ wallet 子系统:起/连 Electrum daemon │
│  │    electrum daemon -w <wallet> → JSON-RPC │
│  ├─ web_auto 子系统:CDP 驱动已开浏览器   │
│  │    (tabs 编排、跨 tab 读/填、多步任务)   │
│  └─ token 校验 + endpoint 白名单 + 审计    │
└──────────────┬─────────────────────────┘
               │ JSON-RPC(127.0.0.1:7777, rpcuser/rpcpassword)
        ┌──────▼──────┐
        │ Electrum    │  加密/签名/助记词全在这,不重造
        └─────────────┘
```

- **形态**:Python 包 `oiagent_coworker`(新建,与 `agent_shell/` 并列),`python -m oiagent_coworker` 起;整合包负责装 Electrum + 自启 openwork。
- **新手零命令行**:openwork 自带 Electrum 的部署与 daemon 生命周期管理(起、崩溃重启、wallet 路径、rpcport 固定)。

## 2. 本地安全红线(与扩展 M7a §5.0.2 同源,不可删)

1. **只监听 127.0.0.1 + 每个敏感 endpoint 校验 `X-OI-Token`**。token 由整合包安装时随机生成,写 chrome.storage.local + coworker 本地配置各一份。无 token/错 token → 401。这是把「本地端口」变成「仅本扩展可调」的关键——否则本机任何网页/进程都能打这个端口转钱。
2. **口令不落盘**:钱包解锁口令只在签名瞬间从扩展传到 coworker,coworker 转交 Electrum 后即弃,不写日志、不持久化。
3. **endpoint 白名单**:coworker 只暴露下面列的端点,**不开放 Electrum 全量 RPC、不开放任意 shell**。其余一律 404。

## 3. HTTP 接口(扩展 ↔ coworker)

基础:`http://127.0.0.1:<port>`(安装时定,如 12450),所有请求头 `X-OI-Token: <token>`。

### 3.1 wallet 子系统(M7c)
| endpoint | 方法 | 干什么 | 对应 Electrum | 风险 |
|---|---|---|---|---|
| `/wallet/status` | GET | 解锁状态/地址(脱敏)/余额/模式 | `getbalance` `listaddresses` | 只读 |
| `/wallet/receive` | POST | 生成收款地址 + memo | `add_request` | 只读(免确认) |
| `/wallet/payto` | POST | 构造+签名+广播(需口令) | `payto` | **L3** |
| `/wallet/history` | GET | 到账查账(入金引导用) | `listaddresses(funded)` `history` | 只读 |

`/wallet/payto` 请求体:`{address, amount, memo, passphrase}`。coworker 先构造 unsigned,**返回给扩展做 L3 确认渲染**,用户确认+输口令后,扩展再发一次带 `confirm:true` 才真正签名广播——确认门槛在扩展侧,coworker 不替用户做决定。

### 3.2 web_auto 子系统(跨 tab,对标 Perplexity Computer)
| endpoint | 方法 | 干什么 |
|---|---|---|
| `/auto/plan` | POST | 多步任务描述 → 步骤计划(经扩展 LLM) |
| `/auto/tabs` | GET | 列出可操作 tab(经 CDP) |
| `/auto/step` | POST | 执行单步{tabId, action, selector/value} |
| `/auto/run` | POST | 跑一个白名单内的多步 recipe |

**安全闸**:web_auto 的每个 step 也走白名单动作(navigate/click/fill/read 这几类),且**敏感 step(提交表单/点支付/发消息)必须扩展侧 L2 确认**。coworker 不自主决策,只执行扩展确认过的步骤。tabs 权限/CDP 访问是 **opt-in**,安装时明确告知用户「将读取可操作你的标签页」。

## 4. 与扩展 M7a 的对接点

扩展侧已有 `agent-run` 路由(M7a,commit dbcdd1d)。coworker 落地后,只需在 `src/agent/registry.js` 加动作、handler 里 `fetch` 上述 endpoint:

- `wallet_status/receive/transfer/fund_guide` → `/wallet/*`(M7c)
- `web_auto_run`(L2,跨 tab recipe)→ `/auto/run`

handler 统一带 token:
```js
const COWORKER = 'http://127.0.0.1:12450';
async function coworker(ep, body) {
  const { agentToken } = await chrome.storage.local.get(['agentToken']);
  const r = await fetch(COWORKER + ep, {
    method: body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json', 'X-OI-Token': agentToken || '' },
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}
```
`agentToken` 安装时由整合包写入(或扩展首启生成后经一次性配对写入 coworker 配置)。

## 5. 分期

- **CW-1(骨架)**:openwork HTTP server + token 校验 + endpoint 白名单框架 + `/wallet/status` 只读打通(连一个 testnet Electrum)。
- **CW-2(钱包)**:receive/payto/history,接扩展 L3 确认;testnet/regtest E2E,严禁主网真钱。
- **CW-3(跨 tab)**:CDP 接浏览器,tabs/step/run;先做 1 个只读 recipe(跨 tab 收集信息),再开放带确认的写操作。

## 6. 待协作团队确认

1. ~~coworker 语言/运行时~~ → **已定:Python**(与 agent_shell 同栈,Electrum 也是 Python)。
2. ~~CDP 目标~~ → **已定:自带受控实例**(安全隔离日常浏览器;复用 9222 仅作需登录态时的显式 opt-in)。
3. ~~token 配对流程~~ → **已定:安装时写两边**(整合包脚本生成 token 写 coworker 配置 + 扩展 storage);首启配对握手做兜底。
4. CW-1 是否现在就能起(不依赖扩展侧,可先行)?

## 7. Perplexity Computer 能力面对标(截图实证 2026-08-12)

我们 CDP 截了 Perplexity `/computer` 的四个配置标签,逐项对标我们的缺口:

| Perplexity 能力 | 它是什么 | 我们有吗 | 要不要补 / 怎么补 |
|---|---|---|---|
| **连接器** | OAuth 连 Gmail/Outlook/HubSpot/Supabase/GitHub 等,读你数据并操作 | ❌ 无 | **要,但走白名单**。不开放任意 OAuth,先做我们生态内的:securedm(加密通信)/chatroom(群聊)/钱包。每个连接器一个 adapter,凭证本地存。 |
| **技能** | 可复用能力包(data-exploration/PRD/sales-research),扩展 Computer 能干什么 | 🟡 雏形 | **要**。我们的 action registry 就是技能骨架;补「技能=带提示词模板的能力包」,让动作可插拔。 |
| **工作流** | 引导式多步流程(简历生成/房源查找),把复杂任务拆成步骤 | ❌ 无 | **要,排后**。多个 action 串成 recipe,带步骤确认。CW-3 跨 tab 是它的底层。 |
| **记忆** | Brain 跨会话积累概念/实体/笔记 | 🟡 雏形 | **部分要**。我们已有审计日志;跨会话记忆要谨慎(隐私红线),做本地 opt-in,默认关。 |
| **工件(Artifacts)** | 可交付的产出物(文档/报告/表格) | 🟡 雏形 | **要**。深入研的报告卡就是工件;补「导出/保存工件」。 |
| **项目** | 把工作组织成项目,挂文件/上下文 | ❌ 无 | **要**。用户明确提了「设定项目目录」——见 §8。 |
| **文件/图片上传** | 在对话里传文件图片给 agent 处理 | ❌ 无 | **要**。用户明确提了——见 §8。 |

## 8. 用户点名的缺失项(优先补)

用户 2026-08-12 明确:对标 Perplexity,我们当前**没有**「传文件/图片」「设定项目目录」「连接器关联在线应用」,以及延伸的「技能/工作流」。逐项研究后决策:

1. **智能模式激活按钮(最高优先,本轮实现)**:平时默认对话模式;点「智能模式」按钮激活后,输入才走 agent 代行。**命名不用 computer use**——暂定「智能模式」(对标 computer use 的激活范式,但语义是我们的)。
2. **文件/图片上传**:NTP 对话框加附件按钮,图片走 vision 端点,文件提取文本进上下文。凭证/隐私同现有红线。
3. **项目目录**:NTP 可建项目,挂对话/工件/文件;切换项目切换上下文。
4. **连接器/技能/工作流**:见 §7 表,分别走白名单 adapter / 能力包 / 多步 recipe。

> 这些逐项已截图研究(上方 §7 来源),按用户要求「研究好再决定要不要」——上面「要不要补」列就是研究后的判断。
