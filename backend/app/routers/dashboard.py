from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services import delivery

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        # 路况已送达格：按回执真源重放态计数，不读投影缓存。
        dist = {
            "待投递": conn.execute(
                "SELECT COUNT(*) c FROM v_send_truth WHERE effective_status='待投递'"
            ).fetchone()["c"],
            "投递中": conn.execute(
                "SELECT COUNT(*) c FROM v_send_truth WHERE effective_status='投递中'"
            ).fetchone()["c"],
            "已送达": conn.execute(
                "SELECT COUNT(*) c FROM v_send_truth WHERE effective_status='已送达'"
            ).fetchone()["c"],
            "已失败": conn.execute(
                "SELECT COUNT(*) c FROM v_send_truth WHERE effective_status='已失败'"
            ).fetchone()["c"],
            "已切备": conn.execute(
                "SELECT COUNT(*) c FROM v_send_truth WHERE effective_status='已切备'"
            ).fetchone()["c"],
            "熔断通道": conn.execute(
                "SELECT COUNT(*) c FROM channels WHERE status='熔断中'"
            ).fetchone()["c"],
            "投影偏差": conn.execute(
                "SELECT COUNT(*) c FROM v_send_truth WHERE projection_drift=1"
            ).fetchone()["c"],
        }
        inflight = rows_to_list(
            conn.execute(
                """
                SELECT v.id AS 编号, v.serial AS 投递单号, v.phone_mask AS 手机掩码,
                       v.scene AS 场景, v.effective_status AS 状态,
                       c.name AS 当前通道
                FROM v_send_truth v
                LEFT JOIN channels c ON c.id = v.channel_id
                WHERE v.effective_status IN ('待投递','投递中','已切备')
                ORDER BY v.id DESC
                """
            ).fetchall()
        )
        chans = [
            {
                "代号": o["code"],
                "名称": o["name"],
                "角色": o["role"],
                "状态": o["status"],
                "在途占用": o["inflight"],
                "占用余量": o["spare"],
                "并发上限": o["concurrency_limit"],
                "连续失败": o["consecutive_fails"],
            }
            for o in delivery.channel_occupancy(conn)
        ]
    return {"分布": dist, "在途": inflight, "通道": chans}
