// PrisirAI 音乐 web 前端 — 国画主题
// - HTMLAudioElement 直连 /api/stream/<track_id>
// - ws /ws/state 推 player 状态
// - ws /ws/lyrics 推 lyric_line + lyric_cfg
// - 控制条: ⏮ ▶/⏸ ⏭ ⏹

// P2.5+22(2026-10-03):轻量 toast(队列空提示等)。2s 自动消失,顶部居中浮层。
function showMusicToast(msg) {
    let host = document.getElementById('music-toast-host');
    if (!host) {
        host = document.createElement('div');
        host.id = 'music-toast-host';
        host.style.cssText = 'position:fixed;top:24px;left:50%;transform:translateX(-50%);z-index:9999;pointer-events:none;';
        document.body.appendChild(host);
    }
    const t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText = 'background:rgba(60,40,20,0.92);color:#f6f1e7;padding:10px 18px;border-radius:6px;' +
        'font-size:14px;margin-top:6px;box-shadow:0 2px 8px rgba(0,0,0,0.2);opacity:0;transition:opacity 0.2s;';
    host.appendChild(t);
    requestAnimationFrame(() => { t.style.opacity = '1'; });
    setTimeout(() => {
        t.style.opacity = '0';
        setTimeout(() => t.remove(), 250);
    }, 2000);
}
// - 折叠歌词区(点击 lyric-head 折叠/展开)
// - 队列点击切歌

(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);

    const els = {
        searchInput: $("search-input"),
        searchBtn: $("search-btn"),
        lxDot: $("lx-dot"),
        lxText: $("lx-text"),
        queueList: $("queue-list"),
        cover: document.querySelector(".cover"),
        curTitle: $("cur-title"),
        curArtist: $("cur-artist"),
        progress: $("progress"),
        progressTime: $("progress-time"),
        progressDur: $("progress-duration"),
        prevBtn: $("prev-btn"),
        playBtn: $("play-btn"),
        nextBtn: $("next-btn"),
        stopBtn: $("stop-btn"),
        audio: $("audio"),
        lyricPane: $("lyric-pane"),
        lyricHead: $("lyric-toggle"),
        lyricBody: $("lyric-body"),
        lyricTitle: $("lyric-title"),
        lyricModeTag: $("lyric-mode-tag"),
        lyricToggleIcon: $("lyric-toggle-icon"),
    };

    const state = {
        playing: false,
        track: null,
        progress: 0,
        duration: 0,
        queue: [],
        cursor: -1,
        lyricLines: [],
        lyricIdx: -1,
    };

    function fmtTime(s) {
        if (!s || isNaN(s)) return "0:00";
        const m = Math.floor(s / 60);
        const ss = Math.floor(s % 60);
        return `${m}:${ss.toString().padStart(2, "0")}`;
    }

    async function api(path, opts = {}) {
        const r = await fetch(path, {
            method: opts.method || "GET",
            headers: opts.body ? { "Content-Type": "application/json" } : {},
            body: opts.body ? JSON.stringify(opts.body) : undefined,
        });
        return await r.json();
    }

    // ============================================================
    // ws state
    // ============================================================
    let wsState = null;
    function connectWsState() {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        wsState = new WebSocket(`${proto}://${location.host}/ws/state`);
        wsState.onmessage = (m) => {
            try {
                const ev = JSON.parse(m.data);
                onStateEvent(ev);
            } catch (e) { console.warn("[ws/state]", e); }
        };
        wsState.onclose = () => {
            console.log("[ws/state] closed, reconnect in 2s");
            setTimeout(connectWsState, 2000);
        };
        wsState.onerror = () => wsState.close();
    }

    function onStateEvent(ev) {
        if (ev.type === "music_state" || ev.type === "music_progress") {
            state.playing = (ev.status === "playing");
            state.track = ev.track || null;
            state.progress = ev.progress || 0;
            state.duration = ev.duration || 0;
            renderNow();
            updatePlayBtn();
            // queue update 仅在 music_state 时
            if (ev.type === "music_state") {
                loadQueue();
            }
        } else if (ev.type === "heartbeat") {
            // ignore
        }
    }

    function renderNow() {
        if (state.track) {
            els.curTitle.textContent = state.track.title || "—";
            els.curArtist.textContent = state.track.artist || "—";
        } else {
            els.curTitle.textContent = "未播放";
            els.curArtist.textContent = "—";
        }
        if (state.duration > 0) {
            els.progress.value = (state.progress / state.duration) * 100;
            els.progressTime.textContent = fmtTime(state.progress);
            els.progressDur.textContent = fmtTime(state.duration);
        }
    }

    function updatePlayBtn() {
        els.playBtn.textContent = state.playing ? "⏸" : "▶";
    }

    // ============================================================
    // queue
    // ============================================================
    async function loadQueue() {
        const r = await api("/api/queue");
        if (!r.ok) return;
        state.queue = r.queue || [];
        state.cursor = r.cursor ?? -1;
        renderQueue();
    }

    function renderQueue() {
        els.queueList.innerHTML = "";
        state.queue.forEach((t, i) => {
            const li = document.createElement("li");
            if (i === state.cursor) li.className = "cursor";
            li.innerHTML = `<span class="t">${escapeHtml(t.title || "(无题)")}</span><span class="a">${escapeHtml(t.artist || "")}</span>`;
            li.onclick = () => playTrack(t.id);
            els.queueList.appendChild(li);
        });
    }

    function escapeHtml(s) {
        return (s || "").replace(/[<>&"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));
    }

    // ============================================================
    // audio
    // ============================================================
    async function playTrack(trackId) {
        const r = await api("/api/cmd", { method: "POST", body: { action: "play", track_id: trackId } });
        if (r.ok && r.state && r.state.track) {
            const t = r.state.track;
            els.audio.src = `/api/stream/${encodeURIComponent(t.id)}`;
            els.audio.volume = (r.state.volume || 80) / 100;
            try { await els.audio.play(); } catch (e) { console.warn("audio.play", e); }
        }
    }

    function audioProgress() {
        if (els.audio.duration && !isNaN(els.audio.duration)) {
            state.duration = els.audio.duration;
            state.progress = els.audio.currentTime;
            els.progress.value = (state.progress / state.duration) * 100;
            els.progressTime.textContent = fmtTime(state.progress);
            els.progressDur.textContent = fmtTime(state.duration);
        }
    }

    async function audioEnded() {
        // 推 next 到后端
        await api("/api/cmd", { method: "POST", body: { action: "next" } });
    }

    // ============================================================
    // control
    // ============================================================
    els.playBtn.onclick = async () => {
        if (!state.track) {
            // 没曲 — 播放队列第 1 首
            if (state.queue.length > 0) {
                await playTrack(state.queue[0].id);
            } else {
                // P2.5+22(2026-10-03):队列空时给提示,不要静默 return 让用户以为卡了。
                // 走轻量 toast,2s 自动消失,顶部提示搜索关键词再选曲。
                showMusicToast("队列为空,先搜索一首曲加入队列再播放");
            }
            return;
        }
        if (els.audio.paused) {
            try { await els.audio.play(); } catch (e) {}
            await api("/api/cmd", { method: "POST", body: { action: "resume" } });
        } else {
            els.audio.pause();
            await api("/api/cmd", { method: "POST", body: { action: "pause" } });
        }
    };

    els.prevBtn.onclick = async () => {
        await api("/api/cmd", { method: "POST", body: { action: "prev" } });
    };

    els.nextBtn.onclick = async () => {
        await api("/api/cmd", { method: "POST", body: { action: "next" } });
    };

    els.stopBtn.onclick = async () => {
        els.audio.pause();
        els.audio.currentTime = 0;
        await api("/api/cmd", { method: "POST", body: { action: "stop" } });
    };

    els.progress.parentElement.onclick = (e) => {
        const rect = els.progress.getBoundingClientRect();
        const pct = (e.clientX - rect.left) / rect.width;
        if (state.duration > 0) {
            const target = pct * state.duration;
            els.audio.currentTime = target;
            api("/api/cmd", { method: "POST", body: { action: "seek", offset: target } });
        }
    };

    // ============================================================
    // search
    // ============================================================
    els.searchBtn.onclick = async () => {
        const q = els.searchInput.value.trim();
        if (!q) return;
        const r = await api(`/api/library/search?q=${encodeURIComponent(q)}`);
        if (r.ok && r.tracks && r.tracks.length > 0) {
            // 自动播第一首(本地库 search 已 set_queue)
            const sr = await api("/api/cmd", { method: "POST", body: { action: "search", query: q } });
            if (sr.ok && sr.queued > 0) {
                // 再 play 第一首
                const first = r.tracks[0].id;
                await playTrack(first);
            }
        }
    };

    // ============================================================
    // ws lyrics
    // ============================================================
    let wsLyrics = null;
    function connectWsLyrics() {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        wsLyrics = new WebSocket(`${proto}://${location.host}/ws/lyrics`);
        wsLyrics.onmessage = (m) => {
            try {
                const ev = JSON.parse(m.data);
                onLyricEvent(ev);
            } catch (e) { console.warn("[ws/lyrics]", e); }
        };
        wsLyrics.onclose = () => setTimeout(connectWsLyrics, 2000);
        wsLyrics.onerror = () => wsLyrics.close();
    }

    function onLyricEvent(ev) {
        if (ev.type === "lyric_cfg") {
            document.documentElement.style.setProperty("--lyrics-color", ev.color || "#f6f1e7");
            document.documentElement.style.setProperty("--lyrics-font-size", (ev.font_size || 32) + "px");
            els.lyricModeTag.textContent = ev.mode === "word" ? "逐字" : "逐行";
        } else if (ev.type === "lyric_line") {
            state.lyricLines = ev.lines || [];
            state.lyricIdx = ev.current_idx ?? -1;
            renderLyric();
        }
    }

    function renderLyric() {
        if (!state.lyricLines || state.lyricLines.length === 0) {
            els.lyricBody.innerHTML = '<div class="empty">(无歌词)</div>';
            return;
        }
        // 增量渲染:简单做法是全部重建
        let html = "";
        state.lyricLines.forEach((l, i) => {
            const active = i === state.lyricIdx ? "active" : "";
            html += `<div class="line ${active}">${escapeHtml(l.text)}</div>`;
        });
        els.lyricBody.innerHTML = html;
        // 滚到 active
        const actEl = els.lyricBody.querySelector(".line.active");
        if (actEl) {
            actEl.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }

    // audio 推进 + 推 progress 到后端 + 更新歌词 index
    els.audio.addEventListener("timeupdate", () => {
        audioProgress();
        // 推进 lyric(本地驱动 — 后端 lyric 也推,但本地更准)
        if (state.lyricLines.length > 0) {
            const ms = els.audio.currentTime * 1000;
            // 二分找最大 time_ms <= ms 的行
            let idx = -1;
            for (let i = 0; i < state.lyricLines.length; i++) {
                if (state.lyricLines[i].time_ms <= ms) idx = i;
                else break;
            }
            if (idx !== state.lyricIdx) {
                state.lyricIdx = idx;
                renderLyric();
            }
        }
    });

    els.audio.addEventListener("ended", audioEnded);
    els.audio.addEventListener("loadedmetadata", audioProgress);

    // 折叠歌词
    els.lyricHead.onclick = () => {
        const collapsed = els.lyricPane.dataset.collapsed === "true";
        els.lyricPane.dataset.collapsed = collapsed ? "false" : "true";
        els.lyricToggleIcon.textContent = collapsed ? "▾" : "▸";
    };

    // 初始化
    connectWsState();
    connectWsLyrics();
    loadQueue();
    api("/api/state").then((r) => {
        if (r.ok && r.state) {
            state.track = r.state.track || null;
            state.playing = r.state.status === "playing";
            state.progress = r.state.progress || 0;
            state.duration = r.state.duration || 0;
            els.audio.volume = (r.state.volume || 80) / 100;
            renderNow();
            updatePlayBtn();
        }
    });
})();
