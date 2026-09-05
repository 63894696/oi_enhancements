"""
AgentMail + Parallel helper for prisiragent.

Quick start:
    from helper import AgentMail, Parallel
    am = AgentMail()
    am.send("x@duck.com", "subject", "body text", html="<p>body</p>")
    am.send_chinese("x@duck.com", "subject", "中文内容")  # 自动转图片
    p = Parallel()
    results = p.search("latest Claude Code features")
"""

import os
import json
import base64
import io
import urllib.request
import urllib.error
from typing import Optional

# PIL only imported when needed (saves startup for search-only workflows)
def _pil():
    from PIL import Image, ImageDraw, ImageFont
    return Image, ImageDraw, ImageFont


# ---------- AgentMail ----------

class AgentMail:
    BASE = "https://api.agentmail.to"
    VERSION = "v0"  # NOT v1 — docs mismatch

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("AGENTMAIL_API_KEY")
        if not self.api_key:
            raise RuntimeError("AGENTMAIL_API_KEY not set")

    def _request(self, method: str, path: str, body=None):
        url = f"{self.BASE}/{self.VERSION}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"AgentMail {method} {path} -> HTTP {e.code}: {e.read().decode()}")

    # ---- inboxes ----
    def list_inboxes(self):
        return self._request("GET", "/inboxes").get("inboxes", [])

    def get_inbox(self, email: str):
        return self._request("GET", f"/inboxes/{email}")

    def create_inbox(self, username: str, display_name: str = ""):
        # display_name in Chinese often garbles — prefer ASCII
        return self._request("POST", "/inboxes", {
            "username": username,
            "display_name": display_name or username,
        })

    def delete_inbox(self, email: str):
        self._request("DELETE", f"/inboxes/{email}")

    # ---- messages ----
    def send(self, to: str, subject: str, text: str, html: str = None,
             from_inbox: str = "prisiragent@agentmail.to"):
        """Send one email. NOTE: `to` is a STRING, not a list."""
        body = {"to": to, "subject": subject, "text": text}
        if html:
            body["html"] = html
        return self._request("POST", f"/inboxes/{from_inbox}/messages/send", body)

    def send_chinese(self, to: str, subject: str, chinese_text: str,
                     from_inbox: str = "prisiragent@agentmail.to",
                     font_size: int = 24):
        """Send email with Chinese text rendered as inline PNG image.

        Workaround for AgentMail's broken UTF-8 on outbound emails.
        The image is base64-embedded in HTML so most clients show it inline.
        Subject itself should still be ASCII or short English (Chinese subject
        will also mangle).

        Note on display_name: AgentMail mangles Chinese display_name too
        (visible as ��� to recipients). Caller must pre-set the inbox's
        display_name to ASCII. We do NOT override it here.
        """
        png_b64 = chinese_to_png_base64(chinese_text, font_size=font_size)
        html = (
            f'<img src="data:image/png;base64,{png_b64}" '
            f'style="max-width:100%;height:auto;display:block" '
            f'alt="chinese message">'
        )
        # text fallback so clients that block images still see something
        text = "[Chinese message rendered as image — see HTML version]"
        return self.send(to, subject, text, html=html, from_inbox=from_inbox)

    def set_display_name(self, email: str, display_name: str):
        """Update inbox display_name. ASCII only — Chinese mangles."""
        # AgentMail may not have a PATCH endpoint — try a few patterns
        for method, path in [
            ("PATCH", f"/inboxes/{email}"),
            ("PUT",   f"/inboxes/{email}"),
            ("POST",  f"/inboxes/{email}/update"),
        ]:
            try:
                return self._request(method, path, {"display_name": display_name})
            except RuntimeError:
                continue
        raise RuntimeError("Could not update display_name — no working endpoint")

    def list_messages(self, inbox: str = "prisiragent@agentmail.to", limit: int = 20):
        return self._request("GET", f"/inboxes/{inbox}/messages?limit={limit}")


# ---------- Parallel ----------

class Parallel:
    BASE = "https://api.parallel.ai"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("PARALLEL_API_KEY")
        if not self.api_key:
            raise RuntimeError("PARALLEL_API_KEY not set")

    def _request(self, method: str, path: str, body=None):
        url = f"{self.BASE}{path}"
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Parallel {method} {path} -> HTTP {e.code}: {e.read().decode()}")

    def search(self, objective: str, **kwargs):
        """Web deep search. Required field is `objective` (NOT `input`)."""
        body = {"objective": objective, **kwargs}
        return self._request("POST", "/v1beta/search", body)


# ---------- Chinese text → PNG (workaround for AgentMail UTF-8 bug) ----------

# Font candidates in priority order — first one that loads wins
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",          # Microsoft YaHei
    r"C:\Windows\Fonts\simhei.ttf",        # SimHei
    r"C:\Windows\Fonts\NotoSansSC-VF.ttf", # Noto Sans SC Variable
    r"C:\Windows\Fonts\simsun.ttc",        # SimSun
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux fallback
    "/System/Library/Fonts/PingFang.ttc",  # macOS fallback
]

def _load_font(size: int):
    Image, _, ImageFont = _pil()
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    # Last resort — will draw boxes for CJK but at least won't crash
    return ImageFont.load_default()

def chinese_to_png_base64(text: str, font_size: int = 24,
                          max_width: int = 900, padding: int = 20,
                          bg: str = "white", fg: str = "#1a1a1a") -> str:
    """Render Chinese (or any) text to a PNG and return base64 string.

    Auto-wraps and auto-sizes. Used to bypass AgentMail's broken UTF-8
    by embedding the rendered text as a base64 PNG in HTML emails.
    """
    Image, ImageDraw, ImageFont = _pil()
    font = _load_font(font_size)

    # Wrap text to max_width by breaking at newlines or word boundaries
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for ch in paragraph:
            test = current + ch
            bbox = font.getbbox(test)
            if bbox[2] - bbox[0] > max_width - 2 * padding and current:
                lines.append(current)
                current = ch
            else:
                current = test
        if current:
            lines.append(current)

    line_height = font_size + 8
    img_height = line_height * len(lines) + 2 * padding
    img_width = max_width

    img = Image.new("RGB", (img_width, img_height), bg)
    draw = ImageDraw.Draw(img)

    y = padding
    for line in lines:
        draw.text((padding, y), line, font=font, fill=fg)
        y += line_height

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


# ---------- CLI demo ----------

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "inboxes":
        print(json.dumps(AgentMail().list_inboxes(), indent=2))
    elif cmd == "send":
        _, _, to, subject, text = sys.argv[:5]
        print(json.dumps(AgentMail().send(to, subject, text), indent=2))
    elif cmd == "send-zh":
        # python helper.py send-zh "to@x.com" "English subject" "中文内容"
        _, _, to, subject, zh = sys.argv[:5]
        print(json.dumps(AgentMail().send_chinese(to, subject, zh), indent=2))
    elif cmd == "render-zh":
        # just render to PNG for inspection
        text = sys.argv[2] if len(sys.argv) > 2 else "中文测试 你好世界"
        b64 = chinese_to_png_base64(text)
        out = "test_output.png"
        with open(out, "wb") as f:
            f.write(base64.b64decode(b64))
        print(f"wrote {out} ({len(b64)} chars b64)")
    elif cmd == "search":
        print(json.dumps(Parallel().search(sys.argv[2]), indent=2)[:2000])
    else:
        print("Usage:")
        print("  python helper.py inboxes")
        print('  python helper.py send "to@x.com" "subj" "body"')
        print('  python helper.py send-zh "to@x.com" "English subj" "中文内容"')
        print('  python helper.py render-zh "中文内容"   # 渲染到 test_output.png')
        print('  python helper.py search "query"')