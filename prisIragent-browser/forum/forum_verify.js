// forum_verify.js — 阅读侧三态渲染(诚实口径落实)
// 三态:✓ 已确认(签名有效 + confirmed ≥3) / ⏳ 未确认(签名有效但未被社区验签) / ✗ 验签失败或已撤下(折叠)
// 与契约 §3 一致:「未确认」≠「是假的」,是「到账延迟」的诚实标注,谨防冒名。

const FORUM_VERIFY = (() => {
  // state: "confirmed" | "pending" | "invalid" | "taken_down" | "retracted"
  function classify(frame) {
    if (frame.taken_down) return "taken_down";
    if (frame.retracted) return "retracted";
    return frame.confirmed ? "confirmed" : "pending";
  }

  const BADGE = {
    confirmed:  { icon: "✓", cls: "fv-ok",      label: "已验证" },
    pending:    { icon: "⏳", cls: "fv-pend",    label: "未确认 · 谨防冒名" },
    invalid:    { icon: "✗", cls: "fv-bad",     label: "验签失败" },
    taken_down: { icon: "🚫", cls: "fv-bad",    label: "已被运营撤下" },
    retracted:  { icon: "↩", cls: "fv-bad",    label: "作者已撤回" },
  };

  function badge(state) { return BADGE[state] || BADGE.invalid; }

  // ── body 渲染(F-3 富内容):纯文本分词 → 图片/视频卡片/链接/文字节点 ──
  // 原则不变:文字一律 textNode 防 XSS;图片/链接走安全 DOM 构造,永不 innerHTML。
  const IMG_TOKEN = /!\[[^\]]*\]\(data:image\/(?:webp|jpeg|png);base64,[A-Za-z0-9+/=]+\)/;
  const URL_TOKEN = /https?:\/\/[^\s<>"'】」\]]+/;
  const SPLIT_RE  = new RegExp("(" + IMG_TOKEN.source + "|" + URL_TOKEN.source + ")", "g");

  const VIDEO_DOMAINS = [
    "youtube.com", "youtu.be", "bilibili.com", "b23.tv", "vimeo.com",
    "dailymotion.com", "tiktok.com", "douyin.com", "ixigua.com",
    "twitch.tv", "niconico.jp", "acfun.cn",
  ];
  function isVideoHost(host) {
    host = host.toLowerCase();
    return VIDEO_DOMAINS.some(d => host === d || host.endsWith("." + d));
  }

  function appendBody(container, bodyText) {
    const parts = String(bodyText).split(SPLIT_RE);
    for (const part of parts) {
      if (!part) continue;
      const im = part.match(/^!\[([^\]]*)\]\(data:image\/(webp|jpeg|png);base64,([A-Za-z0-9+/=]+)\)$/);
      if (im) { // 内联图片(已在客户端压 ≤64KB WebP;data: 同源无害)
        const img = document.createElement("img");
        img.className = "inline-img";
        img.src = "data:image/" + im[2] + ";base64," + im[3];
        img.alt = im[1] || "图片";
        img.loading = "lazy";
        container.appendChild(img);
        continue;
      }
      if (/^https?:\/\//.test(part)) {
        let u = null;
        try { u = new URL(part); } catch (e) { /* 不是合法 URL → 当文字 */ }
        if (u && isVideoHost(u.hostname)) { // 视频页链接 → 一行卡片(域名+路径名,站外打开,不内嵌)
          const card = document.createElement("a");
          card.className = "video-card";
          card.href = part;
          card.target = "_blank";
          card.rel = "noopener";
          const title = document.createElement("span");
          title.className = "vc-title";
          let pathTxt = u.pathname;
          try { pathTxt = decodeURIComponent(u.pathname); } catch (e) { /* 保留原样 */ }
          title.textContent = pathTxt.replace(/\/$/, "") || u.hostname;
          const dom = document.createElement("span");
          dom.className = "vc-domain";
          dom.textContent = u.hostname;
          card.append("▶ ", title, dom);
          container.appendChild(card);
        } else if (u) { // 普通外链
          const a = document.createElement("a");
          a.href = part; a.target = "_blank"; a.rel = "noopener";
          a.textContent = part;
          container.appendChild(a);
        } else {
          container.appendChild(document.createTextNode(part));
        }
        continue;
      }
      container.appendChild(document.createTextNode(part));
    }
  }

  // 渲染一帖(签名由调用方先 FORUM.verifyPost 验过;invalid 时传入 state=invalid)
  function renderPost(frame, verified) {
    const state = verified ? classify(frame) : "invalid";
    const b = badge(state);
    const el = document.createElement("article");
    el.className = `post ${b.cls}`;
    el.dataset.postId = frame.post_id;
    el.dataset.state = state;
    const p = frame.post;
    el.innerHTML = `
      <header>
        <span class="fp" title="公钥指纹 ${p.author_fp}">${p.author_fp.slice(0, 6)}</span>
        <time>${p.ts}</time>
        <span class="badge ${b.cls}">${b.icon} ${b.label}</span>
        <span class="conf" title="社区验签计数">${frame.confirmations} 票</span>
      </header>
      <div class="body"></div>
      <footer>
        <button class="reply-btn" data-pid="${frame.post_id}">回复(=验签引用)</button>
        <button class="follow-btn" data-fp="${p.author_fp}">关注此身份</button>
      </footer>`;
    appendBody(el.querySelector(".body"), p.body);
    return el;
  }

  return { classify, badge, renderPost, appendBody };
})();

if (typeof module !== "undefined") module.exports = FORUM_VERIFY;
