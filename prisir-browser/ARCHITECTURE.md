# Prisir 浏览器架构文档(2026-08-14)

> 定位:**Prisir 浏览器 = 无账号、本地优先的 Chromium 浏览器 + 内嵌 Agent 操作层 + 内嵌网络链路层**。
> 数据默认落本地 profile,不要账号、默认不上云;AI 是浏览器的"受控雇员"而非云端服务的前端;
> 自带 sing-box 内嵌链路,出口自主。

---

## 1. 整体框架

下图展示两大簇的关系:**prisir-browser 浏览器本体**(智能体层 / 网络链路层 / 密信 / 内容柜)与 **prisirwork 能力门面**(本地常驻 daemon),以及壳、模型端点池、自建节点等外部角色。

```mermaid
graph TD
    User[用户] -->|NTP 当场输入 / 预签 A1 授权| Shell["壳 Prisir Shell<br/>(NTP WebUI · chrome://agent)"]

    subgraph Browser["prisir-browser 浏览器本体(Chromium src)"]
        direction TB
        AgentJS["智能体层·纯 JS<br/>registry 白名单 / router 路由<br/>audit 审计 / agent_memory 记忆"]
        AgentNative["智能体层·原生 C++<br/>AgentStore(os_crypt 存 key)<br/>AgentRuntime Mojo(RunAction/Chat)"]
        Proxy["网络链路层 PrisirProxy<br/>prisir_proxy.mojom + handler<br/>拉起 sing-box 子进程"]
        Mixin["mixin 密信<br/>(Ed25519 E2E)"]
        Cabinet["content-cabinet 内容柜"]

        AgentJS -->|Mojo 绑定 *.mojom.js| AgentNative
        AgentJS -->|Mojo| Proxy
        AgentNative -->|OSCrypt::EncryptString| Profile[("本地 profile<br/>apikey.enc / threads.json<br/>nodes.json / link.enc")]
        Proxy --> Singbox["sing-box.exe 随包子进程<br/>本地 SOCKS/HTTP inbound"]
    end

    Shell --> AgentJS

    subgraph Work["prisirwork 能力门面(本地常驻 Python daemon)"]
        Wallet["wallet 子系统<br/>托管 Electrum daemon"]
        Auto["web_auto 子系统<br/>CDP 跨 tab 编排"]
        Gate["X-OI-Token 校验<br/>endpoint 白名单 + 审计"]
        Gate --> Wallet
        Gate --> Auto
    end

    Shell -.->|127.0.0.1 HTTP + 一次性 token| Gate
    AgentNative -->|Chat 代发,key 不出本体| Pool["模型端点池<br/>(kimi/glm/deepseek/qwen …<br/>key 只走环境变量)"]
    Singbox --> VPS["自建 VPS / 订阅节点<br/>(只代理本浏览器流量)"]
```

要点:壳只做入口与渲染;碰浏览器/碰钱/碰网络的执行都在原生层或门面层,纯 JS 层只承载路由、校验与审计这类宿主无关逻辑。

---

## 2. 开发模块概览

### 2.1 agent 操作层(prisir-browser/agent)

- **M2 契约(数据基座)**:AgentStore 为 profile 级存储 —— 配置进 PrefService、apiKey 走 os_crypt 加密落盘(`apikey.enc`)、会话/记忆落 profile JSON(`threads.json` / `memories.json`)。
- **M3 契约(能力映射)**:15 个白名单动作逐个定「插件 API → 本体原生 API」映射;registry / router / audit / product_knowledge / skill_* 为纯 JS 零改动直搬;`agent_memory` 负责对话提炼 → 记忆卡(A0 默认只建议)。
- **白名单机制**:模型输出只能「选白名单 id + 填 schema 参数」,不能发明动作;无 eval、无任意 shell、无任意 OAuth。
- **动作自主权三级**:
  - **A0 只建议**:只读分析,永不执行;
  - **A1 可逆·低风险**:用户预授权后自动执行 + 事后报备卡(默认开,体现智能);
  - **A2 不可逆·高风险**:永需当场确认卡(L1 内嵌卡 / L2 全回显 / L3 安全对话框),默认不开。
- 管家第二层「模型研判」判出的动作同样须过 registry 校验,不合规降级 A0 建议卡。

### 2.2 proxy 网络链路层(prisir-browser/proxy)

- **形态**:PrisirProxy 为 profile 级 KeyedService,`prisir_proxy.mojom` 定义页面侧接口(配置节点/订阅、StartLink/StopLink/TestLink、查询状态),`.cc` handler 实现。
- **sing-box 内嵌**:拉起随包 sing-box.exe 子进程起本地 SOCKS/HTTP inbound,只代理 Chromium 自身 network_context(`StoragePartition::GetNetworkContext → SetProxyConfig`);**不开 TUN、不读系统代理、不枚举其它应用流量**。
- **os_crypt_async**:密钥/token 经 `OSCrypt::EncryptString` 异步加密落 `link.enc`,tag/protocol/address 等非敏感字段落 `nodes.json` 供列表展示;**无 GetNode 回明文接口**,解密只在生成运行时 `singbox.json` 瞬间发生,加密失败直接返回失败、不降级。
- **看门逻辑**:handler 监控子进程,崩溃自动重启、退出清理运行时配置;StopLink 停子进程 + 恢复直连。

### 2.3 prisirwork 地基(能力门面)

- **定位**:127.0.0.1 本地常驻 Python daemon,是"浏览器沙箱"与"本地进程"之间的受控桥。
- **双入口/子系统**:
  - **wallet**:托管 Electrum daemon(本地 CLI 进程),浏览器侧钱包能力经门面转发;
  - **web_auto**:CDP 跨 tab 编排,补浏览器原生 API 之外的自动化面。
- **门禁**:所有 endpoint 过 `X-OI-Token` 一次性令牌校验 + endpoint 白名单 + 调用审计;token 由壳一次性下发,不持久、不外发。

### 2.4 oiagent 协作团队

- oiagent 为项目开发协作智能体,运行于本地对话壳(Prisir Shell),受【项目宪法】硬性契约约束(Ed25519 唯一签名、凭证只走环境变量、本地优先、红线诚实标注)。
- 开发任务以 task 切片下发(M1/M2/M3 范式:源码/契约先行,真编译时按契约落地);测试数据带唯一 TAG 且真自清理,E2E 真跑不假 PASS。

---

## 3. 网络链路层运行流程

下图刻画从"用户配置节点"到"浏览器流量走 sing-box"的完整链路,红色节点为安全红线落点。

```mermaid
flowchart TD
    A[用户在设置页<br/>填单节点 或 粘机场订阅] --> B{SetNode / SetSubscription}
    B -->|订阅| C["subscription_parse<br/>base64 share-link → 节点列表"]
    B -->|单节点| C
    C --> D{数据分路}

    D -->|密钥/token 敏感| E["OSCrypt::EncryptString 加密<br/>落 link.enc(绝不明文)"]
    D -->|tag/protocol/address 非敏感| F["nodes.json(列表展示用)"]

    E --> G[用户点 StartLink]
    F --> G
    G --> H["解密凭证 → 生成运行时<br/>singbox.json(仅内核读)"]
    H --> I["拉起 sing-box.exe 子进程<br/>handler 看门:崩溃重启/退出清理"]
    I --> J["sing-box 起本地<br/>SOCKS/HTTP inbound 端口"]

    J --> K["ApplyProxyToBrowser<br/>StoragePartition::GetNetworkContext<br/>→ SetProxyConfig(只本浏览器)"]
    K --> L["Chromium 自身网络栈流量<br/>→ 本地 inbound → 出站到 VPS"]

    L --> M{TestLink?}
    M -->|是| N["经已代理 network_context<br/>访问测试站 → 回报延迟/出口"]
    M -->|否| O[链路常驻]
    N --> O

    P[StopLink] --> Q[停 sing-box 子进程<br/>+ 恢复直连 + 清理 singbox.json]

    style E stroke:#c0392b,stroke-width:2px
    style K stroke:#c0392b,stroke-width:2px
```

要点:凭证全程"只进不出"——加密落盘、运行时瞬解、无任何回明文的查询接口;代理面严格限于本浏览器,不碰系统级网络。

---

## 4. Agent 子系统一次对话调用时序

下图展示从用户输入到答复渲染的完整时序,含模型路由、白名单校验与 A2 当场确认门槛。

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant S as 壳 (NTP WebUI)
    participant R as router/registry (纯JS)
    participant RT as AgentRuntime Mojo (C++)
    participant M as 模型端点池
    participant T as 工具侧<br/>(原生API / prisirwork)

    U->>S: 输入一句话 (动作唯一触发源)
    S->>R: slash/关键词路由 → 命中动作 id?
    alt 需要 LLM 路由
        R->>RT: Chat(system, catalog, user)
        RT->>RT: AgentStore 读 apiKey (key 不出本体)
        RT->>M: 代发模型请求
        M-->>RT: 返回动作 id + 参数
        RT-->>R: 路由结果
    end
    R->>R: validateParams(schema 校验白名单)

    alt A2 不可逆·高风险
        R->>S: 弹当场确认卡 (L1/L2/L3 全回显)
        U->>S: 确认
    else A1 可逆·低风险
        Note over R: 有预授权则直接执行<br/>事后发报备卡
    else A0 只建议
        R-->>S: 只读建议卡(不执行)
    end

    R->>RT: RunAction(id, params_json)
    alt 浏览器原生能力
        RT->>T: BookmarkModel / ExtensionService / 网络栈
    else 本地进程/跨tab
        RT->>T: prisirwork (127.0.0.1 + X-OI-Token)
    end
    T-->>RT: 执行结果
    RT-->>S: ActionResult(脱敏)
    S->>S: audit 落盘 + (A1)事后报备卡
    S-->>U: 渲染答复卡 / 执行结果
```

要点:模型只负责"选动作 + 填参数",执行权永远在用户(当场输入/预授权/确认卡)手里;Chat 是 apiKey 唯一出手通道。

---

## 5. 关键安全红线

> 以下五条为硬性契约,违反即返工;实现上不接受安慰剂占位(如 `validate(){return true}`),要么真实实现要么标 TODO,验收清单不得据此打 ✅。

1. **key 不出本体**:apiKey 只在 AgentStore(os_crypt 加密落盘)与 Chat Mojo 代发瞬间经手,页面/扩展/JS 层永远拿不到明文;凭证只走环境变量,永不硬编码进源码/补丁/日志/LLM 上下文。
2. **os_crypt 加密落盘**:一切敏感数据(apiKey、节点凭证、link.enc)经 Chromium `OSCrypt::EncryptString`(Windows DPAPI / macOS Keychain / Linux libsecret)加密后落盘;磁盘文件不得出现明文;无回明文查询接口;加密失败即失败,不降级。
3. **白名单唯一出口**:模型输出只能"选白名单动作 id + 填 schema 参数",不能发明动作;无 eval / 任意 shell / 任意 OAuth;管家模型研判的动作同样须过 registry 校验,不合规降级 A0。
4. **页面不触发动作**:动作触发源只能是 NTP 用户当场输入,或用户预先签署的 A1 级授权;页面内容/附件/模型中间产物**永不触发新动作**;定时任务只能到点"提醒/预填",真正执行仍等用户确认。
5. **默认本地(默认知地)**:数据默认落本地 profile,默认不上云、不要账号;任何上传明示 + opt-in;代理面只限本浏览器(不开 TUN/不读系统代理);E2E 不降级,密信私钥不出原生层。
