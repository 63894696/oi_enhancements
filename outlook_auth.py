"""
Outlook IMAP XOAUTH2 授权脚本 — 走 Device Code Flow,零 Azure 注册。

Usage:
    python outlook_auth.py

首次运行:在浏览器登录 outlook 账号,授权"读取邮件"
Token 缓存到:%APPDATA%/oiagent/outlook_token.json(自动 refresh)
"""
import os
import sys
import json
import msal
from pathlib import Path

# 微软 Outlook Desktop 公开 client ID(多年用于 IMAP XOAUTH2)
# 不需要 Azure 注册,这是微软给 Outlook 客户端用的 public client
CLIENT_ID = "4bb3c5cf-16fb-4d17-8318-628e3259f571"

# Tenant:Jack 自注册的"仅限个人 Microsoft帐户"应用,绑到 lsjdlijieoutlook.onmicrosoft.com
# 注意:虽然 default directory 是 lsjdlijieoutlook.onmicrosoft.com,但注册时勾选了
# "仅限个人 Microsoft 帐户",所以 token 会针对 consumer 端点发放
TENANT = "consumers"  # Jack 的 app 注册为"仅限个人 Microsoft账户",必须用 consumers endpoint

# Scope:必须包含 IMAP + offline_access
# - offline_access → 拿 refresh_token,长期免重复登录(MSAL 会自动加,不在这里显式列)
# - IMAP.AccessAsUser.All → IMAP 读权限
# - User.Read → 拿用户基本信息(可选)
# 注意:MSAL 不允许显式列 reserved scope(openid/profile/offline_access),会 ValueError
SCOPES_FOR_FLOW = [
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "User.Read",
]
# 验证 silent acquire 时传完整 scope 列表(包含 offline_access)以便拿到 refresh_token
SCOPES_FOR_SILENT = [
    "offline_access",
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "User.Read",
]

AUTHORITY = f"https://login.microsoftonline.com/{TENANT}"

# Token 存储位置
TOKEN_CACHE_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "oiagent" / "outlook_token.json"


def main():
    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 1. 尝试加载已有 token
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_PATH.exists():
        cache.deserialize(TOKEN_CACHE_PATH.read_text())
        print(f"[cache] loaded existing token from {TOKEN_CACHE_PATH}")

    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        token_cache=cache,
    )

    # 2. 看缓存里有没有可用 account
    accounts = app.get_accounts()
    if accounts:
        print(f"[cache] found {len(accounts)} account(s): {[a['username'] for a in accounts]}")
        result = app.acquire_token_silent(SCOPES_FOR_SILENT, account=accounts[0])
        if result and "access_token" in result:
            print("[auth] silent token OK — no need to re-login")
            save_and_exit(app, cache)
            return

    # 3. 走 Device Code Flow
    # 重要:initiate_device_flow 不能传 offline_access(MSAL reserved scope,自动补)
    print("[auth] starting device code flow...")
    flow = app.initiate_device_flow(scopes=SCOPES_FOR_FLOW)
    if "user_code" not in flow:
        print("ERROR: failed to create device flow")
        print(json.dumps(flow, indent=2))
        sys.exit(1)

    print()
    print("=" * 60)
    print(f"  Open:  {flow['verification_uri']}")
    print(f"  Code:  {flow['user_code']}")
    print("=" * 60)
    print(f"  (expires in {flow.get('expires_in', 900)} seconds)")
    print()

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        print("ERROR: failed to acquire token")
        print(json.dumps(result, indent=2))
        sys.exit(1)

    print("[auth] device code flow OK — token acquired")
    save_and_exit(app, cache)


def save_and_exit(app, cache):
    """Persist token + print summary for verification."""
    if cache.has_state_changed:
        TOKEN_CACHE_PATH.write_text(cache.serialize())
        # Windows:锁一下文件权限(虽然 MSAL 不存密码,只有 access/refresh token)
        try:
            os.chmod(TOKEN_CACHE_PATH, 0o600)
        except Exception:
            pass
        print(f"[cache] saved to {TOKEN_CACHE_PATH}")

    accounts = app.get_accounts()
    if accounts:
        print(f"[ok] authenticated as: {accounts[0]['username']}")

    # 打印 access_token 的前 12 位给 Jack 验证
    result = app.acquire_token_silent(SCOPES_FOR_SILENT, account=accounts[0]) if accounts else None
    if result and "access_token" in result:
        tok = result["access_token"]
        print(f"[token] access_token (first 12): {tok[:12]}...")
        print(f"[token] expires_in: {result.get('expires_in')}s")

    print()
    print("Next step:")
    print("  python outlook_helper.py test   # IMAP 登录验证")


if __name__ == "__main__":
    main()