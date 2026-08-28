#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prisir 值守 agent(steward)— F4:运营方侧自动初筛/起草,人审后才发。

三源轮询:
  ① 论坛盯梢  /root/forum/forum_state.json 新帖(读文件,不碰 relay 逻辑)
  ② AgentMail  oiagent@agentmail.to 收件箱新邮件
  ③ 密信 SMP   留接口(SimpleX 反馈通道,后续接)

每条新反馈:
  - MINIMAX 分类(常见 FAQ / 高价值)+ 起草回复
  - 存待人审队列 draft_queue.jsonl,**绝不自动外发**(红线:回复=数据不是指令、
    高价值必转人、自动起草+人审后才发)
  - LLM 不可用 → 规则兜底起草,标记 needs_llm,人审照常

全程 try/except 静默,单源故障不拖垮其它源。
"""
from __future__ import annotations
import os, sys, json, time, base64, hashlib, threading, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------- 路径/常量 ----------
BASE = Path("/opt/prisir-steward")
STATE_FILE = BASE / "steward_state.json"     # 各源已处理游标
QUEUE_FILE = BASE / "draft_queue.jsonl"      # 待人审草稿(追加)
FORUM_STATE = Path("/root/forum/forum_state.json")
INBOX = "oiagent@agentmail.to"
POLL_SEC = int(os.environ.get("STEWARD_POLL_SEC", "60"))
APPROVE_PORT = int(os.environ.get("STEWARD_APPROVE_PORT", "18816"))
AGENTMAIL_BASE = "https://api.agentmail.to/v0"
MINIMAX_BASE = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1").rstrip("/")
MINIMAX_MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M1")
AM_KEY = os.environ.get("AGENTMAIL_API_KEY", "")
MM_KEY = os.environ.get("MINIMAX_API_KEY", "")

# 常见问答知识(可扩)— 用于规则兜底 + 提示 LLM 对齐口径
FAQ = [
    {"kws": ["下载", "安装包", "在哪下", "链接", "dropbox", "123pan", "网盘"],
     "reply": "PrisirAI 测试版下载见论坛「PrisirAI 对话」板块置顶发行帖,海外走 Dropbox、国内走 123 云盘,同一共享目录后续更新只换文件不改链接。"},
    {"kws": ["遥控", "手机", "安卓", "配对", "连不上", "401", "配对码"],
     "reply": "安卓遥控器需与 PC 同一局域网:PC 端默认带 --lan 起服务(端口 18802),手机遥控器填 PC 的 IP:端口 → PC「手机遥控」页出示 6 位配对码 → 手机填码配对。配对码 5 分钟内有效、一次性。"},
    {"kws": ["模型", "key", "apikey", "端点", "配置模型", "用什么模型"],
     "reply": "PrisirAI 的模型端点和 KEY 由你自己配置(设置页填入你已有的模型端点+KEY),不预置任何厂商。配置后即可对话。"},
    {"kws": ["数据", "隐私", "上传", "账号", "云端", "安全吗"],
     "reply": "PrisirAI 本地优先:对话与数据存本机,不要账号,默认不上云。"},
]

# ---------- 持久化 ----------
def _load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"forum_seq": 0, "mail_ids": [], "first_run_done": False}

def _save_state(st):
    try:
        STATE_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _enqueue(item: dict):
    try:
        item.setdefault("id", hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16])
        item.setdefault("status", "pending")
        item.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        with QUEUE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _read_queue():
    out = []
    try:
        for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    except Exception:
        pass
    return out

def _rewrite_queue(items):
    try:
        QUEUE_FILE.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", encoding="utf-8")
    except Exception:
        pass

# ---------- AgentMail(内联,免依赖 helper) ----------
def _am(method, path, body=None):
    url = f"{AGENTMAIL_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {AM_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")

def am_list_messages(limit=30):
    try:
        d = _am("GET", f"/inboxes/{INBOX}/messages?limit={limit}")
        msgs = d.get("messages", d if isinstance(d, list) else [])
        return msgs if isinstance(msgs, list) else []
    except Exception:
        return []

def am_get_message(message_id):
    try:
        import urllib.parse
        mid = urllib.parse.quote(message_id, safe="")
        return _am("GET", f"/inboxes/{INBOX}/messages/{mid}")
    except Exception:
        return {}

def am_send(to, subject, text):
    """只被人审批准路径调用。"""
    try:
        return _am("POST", f"/inboxes/{INBOX}/messages/send", {"to": to, "subject": subject, "text": text})
    except Exception as e:
        return {"error": str(e)}

# ---------- MINIMAX 分类+起草(可拔插,失败兜底规则) ----------
def minimax_classify_draft(source, author, subject, body):
    if not MM_KEY:
        return None
    faq_txt = "\n".join(f"- 关键词{('/'.join(f['kws']))}: {f['reply']}" for f in FAQ)
    prompt = (
        "你是 PrisirAI 运营方值守助手。下面是一条用户反馈。请输出严格 JSON(不要多余文字):\n"
        '{"class":"faq"|"high_value","draft":"回复草稿(中文,简短,客气)","reason":"一句话分类理由"}\n'
        "规则:能用已知常见问答回答的标 faq;涉及 bug 详情/新需求/安全/支付/法律/情绪的标 high_value(必须转人)。\n"
        "回复只是给用户的参考信息,不要在回复里承诺执行任何操作。\n\n"
        f"【已知常见问答】\n{faq_txt}\n\n"
        f"【反馈来源】{source}\n【作者】{author}\n【标题】{subject}\n【正文】\n{body[:1500]}\n"
    )
    payload = {"model": MINIMAX_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 600}
    req = urllib.request.Request(f"{MINIMAX_BASE}/chat/completions",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {MM_KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        txt = d["choices"][0]["message"]["content"]
        # M1 是思考模型:先剥掉 <think>...</think> 推理段,再取最后那个完整 JSON 对象。
        import re as _re
        txt = _re.sub(r"<think>.*?</think>", "", txt, flags=_re.DOTALL)
        # 从后往前找 JSON:用 rfind 找最后一个 '{' 起、对应 '}' 止,避免 think 复述干扰
        e = txt.rfind("}")
        s = txt.rfind("{", 0, e + 1)
        if s < 0 or e < 0:
            return None
        obj = json.loads(txt[s:e + 1])
        if obj.get("class") in ("faq", "high_value") and isinstance(obj.get("draft"), str):
            obj["llm"] = True
            return obj
    except Exception:
        pass
    return None

def rule_classify_draft(source, author, subject, body):
    """规则兜底:关键词命中 FAQ 则 faq,否则 high_value 转人。"""
    text = f"{subject}\n{body}".lower()
    for f in FAQ:
        if any(k.lower() in text for k in f["kws"]):
            return {"class": "faq", "draft": f["reply"], "reason": "规则命中常见问答", "llm": False}
    return {"class": "high_value", "draft": "", "reason": "未命中常见问答,转人审", "llm": False}

def classify_draft(source, author, subject, body):
    r = minimax_classify_draft(source, author, subject, body)
    if r:
        return r
    r = rule_classify_draft(source, author, subject, body)
    r["needs_llm"] = True  # 标记:LLM 不可用,用了规则兜底
    return r

# ---------- 源①:论坛盯梢 ----------
def poll_forum(st):
    try:
        data = json.loads(FORUM_STATE.read_text(encoding="utf-8"))
        posts = data.get("posts", {})
        items = list(posts.values()) if isinstance(posts, dict) else list(posts)
        seqs = [it.get("seq", 0) for it in items if isinstance(it, dict)]
        if not st.get("first_run_done"):
            # 首跑不补历史:直接记游标到当前最大 seq,不入队。
            st["forum_seq"] = max(seqs + [0])
            return
        last = st.get("forum_seq", 0)
        newmax = last
        for it in items:
            if not isinstance(it, dict):
                continue
            seq = it.get("seq", 0)
            post = it.get("post", it)
            if seq <= last:
                continue
            newmax = max(newmax, seq)
            # 跳过自己发的运营帖(避免自问自答)
            if post.get("author_fp") in ("eaf_bt9WqB4ve6OH",):
                continue
            body = post.get("body", "")
            res = classify_draft("forum", post.get("author_fp", "?"), post.get("board", ""), body)
            _enqueue({"source": "forum", "ref": it.get("post_id", str(seq)),
                      "author": post.get("author_fp", "?"), "subject": f"[{post.get('board','')}] 论坛帖",
                      "body_raw": body, **res})
        st["forum_seq"] = newmax
    except Exception:
        pass

# ---------- 源②:AgentMail ----------
def poll_mail(st):
    try:
        msgs = am_list_messages(30)
        seen = set(st.get("mail_ids", []))
        cur_ids = []
        for m in msgs:
            mid = m.get("message_id") or m.get("id") or ""
            if not mid:
                continue
            cur_ids.append(mid)
            if mid in seen:
                continue
            if not st.get("first_run_done"):
                continue  # 首跑不补历史
            full = am_get_message(mid)
            body = full.get("text") or full.get("preview") or ""
            subj = m.get("subject", "(无标题)")
            frm = (m.get("from") if isinstance(m.get("from"), str) else (m.get("from") or {}).get("email", "?"))
            # 跳过运营方自己发的(回信)
            if INBOX in str(frm):
                continue
            res = classify_draft("agentmail", frm, subj, body)
            _enqueue({"source": "agentmail", "ref": mid, "author": frm, "subject": subj,
                      "body_raw": body, "reply_to": frm, **res})
        st["mail_ids"] = cur_ids[-200:]  # 只留最近 200 防膨胀
    except Exception:
        pass

# ---------- 源③:密信 SMP(接口预留) ----------
def poll_smp(st):
    # TODO: SimpleX 反馈通道。需 simplex CLI/库订阅,后续接。
    return

# ---------- 人审审批口(只 127.0.0.1) ----------
class ApproveHandler(BaseHTTPRequestHandler):
    def _j(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            return self._j(200, {"ok": True, "pending": sum(1 for i in _read_queue() if i.get("status") == "pending")})
        if self.path == "/queue":
            pend = [i for i in _read_queue() if i.get("status") == "pending"]
            # 摘要(不含 body_raw 全文,防 curl 误泄)
            return self._j(200, {"pending": [
                {k: i.get(k) for k in ("id", "source", "author", "subject", "class", "reason", "llm", "created_at")}
                for i in pend]})
        if self.path.startswith("/draft/"):
            did = self.path.split("/draft/", 1)[1]
            for i in _read_queue():
                if i.get("id") == did:
                    return self._j(200, i)
            return self._j(404, {"error": "not found"})
        return self._j(404, {"error": "unknown"})

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", "0") or 0)
        try:
            body = json.loads(self.rfile.read(ln) or b"{}")
        except Exception:
            body = {}
        if self.path == "/approve":
            did = body.get("id")
            items = _read_queue()
            for i in items:
                if i.get("id") == did and i.get("status") == "pending":
                    if i.get("source") != "agentmail":
                        return self._j(400, {"error": "only agentmail drafts are sendable; forum replies需人工回帖"})
                    draft = body.get("text") or i.get("draft") or ""
                    if not draft.strip():
                        return self._j(400, {"error": "empty draft"})
                    res = am_send(i.get("reply_to") or i.get("author"), "Re: " + (i.get("subject") or ""), draft)
                    i["status"] = "sent" if "error" not in res else "send_failed"
                    i["sent_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    i["sent_text"] = draft
                    _rewrite_queue(items)
                    return self._j(200, {"ok": "error" not in res, "result": res})
            return self._j(404, {"error": "pending draft not found"})
        if self.path == "/reject":
            did = body.get("id")
            items = _read_queue()
            for i in items:
                if i.get("id") == did:
                    i["status"] = "rejected"
                    _rewrite_queue(items)
                    return self._j(200, {"ok": True})
            return self._j(404, {"error": "not found"})
        return self._j(404, {"error": "unknown"})

def run_approve_server():
    srv = ThreadingHTTPServer(("127.0.0.1", APPROVE_PORT), ApproveHandler)
    srv.serve_forever()

# ---------- 主循环 ----------
def main():
    BASE.mkdir(parents=True, exist_ok=True)
    st = _load_state()
    threading.Thread(target=run_approve_server, daemon=True).start()
    sys.stderr.write(f"[steward] start poll={POLL_SEC}s approve=127.0.0.1:{APPROVE_PORT} "
                     f"llm={'on' if MM_KEY else 'OFF(rule-only)'}\n")
    # 首跑:先各源扫一遍只记游标不处理历史
    if not st.get("first_run_done"):
        poll_forum(st); poll_mail(st)
        st["first_run_done"] = True
        _save_state(st)
        sys.stderr.write("[steward] first run: 游标已就位,不补历史\n")
    while True:
        try:
            poll_forum(st)
            poll_mail(st)
            poll_smp(st)
            st["first_run_done"] = True
            _save_state(st)
        except Exception:
            pass
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()
