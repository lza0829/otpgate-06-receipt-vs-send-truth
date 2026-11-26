from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list
from app.services.common import audit, now_str
from app.services import delivery

router = APIRouter(tags=["投递"])


class SendCreate(BaseModel):
    手机掩码: str = Field(min_length=3)
    场景: str = "登录"
    备注: str = ""


class CorrectBody(BaseModel):
    目标状态: str
    备注: str = ""


def pick_channel(conn):
    policy = conn.execute("SELECT mode FROM route_policy WHERE id=1").fetchone()["mode"]
    if policy == "强制主路":
        row = conn.execute(
            "SELECT * FROM channels WHERE role='主路' AND status='启用' LIMIT 1"
        ).fetchone()
    elif policy == "强制备路":
        row = conn.execute(
            "SELECT * FROM channels WHERE role='备路' AND status='启用' LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM channels WHERE status='启用'
            ORDER BY weight DESC, id ASC LIMIT 1
            """
        ).fetchone()
    return row


def _serialize(r: dict) -> dict:
    return {
        "编号": r["id"],
        "投递单号": r["serial"],
        "手机掩码": r["phone_mask"],
        "场景": r["scene"],
        # 读侧只认真源重放态；缓存态单独留作对照，不用于业务判断。
        "状态": r["effective_status"],
        "缓存状态": r["status"],
        "投影偏差": bool(r["projection_drift"]),
        "通道": r.get("channel_name") or "-",
        "切备通道": r.get("failover_name") or "-",
        "送达序号": r["deliver_seq"],
        "单态戳": r["delivered_at"],
        "最近回执序号": r["latest_receipt_seq"],
        "缺回执": r["latest_receipt_id"] is None,
        "创建时间": r["created_at"],
        "备注": r["note"],
    }


@router.get("/投递单")
def list_sends():
    with db() as conn:
        rows = rows_to_list(
            conn.execute(
                """
                SELECT v.*, c.name AS channel_name, f.name AS failover_name
                FROM v_send_truth v
                LEFT JOIN channels c ON c.id = v.channel_id
                LEFT JOIN channels f ON f.id = v.failover_channel_id
                ORDER BY v.id DESC
                """
            ).fetchall()
        )
    return [_serialize(r) for r in rows]


@router.get("/投递单/{sid}")
def send_detail(sid: int):
    """验码单详情终态区：终态、回执链、送达序号、单态戳、投影对照一次取齐。"""
    with db() as conn:
        row = conn.execute(
            """
            SELECT v.*, c.name AS channel_name, f.name AS failover_name
            FROM v_send_truth v
            LEFT JOIN channels c ON c.id = v.channel_id
            LEFT JOIN channels f ON f.id = v.failover_channel_id
            WHERE v.id=?
            """,
            (sid,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "投递单不存在")
        receipts = rows_to_list(
            conn.execute(
                """
                SELECT r.id, r.seq, r.result, r.vendor_code, r.created_at, c.name AS channel_name
                FROM receipts r JOIN channels c ON c.id = r.channel_id
                WHERE r.send_id=? ORDER BY r.seq
                """,
                (sid,),
            ).fetchall()
        )
    return {
        **_serialize(dict(row)),
        "回执链": [
            {
                "编号": r["id"],
                "送达序号": r["seq"],
                "结果": r["result"],
                "厂商码": r["vendor_code"],
                "通道": r["channel_name"],
                "时间": r["created_at"],
            }
            for r in receipts
        ],
    }


@router.post("/投递单")
def create_send(body: SendCreate):
    with db() as conn:
        ch = pick_channel(conn)
        if not ch:
            raise HTTPException(400, "无可用通道")
        # 微秒入号，避免同秒连建撞 sends.serial 唯一键。
        serial = f"验{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        now = now_str()
        cur = conn.execute(
            """
            INSERT INTO sends(serial, phone_mask, scene, status, channel_id, note, created_at)
            VALUES (?, ?, ?, '待投递', ?, ?, ?)
            """,
            (serial, body.手机掩码, body.场景, ch["id"], body.备注, now),
        )
        sid = cur.lastrowid
        audit(conn, "投递单", sid, "创建", serial)
    return {"编号": sid, "投递单号": serial, "通道": ch["name"]}


@router.post("/投递单/{sid}/投递")
def dispatch_send(sid: int):
    # 投递按钮旁路：只推进状态机在途态，不产生送达结论，不参与真源裁决。
    with db() as conn:
        s = conn.execute("SELECT * FROM v_send_truth WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "投递单不存在")
        if s["effective_status"] not in ("待投递", "已切备"):
            raise HTTPException(400, f"当前状态不可投递：{s['effective_status']}")
        ch_id = s["failover_channel_id"] or s["channel_id"]
        ch = conn.execute("SELECT * FROM channels WHERE id=?", (ch_id,)).fetchone()
        if not ch or ch["status"] != "启用":
            raise HTTPException(400, "目标通道不可用")
        occ = delivery.channel_occupancy(conn)
        inflight = next((o["inflight"] for o in occ if o["id"] == ch_id), 0)
        if inflight >= ch["concurrency_limit"]:
            raise HTTPException(400, "通道并发已满")
        conn.execute(
            "UPDATE sends SET status='投递中', channel_id=?, sent_at=? WHERE id=?",
            (ch_id, now_str(), sid),
        )
        audit(conn, "投递单", sid, "投递", ch["name"])
    return {"结果": "投递中", "通道": ch["name"]}


@router.post("/投递单/{sid}/纠正")
def correct_send(sid: int, body: CorrectBody):
    """缺回执纠偏位（单态侧）：只对无回执单开放，有回执一律由回执真源裁决。"""
    with db() as conn:
        delivery.begin_write(conn)
        out = delivery.manual_correct_status(conn, sid, body.目标状态, body.备注)
    return out
