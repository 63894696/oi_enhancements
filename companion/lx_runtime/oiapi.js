/*!
 * @name oiapi (网易云第二兜底)
 * @description 当 huibq.js(限流) + gdstudio.js(挂了) 都失败时,
 *              oiapi.js 接盘 — 基于 oiapi.net 公共反向代理 API。
 *              只支持 netease 子源,跟 gdstudio 完全重复,但三层冗余保稳。
 *              跟 gdstudio 关键差别:**直接 id → mp3 URL,不需要 search**。
 * @version v1
 * @author prisir
 *
 * 用法:LX EVENT_NAMES 协议。声明 `wy` 子源;当 huibq/gdstudio 都失败抛错时,
 *      lx_runtime.callRequest 自动遍历下一个声明 wy 的 handler → 本源接盘。
 */
const { EVENT_NAMES, request, on, send, version } = globalThis.lx;

const API_URL = 'https://oiapi.net/api/Music_163';
const DEFAULT_TIMEOUT = 8000; // 8s,避免挂起

const musicSources = {
  wy: {
    name: '网易云(oiapi兜底)',
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
        reject(new Error('oiapi: bad json: ' + (e.message || e)));
      }
    });
  });
}

function extractMp3Url(body) {
  // oiapi 响应形态:{"code":0, "data":[{"url":"http://m701.music.126.net/.../...mp3", ...}]}
  if (!body || body.code !== 0) {
    throw new Error(`oiapi: bad response code=${body && body.code}`);
  }
  const arr = body.data;
  if (!Array.isArray(arr) || arr.length === 0) {
    throw new Error('oiapi: empty data array');
  }
  const url = arr[0] && arr[0].url;
  if (!url) throw new Error('oiapi: no url in data[0]');
  return url;
}

async function handleMusicUrl(source, musicInfo, quality) {
  // 仅处理 wy(netease)子源
  if (source !== 'wy') throw new Error(`oiapi: source='${source}' not supported (only wy)`);

  // oiapi 直接接 songId 参数,**不需要 search**;若上游没传 id,必须报错
  const songId = musicInfo && (musicInfo.songmid || musicInfo.id || musicInfo.songId);
  if (!songId) {
    throw new Error('oiapi: no song id in musicInfo (need songmid/id)');
  }

  // quality → br:128k=128000, 320k=320000
  const br = quality === '320k' ? 320000 : 128000;
  const url = `${API_URL}?id=${encodeURIComponent(songId)}&br=${br}`;
  const body = await httpGet(url);
  return extractMp3Url(body);
}

on(EVENT_NAMES.request, ({ action, source, info }) => {
  if (action === 'musicUrl') {
    return handleMusicUrl(source, info.musicInfo, info.type || '320k')
      .then(data => Promise.resolve(data))
      .catch(err => Promise.reject(new Error(`oiapi[${source}]: ${err.message || err}`)));
  }
  return Promise.reject(new Error(`oiapi: action='${action}' not supported`));
});

send(EVENT_NAMES.inited, { status: true, openDevTools: false, sources: musicSources, update: { version: version || '1', log: '', updateUrl: '' } });
