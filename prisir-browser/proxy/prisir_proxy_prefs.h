// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.

#ifndef CHROME_BROWSER_PRISIR_PROXY_PRISIR_PROXY_PREFS_H_
#define CHROME_BROWSER_PRISIR_PROXY_PRISIR_PROXY_PREFS_H_

class PrefRegistrySimple;

namespace prisir::proxy {

// 登记 PrisirProxy 相关 pref 默认值。在 profile pref 注册处调用
// (chrome/browser/prefs/browser_prefs.cc 的 RegisterProfilePrefs)。
// 只存非敏感项(当前选中节点 id、开关);密钥/订阅 token 一律走 os_crypt 文件,不进 prefs。
void RegisterPrisirProxyPrefs(PrefRegistrySimple* registry);

}  // namespace prisir::proxy

#endif  // CHROME_BROWSER_PRISIR_PROXY_PRISIR_PROXY_PREFS_H_
