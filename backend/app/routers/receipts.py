from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import db, rows_to_list
from app.services import delivery

router = APIRouter(tags=["回执"])


class ReceiptBody(BaseModel):
    结果: str
    厂商码: str = ""
    幂等键: str = ""


@router.get("/回执")
def list_receipts():
    """回执簿时间序：送达事件流原文，附该单按真源重放的终态与投影偏差。"""
    with db() as conn:
        rows = rows_to_list(
            conn.execute(
                """
                SELECT r.id, r.seq, r.send_id, r.channel_id, r.result, r.vendor_code,
                       r.created_at, s.serial, c.name AS channel_name,
                       v.effective_status, v.projection_drift, v.deliver_seq
                FROM receipts r
                JOIN sends s ON s.id = r.send_id
                JOIN channels c ON c.id = r.channel_id
                JOIN v_send_truth v ON v.id = r.send_id
                ORDER BY r.seq DESC, r.id DESC
                LIMIT 200
                """
            ).fetchall()
        )
        summary = conn.execute(
            """
            SELECT
              COUNT(*) AS 回执条数,
              SUM(CASE WHEN result='成功' THEN 1 ELSE 0 END) AS 成功条数,
              SUM(CASE WHEN result='失败' THEN 1 ELSE 0 END) AS 失败条数
            FROM receipts
            """
        ).fetchone()
    return {
        "汇总": {
            "回执条数": summary["回执条数"] or 0,
            "成功条数": summary["成功条数"] or 0,
            "失败条数": summary["失败条数"] or 0,
        },
        "列表": [
            {
                "编号": r["id"],
                "送达序号": r["seq"],
                "投递单编号": r["send_id"],
                "投递单号": r["serial"],
                "通道": r["channel_name"],
                "结果": r["result"],
                "厂商码": r["vendor_code"],
                "时间": r["created_at"],
                "重放终态": r["effective_status"],
                "单态戳序号": r["deliver_seq"],
                "投影偏差": bool(r["projection_drift"]),
            }
            for r in rows
        ],
    }


@router.post("/投递单/{sid}/回执")
def add_receipt(sid: int, body: ReceiptBody):
    # 回执插入与单态推进在 delivery.append_receipt 内同一事务，同成同败。
    with db() as conn:
        delivery.begin_write(conn)
        out = delivery.append_receipt(conn, sid, body.结果, body.厂商码, body.幂等键)
    return out
