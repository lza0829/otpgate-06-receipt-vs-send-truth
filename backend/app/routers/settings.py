from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import db, rows_to_list
from app.services.common import audit

router = APIRouter(tags=["设置"])

# 真源裁决由实现钉死，任何外部写入都不允许改这个键。
PROTECTED_KEYS = {"送达结果真源"}


class SettingsBody(BaseModel):
    项: dict[str, str]


@router.get("/设置")
def get_settings():
    with db() as conn:
        rows = rows_to_list(conn.execute("SELECT key, value FROM settings").fetchall())
    return {r["key"]: r["value"] for r in rows}


@router.put("/设置")
def put_settings(body: SettingsBody):
    forbidden = PROTECTED_KEYS.intersection(body.项.keys())
    if forbidden:
        raise HTTPException(409, f"真源裁决项只读，禁止改写：{','.join(forbidden)}")
    with db() as conn:
        for k, v in body.项.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, v),
            )
        audit(conn, "设置", None, "批量更新", ",".join(body.项.keys()))
    return {"结果": "已更新"}
