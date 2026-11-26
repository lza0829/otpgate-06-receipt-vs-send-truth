"""存量库迁移验证：模拟旧版双账数据（旧表无 seq/投影列），启动后须回填序号并重放纠偏。"""
import os
import sqlite3
import sys
import tempfile

tmp = tempfile.mkdtemp(prefix="otpgate-mig-")
db_path = os.path.join(tmp, "old.db")
os.environ["OTPGATE_DB"] = db_path

# 1. 用旧 schema 造一个拧花存量库：回执成功但单态停在投递中；另有无回执被强造已送达。
conn = sqlite3.connect(db_path)
conn.executescript(
    """
    CREATE TABLE channels (
      id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT, role TEXT,
      weight INTEGER DEFAULT 100, status TEXT DEFAULT '启用',
      concurrency_limit INTEGER DEFAULT 20, fail_threshold INTEGER DEFAULT 3,
      consecutive_fails INTEGER DEFAULT 0);
    CREATE TABLE route_policy (id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT, updated_at TEXT);
    CREATE TABLE sends (
      id INTEGER PRIMARY KEY AUTOINCREMENT, serial TEXT UNIQUE, phone_mask TEXT,
      scene TEXT DEFAULT '登录', status TEXT, channel_id INTEGER,
      failover_channel_id INTEGER, note TEXT DEFAULT '',
      created_at TEXT, sent_at TEXT, finished_at TEXT);
    CREATE TABLE receipts (
      id INTEGER PRIMARY KEY AUTOINCREMENT, send_id INTEGER, channel_id INTEGER,
      result TEXT, vendor_code TEXT DEFAULT '', created_at TEXT);
    CREATE TABLE break_events (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER,
      action TEXT, send_id INTEGER, created_at TEXT, note TEXT DEFAULT '');
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT,
      ref_id INTEGER, action TEXT, created_at TEXT, note TEXT DEFAULT '');
    INSERT INTO channels(code,name,role,weight,status,concurrency_limit,fail_threshold,consecutive_fails)
      VALUES ('ali','阿里主','主路',80,'启用',30,3,0),('tx','腾讯备','备路',20,'启用',20,3,0);
    INSERT INTO route_policy(id,mode,updated_at) VALUES (1,'权重','2026-01-01 00:00:00');
    INSERT INTO sends(serial,phone_mask,status,channel_id,created_at)
      VALUES ('验OLD1','138****1','投递中',1,'2026-01-01 10:00:00'),
             ('验OLD2','138****2','已送达',2,'2026-01-01 10:01:00');
    INSERT INTO receipts(send_id,channel_id,result,vendor_code,created_at)
      VALUES (1,1,'成功','OK','2026-01-01 10:00:05');
    """
)
conn.commit()
conn.close()

sys.path.insert(0, "/workspace/backend")
from app.seed import seed_if_empty  # noqa: E402
from app.db import db  # noqa: E402

# 2. 启动迁移（init_db + backfill_truth）
seed_if_empty()

with db() as c:
    rows = {r["id"]: dict(r) for r in c.execute("SELECT * FROM v_send_truth").fetchall()}
    r1, r2 = rows[1], rows[2]

    assert r1["effective_status"] == "已送达", r1
    assert r1["status"] == "已送达", "拧花单态未被重放纠回"
    assert r1["projection_drift"] == 0
    assert r1["deliver_seq"] is not None and r1["delivered_at"] == "2026-01-01 10:00:05"
    # 旧回执已补全局序号
    assert c.execute("SELECT seq FROM receipts WHERE send_id=1").fetchone()["seq"] == 1
    # 无回执强造已送达被拉回
    assert r2["effective_status"] == "待投递" and r2["projection_drift"] == 0, r2
    # 轧差自洽
    drift = c.execute("SELECT COUNT(*) c FROM v_send_truth WHERE projection_drift=1").fetchone()["c"]
    assert drift == 0
print("MIGRATION OK: 旧库序号已回填，拧花单态与强造终态均重放纠回，投影偏差=0")
