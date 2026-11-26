from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

# 送达结果真源（全局唯一）：回执簿送达事件重放。
# receipts 是只追加的送达事件流（seq 为事件序号），sends.status 只是它的投影缓存；
# v_send_truth 是唯一的重放读模型，所有读面（验码单终态/回执簿/路况/轧差/占用余量）都从它取数。
# 基础表结构（全部 IF NOT EXISTS，旧库安全）。
BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('主路','备路')),
  weight INTEGER NOT NULL DEFAULT 100,
  status TEXT NOT NULL DEFAULT '启用' CHECK(status IN ('启用','熔断中','停用')),
  concurrency_limit INTEGER NOT NULL DEFAULT 20,
  fail_threshold INTEGER NOT NULL DEFAULT 3,
  consecutive_fails INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS route_policy (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  mode TEXT NOT NULL DEFAULT '权重' CHECK(mode IN ('权重','强制主路','强制备路')),
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sends (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serial TEXT NOT NULL UNIQUE,
  phone_mask TEXT NOT NULL,
  scene TEXT NOT NULL DEFAULT '登录',
  status TEXT NOT NULL DEFAULT '待投递'
    CHECK(status IN ('待投递','投递中','已送达','已失败','已切备')),
  channel_id INTEGER,
  failover_channel_id INTEGER,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  sent_at TEXT,
  finished_at TEXT,
  deliver_seq INTEGER,
  delivered_at TEXT,
  FOREIGN KEY(channel_id) REFERENCES channels(id),
  FOREIGN KEY(failover_channel_id) REFERENCES channels(id)
);
CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  send_id INTEGER NOT NULL,
  channel_id INTEGER NOT NULL,
  result TEXT NOT NULL CHECK(result IN ('成功','失败')),
  vendor_code TEXT NOT NULL DEFAULT '',
  seq INTEGER,
  idempotency_key TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(send_id) REFERENCES sends(id),
  FOREIGN KEY(channel_id) REFERENCES channels(id)
);
CREATE TABLE IF NOT EXISTS break_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('熔断','恢复','切备')),
  send_id INTEGER,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(channel_id) REFERENCES channels(id),
  FOREIGN KEY(send_id) REFERENCES sends(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
"""

# 依赖迁移列的索引与唯一重放读模型，必须在补列之后创建。
OBJECTS_SCHEMA = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_receipts_seq ON receipts(seq);
CREATE UNIQUE INDEX IF NOT EXISTS idx_receipts_idem
  ON receipts(send_id, idempotency_key) WHERE idempotency_key <> '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_sends_deliver_seq ON sends(deliver_seq);

-- 单一重放读模型：一笔送达是否终态、终态是什么，只由 receipts 事件流决定。
-- 有回执：按事件序号倒序取最近一条为重放结果（晚到改写是独立准入策略，不改这里的规则）；
-- 无回执：维持状态机原值（待投递/投递中/已切备/已失败），缺回执绝不强造送达终态。
CREATE VIEW IF NOT EXISTS v_send_truth AS
SELECT s.*,
  (SELECT r.seq FROM receipts r WHERE r.send_id = s.id ORDER BY r.seq DESC, r.id DESC LIMIT 1)
    AS latest_receipt_seq,
  (SELECT r.id FROM receipts r WHERE r.send_id = s.id ORDER BY r.seq DESC, r.id DESC LIMIT 1)
    AS latest_receipt_id,
  (SELECT r.result FROM receipts r WHERE r.send_id = s.id ORDER BY r.seq DESC, r.id DESC LIMIT 1)
    AS latest_result,
  (SELECT r.created_at FROM receipts r WHERE r.send_id = s.id ORDER BY r.seq DESC, r.id DESC LIMIT 1)
    AS terminal_at,
  CASE
    WHEN EXISTS (SELECT 1 FROM receipts r WHERE r.send_id = s.id)
    THEN CASE
      (SELECT r.result FROM receipts r WHERE r.send_id = s.id ORDER BY r.seq DESC, r.id DESC LIMIT 1)
      WHEN '成功' THEN '已送达'
      ELSE '已失败'
    END
    ELSE s.status
  END AS effective_status,
  CASE WHEN s.status =
    (CASE
      WHEN EXISTS (SELECT 1 FROM receipts r WHERE r.send_id = s.id)
      THEN CASE
        (SELECT r.result FROM receipts r WHERE r.send_id = s.id ORDER BY r.seq DESC, r.id DESC LIMIT 1)
        WHEN '成功' THEN '已送达'
        ELSE '已失败'
      END
      ELSE s.status
    END) THEN 0 ELSE 1 END AS projection_drift,
  EXISTS (SELECT 1 FROM receipts r WHERE r.send_id = s.id AND r.result = '成功') AS ever_delivered
FROM sends s
"""

_ADDED_COLUMNS = {
    "sends": [
        ("deliver_seq", "ALTER TABLE sends ADD COLUMN deliver_seq INTEGER"),
        ("delivered_at", "ALTER TABLE sends ADD COLUMN delivered_at TEXT"),
    ],
    "receipts": [
        ("seq", "ALTER TABLE receipts ADD COLUMN seq INTEGER"),
        ("idempotency_key", "ALTER TABLE receipts ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''"),
    ],
}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # 写并发靠库级锁串行，busy_timeout 让并发补回执等待而不是立刻报 locked。
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = _existing_columns(conn, table)
        for name, ddl in columns:
            if name not in existing:
                conn.execute(ddl)


def init_db() -> None:
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        # 顺序：建基础表（IF NOT EXISTS，旧库不动）→ 补迁移列 → 最后建依赖新列的索引与真源视图。
        conn.executescript(BASE_SCHEMA)
        _migrate(conn)
        conn.execute("DROP VIEW IF EXISTS v_send_truth")
        conn.executescript(OBJECTS_SCHEMA)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
