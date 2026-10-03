// services/emitter.ts — P2.5+24(2026-10-03)
// 极简 EventEmitter,避免 Node 'events' 类型声明问题。
// PlayerService / WS client 都用它来解耦。

type Listener = (...args: any[]) => void

export class Emitter {
  private _listeners: Map<string | symbol, Listener[]> = new Map()

  on(event: string | symbol, listener: Listener): this {
    const list = this._listeners.get(event)
    if (list) {
      list.push(listener)
    } else {
      this._listeners.set(event, [listener])
    }
    return this
  }

  off(event: string | symbol, listener: Listener): this {
    const list = this._listeners.get(event)
    if (!list) return this
    const idx = list.indexOf(listener)
    if (idx >= 0) list.splice(idx, 1)
    return this
  }

  emit(event: string | symbol, ...args: any[]): boolean {
    const list = this._listeners.get(event)
    if (!list) return false
    for (const l of list.slice()) {
      try {
        l(...args)
      } catch (e) {
        console.error('[emitter] listener err:', e)
      }
    }
    return true
  }

  removeAllListeners(event?: string | symbol): this {
    if (event) {
      this._listeners.delete(event)
    } else {
      this._listeners.clear()
    }
    return this
  }
}