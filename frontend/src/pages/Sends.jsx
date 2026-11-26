import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Sends() {
  const [rows, setRows] = useState([])
  const [phone, setPhone] = useState('139****6602')
  const [scene, setScene] = useState('登录')
  const [msg, setMsg] = useState('')
  const [detail, setDetail] = useState(null)
  const load = () => api('/投递单').then(setRows).catch((e) => setMsg(e.message))
  useEffect(() => { load() }, [])
  const create = async () => {
    try {
      await api('/投递单', { method: 'POST', body: JSON.stringify({ 手机掩码: phone, 场景: scene }) })
      setMsg('已创建')
      load()
    } catch (e) { setMsg(e.message) }
  }
  const dispatch = async (id) => {
    try {
      const r = await api(`/投递单/${id}/投递`, { method: 'POST' })
      setMsg(`投递：${r.通道}`)
      load()
    } catch (e) { setMsg(e.message) }
  }
  const openDetail = async (id) => {
    try { setDetail(await api(`/投递单/${id}`)) } catch (e) { setMsg(e.message) }
  }
  const correct = async (id, target) => {
    try {
      const r = await api(`/投递单/${id}/纠正`, { method: 'POST', body: JSON.stringify({ 目标状态: target }) })
      setMsg(`已纠正为 ${r.状态}（仅缺回执单可手改）`)
      load()
    } catch (e) { setMsg(e.message) }
  }
  return (
    <div className="panel">
      <h2>验码单（终态读回执重放）</h2>
      <div className="row">
        <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="手机掩码" />
        <select value={scene} onChange={(e) => setScene(e.target.value)}>
          <option value="登录">登录</option>
          <option value="支付">支付</option>
          <option value="改密">改密</option>
        </select>
        <button type="button" onClick={create}>新建投递单</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>投递单号</th><th>终态(真源)</th><th>缓存态</th><th>送达序号</th><th>单态戳</th><th>通道</th><th>缺回执</th><th>操作</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.投影偏差 ? 'drift' : ''}>
                <td><button type="button" className="link" onClick={() => openDetail(r.编号)}>{r.投递单号}</button></td>
                <td className={r.状态 === '已送达' ? 'ok' : r.状态 === '已失败' ? 'bad' : ''}>{r.状态}</td>
                <td>{r.缓存状态}{r.投影偏差 && <span className="badge bad"> 拧花</span>}</td>
                <td>{r.送达序号 ? `#${r.送达序号}` : '-'}</td>
                <td>{r.单态戳 || '-'}</td>
                <td>{r.通道}</td>
                <td>{r.缺回执 ? <span className="badge warn">是</span> : '否'}</td>
                <td className="row">
                  <button type="button" onClick={() => dispatch(r.编号)} disabled={!['待投递','已切备'].includes(r.状态)}>投递</button>
                  {r.缺回执 && <button type="button" className="secondary" onClick={() => correct(r.编号, '已失败')}>缺回执置失败</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {detail && (
        <div className="panel inner">
          <h3>详情终态区 · {detail.投递单号}</h3>
          <div className="row">
            <span>真源终态：<b>{detail.状态}</b></span>
            <span>缓存态：{detail.缓存状态}</span>
            <span>送达序号：{detail.送达序号 ? `#${detail.送达序号}` : '-'}</span>
            <span>单态戳：{detail.单态戳 || '-'}</span>
            <span>投影偏差：{detail.投影偏差 ? '是' : '否'}</span>
            <button type="button" className="secondary" onClick={() => setDetail(null)}>关闭</button>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>回执送达序号</th><th>结果</th><th>通道</th><th>厂商码</th><th>时间</th></tr></thead>
              <tbody>
                {detail.回执链.length === 0 && <tr><td colSpan={5}>无回执（缺回执，终态待事件补齐）</td></tr>}
                {detail.回执链.map((x) => (
                  <tr key={x.编号}><td>#{x.送达序号}</td><td>{x.结果}</td><td>{x.通道}</td><td>{x.厂商码}</td><td>{x.时间}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div className="row"><span className="msg">{msg}</span><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
