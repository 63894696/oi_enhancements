// ======================================================================
// ============================================================================
// Prisir 语伴 web 客户端 — 完整自包含:
//   · WS /ws 主对话(用户文本/AI 流式/ASR partial+final+finished+fallback)
//   · /ws 旁路事件:knowledge_refs / idle_warning / idle_timeout / bye / sessions / err
//   · 设置面板(ASR 厂商 + 上下文注入 toggle + 派发 toggle)
//   · 历史侧栏(单条删除) / 续接卡片 / emoji 面板 / 派发 badge
//   · 启动/挂断按钮 + 计时器(仅会话期间累加)
//   · 音乐 UI 已与语伴完全解耦(2026-09-19 M3.29.8):
//     入口:PrisirAI Tauri 托盘菜单「启动音乐播放器」;
//     桌面歌词:Tauri 透明 BrowserWindow,跨应用置顶。
//     此页面不再显示任何音乐 UI。
// ============================================================================
(function () {
"use strict";

// ---- DOM ref ----------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const msgsEl = $("msgs");
const emptyEl = $("empty");
const inEl = $("in");
const sendBtn = $("btnSend");
const micBtn = $("btnMic");
const statusDot = $("statusDot");
const statusTxt = $("statusTxt");
const waveform = $("waveform");
const timerEl = $("timer");
const avatarEl = $("avatar");
const btnHistory = $("btnHistory");
const btnTts = $("btnTts");
const btnSettings = $("btnSettings");
const btnDispatch = $("btnDispatch");
const dispatchBadge = $("dispatchBadge");
const btnEmoji = $("btnEmoji");
const btnStart = $("btnStart");
const sessionsPanel = $("sessionsPanel");
const sessionsList = $("sessionsList");
const settingsPanel = $("settingsPanel");
const settingsClose = $("btnSettingsClose");
const continueCard = $("continueCard");
const emojiPanel = $("emojiPanel");
const emojiTabs = $("emojiTabs");
const emojiList = $("emojiList");
const m323Confirm = $("m323Confirm");
const m323ConfirmBody = $("m323ConfirmBody");
const m323ConfirmTitle = $("m323ConfirmTitle");
const m323ConfirmOk = $("m323ConfirmOk");
const m323ConfirmCancel = $("m323ConfirmCancel");
// P3j T16-C: 视频/YouTube 能力确认卡
const capConfirm = $("capConfirm");
const capConfirmTitle = $("capConfirmTitle");
const capConfirmCap = $("capConfirmCap");
const capConfirmRisk = $("capConfirmRisk");
const capConfirmBody = $("capConfirmBody");
const capConfirmArgs = $("capConfirmArgs");
const capConfirmOk = $("capConfirmOk");
const capConfirmCancel = $("capConfirmCancel");

// ---- 常量 -------------------------------------------------------------------
const EMOJI_GROUPS = [
    "😀😃😄😁😆😅🤣😂🙂🙃😉😊😇🥰😍🤩😘😗😚😙😋😛😜🤪😝🤑🤗🤭🤫🤔🤐🤨😐😑😶😏😒🙄😬🤥😌😔😪🤤😴😷🤒🤕🤢🤮🤧🥵🥶🥴😵🤯🤠🥳😎🤓🧐😕😟🙁☹️😮😯😲😳🥺😦😧😨😰😱😖😣😞😓😩😫🥱😤😡😠🤬😈👿💀💩🤡👹👺👻👽👾🤖😺😸😹😻😼😽🙀😿😾",
    "👋🤚🖐️✋🖖👌🤏✌️🤞🤟🤘🤙👈👉👆🖕👇☝️👍👎✊👊🤛🤜👏🙌👐🤲🤝🙏✍️💅🤳💪🦾🦿🦵🦶👂🦻👃🧠🦷🦴👀👁️👅👄💋🩸",
    "📦📁📂🗂️📅📆🗒️🗓️📇📈📉📊📋📌📍📎🖇️📏📐✏️🖊️🖌️🖍️📝📞📟📠📺📻🎙️🎚️🎛️🧭⏱️⏲️⏰🕰️⌚⌨️🖥️🖨️💻🖱️🖲️🕹️🗜️💽💾💿📀📷📸📹🎥📽️🎞️📞☎️",
    "❤🧡💛💚💙💜🖤🤍🤎💔❣️💕💞💓💗💖💘💝💟☮️✝️☪️🕉️☸️✡️🔯🕎☯️☦️🛐⛎♈♉♊♋♌♍♎♏♐♑♒♓🆔⚛️🉑☢️☣️📴📳🈶🈚🈸🈺🈷️✴️🆚💮🉑㊙️㊗️🈴🈵🈹🈲🅰️🅱️🆎🆑🅾️🆘❌⭕🛑⛔📛🚫💯💢♨️🚷🚯🚳🚱🔞📵🚭",
];

// ---- 状态 -------------------------------------------------------------------
const st = {
    sid: "",
    ws: null,
    wsUrl: (location.protocol === "https:" ? "wss:" : "ws:")
        + "//" + location.host + "/ws",
    wsRetry: 0,
    wsTimer: null,
    pendingCapConfirm: null,  // P3j T16-C: 待确认 capability
    // 会话生命周期:不启动则不计时;M3.29.8 用户反馈「点开就开始计费」误解
    running: false,
    sessionStart: 0,
    recording: false,
    asr: null,
    asrBuf: [],
    lastPartial: "",
    tts: localStorage.getItem("prisir.tts") !== "off",
    pendingConfirmResolve: null,
    aiTurnCount: 0,
    sessionsOpen: false,
    settingsOpen: false,
    emojiOpen: false,
    activeEmojiGroup: 0,
    mediaRecorder: null,
    audioCtx: null,
    workletNode: null,
};

// ---- Timer — 仅在 running=true 时累加 -----------------------------------------
function fmtTimerEl() {
    if (!timerEl) return;
    timerEl.textContent = "00:00";
}
let timerIv = null;
function ensureTimer() {
    if (timerIv) return;
    timerIv = setInterval(() => {
        if (!st.running) return;  // 未启动 → 不动 DOM
        const e = Math.floor((Date.now() - st.sessionStart) / 1000);
        const m = String(Math.floor(e / 60)).padStart(2, "0");
        const s = String(e % 60).padStart(2, "0");
        if (timerEl) timerEl.textContent = m + ":" + s;
    }, 1000);
}
ensureTimer();
fmtTimerEl();  // 初始显示 00:00

// ---- Utility ----------------------------------------------------------------
function escHtml(s) {
    return (s || "").replace(/[&<>"']/g, (c) => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
}
function setStatus(state, text) {
    statusDot.className = "status-dot";
    if (state === "thinking") statusDot.classList.add("thinking");
    else if (state === "speaking") statusDot.classList.add("speaking");
    else if (state === "off") statusDot.classList.add("disconnected");
    statusTxt.textContent = text;
}
function renderMsg(role, text, opts) {
    opts = opts || {};
    if (emptyEl && emptyEl.parentNode) emptyEl.parentNode.removeChild(emptyEl);
    const div = document.createElement("div");
    div.className = "msg " + (role === "user" ? "me" : "them");
    if (opts.partial) div.classList.add("partial");
    div.innerHTML = escHtml(text).replace(/\n/g, "<br>");
    if (opts.src) {
        const sr = document.createElement("div");
        sr.className = "src";
        sr.textContent = opts.src;
        div.appendChild(sr);
    }
    msgsEl.appendChild(div);
    msgsEl.scrollTop = msgsEl.scrollHeight;
    return div;
}
function renderSys(text) {
    const div = document.createElement("div");
    div.className = "sys";
    div.textContent = text;
    msgsEl.appendChild(div);
    msgsEl.scrollTop = msgsEl.scrollHeight;
}
function clearMsgs() {
    while (msgsEl.firstChild) msgsEl.removeChild(msgsEl.firstChild);
}

// ---- Audio / Mic / ASR ------------------------------------------------------
// M3.3:用 AudioWorklet 录 PCM 16k Int16 mono 直接发 ws 二进制帧。
async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        st.audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
        await st.audioCtx.audioWorklet.addModule("/static/pcm-worklet.js");
        const src = st.audioCtx.createMediaStreamSource(stream);
        st.workletNode = new AudioWorkletNode(st.audioCtx, "pcm-capture");
        st.workletNode.port.onmessage = (e) => {
            if (!st.recording) return;
            const int16 = e.data;  // Int16Array
            // 转 ArrayBuffer 透传
            const ab = int16.buffer.slice(int16.byteOffset, int16.byteOffset + int16.byteLength);
            const head = JSON.stringify({ type: "audio_chunk", mime: "audio/pcm;rate=16000", sample_rate: 16000 });
            const frame = new TextEncoder().encode(head + "\n");
            const merged = new Uint8Array(frame.length + ab.byteLength);
            merged.set(frame, 0);
            merged.set(new Uint8Array(ab), frame.length);
            if (st.ws && st.ws.readyState === WebSocket.OPEN) {
                st.ws.send(merged.buffer);
            }
        };
        src.connect(st.workletNode);
        st.recordStream = stream;
        st.recording = true;
        micBtn.classList.add("recording");
        micBtn.textContent = "🎤 停止";
        setStatus("thinking", "正在听…");
        // 通知后端 asr_start
        sendWs({ type: "start_asr" });
        renderSys("🎤 开始录音(对麦克风说话)");
    } catch (e) {
        renderSys("⚠ 麦克风不可用: " + e);
        st.recording = false;
    }
}
async function stopRecording() {
    if (!st.recording) return;
    st.recording = false;
    micBtn.classList.remove("recording");
    micBtn.textContent = "🎤 语音";
    if (st.recordStream) {
        try { st.recordStream.getTracks().forEach((t) => t.stop()); } catch (e) {}
        st.recordStream = null;
    }
    if (st.workletNode) {
        try { st.workletNode.disconnect(); } catch (e) {}
        st.workletNode = null;
    }
    if (st.audioCtx) {
        try { await st.audioCtx.close(); } catch (e) {}
        st.audioCtx = null;
    }
    sendWs({ type: "stop_asr" });
    setStatus("", "已连接");
}

// ---- Input 行为 -------------------------------------------------------------
function autoSize() {
    inEl.style.height = "auto";
    const lineH = 22;
    const maxRows = 8;
    const rows = Math.min(maxRows, Math.max(1, inEl.value.split("\n").length));
    inEl.style.height = (rows * lineH + 18) + "px";
}
inEl.addEventListener("input", autoSize);
inEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        doSend();
    }
});
sendBtn.addEventListener("click", () => doSend());
function doSend() {
    const text = inEl.value.trim();
    if (!text) return;
    inEl.value = "";
    autoSize();
    sendWs({ type: "user_text", text: text });
}
micBtn.addEventListener("click", () => {
    if (st.recording) stopRecording();
    else startRecording();
});

// ---- 派发触发词检测 ---------------------------------------------------------
function shouldDispatch(text) {
    const phrases = ["交给 PrisirAI", "派过去", "派给 PrisirAI", "让 PrisirAI 做",
                     "dispatch to prisirai", "交给主面板"];
    return phrases.some((p) => text.indexOf(p) >= 0);
}

// ---- WS 主对话 --------------------------------------------------------------
function sendWs(obj) {
    if (st.ws && st.ws.readyState === WebSocket.OPEN) {
        st.ws.send(JSON.stringify(obj));
    }
}
function connectWs() {
    if (st.ws && (st.ws.readyState === WebSocket.OPEN || st.ws.readyState === WebSocket.CONNECTING)) {
        return;
    }
    setStatus("", "连接中…");
    const ws = new WebSocket(st.wsUrl);
    st.ws = ws;
    ws.onopen = () => {
        st.wsRetry = 0;
        setStatus("", "已连接");
    };
    ws.onmessage = (ev) => {
        let m;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        handleWsMsg(m);
    };
    ws.onclose = () => {
        setStatus("off", "已断开");
        const delay = Math.min(10000, 1000 * Math.pow(1.5, st.wsRetry));
        st.wsRetry++;
        clearTimeout(st.wsTimer);
        st.wsTimer = setTimeout(connectWs, delay);
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
}
function handleWsMsg(m) {
    const t = m.type;
    if (t === "hello") {
        st.sid = m.sid || st.sid;
        renderContinueCard(m.continue);
    } else if (t === "history") {
        if (Array.isArray(m.messages)) {
            clearMsgs();
            for (const it of m.messages) {
                if (it.role === "user") renderMsg("user", it.text, { src: it.src || "text" });
                else if (it.role === "assistant") renderMsg("assistant", it.text, { src: it.model || "llm" });
            }
        }
    } else if (t === "user_echo") {
        renderMsg("user", m.text, { src: m.src || "text" });
        setStatus("thinking", "思考中…");
        // M3.27 派发触发词检测 → 弹确认卡
        if (shouldDispatch(m.text || "")) {
            askDispatchConfirm("检测到派发触发词,是否派发到 PrisirAI?");
        }
    } else if (t === "guard_block") {
        // M3.45 P0-2 护栏:Jev 前置拦截
        // - need_confirm=True → 复用 m323Confirm 弹卡
        // - need_confirm=False(block) → 直接提示,不让用户走 LLM
        const reason = m.reason || "risk";
        const risk = m.risk || "?";
        const jbP = (m.jailbreak_prob || 0).toFixed(2);
        if (m.need_confirm) {
            m323ConfirmTitle.textContent = "⚠ 消息被识别为高风险,是否继续?";
            m323ConfirmBody.textContent =
                `Jev 评估:risk=${risk}, 越狱概率=${jbP}。\n` +
                `继续 = 该消息依然发送给 AI;取消 = 丢弃。`;
            m323Confirm.style.display = "block";
            st.pendingConfirmResolve = (ok) => {
                if (ok) {
                    // 通知后端标记 sess 已 confirm,后续同 sess 不再弹
                    if (st.ws && st.ws.readyState === 1) {
                        st.ws.send(JSON.stringify({ type: "jev_confirm" }));
                    }
                    setStatus("", "已确认,等待下一条");
                } else {
                    setStatus("", "已拒绝");
                }
            };
        } else {
            renderSys(`🛡 Jev 已拦截(${reason}):risk=${risk}, 越狱=${jbP}`);
            setStatus("off", "已拦截");
        }
    } else if (t === "intent") {
        // M3.45 P0-1 意图分发:Jev Choice 给本轮消息打标签
        renderIntentBadge(m);
    } else if (t === "incremental_added") {
        // M3.45 P1-4 阶段成果入库成功
        renderIncrementalAdded(m);
    } else if (t === "incremental_skipped") {
        // 调试用:评估了但未入库(异常 reason)
        console.log("[p14] skipped:", m);
    } else if (t === "asr_started") {
        setStatus("speaking", "正在识别…");
    } else if (t === "asr_partial") {
        st.lastPartial = m.text || "";
        setStatus("speaking", "🎧 " + st.lastPartial);
    } else if (t === "asr_final") {
        st.lastPartial = "";
        // asr_final 后面紧跟 user_echo,我们不在这里再 render 一遍
        setStatus("thinking", "思考中…");
    } else if (t === "asr_finished") {
        setStatus("", "已连接");
    } else if (t === "asr_fallback") {
        renderSys("🔁 ASR 兜底切换:" + m.from_provider + " → " + m.to_provider);
    } else if (t === "capability_confirm_request") {
        // P3j T16-C: 弹确认卡(L1/L2/L3 风险)
        showCapConfirm(m);
    } else if (t === "capability_exec_result") {
        // P3j T16-C: 真发结果(主对话流中嵌的能力执行)
        renderCapExecResult(m);
    } else if (t === "ai_delta") {
        // 流式累积到当前 ai div
        let aiDiv = msgsEl.querySelector(".msg.them.streaming");
        if (!aiDiv) {
            aiDiv = renderMsg("assistant", "", { src: "llm" });
            aiDiv.classList.add("streaming");
            setStatus("speaking", "正在说…");
            st.aiTurnCount++;
        }
        aiDiv.innerHTML = escHtml((aiDiv.dataset.raw || "") + (m.text || "")).replace(/\n/g, "<br>");
        aiDiv.dataset.raw = (aiDiv.dataset.raw || "") + (m.text || "");
        msgsEl.scrollTop = msgsEl.scrollHeight;
    } else if (t === "ai_done") {
        const aiDiv = msgsEl.querySelector(".msg.them.streaming");
        if (aiDiv) {
            aiDiv.classList.remove("streaming");
            aiDiv.classList.remove("partial");
            aiDiv.innerHTML = escHtml(m.text || aiDiv.dataset.raw || "").replace(/\n/g, "<br>");
            aiDiv.dataset.raw = m.text || "";
            const sr = document.createElement("div");
            sr.className = "src";
            sr.textContent = (m.model || "llm") + (m.elapsed ? " · " + m.elapsed : "");
            aiDiv.appendChild(sr);
        } else {
            renderMsg("assistant", m.text || "", { src: m.model || "llm" });
        }
        setStatus("", "已连接");
        if (st.tts) tryPlayTts(m.text || "");
        refreshDispatchBadge();
    } else if (t === "barge_ack") {
        setStatus("speaking", "已打断");
    } else if (t === "knowledge_refs") {
        if (Array.isArray(m.hits) && m.hits.length) {
            const sysDiv = document.createElement("div");
            sysDiv.className = "sys";
            sysDiv.style.cursor = "pointer";
            sysDiv.title = "点击查看命中";
            sysDiv.innerHTML = "📎 知识库命中 " + m.hits.length + " 条 (来自对话上下文)";
            msgsEl.appendChild(sysDiv);
            msgsEl.scrollTop = msgsEl.scrollHeight;
        }
    } else if (t === "idle_warning") {
        renderSys("⚠ " + (m.msg || ("已 " + m.idle_sec + "s 无交互")));
    } else if (t === "idle_timeout") {
        renderSys("📴 " + (m.msg || "已挂断"));
    } else if (t === "bye") {
        renderSys("📴 已挂断(总轮数 " + m.turns + ")");
    } else if (t === "sessions") {
        renderSessions(m.items || []);
    } else if (t === "err") {
        renderSys("⚠ " + (m.err || "unknown error"));
    } else if (t === "pong") {
        // heartbeat
    }
}

// ---- 续接卡片 ---------------------------------------------------------------
function renderContinueCard(card) {
    if (!card || !card.preview || card.preview.length === 0) {
        continueCard.style.display = "none";
        return;
    }
    let html = '<div class="ctitle">⏪ 续接 ' + (card.sid || "") + (card.delta ? " · " + card.delta : "") + '</div>';
    for (const p of card.preview) {
        const roleCls = p.role === "user" ? "user" : "assist";
        html += '<div class="cprev"><span class="r ' + roleCls + '">' + (p.role || "") + '</span>' + escHtml(p.text || "") + '</div>';
    }
    continueCard.innerHTML = html;
    continueCard.style.display = "block";
}

// ---- 意图徽标(M3.45 P0-1) ---------------------------------------------------
const INTENT_BADGE_COLORS = {
    chat:      "var(--acc)",
    code:      "#3b82f6",
    search:    "#a855f7",
    tool_call: "#e0a34a",
    roleplay:  "#d6b26c",
    unknown:   "var(--dim)",
};

function renderIntentBadge(m) {
    // m = {intent, intent_zh, confidence, probabilities, route_applied, elapsed_ms}
    const intent = m.intent || "unknown";
    const zh = m.intent_zh || "未识别";
    const conf = (m.confidence || 0);
    const elapsed = m.elapsed_ms || 0;
    const routeApplied = !!m.route_applied;
    const color = INTENT_BADGE_COLORS[intent] || INTENT_BADGE_COLORS.unknown;
    const sym = intent === "code" ? "💻"
        : intent === "search" ? "🔍"
        : intent === "tool_call" ? "🛠"
        : intent === "roleplay" ? "🎭"
        : intent === "chat" ? "💬"
        : "❔";
    const tag = routeApplied ? "已路由" : "未路由";
    const confPct = (conf * 100).toFixed(0);
    // 把徽标挂到当前正在 streaming 的 AI 气泡上;
    // 没有 streaming 气泡(比如路由前就被 guard 拦)→ 用 sys 行显示
    const aiDiv = msgsEl.querySelector(".msg.them.streaming");
    if (aiDiv) {
        // 清掉同位置的旧徽标,避免叠加
        const old = aiDiv.querySelector(".intent-badge");
        if (old) old.remove();
        const badge = document.createElement("div");
        badge.className = "intent-badge";
        badge.style.cssText = (
            "font-size:11px;color:" + color +
            ";margin-top:4px;opacity:.85;display:flex;gap:6px;align-items:center;"
        );
        badge.innerHTML =
            sym + " <b>" + escHtml(zh) + "</b> " +
            "<span style='opacity:.6'>" + confPct + "%</span>" +
            "<span style='opacity:.5;font-size:10px'>·" + elapsed + "ms</span>" +
            "<span style='opacity:.5;font-size:10px'>·" + tag + "</span>";
        aiDiv.appendChild(badge);
    } else {
        // 无 streaming 气泡 → sys 行
        renderSys(sym + " 意图:" + zh + " · " + confPct + "% · " + elapsed + "ms · " + tag);
    }
}

// ---- 阶段成果入库(M3.45 P1-4) ---------------------------------------------
// 本会话累计入库段数(启动会话时清零)
st.p14SessionAdded = 0;

function renderIncrementalAdded(m) {
    // m = {value, value_zh, value_index, added_count, skipped_count, path,
    //      has_prob, total_added, reason}
    const added = m.added_count || 0;
    const skipped = m.skipped_count || 0;
    const valueZh = m.value_zh || "可入档";
    const total = m.total_added || 0;
    if (typeof total === "number") st.p14SessionAdded = total;
    // toast 通知(用现有的 sys 行即可,避免引入额外 UI 组件)
    let pathShort = "";
    if (m.path) {
        const parts = m.path.split(/[\\\/]/);
        pathShort = parts[parts.length - 1];
    }
    const skipInfo = skipped > 0 ? ` · 跳重 ${skipped}` : "";
    const icon = "📚";
    renderSys(
        `${icon} 阶段成果入库 ${valueZh}: +${added} 段${skipInfo}`
        + (pathShort ? ` → ${pathShort}` : "")
        + (m.has_prob ? ` · prob=${(m.has_prob * 100).toFixed(0)}%` : "")
    );
    // 更新顶部徽标(若元素存在)
    const badge = $("p14Badge");
    if (badge) {
        badge.textContent = String(st.p14SessionAdded);
        badge.style.display = st.p14SessionAdded > 0 ? "inline-block" : "none";
    }
}

// ---- 历史侧栏 ---------------------------------------------------------------
btnHistory.addEventListener("click", () => {
    st.sessionsOpen = !st.sessionsOpen;
    sessionsPanel.classList.toggle("open", st.sessionsOpen);
    if (st.sessionsOpen) loadSessions();
});
function loadSessions() {
    fetch("/api/sessions").then((r) => r.json()).then((j) => {
        if (j.ok) renderSessions(j.items || []);
    }).catch(() => {});
}
function renderSessions(items) {
    if (!items.length) {
        sessionsList.innerHTML = '<div style="color:var(--dim);font-size:12px">暂无历史</div>';
        return;
    }
    sessionsList.innerHTML = items.map((it) => (
        '<div class="sessitem" data-sid="' + escHtml(it.sid || "") + '">'
        + '<div class="title-row" style="display:flex;justify-content:space-between;align-items:center;gap:6px">'
        + '<span class="title">' + escHtml(it.title || it.sid || "") + '</span>'
        + '<button class="sess-del" data-del="' + escHtml(it.sid || "") + '" title="删除该会话">🗑</button>'
        + '</div>'
        + '<div class="preview">' + escHtml((it.preview || "").slice(0, 80)) + '</div>'
        + '<div class="meta">' + escHtml(it.meta || "") + '</div>'
        + '</div>'
    )).join("");
    // 行点击 = 切会话
    sessionsList.querySelectorAll(".sessitem").forEach((el) => {
        el.addEventListener("click", (e) => {
            if (e.target.closest(".sess-del")) return;  // 删除按钮独立
            const sid = el.getAttribute("data-sid");
            if (sid) {
                st.wsUrl = (location.protocol === "https:" ? "wss:" : "ws:")
                    + "//" + location.host + "/ws?sid=" + encodeURIComponent(sid);
                if (st.ws) { try { st.ws.close(); } catch (e) {} }
                st.ws = null;
                connectWs();
                st.sessionsOpen = false;
                sessionsPanel.classList.remove("open");
            }
        });
    });
    // 🗑 = 二次确认 → DELETE /api/sessions?sid=...
    sessionsList.querySelectorAll(".sess-del").forEach((b) => {
        b.addEventListener("click", async (e) => {
            e.stopPropagation();
            const sid = b.getAttribute("data-del");
            if (!sid) return;
            if (!window.confirm("删除该会话?对话内容将一并清除。")) return;
            try {
                const r = await fetch("/api/sessions?sid=" + encodeURIComponent(sid), { method: "DELETE" });
                const j = await r.json();
                if (j.ok) {
                    loadSessions();
                    renderSys("🗑 已删除 " + sid.slice(0, 8));
                } else {
                    renderSys("❌ 删除失败: " + (j.err || "unknown"));
                }
            } catch (err) {
                renderSys("❌ 删除异常: " + err);
            }
        });
    });
}

// ---- 设置面板 ---------------------------------------------------------------
btnSettings.addEventListener("click", () => {
    st.settingsOpen = !st.settingsOpen;
    settingsPanel.classList.toggle("open", st.settingsOpen);
    if (st.settingsOpen) loadSettingsPanel();
});
settingsClose.addEventListener("click", () => {
    st.settingsOpen = false;
    settingsPanel.classList.remove("open");
});
function loadSettingsPanel() {
    // M3.29.8 — 厂商下拉换 ASR,删除「已配」重复块;真实测试连接
    // M3.45 — 加 Jev 护栏 settings
    Promise.all([
        fetch("/api/asr/providers").then((r) => r.json()).catch(() => ({ ok: false, providers: [] })),
        fetch("/api/m323/cfg").then((r) => r.json()).catch(() => ({ ok: false })),
        fetch("/api/dispatch/settings").then((r) => r.json()).catch(() => ({})),
        fetch("/api/m345/jev/cfg").then((r) => r.json()).catch(() => ({ ok: false })),
    ]).then(([prov, m323, dispatch, m345]) => {
        renderProviderForm(prov.providers || []);
        renderM323Panel(m323.cfg || {}, m323.fcontent || {});
        renderM327Panel(dispatch || {});
        renderM345Panel(m345.cfg || {});
        renderM345IntentPanel(m345.intent_cfg || {});
        renderM345P14Panel(m345.p14_cfg || {});
    });
}
function renderProviderForm(specs) {
    const sel = $("selProvider");
    const fields = $("providerFields");
    if (!sel || !fields) return;
    // M3.29.9 — 分级:简单组(填 key 即可)+ 高级组(需在控制台或本地准备)
    const simpleSpecs = specs.filter((p) => p.tier === "simple");
    const advSpecs = specs.filter((p) => p.tier !== "simple");
    let opts = '<option value="">-- 选择 ASR 厂商 --</option>';
    if (simpleSpecs.length) {
        opts += '<optgroup label="✨ 简单组(填 key 即可)">'
              + simpleSpecs.map((p) =>
                  '<option value="' + escHtml(p.name) + '">' + escHtml(p.display) + '</option>'
                ).join("")
              + '</optgroup>';
    }
    if (advSpecs.length) {
        opts += '<optgroup label="🛠 高级组(需在控制台或本地准备)">'
              + advSpecs.map((p) =>
                  '<option value="' + escHtml(p.name) + '">' + escHtml(p.display) + '</option>'
                ).join("")
              + '</optgroup>';
    }
    sel.innerHTML = opts;
    // onchange 用 onXxx 而非 addEventListener("change"):防止 settingsPanel 多次打开重复挂
    sel.onchange = () => {
        const id = sel.value;
        const sp = specs.find((p) => p.name === id);
        if (!sp) { fields.innerHTML = ""; return; }
        let html = "";
        // 把 url 字段(base_url / endpoint)排第一位(若有)
        const ordered = (sp.fields || []).slice().sort((a, b) => {
            const aUrl = (a.key === "base_url" || a.key === "endpoint") ? -1 : 0;
            const bUrl = (b.key === "base_url" || b.key === "endpoint") ? -1 : 0;
            return aUrl - bUrl;
        });
        for (const f of ordered) {
            const label = f.label || f.key;
            const type = f.secret ? "password" : "text";
            html += '<label>' + escHtml(label) + (f.required ? ' <span style="color:var(--bad)">*</span>' : '') + '</label>'
                  + '<input type="' + type + '" data-field="' + escHtml(f.key) + '" '
                  + 'placeholder="' + escHtml(f.placeholder || (f.default || "")) + '" '
                  + 'value="' + escHtml(f.default || "") + '">';
        }
        // M3.29.9 — 高级组的厂商追加黄色 prep_hint 提示
        if (sp.tier !== "simple" && sp.prep_hint) {
            // 多行 hint 保留 \n,用 white-space:pre-wrap 渲染
            html += '<div style="margin-top:8px;padding:8px 10px;background:rgba(224,163,74,.15);border:1px solid var(--warn);border-radius:6px;font-size:12px;color:#8a6d3b;white-space:pre-wrap;line-height:1.5">⚠ '
                  + escHtml(sp.prep_hint) + '</div>';
        }
        fields.innerHTML = html;
    };
    // 测试连接 — 真打 /api/settings/test,空必填字段直接返错
    const btnTest = $("btnTest");
    if (btnTest) btnTest.onclick = () => {
        const id = sel.value;
        if (!id) { setTestOut("请先选厂商", false); return; }
        const sp = specs.find((p) => p.name === id);
        if (!sp) { setTestOut("厂商不存在", false); return; }
        const form = collectForm();
        // 前端预检:必填字段不能空(测试就拒)
        const missing = (sp.fields || []).filter((f) => {
            if (!f.required) return false;
            const v = (form[f.key] || "").trim();
            return !v;
        });
        if (missing.length) {
            setTestOut("❌ 必填字段未填: " + missing.map((f) => f.label || f.key).join(", "), false);
            return;
        }
        setTestOut("⏳ 测试中…", null);
        fetch("/api/settings/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ provider: id, form_data: form })
        }).then((r) => r.json()).then((j) => {
            setTestOut(j.ok ? "✅ 连接 OK" : ("❌ " + (j.err || "失败")), !!j.ok);
        }).catch((e) => setTestOut("❌ 网络异常: " + e, false));
    };
    // 保存 — POST /api/settings 落盘
    const btnSave = $("btnSave");
    if (btnSave) btnSave.onclick = () => {
        const id = sel.value;
        if (!id) { setTestOut("请先选厂商", false); return; }
        const form = collectForm();
        setTestOut("⏳ 保存中…", null);
        fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ active_provider: id, providers: { [id]: form } })
        }).then((r) => r.json()).then((j) => {
            setTestOut(j.ok ? "✅ 已保存" : ("❌ " + (j.err || "保存失败")), !!j.ok);
        }).catch((e) => setTestOut("❌ 网络异常: " + e, false));
    };
    // 触发一次默认显示
    if (sel.value) sel.onchange();
}
function collectForm() {
    const out = {};
    const fields = $("providerFields");
    if (!fields) return out;
    fields.querySelectorAll("[data-field]").forEach((i) => {
        out[i.getAttribute("data-field")] = i.value;
    });
    return out;
}
function setTestOut(text, ok) {
    const el = $("testOut");
    if (!el) return;
    el.textContent = text;
    el.className = "test-out " + (ok === true ? "ok" : ok === false ? "bad" : "");
}

// ---- M3.24 panel ------------------------------------------------------------
function renderM323Panel(cfg, fcontent) {
    const screenToggle = $("m323ScreenToggle");
    const knowToggle = $("m323KnowledgeToggle");
    const rootInput = $("m323FcontentRoot");
    const statusEl = $("m323Status");
    if (!screenToggle) return;
    screenToggle.checked = !!cfg.asr_screen_capture;
    knowToggle.checked = !!cfg.asr_knowledge_lookup;
    rootInput.value = cfg.fcontent_root || "";

    function checkGate(cb, label, body) {
        // 从关 → 开 → 弹 gate
        if (cb.checked) {
            cb.checked = false;
            m323ConfirmBody.textContent = body;
            m323ConfirmTitle.textContent = "⚠ 开启" + label + "需要授权";
            m323Confirm.style.display = "block";
            st.pendingConfirmResolve = (ok) => {
                if (ok) cb.checked = true;
                doM323Save();
            };
        } else {
            doM323Save();
        }
    }
    function doM323Save() {
        const body = {
            asr_screen_capture: screenToggle.checked,
            asr_knowledge_lookup: knowToggle.checked,
            fcontent_root: rootInput.value.trim(),
        };
        fetch("/api/m323/cfg", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        }).then((r) => r.json()).then((j) => {
            if (statusEl) {
                statusEl.textContent = j.ok ? "✅ 已保存" : ("❌ " + (j.err || "失败"));
                statusEl.className = "test-out " + (j.ok ? "ok" : "bad");
            }
        });
    }
    screenToggle.addEventListener("change", () => checkGate(screenToggle, "读屏", "开启后,每次你说话 Prisir 会自动读取当前屏幕 UI 树作为事实背景。仅在本地内存处理,不会上传。"));
    knowToggle.addEventListener("change", () => checkGate(knowToggle, "本地知识库", "开启后,每次你说话 Prisir 会查询你指定的本地目录(FTS5 索引,top-3)。仅在本地内存处理。"));
    if ($("btnM323Save")) $("btnM323Save").addEventListener("click", doM323Save);
    if ($("btnM323Rebuild")) $("btnM323Rebuild").addEventListener("click", () => {
        if (statusEl) statusEl.textContent = "⏳ 重建中…";
        fetch("/api/m323/fcontent/rebuild", { method: "POST" }).then((r) => r.json()).then((j) => {
            if (statusEl) {
                statusEl.textContent = j.ok ? ("✅ 已加入队列: " + (j.queued || "")) : ("❌ " + (j.err || "失败"));
                statusEl.className = "test-out " + (j.ok ? "ok" : "bad");
            }
        });
    });
    m323ConfirmOk.addEventListener("click", () => {
        m323Confirm.style.display = "none";
        if (st.pendingConfirmResolve) { st.pendingConfirmResolve(true); st.pendingConfirmResolve = null; }
    });
    m323ConfirmCancel.addEventListener("click", () => {
        m323Confirm.style.display = "none";
        if (st.pendingConfirmResolve) { st.pendingConfirmResolve(false); st.pendingConfirmResolve = null; }
    });
    // P3j T16-C: 视频能力确认卡
    capConfirmOk.addEventListener("click", () => {
        const pending = st.pendingCapConfirm;
        capConfirm.style.display = "none";
        st.pendingCapConfirm = null;
        if (pending && st.ws && st.ws.readyState === 1) {
            st.ws.send(JSON.stringify({
                type: "capability_confirm",
                capability: pending.capability,
                args: pending.args,
                approved: true,
            }));
        }
    });
    capConfirmCancel.addEventListener("click", () => {
        const pending = st.pendingCapConfirm;
        capConfirm.style.display = "none";
        st.pendingCapConfirm = null;
        if (pending && st.ws && st.ws.readyState === 1) {
            st.ws.send(JSON.stringify({
                type: "capability_confirm",
                capability: pending.capability,
                args: pending.args,
                approved: false,
            }));
        }
    });
}

// P3j T16-C: 弹视频能力确认卡
function showCapConfirm(m) {
    const cap = m.capability || "";
    const risk = m.risk || "L1";
    const args = m.args || {};
    if (capConfirmTitle) capConfirmTitle.textContent = m.title || ("⚠ 能力需要确认:" + cap);
    if (capConfirmCap) capConfirmCap.textContent = cap;
    if (capConfirmRisk) capConfirmRisk.textContent = risk;
    if (capConfirmBody) capConfirmBody.textContent = m.confirm || ("风险等级:" + risk);
    if (capConfirmArgs) {
        // 渲染 args 为 key=value 列表(便于用户看清要发什么)
        const lines = Object.keys(args).map(k => `${k} = ${args[k]}`);
        capConfirmArgs.textContent = lines.length ? lines.join("\n") : "(无参数)";
    }
    if (capConfirm) {
        capConfirm.setAttribute("data-risk", risk);
        capConfirm.style.display = "flex";
    }
    st.pendingCapConfirm = { capability: cap, args: args };
}

// P3j T16-C: 渲染真发结果(主对话流)
function renderCapExecResult(m) {
    const cap = m.capability || "?";
    const ok = m.ok === true;
    const err = m.error || "";
    const result = m.result || {};
    // P3j T16-D: 渲染一个富节点 — 状态 + capability + 跳转链接
    const div = document.createElement("div");
    div.className = "sys cap-exec " + (ok ? "cap-ok" : "cap-bad");
    const sym = ok ? "✅" : "❌";
    const head = document.createElement("div");
    head.className = "cap-exec-head";
    head.textContent = `${sym} ${cap} ${ok ? "已执行" : "执行失败"}`;
    div.appendChild(head);
    if (!ok && err) {
        const errDiv = document.createElement("div");
        errDiv.className = "cap-exec-err";
        errDiv.textContent = err;
        div.appendChild(errDiv);
    }
    // P3j T17-H: 中文翻译 + hint/link 跳转(主对话 EXEC 失败时引导用户)
    if (!ok && m.zh) {
        const zhDiv = document.createElement("div");
        zhDiv.className = "cap-exec-zh";
        zhDiv.textContent = "💡 " + m.zh;
        div.appendChild(zhDiv);
    }
    if (!ok && m.link) {
        const publisherBase = "http://localhost:18899";
        const focusId = String(m.link).replace(/^\//, "");
        const a = document.createElement("a");
        a.className = "cap-exec-link";
        a.textContent = "💡 " + (m.hint || "点此去配置") + " →";
        a.href = publisherBase + "/?focus=" + encodeURIComponent(focusId);
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        div.appendChild(a);
    }
    // 真发成功的:展示 artifact 路径(若有) + 跳转 💬 一句话 Tab 链接
    if (ok && result.artifact && result.artifact.path) {
        const pathDiv = document.createElement("div");
        pathDiv.className = "cap-exec-path";
        pathDiv.textContent = "📁 " + result.artifact.path;
        div.appendChild(pathDiv);
    }
    // 跳转到 💬 一句话 Tab 链接(若有 window 切换能力)
    const link = document.createElement("a");
    link.className = "cap-exec-link";
    link.textContent = "💬 查看「一句话」任务列表";
    link.href = "#";
    link.style.cursor = "pointer";
    link.addEventListener("click", (ev) => {
        ev.preventDefault();
        // 触发 custom event,让 wechat-publisher 监听 + 切到对应 Tab
        try {
            window.dispatchEvent(new CustomEvent("prisIrai:cap-exec-link", {
                detail: { capability: cap, ok: ok, result: result },
            }));
        } catch (e) { /* ignore */ }
        renderSys(`(${cap} 已跳转 💬 一句话)`);
    });
    div.appendChild(link);
    msgsEl.appendChild(div);
    msgsEl.scrollTop = msgsEl.scrollHeight;
}

// P3j T16-D: ESC 关闭确认卡
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && capConfirm && capConfirm.style.display !== "none") {
        capConfirmCancel.click();
    }
});

// ---- M3.27 panel ------------------------------------------------------------
function renderM345Panel(cfg) {
    const enToggle = $("m345EnabledToggle");
    const riskSel = $("m345RiskThreshold");
    const jbInput = $("m345JailbreakThreshold");
    const testInput = $("m345TestInput");
    const statusEl = $("m345Status");
    if (!enToggle) return;
    enToggle.checked = !!cfg.jev_enabled;
    if (riskSel) riskSel.value = cfg.jev_risk_threshold || "medium";
    if (jbInput) jbInput.value = (cfg.jev_jailbreak_threshold ?? 0.7);

    function save() {
        // 同步收集 intent 字段,一起提交(避免 intent 段单独再点保存)
        const intentEnabled = $("m345IntentEnabled");
        const intentRouting = $("m345IntentRouting");
        const intentTimeout = $("m345IntentTimeout");
        const intentMinConf = $("m345IntentMinConf");
        fetch("/api/m345/jev/cfg", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                jev_enabled: enToggle.checked,
                jev_risk_threshold: riskSel ? riskSel.value : "medium",
                jev_jailbreak_threshold: jbInput ? parseFloat(jbInput.value) : 0.7,
                intent_enabled: intentEnabled ? intentEnabled.checked : true,
                intent_routing: intentRouting ? intentRouting.checked : true,
                intent_timeout_sec: intentTimeout ? parseFloat(intentTimeout.value) : 1.0,
                intent_min_confidence: intentMinConf ? parseFloat(intentMinConf.value) : 0.55,
            })
        }).then((r) => r.json()).then((j) => {
            if (statusEl) {
                statusEl.textContent = j.ok ? "✅ 已保存(jev + intent)" : ("❌ " + (j.err || "失败"));
                statusEl.className = "test-out " + (j.ok ? "ok" : "bad");
            }
        });
    }
    if ($("btnM345Save")) $("btnM345Save").addEventListener("click", save);
    if ($("btnM345Test")) $("btnM345Test").addEventListener("click", () => {
        const text = (testInput && testInput.value || "").trim();
        if (!text) {
            if (statusEl) { statusEl.textContent = "❌ 测试文本为空"; statusEl.className = "test-out bad"; }
            return;
        }
        if (statusEl) { statusEl.textContent = "⏳ 探测中…"; statusEl.className = "test-out"; }
        fetch("/api/m345/jev/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text })
        }).then((r) => r.json()).then((j) => {
            if (!statusEl) return;
            if (!j.ok) {
                statusEl.textContent = "❌ " + (j.err || "失败");
                statusEl.className = "test-out bad";
                return;
            }
            const r = (j.judgments && j.judgments.risk) || {};
            const jb = (j.judgments && j.judgments.jailbreak) || {};
            const d = j.decision || {};
            statusEl.textContent =
                `risk=${r.score || "?"} conf=${(r.confidence || 0).toFixed(2)} | ` +
                `jailbreak=${jb.yes ? "YES" : "no"} prob=${(jb.probability || 0).toFixed(2)} | ` +
                `decision=${d.reason}${d.block ? " (硬拦)" : d.need_confirm ? " (待确认)" : " (直通)"}`;
            statusEl.className = "test-out ok";
        });
    });
}

// ---- M3.45 P0-1 意图分发 panel(2026-09-22)---------------------------------
function renderM345IntentPanel(cfg) {
    const enToggle = $("m345IntentEnabled");
    const routeToggle = $("m345IntentRouting");
    const timeoutInput = $("m345IntentTimeout");
    const minConfInput = $("m345IntentMinConf");
    const testInput = $("m345IntentTestInput");
    const statusEl = $("m345IntentStatus");
    if (!enToggle) return;
    enToggle.checked = cfg.intent_enabled !== false;
    if (routeToggle) routeToggle.checked = cfg.intent_routing !== false;
    if (timeoutInput) timeoutInput.value = cfg.intent_timeout_sec ?? 1.0;
    if (minConfInput) minConfInput.value = cfg.intent_min_confidence ?? 0.55;

    function save() {
        fetch("/api/m345/jev/cfg", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                intent_enabled: enToggle.checked,
                intent_routing: routeToggle ? routeToggle.checked : true,
                intent_timeout_sec: timeoutInput ? parseFloat(timeoutInput.value) : 1.0,
                intent_min_confidence: minConfInput ? parseFloat(minConfInput.value) : 0.55,
            })
        }).then((r) => r.json()).then((j) => {
            if (statusEl) {
                statusEl.textContent = j.ok ? "✅ 已保存" : ("❌ " + (j.err || "失败"));
                statusEl.className = "test-out " + (j.ok ? "ok" : "bad");
            }
        });
    }
    if ($("btnM345IntentSave")) $("btnM345IntentSave").addEventListener("click", save);
    if ($("btnM345IntentTest")) $("btnM345IntentTest").addEventListener("click", () => {
        const text = (testInput && testInput.value || "").trim();
        if (!text) {
            if (statusEl) { statusEl.textContent = "❌ 测试文本为空"; statusEl.className = "test-out bad"; }
            return;
        }
        if (statusEl) { statusEl.textContent = "⏳ 探测中…"; statusEl.className = "test-out"; }
        fetch("/api/m345/intent/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text })
        }).then((r) => r.json()).then((j) => {
            if (!statusEl) return;
            if (!j.ok) {
                statusEl.textContent = "❌ " + (j.err || "失败");
                statusEl.className = "test-out bad";
                return;
            }
            const it = j.intent || {};
            const probs = it.probabilities || {};
            const probsStr = Object.keys(probs).length
                ? Object.entries(probs)
                    .sort((a, b) => b[1] - a[1])
                    .map(([k, v]) => k + ":" + (v * 100).toFixed(0) + "%")
                    .join(" ")
                : "(无)";
            statusEl.textContent =
                `意图=${it.choice || "?"} (${it.confidence >= 0.55 ? "已路由" : "未路由"}) ` +
                `conf=${(it.confidence || 0).toFixed(2)} | ` +
                `probs=${probsStr} | ${j.elapsed_ms || 0}ms`;
            statusEl.className = "test-out ok";
        });
    });
}

// ---- M3.45 P1-4 阶段成果入库 panel(2026-09-22)---------------------------
function renderM345P14Panel(cfg) {
    const enToggle = $("m345P14Enabled");
    const minValSel = $("m345P14MinValue");
    const topicSel = $("m345P14TopicStrategy");
    const timeoutInput = $("m345P14Timeout");
    const userInput = $("m345P14UserText");
    const asstInput = $("m345P14AssistantText");
    const statusEl = $("m345P14Status");
    const statsEl = $("m345P14Stats");
    if (!enToggle) return;
    enToggle.checked = cfg.p14_enabled !== false;
    if (minValSel) minValSel.value = cfg.p14_min_value ?? 2;
    if (topicSel) topicSel.value = cfg.p14_topic_strategy ?? "auto";
    if (timeoutInput) timeoutInput.value = cfg.p14_timeout_sec ?? 1.2;

    function refreshStats() {
        fetch("/api/m345/p14/stats").then((r) => r.json()).then((j) => {
            if (!statsEl) return;
            if (!j.ok) {
                statsEl.textContent = "未连接";
                return;
            }
            statsEl.textContent = `${j.n_segments} 段 / ${j.n_files} 文件 (root=${j.root || "未设"})`;
        }).catch(() => {
            if (statsEl) statsEl.textContent = "查询失败";
        });
    }
    refreshStats();

    function save() {
        fetch("/api/m345/jev/cfg", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                p14_enabled: enToggle.checked,
                p14_min_value: minValSel ? parseInt(minValSel.value, 10) : 2,
                p14_topic_strategy: topicSel ? topicSel.value : "auto",
                p14_timeout_sec: timeoutInput ? parseFloat(timeoutInput.value) : 1.2,
            })
        }).then((r) => r.json()).then((j) => {
            if (statusEl) {
                statusEl.textContent = j.ok ? "✅ 已保存" : ("❌ " + (j.err || "失败"));
                statusEl.className = "test-out " + (j.ok ? "ok" : "bad");
            }
        });
    }
    if ($("btnM345P14Save")) $("btnM345P14Save").addEventListener("click", save);
    if ($("btnM345P14StatsRefresh")) $("btnM345P14StatsRefresh").addEventListener("click", refreshStats);
    if ($("btnM345P14Test")) $("btnM345P14Test").addEventListener("click", () => {
        const userText = (userInput && userInput.value || "").trim();
        const asstText = (asstInput && asstInput.value || "").trim();
        if (!userText || !asstText) {
            if (statusEl) {
                statusEl.textContent = "❌ user_text / assistant_text 都必填";
                statusEl.className = "test-out bad";
            }
            return;
        }
        if (statusEl) { statusEl.textContent = "⏳ 评估+入库中…"; statusEl.className = "test-out"; }
        fetch("/api/m345/p14/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_text: userText, assistant_text: asstText })
        }).then((r) => r.json()).then((j) => {
            if (!statusEl) return;
            if (!j.ok) {
                statusEl.textContent = "❌ " + (j.err || "失败");
                statusEl.className = "test-out bad";
                return;
            }
            const r = j.result || {};
            let line = `reason=${r.reason || "?"}`;
            if (r.has_prob) line += ` has=${(r.has_prob * 100).toFixed(0)}%`;
            if (r.value_index !== undefined) line += ` value=${r.value || "?"}(${r.value_index})`;
            if (r.added_count !== undefined) line += ` added=${r.added_count} skipped=${r.skipped_count || 0}`;
            if (r.path) line += ` path=${r.path.split(/[\\\/]/).pop()}`;
            line += ` · ${j.elapsed_ms || 0}ms`;
            statusEl.textContent = line;
            statusEl.className = r.added_count > 0 ? "test-out ok" : "test-out";
            refreshStats();
        });
    });
    // 顶部按钮点击 = 打开 settings + 刷新索引
    if ($("btnP14Stats")) $("btnP14Stats").addEventListener("click", () => {
        if (typeof openSettings === "function") openSettings();
        // 滚到 P1-4 段
        setTimeout(() => {
            const det = document.querySelector(".m345-p14-section");
            if (det && det.open !== undefined) det.open = true;
            refreshStats();
        }, 100);
    });
}

// ---- M3.27 panel ------------------------------------------------------------
function renderM327Panel(d) {
    const enToggle = $("m327EnableToggle");
    const knToggle = $("m327IncludeKnowledgeToggle");
    const urlInput = $("m327PrisiraiUrl");
    const statusEl = $("m327Status");
    if (!enToggle) return;
    enToggle.checked = !!d.enable_dispatch;
    knToggle.checked = !!d.dispatch_include_knowledge;
    // M3.29.9 — URL override(留空 = 走 env/default)
    if (urlInput) urlInput.value = d.prisirai_url_override || "";
    function save() {
        fetch("/api/dispatch/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                enable_dispatch: enToggle.checked,
                dispatch_include_knowledge: knToggle.checked,
            })
        }).then((r) => r.json()).then((j) => {
            if (statusEl) {
                statusEl.textContent = j.ok ? "✅ 已保存" : ("❌ " + (j.err || "失败"));
                statusEl.className = "test-out " + (j.ok ? "ok" : "bad");
            }
        });
    }
    enToggle.addEventListener("change", save);
    knToggle.addEventListener("change", save);
    if ($("btnM327Save")) $("btnM327Save").addEventListener("click", save);

    // M3.29.9 — 真打 PrisirAI 端点探测
    if ($("btnM327Test")) $("btnM327Test").onclick = () => {
        if (statusEl) { statusEl.textContent = "⏳ 测试中…"; statusEl.className = "test-out"; }
        fetch("/api/dispatch/test", { method: "POST" })
            .then((r) => r.json()).then((j) => {
                if (!statusEl) return;
                if (j.ok) {
                    const warn = j.warning ? " (" + j.warning + ")" : "";
                    statusEl.textContent = "✅ PrisirAI 可达 " + j.url + " · HTTP " + j.status + warn + " · " + j.ms + "ms";
                    statusEl.className = "test-out ok";
                } else {
                    statusEl.textContent = "❌ PrisirAI 不可达: " + (j.err || "失败") + "\nurl=" + j.url;
                    statusEl.className = "test-out bad";
                }
            }).catch((e) => {
                if (statusEl) { statusEl.textContent = "❌ 网络异常: " + e; statusEl.className = "test-out bad"; }
            });
    };

    // M3.29.9 — PrisirAI URL override 保存/恢复
    const urlStatus = $("m327UrlStatus");
    if ($("btnM327UrlSave")) $("btnM327UrlSave").onclick = () => {
        const url = ($("m327PrisiraiUrl") && $("m327PrisiraiUrl").value || "").trim();
        fetch("/api/dispatch/settings", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prisirai_url_override: url })
        }).then((r) => r.json()).then((j) => {
            if (urlStatus) {
                urlStatus.textContent = j.ok
                    ? ("✅ 已保存(当前生效:" + (j.settings.prisirai_url || "") + ")")
                    : ("❌ " + (j.err || "失败"));
                urlStatus.className = "test-out " + (j.ok ? "ok" : "bad");
            }
            if (j.ok && j.settings) {
                const u = $("m327PrisiraiUrl");
                if (u) u.value = j.settings.prisirai_url_override || "";
            }
        });
    };
    if ($("btnM327UrlReset")) $("btnM327UrlReset").onclick = () => {
        const u = $("m327PrisiraiUrl"); if (u) u.value = "";
        if ($("btnM327UrlSave")) $("btnM327UrlSave").click();
    };
}
function askDispatchConfirm(text) {
    const ok = window.confirm("📤 " + text);
    if (ok) doDispatch("incremental");
}
function doDispatch(mode) {
    if (!st.sid) return;
    fetch("/api/dispatch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sid: st.sid, mode: mode || "incremental" })
    }).then((r) => r.json()).then((j) => {
        if (j.ok) {
            renderSys("📤 已派发 " + (j.count || 0) + " 条到 PrisirAI");
        } else {
            renderSys("❌ 派发失败: " + (j.err || "unknown"));
        }
        refreshDispatchBadge();
    });
}
btnDispatch.addEventListener("click", () => doDispatch("incremental"));
function refreshDispatchBadge() {
    if (!dispatchBadge || !st.sid) return;
    fetch("/api/dispatch/buffer?sid=" + encodeURIComponent(st.sid)).then((r) => r.json()).then((j) => {
        const pending = j.pending || 0;
        if (pending > 0) {
            dispatchBadge.textContent = String(pending);
            dispatchBadge.style.display = "inline-block";
        } else {
            dispatchBadge.style.display = "none";
        }
    });
}
setInterval(refreshDispatchBadge, 5000);

// ---- 音乐 UI 已与语伴完全解耦(2026-09-19 M3.29.8) -------------------------
// 不再在语伴页提供音乐启动入口。音乐启动:PrisirAI Tauri 托盘菜单。
// /api/music/dispatch + /api/music/start 后端仍保留,只对 Tauri 壳可见。

// ---- TTS toggle -------------------------------------------------------------
btnTts.addEventListener("click", () => {
    st.tts = !st.tts;
    localStorage.setItem("prisir.tts", st.tts ? "on" : "off");
    btnTts.style.color = st.tts ? "" : "var(--dim)";
    renderSys(st.tts ? "🔊 已开启语音朗读" : "🔇 已关闭语音朗读");
});
function tryPlayTts(text) {
    if (!text || !window.speechSynthesis) return;
    try {
        window.speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.lang = "zh-CN";
        u.rate = 1.0;
        window.speechSynthesis.speak(u);
    } catch (e) {}
}

// ---- 启动 / 挂断(M3.29.8 — 单按钮二态) ---------------------------------------
// 计时仅在用户点「启动」后累加,「挂断」后停 + 归零 + 真正断开 ws
function startSession() {
    if (st.running) return;
    st.running = true;
    st.sessionStart = Date.now();
    // M3.45 P1-4:启动会话时清零本会话入库累计
    st.p14SessionAdded = 0;
    const p14Badge = $("p14Badge");
    if (p14Badge) p14Badge.style.display = "none";
    if (timerEl) timerEl.textContent = "00:00";
    setStatus("", "会话中…");
    connectWs();
    if (btnStart) {
        btnStart.textContent = "⏸ 挂断";
        btnStart.classList.add("danger");
        btnStart.classList.remove("primary");
    }
    renderSys("▶ 会话已开始,计时仅在此期间累加");
}
function stopSession() {
    if (!st.running) return;
    st.running = false;
    if (st.recording) stopRecording();
    sendWs({ type: "hangup" });
    if (st.ws) { try { st.ws.close(); } catch (e) {} st.ws = null; }
    st.sessionStart = 0;
    if (timerEl) timerEl.textContent = "00:00";
    setStatus("off", "已挂断");
    if (btnStart) {
        btnStart.textContent = "▶ 启动";
        btnStart.classList.remove("danger");
        btnStart.classList.add("primary");
    }
    renderSys("⏸ 已挂断(计时已停止)");
}
if (btnStart) btnStart.addEventListener("click", () => {
    if (st.running) stopSession(); else startSession();
});

// ---- Emoji panel ------------------------------------------------------------
btnEmoji.addEventListener("click", () => {
    st.emojiOpen = !st.emojiOpen;
    emojiPanel.classList.toggle("open", st.emojiOpen);
    if (st.emojiOpen) renderEmoji(st.activeEmojiGroup);
});
emojiTabs.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-grp]");
    if (!btn) return;
    st.activeEmojiGroup = parseInt(btn.getAttribute("data-grp")) || 0;
    emojiTabs.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
    renderEmoji(st.activeEmojiGroup);
});
function renderEmoji(g) {
    const list = EMOJI_GROUPS[g] || EMOJI_GROUPS[0];
    emojiList.innerHTML = Array.from(list).map((ch) =>
        '<span data-emoji="' + ch + '">' + ch + '</span>'
    ).join("");
    emojiList.querySelectorAll("span[data-emoji]").forEach((sp) => {
        sp.addEventListener("click", () => {
            const ch = sp.getAttribute("data-emoji");
            const start = inEl.selectionStart || 0;
            const end = inEl.selectionEnd || 0;
            inEl.value = inEl.value.slice(0, start) + ch + inEl.value.slice(end);
            inEl.focus();
            inEl.selectionStart = inEl.selectionEnd = start + ch.length;
            autoSize();
        });
    });
}

// =============================================================================
// 音乐 UI 已与语伴完全解耦(2026-09-19 M3.29.8)
// 音乐启动入口:PrisirAI Tauri 托盘菜单。
// =============================================================================

// ---- 启动 -------------------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
    autoSize();
    // 不自动 connectWs — 计时 / 连接只在用户点「▶ 启动」后才发生
    // connectWs();
});

})();
