// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.
//
// Prisir 网络链路 Mojo handler(#59)— prisir_proxy.mojom 的浏览器侧实现。
//
// 契约:prisir-browser/proxy/prisir_proxy.mojom(2026-08-21)。
// 定位:profile-keyed service;经 Mojo 暴露给设置页 WebUI。内部:
//   - 凭证:节点密钥/订阅 token 经 Chromium 153 的 os_crypt_async(Encryptor)加密,
//     原子落 <profile>/Prisir/proxy/link.enc。明文不进 prefs/日志/审计(红线)。
//     注:Chromium 153 已删除同步 OSCrypt::EncryptString,改为 os_crypt_async::
//     OSCryptAsync::GetInstance(callback) → scoped_refptr<Encryptor>,Encryptor::
//     Encrypt(span)/Decrypt(span)。本 handler 持有 OSCryptAsync*(由 browser_process
//     注入),首次用时异步取 Encryptor 并缓存,此后同步用。
//   - 内核:生成 sing-box JSON(本地 SOCKS inbound 127.0.0.1:随机端口 + outbound 选中节点)
//     → 拉起随包 sing-box.exe 子进程 → 看门。内核不编进本体。
//   - 网络栈:把 Chromium 本进程 network_context 的 proxy config 指向本地 inbound,
//     只影响本浏览器,不开 TUN、不碰系统代理、不枚举其它应用流量(红线)。
//
// 责任边界:只做协议客户端,不内置节点、不做分发。

#ifndef CHROME_BROWSER_PRISIR_PROXY_PRISIR_PROXY_HANDLER_H_
#define CHROME_BROWSER_PRISIR_PROXY_PRISIR_PROXY_HANDLER_H_

#include <memory>
#include <string>
#include <vector>

#include "base/files/file_path.h"
#include "base/memory/raw_ptr.h"
#include "base/memory/scoped_refptr.h"
#include "base/process/process.h"
#include "chrome/browser/prisir/proxy/mojom/prisir_proxy.mojom.h"
#include "mojo/public/cpp/bindings/pending_receiver.h"
#include "mojo/public/cpp/bindings/receiver.h"

class PrefService;

namespace os_crypt_async {
class OSCryptAsync;
class Encryptor;
}  // namespace os_crypt_async

namespace prisir::proxy {

class PrisirProxyHandler : public prisir::proxy::mojom::PrisirProxy {
 public:
  // prefs:profile 的 PrefService(非敏感项);profile_dir:profile 根目录;
  // os_crypt:browser 级 OSCryptAsync(g_browser_process->os_crypt_async())。
  // 三者均不拥有,调用方(profile-keyed service factory)保证生命周期。
  PrisirProxyHandler(PrefService* prefs,
                     const base::FilePath& profile_dir,
                     os_crypt_async::OSCryptAsync* os_crypt);
  ~PrisirProxyHandler() override;

  PrisirProxyHandler(const PrisirProxyHandler&) = delete;
  PrisirProxyHandler& operator=(const PrisirProxyHandler&) = delete;

  void Bind(mojo::PendingReceiver<prisir::proxy::mojom::PrisirProxy> receiver);

  // ── prisir::proxy::mojom::PrisirProxy ──
  void SetNode(prisir::proxy::mojom::ProxyNodeConfigPtr cfg,
               SetNodeCallback callback) override;
  void SetSubscription(const std::string& url,
                       SetSubscriptionCallback callback) override;
  void SelectNode(const std::string& node_id,
                  SelectNodeCallback callback) override;
  void StartLink(StartLinkCallback callback) override;
  void StopLink(StopLinkCallback callback) override;
  void GetStatus(GetStatusCallback callback) override;
  void TestLink(TestLinkCallback callback) override;

 private:
  // ── 凭证(os_crypt_async,落 profile 文件;明文不出本类) ──
  base::FilePath GetLinkFilePath() const;
  base::FilePath GetNodesFilePath() const;
  base::FilePath GetSingboxConfigPath() const;
  base::FilePath GetSingboxBinaryPath() const;

  // 确保 Encryptor 就绪(异步取并缓存)。ready 后经回调继续。加密/解密前必调。
  void EnsureEncryptor(base::OnceClosure ready, base::OnceClosure unavailable);

  // 加密一个字符串凭证(Encryptor 就绪后调用)。失败返回空。
  std::string EncryptSecret(const std::string& plain);
  // 解密(Encryptor 就绪后调用)。失败返回空。
  std::string DecryptSecret(const std::string& cipher_b64);

  // 加密持久化一个节点的敏感配置(异步:先 EnsureEncryptor 再写盘)。
  void StoreNodeSecretAsync(prisir::proxy::mojom::ProxyNodeConfigPtr cfg,
                            base::OnceCallback<void(std::string node_id)> done);
  // 读某节点明文敏感配置(仅本体内 StartLink 用)。
  bool LoadNodeSecret(const std::string& node_id,
                      prisir::proxy::mojom::ProxyNodeConfig* out);

  // ── sing-box 子进程 ──
  bool BuildSingboxConfig(const std::string& node_id, int local_port, std::string* out_json);
  bool LaunchSingbox(int local_port, std::string* out_inbound, std::string* out_error);
  void KillSingbox();
  bool IsSingboxRunning() const;
  bool ApplyProxyToBrowser(const std::string& local_inbound, std::string* out_error);
  int PickLocalPort() const;

  raw_ptr<PrefService> prefs_;              // 不拥有
  raw_ptr<os_crypt_async::OSCryptAsync> os_crypt_;  // 不拥有(browser 级)
  scoped_refptr<os_crypt_async::Encryptor> encryptor_;  // 缓存,首次 EnsureEncryptor 后非空
  base::FilePath profile_dir_;
  base::FilePath proxy_dir_;
  base::Process singbox_process_;
  std::string local_inbound_;
  mojo::Receiver<prisir::proxy::mojom::PrisirProxy> receiver_{this};
};

}  // namespace prisir::proxy

#endif  // CHROME_BROWSER_PRISIR_PROXY_PRISIR_PROXY_HANDLER_H_
