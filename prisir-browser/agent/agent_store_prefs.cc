// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.
//
// Prisir 智能体存储 — PrefService 默认值注册(M2a 配套)。
// 在 profile pref 注册处调用,登记模型配置 pref 的默认值。
// 红线:apiKey 绝不注册进 PrefService(走 os_crypt 加密文件,见 agent_store.cc)。

#include "chrome/browser/prisir/agent/agent_store_prefs.h"

#include "components/prefs/pref_registry_simple.h"

namespace prisir::agent {

// 与 agent_store.cc 内部键一致(声明于此供注册)。
const char kPrefBaseURL[] = "prisir.agent.base_url";
const char kPrefModel[] = "prisir.agent.model";
const char kPrefVisionModel[] = "prisir.agent.vision_model";
const char kPrefPersistHistory[] = "prisir.agent.persist_history";

// 迁移状态 pref(M2b 用,此处一并登记避免二次改注册)。
const char kPrefMigrationDone[] = "prisir.agent.migration_done";
const char kPrefMigrationCount[] = "prisir.agent.migration_count";

void RegisterAgentStorePrefs(PrefRegistrySimple* registry) {
  // 模型配置默认空(未配置);持久化默认开(对齐 chatstore enabled() 默认 true)。
  registry->RegisterStringPref(kPrefBaseURL, "");
  registry->RegisterStringPref(kPrefModel, "");
  registry->RegisterStringPref(kPrefVisionModel, "");
  registry->RegisterBooleanPref(kPrefPersistHistory, true);

  // 迁移状态:默认未迁移、已迁 0 会话。
  registry->RegisterBooleanPref(kPrefMigrationDone, false);
  registry->RegisterIntegerPref(kPrefMigrationCount, 0);
}

}  // namespace prisir::agent
