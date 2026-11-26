"""送达结果真源服务。

唯一真源：回执簿（receipts）送达事件流，按 seq 重放决定每笔投递的终态。
sends.status 是投影缓存，只允许在本模块的同一笔写事务内被刷新；
任何读面都走 v_send_truth，禁止再直接拿 sends.status 当送达结论。
"""
from __future__ import annotations

from fastapi import HTTPException

from app.services.common import audit, get_setting, now_str

# 送达终态：只有回执重放能给出这两个结论。
TERMINAL_STATUSES = ("已送达", "已失败")
# 缺回执时，状态机允许停留/人工可纠正到的非终态（不得手造出“已送达”）。
MANUAL_ALLOWED = ("待投递", "投递中", "已失败")


def begin_write(conn) -> None:
    """拿库级写锁，把并发补回执/手改串行化，配合 busy_timeout 等待。"""
    conn.execute("BEGIN IMMEDIATE")


def next_receipt_seq(conn) -> int:
    return conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM receipts").fetchone()["n"]


def _latest_receipt(conn, send_id):
    return conn.execute(
        """
        SELECT id, result, created_at, seq
        FROM receipts
        WHERE send_id = ?
        ORDER BY seq DESC, id DESC
        LIMIT 1
        """,
        (send_id,),
    ).fetchone()


def effective_status(conn, send_id) -> str | None:
    row = conn.execute(
        "SELECT effective_status FROM v_send_truth WHERE id = ?", (send_id,)
    ).fetchone()
    return row["effective_status"] if row else None


def refresh_projection(conn, send_id) -> None:
    """把 sends 投影缓存刷成回执重放结论。只能在写事务内调用。

    有回执：终态=最近一条回执结果，同笔更新 status/finished_at/送达序号/单态戳；
    无回执：保留状态机原值，缺回执绝不强造终态。
    """
    s = conn.execute("SELECT * FROM sends WHERE id=?", (send_id,)).fetchone()
    if s is None:
        return
    latest = _latest_receipt(conn, send_id)
    if latest is None:
        # 缺回执绝不允许“已送达”投影：若被强造，重放拉回待终结态并清空送达戳。
        if s["status"] == "已送达":
            conn.execute(
                "UPDATE sends SET status='待投递', finished_at=NULL, "
                "deliver_seq=NULL, delivered_at=NULL WHERE id=?",
                (send_id,),
            )
        return
    eff = "已送达" if latest["result"] == "成功" else "已失败"
    if eff == "已送达":
        conn.execute(
            """
            UPDATE sends
            SET status=?, finished_at=COALESCE(finished_at, ?),
                deliver_seq=?, delivered_at=?
            WHERE id=?
            """,
            (eff, latest["created_at"], latest["seq"], latest["created_at"], send_id),
        )
    else:
        # 晚到失败改写：撤掉此前的送达序号与单态戳，全库只认这一个终态。
        conn.execute(
            """
            UPDATE sends
            SET status=?, finished_at=COALESCE(finished_at, ?),
                deliver_seq=NULL, delivered_at=NULL
            WHERE id=?
            """,
            (eff, latest["created_at"], send_id),
        )


def append_receipt(conn, send_id: int, result: str, vendor_code: str = "",
                   idempotency_key: str = "") -> dict:
    """回执写入 + 终态推进的唯一入口，整段同笔事务、同成同败。"""
    if result not in ("成功", "失败"):
        raise HTTPException(400, "结果非法")
    s = conn.execute("SELECT * FROM sends WHERE id=?", (send_id,)).fetchone()
    if s is None:
        raise HTTPException(404, "投递单不存在")

    if idempotency_key:
        dup = conn.execute(
            "SELECT * FROM receipts WHERE send_id=? AND idempotency_key=?",
            (send_id, idempotency_key),
        ).fetchone()
        if dup is not None:
            return {"结果": "回执幂等命中", "序号": dup["seq"], "幂等": True}

    truth = conn.execute(
        "SELECT effective_status, latest_receipt_id FROM v_send_truth WHERE id=?",
        (send_id,),
    ).fetchone()
    already_terminal = truth["effective_status"] in TERMINAL_STATUSES
    if already_terminal:
        # 晚到改写是独立准入策略：只决定事件能不能进日志，重放规则不因此分叉。
        if get_setting(conn, "回执晚到可改写", "是") != "是":
            raise HTTPException(409, "该单已终结，渠规禁止晚到回执改写；终态仍以既有回执重放为准")

    ch_id = s["failover_channel_id"] or s["channel_id"]
    if not ch_id:
        raise HTTPException(400, "无通道，回执无法挂接")

    seq = next_receipt_seq(conn)
    now = now_str()
    conn.execute(
        """
        INSERT INTO receipts(send_id, channel_id, result, vendor_code, seq, idempotency_key, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (send_id, ch_id, result, vendor_code, seq, idempotency_key, now),
    )
    # 投影刷新与回执插入同一事务：回执落库则终态必同步，不存在半截。
    refresh_projection(conn, send_id)
    if result == "成功":
        conn.execute(
            "UPDATE channels SET consecutive_fails=0 WHERE id=?", (ch_id,)
        )
    audit(conn, "回执", send_id, f"{result}#seq{seq}", vendor_code or "送达事件")
    return {"结果": "回执已记", "序号": seq, "幂等": False}


def manual_correct_status(conn, send_id: int, target: str, note: str = "") -> dict:
    """手改单态。真源守卫：已有回执的单一律拒绝，避免补回执与手改长期双账。"""
    s = conn.execute("SELECT * FROM sends WHERE id=?", (send_id,)).fetchone()
    if s is None:
        raise HTTPException(404, "投递单不存在")
    has_receipt = conn.execute(
        "SELECT 1 FROM receipts WHERE send_id=? LIMIT 1", (send_id,)
    ).fetchone()
    if has_receipt is not None:
        raise HTTPException(409, "该单已有送达回执，终态以回执重放为准，禁止手改；如需纠正请补回执或走纠偏重放")
    if target not in MANUAL_ALLOWED:
        raise HTTPException(400, f"无回执单仅可纠正到 { '/'.join(MANUAL_ALLOWED) }，不得手造已送达")
    conn.execute(
        "UPDATE sends SET status=?, finished_at=? WHERE id=?",
        (target, now_str() if target in TERMINAL_STATUSES else None, send_id),
    )
    audit(conn, "手改", send_id, f"状态纠正为{target}", note or "缺回执人工纠正")
    return {"结果": "已纠正", "状态": target}


def replay_repair(conn) -> dict:
    """缺回执纠偏位：按回执事件流全量重放，把所有半截拧花的投影修回真源。"""
    begin_write(conn)
    ids = [r["id"] for r in conn.execute("SELECT id FROM sends").fetchall()]
    for sid in ids:
        refresh_projection(conn, sid)
    drift_after = conn.execute(
        "SELECT COUNT(*) c FROM v_send_truth WHERE projection_drift=1"
    ).fetchone()["c"]
    audit(conn, "纠偏", None, "全量重放纠偏", f"复核{len(ids)}单，残余偏差{drift_after}")
    return {"复核单数": len(ids), "残余投影偏差": drift_after}


def backfill_truth(conn) -> None:
    """启动回填：老库回执补全局序号，再逐单重放刷投影。"""
    max_seq = conn.execute("SELECT COALESCE(MAX(seq), 0) m FROM receipts").fetchone()["m"]
    for row in conn.execute(
        "SELECT id FROM receipts WHERE seq IS NULL ORDER BY id"
    ).fetchall():
        max_seq += 1
        conn.execute("UPDATE receipts SET seq=? WHERE id=?", (max_seq, row["id"]))
    for row in conn.execute("SELECT id FROM sends").fetchall():
        refresh_projection(conn, row["id"])


def channel_occupancy(conn) -> list[dict]:
    """占用余量探针：在途计数同样走重放读模型，不读裸 sends.status。"""
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT c.id, c.code, c.name, c.role, c.status, c.weight,
                   c.concurrency_limit, c.fail_threshold, c.consecutive_fails,
                   (SELECT COUNT(*) FROM v_send_truth v
                     WHERE v.channel_id = c.id AND v.effective_status = '投递中') AS inflight,
                   c.concurrency_limit -
                     (SELECT COUNT(*) FROM v_send_truth v
                      WHERE v.channel_id = c.id AND v.effective_status = '投递中') AS spare
            FROM channels c
            ORDER BY c.role, c.id
            """
        ).fetchall()
    ]


def reconcile_snapshot(conn) -> dict:
    """轧差快照：所有计数同源于 v_send_truth，送达差额恒可对照归零。"""
    def cnt(status: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) c FROM v_send_truth WHERE effective_status=?", (status,)
        ).fetchone()["c"]

    receipt_total = conn.execute("SELECT COUNT(*) c FROM receipts").fetchone()["c"]
    receipt_ok = conn.execute("SELECT COUNT(*) c FROM receipts WHERE result='成功'").fetchone()["c"]
    receipt_fail = receipt_total - receipt_ok
    delivered = cnt("已送达")
    # 真源一致性：投影已送达数与“最近回执为重放成功”的单数必须相等。
    replayed_delivered = conn.execute(
        "SELECT COUNT(*) c FROM v_send_truth WHERE latest_result='成功'"
    ).fetchone()["c"]
    drift = conn.execute(
        "SELECT COUNT(*) c FROM v_send_truth WHERE projection_drift=1"
    ).fetchone()["c"]
    missing = conn.execute(
        """
        SELECT COUNT(*) c FROM v_send_truth
        WHERE latest_receipt_id IS NULL AND status IN ('投递中', '已切备', '已失败')
        """
    ).fetchone()["c"]
    return {
        "投递申请数": conn.execute("SELECT COUNT(*) c FROM sends").fetchone()["c"],
        "已送达": delivered,
        "已失败": cnt("已失败"),
        "在途投递中": cnt("投递中"),
        "已切备在途": cnt("已切备"),
        "待投递": cnt("待投递"),
        "回执条数": receipt_total,
        "回执成功": receipt_ok,
        "回执失败": receipt_fail,
        "送达与回执差": delivered - replayed_delivered,
        "投影偏差单数": drift,
        "缺回执待终结": missing,
    }
