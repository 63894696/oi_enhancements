/*!
 * @name Huibq_lxmusic源
 * @description Github搜索“洛雪音乐音源”，禁止批量下载！
 * @version v1.2.0
 * @author Huibq
 */
const DEV_ENABLE = false
const API_URL = 'https://lxmusicapi.onrender.com'
const API_KEY = 'share-v3'
const MUSIC_QUALITY = {
  kw: ['128k', '320k'],
  kg: ['128k', '320k'],
  tx: ['128k', '320k'],
  wy: ['128k', '320k'],
  mg: ['128k', '320k'],
}
const MUSIC_SOURCE = Object.keys(MUSIC_QUALITY)
const { EVENT_NAMES, request, on, send, utils, env, version } = globalThis.lx
const httpFetch = (url, options = { method: 'GET' }) => {
  return new Promise((resolve, reject) => {
    request(url, options, (err, resp) => {
      if (err) return reject(err)
      resolve(resp)
    })
  })
}
const handleGetMusicUrl = async (source, musicInfo, quality) => {
  const songId = musicInfo.hash ?? musicInfo.songmid

  const request = await httpFetch(`${API_URL}/url/${source}/${songId}/${quality}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
      'User-Agent': `${env ? `lx-music-${env}/${version}` : `lx-usic-request/${version}`}`,
      'X-Request-Key': API_KEY,
    },
  })
  const { body } = request
  if (!body || isNaN(Number(body.code))) throw new Error('unknow error')
  switch (body.code) {
    case 0:
      return body.url
    case 1:
      throw new Error('block ip')
    case 2:
      throw new Error('get music url failed')
    case 4:
      throw new Error('internal server error')
    case 5:
      throw new Error('too many requests')
    case 6:
      throw new Error('param error')
    default:
      throw new Error(body.msg ?? 'unknow error')
  }
}
const handleSearch = async (source, keywords, limit) => {
  // P2.5+29(2026-10-05):LX search action for huibq.js — 5 源并行搜
  // GET https://lxmusicapi.onrender.com/search/{source}?keywords={q}&limit={n}
  // 响应:{ code: 0, data: { list: [{id, name, singer, albumName, interval, ...}]}}
  // 标准化成 [{source, songmid, songname, singer, album, duration}]
  const cnt = limit ?? 20
  const req = await httpFetch(`${API_URL}/search/${source}?keywords=${encodeURIComponent(keywords)}&limit=${cnt}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
      'User-Agent': `${env ? `lx-music-${env}/${version}` : `lx-usic-request/${version}`}`,
      'X-Request-Key': API_KEY,
    },
  })
  const { body } = req
  if (!body || isNaN(Number(body.code))) throw new Error('search unknow error')
  switch (body.code) {
    case 0:
      return (body.data?.list || []).map(item => ({
        source,
        songmid: `${source}_${item.id}`,
        songname: item.name ?? '',
        singer: item.singer ?? '',
        album: item.albumName ?? '',
        duration: Number(item.interval) || 0,
      }))
    case 1:
      throw new Error('block ip')
    case 2:
      throw new Error('search failed')
    case 5:
      throw new Error('too many requests')
    default:
      throw new Error(body.msg ?? 'search unknow error')
  }
}
const musicSources = {}
MUSIC_SOURCE.forEach(item => {
  musicSources[item] = {
    name: item,
    type: 'music',
    actions: ['musicUrl', 'search'],
    qualitys: MUSIC_QUALITY[item],
  }
})
on(EVENT_NAMES.request, ({ action, source, info }) => {
  switch (action) {
    case 'musicUrl':
      if (env != 'mobile') {
        console.group(`Handle Action(musicUrl)`)
        console.log('source', source)
        console.log('quality', info.type)
        console.log('musicInfo', info.musicInfo)
        console.groupEnd()
      } else {
        console.log(`Handle Action(musicUrl)`)
        console.log('source', source)
        console.log('quality', info.type)
        console.log('musicInfo', info.musicInfo)
      }
      return handleGetMusicUrl(source, info.musicInfo, info.type)
        .then(data => Promise.resolve(data))
        .catch(err => Promise.reject(err))
    case 'search':
      // P2.5+29(2026-10-05):LX search action 接入
      // info.keywords 是纯文本 query,不传音频内容(P3.10b 0 上传红线延续)
      console.log(`Handle Action(search) source=${source} keywords=${info.keywords} limit=${info.limit}`)
      return handleSearch(source, info.keywords, info.limit)
        .then(data => Promise.resolve(data))
        .catch(err => Promise.reject(err))
    default:
      console.error(`action(${action}) not support`)
      return Promise.reject('action not support')
  }
})
send(EVENT_NAMES.inited, { status: true, openDevTools: DEV_ENABLE, sources: musicSources })
