# M3 — agent 运行时内置:能力映射表 + 搬运清单(2026-08-14)

> 上游:`ntp-agent-into-browser-source-plan-2026-08-14.md` §6 步骤 M3;M1 报告(独立 WebUI 设为 NTP,已拍板)。
> 定位:registry/router/audit(纯 JS)搬进本体;代行动作的「浏览器能力调用」从扩展 API 改走浏览器原生 API(Mojo handler)。
> **本文件是 M3 的核心可交付物(编译前)**:15 个动作逐个定「插件 API → 本体原生 API」映射 + 搬运边界。
> 与 M2 同范式:源码/契约先行,真编译(等前期完成)时按本表落地。

---

## 0. 三层架构(内置后)

```
NTP WebUI 页面(chrome://agent,M4)
   │  用户输入
   ▼
agent 运行时(纯 JS,搬进本体)        ← registry.js / router.js / audit.js(宿主无关,直接搬)
   │  路由 → 动作 id + 参数
   ▼
AgentRuntime Mojo handler(C++,本文件 §3 契约)  ← 代行动作的浏览器能力在这层
   │  原生 API 调用
   ▼
Chromium 浏览器能力(BookmarkModel/ExtensionRegistry/ContentExtraction/网络栈)
```

**关键边界:路由/校验/确认卡/审计(纯逻辑)在 JS 层;只有「真正碰浏览器」的执行在 Mojo handler 层。**

---

## 1. 纯 JS 层直接搬(零改动,宿主无关)

| 文件 | 内容 | chrome.* 依赖 | 搬运 |
|------|------|--------------|------|
| `agent/registry.js` | 白名单 + validateParams + catalogForPrompt | **无** | 直接搬进 WebUI JS |
| `agent/router.js` | slash/keyword/llm 路由 + productClass | **无**(chatText 由外部传入) | 直接搬;chatText 改由本体模型通路注入 |
| `agent/audit.js` | 审计日志 | chrome.storage.local | 逻辑搬,存储改 AgentStore(M2) |
| `agent/product_knowledge.js` | 产品知识单一事实源 | 无 | 直接搬 |
| `agent/skill_*.js` / `rubric_fetch.js` | skill 系统(本期已建) | 部分 storage | 随 M3 一并,存储改 AgentStore |

**chatText 注入点**:router 的 `llmRoute(chatText, text)` 由外部传 chatText。插件态 chatText 走插件 SW fetch;本体态改由 **AgentRuntime.Chat**(Mojo,经 AgentStore 读 key 代发)提供——**key 不出本体**(M2 红线)。

---

## 2. 代行动作能力映射表(15 动作 × 插件 API → 本体原生 API)

> 每行:动作 id | 插件态用的扩展 API(background.js) | 本体态原生 API | 落点(Mojo handler / 复用底座)

### L0 只读/内省类
| 动作 | 插件 API | 本体原生 API | 说明 |
|------|----------|-------------|------|
| `translate_page` | `chrome.tabs.sendMessage`(给 content.js) | **复用翻译底座**(M5 插件改客户端;本体态经内部通道调翻译引擎) | 翻译仍属插件/底座,本体代行只是触发 |
| `poster_search` | fetch 图源(百度/DDG) | 本体网络栈 `network::SimpleURLLoader` | 纯网络请求,无浏览器状态 |
| `bookmark_preview` | `chrome.bookmarks.getTree` | **`BookmarkModel`**(components/bookmarks) | 只读扫描 |
| `translate_status` | `chrome.storage.local.get` + manifest | **AgentStore**(M2 配置)+ 本体能力自述 | 配置已迁 M2 |
| `browser_introspect` | `chrome.management.getAll` + `chrome.runtime.id` | **`ExtensionRegistry`**(extensions/browser)枚举扩展 | 本体有权限,不再受「无 management」限 |
| `config_list_models` | `chrome.storage.local.get` + fetch /models | **AgentStore 读端点** + 本体网络栈拉模型 | key 经 AgentStore::GetApiKeyForInternalUse |
| `plugin_search_install` | fetch 搜索 + `chrome.tabs.create` | 本体网络栈 + **`chrome::NewTab`** | 安装仍用户亲手点(红线) |
| `product_info` | 无(内置知识) | 无 | 纯 JS,直答 |
| `read_page_tab` | `chrome.tabs.create/query/sendMessage` + content.js 读 DOM | **本体内容抽取**(`content::RenderFrameHost` / DOM distiller)| 本体直读渲染后 DOM,无需 content.js 注入 |

### L1 写操作类(确认卡)
| 动作 | 插件 API | 本体原生 API | 说明 |
|------|----------|-------------|------|
| `bookmark_apply` | `chrome.bookmarks.move/create/getChildren` | **`BookmarkModel` 写** + 撤销栈 | 可撤销(undo) |
| `extension_set_enabled` | `chrome.management.setEnabled` | **`ExtensionService::SetEnabled`** | 本体有权限 |
| `extension_uninstall` | `chrome.tabs.create`(打开详情页,用户手点) | **打开 chrome://extensions 详情** | 浏览器安全限制保留:本体也**不代人点卸载**(红线),仍引导用户手点 |
| `config_set_endpoint` | `chrome.storage.local.set` | **AgentStore::SetModelConfig** | 迁 M2 |
| `config_set_key` | `chrome.storage.local.set({apiKey})` | **AgentStore::SetApiKey**(os_crypt 加密) | 迁 M2,红线:不回显/不进上下文 |
| `config_set_model` | `chrome.storage.local.set({model})` | **AgentStore::SetModelConfig** | 迁 M2 |

---

## 3. AgentRuntime Mojo 契约(骨架,真编译细化)

代行动作经此 handler 暴露给 WebUI。与 AgentStore(存储)分开——AgentRuntime 管「动作执行」,AgentStore 管「数据读写」。

```mojom
module prisir.agent.mojom;

// 动作执行请求(WebUI 路由后下发)
struct ActionRequest {
  string id;                 // registry 动作 id(白名单)
  string params_json;        // validateParams 校验过的参数(序列化)
};

struct ActionResult {
  bool ok;
  string result_json;        // 动作产出(结构各动作不同)
  string error;              // 失败原因(脱敏)
};

interface AgentRuntime {
  // 执行一个白名单动作(handler 内部按 id 分发到原生 API)。
  RunAction(ActionRequest req) => (ActionResult res);

  // 模型对话(给 router 的 chatText):本体经 AgentStore 读 key 代发,key 不出本体。
  Chat(string system, string user, int32 max_tokens) => (bool ok, string text);
};
```

**红线落实**:
- `RunAction` 只接受 registry 白名单 id;handler 内 switch 分发,**不执行任意代码**(对齐 registry 头注红线)。
- `Chat` 是 key 唯一出手的通道,且只发模型请求、不回 key 明文;页面/插件拿不到 key。
- `extension_uninstall` 在 handler 里仍是「打开详情页引导手点」,**不提供程序化卸载**(对齐 CW-2 红线)。

---

## 4. 搬运清单(M3 真编译时执行)

1. **JS 层**:`registry/router/audit/product_knowledge/skill_*` 搬进 WebUI 资源(`chrome/browser/resources/prisir_agent/`);chatText 注入改 AgentRuntime.Chat。
2. **C++ 层**:新建 `AgentRuntimeHandler`(实现 §3 mojom),内部分发 15 动作到原生 API(BookmarkModel/ExtensionService/内容抽取/网络栈)。
3. **存储**:audit + 配置走 AgentStore(M2 已交付);会话走 M2 threads.json。
4. **翻译动作**:M5 插件改客户端后,`translate_page` 经内部通道调翻译底座(本期可先返回「请装翻译插件」占位)。
5. **回归**:白名单/确认卡/审计脱敏/key 不出本体 四条红线逐一验证。

---

## 5. 待拍板 / 依赖

- **依赖 M2**:配置/会话/key 存储通路(M2a/M2c 已交付,M2d 待真编译)。
- **依赖本体↔插件通道**:`translate_page` 完整实现等 M5(插件改底座客户端)。
- **待拍板**:`read_page_tab` 本体态用 DOM distiller 还是自写抽取(影响正文质量);`browser_introspect` 本体有权限后是否仍限制枚举(建议保留「不枚举他站扩展细节」的克制,只报必要)。
- **skill flow 原语子集**(方案 §9.2 待拍板)→ 列入 M3 运行时工具白名单。

> 编译等前期完成(用户 2026-08-14 指示);本表 + mojom 骨架为编译就绪的契约交付。
