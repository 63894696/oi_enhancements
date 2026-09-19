// forum_store.js — 本地存档(我的帖子副本 / 关注身份 / 已读游标)
// 红线:本地优先——落 localStorage(MVP),重编译后迁 profile JSON(os_crypt 边界外,非敏感)。
// 我的帖子副本 = 「数据属于个人」的落地:即使 relay TTL 过期/撤下,本地副本仍完整可验。

const FORUM_STORE = (() => {
  const K_POSTS = "forum_my_posts";     // [{post, post_id, ts}] 我发过的帖(含 retract 后仍保留本地副本)
  const K_FOLLOWS = "forum_follows";    // [author_fp] 我关注/信任的身份
  const K_CURSOR = "forum_last_seq";    // 增量拉取游标

  function load(k, dflt) { try { return JSON.parse(localStorage.getItem(k)) ?? dflt; } catch { return dflt; } }
  function save(k, v) { localStorage.setItem(k, JSON.stringify(v)); }

  function rememberMyPost(post, post_id) {
    const list = load(K_POSTS, []);
    if (!list.find(p => p.post_id === post_id)) {
      list.push({ post, post_id, saved_at: new Date().toISOString() });
      save(K_POSTS, list);
    }
  }
  function myPosts() { return load(K_POSTS, []); }
  function removeMyPost(post_id) { save(K_POSTS, load(K_POSTS, []).filter(p => p.post_id !== post_id)); }

  function follows() { return load(K_FOLLOWS, []); }
  function follow(fp) { const l = load(K_FOLLOWS, []); if (!l.includes(fp)) { l.push(fp); save(K_FOLLOWS, l); } }
  function unfollow(fp) { save(K_FOLLOWS, load(K_FOLLOWS, []).filter(x => x !== fp)); }

  function lastSeq() { return load(K_CURSOR, 0); }
  function setLastSeq(n) { save(K_CURSOR, Math.max(n, lastSeq())); }

  return { rememberMyPost, myPosts, removeMyPost, follows, follow, unfollow, lastSeq, setLastSeq };
})();

if (typeof module !== "undefined") module.exports = FORUM_STORE;
