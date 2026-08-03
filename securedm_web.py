"""securedm_web.py — Agent-First OS 阶段 3.A SecureDM 自研 1:1 IM 端(L4)

架构 §3.A:SimpleX 协议自有前端,1:1 E2E 加密私信。复用 simplex_runtime/tools/integrity,
做成"联系人列表 + 会话窗"的 IM 形态,交互仍只有对话/确认(架构 §7.4)。

与 l4_web 的关系:l4_web 是"人↔agent 对话反馈窗"(走 daemon 工具循环);SecureDM 是
"人↔人 / 人↔agent 的 1:1 私信端",直接驱动 simplex_runtime(不经 daemon ask)。

核心功能:
  - 联系人列表(simplex_list_contacts)+ 新建邀请(simplex_create_invitation 生成链接给人)
  - 会话窗:读消息(chat_texts 持久历史)+ 发消息(simplex_send_message)
  - 文件消息:列出待下载文件(simplex_list_incoming_files)+ 下载(simplex_receive_file)
  - **签名验证标**:收到的文件若带有效签名清单,显示"✓ 已验证来自 X"(simplex_verify_file_by_manifest);
    验证失败醒目提示。发送方可选 simplex_send_file_signed 发签名文件。
  - token 认证(与 l4_web 同一信任根文件)。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simplex_tools as st  # noqa: E402
import simplex_files as sf  # noqa: E402
import simplex_integrity as si  # noqa: E402
import simplex_a2h as sa  # noqa: E402
from simplex_runtime import SimplexRuntime  # noqa: E402

DM_HOST = os.environ.get("DM_HOST", "127.0.0.1")
DM_PORT = int(os.environ.get("DM_PORT", "18801"))
# 实例身份(多实例部署时区分):DM_IDENTITY=显示名,DM_DB=该实例独立的 simplex 数据目录前缀
DM_IDENTITY = os.environ.get("DM_IDENTITY", "oiagent")
DM_DB_PREFIX = os.environ.get("DM_DB_PREFIX", "")
_TOKEN_FILE = Path.home() / ".local" / "share" / "aureon" / "l4_token"


def _token() -> str:
    try:
        if _TOKEN_FILE.exists():
            return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("L4_TOKEN", "")


_ACCESS_TOKEN = _token()


def _ok(o, **e):
    return {"ok": True, "output": o, **e}


def _err(r, d="", **e):
    return {"ok": False, "error": r, "diagnosable": d, **e}


def _rt() -> SimplexRuntime:
    return SimplexRuntime.instance()


# ────────────────────────────────────────────────────────────────────── #
# 后端 API 实现
# ────────────────────────────────────────────────────────────────────── #

def api_status() -> dict[str, Any]:
    rt = _rt()
    running = rt._thread is not None and rt._thread.is_alive()
    if not running:
        return _ok({"running": False})
    st_ = rt.status()
    contacts = rt.list_contacts()
    return _ok({
        "running": True,
        "active_user": st_.get("active_user"),
        "server": st_.get("smp_server"),
        "contacts": contacts,
    })


def api_setup(display_name: str = "") -> dict[str, Any]:
    rt = _rt()
    if rt._thread and rt._thread.is_alive():
        return _ok({"running": True, "note": "已在运行"})
    name = display_name or DM_IDENTITY
    args: dict[str, Any] = {"display_name": name}
    if DM_DB_PREFIX:
        args["db_prefix"] = DM_DB_PREFIX
    r = st.call_tool("simplex_setup", args)
    return r


def api_create_invite() -> dict[str, Any]:
    return st.call_tool("simplex_create_invitation", {})


def api_contacts() -> dict[str, Any]:
    rt = _rt()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("runtime 未启动", "先 setup")
    contacts = rt.list_contacts()
    # 每个联系人附带未读/最近一条预览
    out = []
    for c in contacts:
        cid = c["contact_id"]
        try:
            texts = rt.chat_texts(cid, limit=1)
            preview = texts[-1][:60] if texts else ""
        except Exception:  # noqa: BLE001
            preview = ""
        out.append({**c, "preview": preview})
    return _ok(out)


def api_delete_contact(contact: str) -> dict[str, Any]:
    return st.call_tool("simplex_delete_contact", {"contact": contact})


def api_history(contact: str, limit: int = 60) -> dict[str, Any]:
    rt = _rt()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("runtime 未启动", "先 setup")
    resolved = rt.resolve_contact(contact)
    if resolved is None:
        return _err(f"没有联系人 {contact}", "先接受邀请")
    cid = resolved["contact_id"]
    # 用 chat_items(带方向 me/them + 完整历史),不是 chat_texts(无方向,易只显示一条)
    items = rt.chat_items(cid, limit=limit)
    msgs = [{"id": it["id"], "dir": it["dir"], "kind": it["kind"], "text": it["text"], "ts": it["ts"]} for it in items]
    # 待下载文件邀请
    files = rt.list_inbox_files(cid)
    return _ok({"contact": resolved, "messages": msgs, "incoming_files": files})


def api_send(contact: str, text: str) -> dict[str, Any]:
    return st.call_tool("simplex_send_message", {"contact": contact, "text": text})


# ── 2 人 E2E 通话(轻量信令经 E2E 加密通道,媒体 P2P WebRTC)───────────────
# 信令 = JSON 消息,前缀 [DMCALL];经 simplex_send_message(已 E2E 加密)传输。
# 浏览器侧原生 RTCPeerConnection 收发媒体;后端只搬信令,不碰媒体。
_CALL_PREFIX = "[DMCALL]"


def api_call_signal(contact: str, signal: dict) -> dict[str, Any]:
    """把一条通话信令(offer/answer/ice/end)作为 JSON 消息发给联系人。"""
    import json as _json
    payload = _CALL_PREFIX + _json.dumps(signal, ensure_ascii=False)
    return st.call_tool("simplex_send_message", {"contact": contact, "text": payload})


def api_call_poll(contact: str, since_id: int = 0) -> dict[str, Any]:
    """拉取该联系人最近的通话信令([DMCALL] 前缀的消息)。

    返回 [{id, dir, signal, ts}],id = chat item 的稳定 itemId。
    **增量按 since_id(消息 id),不是时间戳** —— 时间戳跨时钟比较不可靠
    (服务器 UTC vs 浏览器本地钟,毫秒/格式不一),曾导致"等待接听无反应"。
    since_id:只返回 itemId 大于它的信令。"""
    rt = _rt()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("runtime 未启动", "先 setup")
    resolved = rt.resolve_contact(contact)
    if resolved is None:
        return _err(f"没有联系人 {contact}", "先接受邀请")
    cid = resolved["contact_id"]
    items = rt.chat_items(cid, limit=60)
    import json as _json
    out = []
    for it in items:
        t = it.get("text", "")
        if not t.startswith(_CALL_PREFIX):
            continue
        iid = it.get("id") or 0
        if since_id and iid <= since_id:
            continue
        try:
            sig = _json.loads(t[len(_CALL_PREFIX):])
        except Exception:  # noqa: BLE001
            continue
        out.append({"id": iid, "dir": it["dir"], "signal": sig, "ts": it.get("ts", "")})
    return _ok(out)


def api_receive_file(file_id: int) -> dict[str, Any]:
    return sf.call_tool("simplex_receive_file", {"file_id": file_id, "timeout": 60})


def api_verify_file(contact: str, file_name: str) -> dict[str, Any]:
    return si.call_tool("simplex_verify_file_by_manifest", {"contact": contact, "file_name": file_name})


def api_send_file_signed(contact: str, path: str) -> dict[str, Any]:
    return si.call_tool("simplex_send_file_signed", {"contact": contact, "path": path})


def api_trust_establish(contact: str) -> dict[str, Any]:
    return si.call_tool("simplex_trust_establish", {"contact": contact})


def api_accept_invite(link: str) -> dict[str, Any]:
    return st.call_tool("simplex_accept_invitation", {"link": link, "timeout": 100})


def api_trust_import(contact: str, key: str) -> dict[str, Any]:
    return si.call_tool("simplex_trust_import", {"contact": contact, "key": key})


def api_send_file(contact: str, path: str, signed: bool) -> dict[str, Any]:
    """发送文件(可选带签名清单)。signed=true 走 simplex_send_file_signed。"""
    if signed:
        return si.call_tool("simplex_send_file_signed", {"contact": contact, "path": path})
    return sf.call_tool("simplex_send_file", {"contact": contact, "path": path})


def api_file_info(path: str) -> dict[str, Any]:
    return si.call_tool("simplex_file_info", {"path": path})


def api_a2h_status() -> dict[str, Any]:
    """A2H 审批状态:approver + 待裁决列表(供 UI 显示审批卡)。"""
    rt = _rt()
    pend = sa.call_tool("simplex_a2h_pending", {})
    return _ok({
        "approver": rt._a2h_approver_name,
        "approver_cid": rt._a2h_approver_cid,
        "pending": pend.get("output", []) if pend.get("ok") else [],
    })


def api_a2h_set_approver(contact: str) -> dict[str, Any]:
    return sa.call_tool("simplex_a2h_set_approver", {"contact": contact})


# ────────────────────────────────────────────────────────────────────── #
# 嵌入式 IM 界面
# ────────────────────────────────────────────────────────────────────── #

_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SecureDM · 加密私信</title>
<style>
  :root{--bg:#0f1115;--panel:#171a21;--line:#23262f;--txt:#e6e8ec;--dim:#9aa3b2;--acc:#4c8dff;--ok:#3fb27f;--warn:#e0a34a;--bad:#e06a6a;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 -apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;height:100vh;display:flex;flex-direction:column}
  header{padding:11px 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}
  header .dot{width:9px;height:9px;border-radius:50%;background:var(--ok)}
  header h1{font-size:16px;margin:0;font-weight:600}
  header .st{margin-left:auto;font-size:12px;color:var(--dim)}
  #main{flex:1;display:flex;min-height:0}
  #contacts{width:250px;border-right:1px solid var(--line);overflow-y:auto;display:flex;flex-direction:column}
  #contacts .hd{padding:12px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
  #contacts .hd b{font-size:14px}
  #contacts .hd button{background:var(--acc);border:none;color:#fff;padding:5px 10px;border-radius:7px;cursor:pointer;font-size:12px}
  .citem{padding:11px 12px;border-bottom:1px solid #1a1d24;cursor:pointer}
  .citem:hover{background:#1a1e26}
  .citem.active{background:#1d2330}
  .citem .nm{font-weight:600;font-size:14px}
  .citem .pv{font-size:12px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #chat{flex:1;display:flex;flex-direction:column;min-width:0}
  #msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:6px}
  /* IM 布局惯例(参照 WeChat/iMessage/WhatsApp 调研):
     我=右侧+品牌色,对方=左侧+中性色;气泡角在发送方一侧收平;系统消息居中灰色小字。
     位置+颜色+形状三重区分,不单靠颜色(色盲/暗色模式友好)。 */
  .m{max-width:78%;padding:9px 13px;font-size:15px;line-height:1.4;white-space:pre-wrap;word-break:break-word;box-shadow:0 1px 2px rgba(0,0,0,.28)}
  /* 我:右侧,品牌蓝,右下角收平(LTR 发送方侧) */
  .m.me{align-self:flex-end;background:linear-gradient(135deg,#3b82d6,#2b6cb0);color:#fff;border-radius:18px 18px 4px 18px}
  /* 对方:左侧,中性深灰 + 左边框,左下角收平 */
  .m.them{align-self:flex-start;background:#232833;color:#e6e8ec;border-left:3px solid #4a5568;border-radius:18px 18px 18px 4px}
  /* 系统/签名清单:居中、灰小字、无气泡感 */
  .m.manifest{align-self:center;background:rgba(255,255,255,.05);border:1px dashed #3a4150;font-size:12px;color:var(--dim);border-radius:8px;max-width:88%}
  .vbadge{display:inline-flex;align-items:center;gap:5px;font-size:12px;padding:3px 9px;border-radius:11px;margin-top:5px}
  .vbadge.ok{background:#153326;color:var(--ok);border:1px solid var(--ok)}
  .vbadge.bad{background:#3a1d1d;color:var(--bad);border:1px solid var(--bad)}
  .vbadge.pending{background:#2a2e38;color:var(--dim)}
  .fmsg{align-self:flex-start;background:#1e222b;padding:10px 12px;border-radius:11px;border:1px solid #2a2e38}
  .fmsg .fname{font-weight:600}
  .fmsg button{background:var(--acc);border:none;color:#fff;padding:5px 12px;border-radius:7px;cursor:pointer;font-size:12px;margin-top:6px}
  .sys{align-self:center;font-size:12px;color:var(--dim)}
  #composer{display:flex;gap:9px;padding:13px 16px;border-top:1px solid var(--line)}
  #in{flex:1;background:var(--panel);border:1px solid #2a2e38;color:var(--txt);border-radius:9px;padding:10px 12px;outline:none}
  #in:focus{border-color:var(--acc)}
  #composer button{background:var(--acc);border:none;color:#fff;padding:0 17px;border-radius:9px;cursor:pointer;font-weight:600}
  #empty{flex:1;display:flex;align-items:center;justify-content:center;color:var(--dim);flex-direction:column;gap:10px}
  #inviteModal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center}
  #inviteModal .box{background:var(--panel);padding:20px;border-radius:12px;max-width:560px;width:90%}
  #inviteModal textarea{width:100%;height:90px;background:#0d0f13;color:var(--txt);border:1px solid #2a2e38;border-radius:8px;padding:8px;font-size:12px}
  #inviteModal button{margin-top:10px;background:var(--acc);border:none;color:#fff;padding:7px 14px;border-radius:8px;cursor:pointer}
</style>
</head>
<body>
<header><span class="dot" id="livedot"></span><h1>SecureDM 加密私信</h1><span class="st" id="status">…</span></header>
<div id="main">
  <div id="contacts">
    <div class="hd"><b>联系人</b><span><button onclick="trustFlow()" title="与选中联系人建立文件签名信任根">🔑</button><button onclick="newInvite()">+ 邀请</button></span></div>
    <div id="clist"></div>
  </div>
  <div id="chat">
    <div id="msgs"><div id="empty"><div>选择左侧联系人开始加密对话</div><div style="font-size:12px">或点 + 邀请 生成一次性链接发给对方</div></div></div>
    <div id="composer">
      <input id="in" placeholder="发 E2E 加密消息…" disabled>
      <button id="send" onclick="sendMsg()">发送</button>
      <button id="attachBtn" title="发送文件" onclick="toggleAttach()">📎</button>
      <button id="callBtn" title="语音通话" onclick="startCall(false)">📞</button>
      <button id="videoBtn" title="视频通话" onclick="startCall(true)">📹</button>
    </div>
    <div id="callPanel" style="display:none;padding:10px 14px;border-top:1px solid var(--line);background:#14161c">
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <video id="remoteVideo" autoplay playsinline style="width:280px;background:#000;border-radius:8px"></video>
        <video id="localVideo" autoplay playsinline muted style="width:120px;background:#000;border-radius:8px"></video>
        <div style="display:flex;flex-direction:column;gap:7px">
          <div id="callStatus" style="font-size:13px;color:var(--dim)">呼叫中…</div>
          <div style="display:flex;gap:7px">
            <button onclick="toggleMic()" id="micBtn" style="background:#3a3f4b;border:none;color:#fff;padding:6px 12px;border-radius:7px;cursor:pointer">🎤 静音</button>
            <button onclick="toggleCam()" id="camBtn" style="background:#3a3f4b;border:none;color:#fff;padding:6px 12px;border-radius:7px;cursor:pointer">📷 关视频</button>
            <button onclick="switchCamera()" id="switchCamBtn" title="切换前后摄像头" style="background:#3a3f4b;border:none;color:#fff;padding:6px 12px;border-radius:7px;cursor:pointer">🔄 切换摄像头</button>
            <button onclick="shareScreen()" id="screenBtn" style="background:#3a3f4b;border:none;color:#fff;padding:6px 12px;border-radius:7px;cursor:pointer">🖥️ 共享屏幕</button>
            <button onclick="endCall()" style="background:var(--bad);border:none;color:#fff;padding:6px 12px;border-radius:7px;cursor:pointer">挂断</button>
          </div>
        </div>
      </div>
    </div>
    <div id="attach" style="display:none;padding:9px 16px;border-top:1px solid var(--line);gap:8px;align-items:center">
      <input id="fpath" placeholder="文件绝对路径(须在允许目录内)" style="flex:1;background:var(--panel);border:1px solid #2a2e38;color:var(--txt);border-radius:8px;padding:8px 10px">
      <label style="font-size:13px;color:var(--dim);display:flex;align-items:center;gap:4px"><input type="checkbox" id="fsigned" checked>带签名清单</label>
      <button onclick="sendFile()" style="background:var(--acc);border:none;color:#fff;padding:7px 14px;border-radius:8px;cursor:pointer">发文件</button>
    </div>
  </div>
</div>
<div id="inviteModal"><div class="box"><b>一次性邀请链接</b><div style="font-size:12px;color:var(--dim);margin:6px 0">发给对方,在其 SimpleX/SecureDM 里"通过链接连接"接受即建立 E2E 联系</div><textarea id="inviteLink" readonly></textarea><button onclick="copyInvite()">复制</button> <button onclick="closeInvite()">关闭</button></div></div>
<script>
const TOKEN=(()=>{const q=new URLSearchParams(location.search).get('token');if(q){localStorage.setItem('dm_token',q);history.replaceState({},'',location.pathname);return q;}return localStorage.getItem('dm_token')||'';})();
// WS server 地址由服务端注入(电脑 LAN IP,手机经 WiFi 可达;本机访问时服务端填 127.0.0.1)
const __WS_HOST__ = '__WS_HOST_VALUE__';
const H=(e)=>{const h=Object.assign({},e||{});if(TOKEN)h['X-L4-Token']=TOKEN;return h;};
let cur=null;
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function setSt(t,ok){document.getElementById('status').textContent=t;document.getElementById('livedot').style.background=ok===false?'var(--bad)':'var(--ok)';}
async function api(path,opts){const r=await fetch(path,Object.assign({headers:H({'Content-Type':'application/json'})},opts||{}));return r.json();}

async function refresh(){
  const s=await api('/dm/api/status');
  if(!s.ok){setSt('错误',false);return;}
  if(!s.output.running){setSt('未初始化 — 点击启动',false);
    document.getElementById('msgs').innerHTML='<div id="empty"><button style="background:var(--acc);border:none;color:#fff;padding:10px 20px;border-radius:9px;cursor:pointer" onclick="doSetup()">启动 SecureDM(初始化身份)</button></div>';return;}
  setSt((s.output.active_user||'')+' · 已连接',true);
  window._activeUser = s.output.active_user || 'me';  // WS 房间命名用(两端各自算 room)
  loadContacts();
  // 显示 WS 信令连接状态(便于诊断手机 WS 是否连上 VPS)
  setTimeout(()=>{
    const st=document.getElementById('status');
    if(st && typeof wsReady!=='undefined') st.textContent=(s.output.active_user||'')+(wsReady?' · 信令✓':' · 信令✗(检查网络)');
  },2500);
}
async function doSetup(){await api('/dm/api/setup',{method:'POST',body:'{}'});refresh();}
async function loadContacts(){
  // 来电模态优先级高于轮询刷新:响铃期间跳过联系人重渲染,避免任何重绘干扰来电 UI
  if(document.querySelector('.incomingCallModal'))return;
  const c=await api('/dm/api/contacts');
  const cl=document.getElementById('clist');cl.innerHTML='';
  if(!c.ok||!c.output.length){cl.innerHTML='<div style="padding:14px;color:var(--dim);font-size:13px">暂无联系人<br>点 + 邀请 开始</div>';return;}
  c.output.forEach(ct=>{
    const d=document.createElement('div');d.className='citem'+(cur&&cur.contact_id===ct.contact_id?' active':'');
    d.innerHTML='<div class="nm">'+esc(ct.display_name||('联系人'+ct.contact_id))+'</div><div class="pv">'+esc(ct.preview||'')+'</div>';
    d.onclick=()=>openChat(ct);cl.appendChild(d);
  });
}
// 已渲染消息的索引:itemId -> DOM 节点。reconcile 只增不删,防 5s 轮询闪烁。
const _rendered = new Map();
let _optimisticSeq = 0;

function _msgNode(m){
  if(m.kind==='manifest'){
    const d=document.createElement('div');d.className='m manifest';
    d.textContent='[签名清单] '+m.text.slice(0,80)+'…';return d;
  }
  const d=document.createElement('div');
  d.className='m '+(m.dir==='me'?'me':'them');
  d.textContent=m.text;return d;
}

async function openChat(ct, isRefresh){
  // 响铃期间不轮询重渲染聊天区(用户手动点击仍允许:此时无来电模态或用户已明确要切换)
  if(isRefresh && document.querySelector('.incomingCallModal'))return;
  cur=ct;loadContacts();
  document.getElementById('in').disabled=false;
  let r;
  try{ r=await api('/dm/api/history?contact='+encodeURIComponent(ct.contact_id)); }
  catch(e){ if(!isRefresh){const box=document.getElementById('msgs');box.innerHTML='<div class="sys">加载失败:'+esc(e)+'</div>';} return; }
  if(!r.ok){
    if(!isRefresh){const box=document.getElementById('msgs');box.innerHTML='<div class="sys">'+esc(r.error||'加载失败')+'</div>';}
    return;
  }
  const box=document.getElementById('msgs');
  const nearBottom = (box.scrollHeight - box.scrollTop - box.clientHeight) < 120;
  // 首次打开:清空重建;之后 reconcile(只追加新消息,不重排不闪烁)
  if(!isRefresh){ box.innerHTML=''; _rendered.clear(); }
  (r.output.messages||[]).forEach(m=>{
    // 1) 服务端去重:同一 itemId 不重复渲染
    if(m.id!=null && _rendered.has('srv_'+m.id)) return;
    // 2) 乐观气泡 reconcile:同方向+全文本匹配的乐观气泡,原地转正(不新增不跳侧)
    const optKey = 'txt_'+m.dir+'_'+m.text;
    const existing = _rendered.get(optKey);
    if(existing){
      // 已渲染过同文本气泡(乐观插入或上次),标记为已确认并跳过
      if(m.id!=null){ _rendered.set('srv_'+m.id, existing); }
      return;
    }
    const node=_msgNode(m);
    node.dataset.key = m.id!=null ? ('srv_'+m.id) : optKey;
    box.appendChild(node);
    if(m.id!=null) _rendered.set('srv_'+m.id, node);
    _rendered.set(optKey, node);
  });
  // 待下载文件
  (r.output.incoming_files||[]).forEach(f=>{
    const fk='file_'+f.file_id;
    if(_rendered.has(fk))return;
    const card=fileCard(ct,f);card.dataset.key=fk;box.appendChild(card);_rendered.set(fk,card);
  });
  if(nearBottom||!isRefresh) box.scrollTop=box.scrollHeight;
}
function fileCard(ct,f){
  const d=document.createElement('div');d.className='fmsg';
  d.innerHTML='<div class="fname">📎 '+esc(f.file_name||'文件')+'</div>';
  const vb=document.createElement('span');vb.className='vbadge pending';vb.textContent='…';d.appendChild(vb);
  const btn=document.createElement('button');btn.textContent='下载';d.appendChild(btn);
  btn.onclick=async()=>{
    btn.disabled=true;btn.textContent='下载中…';
    const rr=await api('/dm/api/receive_file',{method:'POST',body:JSON.stringify({file_id:f.file_id})});
    btn.textContent=rr.ok?'已下载':'失败';
    verifyBadge(ct,f.file_name,vb);
  };
  verifyBadge(ct,f.file_name,vb);
  return d;
}
async function verifyBadge(ct,fname,el){
  el.className='vbadge pending';el.textContent='校验中…';
  const v=await api('/dm/api/verify_file?contact='+encodeURIComponent(ct.contact_id)+'&file_name='+encodeURIComponent(fname));
  if(v.ok&&v.output&&v.output.verified){el.className='vbadge ok';el.textContent='✓ 已验证来自 '+(v.output.sender||'对方');}
  else if(v.ok&&v.output){el.className='vbadge bad';el.textContent='✗ 校验失败';el.title=v.diagnosable||'';}
  else{el.className='vbadge pending';el.textContent='无签名';el.title=(v.diagnosable||v.error||'');}
}
async function sendMsg(){
  const inp=document.getElementById('in');const t=inp.value.trim();if(!t||!cur)return;
  inp.value='';
  const box=document.getElementById('msgs');
  // 乐观插入:最终位置/样式,用全文本 key 登记;服务端同文本到达时原地转正(不重复不跳侧)
  const d=document.createElement('div');d.className='m me';d.textContent=t;
  _rendered.set('txt_me_'+t, d);
  box.appendChild(d);box.scrollTop=box.scrollHeight;
  const r=await api('/dm/api/send',{method:'POST',body:JSON.stringify({contact:String(cur.contact_id),text:t})});
  if(!r.ok){
    _rendered.delete('txt_me_'+t);
    d.remove();
    const e=document.createElement('div');e.className='sys';e.textContent='发送失败:'+(r.error||'');box.appendChild(e);
  }
}
function toggleAttach(){const a=document.getElementById('attach');a.style.display=a.style.display==='none'?'flex':'none';}
async function sendFile(){
  if(!cur)return;
  const path=document.getElementById('fpath').value.trim();if(!path)return;
  const signed=document.getElementById('fsigned').checked;
  const box=document.getElementById('msgs');
  const note=document.createElement('div');note.className='sys';note.textContent='发送文件…';box.appendChild(note);box.scrollTop=box.scrollHeight;
  const r=await api('/dm/api/send_file',{method:'POST',body:JSON.stringify({contact:String(cur.contact_id),path,signed})});
  note.remove();
  const d=document.createElement('div');d.className='fmsg';
  if(r.ok){d.innerHTML='<div class="fname">📎 已发送 '+(r.output.file||path.split(/[\\/]/).pop())+(signed?' <span class="vbadge ok">带签名</span>':'')+'</div>';}
  else{d.innerHTML='<div class="fname" style="color:var(--bad)">发送失败</div><div style="font-size:12px;color:var(--dim)">'+esc(r.error||'')+'</div>';}
  box.appendChild(d);box.scrollTop=box.scrollHeight;
  document.getElementById('fpath').value='';
}
async function newInvite(){
  const r=await api('/dm/api/create_invite',{method:'POST',body:'{}'});
  if(r.ok&&r.output){document.getElementById('inviteLink').value=(r.output.link||r.output);document.getElementById('inviteModal').style.display='flex';}
}
// A2H 审批卡:轮询待裁决请求,渲染确认/取消按钮(L4 唯一交互=确认/追问,架构 §7.4)
async function pollA2H(){
  try{
    const r=await api('/dm/api/a2h_status');
    if(!r.ok)return;
    renderA2H(r.output.pending||[]);
  }catch(e){}
}
function renderA2H(pending){
  // 移除旧审批卡
  document.querySelectorAll('.a2hcard').forEach(e=>e.remove());
  const box=document.getElementById('msgs');
  pending.forEach(p=>{
    const d=document.createElement('div');d.className='fmsg a2hcard';d.style.borderColor='var(--warn)';
    d.innerHTML='<div class="fname" style="color:var(--warn)">⚠ agent 请求审批</div><div style="font-size:13px;margin:5px 0">'+esc(p.action||'')+'</div><div style="font-size:12px;color:var(--dim)">'+esc(p.reason||'')+'</div>';
    const row=document.createElement('div');row.style.marginTop='8px';
    const yes=document.createElement('button');yes.textContent='批准';yes.style.cssText='background:var(--ok);border:none;color:#fff;padding:6px 14px;border-radius:7px;cursor:pointer;margin-right:8px';
    const no=document.createElement('button');no.textContent='拒绝';no.style.cssText='background:var(--bad);border:none;color:#fff;padding:6px 14px;border-radius:7px;cursor:pointer';
    yes.onclick=()=>{sendText('yes '+p.request_id);d.remove();};
    no.onclick=()=>{sendText('no '+p.request_id);d.remove();};
    row.appendChild(yes);row.appendChild(no);d.appendChild(row);
    box.appendChild(d);
  });
  box.scrollTop=box.scrollHeight;
}
async function sendText(t){
  if(!cur)return;
  await api('/dm/api/send',{method:'POST',body:JSON.stringify({contact:String(cur.contact_id),text:t})});
}

// ═══════════════ 2 人 E2E 通话(P2P WebRTC,信令经 E2E 加密通道)═══════════════
let pc=null, localStream=null, callState='idle', callTimer=null, screenTrack=null, isCaller=false, callStartTs=0;
const ICE_SERVERS=[{urls:'stun:stun.l.google.com:19302'}];  // P2P STUN;内网直连为主,无需 TURN
const PAGE_LOAD_TS = new Date().toISOString();  // 只响应页面加载后的信令,忽略历史遗留
const STALE_CALL_MS = 45000;  // 通话握手 45s 未完成自动复位 idle(防卡在 answering 收 busy 死循环)

function setCallStatus(t){const el=document.getElementById('callStatus');if(el)el.textContent=t;}
function showCallPanel(show){document.getElementById('callPanel').style.display=show?'block':'none';}

// 卡死看门狗:非 idle 但长时间未 connected,强制复位
setInterval(()=>{
  if(callState!=='idle' && callState!=='connected' && (Date.now()-callStartTs)>STALE_CALL_MS){
    endCall(true);  // 静默复位
  }
},5000);

// ═══ WebSocket 信令(方案 A):实时推送,不轮询聊天记录 ═══
// 两端浏览器各连 VPS 上的 WS 信令服务器(wss://signal.dreamproject.qzz.io/ws,LE 证书),
// SDP/ICE 经服务器**实时转发**给对端。房间 = 双方身份排序拼接(两端算出同一 room)。
let wsSig=null, wsReady=false, myPeerId=null, currentRoom=null, _wsManualClose=false;

// WS 信令服务器(VPS,Let's Encrypt 证书,公网可达,手机/电脑直连,不依赖 adb/局域网)
const WS_SIGNAL_URL = 'wss://signal.dreamproject.qzz.io/ws';
function _wsUrl(){ return WS_SIGNAL_URL; }
// 房间模型:每人一个"个人收件房间"(以**对方**视角命名)。主叫向对方的收件房间发 offer。
// 计算规则:room = 'inbox-' + 目标身份的**服务端 userId 等价名**。两端都用 display_name 小写,保证一致。
function _inboxRoom(identity){ return 'inbox-'+(identity||'me').toLowerCase(); }
function _callRoom(a, b){ return 'call-'+[a.toLowerCase(), b.toLowerCase()].sort().join('__'); }

function ensureWs(onready){
  if(wsSig && wsReady){ if(onready)onready(); return; }
  if(wsSig && wsSig.readyState===WebSocket.CONNECTING){ if(onready)wsSig.addEventListener('open',()=>onready(),{once:true}); return; }
  wsSig = new WebSocket(_wsUrl());
  wsSig.onopen = ()=>{
    wsReady=true;
    myPeerId = (window._activeUser||('peer-'+Math.random().toString(36).slice(2,8)));
    // 心跳保活(防 Chrome 空闲/网络设备掐断空闲 WS)
    if(window._wsPing)clearInterval(window._wsPing);
    window._wsPing=setInterval(()=>{ if(wsSig&&wsReady)wsSig.send(JSON.stringify({type:'ping'})); },25000);
    if(onready)onready();
  };
  wsSig.onmessage = (ev)=>{
    let msg; try{ msg=JSON.parse(ev.data); }catch(e){ return; }
    if(msg.type==='signal' && msg.data){ handleSignal(msg.data); }
    else if(msg.type==='peer-joined'){ setCallStatus('对方已上线'); }
    else if(msg.type==='peer-left'){ if(callState==='connected')endCall(true); }
  };
  wsSig.onclose = ()=>{
    wsReady=false; wsSig=null;
    // 自动重连(手机 Chrome 后台/网络抖动会断 WS,不重连就收不到来电)
    if(!_wsManualClose){ setTimeout(()=>{ ensureWs(()=>{ wsJoin(_inboxRoom(window._activeUser||'me'), myPeerId, false); if(currentRoom)wsJoin(currentRoom,myPeerId, true); }); }, 2000); }
  };
  wsSig.onerror = ()=>{ wsReady=false; };
}

// isCallRoom:仅当加入的是"通话房间"时才更新 currentRoom。
// "个人收件房间"(_inboxRoom)只是收 offer 的邮箱,不是通话房间,join 它绝不能污染 currentRoom
// (否则断线重连后 sendSig 的 answer/ice/end 会默认发到收件房间,对方收不到,通话静默坏死)。
function wsJoin(room, peer, isCallRoom){
  if(wsSig && wsReady){
    if(isCallRoom===true) currentRoom = room;
    wsSig.send(JSON.stringify({type:'join', room, peer, token: TOKEN}));
  }
}

function sendSig(sig, room){
  // 经 WS 实时发送(替代旧的 simplex_send_message 轮询通道)。
  // 默认发当前通话房间;offer 由主叫发到对方的收件房间。
  const r = room || currentRoom;
  if(wsSig && wsReady && r){
    wsSig.send(JSON.stringify({type:'signal', room: r, data: sig}));
  }
}

async function getMedia(video){
  // 显式开 AEC/降噪/AGC(复用 _audioConstraints):手机外放场景的声学回声自激,
  // 裸 audio:true 在安卓 Chrome 常不打 AEC,导致环路增益>1 啸叫。
  const audioC = _audioConstraints();
  const constraints = video
    ? {audio: audioC, video:{width:{ideal:640},frameRate:{ideal:24},facingMode:{ideal:_facingMode}}}
    : {audio: audioC};
  return await navigator.mediaDevices.getUserMedia(constraints);
}

// 当前摄像头朝向:environment=后置(默认,性能更好),user=前置。手机端切换摄像头用。
let _facingMode = 'environment';

// 通话中无缝切换前后摄像头:重新采集对应朝向的视频轨,用 replaceTrack 替换
// (不重建 PeerConnection,对端无感知、不断连),并更新本地预览。
async function switchCamera(){
  if(!localStream || !localStream.getVideoTracks().length){ setCallStatus('当前无视频轨可切换'); return; }
  _facingMode = (_facingMode==='environment') ? 'user' : 'environment';
  // 手机摄像头是独占资源:必须先停掉旧轨释放摄像头,再采新轨,否则新采集 NotReadableError。
  const old = localStream.getVideoTracks()[0];
  try{
    if(old){ old.stop(); localStream.removeTrack(old); }   // 先释放旧摄像头
    const ns = await navigator.mediaDevices.getUserMedia({
      video:{width:{ideal:640},frameRate:{ideal:24},facingMode:{ideal:_facingMode}},
      audio:false,
    });
    const newTrack = ns.getVideoTracks()[0];
    if(!newTrack){ setCallStatus('未拿到新摄像头轨'); return; }
    // 替换发送给对端的轨(所有 video sender)
    if(pc){
      const senders = pc.getSenders().filter(s=>s.track && s.track.kind==='video');
      for(const s of senders){ try{ await s.replaceTrack(newTrack); }catch(e){} }
    }
    // 挂新轨并更新预览
    localStream.addTrack(newTrack);
    document.getElementById('localVideo').srcObject = localStream;
    setCallStatus('已切换到'+(_facingMode==='environment'?'后置':'前置')+'摄像头');
  }catch(e){
    // 失败时尝试恢复原朝向,避免视频轨彻底丢失
    setCallStatus('切换摄像头失败:'+e);
    try{
      _facingMode = (_facingMode==='environment') ? 'user' : 'environment';  // 回退朝向
      const rs = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:_facingMode}},audio:false});
      const rt = rs.getVideoTracks()[0];
      if(rt && pc){ for(const s of pc.getSenders().filter(s=>s.track&&s.track.kind==='video')){ try{ await s.replaceTrack(rt); }catch(e){} } localStream.addTrack(rt); document.getElementById('localVideo').srcObject=localStream; }
    }catch(e2){}
  }
}

async function startCall(video){
  if(!cur){alert('先选中联系人');return;}
  if(callState!=='idle'){alert('已在通话中');return;}
  isCaller=true;callState='calling';callStartTs=Date.now();
  showCallPanel(true);setCallStatus(video?'视频呼叫中…':'语音呼叫中…');
  try{
    localStream=await getMediaSafe(video);
    document.getElementById('localVideo').srcObject=localStream;
    setupPeer();
    applyLocalTracks();   // 挂本地轨 + 显式 sendrecv(替代旧 addTrack forEach)
    // 加入 WS:先 join 我的收件房间(听来电),再在通话房间发 offer
    const me = window._activeUser || 'me';
    // 信令房间身份源 = 对方跨端稳定的 profile 显示名(被叫监听房间 = 它自己的 profile 名,
    // 两端自动同源)。本地备注名(display_name)仅用于 UI 显示,不作房间路由。
    const peerName = cur.peer_profile_name || cur.display_name;
    await new Promise(res=>ensureWs(res));
    currentRoom = _callRoom(me, peerName);      // 通话房间(两端同名)
    wsJoin(currentRoom, myPeerId, true);       // 加入通话房间(收发 answer/ice/end)
    const offer=await pc.createOffer();
    await pc.setLocalDescription(offer);
    // 等 ICE gathering 基本完成后一次性发 offer(含 candidates)
    await waitIce();
    // offer 发到**对方的收件房间**(对方在那里监听来电)
    sendSig({type:'offer', sdp:pc.localDescription.sdp, video, from: me}, _inboxRoom(peerName));
    setCallStatus('等待对方接听…');
  }catch(e){setCallStatus('呼叫失败:'+e);callState='idle';}
}

function setupPeer(){
  pc=new RTCPeerConnection({iceServers:ICE_SERVERS});
  // 不预建 transceiver 再用 addTrack 填(易错:addTrack 复用 recvonly transceiver 不会提升方向,
  // 导致应答方 answer 被压成 recvonly 只收不发——probe 实测 audio=recvonly 证实)。
  // 改为:addTrack 让浏览器自然建 sendrecv transceiver;仅当本地确实缺某媒体时,才补一个 recvonly
  // 占位,保证仍能收到对方该媒体。方向提升统一在 applyLocalTracks 里做。
  pc.ontrack=(ev)=>{
    // 远端媒体:音频轨进专用 audio 元素;视频轨区分"摄像头"与"屏幕共享"(第二条视频轨)。
    let ra=document.getElementById('remoteAudio');
    if(!ra){ ra=document.createElement('audio');ra.id='remoteAudio';ra.autoplay=true;document.body.appendChild(ra); }
    if(ev.track.kind==='audio'){
      let s = ra.srcObject instanceof MediaStream ? ra.srcObject : new MediaStream();
      if(!s.getAudioTracks().includes(ev.track))s.addTrack(ev.track);
      ra.srcObject=s;ra.muted=false;ra.play().catch(()=>{});
      return;
    }
    // 视频轨:判断是摄像头还是屏幕共享。
    // 屏幕共享 = 通话中**新增的第二条**视频轨(对方 shareScreen addTrack 产生)。
    // 判定:contentHint=detail/text,或 remoteVideo 已有视频轨后又来一条视频轨。
    const rv=document.getElementById('remoteVideo');
    const alreadyHasCam = rv.srcObject && rv.srcObject.getVideoTracks().length>0;
    const isScreen = ev.track.contentHint==='detail' || ev.track.contentHint==='text' || alreadyHasCam;
    if(isScreen){
      // 屏幕共享:挂到专用大屏元素(不覆盖摄像头小窗)
      let sv=document.getElementById('screenVideo');
      if(!sv){
        sv=document.createElement('video');sv.id='screenVideo';sv.autoplay=true;sv.playsInline=true;sv.muted=true;
        sv.style.cssText='position:fixed;top:5%;left:50%;transform:translateX(-50%);width:70%;max-height:80%;background:#000;border:2px solid var(--acc);border-radius:10px;z-index:1000';
        const closeB=document.createElement('button');closeB.textContent='✕ 关闭共享视图';
        closeB.style.cssText='position:fixed;top:calc(5% + 8px);right:12%;z-index:1001;background:var(--bad);border:none;color:#fff;padding:5px 12px;border-radius:7px;cursor:pointer';
        closeB.onclick=()=>{sv.remove();closeB.remove();};
        document.body.appendChild(sv);document.body.appendChild(closeB);
      }
      if(ev.streams&&ev.streams[0])sv.srcObject=ev.streams[0];
      else{let s=new MediaStream();s.addTrack(ev.track);sv.srcObject=s;}
      sv.play().catch(()=>{});
    }else{
      // 摄像头:挂到 remoteVideo(静音防回声,声音走 remoteAudio)
      if(ev.streams&&ev.streams[0])rv.srcObject=ev.streams[0];
      else{let s=new MediaStream();s.addTrack(ev.track);rv.srcObject=s;}
      rv.muted=true;
      rv.play().catch(()=>{});
    }
  };
  pc.onicecandidate=(ev)=>{ if(ev.candidate) sendSig({type:'ice', candidate:ev.candidate.toJSON()}); };
  pc.onconnectionstatechange=()=>{
    setCallStatus('连接状态:'+pc.connectionState);
    if(pc.connectionState==='connected'){callState='connected';setCallStatus('已接通(E2E 加密通话)');}
    if(['disconnected','failed','closed'].includes(pc.connectionState))endCall(true);
  };
}

// 挂本地轨并把方向显式提升为 sendrecv,本地缺的媒体补 recvonly。
// 关键:addTrack 后必须把该 transceiver.direction 设为 sendrecv——
// 否则若 transceiver 已是 recvonly(如先 setRemoteDescription 建好的),addTrack 不会自动提升,
// 应答方 answer 会被压成 recvonly 只收不发(probe 实测证实)。双向都要调用本函数。
function applyLocalTracks(){
  if(!pc||!localStream)return;
  // 跳过借用的语音触发轨(_voiceTrigger):它只为强制通信音频模式而采集,不发送给对方。
  const sendTracks = localStream.getTracks().filter(t=>!t._voiceTrigger);
  sendTracks.forEach(t=>{
    pc.addTrack(t, localStream);
  });
  const hasAudio = sendTracks.some(t=>t.kind==='audio');
  const hasVideo = sendTracks.some(t=>t.kind==='video');
  // 显式提升已挂轨的 transceiver 方向为 sendrecv
  pc.getTransceivers().forEach(tc=>{
    if(tc.sender.track){  // 有本地轨 → 要能发也要能收
      tc.direction='sendrecv';
    }
  });
  // 本地缺的媒体补 recvonly(只收对方,不发),保证仍能收到对方该媒体
  const kinds = pc.getTransceivers().map(tc=>tc.receiver.track?tc.receiver.track.kind:(tc.sender.track?tc.sender.track.kind:null));
  try{
    if(!hasAudio && !kinds.includes('audio')) pc.addTransceiver('audio',{direction:'recvonly'});
    if(!hasVideo && !kinds.includes('video')) pc.addTransceiver('video',{direction:'recvonly'});
  }catch(e){}
}



function waitIce(){
  return new Promise(res=>{
    if(pc.iceGatheringState==='complete')return res();
    let n=0;
    const iv=setInterval(()=>{
      n++;
      if(pc.iceGatheringState==='complete'||n>20){clearInterval(iv);res();}
    },150);
  });
}

// 信令经 WS 实时推送(ws.onmessage → handleSignal),无需轮询 loop。
async function handleSignal(sig){
  if(!sig||!sig.type)return;
  try{
    if(sig.type==='offer'){
      // 防御:SDP 必须以 v= 开头(过滤历史里的测试/伪造 offer)
      if(!sig.sdp||!String(sig.sdp).startsWith('v='))return;
      // 被叫:只有"真的在通话中"(pc 存在且未关闭/未 failed)才回 busy。
      const inRealCall = pc && !['closed','failed','disconnected'].includes(pc.connectionState) && pc.connectionState!=='new';
      if(callState!=='idle' && inRealCall){await sendSig({type:'busy'});return;}
      if(callState!=='idle'){endCall(true);}
      // 不自动接听 —— 弹出来电提示,由用户选择"接听/拒绝"(对齐主流 IM 习惯)
      showIncomingCall(sig);
      return;
    }else if(sig.type==='answer'){
      // 主叫收 answer:必须有活跃 pc + 合法 SDP 才处理
      if(pc&&callState==='calling'&&sig.sdp&&String(sig.sdp).startsWith('v=')){await pc.setRemoteDescription({type:'answer',sdp:sig.sdp});}
    }else if(sig.type==='ice'){
      if(pc&&sig.candidate){try{await pc.addIceCandidate(sig.candidate);}catch(e){}}
    }else if(sig.type==='end'){
      setCallStatus('对方已挂断');endCall(true);
    }else if(sig.type==='busy'){
      setCallStatus('对方忙线中');endCall(true);
    }else if(sig.type==='reject'){
      setCallStatus('对方拒绝了来电');endCall(true);
    }else if(sig.type==='noanswer'){
      setCallStatus('对方未接听');endCall(true);
    }
  }catch(e){setCallStatus('信令处理异常:'+e);}
}

// ─── 来电提示(接听/拒绝)— 显式模态,不被联系人列表/空会话遮挡 ───────────
let _pendingOffer=null, _ringTimer=null;
function showIncomingCall(sig){
  _pendingOffer=sig;
  // 移除旧的来电模态
  document.querySelectorAll('.incomingCallModal').forEach(e=>e.remove());
  const callerName = sig.from || (cur?cur.display_name:'对方');
  const modal=document.createElement('div');modal.className='incomingCallModal';
  modal.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.75);display:flex;align-items:center;justify-content:center;z-index:2000';
  modal.innerHTML=`<div style="background:var(--panel);padding:28px 32px;border-radius:16px;text-align:center;max-width:340px;border:2px solid var(--ok)">
    <div style="font-size:40px;margin-bottom:8px">📞</div>
    <div style="font-size:18px;font-weight:600;margin-bottom:4px">${esc(callerName)}</div>
    <div style="font-size:14px;color:var(--dim);margin-bottom:18px">${sig.video?'视频通话':'语音通话'} · 等待你接听</div>
    <div style="display:flex;gap:14px;justify-content:center">
      <button id="incAcc" style="background:var(--ok);border:none;color:#fff;padding:11px 26px;border-radius:10px;cursor:pointer;font-size:16px;font-weight:600">接听</button>
      <button id="incRej" style="background:var(--bad);border:none;color:#fff;padding:11px 26px;border-radius:10px;cursor:pointer;font-size:16px;font-weight:600">拒绝</button>
    </div></div>`;
  document.body.appendChild(modal);
  modal.querySelector('#incAcc').onclick=()=>{modal.remove();acceptCall(sig);};
  modal.querySelector('#incRej').onclick=()=>{modal.remove();rejectCall();};
  // 30s 未接自动按"未接"处理(通知主叫)
  _ringTimer=setTimeout(()=>{modal.remove();rejectCall(true);},30000);
  setCallStatus('来电:'+callerName+'('+(sig.video?'视频':'语音')+'),等待你接听');
}

async function acceptCall(sig){
  if(_ringTimer){clearTimeout(_ringTimer);_ringTimer=null;}
  _pendingOffer=null;
  isCaller=false;callState='answering';callStartTs=Date.now();
  showCallPanel(true);setCallStatus('接听中…');
  try{
    const me = window._activeUser||'me';
    const callerName = sig.from || (cur?cur.display_name:'');
    currentRoom = _callRoom(me, callerName);
    wsJoin(currentRoom, myPeerId, true);
    if(!cur && callerName){ cur = {contact_id: 0, display_name: callerName}; }
    // 关键修复 NotReadableError:先尝试 getUserMedia;若视频源不可用,降级为仅音频,不让整个接听失败
    localStream=await getMediaSafe(sig.video);
    document.getElementById('localVideo').srcObject=localStream;
    setupPeer();
    await pc.setRemoteDescription({type:'offer',sdp:sig.sdp});
    applyLocalTracks();   // setRemoteDescription 之后挂本地轨并把方向提升为 sendrecv
    const ans=await pc.createAnswer();
    await pc.setLocalDescription(ans);
    await waitIce();
    sendSig({type:'answer', sdp:pc.localDescription.sdp});
    setCallStatus('已接听,建立连接中…');
  }catch(e){setCallStatus('接听失败:'+e);callState='idle';}
}

function rejectCall(timeout){
  if(_ringTimer){clearTimeout(_ringTimer);_ringTimer=null;}
  _pendingOffer=null;
  sendSig({type: timeout?'noanswer':'reject'});
  setCallStatus(timeout?'未接听':'已拒绝来电');
}

// getUserMedia 安全降级:视频源不可用时退化为仅音频,而不是整个失败(NotReadableError)
async function getMediaSafe(video){
  if(video){
    try{ return await getMedia(true); }
    catch(e){
      setCallStatus('视频源不可用,已切换为语音通话');
      try{ return await _getVoiceStream(); }catch(e2){ throw e2; }
    }
  }
  return await _getVoiceStream();
}

// 纯语音通话:采集 {audio, video} 但把视频轨 disable 且不发送。
// 为什么带视频轨:只请求 audio 时,安卓 Chrome 走普通媒体录音路径,系统级 AEC 不生效
// (这就是"视频通话防啸好、纯语音啸叫严重"的根因)。带一条视频轨会强制浏览器进入
// 通信音频模式(AUDIO_MODE_IN_COMMUNICATION),系统 AEC/降噪才启用。视频轨仅借用采集,
// 不 addTrack 发给对方(见 applyLocalTracks 的 voiceOnly 分支)。
async function _getVoiceStream(){
  try{
    const s = await navigator.mediaDevices.getUserMedia({
      audio: _audioConstraints(),
      video: {width:{ideal:160},frameRate:{ideal:5},facingMode:{ideal:'user'}},  // 极小分辨率,只借通信模式
    });
    // 视频轨标记为"借用的语音触发轨",不发送、不预览
    s.getVideoTracks().forEach(t=>{ t.enabled=false; t._voiceTrigger=true; });
    return s;
  }catch(e){
    // 某些设备拿不到视频(无摄像头),退回纯音频(AEC 弱但能用)
    return await navigator.mediaDevices.getUserMedia({audio: _audioConstraints()});
  }
}

// 抽出音频约束,供 getMedia / _getVoiceStream 复用
function _audioConstraints(){
  return {
    echoCancellation: {ideal: true},
    echoCancellationType: {ideal: 'system'},
    noiseSuppression: {ideal: true},
    autoGainControl: {ideal: true},
    channelCount: {ideal: 1},
    googEchoCancellation: {ideal: true},
    googAutoGainControl: {ideal: true},
    googNoiseSuppression: {ideal: true},
    googHighpassFilter: {ideal: true},
    googTypingNoiseDetection: {ideal: true},
  };
}

function toggleMic(){
  if(!localStream)return;
  const t=localStream.getAudioTracks()[0];if(!t)return;
  t.enabled=!t.enabled;
  document.getElementById('micBtn').textContent=t.enabled?'🎤 静音':'🎤 已静音';
}
function toggleCam(){
  if(!localStream)return;
  const t=localStream.getVideoTracks()[0];if(!t)return;
  t.enabled=!t.enabled;
  document.getElementById('camBtn').textContent=t.enabled?'📷 关视频':'📷 已关';
}
async function shareScreen(){
  if(!pc){alert('先建立通话');return;}
  try{
    if(screenTrack){ // 停止共享
      const sender=pc.getSenders().find(s=>s.track===screenTrack);
      if(sender)pc.removeTrack(sender);
      screenTrack.stop();screenTrack=null;
      document.getElementById('screenBtn').textContent='🖥️ 共享屏幕';
      return;
    }
    // getDisplayMedia 在某些环境(老浏览器/非安全上下文/部分安卓 WebView)不可用 —— 明确提示而不是报 TypeError
    if(!navigator.mediaDevices || typeof navigator.mediaDevices.getDisplayMedia!=='function'){
      setCallStatus('此浏览器不支持屏幕共享(需较新 Chrome + 安全上下文)');
      return;
    }
    const disp=await navigator.mediaDevices.getDisplayMedia({video:{frameRate:{ideal:10},displaySurface:'monitor'},audio:false});
    screenTrack=disp.getVideoTracks()[0];
    screenTrack.contentHint='detail';
    pc.addTrack(screenTrack,disp);
    screenTrack.onended=()=>{document.getElementById('screenBtn').textContent='🖥️ 共享屏幕';screenTrack=null;};
    document.getElementById('screenBtn').textContent='🖥️ 停止共享';
  }catch(e){setCallStatus('屏幕共享失败:'+e);}
}

async function endCall(remote){
  if(!remote)sendSig({type:'end'});
  if(screenTrack){screenTrack.stop();screenTrack=null;}
  if(pc){try{pc.close();}catch(e){}pc=null;}
  if(localStream){localStream.getTracks().forEach(t=>t.stop());localStream=null;}
  document.getElementById('localVideo').srcObject=null;
  document.getElementById('remoteVideo').srcObject=null;
  callState='idle';
  setTimeout(()=>showCallPanel(false),1500);
}

function copyInvite(){const t=document.getElementById('inviteLink');t.select();document.execCommand('copy');}
async function trustFlow(){
  if(!cur){alert('先选中一个联系人');return;}
  const r=await api('/dm/api/trust_establish',{method:'POST',body:JSON.stringify({contact:String(cur.contact_id)})});
  alert(r.ok?('已与 '+cur.display_name+' 建立文件签名信任根,可互发签名文件。'):('失败:'+(r.error||r.diagnosable||'')));
}
function closeInvite(){document.getElementById('inviteModal').style.display='none';}
// 来电监听经 WS(方案 A):页面加载即连 WS 并加入"自己身份的等待房间",
// 来电 offer 经服务器实时推送触发 handleSignal,不轮询、无残留状态。
// 注意:startSignal 已在 ws.onmessage 里统一调 handleSignal,这里只需确保 WS 常连 + 加入等待房间。
function startWsListener(){
  ensureWs(()=>{
    // 加入"我的收件房间"(以我的身份命名),主叫方向这里发 offer。收件房间不是通话房间,不写 currentRoom。
    wsJoin(_inboxRoom(window._activeUser||'me'), myPeerId, false);
  });
}
document.getElementById('in').addEventListener('keydown',e=>{if(e.key==='Enter')sendMsg();});
refresh();setInterval(()=>{if(cur)openChat(cur,true);else loadContacts();pollA2H();},5000);
startWsListener();

// ─── 媒体权限预热(Bug 2):把"系统授权弹窗"从"接听瞬间"提前到"来电之前" ───
// 浏览器策略:getUserMedia 必须由用户手势触发,页面加载即调会被静默拒绝并可能污染权限状态。
// 因此挂一个 once 的 pointerdown/keydown:首次交互后静默采一次流,只为了让系统记住授权,
// 立即 release 所有轨,不占用摄像头/麦克风。拒绝则静默放弃,不影响后续 acceptCall 正常再申请。
// 复用 getMediaSafe(false)(内部 _getVoiceStream 会尽量连视频权限一起申请,覆盖语音/视频两种来电)。
(function(){
  let _preheated=false;
  async function _preheatMedia(){
    if(_preheated)return;_preheated=true;
    try{
      const s=await getMediaSafe(false);
      if(s)s.getTracks().forEach(t=>t.stop());
    }catch(e){/* 用户拒绝/无设备:静默,接听时再正常申请 */}
  }
  const once={once:true};
  window.addEventListener('pointerdown',_preheatMedia,once);
  window.addEventListener('keydown',_preheatMedia,once);
})();
</script>
</body></html>
"""


# ────────────────────────────────────────────────────────────────────── #
# HTTP 处理
# ────────────────────────────────────────────────────────────────────── #

class DMHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _auth(self) -> bool:
        if not _ACCESS_TOKEN:
            return True
        if self.headers.get("X-L4-Token", "") == _ACCESS_TOKEN:
            return True
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (q.get("token") or [""])[0] == _ACCESS_TOKEN

    def _json(self, o, code=200):
        b = json.dumps(o, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _html(self, s):
        b = s.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path, q = u.path, urllib.parse.parse_qs(u.query)
        if path in ("/", "/dm"):
            if not self._auth():
                return self._json({"ok": False, "error": "unauthorized"}, 401)
            return self._html(_PAGE)
        if not self._auth():
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        if path == "/dm/api/status":
            return self._json(api_status())
        if path == "/dm/api/contacts":
            return self._json(api_contacts())
        if path == "/dm/api/history":
            return self._json(api_history((q.get("contact") or [""])[0], int((q.get("limit") or [60])[0])))
        if path == "/dm/api/verify_file":
            return self._json(api_verify_file((q.get("contact") or [""])[0], (q.get("file_name") or [""])[0]))
        if path == "/dm/api/a2h_status":
            return self._json(api_a2h_status())
        if path == "/dm/api/file_info":
            return self._json(api_file_info((q.get("path") or [""])[0]))
        if path == "/dm/api/call_poll":
            try:
                sid = int((q.get("since_id") or ["0"])[0])
            except ValueError:
                sid = 0
            return self._json(api_call_poll((q.get("contact") or [""])[0], sid))
        return self._json({"ok": False, "error": "unknown path"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if not self._auth():
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode()) if length else {}
        except Exception:  # noqa: BLE001
            return self._json({"ok": False, "error": "invalid json"}, 400)
        if path == "/dm/api/setup":
            # 缺省传 ""(不是 "oiagent"):让 api_setup 的 "display_name or DM_IDENTITY" 生效,
            # 否则硬编码缺省值永远覆盖 DM_IDENTITY,bob 实例被错建成 oiagent。
            return self._json(api_setup(req.get("display_name", "")))
        if path == "/dm/api/create_invite":
            return self._json(api_create_invite())
        if path == "/dm/api/accept_invite":
            return self._json(api_accept_invite(req.get("link", "")))
        if path == "/dm/api/delete_contact":
            return self._json(api_delete_contact(str(req.get("contact", ""))))
        if path == "/dm/api/send":
            return self._json(api_send(str(req.get("contact", "")), req.get("text", "")))
        if path == "/dm/api/receive_file":
            return self._json(api_receive_file(int(req.get("file_id", 0))))
        if path == "/dm/api/send_file_signed":
            return self._json(api_send_file_signed(str(req.get("contact", "")), req.get("path", "")))
        if path == "/dm/api/send_file":
            return self._json(api_send_file(str(req.get("contact", "")), req.get("path", ""), bool(req.get("signed", False))))
        if path == "/dm/api/file_info":
            return self._json(api_file_info(req.get("path", "")))
        if path == "/dm/api/a2h_status":
            return self._json(api_a2h_status())
        if path == "/dm/api/a2h_set_approver":
            return self._json(api_a2h_set_approver(str(req.get("contact", ""))))
        if path == "/dm/api/trust_establish":
            return self._json(api_trust_establish(str(req.get("contact", ""))))
        if path == "/dm/api/trust_import":
            return self._json(api_trust_import(str(req.get("contact", "")), req.get("key", "")))
        if path == "/dm/api/call_signal":
            return self._json(api_call_signal(str(req.get("contact", "")), req.get("signal", {})))
        return self._json({"ok": False, "error": "unknown path"}, 404)


def run(host: str = DM_HOST, port: int = DM_PORT) -> None:
    srv = ThreadingHTTPServer((host, port), DMHandler)
    if _ACCESS_TOKEN:
        print(f"SecureDM 就绪: http://{host}:{port}/?token={_ACCESS_TOKEN}")
    else:
        print(f"SecureDM 就绪: http://{host}:{port}  (token 禁用)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    # argv 优先于 env(Windows Start-Process 传 env 常失败,argv 100% 可靠):
    #   python securedm_web.py <port> [identity] [db_prefix]
    # 也支持 PORT env 变量(供 preview 工具 autoPort 用)
    _port_env = os.environ.get("PORT")
    p = int(sys.argv[1] if len(sys.argv) > 1 else (_port_env if _port_env else DM_PORT))
    if len(sys.argv) > 2 and sys.argv[2]:
        DM_IDENTITY = sys.argv[2]
    if len(sys.argv) > 3 and sys.argv[3]:
        DM_DB_PREFIX = sys.argv[3]
    run(port=p)
