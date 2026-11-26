import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Receipts() {
  const [sends, setSends] = useState([])
  const [data, setData] = useState({ 汇总: {}, 列表: [] })
  const [sid, setSid] = useState('')
  const [result, setResult] = useState('成功')
  const [msg, setMsg] = useState('')
  const load = async () => {
    try {
      const s = await api('/投递单')
      setSends(s)
      if (!sid && s[0]) setSid(String(s[0].编号))
      setData(await api('/回执'))
    } catch (e) { setMsg(e.message) }
  }
  useEffect(() => { load() }, [])
  const submit = async () => {
    try {
      const r = await api(`/投递单/${sid}/回执`, {
        method: 'POST',
        body: JSON.stringify({ 结果: result, 厂商码: 'OK' }),
      })
      setMsg(`回执已记，送达序号 #${r.序号}`)
      load()
    } catch (e) { setMsg(e.message) }
  }
  return (
    <div className="panel">
      <h2>回执簿（送达结果真源）</h2>
      <p className="msg">送达事件只追加流：终态按送达序号重放，单态为投影缓存，与回执同笔刷新</p>
      <div className="row">
        <select value={sid} onChange={(e) => setSid(e.target.value)}>
          {sends.map((s) => <option key={s.编号} value={s.编号}>{s.投递单号} · {s.状态}</option>)}
        </select>
        <select value={result} onChange={(e) => setResult(e.target.value)}>
          <option value="成功">成功</option>
          <option value="失败">失败</option>
        </select>
        <button type="button" onClick={submit}>写入回执</button>
      </div>
      <div className="grid">
        <div className="stat"><div className="n">{data.汇总.回执条数 ?? 0}</div><div className="l">回执条数</div></div>
        <div className="stat"><div className="n">{data.汇总.成功条数 ?? 0}</div><div className="l">成功</div></div>
        <div className="stat"><div className="n">{data.汇总.失败条数 ?? 0}</div><div className="l">失败</div></div>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>送达序号</th><th>投递单号</th><th>通道</th><th>结果</th><th>重放终态</th><th>单态戳序号</th><th>对照</th><th>厂商码</th><th>时间</th></tr></thead>
          <tbody>
            {data.列表.map((r) => (
              <tr key={r.编号} className={r.投影偏差 ? 'drift' : ''}>
                <td>#{r.送达序号}</td>
                <td>{r.投递单号}</td>
                <td>{r.通道}</td>
                <td className={r.结果 === '成功' ? 'ok' : 'bad'}>{r.结果}</td>
                <td>{r.重放终态}</td>
                <td>{r.单态戳序号 ? `#${r.单态戳序号}` : '-'}</td>
                <td>{r.投影偏差 ? <span className="badge bad">拧花待纠</span> : <span className="badge ok">一致</span>}</td>
                <td>{r.厂商码}</td>
                <td>{r.时间}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><span className="msg">{msg}</span><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
