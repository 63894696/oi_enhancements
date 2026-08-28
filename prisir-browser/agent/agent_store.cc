// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.

#include "chrome/browser/prisir/agent/agent_store.h"

#include "base/files/file_util.h"
#include "base/files/important_file_writer.h"
#include "base/json/json_reader.h"
#include "base/json/json_writer.h"
#include "base/logging.h"
#include "base/rand_util.h"
#include "base/strings/string_number_conversions.h"
#include "base/strings/string_util.h"
#include "base/strings/utf_string_conversions.h"
#include "base/time/time.h"
#include "components/os_crypt/sync/os_crypt.h"
#include "components/prefs/pref_service.h"

namespace prisir::agent {

namespace {

// ── PrefService 键(模型配置,非 key) ─────────────────────────────────────
// 注:这些 pref 需在 prisir 的 pref 注册处(RegisterProfilePrefs)登记默认值,
//     见 agent_store_prefs.cc(本目录)。此处只引用键名。
const char kPrefBaseURL[] = "prisir.agent.base_url";
const char kPrefModel[] = "prisir.agent.model";
const char kPrefVisionModel[] = "prisir.agent.vision_model";
const char kPrefPersistHistory[] = "prisir.agent.persist_history";

// ── 会话存档结构键(对齐 chatstore.js) ───────────────────────────────────
const char kStoreOrder[] = "order";
const char kStoreConversations[] = "conversations";
const char kConvId[] = "id";
const char kConvTitle[] = "title";
const char kConvCreatedAt[] = "createdAt";
const char kConvUpdatedAt[] = "updatedAt";
const char kConvMessages[] = "messages";
const char kMsgRole[] = "role";
const char kMsgText[] = "text";
const char kMsgCitations[] = "citations";

// 文件名。
const char kAgentDirName[] = "Prisir";
const char kAgentSubDir[] = "agent";
const char kThreadsFileName[] = "threads.json";
const char kApiKeyFileName[] = "apikey.enc";  // os_crypt 加密,绝不明文

}  // namespace

AgentStore::AgentStore(PrefService* prefs, const base::FilePath& profile_dir)
    : prefs_(prefs), profile_dir_(profile_dir) {
  agent_dir_ = profile_dir_.AppendASCII(kAgentDirName).AppendASCII(kAgentSubDir);
}

AgentStore::~AgentStore() = default;

// ============================================================================
// 模型配置(PrefService)
// ============================================================================

AgentStore::ModelConfig AgentStore::GetModelConfig() const {
  ModelConfig c;
  if (!prefs_) return c;
  c.base_url = prefs_->GetString(kPrefBaseURL);
  c.model = prefs_->GetString(kPrefModel);
  c.vision_model = prefs_->GetString(kPrefVisionModel);
  c.persist_history = prefs_->GetBoolean(kPrefPersistHistory);
  return c;
}

bool AgentStore::SetModelConfig(const ModelConfig& config) {
  if (!prefs_) return false;
  prefs_->SetString(kPrefBaseURL, config.base_url);
  prefs_->SetString(kPrefModel, config.model);
  prefs_->SetString(kPrefVisionModel, config.vision_model);
  prefs_->SetBoolean(kPrefPersistHistory, config.persist_history);
  return true;
}

// ============================================================================
// apiKey(os_crypt 加密,落 profile 文件;不落明文)
// ============================================================================

base::FilePath AgentStore::GetApiKeyFilePath() const {
  return agent_dir_.AppendASCII(kApiKeyFileName);
}

bool AgentStore::HasApiKey() const {
  return base::PathExists(GetApiKeyFilePath());
}

bool AgentStore::SetApiKey(const std::string& key) {
  // 红线:读出→os_crypt 加密→落盘,中间态不写明文日志/审计。
  if (key.empty()) return ClearApiKey();
  std::string encrypted;
  if (!OSCrypt::EncryptString(key, &encrypted)) {
    // os_crypt 边界诚实:加密失败(如非交互会话 keyring 不可用)不静默降级明文。
    LOG(ERROR) << "AgentStore: OSCrypt::EncryptString failed, key NOT stored";
    return false;
  }
  if (!base::CreateDirectory(agent_dir_)) {
    LOG(ERROR) << "AgentStore: cannot create agent dir";
    return false;
  }
  // 加密串是二进制安全(base::ImportantFileWriter 写字符串字节),原子写。
  bool ok = base::ImportantFileWriter::WriteFileAtomically(
      GetApiKeyFilePath(), encrypted, "AgentStore.ApiKey");
  // encrypted 即将析构;明文 key 由调用方持有,本函数不复制留存。
  return ok;
}

bool AgentStore::ClearApiKey() {
  if (!HasApiKey()) return true;
  return base::DeleteFile(GetApiKeyFilePath());
}

std::string AgentStore::GetApiKeyForInternalUse() const {
  // 仅本体内部调用;读出→解密→返回(调用方用完即弃,不写日志)。
  if (!HasApiKey()) return std::string();
  std::string encrypted;
  if (!base::ReadFileToString(GetApiKeyFilePath(), &encrypted)) {
    return std::string();
  }
  std::string plain;
  if (!OSCrypt::DecryptString(encrypted, &plain)) {
    // 解密失败(会话切换/keyring 变)→ 返回空,调用方按「未配置」处理。
    LOG(WARNING) << "AgentStore: OSCrypt::DecryptString failed";
    return std::string();
  }
  return plain;
}

// ============================================================================
// 会话存档(threads.json,原子写)
// ============================================================================

base::FilePath AgentStore::GetThreadsFilePath() const {
  return agent_dir_.AppendASCII(kThreadsFileName);
}

base::Value::Dict AgentStore::ReadThreadsStore() const {
  base::Value::Dict store;
  store.Set(kStoreOrder, base::Value::List());
  store.Set(kStoreConversations, base::Value::Dict());

  std::string json;
  if (!base::ReadFileToString(GetThreadsFilePath(), &json) || json.empty()) {
    return store;  // 无文件 → 空结构
  }
  std::optional<base::Value> parsed =
      base::JSONReader::Read(json, base::JSON_PARSE_CHROMIUM_EXTENSIONS);
  if (!parsed || !parsed->is_dict()) {
    LOG(WARNING) << "AgentStore: threads.json corrupt, returning empty";
    return store;
  }
  base::Value::Dict dict = std::move(parsed->GetDict());
  // 校验必需键,缺则补空(对齐 chatstore 的 _empty 兜底)。
  if (!dict.FindList(kStoreOrder)) dict.Set(kStoreOrder, base::Value::List());
  if (!dict.FindDict(kStoreConversations))
    dict.Set(kStoreConversations, base::Value::Dict());
  return dict;
}

bool AgentStore::WriteThreadsStore(const base::Value::Dict& store) {
  if (!base::CreateDirectory(agent_dir_)) {
    LOG(ERROR) << "AgentStore: cannot create agent dir";
    return false;
  }
  std::string json;
  if (!base::JSONWriter::Write(store, &json)) {
    LOG(ERROR) << "AgentStore: serialize threads failed";
    return false;
  }
  // 原子写(临时文件 + rename),损坏兜:旧文件保留到最后一刻被替换。
  return base::ImportantFileWriter::WriteFileAtomically(
      GetThreadsFilePath(), json, "AgentStore.Threads");
}

// ── 工具函数 ────────────────────────────────────────────────────────────

std::string AgentStore::NowISO8601() {
  // 对齐 chatstore 的 new Date().toISOString():ISO8601 带毫秒(如 2026-08-14T01:23:45.678Z)。
  // base::Time::ToISO8601() 不带毫秒,用 Exploded 手动补。
  base::Time::Exploded ex;
  base::Time::Now().UTCExplode(&ex);
  return base::StringPrintf("%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
                            ex.year, ex.month, ex.day_of_month, ex.hour,
                            ex.minute, ex.second, ex.millisecond);
}

std::string AgentStore::TitleFrom(const std::string& text) {
  // 对齐 chatstore._titleFrom:压缩空白,截 40 字加省略号。
  std::string t = base::CollapseWhitespaceASCII(text, true);
  if (t.size() > kTitleLen) {
    // 注意:chatstore 按 JS 字符截;此处按字节截 ASCII 安全,中文按 UTF-8 边界截。
    // 简单起见不破坏多字节:找到 <= kTitleLen 的最大 UTF-8 边界。
    size_t cut = kTitleLen;
    while (cut > 0 && (static_cast<unsigned char>(t[cut]) & 0xC0) == 0x80) --cut;
    return t.substr(0, cut) + "\xE2\x80\xA6";  // "…"
  }
  return t.empty() ? "(空对话)" : t;
}

std::string AgentStore::GenerateUuid() {
  // RFC4122 v4,对齐 chatstore._uuid 输出格式。
  uint64_t b0 = base::RandUint64();
  uint64_t b1 = base::RandUint64();
  uint8_t b[16];
  memcpy(b, &b0, 8);
  memcpy(b + 8, &b1, 8);
  b[6] = (b[6] & 0x0f) | 0x40;  // version 4
  b[8] = (b[8] & 0x3f) | 0x80;  // variant
  return base::StringPrintf(
      "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
      b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7], b[8], b[9], b[10], b[11],
      b[12], b[13], b[14], b[15]);
}

bool AgentStore::ApplyLazyTitle(base::Value::Dict& conv) {
  std::string* title = conv.FindString(kConvTitle);
  if (title && !title->empty()) return false;
  base::Value::List* msgs = conv.FindList(kConvMessages);
  if (!msgs) return false;
  for (const auto& m : *msgs) {
    if (!m.is_dict()) continue;
    const std::string* role = m.GetDict().FindString(kMsgRole);
    const std::string* text = m.GetDict().FindString(kMsgText);
    if (role && *role == "user" && text && !text->empty()) {
      conv.Set(kConvTitle, TitleFrom(*text));
      return true;  // dirty
    }
  }
  return false;
}

// ── 会话 CRUD ───────────────────────────────────────────────────────────

std::string AgentStore::CreateConversation() {
  base::Value::Dict store = ReadThreadsStore();
  std::string id = GenerateUuid();
  std::string now = NowISO8601();

  base::Value::Dict conv;
  conv.Set(kConvId, id);
  conv.Set(kConvTitle, "");
  conv.Set(kConvCreatedAt, now);
  conv.Set(kConvUpdatedAt, now);
  conv.Set(kConvMessages, base::Value::List());

  base::Value::List* order = store.FindList(kStoreOrder);
  base::Value::Dict* convs = store.FindDict(kStoreConversations);
  if (!order || !convs) return std::string();

  convs->Set(id, std::move(conv));
  order->Insert(order->begin(), base::Value(id));  // unshift(最近在前)

  // LRU:超 kMaxConvs 裁最旧(order 尾部)。
  while (order->size() > static_cast<size_t>(kMaxConvs)) {
    const std::string* drop = order->back().GetIfString();
    if (drop) convs->Remove(*drop);
    order->Erase(--order->end());
  }

  if (!WriteThreadsStore(store)) return std::string();
  return id;
}

bool AgentStore::AppendMessage(const std::string& conv_id,
                               const ChatMessage& msg) {
  base::Value::Dict store = ReadThreadsStore();
  base::Value::Dict* convs = store.FindDict(kStoreConversations);
  base::Value::List* order = store.FindList(kStoreOrder);
  if (!convs || !order) return false;
  base::Value::Dict* conv = convs->FindDict(conv_id);
  if (!conv) return false;

  base::Value::Dict m;
  m.Set(kMsgRole, msg.role);
  m.Set(kMsgText, msg.text);
  base::Value::List cites;
  for (const auto& c : msg.citations) cites.Append(c);
  m.Set(kMsgCitations, std::move(cites));

  base::Value::List* msgs = conv->FindList(kConvMessages);
  if (!msgs) { conv->Set(kConvMessages, base::Value::List()); msgs = conv->FindList(kConvMessages); }
  msgs->Append(std::move(m));
  // 超 kMaxMsgs 裁最旧(slice(-MAX))。
  while (msgs->size() > static_cast<size_t>(kMaxMsgs)) {
    msgs->Erase(msgs->begin());
  }

  // 自动标题:无标题且首条 user 消息。
  std::string* title = conv->FindString(kConvTitle);
  if (title && title->empty() && msg.role == "user") {
    conv->Set(kConvTitle, TitleFrom(msg.text));
  }
  conv->Set(kConvUpdatedAt, NowISO8601());

  // 触碰 LRU:挪到 order 最前。
  for (auto it = order->begin(); it != order->end(); ++it) {
    const std::string* s = it->GetIfString();
    if (s && *s == conv_id) {
      if (it != order->begin()) {
        order->Erase(it);
        order->Insert(order->begin(), base::Value(conv_id));
      }
      break;
    }
  }

  return WriteThreadsStore(store);
}

std::vector<AgentStore::ConversationSummary> AgentStore::ListConversations() {
  base::Value::Dict store = ReadThreadsStore();
  base::Value::List* order = store.FindList(kStoreOrder);
  base::Value::Dict* convs = store.FindDict(kStoreConversations);
  std::vector<ConversationSummary> out;
  if (!order || !convs) return out;

  bool dirty = false;
  for (const auto& idv : *order) {
    const std::string* id = idv.GetIfString();
    if (!id) continue;
    base::Value::Dict* conv = convs->FindDict(*id);
    if (!conv) continue;
    // 惰性命名兜底(对齐 chatstore listConversations)。
    if (ApplyLazyTitle(*conv)) dirty = true;

    ConversationSummary s;
    s.id = *id;
    const std::string* t = conv->FindString(kConvTitle);
    s.title = (t && !t->empty()) ? *t : "(未命名)";
    const std::string* ca = conv->FindString(kConvCreatedAt);
    s.created_at = ca ? *ca : "";
    const std::string* ua = conv->FindString(kConvUpdatedAt);
    s.updated_at = ua ? *ua : "";
    base::Value::List* msgs = conv->FindList(kConvMessages);
    s.message_count = msgs ? static_cast<int>(msgs->size()) : 0;
    out.push_back(std::move(s));
  }
  if (dirty) WriteThreadsStore(store);  // 固化惰性命名(幂等)
  return out;
}

std::unique_ptr<AgentStore::Conversation> AgentStore::GetConversation(
    const std::string& conv_id) {
  base::Value::Dict store = ReadThreadsStore();
  base::Value::Dict* convs = store.FindDict(kStoreConversations);
  if (!convs) return nullptr;
  base::Value::Dict* conv = convs->FindDict(conv_id);
  if (!conv) return nullptr;

  auto out = std::make_unique<Conversation>();
  const std::string* id = conv->FindString(kConvId);
  out->id = id ? *id : conv_id;
  const std::string* t = conv->FindString(kConvTitle);
  out->title = t ? *t : "";
  const std::string* ca = conv->FindString(kConvCreatedAt);
  out->created_at = ca ? *ca : "";
  const std::string* ua = conv->FindString(kConvUpdatedAt);
  out->updated_at = ua ? *ua : "";

  base::Value::List* msgs = conv->FindList(kConvMessages);
  if (msgs) {
    for (const auto& mv : *msgs) {
      if (!mv.is_dict()) continue;
      const base::Value::Dict& md = mv.GetDict();
      ChatMessage m;
      const std::string* r = md.FindString(kMsgRole);
      m.role = r ? *r : "";
      const std::string* tx = md.FindString(kMsgText);
      m.text = tx ? *tx : "";
      const base::Value::List* cl = md.FindList(kMsgCitations);
      if (cl) {
        for (const auto& cv : *cl) {
          const std::string* cs = cv.GetIfString();
          if (cs) m.citations.push_back(*cs);
        }
      }
      out->messages.push_back(std::move(m));
    }
  }
  return out;
}

bool AgentStore::RenameConversation(const std::string& conv_id,
                                    const std::string& title) {
  base::Value::Dict store = ReadThreadsStore();
  base::Value::Dict* convs = store.FindDict(kStoreConversations);
  if (!convs) return false;
  base::Value::Dict* conv = convs->FindDict(conv_id);
  if (!conv) return false;
  // 对齐 chatstore:截 80 字,空则保留原标题。
  std::string nt = title.substr(0, 80);
  if (nt.empty()) {
    const std::string* t = conv->FindString(kConvTitle);
    nt = t ? *t : "";
  }
  conv->Set(kConvTitle, nt);
  return WriteThreadsStore(store);
}

bool AgentStore::DeleteConversation(const std::string& conv_id) {
  base::Value::Dict store = ReadThreadsStore();
  base::Value::Dict* convs = store.FindDict(kStoreConversations);
  base::Value::List* order = store.FindList(kStoreOrder);
  if (!convs || !order) return false;
  if (!convs->FindDict(conv_id)) return false;
  convs->Remove(conv_id);
  // order 过滤掉该 id。
  for (auto it = order->begin(); it != order->end();) {
    const std::string* s = it->GetIfString();
    if (s && *s == conv_id) it = order->Erase(it);
    else ++it;
  }
  return WriteThreadsStore(store);
}

int AgentStore::PruneEmpty() {
  base::Value::Dict store = ReadThreadsStore();
  base::Value::Dict* convs = store.FindDict(kStoreConversations);
  base::Value::List* order = store.FindList(kStoreOrder);
  if (!convs || !order) return 0;

  std::vector<std::string> to_drop;
  for (const auto kv : *convs) {
    const base::Value::Dict& conv = kv.second.GetDict();
    const base::Value::List* msgs = conv.FindList(kConvMessages);
    if (!msgs || msgs->empty()) to_drop.push_back(kv.first);
  }
  for (const auto& id : to_drop) convs->Remove(id);
  for (auto it = order->begin(); it != order->end();) {
    const std::string* s = it->GetIfString();
    bool gone = !s || !convs->FindDict(*s);
    if (gone) it = order->Erase(it);
    else ++it;
  }
  if (!to_drop.empty()) WriteThreadsStore(store);
  return static_cast<int>(to_drop.size());
}

}  // namespace prisir::agent
