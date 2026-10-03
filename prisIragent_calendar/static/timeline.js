/* prisIr_calendar/static/timeline.js
 * 只读时间线 — vanilla JS, 不引 React/Vue.
 *
 * 三个 fetch URL:
 *   GET  /prisIragent/api/calendar/timeline   → TodayView JSON
 *   POST /prisIragent/api/calendar/dismiss    → dismiss 单事件
 *   POST /prisIragent/api/calendar/scan       → 触发 scan_and_protect
 *   GET  /prisIragent/api/calendar/export.ics → ICS 文件下载
 *
 * 渲染策略:
 *   - 30 天按日期倒序分组 (today 起 → +29 天)
 *   - 事件卡片左时间 / 中标题+venue / 右 [×]
 *   - buffer 卡片左时间 / 中 "→ {dest}" + 元数据 / 右 [×]
 *   - skip 卡片显示灰色删除线
 *   - 同一天相邻 in-person 之间画一条淡色 chain-link
 *
 * 没有"添加事件"/"设置"按钮(产品边界)。
 */
(function () {
  'use strict';

  var API_BASE = '/prisIragent/api/calendar';
  var PAGE_BASE = '/prisIragent/calendar';

  // ---- DOM 引用 ----
  var dayGroupsEl = document.getElementById('day-groups');
  var emptyEl = document.getElementById('empty-state');
  var statusEl = document.getElementById('status-line');
  var btnRefresh = document.getElementById('btn-refresh');
  var btnExport = document.getElementById('btn-export');
  var btnAiHistory = document.getElementById('btn-ai-history');
  var btnAiClear = document.getElementById('btn-ai-clear');
  var aiHistoryPanel = document.getElementById('ai-history-panel');
  var aiHistoryList = document.getElementById('ai-history-list');

  // ---- 工具 ----
  function pad2(n) { return n < 10 ? '0' + n : '' + n; }

  function fmtTime(iso) {
    if (!iso) return '--:--';
    // 形如 2026-10-01T09:00:00+00:00 — 取 09:00
    var m = /T(\d{2}:\d{2})/.exec(iso);
    return m ? m[1] : iso;
  }

  function fmtDateCN(iso) {
    // 转本地日期标签 — 仅取 YYYY-MM-DD 部分
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    if (!m) return iso;
    return m[1] + '-' + m[2] + '-' + m[3];
  }

  function weekdayCN(date) {
    // 简单 weekday — 用本地 Date
    var names = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
    return names[date.getDay()];
  }

  function durationMin(startIso, endIso) {
    var s = Date.parse(startIso);
    var e = Date.parse(endIso);
    if (isNaN(s) || isNaN(e)) return 0;
    return Math.max(0, Math.round((e - s) / 60000));
  }

  function setStatus(text, isErr) {
    statusEl.textContent = text || '';
    statusEl.className = 'status-line' + (isErr ? ' error' : '');
  }

  function esc(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ---- 渲染 ----
  function renderTimeline(view) {
    dayGroupsEl.innerHTML = '';
    var events = (view && view.events) || [];
    var buffersByEvent = (view && view.buffers_by_event) || {};
    var chainLinks = (view && view.chain_links) || [];

    if (!events.length) {
      emptyEl.hidden = false;
      setStatus('共 0 个事件 · ' + (view.scope_start || '') + ' → ' + (view.scope_end || ''));
      return;
    }
    emptyEl.hidden = true;

    // 按日期 YYYY-MM-DD 分组
    var groups = {};
    var groupOrder = [];
    events.forEach(function (ev) {
      var d = fmtDateCN(ev.dtstart_utc);
      if (!groups[d]) {
        groups[d] = { date: d, items: [] };
        groupOrder.push(d);
      }
      groups[d].items.push(ev);
    });

    // 倒序展示 (今天优先)
    groupOrder.sort().reverse().forEach(function (d) {
      var g = groups[d];
      var groupEl = renderDayGroup(g);
      dayGroupsEl.appendChild(groupEl);
    });

    setStatus('共 ' + events.length + ' 个事件 · ' + groupOrder.length + ' 天');
  }

  function renderDayGroup(group) {
    var wrap = document.createElement('section');
    wrap.className = 'day-group';

    // header
    var hdr = document.createElement('div');
    hdr.className = 'day-header';
    var parts = group.date.split('-');
    var dt = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    hdr.innerHTML =
      '<span class="day-date">' + esc(group.date) + '</span>' +
      '<span class="day-weekday">' + weekdayCN(dt) + '</span>' +
      '<span class="day-count">' + group.items.length + ' 项</span>';
    wrap.appendChild(hdr);

    // items — 同一 group 内,相邻 in-person 事件之间画 chain-link
    var prevInPersonId = null;
    group.items.forEach(function (ev) {
      if (prevInPersonId && isInPerson(ev)) {
        var link = document.createElement('div');
        link.className = 'chain-link';
        wrap.appendChild(link);
      }
      prevInPersonId = isInPerson(ev) ? ev.event_id : prevInPersonId;

      // event card
      wrap.appendChild(renderEventCard(ev));

      // 关联 buffer cards (在 event card 之后)
      var buffers = buffersByEvent[ev.event_id] || [];
      buffers.forEach(function (b) {
        wrap.appendChild(renderBufferCard(ev, b));
      });
    });

    return wrap;
  }

  function isInPerson(ev) {
    // 简判:有 venue 且 source=user → in-person
    return ev && ev.source !== 'agent' && ev.venue && ev.venue.normalized;
  }

  function renderEventCard(ev) {
    var card = document.createElement('div');
    card.className = 'card event';
    card.dataset.eventId = ev.event_id;

    var startT = fmtTime(ev.dtstart_utc);
    var endT = fmtTime(ev.dtend_utc);
    var dur = durationMin(ev.dtstart_utc, ev.dtend_utc);

    var venueTxt = '';
    if (ev.venue && ev.venue.normalized) {
      venueTxt = '@ ' + ev.venue.normalized;
    }

    // skip reason? (event.venue == null && source='agent' → skip 形态;不过我们用单独渲染)
    // 走 source 判断:source='agent' + 无 summary → 不该出现在 events 列表
    var metaHtml = '';
    if (venueTxt) metaHtml += '<span class="meta-pill">' + esc(venueTxt) + '</span>';
    if (ev.source && ev.source !== 'user') {
      metaHtml += '<span class="meta-pill">' + esc(ev.source) + '</span>';
    }

    card.innerHTML =
      '<span class="bar"></span>' +
      '<div class="card-time">' +
        esc(startT) + '–' + esc(endT) +
        '<span class="dur">' + dur + ' min</span>' +
      '</div>' +
      '<div class="card-body">' +
        '<div class="card-title">' + esc(ev.summary || '(无标题)') + '</div>' +
        (metaHtml ? '<div class="card-meta">' + metaHtml + '</div>' : '') +
      '</div>' +
      '<button class="card-x" type="button" title="不喜欢这条,agent 会记住" aria-label="dismiss">×</button>';

    var xBtn = card.querySelector('.card-x');
    xBtn.addEventListener('click', function () {
      onDismiss(card, ev.event_id);
    });

    return card;
  }

  function renderBufferCard(parentEv, buf) {
    var card = document.createElement('div');
    card.className = 'card buffer';
    card.dataset.bufferId = buf.buffer_id;

    var startT = fmtTime(buf.inserted_before);
    var endT = fmtTime(buf.inserted_after);
    var dur = buf.duration_min || durationMin(buf.inserted_before, buf.inserted_after);

    var meta = buf.metadata || {};
    var mode = meta.mode || '';
    var vendor = meta.vendor || '';
    var origin = meta.origin || '';
    var dest = meta.destination || (parentEv.venue && parentEv.venue.normalized) || '';

    var arrowTxt = '→ ' + (dest || parentEv.summary || '目的地');
    var metaHtml = '';
    if (mode) metaHtml += '<span class="meta-pill">' + esc(mode) + '</span>';
    if (vendor) metaHtml += '<span class="meta-pill">' + esc(vendor) + '</span>';
    if (origin) metaHtml += '<span class="meta-pill">' + esc(origin) + ' 起</span>';

    card.innerHTML =
      '<span class="bar"></span>' +
      '<div class="card-time">' +
        esc(startT) + '–' + esc(endT) +
        '<span class="dur">' + dur + ' min</span>' +
      '</div>' +
      '<div class="card-body">' +
        '<div class="card-title">' + esc(arrowTxt) + '</div>' +
        (metaHtml ? '<div class="card-meta">' + metaHtml + '</div>' : '') +
      '</div>' +
      '<button class="card-x" type="button" title="不喜欢这条交通时间,agent 会记住" aria-label="dismiss">×</button>';

    var xBtn = card.querySelector('.card-x');
    xBtn.addEventListener('click', function () {
      onDismissBuffer(card, parentEv.event_id, buf.buffer_id);
    });

    return card;
  }

  function renderSkipNote(text) {
    // 当前 reader.get_today_view 不会产出独立的 skip 卡片;skip 原因
    // 体现在 events 里 venue=null / 没 chain 等场景。这里保留渲染通道
    // 以备 reader 后续扩展 — 暂不直接用。
    var card = document.createElement('div');
    card.className = 'card skip';
    card.innerHTML =
      '<span class="bar"></span>' +
      '<div class="card-time">—</div>' +
      '<div class="card-body">' +
        '<div class="card-title">' + esc(text) + '</div>' +
      '</div>' +
      '<button class="card-x" type="button" aria-label="dismiss">×</button>';
    return card;
  }

  // ---- 交互 ----
  function onDismiss(cardEl, eventId) {
    cardEl.classList.add('dismissed');
    fetch(API_BASE + '/dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_id: eventId, reason: 'user_clicked_x' })
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (resp) {
        if (!resp.ok || !resp.j.ok) {
          cardEl.classList.remove('dismissed');
          setStatus('dismiss 失败:' + (resp.j && resp.j.error || '未知错误'), true);
        } else {
          setStatus('已 dismiss ' + eventId.substring(0, 8));
        }
      })
      .catch(function (e) {
        cardEl.classList.remove('dismissed');
        setStatus('网络错误:' + e.message, true);
      });
  }

  function onDismissBuffer(cardEl, eventId, bufferId) {
    cardEl.classList.add('dismissed');
    fetch(API_BASE + '/dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        event_id: eventId,
        buffer_id: bufferId,
        reason: 'user_clicked_x'
      })
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (resp) {
        if (!resp.ok || !resp.j.ok) {
          cardEl.classList.remove('dismissed');
          setStatus('dismiss 失败:' + (resp.j && resp.j.error || '未知错误'), true);
        } else {
          setStatus('已 dismiss buffer ' + bufferId.substring(0, 8));
        }
      })
      .catch(function (e) {
        cardEl.classList.remove('dismissed');
        setStatus('网络错误:' + e.message, true);
      });
  }

  function onRefresh() {
    btnRefresh.disabled = true;
    setStatus('刷新中…');

    // 先触发 scan_and_protect,再 fetch timeline
    fetch(API_BASE + '/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ days: 30 })
    })
      .then(function (r) { return r.json(); })
      .then(function (scanReport) {
        var msg = scanReport.inserted_count + ' inserted · ' +
                  scanReport.skipped_count + ' skipped · ' +
                  scanReport.error_count + ' errors';
        return fetch(API_BASE + '/timeline?days=30')
          .then(function (r2) { return r2.json(); })
          .then(function (view) {
            renderTimeline(view);
            setStatus('已刷新 · scan: ' + msg);
            btnRefresh.disabled = false;
          });
      })
      .catch(function (e) {
        setStatus('刷新失败:' + e.message, true);
        btnRefresh.disabled = false;
      });
  }

  function onExport() {
    // ICS 走 window.location 触发下载 — 不引 fetch+blob,简单稳定
    window.location.href = API_BASE + '/export.ics';
    setStatus('已请求导出 ICS…');
  }

  // ---- 启动 ----
  btnRefresh.addEventListener('click', onRefresh);
  btnExport.addEventListener('click', onExport);

  // P2.5+8(2026-09-20):AI 自动编排历史 + 一键清除
  function onAiHistory() {
    if (!aiHistoryPanel) return;
    if (!aiHistoryPanel.hidden) {
      aiHistoryPanel.hidden = true;
      return;
    }
    aiHistoryPanel.hidden = false;
    aiHistoryList.textContent = '加载中…';
    fetch(API_BASE + '/schedule/history')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) {
          aiHistoryList.textContent = '加载失败';
          return;
        }
        var rows = d.history || [];
        var enabledTxt = d.enabled ? '✅ 已批准'
          : (d.consent_required ? '⚠ 待批准' : '?');
        var html = '<div style="margin:6px 0;color:#5b6a61;font-size:12px;">状态:'
          + enabledTxt + ' · 共 ' + (d.history_count || 0) + ' 条</div>';
        if (!rows.length) {
          html += '<div style="color:#8a968e;font-size:12px;">(暂无 AI 写入记录)</div>';
        } else {
          html += '<table style="width:100%;border-collapse:collapse;font-size:12px;">'
            + '<tr style="border-bottom:1px solid #d8cfbc;text-align:left;">'
            + '<th style="padding:4px;">时间</th><th>类型</th><th>数量</th></tr>';
          for (var i = rows.length - 1; i >= 0; i--) {
            var e = rows[i];
            var ts = e.ts ? new Date(e.ts * 1000).toLocaleString() : '-';
            var kind = e.kind || '?';
            var count = e.count != null ? e.count : (e.ids ? e.ids.length : '-');
            html += '<tr style="border-bottom:1px solid #efe8da;">'
              + '<td style="padding:4px;color:#5b6a61;">' + ts + '</td>'
              + '<td>' + kind + '</td>'
              + '<td>' + count + '</td></tr>';
          }
          html += '</table>';
        }
        aiHistoryList.innerHTML = html;
      })
      .catch(function () { aiHistoryList.textContent = '加载失败'; });
  }
  function onAiClear() {
    if (!confirm('确认清除 AI 主动编排的所有事件 + todo + 历史?\n'
        + '此操作不可撤销。')) return;
    fetch(API_BASE + '/schedule/history', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({clear: true})
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var msg = '已清除 ' + JSON.stringify(d && d.cleared || {});
        if (aiHistoryPanel && !aiHistoryPanel.hidden) onAiHistory();
        onRefresh();
        alert(msg);
      })
      .catch(function () { alert('清除失败'); });
  }
  if (btnAiHistory) btnAiHistory.addEventListener('click', onAiHistory);
  if (btnAiClear) btnAiClear.addEventListener('click', onAiClear);

  // 首次加载 timeline
  fetch(API_BASE + '/timeline?days=30')
    .then(function (r) { return r.json(); })
    .then(function (view) {
      renderTimeline(view);
    })
    .catch(function (e) {
      setStatus('加载失败:' + e.message + ' (点击「刷新」重试)', true);
    });

  // 暴露给测试用的 hook(可空)
  window.__prisIr_calendar_timeline = {
    renderTimeline: renderTimeline,
    onRefresh: onRefresh,
    onExport: onExport
  };
})();