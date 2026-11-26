import React, { useEffect, useState } from 'react'
import { api } from '../api'

const KINDS = ['', '回执', '手改', '纠偏', '投递单', '熔断', '通道', '选路', '设置', '系统']

export default function Audit() {
  const [rows, setRows] = useState([])
  const [kind, setKind] = useState('')
  const [ref, setRef] = useState('')
  const [err, setErr] = useState('')
  const load = () => {
    const q = new URLSearchParams()
    if (kind) q.set('类别', kind)
    if (ref) q.set('关联', ref)
    return api(`/审计?${q.toString()}`).then(setRows).catch((e) => setErr(e.message))
  }
  useEffect(() => { load() }, [kind, ref])
  if (err) return <div className="panel err">{err}</div>
  return (
    <div className="panel">
      <h2>事件痕（同一笔送达的全链路痕迹）</h2>
      <div className="row">
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map((k) => <option key={k} value={k}>{k || '全部类别'}</option>)}
        </select>
        <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="按投递单编号过滤" />
        <button type="button" className="secondary" onClick={() => { setKind(''); setRef('') }}>清空过滤</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>类别</th><th>关联</th><th>动作</th><th>时间</th><th>备注</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号}><td>{r.类别}</td><td>{r.关联 ?? '-'}</td><td>{r.动作}</td><td>{r.时间}</td><td>{r.备注}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
