import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Reconcile() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = () => api('/对账').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const repair = async () => {
    try {
      const r = await api('/对账/纠偏重放', { method: 'POST' })
      setMsg(`重放纠偏完成：复核 ${r.复核单数} 单，残余投影偏差 ${r.残余投影偏差}`)
      load()
    } catch (e) { setMsg(e.message) }
  }
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const warnKeys = ['送达与回执差', '投影偏差单数', '缺回执待终结']
  return (
    <div className="panel">
      <h2>轧差（全部计数同源回执重放）</h2>
      <div className="row">
        <button type="button" onClick={repair}>缺回执纠偏：全量重放</button>
        <span className="msg">{msg}</span>
      </div>
      <div className="grid">
        {Object.entries(data).map(([k, v]) => (
          <div className={`stat ${warnKeys.includes(k) && v !== 0 ? 'warn' : ''}`} key={k}>
            <div className="n">{v}</div><div className="l">{k}</div>
          </div>
        ))}
      </div>
      <p className="msg">送达与回执差恒为 0 即真源自洽；投影偏差单数 / 缺回执待终结非 0 时，点上方重放纠回唯一真源。</p>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
