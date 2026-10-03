// services/api.ts — P2.5+24(2026-10-03)
// API + WS 客户端,封装 fetch/JSON + 简单重试。
// 路径前缀假设同源(/api/...)。

export interface ApiResponse<T = any> {
  ok: boolean
  err?: string
  [k: string]: any
}

export async function request<T = any>(method: string, path: string, body?: any): Promise<ApiResponse<T>> {
  const opts: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body !== undefined) {
    opts.body = JSON.stringify(body)
  }
  try {
    const r = await fetch(path, opts)
    const text = await r.text()
    let json: any = {}
    try {
      json = text ? JSON.parse(text) : {}
    } catch {
      return { ok: false, err: `bad json (${r.status}): ${text.slice(0, 200)}` }
    }
    if (!r.ok && !json.ok) {
      return { ok: false, err: json.err || `http ${r.status}` }
    }
    return json
  } catch (e: any) {
    return { ok: false, err: `network: ${e?.message || e}` }
  }
}

// 默认导出:函数签名 api('/api/cmd', body?) 也能用 api.get / api.post
type ApiFn = {
  (path: string, body?: any): Promise<ApiResponse>
  get: <T = any>(path: string) => Promise<ApiResponse<T>>
  post: <T = any>(path: string, body: any) => Promise<ApiResponse<T>>
}
const api = ((path: string, body?: any) => request(body !== undefined ? 'POST' : 'GET', path, body)) as ApiFn
api.get = <T = any>(path: string) => request<T>('GET', path)
api.post = <T = any>(path: string, body: any) => request<T>('POST', path, body)

export { api }
export default api

export function wsConnect(path: string, onMsg: (msg: any) => void): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${proto}//${location.host}${path}`
  const ws = new WebSocket(url)
  ws.onmessage = (ev) => {
    try {
      onMsg(JSON.parse(ev.data))
    } catch {
      // ignore
    }
  }
  return ws
}