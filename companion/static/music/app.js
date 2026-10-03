// PrisirAI 音乐 web 前端 — 国画主题
// - HTMLAudioElement 直连 /api/stream/<track_id>
// - ws /ws/state 推 player 状态
// - ws /ws/lyrics 推 lyric_line + lyric_cfg
// - 控制条: ⏮ ▶/⏸ ⏭(右下方 footer)
// - 左 + 中 2/3 歌单网格:用户填 CSV → startup 随机 60 首
// - 收藏 ♥ / 下载 ⬇ 按钮(歌词栏目下方)

// P2.5+22(2026-10-03):轻量 toast。2s 自动消失,顶部居中浮层。
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

// P2.5+23(2026-10-03):歌名首字 + 哈希渐变作封面占位(避免 iTunes Search API 429)。
function coverGradient(title) {
    const s = (title || "?").trim();
    const ch = s.charAt(0).toUpperCase() || "?";
    let h = 0;
    for (let i = 0; i < s.length; i++) {
        h = (h * 31 + s.charCodeAt(i)) >>> 0;
    }
    const h1 = h % 360;
    const h2 = (h1 + 40) % 360;
    return {
        bg: `linear-gradient(135deg, hsl(${h1}deg 35% 45%), hsl(${h2}deg 45% 35%))`,
        text: ch,
    };
}

function escapeHtml(s) {
    return (s || "").replace(/[<>&"]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));
}

(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);

    const els = {
        lxDot: $("lx-dot"),
        lxText: $("lx-text"),
        songGrid: $("song-grid"),
        gridCount: $("grid-count"),
        tagFilter: $("tag-filter"),
        respinBtn: $("respin-btn"),
        favoriteBtn: $("favorite-btn"),
        downloadBtn: $("download-btn"),
        progress: $("progress"),
        progressTime: $("progress-time"),
        progressDur: $("progress-duration"),
        prevBtn: $("prev-btn"),
        playBtn: $("play-btn"),
        nextBtn: $("next-btn"),
        nowTitle: $("now-title"),
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
        // 当前网格(60 首)
        songs: [],
        tags: [],
        // 当前过滤
        currentTag: "",
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
    // 歌名池(song pool)
    // ============================================================
    async function loadSongs(tag = "") {
        const url = tag ? `/api/songs?tag=${encodeURIComponent(tag)}` : "/api/songs";
        const r = await api(url);
        if (!r.ok) {
            showMusicToast(r.err || "歌单加载失败");
            state.songs = [];
            state.tags = state.tags || [];
            renderSongGrid();
            return;
        }
        state.songs = r.songs || [];
        if (!tag) state.tags = r.tags || [];
        if (!tag) renderTagFilter(state.tags);
        renderSongGrid();
    }

    function renderTagFilter(tags) {
        if (!els.tagFilter) return;
        const cur = els.tagFilter.value || "";
        els.tagFilter.innerHTML = "";
        const optAll = document.createElement("option");
        optAll.value = "";
        optAll.textContent = `全部分类 (${state.songs.length || ""})`;
        els.tagFilter.appendChild(optAll);
        (tags || []).forEach((t) => {
            const o = document.createElement("option");
            o.value = t;
            o.textContent = t;
            els.tagFilter.appendChild(o);
        });
        // 恢复当前值
        if (cur && (tags || []).includes(cur)) {
            els.tagFilter.value = cur;
        }
    }

    function renderSongGrid() {
        if (!els.songGrid) return;
        els.songGrid.innerHTML = "";
        const songs = state.songs;
        if (!songs.length) {
            els.songGrid.innerHTML = '<div class="empty-grid">(当前标签下没有歌曲)</div>';
            els.gridCount.textContent = "0 首";
            return;
        }
        els.gridCount.textContent = `${songs.length} 首${state.currentTag ? " · " + state.currentTag : ""}`;
        songs.forEach((song) => {
            const card = document.createElement("div");
            card.className = "song-card";
            card.dataset.id = song.id;
            const cv = coverGradient(song.title);
            card.innerHTML = `
                <div class="cover-tile" style="background:${cv.bg};">
                    <span class="cover-char">${escapeHtml(cv.text)}</span>
                </div>
                <div class="card-title">${escapeHtml(song.title)}</div>
                <div class="card-artist">${escapeHtml(song.artist || "—")}</div>
                ${song.tag ? `<span class="card-tag">${escapeHtml(song.tag)}</span>` : ""}
            `;
            card.onclick = () => onSongClick(song);
            els.songGrid.appendChild(card);
        });
    }

    async function onSongClick(song) {
        // P2.5+23:点歌 → 调 mock.js musicUrl 拿 url → seed_from_url → 自动 play。
        // song_info 必须给 hash/songmid/歌名,即使 mock.js 不真用也能保接口兼容。
        const songInfo = {
            hash: song.id,
            songmid: song.id,
            songname: song.title,
            title: song.title,
            artist: song.artist,
        };
        const r = await api("/api/cmd", {
            method: "POST",
            body: { action: "play_url", song_info: songInfo, title: song.title, artist: song.artist },
        });
        if (!r.ok) {
            showMusicToast(`播放失败: ${r.err || "?"}`);
            return;
        }
        const t = r.state && r.state.track;
        if (!t) {
            showMusicToast("播放失败: 后端未返曲目");
            return;
        }
        state.track = t;
        els.audio.src = `/api/stream/${encodeURIComponent(t.id)}`;
        els.audio.volume = ((r.state && r.state.volume) || 80) / 100;
        try { await els.audio.play(); } catch (e) { console.warn("audio.play", e); }
        showMusicToast(`正在播放: ${song.title} — ${song.artist || "?"}`);
        enableTrackActions();
    }

    function enableTrackActions() {
        if (els.favoriteBtn) els.favoriteBtn.disabled = false;
        if (els.downloadBtn) els.downloadBtn.disabled = false;
    }

    function disableTrackActions() {
        if (els.favoriteBtn) els.favoriteBtn.disabled = true;
        if (els.downloadBtn) els.downloadBtn.disabled = true;
    }

    // ============================================================
    // 自动衔接(播完 → 随机下一首)
    // ============================================================
    async function fetchRandomNext() {
        const songs = state.songs;
        if (!songs.length) return;
        const cur = state.track && state.track.id;
        // 随机选一首非当前播放的
        let pool = songs.filter((s) => s.id !== cur);
        if (!pool.length) pool = songs;
        const pick = pool[Math.floor(Math.random() * pool.length)];
        await onSongClick(pick);
    }

    async function audioEnded() {
        await fetchRandomNext();
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

    function updatePlayBtn() {
        els.playBtn.textContent = state.playing ? "⏸" : "▶";
    }

    function renderNow() {
        if (state.track) {
            els.nowTitle.textContent = `${state.track.title || "—"} — ${state.track.artist || "—"}`;
        } else {
            els.nowTitle.textContent = "未播放";
        }
        if (state.duration > 0) {
            els.progress.value = (state.progress / state.duration) * 100;
            els.progressTime.textContent = fmtTime(state.progress);
            els.progressDur.textContent = fmtTime(state.duration);
        }
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
            if (state.track) enableTrackActions();
            else disableTrackActions();
            renderNow();
            updatePlayBtn();
        }
    }

    // ============================================================
    // control(footer)
    // ============================================================
    els.playBtn.onclick = async () => {
        if (!state.track) {
            // 没曲 — 随机播一首网格里的
            if (state.songs.length > 0) {
                await fetchRandomNext();
                return;
            }
            // 池也空 — fallback 到后端 random
            const r = await api("/api/cmd", {
                method: "POST",
                body: { action: "random", count: 1 },
            });
            if (r.ok && r.state && r.state.track) {
                const t = r.state.track;
                els.audio.src = `/api/stream/${encodeURIComponent(t.id)}`;
                els.audio.volume = (r.state.volume || 80) / 100;
                try { await els.audio.play(); } catch (e) {}
                showMusicToast(`随机播放: ${t.title || t.id}`);
                enableTrackActions();
                return;
            }
            showMusicToast("歌单为空,请先点击右上 🔄 换一批");
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
    // tag filter + respin
    // ============================================================
    els.tagFilter.onchange = async () => {
        state.currentTag = els.tagFilter.value || "";
        await loadSongs(state.currentTag);
    };

    els.respinBtn.onclick = async () => {
        // 重洗 + 立即重拉当前 tag
        const r = await api("/api/songs/respin", { method: "POST" });
        if (!r.ok) {
            showMusicToast(`换一批失败: ${r.err || "?"}`);
            return;
        }
        await loadSongs(state.currentTag);
        showMusicToast("已换一批新歌");
    };

    // ============================================================
    // favorite / download
    // ============================================================
    els.favoriteBtn.onclick = async () => {
        if (!state.track) {
            showMusicToast("请先播放一首歌曲");
            return;
        }
        const r = await api("/api/cmd", {
            method: "POST",
            body: { action: "favorite", track_id: state.track.id },
        });
        if (r.ok) {
            showMusicToast(r.dedup ? `已在本地库: ${r.title}` : `已收藏: ${r.title}`);
        } else {
            showMusicToast(`收藏失败: ${r.err || "?"}`);
        }
    };

    els.downloadBtn.onclick = async () => {
        if (!state.track) {
            showMusicToast("请先播放一首歌曲");
            return;
        }
        showMusicToast("下载中…");
        const r = await api("/api/cmd", {
            method: "POST",
            body: { action: "download", track_id: state.track.id },
        });
        if (r.ok) {
            const sizeKB = r.size ? `(${(r.size / 1024).toFixed(1)}KB)` : "";
            showMusicToast(`已下载: ${r.title} ${sizeKB}`);
        } else {
            showMusicToast(`下载失败: ${r.err || "?"}`);
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
        let html = "";
        state.lyricLines.forEach((l, i) => {
            const active = i === state.lyricIdx ? "active" : "";
            html += `<div class="line ${active}">${escapeHtml(l.text)}</div>`;
        });
        els.lyricBody.innerHTML = html;
        const actEl = els.lyricBody.querySelector(".line.active");
        if (actEl) {
            actEl.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }

    // audio 推进 + lyric
    els.audio.addEventListener("timeupdate", () => {
        audioProgress();
        if (state.lyricLines.length > 0) {
            const ms = els.audio.currentTime * 1000;
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

    // ============================================================
    // 初始化
    // ============================================================
    disableTrackActions();
    connectWsState();
    connectWsLyrics();
    // 先拉歌单网格,再拉 state
    loadSongs().then(() => {
        api("/api/state").then((r) => {
            if (r.ok && r.state) {
                state.track = r.state.track || null;
                state.playing = r.state.status === "playing";
                state.progress = r.state.progress || 0;
                state.duration = r.state.duration || 0;
                els.audio.volume = (r.state.volume || 80) / 100;
                if (state.track) enableTrackActions();
                renderNow();
                updatePlayBtn();
            }
        });
    });
})();