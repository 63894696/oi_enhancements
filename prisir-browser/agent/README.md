# M2a — Prisir 智能体存储层(AgentStore)

> 浏览器本体 profile 级存储服务。把「模型配置 + apiKey + 会话存档」从插件
> `chrome.storage.local` 迁到浏览器 profile,卸载/重装翻译插件后原样保留。
> 本目录 = 可编译源码 + 挂接说明;真实 Chromium src 在云实例(见 M1 报告),
> 真编译时把本目录 `.cc/.h` `cp` 进 src 树对应路径即可。

## 契约来源(全部已拍板)
| 契约 | 文件 | 关系 |
|------|------|------|
| M2 设计 | `custom-hover-translate/docs/m2-storage-migration-plan-2026-08-14.md` | §2 目标存储 / §4 红线(已拍板) |
| Mojo 接口 | `agent_store.mojom`(本目录) | M2c;M2a 是其存储后端 |
| 会话语义 | `custom-hover-translate/extension/src/ntp/chatstore.js` | LRU/惰性命名/原子写,逐行对齐 |

## 文件清单
| 文件 | 作用 |
|------|------|
| `agent_store.h` / `agent_store.cc` | **M2a** AgentStore 服务(PrefService 配置 + os_crypt key + threads.json 会话) |
| `agent_store_prefs.h` / `.cc` | pref 默认值注册(`RegisterAgentStorePrefs`) |
| `agent_store_handler.h` / `.cc` | **M2c** Mojo handler(实现 agent_store.mojom,调 M2a;apiKey 只进不出) |
| `migration_m2b.js` | **M2b** 一次性迁移核心(存储后端无关,Node 可测;校验/回滚/幂等/审计脱敏) |
| `agent_store_unittest.cc` | M2a 单元测试(语义对齐 + 凭证红线断言) |
| `BUILD.gn` | GN 构建定义(source_set + mojom + 注释掉的 unittest target) |
| `agent_store.mojom` | **M2c** Mojo 接口契约(已定稿;时间戳用 ISO8601 string 对齐 chatstore) |
| `agent_runtime.mojom` | **M3** 运行时契约(RunAction 白名单 + RunActionSequence 连续代行 + Chat 代发,key 不出本体) |
| `M3-runtime-mapping.md` | **M3** 能力映射表(15 动作 × 插件 API→本体原生 API)+ 搬运清单 |
| `action_sequence.js` | **红利 P0** 连续代行链编排器(Node 18/18 绿;白名单/L1+ 确认卡暂停续跑) |
| `agent_memory.js` | **红利 P0** 长期记忆提炼层(Node 15/15 绿;偏好/事实/上下文/工作流,敏感过滤+去重合并+LRU+最小化注入) |

## 存储分四路(见 M2 §2 + 红利备忘)
| 数据 | 落点 | 说明 |
|------|------|------|
| 模型配置(baseURL/model/visionModel,非 key) | `PrefService` | pref 键 `prisir.agent.*` |
| **apiKey**(凭证) | os_crypt 加密 → `<profile>/Prisir/agent/apikey.enc` | **绝不进 PrefService 明文** |
| 会话存档(oiThreads) | `<profile>/Prisir/agent/threads.json`(原子写) | LRU 50会话/200条 |
| **长期记忆**(偏好/上下文摘要) | `<profile>/Prisir/agent/memory.json`(原子写) | 提炼层 agent_memory.js;≤100 条 LRU;敏感不落 |

## 红线(见 M2 §4,代码内强制)
- **apiKey 不落明文**:`SetApiKey` 读出→`OSCrypt::EncryptString`→原子落盘;加密失败**返回 false 不降级明文**(诚实,不静默)。
- **apiKey 永不进 LLM 上下文/审计**:`GetApiKeyForInternalUse()` 仅供本体内 handler 代发请求,不经 Mojo 暴露给页面/插件。
- **会话本地**:threads.json 落 profile,不上云。
- **os_crypt 边界诚实**:Win 非交互会话 DPAPI 降级(见 M1 §5);解密失败按「未配置」处理,不夸大保护强度。

## 真编译挂接步骤(cp 进 Chromium src 后)
1. **复制**:`prisir-browser/agent/agent_store*.{h,cc}` → `chrome/browser/prisir/agent/`
2. **BUILD**:把 `BUILD.gn` 的 `source_set("agent")` 并入 `chrome/browser/BUILD.gn`,deps 已列。
3. **pref 注册**:在 `chrome/browser/prefs/browser_prefs.cc` 的 `RegisterProfilePrefs()` 里加一行:
   ```cpp
   prisir::agent::RegisterAgentStorePrefs(registry);
   ```
4. **os_crypt 依赖**:确认 `components/os_crypt/sync` 在 deps(Win 用 DPAPI,Chromium 内置)。
5. **profile 接入**:M2c 的 Mojo handler 工厂以 profile-keyed service 形式持有 AgentStore
   (注入 `profile->GetPrefs()` + `profile->GetPath()`),参考 `new_tab_page` 的 GetPrefs 用法。

## 单元测试(真编译时跑)
`agent_store_unittest.cc` 用 `OSCryptMocker`(不依赖真实 DPAPI)+ `TestingPrefServiceSimple`
+ `ScopedTempDir`,覆盖:配置往返 / **apiKey 盘无明文(红线断言)** / 会话创建+自动标题 /
LRU 会话淘汰 / 消息上限裁剪 / pruneEmpty / 重命名删除。

## 当前状态 / 下一步
- **M2a 存储层已交付**(`agent_store.{h,cc}` + prefs + 单测),待真实 src 编译验证。
- **M2b 迁移核心已交付**(`migration_m2b.js`,Node 16/16 自测绿,含 apiKey 审计脱敏红线/校验失败不置 flag/回滚窗)。
- **M2c Mojo 接口已交付**(`agent_store.mojom` 定稿 + `agent_store_handler.{h,cc}` 实现;apiKey 只进不出;时间戳契约已对齐 ISO8601 string)。
  - handler 中迁移编排(`RunMigration`/`GetMigrationStatus`)为骨架:真实迁移需**经扩展通道读插件快照**(extension messaging / native messaging),该通道本体↔插件打通后接线。
- **M2d**(双跑 + E2E)→ 验证「卸载重装插件后配置+会话仍在」;依赖真编译环境。

> 编译等前期项目完成(用户 2026-08-14 指示);本目录为编译就绪的源码交付。
> M2 四个子任务契约全部齐备(M1 落点 / M2 设计 / mojom / chatstore 语义),真编译时按 README「挂接步骤」接入即可。
