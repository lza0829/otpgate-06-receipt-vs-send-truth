from __future__ import annotations
import importlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

ROUTER_MODS = ["audit", "breaker", "channels", "dashboard", "receipts",
               "reconcile", "route", "sends", "settings"]


def _fresh_app(db_path: str):
    """按给定库路径重新装配整套 app，保证用例间库与状态完全隔离。"""
    os.environ["OTPGATE_DB"] = db_path
    import app.config
    importlib.reload(app.config)
    import app.db
    importlib.reload(app.db)
    import app.services.common
    importlib.reload(app.services.common)
    import app.services.delivery
    importlib.reload(app.services.delivery)
    for name in ROUTER_MODS:
        mod = importlib.import_module(f"app.routers.{name}")
        importlib.reload(mod)
    import app.seed
    importlib.reload(app.seed)
    import app.main
    importlib.reload(app.main)
    return app.main.app, app.db


@pytest.fixture()
def env(tmp_path):
    app, db_mod = _fresh_app(str(tmp_path / "case.db"))
    return TestClient(app), db_mod


def _new_send(client) -> int:
    r = client.post("/api/投递单", json={"手机掩码": "139****0000", "场景": "登录"})
    assert r.status_code == 200, r.text
    return r.json()["编号"]


def _dispatch(client, sid: int):
    return client.post(f"/api/投递单/{sid}/投递")


def _receipt(client, sid: int, result: str, key: str = ""):
    return client.post(
        f"/api/投递单/{sid}/回执",
        json={"结果": result, "厂商码": "OK", "幂等键": key},
    )


def _truth_row(db_mod, sid: int) -> dict:
    with db_mod.db() as conn:
        return dict(conn.execute(
            "SELECT * FROM v_send_truth WHERE id=?", (sid,)
        ).fetchone())


def test_1_回执与单态同笔成败且轧差同源(env):
    client, db_mod = env
    sid = _new_send(client)
    assert _dispatch(client, sid).status_code == 200
    rec = client.get("/api/对账").json()
    assert rec["已送达"] == 0 and rec["在途投递中"] >= 1

    r = _receipt(client, sid, "成功")
    assert r.status_code == 200
    seq = r.json()["序号"]

    row = _truth_row(db_mod, sid)
    assert row["effective_status"] == "已送达"
    assert row["status"] == "已送达"           # 投影同笔已刷
    assert row["projection_drift"] == 0
    assert row["deliver_seq"] == seq           # 送达序号=回执事件序号
    assert row["delivered_at"] == row["terminal_at"]

    rec = client.get("/api/对账").json()
    assert rec["送达与回执差"] == 0
    assert rec["投影偏差单数"] == 0
    ov = client.get("/api/总览").json()
    assert ov["分布"]["已送达"] == rec["已送达"]
    book = client.get("/api/回执").json()
    assert book["汇总"]["成功条数"] >= 1
    assert any(x["投递单编号"] == sid and x["重放终态"] == "已送达" for x in book["列表"])


def test_2_缺回执不强造终态且纠偏位显形(env):
    client, db_mod = env
    sid = _new_send(client)
    _dispatch(client, sid)
    row = _truth_row(db_mod, sid)
    assert row["effective_status"] == "投递中" and row["latest_receipt_id"] is None

    r = client.post(f"/api/投递单/{sid}/失败", json={"备注": "通道抖动"})
    assert r.status_code == 200
    row = _truth_row(db_mod, sid)
    assert row["effective_status"] == "已失败" and row["latest_receipt_id"] is None
    rec = client.get("/api/对账").json()
    assert rec["缺回执待终结"] >= 1

    r = _receipt(client, sid, "成功")
    assert r.status_code == 200
    row = _truth_row(db_mod, sid)
    assert row["effective_status"] == "已送达" and row["latest_receipt_id"] is not None


def test_3_并发补回执串行化且只留一个终态(env):
    client, db_mod = env
    sid = _new_send(client)
    _dispatch(client, sid)

    def fire(i):
        return _receipt(client, sid, "成功", key=f"k-{i}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fire, range(8)))
    assert all(r.status_code == 200 for r in results)
    seqs = {r.json()["序号"] for r in results}
    assert len(seqs) == 8  # 无丢号、无重号

    with db_mod.db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM receipts WHERE send_id=?", (sid,)).fetchone()["c"]
    assert n == 8
    row = _truth_row(db_mod, sid)
    assert row["effective_status"] == "已送达"
    assert row["projection_drift"] == 0


def test_4_半截拧花可纠到真源(env):
    client, db_mod = env
    sid = _new_send(client)
    _dispatch(client, sid)
    r = _receipt(client, sid, "成功")
    good_seq = r.json()["序号"]
    with db_mod.db() as conn:
        conn.execute("UPDATE sends SET status='投递中', deliver_seq=NULL WHERE id=?", (sid,))
    row = _truth_row(db_mod, sid)
    assert row["effective_status"] == "已送达" and row["projection_drift"] == 1

    rep = client.post("/api/对账/纠偏重放")
    assert rep.status_code == 200
    assert rep.json()["残余投影偏差"] == 0
    row = _truth_row(db_mod, sid)
    assert row["status"] == "已送达" and row["deliver_seq"] == good_seq

    sid2 = _new_send(client)
    with db_mod.db() as conn:
        conn.execute("UPDATE sends SET status='已送达' WHERE id=?", (sid2,))
    client.post("/api/对账/纠偏重放")
    row2 = _truth_row(db_mod, sid2)
    assert row2["projection_drift"] == 0
    assert row2["effective_status"] == "待投递"  # 缺回执绝不保留强造的已送达


def test_5_晚到回执改写独立准入且撤旧序号(env):
    client, db_mod = env
    sid = _new_send(client)
    _dispatch(client, sid)
    _receipt(client, sid, "成功")
    assert _truth_row(db_mod, sid)["effective_status"] == "已送达"
    _receipt(client, sid, "失败")
    row = _truth_row(db_mod, sid)
    assert row["effective_status"] == "已失败"
    assert row["deliver_seq"] is None and row["delivered_at"] is None

    client.put("/api/设置", json={"项": {"回执晚到可改写": "否"}})
    r = _receipt(client, sid, "成功")
    assert r.status_code == 409
    assert _truth_row(db_mod, sid)["effective_status"] == "已失败"
    client.put("/api/设置", json={"项": {"回执晚到可改写": "是"}})


def test_6_手改单态与回执真源互斥(env):
    client, db_mod = env
    sid = _new_send(client)
    _dispatch(client, sid)
    _receipt(client, sid, "成功")
    r = client.post(f"/api/投递单/{sid}/纠正", json={"目标状态": "投递中"})
    assert r.status_code == 409
    assert _truth_row(db_mod, sid)["effective_status"] == "已送达"

    sid2 = _new_send(client)
    r = client.post(f"/api/投递单/{sid2}/纠正", json={"目标状态": "已送达"})
    assert r.status_code == 400  # 无回执不得手造已送达
    r = client.post(f"/api/投递单/{sid2}/纠正", json={"目标状态": "已失败"})
    assert r.status_code == 200
    assert _truth_row(db_mod, sid2)["effective_status"] == "已失败"


def test_7_占用余量探针同走重放(env):
    client, _ = env
    sid = _new_send(client)
    _dispatch(client, sid)
    ch = client.get("/api/通道").json()[0]
    assert ch["在途占用"] >= 1
    assert ch["占用余量"] == ch["并发上限"] - ch["在途占用"]
    _receipt(client, sid, "成功")
    ch2 = client.get("/api/通道").json()[0]
    assert ch2["在途占用"] == ch["在途占用"] - 1
    ov = client.get("/api/总览").json()
    assert ov["通道"][0]["占用余量"] == ch2["占用余量"]


def test_8_幂等回执不重复入账(env):
    client, db_mod = env
    sid = _new_send(client)
    _dispatch(client, sid)
    a = _receipt(client, sid, "成功", key="idem-1")
    b = _receipt(client, sid, "成功", key="idem-1")
    assert a.json()["幂等"] is False and b.json()["幂等"] is True
    with db_mod.db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) c FROM receipts WHERE send_id=? AND idempotency_key='idem-1'",
            (sid,),
        ).fetchone()["c"]
    assert n == 1


def test_9_事件痕可按类别与投递单过滤(env):
    client, _ = env
    sid = _new_send(client)
    _dispatch(client, sid)
    _receipt(client, sid, "成功")
    rows = client.get("/api/审计", params={"类别": "回执", "关联": sid}).json()
    assert rows and all(r["类别"] == "回执" and r["关联"] == sid for r in rows)


def test_10_真源裁决键只读(env):
    client, _ = env
    r = client.put("/api/设置", json={"项": {"送达结果真源": "我想改成单态状态机"}})
    assert r.status_code == 409
    assert "回执簿" in client.get("/api/设置").json()["送达结果真源"]
