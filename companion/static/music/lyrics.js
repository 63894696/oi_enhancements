// M3.29.3 桌面歌词前端
// - 收 ws /ws/lyrics → 渲染活动行 + 推 CSS var(字号/颜色/背景)
// - 整窗可拖动(Tauri 透明窗 decorations:false → CSS -webkit-app-region: drag)
// - agent-only cfg: 不暴露任何 UI 控件
//
// 兼容两种模式:
//   A) 真 Tauri 透明窗(-webkit-app-region: drag 生效)
//   B) 普通浏览器(纯渲染验证用)— 此模式下拖动条只是装饰,不影响显示

(function () {
    "use strict";

    const lyricsEl = document.getElementById("lyrics");
    const trackTitleEl = document.getElementById("track-title");
    const trackArtistEl = document.getElementById("track-artist");
    const connTagEl = document.getElementById("conn-tag");

    const state = {
        lines: [],          // [{time_ms, text, words?}]
        idx: -1,
        track: null,
        cfg: {
            color: "#f6f1e7",
            font_size: 32,
            mode: "line",     // line | word
            delay_ms: 0,
            opacity: 0.85,
            window_visible: true,
        },
    };

    function setVar(name, val) {
        document.documentElement.style.setProperty(name, val);
    }

    function applyCfg(cfg) {
        if (cfg.color) { state.cfg.color = cfg.color; setVar("--lyrics-color", cfg.color); }
        if (cfg.font_size) { state.cfg.font_size = cfg.font_size; setVar("--lyrics-font-size", cfg.font_size + "px"); }
        if (cfg.opacity !== undefined) { state.cfg.opacity = cfg.opacity; setVar("--lyrics-opacity", String(cfg.opacity)); }
        if (cfg.mode) { state.cfg.mode = cfg.mode; }
        if (cfg.delay_ms !== undefined) { state.cfg.delay_ms = cfg.delay_ms; }
    }

    function renderLines(lines, currentIdx) {
        state.lines = lines || [];
        state.idx = currentIdx ?? -1;
        if (state.lines.length === 0) {
            lyricsEl.innerHTML = '<div class="empty">(当前曲目暂无歌词)</div>';
            return;
        }
        // 渲染
        const html = state.lines.map((l, i) => {
            let cls = "line";
            if (i === state.idx) cls += " active";
            else if (i === state.idx - 1) cls += " prev";
            else if (i === state.idx + 1) cls += " next";
            const text = (l.text || "").replace(/[<>&"]/g, c => ({"<":"&lt;",">":"&gt;","&":"&amp;",'"':"&quot;"}[c]));
            return `<div class="${cls}">${text}</div>`;
        }).join("");
        lyricsEl.innerHTML = html;
        // 滚动 active 到可视区中心
        const actEl = lyricsEl.querySelector(".line.active");
        if (actEl) {
            actEl.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }

    function updateTrack(track) {
        state.track = track;
        trackTitleEl.textContent = track && track.title || "—";
        trackArtistEl.textContent = track && track.artist || "—";
    }

    // ============================================================
    // ws /ws/lyrics
    // ============================================================
    let ws = null;
    let reconnectTimer = null;
    function connect() {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const url = `${proto}://${location.host}/ws/lyrics`;
        try {
            ws = new WebSocket(url);
        } catch (e) {
            connTagEl.textContent = "❌";
            scheduleReconnect();
            return;
        }
        ws.onopen = () => {
            connTagEl.textContent = "🟢";
        };
        ws.onmessage = (m) => {
            try {
                const ev = JSON.parse(m.data);
                onLyricEvent(ev);
            } catch (e) { console.warn("[lyrics] parse", e); }
        };
        ws.onclose = () => {
            connTagEl.textContent = "🔴";
            scheduleReconnect();
        };
        ws.onerror = () => {
            connTagEl.textContent = "❌";
            try { ws.close(); } catch (e) {}
        };
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connect();
        }, 2000);
    }

    function onLyricEvent(ev) {
        if (ev.type === "lyric_cfg") {
            applyCfg(ev);
        } else if (ev.type === "lyric_line") {
            renderLines(ev.lines || [], ev.current_idx ?? -1);
        } else if (ev.type === "music_state" || ev.type === "music_progress") {
            updateTrack(ev.track || null);
            // 用 progress 推底部进度条
            if (ev.progress && ev.duration && ev.duration > 0) {
                const pct = Math.max(0, Math.min(100, (ev.progress / ev.duration) * 100));
                setVar("--progress", pct.toFixed(2) + "%");
            }
        } else if (ev.type === "heartbeat") {
            // ignore
        }
    }

    // ============================================================
    // 启动
    // ============================================================
    connect();

    // 拉初始 cfg + state(走 REST)
    fetch("/api/agent/cfg/list").then(r => r.json()).then(j => {
        if (j.ok && j.items) {
            const m = {};
            j.items.forEach(it => { m[it.path] = it.value; });
            applyCfg({
                color: m["lyrics.color"],
                font_size: m["lyrics.font_size"],
                mode: m["lyrics.mode"],
                delay_ms: m["lyrics.delay_ms"],
                opacity: m["lyrics.opacity"],
            });
        }
    }).catch(() => {});
    fetch("/api/state").then(r => r.json()).then(j => {
        if (j.ok && j.state) updateTrack(j.state.track);
    }).catch(() => {});
})();
