from __future__ import annotations
from fastapi import APIRouter
from app.db import db
from app.services import delivery

router = APIRouter(tags=["对账"])


@router.get("/对账")
def reconcile():
    # 轧差大数与路况格、占用余量同源于 v_send_truth 重放，不再裸读 sends.status。
    with db() as conn:
        data = delivery.reconcile_snapshot(conn)
        data["切备次数"] = conn.execute(
            "SELECT COUNT(*) c FROM break_events WHERE action='切备'"
        ).fetchone()["c"]
    return data


@router.post("/对账/纠偏重放")
def reconcile_replay():
    """缺回执纠偏位：半截单态与回执拧花时，按回执事件流全量重放修回唯一真源。"""
    with db() as conn:
        out = delivery.replay_repair(conn)
    return out
