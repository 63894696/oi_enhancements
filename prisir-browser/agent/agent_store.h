// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.
//
// Prisir 智能体存储层(M2a)— 浏览器本体 AgentStore 服务。
//
// 上游契约:
//   设计: custom-hover-translate/docs/m2-storage-migration-plan-2026-08-14.md(§2 目标存储)
//   接口: prisir-browser/agent/agent_store.mojom(M2c,M2a 是其存储后端)
//   语义: custom-hover-translate/extension/src/ntp/chatstore.js(LRU/惰性命名,逐行对齐)
//
// 定位:把「模型配置 + apiKey + 会话存档」从插件 chrome.storage.local 迁到
//       浏览器 profile 级。本服务是唯一读写入口;M3(运行时)/M4(NTP WebUI)/
//       M5(插件改客户端)都经 Mojo handler 调它,不直接碰存储。
//
// 存储分三路(见 M2 §2):
//   - 模型配置(非 key)  → PrefService(profile prefs)
//   - apiKey(凭证)      → os_crypt 加密字符串,落 profile 下加密文件;绝不进 PrefService 明文
//   - 会话存档           → profile 下 <profile>/Prisir/agent/threads.json(原子写)
//
// 红线(见 M2 §4,已拍板):
//   - apiKey 全程不落明文:读出→os_crypt 加密→落盘,任何中间态不写明文日志/审计/prefs。
//   - apiKey 永不进 LLM 上下文/审计明文。
//   - 会话存档默认本地 profile,不上云。
//   - os_crypt 边界诚实:Win 非交互会话 DPAPI 降级为明文,不夸大保护强度。

#ifndef CHROME_BROWSER_PRISIR_AGENT_AGENT_STORE_H_
#define CHROME_BROWSER_PRISIR_AGENT_AGENT_STORE_H_

#include <memory>
#include <string>
#include <vector>

#include "base/files/file_path.h"
#include "base/memory/raw_ptr.h"
#include "base/values.h"

class PrefService;

namespace prisir::agent {

// 模型配置(非 key 部分,存 PrefService)。
struct ModelConfig {
  std::string base_url;      // OpenAI 兼容端点
  std::string model;         // 文本模型
  std::string vision_model;  // 多模态模型
  bool persist_history = true;  // 会话持久化开关(默认开)
};

// 会话存档单条消息(对齐 chatstore.js {role,text,citations})。
struct ChatMessage {
  std::string role;               // "user" | "assistant" | "system"
  std::string text;
  std::vector<std::string> citations;
  // 注:chatstore 不落 created_at 于单条;createdAt 在会话级。此处对齐,不单列。
};

// 单个会话。
struct Conversation {
  std::string id;
  std::string title;
  std::string created_at;  // ISO8601,对齐 chatstore(new Date().toISOString())
  std::string updated_at;
  std::vector<ChatMessage> messages;
};

// 列表用轻量摘要(不带 messages 全文),对齐 listConversations()。
struct ConversationSummary {
  std::string id;
  std::string title;
  std::string created_at;
  std::string updated_at;
  int message_count = 0;
};

// AgentStore:浏览器本体智能体存储服务。绑定到一个 profile(PrefService + 路径)。
// 非线程安全:所有方法须在 UI 线程(或创建方指定序列)调用;文件 IO 内部走
// 原子写(临时文件 + ReplaceFile),调用方无需关心。
class AgentStore {
 public:
  // prefs:profile 的 PrefService(模型配置/开关);profile_dir:profile 根目录
  //   (会话 threads.json 与加密 key 文件落其子目录)。
  // 两者均不拥有,调用方(通常是 profile-keyed service factory)保证生命周期。
  AgentStore(PrefService* prefs, const base::FilePath& profile_dir);
  ~AgentStore();

  AgentStore(const AgentStore&) = delete;
  AgentStore& operator=(const AgentStore&) = delete;

  // ── 模型配置(PrefService) ──────────────────────────────────────────
  ModelConfig GetModelConfig() const;
  bool SetModelConfig(const ModelConfig& config);

  // ── apiKey(os_crypt 加密,落 profile 文件) ──────────────────────────
  bool HasApiKey() const;
  // 写 key:读出→os_crypt 加密→落盘。内部不留明文副本。
  bool SetApiKey(const std::string& key);
  bool ClearApiKey();
  // 浏览器内部代发请求用:读明文 key 发起模型调用,key 不出本体。
  // 仅本体内 handler 调用;不经 Mojo 暴露给页面/插件。
  std::string GetApiKeyForInternalUse() const;

  // ── 会话 CRUD(threads.json,对齐 chatstore 语义) ────────────────────
  // 持久化开关关闭(persist_history=false)时,写操作照常执行但调用方(Mojo handler)
  // 应据此决定是否真的落盘;本层诚实执行,开关判定留给 handler(见 M2c)。
  std::string CreateConversation();
  bool AppendMessage(const std::string& conv_id, const ChatMessage& msg);
  std::vector<ConversationSummary> ListConversations();
  // 取完整会话(含消息体);不存在返回 nullptr。
  std::unique_ptr<Conversation> GetConversation(const std::string& conv_id);
  bool RenameConversation(const std::string& conv_id, const std::string& title);
  bool DeleteConversation(const std::string& conv_id);
  // 清掉所有 0 消息的空会话,返回清理数(对齐 pruneEmpty)。
  int PruneEmpty();

  // ── 容量规则(对齐 chatstore.js) ────────────────────────────────────
  static constexpr int kMaxConvs = 50;    // MAX_CONVS
  static constexpr int kMaxMsgs = 200;    // MAX_MSGS
  static constexpr int kTitleLen = 40;    // TITLE_LEN

  // 供迁移器/测试用:会话存档文件路径与加密 key 文件路径。
  base::FilePath GetThreadsFilePath() const;
  base::FilePath GetApiKeyFilePath() const;

 private:
  // ── 会话存档内部表示与读写 ──
  // 读取整个 threads.json 到 value(失败返回空结构)。结构对齐 chatstore:
  //   { "order": [convId...], "conversations": { id: {...} } }
  base::Value::Dict ReadThreadsStore() const;
  // 原子写:先写临时文件再 ReplaceFile 覆盖 threads.json。
  bool WriteThreadsStore(const base::Value::Dict& store);

  // 惰性命名兜底(对齐 listConversations):标题空但有 user 消息 → 用首条命名并固化。
  // 返回是否有改动(dirty)。
  static bool ApplyLazyTitle(base::Value::Dict& conv);
  static std::string TitleFrom(const std::string& text);
  static std::string NowISO8601();
  static std::string GenerateUuid();

  raw_ptr<PrefService> prefs_;  // 不拥有
  base::FilePath profile_dir_;
  base::FilePath agent_dir_;    // <profile>/Prisir/agent
};

}  // namespace prisir::agent

#endif  // CHROME_BROWSER_PRISIR_AGENT_AGENT_STORE_H_
