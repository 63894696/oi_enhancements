# #59 — Prisir 网络链路层(PrisirProxy,内嵌 sing-box)

> 浏览器本体 profile 级「网络链路」服务。用户填自建 VPS 单节点 或 粘机场订阅,
> 本体拉起随包 sing-box 内核起本地 SOCKS/HTTP inbound,只把 Chromium 自身网络栈
> 指向它(不开 TUN、不碰系统代理)。
> 本目录 = 可编译源码 + 挂接说明;真实 Chromium src 在云实例,真编译时 `.cc/.h/.mojom`
> `cp` 进 src 树对应路径即可。

## 契约来源
| 契约 | 文件 |
|------|------|
| 实施契约 | `docs/network-link-singbox-contract-2026-08-21.md`(§三 B) |
| 开工方案 | `docs/network-link-launch-plan-2026-08-21.md` |
| 融合设计 | `docs/m3-fusion-agent-layer-network-link-2026-08-21.md`(网络链路段) |

## 文件清单
| 文件 | 作用 |
|------|------|
| `prisir_proxy.mojom` | Mojo 接口契约(SetNode/SetSubscription/SelectNode/StartLink/StopLink/GetStatus/TestLink) |
| `prisir_proxy_handler.{h,cc}` | Mojo handler:sing-box 子进程编排 + os_crypt 凭证 + network_context 运行时代理 |
| `prisir_proxy_prefs.{h,cc}` | pref 注册(仅非敏感:选中节点 id/开关/端口) |
| `subscription_parse.{h,cc}` | 订阅解析(base64 share-link 列表→节点)+ sing-box 配置 JSON 生成 |
| `BUILD.gn` | GN 构建定义(source_set + mojom) |

## 存储分两路
| 数据 | 落点 | 说明 |
|------|------|------|
| 节点密钥/订阅 token(敏感) | os_crypt 加密 → `<profile>/Prisir/proxy/link.enc` | **绝不进 PrefService 明文/LLM/审计** |
| 节点元数据(非敏感 tag/protocol/address/port) | `<profile>/Prisir/proxy/nodes.json` | 列表展示用 |
| 运行时 sing-box 配置 | `<profile>/Prisir/proxy/singbox.json` | StartLink 时生成,含敏感(由内核子进程读) |

## 红线(契约 §四,代码内强制)
- **凭证只进不出**:SetNode/SetSubscription 收密钥→os_crypt 加密→落盘;**无 GetNode/GetSubscription 回明文**。加密失败返回失败,不降级明文。
- **网络最小面**:只 SetProxyConfig 本浏览器 network_context;不开 TUN、不读系统代理、不枚举其它应用流量。
- **内核不编进本体**:sing-box 为随包二进制(`<exe_dir>/prisir-proxy/sing-box.exe`),handler 看门(崩溃重启/退出清理)。
- **责任边界**:只做协议客户端,不内置节点、不做分发。

## 真编译挂接步骤(cp 进 Chromium src 后)
1. **复制**:`prisir-browser/proxy/*.{h,cc,mojom}` → `chrome/browser/prisir/proxy/`
2. **BUILD**:把 `BUILD.gn` 的 `source_set("proxy")` 并入 `chrome/browser/BUILD.gn`,deps 已列。
3. **pref 注册**:`chrome/browser/prefs/browser_prefs.cc` 的 `RegisterProfilePrefs()` 加一行:
   ```cpp
   prisir::proxy::RegisterPrisirProxyPrefs(registry);
   ```
4. **内核随包**:下载 sing-box windows-amd64 release,放 `<out>/prisir-proxy/sing-box.exe`,mini_installer 打包进安装树。
5. **profile 接入**:handler 以 profile-keyed service 形式持有(注入 `profile->GetPrefs()` + `profile->GetPath()`),并持有 network_context 用于 ApplyProxyToBrowser。

## 待编译期完成的接线点(骨架已留 TODO)
- `SetSubscription` 的 HTTP 拉取:接 Chromium `SimpleURLLoader` 拉订阅体(当前 body 为空骨架)。
- `TestLink`:接 `network_service::URLLoader` 经已代理 network_context 访问测试站测延迟/出口。
- `ApplyProxyToBrowser`:接 `StoragePartition::GetNetworkContext()->SetProxyConfig`(只本浏览器)。

## B8 视频修复补丁(随本期一并验证)
args.gn 已加:`proprietary_codecs=true` + `ffmpeg_branding="Chrome"`(B8-1 主因,H.264/AAC)
+ `enable_widevine=true`(B8-2,CDM 二进制另行下发)。源码侧 ffmpeg/widevine 已在树,纯开关。
