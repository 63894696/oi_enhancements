/*!
 * @name gdstudio (网易云兜底)
 * @description 当 huibq.js 走的 onrender.com 公共服务限流 (`code: 1禁止批量下载`) 时,
 *              gdstudio.js 接盘 — 基于 music-api.gdstudio.xyz 公共反向代理 API。
 *              只支持 netease 子源,主流中文歌 90%+ 在网易云有版权。
 * @version v1
 * @author prisir
 *
 * 用法:LX EVENT_NAMES 协议。声明 `wy` 子源;当 huibq.js 失败抛错时,
 *      lx_runtime.callRequest 自动遍历下一个声明 wy 的 handler → 本源接盘。
 */
const { EVENT_NAMES, request, on, send, version } = globalThis.lx;

const API_URL = 'https://music-api.gdstudio.xyz/api.php';
const DEFAULT_TIMEOUT = 8000; // 8s/step,避免挂起

const musicSources = {
  wy: {
    name: '网易云(gdstudio兜底)',
    type: 'music',
    actions: ['musicUrl'],
    qualitys: ['128k', '320k'],
  },
};

function httpGet(url, timeout = DEFAULT_TIMEOUT) {
  return new Promise((resolve, reject) => {
    request(url, { method: 'GET', timeout }, (err, resp) => {
      if (err) return reject(err);
      try {
        const body = typeof resp.body === 'string' ? JSON.parse(resp.body) : resp.body;
        resolve(body);
      } catch (e) {
        reject(new Error('gdstudio: bad json: ' + (e.message || e)));
      }
    });
  });
}

async function searchNeteaseId(title, artist) {
  // gdstudio 的 search 用 `name` 字段;`keyword` 用 title+artist 提高命中率
  const keyword = (title + (artist ? ' ' + artist : '')).trim();
  const url = `${API_URL}?types=search&source=netease&name=${encodeURIComponent(title)}&keyword=${encodeURIComponent(keyword)}&count=1`;
  const data = await httpGet(url);
  if (!Array.isArray(data) || data.length === 0) {
    throw new Error('gdstudio: search empty');
  }
  const first = data[0];
  if (!first || !first.id) throw new Error('gdstudio: no id in search result');
  return String(first.id);
}

async function getNeteaseUrl(songId, quality) {
  // quality → br:128k=128000, 320k=320000
  const br = quality === '320k' ? 320000 : 128000;
  const url = `${API_URL}?types=url&source=netease&id=${encodeURIComponent(songId)}&br=${br}`;
  const data = await httpGet(url);
  if (!data || !data.url) throw new Error('gdstudio: no url in response');
  return data.url;
}

async function handleMusicUrl(source, musicInfo, quality) {
  // 仅处理 wy(netease)子源
  if (source !== 'wy') throw new Error(`gdstudio: source='${source}' not supported (only wy)`);

  const title = (musicInfo && (musicInfo.songname || musicInfo.name || musicInfo.title)) || '';
  const artist = (musicInfo && (musicInfo.singer || musicInfo.artist)) || '';
  if (!title) throw new Error('gdstudio: no song title');

  // Step 1: search id
  const songId = await searchNeteaseId(title, artist);
  // Step 2: get mp3 url
  const mp3Url = await getNeteaseUrl(songId, quality || '320k');
  return mp3Url;
}

on(EVENT_NAMES.request, ({ action, source, info }) => {
  if (action === 'musicUrl') {
    return handleMusicUrl(source, info.musicInfo, info.type || '320k')
      .then(data => Promise.resolve(data))
      .catch(err => Promise.reject(new Error(`gdstudio[${source}]: ${err.message || err}`)));
  }
  return Promise.reject(new Error(`gdstudio: action='${action}' not supported`));
});

send(EVENT_NAMES.inited, { status: true, openDevTools: false, sources: musicSources, update: { version: version || '1', log: '', updateUrl: '' } });
