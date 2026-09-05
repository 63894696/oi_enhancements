"""
Outlook IMAP helper — 读取 xthinkerai@duck.com / @outlook.com 邮件。

依赖:Python stdlib imaplib + outlook_auth.py 缓存的 token。

Usage:
    from outlook_helper import Outlook
    o = Outlook()
    print(o.username)            # xthinkerai@duck.com
    folders = o.list_folders()
    msgs = o.fetch_recent("Inbox", limit=5)
    full = o.fetch_full(message_id)
"""
import os
import sys
import imaplib
import email
import json
import msal
from pathlib import Path
from email.header import decode_header, make_header

# 复用 outlook_auth.py 的 client/scope/token 路径配置
CLIENT_ID = "4bb3c5cf-16fb-4d17-8318-628e3259f571"
TENANT = "consumers"  # Jack 的 app 注册为"仅限个人 Microsoft账户",必须用 consumers endpoint
# silent acquire 必须传完整 scope(含 offline_access),才能拿到 refresh_token 续期
SCOPES = [
    "offline_access",
    "https://outlook.office.com/IMAP.AccessAsUser.All",
    "User.Read",
]
AUTHORITY = f"https://login.microsoftonline.com/{TENANT}"
TOKEN_CACHE_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "prisiragent" / "outlook_token.json"

IMAP_HOST = "outlook.office365.com"
IMAP_PORT = 993


class OutlookAuthError(Exception):
    pass


def _load_token() -> tuple[str, str]:
    """Load cached token via MSAL silent flow. Returns (access_token, username)."""
    if not TOKEN_CACHE_PATH.exists():
        raise OutlookAuthError(
            f"no token cache at {TOKEN_CACHE_PATH} — run: python outlook_auth.py"
        )
    cache = msal.SerializableTokenCache()
    cache.deserialize(TOKEN_CACHE_PATH.read_text())
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)
    accounts = app.get_accounts()
    if not accounts:
        raise OutlookAuthError("no accounts in cache — re-run outlook_auth.py")
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise OutlookAuthError(f"silent token failed: {result}")
    # persist refreshed token if any
    if cache.has_state_changed:
        TOKEN_CACHE_PATH.write_text(cache.serialize())
    return result["access_token"], accounts[0]["username"]


class Outlook:
    def __init__(self):
        self.access_token, self.username = _load_token()
        self.imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        # XOAUTH2 SASL format: base64("user=" + user + "^A" + "auth=Bearer " + token + "^A^A")
        # user must be the full email (or just "username" — Microsoft accepts both)
        import base64
        auth_string = f"user={self.username}\x01auth=Bearer {self.access_token}\x01\x01"
        self.imap.authenticate("XOAUTH2", lambda x: base64.b64encode(auth_string.encode()).decode())

    # ---------- folder ops ----------
    def list_folders(self) -> list[str]:
        status, data = self.imap.list()
        if status != "OK":
            raise RuntimeError(f"LIST failed: {status}")
        # raw format: '(\\HasNoChildren) "/" "Inbox"' etc.
        folders = []
        for line in data:
            if isinstance(line, bytes):
                line = line.decode(errors="replace")
            # extract last quoted segment
            parts = line.rsplit('" "', 1)
            if len(parts) == 2:
                folders.append(parts[1].rstrip('"'))
        return folders

    def select_folder(self, name: str) -> int:
        """SELECT folder, return message count. IMAP folder names with non-ASCII
        chars are auto-converted to modified UTF-7 (Python 3.4+)."""
        import imaplib as _imaplib
        if any(ord(c) > 127 for c in name):
            name = _imaplib.IMAP4_utf8(name)
        status, data = self.imap.select(name)
        if status != "OK":
            raise RuntimeError(f"SELECT {name} failed: {status} {data}")
        # data[0] is like '3' = message count
        return int(data[0]) if data and data[0] else 0

    # ---------- message ops ----------
    def fetch_recent(self, folder: str = "Inbox", limit: int = 10) -> list[dict]:
        """Fetch recent messages as lightweight summaries (subject/from/date/uid)."""
        self.select_folder(folder)
        status, data = self.imap.search(None, "ALL")
        if status != "OK" or not data[0]:
            return []
        ids = data[0].split()
        recent = ids[-limit:]
        results = []
        for mid in recent:
            meta = self._fetch_metadata(mid)
            if meta:
                results.append(meta)
        return results

    def fetch_full(self, uid_or_msgid: str, folder: str = "Inbox") -> dict:
        """Fetch full message including body."""
        self.select_folder(folder)
        status, data = self.imap.fetch(uid_or_msgid, "(RFC822)")
        if status != "OK" or not data[0]:
            raise RuntimeError(f"FETCH {uid_or_msgid} failed")
        raw = data[0][1]
        msg = email.message_from_bytes(raw)
        return self._parse_message(msg)

    def _fetch_metadata(self, msgid: bytes) -> dict | None:
        status, data = self.imap.fetch(msgid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        if status != "OK" or not data[0]:
            return None
        raw_header = data[0][1] if isinstance(data[0], tuple) else data[0]
        if not raw_header:
            return None
        msg = email.message_from_bytes(raw_header)
        return {
            "id": msgid.decode(),
            "from": str(make_header(decode_header(msg.get("From", "")))),
            "subject": str(make_header(decode_header(msg.get("Subject", "")))),
            "date": msg.get("Date", ""),
        }

    def _parse_message(self, msg) -> dict:
        subject = str(make_header(decode_header(msg.get("Subject", ""))))
        from_ = str(make_header(decode_header(msg.get("From", ""))))
        date = msg.get("Date", "")

        # Extract body
        body_text = ""
        body_html = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain" and not body_text:
                    try:
                        body_text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                    except Exception:
                        body_text = str(part.get_payload())
                elif ct == "text/html" and not body_html:
                    try:
                        body_html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                    except Exception:
                        body_html = str(part.get_payload())
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                try:
                    body_text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    body_text = payload.decode(errors="replace")

        return {
            "subject": subject,
            "from": from_,
            "date": date,
            "body_text": body_text,
            "body_html": body_html,
            "raw_headers": dict(msg.items()),
        }

    def logout(self):
        try:
            self.imap.logout()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.logout()


# ---------- CLI ----------

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"

    if cmd == "test":
        with Outlook() as o:
            print(f"authenticated: {o.username}")
            print(f"folders: {o.list_folders()}")
            print()
            print("=== Inbox 最近 3 封 ===")
            for m in o.fetch_recent("Inbox", limit=3):
                print(f"  [{m['date']}] {m['from']}")
                print(f"     {m['subject']}")
    elif cmd == "fetch":
        # python outlook_helper.py fetch "Inbox" 5
        folder = sys.argv[2] if len(sys.argv) > 2 else "Inbox"
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        with Outlook() as o:
            print(json.dumps(o.fetch_recent(folder, limit), indent=2, ensure_ascii=False))
    else:
        print("Usage:")
        print("  python outlook_helper.py test           # 登录 + 列 folder + 看最近 3 封")
        print("  python outlook_helper.py fetch Inbox 5  # 拉最近 5 封 metadata")