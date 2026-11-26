import React, { useEffect, useState } from 'react'
import { api } from '../api'

// 实现方钉死的真源裁决键，只读，不进可保存的编辑项。
const READONLY_KEYS = ['送达结果真源']

export default function Settings() {
  const [items, setItems] = useState({})
  const [msg, setMsg] = useState('')
  const load = () => api('/设置').then(setItems).catch((e) => setMsg(e.message))
  useEffect(() => { load() }, [])
  const save = async () => {
    try {
      const editable = Object.fromEntries(
        Object.entries(items).filter(([k]) => !READONLY_KEYS.includes(k))
      )
      await api('/设置', { method: 'PUT', body: JSON.stringify({ 项: editable }) })
      setMsg('已保存（真源裁决项只读，不可改写）')
      load()
    } catch (e) { setMsg(e.message) }
  }
  return (
    <div className="panel">
      <h2>渠规</h2>
      {Object.entries(items).map(([k, v]) => (
        <div className="row" key={k}>
          <span style={{ minWidth: 140 }}>{k}</span>
          {READONLY_KEYS.includes(k) ? (
            <span className="badge ok" style={{ flex: 1 }}>{v}</span>
          ) : (
            <select value={v} onChange={(e) => setItems({ ...items, [k]: e.target.value })}>
              <option value="是">是</option>
              <option value="否">否</option>
            </select>
          )}
        </div>
      ))}
      <div className="row"><button type="button" onClick={save}>保存</button><span className="msg">{msg}</span></div>
    </div>
  )
}
