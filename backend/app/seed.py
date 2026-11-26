from __future__ import annotations
from datetime import datetime

from app.db import db, init_db
from app.services.common import audit, now_str
from app.services.delivery import backfill_truth


def seed_if_empty() -> None:
    init_db()
    with db() as conn:
        # 每次启动先按回执事件流回填序号并重放投影，保证半截拧花不会跨进程长期存在。
        backfill_truth(conn)
        n = conn.execute("SELECT COUNT(*) AS c FROM channels").fetchone()["c"]
        if n:
            return
        now = now_str()
        conn.execute(
            """
            INSERT INTO channels(code, name, role, weight, status, concurrency_limit, fail_threshold)
            VALUES
              ('阿里云短信', '阿里云主通道', '主路', 80, '启用', 30, 3),
              ('腾讯云短信', '腾讯云备通道', '备路', 20, '启用', 20, 3)
            """
        )
        conn.execute(
            "INSERT INTO route_policy(id, mode, updated_at) VALUES (1, '权重', ?)",
            (now,),
        )
        conn.execute(
            """
            INSERT INTO settings(key, value) VALUES
              ('送达结果真源', '回执簿送达事件重放（sends.status 仅为投影缓存，由回执同笔刷新）'),
              ('允许双挂硬投', '否'),
              ('熔断后自动探测', '是'),
              ('回执晚到可改写', '是')
            """
        )
        main = conn.execute("SELECT id FROM channels WHERE role='主路'").fetchone()["id"]
        serial = f"验{datetime.now().strftime('%Y%m%d')}0001"
        conn.execute(
            """
            INSERT INTO sends(serial, phone_mask, scene, status, channel_id, note, created_at)
            VALUES (?, '138****8001', '登录', '待投递', ?, '种子可投递单', ?)
            """,
            (serial, main, now),
        )
        audit(conn, "系统", None, "种子初始化", "验码渠")
    # 既有库也要钉住真源声明；该键由实现固定，设置页只读，不接受改写。
    with db() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value)
            VALUES ('送达结果真源', '回执簿送达事件重放（sends.status 仅为投影缓存，由回执同笔刷新）')
            ON CONFLICT(key) DO NOTHING
            """
        )
