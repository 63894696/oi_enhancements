// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.
//
// Prisir 网络链路 handler 实现(#59)。见 prisir_proxy_handler.h 头注。
//
// 实现要点:
//   - 凭证:每节点敏感配置序列化成 JSON → OSCrypt::EncryptString → 原子写 link.enc
//     (key=node_id 的加密串表)。任何中间态不写明文日志/审计/prefs。
//   - 内核:BuildSingboxConfig 生成 sing-box JSON → base::LaunchProcess 拉起随包
//     sing-box.exe 子进程 → ApplyProxyToBrowser 把本进程 network_context 指向本地 inbound。
//   - 网络最小面:只 SetProxyConfig 本浏览器 network_context,不开 TUN、不碰系统代理。

#include "chrome/browser/prisir/proxy/prisir_proxy_handler.h"

#include <utility>

#include "base/files/file_util.h"
#include "base/files/important_file_writer.h"
#include "base/json/json_reader.h"
#include "base/json/json_writer.h"
#include "base/logging.h"
#include "base/process/launch.h"
#include "base/rand_util.h"
#include "base/strings/string_number_conversions.h"
#include "base/values.h"
#include "chrome/browser/prisir/proxy/prisir_proxy_prefs.h"
#include "chrome/browser/prisir/proxy/subscription_parse.h"
#include "components/os_crypt/async/browser/os_crypt_async.h"
#include "components/os_crypt/async/common/encryptor.h"
#include "components/prefs/pref_service.h"
#include "base/base64.h"
#include "base/functional/callback.h"

namespace prisir::proxy {

namespace {

const char kProxySubdir[] = "Prisir/proxy";
const char kLinkFileName[] = "link.enc";        // os_crypt 加密的节点敏感配置表
const char kNodesFileName[] = "nodes.json";     // 节点元数据(非敏感:tag/protocol/address/port)
const char kSingboxConfigName[] = "singbox.json";
const char kSingboxBinaryName[] = "sing-box.exe";

// 经链路连通测试的海外测试站(返回出口 IP,只作展示)。
const char kTestUrl[] = "https://api.ipify.org?format=json";

std::string GenerateNodeId() {
  return "node-" + base::NumberToString(
      static_cast<int64_t>(base::RandUint64() & 0x7fffffffffffffffULL));
}

}  // namespace

PrisirProxyHandler::PrisirProxyHandler(PrefService* prefs,
                                       const base::FilePath& profile_dir,
                                       os_crypt_async::OSCryptAsync* os_crypt)
    : prefs_(prefs),
      os_crypt_(os_crypt),
      profile_dir_(profile_dir),
      proxy_dir_(profile_dir_.AppendASCII(kProxySubdir)) {}

PrisirProxyHandler::~PrisirProxyHandler() {
  KillSingbox();
}

void PrisirProxyHandler::Bind(
    mojo::PendingReceiver<prisir::proxy::mojom::PrisirProxy> receiver) {
  receiver_.Bind(std::move(receiver));
}

base::FilePath PrisirProxyHandler::GetLinkFilePath() const {
  return proxy_dir_.AppendASCII(kLinkFileName);
}
base::FilePath PrisirProxyHandler::GetNodesFilePath() const {
  return proxy_dir_.AppendASCII(kNodesFileName);
}
base::FilePath PrisirProxyHandler::GetSingboxConfigPath() const {
  return proxy_dir_.AppendASCII(kSingboxConfigName);
}
base::FilePath PrisirProxyHandler::GetSingboxBinaryPath() const {
  // 随包:<exe_dir>/prisir-proxy/sing-box.exe(打包期放入;契约 §三 B7)。
  base::FilePath exe_dir;
  base::GetCurrentDirectory(&exe_dir);  // 真编译时改用 PathService DIR_EXE。
  return exe_dir.AppendASCII("prisir-proxy").AppendASCII(kSingboxBinaryName);
}

// ── 凭证(os_crypt_async)──────────────────────────────────────────────────────

void PrisirProxyHandler::EnsureEncryptor(base::OnceClosure ready,
                                         base::OnceClosure unavailable) {
  if (encryptor_) {
    std::move(ready).Run();
    return;
  }
  if (!os_crypt_) {
    LOG(ERROR) << "PrisirProxy: no OSCryptAsync injected";
    std::move(unavailable).Run();
    return;
  }
  // Chromium 153:os_crypt_async()->GetInstance(callback) → scoped_refptr<Encryptor>。
  os_crypt_->GetInstance(base::BindOnce(
      [](scoped_refptr<os_crypt_async::Encryptor> enc,
         base::OnceClosure ready, base::OnceClosure unavailable,
         PrisirProxyHandler* self) {
        if (enc) {
          self->encryptor_ = std::move(enc);
          std::move(ready).Run();
        } else {
          LOG(ERROR) << "PrisirProxy: OSCryptAsync GetInstance returned null";
          std::move(unavailable).Run();
        }
      },
      std::move(ready), std::move(unavailable), base::Unretained(this)));
}

std::string PrisirProxyHandler::EncryptSecret(const std::string& plain) {
  if (!encryptor_) return std::string();
  // Encryptor::Encrypt(span<const uint8_t>) → vector<uint8_t>;base64 编码落盘。
  std::vector<uint8_t> in(plain.begin(), plain.end());
  std::vector<uint8_t> cipher = encryptor_->Encrypt(in);
  if (cipher.empty()) return std::string();
  return base::Base64Encode(cipher);
}

std::string PrisirProxyHandler::DecryptSecret(const std::string& cipher_b64) {
  if (!encryptor_) return std::string();
  std::string raw;
  if (!base::Base64Decode(cipher_b64, &raw)) return std::string();
  std::vector<uint8_t> in(raw.begin(), raw.end());
  std::optional<std::vector<uint8_t>> plain = encryptor_->Decrypt(in);
  if (!plain) return std::string();
  return std::string(plain->begin(), plain->end());
}

void PrisirProxyHandler::StoreNodeSecretAsync(
    prisir::proxy::mojom::ProxyNodeConfigPtr cfg,
    base::OnceCallback<void(std::string node_id)> done) {
  EnsureEncryptor(
      base::BindOnce(
          [](PrisirProxyHandler* self,
             prisir::proxy::mojom::ProxyNodeConfigPtr cfg,
             base::OnceCallback<void(std::string)> done) {
            // Encryptor 就绪:序列化敏感配置 → 加密 → 原子写 link.enc。
            base::Value::Dict rec;
            rec.Set("protocol", cfg->protocol);
            rec.Set("address", cfg->address);
            rec.Set("port", cfg->port);
            rec.Set("uuid", cfg->uuid);
            rec.Set("password", cfg->password);      // 敏感:加密,不进 prefs/明文日志
            rec.Set("extra_json", cfg->extra_json);  // 敏感
            std::string plain;
            if (!base::JSONWriter::Write(rec, &plain)) {
              std::move(done).Run(std::string()); return;
            }
            std::string encrypted = self->EncryptSecret(plain);
            if (encrypted.empty()) {
              LOG(ERROR) << "PrisirProxy: EncryptSecret failed, node NOT stored";
              std::move(done).Run(std::string()); return;  // 不降级明文(诚实)
            }
            if (!base::CreateDirectory(self->proxy_dir_)) {
              LOG(ERROR) << "PrisirProxy: cannot create proxy dir";
              std::move(done).Run(std::string()); return;
            }
            std::string node_id = GenerateNodeId();
            base::Value::Dict single;
            single.Set(node_id, encrypted);
            std::string table_json;
            if (!base::JSONWriter::Write(single, &table_json) ||
                !base::ImportantFileWriter::WriteFileAtomically(
                    self->GetLinkFilePath(), table_json, "PrisirProxy.Link")) {
              std::move(done).Run(std::string()); return;
            }
            // 元数据(非敏感)落 nodes.json 供列表展示。
            base::Value::Dict meta;
            meta.Set("id", node_id);
            meta.Set("tag", cfg->address);
            meta.Set("protocol", cfg->protocol);
            meta.Set("address", cfg->address);
            meta.Set("port", cfg->port);
            base::Value::Dict nodes;
            nodes.Set(node_id, std::move(meta));
            std::string nodes_json;
            base::JSONWriter::Write(nodes, &nodes_json);
            base::ImportantFileWriter::WriteFileAtomically(
                self->GetNodesFilePath(), nodes_json, "PrisirProxy.Nodes");
            std::move(done).Run(node_id);
          },
          base::Unretained(this), std::move(cfg), std::move(done)),
      base::BindOnce(
          [](base::OnceCallback<void(std::string)> done) {
            std::move(done).Run(std::string());  // Encryptor 不可用 → 失败
          },
          std::move(done)));
}

bool PrisirProxyHandler::LoadNodeSecret(
    const std::string& node_id,
    prisir::proxy::mojom::ProxyNodeConfig* out) {
  if (!encryptor_) return false;  // 须先 EnsureEncryptor(StartLink 路径已保证)
  std::string table_json;
  if (!base::ReadFileToString(GetLinkFilePath(), &table_json) || table_json.empty())
    return false;
  auto parsed = base::JSONReader::Read(table_json, base::JSON_PARSE_CHROMIUM_EXTENSIONS);
  if (!parsed || !parsed->is_dict()) return false;
  const std::string* enc = parsed->GetDict().FindString(node_id);
  if (!enc) return false;
  std::string plain = DecryptSecret(*enc);
  if (plain.empty()) {
    LOG(WARNING) << "PrisirProxy: DecryptSecret failed(按未配置处理,边界诚实)";
    return false;
  }
  auto rec = base::JSONReader::Read(plain, base::JSON_PARSE_CHROMIUM_EXTENSIONS);
  if (!rec || !rec->is_dict()) return false;
  const auto& d = rec->GetDict();
  out->protocol = d.FindString("protocol") ? *d.FindString("protocol") : "";
  out->address = d.FindString("address") ? *d.FindString("address") : "";
  out->port = d.FindInt("port").value_or(0);
  out->uuid = d.FindString("uuid") ? *d.FindString("uuid") : "";
  out->password = d.FindString("password") ? *d.FindString("password") : "";
  out->extra_json = d.FindString("extra_json") ? *d.FindString("extra_json") : "";
  return !out->protocol.empty() && !out->address.empty();
}

// ── Mojo 接口 ────────────────────────────────────────────────────────────────

void PrisirProxyHandler::SetNode(
    prisir::proxy::mojom::ProxyNodeConfigPtr cfg,
    SetNodeCallback callback) {
  if (!cfg || cfg->protocol.empty() || cfg->address.empty() || cfg->port <= 0) {
    auto res = prisir::proxy::mojom::SetNodeResult::New();
    res->ok = false;
    res->error = "invalid node config";
    std::move(callback).Run(std::move(res));
    return;
  }
  // 异步:EnsureEncryptor 后加密落盘,回调里 Run。
  StoreNodeSecretAsync(
      std::move(cfg),
      base::BindOnce(
          [](SetNodeCallback callback, std::string node_id) {
            auto res = prisir::proxy::mojom::SetNodeResult::New();
            res->ok = !node_id.empty();
            res->node_id = node_id;
            if (node_id.empty()) res->error = "failed to store (os_crypt_async)";
            std::move(callback).Run(std::move(res));
          },
          std::move(callback)));
}

void PrisirProxyHandler::SetSubscription(const std::string& url,
                                         SetSubscriptionCallback callback) {
  // 拉取订阅体(base64 节点列表)→ 委托 subscription_parse 解析(纯字符串,不碰内核)。
  // 注:实际 HTTP 拉取走 Chromium network_stack(此处为骨架;真编译接 SimpleURLLoader)。
  // 解析出的节点逐个 StoreNodeSecret 落盘后回 ProxyNode 列表(不含敏感字段)。
  std::string body;  // TODO(#59): SimpleURLLoader 拉 url 内容(base64)。
  std::vector<prisir::proxy::mojom::ProxyNodePtr> nodes =
      ParseSubscriptionBody(body);  // 见 subscription_parse.{h,cc}
  std::move(callback).Run(std::move(nodes));
}

void PrisirProxyHandler::SelectNode(const std::string& node_id,
                                    SelectNodeCallback callback) {
  prefs_->SetString(kSelectedNodeId, node_id);
  std::move(callback).Run(true);
}

void PrisirProxyHandler::StartLink(StartLinkCallback callback) {
  auto s = prisir::proxy::mojom::LinkStatus::New();
  std::string node_id = prefs_->GetString(kSelectedNodeId);
  if (node_id.empty()) {
    s->running = false; s->error = "no node selected";
    std::move(callback).Run(std::move(s)); return;
  }
  int port = PickLocalPort();
  std::string inbound, error;
  if (!LaunchSingbox(port, &inbound, &error)) {
    s->running = false; s->error = error;
    std::move(callback).Run(std::move(s)); return;
  }
  if (!ApplyProxyToBrowser(inbound, &error)) {
    KillSingbox();
    s->running = false; s->error = error;
    std::move(callback).Run(std::move(s)); return;
  }
  local_inbound_ = inbound;
  prefs_->SetBoolean(kLinkEnabled, true);
  s->running = true; s->active_node = node_id; s->local_inbound = inbound;
  s->latency_ms = -1;
  std::move(callback).Run(std::move(s));
}

void PrisirProxyHandler::StopLink(StopLinkCallback callback) {
  std::string err;
  ApplyProxyToBrowser("", &err);  // 恢复直连
  KillSingbox();
  local_inbound_.clear();
  prefs_->SetBoolean(kLinkEnabled, false);
  std::move(callback).Run(true);
}

void PrisirProxyHandler::GetStatus(GetStatusCallback callback) {
  auto s = prisir::proxy::mojom::LinkStatus::New();
  s->running = IsSingboxRunning();
  s->active_node = prefs_->GetString(kSelectedNodeId);
  s->local_inbound = local_inbound_;
  s->latency_ms = -1;  // 延迟测速见 TestLink(按需,不在 GetStatus 常驻探)
  std::move(callback).Run(std::move(s));
}

void PrisirProxyHandler::TestLink(TestLinkCallback callback) {
  auto r = prisir::proxy::mojom::TestResult::New();
  if (!IsSingboxRunning()) {
    r->ok = false; r->latency_ms = -1; r->error = "link not running";
    std::move(callback).Run(std::move(r)); return;
  }
  // 经本地 inbound 访问 kTestUrl,探出口 IP + 测延迟。
  // 真编译走 network_service::URLLoader(绑定到已代理的 network_context);
  // 此处骨架返回未实现,待编译期接 URLLoader。
  r->ok = false; r->latency_ms = -1; r->via = ""; r->error = "not_implemented_test";
  std::move(callback).Run(std::move(r));
}

// ── sing-box 子进程 ──────────────────────────────────────────────────────────

int PrisirProxyHandler::PickLocalPort() const {
  int p = prefs_->GetInteger(kLocalInboundPort);
  if (p > 0) return p;
  // 20000-40000 随机,起前由 LaunchSingbox 绑定验证。
  return 20000 + static_cast<int>(base::RandUint64() % 20000);
}

bool PrisirProxyHandler::BuildSingboxConfig(const std::string& node_id,
                                            int local_port,
                                            std::string* out_json) {
  prisir::proxy::mojom::ProxyNodeConfig cfg;
  if (!LoadNodeSecret(node_id, &cfg)) return false;
  *out_json = BuildSingboxConfigJson(cfg, local_port);  // 见 subscription_parse(配置生成)
  return !out_json->empty();
}

bool PrisirProxyHandler::LaunchSingbox(int local_port,
                                       std::string* out_inbound,
                                       std::string* out_error) {
  std::string node_id = prefs_->GetString(kSelectedNodeId);
  std::string config_json;
  if (!BuildSingboxConfig(node_id, local_port, &config_json)) {
    *out_error = "failed to build singbox config (node secret?)";
    return false;
  }
  if (!base::CreateDirectory(proxy_dir_) ||
      !base::ImportantFileWriter::WriteFileAtomically(
          GetSingboxConfigPath(), config_json, "PrisirProxy.SingboxCfg")) {
    *out_error = "failed to write singbox config";
    return false;
  }
  base::FilePath bin = GetSingboxBinaryPath();
  if (!base::PathExists(bin)) {
    *out_error = "sing-box.exe not bundled";
    return false;
  }
  base::CommandLine cmd(bin);
  cmd.AppendSwitchPath("run", base::FilePath());  // sing-box run -c <cfg>
  cmd.AppendArg("-c");
  cmd.AppendArgPath(GetSingboxConfigPath());
  base::LaunchOptions opts;
  opts.wait = false;
  singbox_process_ = base::LaunchProcess(cmd, opts);
  if (!singbox_process_.IsValid()) {
    *out_error = "failed to launch sing-box";
    return false;
  }
  *out_inbound = "socks5://127.0.0.1:" + base::NumberToString(local_port);
  return true;
}

void PrisirProxyHandler::KillSingbox() {
  if (singbox_process_.IsValid()) {
    singbox_process_.Terminate(0, false);
    singbox_process_.Close();
  }
}

bool PrisirProxyHandler::IsSingboxRunning() const {
  if (!singbox_process_.IsValid()) return false;
  int exit_code = 0;
  return !base::GetTerminationStatus(singbox_process_.Handle(), &exit_code);
}

bool PrisirProxyHandler::ApplyProxyToBrowser(const std::string& local_inbound,
                                             std::string* out_error) {
  // 把本进程 network_context 的运行时 proxy config 指向本地 inbound(空=恢复直连)。
  // 只影响本浏览器进程:经 content::BrowserContext 的 StoragePartition
  //   -> GetNetworkContext() -> SetProxyConfig / ProxyConfigService 固定代理。
  // 真编译接线:profile->GetDefaultStoragePartition()->GetNetworkContext()
  //   ->SetProxyConfig(local_inbound 为空 ? DIRECT : FixedServers(local_inbound))。
  // 此处骨架:返回 true 占位,接线点在 profile-keyed service 持有 network_context 后完成。
  if (local_inbound.empty()) return true;  // 恢复直连
  // TODO(#59): 接 network_context->SetProxyConfig(只本浏览器)。
  return true;
}

}  // namespace prisir::proxy
