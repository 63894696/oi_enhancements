#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prisir 值守审核台(本机网页)— 经 SSH 隧道连 VPS 审批口(127.0.0.1:18816)。

用法:
  1. 先开隧道:  ssh -i ~/.ssh/id_ed25519 -p 49108 -L 18816:127.0.0.1:18816 -N root@192.220.14.165
     (或用 --tunnel 让本脚本自己拉起隧道子进程)
  2. python steward_console.py  → 浏览器开 http://127.0.0.1:18860

页面:待审反馈列表(来源/作者/分类/理由/LLM标记) → 点开看正文+LLM 草稿(可改) →
      「批准发送」(AgentMail 才真发;论坛帖提示需人工回帖)/「拒绝」/「跳过」。
红线:发送动作必须人在网页点「批准」才触发,审核台本身绝不自动发。
"""
from __future__ import annotations
import json, subprocess, sys, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VPS_LOCAL = "127.0.0.1:18826"   # 隧道落地的本机端口(18816 被本机残留进程占用,故用 18826)→ 转发到 VPS 18816
CONSOLE_PORT = 18860
SSH = ["ssh", "-i", str(Path.home()/".ssh"/"id_ed25519"), "-p", "49108",
       "-L", f"{VPS_LOCAL.split(':')[0]}:127.0.0.1:18816", "-N",
       "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
       "root@192.220.14.165"]

def _vps(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{VPS_LOCAL}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"{}")

def tunnel_up():
    try:
        with urllib.request.urlopen(f"http://{VPS_LOCAL}/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False

PAGE = """<!doctype html><html lang=zh><head><meta charset=utf-8><title>Prisir 值守审核台</title>
<style>
body{margin:0;font-family:system-ui,"Microsoft YaHei",sans-serif;background:#f6f1e7;color:#333}
header{background:#0f1a24;color:#e8d9b8;padding:14px 20px;display:flex;align-items:center;gap:12px}
header b{color:#c98a4b}
.badge{background:#c98a4b;color:#0f1a24;border-radius:10px;padding:1px 9px;font-size:13px;font-weight:600}
.wrap{display:flex;height:calc(100vh - 52px)}
#list{width:340px;border-right:1px solid #ddd3bd;overflow:auto;background:#fbf7ee}
.item{padding:12px 14px;border-bottom:1px solid #eee3cd;cursor:pointer}
.item:hover{background:#f3ead6}.item.active{background:#ecdfbe}
.item .s{font-size:12px;color:#8a7f63}
.tag{display:inline-block;font-size:11px;border-radius:8px;padding:0 7px;margin-left:6px}
.faq{background:#dfe9d8;color:#3c6b3c}.high{background:#f0d8cf;color:#a33}.llmy{color:#2f8f83}.llmn{color:#b00}
#detail{flex:1;padding:20px;overflow:auto}
h2{margin:0 0 4px;font-size:19px}.meta{color:#8a7f63;font-size:13px;margin-bottom:14px}
.box{background:#fffdf7;border:1px solid #e6dcc2;border-radius:8px;padding:14px;margin-bottom:14px;white-space:pre-wrap;font-size:14px}
textarea{width:100%;min-height:170px;font-family:inherit;font-size:14px;padding:10px;border:1px solid #d8ccab;border-radius:8px;background:#fff;box-sizing:border-box}
button{border:0;border-radius:8px;padding:10px 18px;font-size:14px;cursor:pointer;margin-right:10px}
.send{background:#2f8f83;color:#fff}.rej{background:#b06a4a;color:#fff}.skip{background:#cfc4a5;color:#4a4436}
#msg{margin-top:12px;font-size:14px;min-height:20px}
.empty{color:#9a8f70;text-align:center;margin-top:60px}
</style></head><body>
<header><b>◔ Prisir 值守审核台</b><span class=badge id=count>…</span><span style="font-size:13px;color:#b8ab8a">待审反馈 · 批准才发送(红线:不自动外发)</span>
<button onclick="load()" style="margin-left:auto;background:#c98a4b;color:#0f1a24">刷新</button></header>
<div class=wrap><div id=list></div><div id=detail><div class=empty>← 选一条待审反馈</div></div></div>
<script>
let Q=[], cur=null;
async function api(p,m,b){const r=await fetch(p,{method:m||'GET',headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});return r.json();}
async function load(){
  const h=await api('/api/health'); Q=(await api('/api/queue')).pending||[];
  document.getElementById('count').textContent='待审 '+Q.length;
  const L=document.getElementById('list');
  if(!Q.length){L.innerHTML='<div class=empty>没有待审反馈<br><br>值守 agent 正常监听中</div>';document.getElementById('detail').innerHTML='<div class=empty>全部审完 ✓</div>';return;}
  L.innerHTML=Q.map(i=>`<div class="item ${cur&&cur.id==i.id?'active':''}" onclick="open1('${i.id}')">
    <div>${esc(i.subject||'(无标题)')}</div>
    <div class=s>${i.source} · ${esc((i.author||'').slice(0,18))}
    <span class="tag ${i.class=='faq'?'faq':'high'}">${i.class=='faq'?'常见':'高价值'}</span>
    <span class="tag ${i.llm?'llmy':'llmn'}">${i.llm?'AI起草':'规则'}</span></div></div>`).join('');
}
async function open1(id){cur=await api('/api/draft/'+id);render();load();}
function render(){const d=document.getElementById('detail');if(!cur)return;
  d.innerHTML=`<h2>${esc(cur.subject||'(无标题)')}</h2>
  <div class=meta>来源 ${cur.source} · 作者 ${esc(cur.author||'')} · 分类 <b>${cur.class=='faq'?'常见问答':'高价值(建议转人)'}</b> · ${esc(cur.reason||'')} · ${cur.created_at||''}</div>
  <div style="font-size:13px;color:#8a7f63;margin-bottom:4px">用户原文</div><div class=box>${esc(cur.body_raw||'(无正文)')}</div>
  <div style="font-size:13px;color:#8a7f63;margin-bottom:4px">回复草稿(可编辑)</div>
  <textarea id=draft>${esc(cur.draft||'')}</textarea>
  <div style="margin-top:14px">
  ${cur.source=='agentmail'?'<button class=send onclick="decide(1)">✓ 批准发送(邮件)</button>':'<button class=send disabled title="论坛帖需人工回帖" style="opacity:.5">论坛帖需人工回帖</button>'}
  <button class=rej onclick="decide(0)">✗ 拒绝</button>
  <span id=msg></span></div>`;}
async function decide(ok){if(!cur)return;const m=document.getElementById('msg');
  if(ok){const text=document.getElementById('draft').value;const r=await api('/api/approve','POST',{id:cur.id,text});
    m.textContent=r.ok?'✅ 已发送':'发送失败: '+(r.result&&r.result.error||'');}
  else{await api('/api/reject','POST',{id:cur.id});m.textContent='已拒绝';}
  cur=null;setTimeout(load,800);}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
load();setInterval(load,30000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def _j(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
    def do_GET(self):
        try:
            if self.path == "/": return self._page()
            if self.path == "/api/health": return self._j(200, _vps("/health"))
            if self.path == "/api/queue": return self._j(200, _vps("/queue"))
            if self.path.startswith("/api/draft/"): return self._j(200, _vps("/draft/" + self.path.split("/api/draft/", 1)[1]))
            return self._j(404, {"error": "unknown"})
        except Exception as e:
            return self._j(502, {"error": f"VPS 隧道不通: {e}. 先开隧道: {' '.join(SSH)}"})
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", "0") or 0)
        try: body = json.loads(self.rfile.read(ln) or b"{}")
        except Exception: body = {}
        try:
            if self.path == "/api/approve": return self._j(200, _vps("/approve", "POST", body))
            if self.path == "/api/reject": return self._j(200, _vps("/reject", "POST", body))
            return self._j(404, {"error": "unknown"})
        except Exception as e:
            return self._j(502, {"error": str(e)})
    def _page(self):
        b = PAGE.encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)


def main():
    if "--tunnel" in sys.argv:
        if not tunnel_up():
            print("[console] 拉起 SSH 隧道…")
            subprocess.Popen(SSH, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import time
            for _ in range(20):
                if tunnel_up(): break
                time.sleep(0.5)
    ok = tunnel_up()
    print(f"[console] VPS 审批口: {'通' if ok else '不通(先开隧道)'}  →  http://127.0.0.1:{CONSOLE_PORT}")
    ThreadingHTTPServer(("127.0.0.1", CONSOLE_PORT), H).serve_forever()

if __name__ == "__main__":
    main()
