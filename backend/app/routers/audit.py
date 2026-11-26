from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit(类别: str | None = None, 关联: int | None = None):
    # 事件痕过滤：可按类别（回执/手改/纠偏/投递单/熔断…）与关联投递单定位同一笔送达的全部痕迹。
    sql = "SELECT * FROM audit_events WHERE 1=1"
    args: list = []
    if 类别:
        sql += " AND kind=?"
        args.append(类别)
    if 关联 is not None:
        sql += " AND ref_id=?"
        args.append(关联)
    sql += " ORDER BY id DESC LIMIT 200"
    with db() as conn:
        rows = rows_to_list(conn.execute(sql, args).fetchall())
    return [
        {
            "编号": r["id"],
            "类别": r["kind"],
            "关联": r["ref_id"],
            "动作": r["action"],
            "时间": r["created_at"],
            "备注": r["note"],
        }
        for r in rows
    ]
