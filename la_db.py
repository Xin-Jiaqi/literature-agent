#!/usr/bin/env python3
"""literature-agent v1 数据层：schema v1 migration + 记录辅助。纯 stdlib。"""
import json
import os
import sqlite3
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import DB_PATH as _CFG_DB_PATH
DB_PATH = os.environ.get("LITAGENT_DB", _CFG_DB_PATH)

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS work_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id TEXT NOT NULL,
  source TEXT NOT NULL,
  source_version TEXT DEFAULT '',
  title TEXT,
  abstract_hash TEXT,
  metadata_hash TEXT,
  seen_at TEXT,
  content_changed INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_work_versions_uniq ON work_versions(work_id, source, source_version);

CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT,
  url TEXT,
  sha256 TEXT,
  size INTEGER,
  status TEXT,
  detail TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_artifacts_work ON artifacts(work_id);

CREATE TABLE IF NOT EXISTS errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT,
  work_id TEXT,
  stage TEXT,
  message TEXT,
  retryable INTEGER DEFAULT 0,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS source_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  feed TEXT,
  run_id TEXT,
  ok INTEGER,
  items INTEGER,
  latency_ms INTEGER,
  error TEXT,
  fetched_at TEXT,
  new_works INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_source_runs_src ON source_runs(source, fetched_at);

CREATE TABLE IF NOT EXISTS dedup_review (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id_a TEXT NOT NULL,
  work_id_b TEXT NOT NULL,
  match_kind TEXT NOT NULL,
  score REAL,
  evidence TEXT,
  status TEXT DEFAULT 'pending',
  created_at TEXT,
  reviewed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup_review_uniq ON dedup_review(work_id_a, work_id_b, match_kind);
"""


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path=None, schema=True):
    db_path = db_path or os.environ.get("LITAGENT_DB") or DB_PATH
    conn = sqlite3.connect(db_path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    if schema:
        conn.executescript(SCHEMA_V1)
    return conn


def db_count(conn, table, where="1=1", args=()):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]


def record_source_run(conn, source, feed, run_id, ok, items, latency_ms, error, new_works=0, fetched_at=None):
    conn.execute(
        "INSERT INTO source_runs (source,feed,run_id,ok,items,latency_ms,error,fetched_at,new_works) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (source, feed or "", run_id or "", int(ok), int(items or 0), int(latency_ms or 0),
         (error or "")[:500], fetched_at or now_iso(), int(new_works or 0)))


def record_error(conn, source, work_id, stage, message, retryable=False):
    conn.execute("INSERT INTO errors (source,work_id,stage,message,retryable,created_at) VALUES (?,?,?,?,?,?)",
                 (source or "", work_id or "", stage or "", (message or "")[:1000], int(bool(retryable)), now_iso()))


def record_work_version(conn, work_id, source, source_version, title, abstract, metadata, content_changed=0, seen_at=None):
    """记录 work 在某个来源/版本的快照哈希（摘要+元数据）。"""
    import hashlib
    conn.execute(
        "INSERT OR REPLACE INTO work_versions (work_id,source,source_version,title,abstract_hash,metadata_hash,seen_at,content_changed) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (work_id, source or "", source_version or "", title or "",
         hashlib.sha256((abstract or "").encode("utf-8", "ignore")).hexdigest()[:16],
         hashlib.sha256(json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False).encode("utf-8", "ignore")).hexdigest()[:16],
         seen_at or now_iso(), int(content_changed or 0)))


def record_artifact(conn, work_id, kind, path, url, sha256, size, status, detail=""):
    conn.execute(
        "INSERT INTO artifacts (work_id,kind,path,url,sha256,size,status,detail,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (work_id, kind or "", path or "", url or "", sha256 or "", size, status or "", (detail or "")[:500], now_iso()))


def record_dedup_review(conn, work_id_a, work_id_b, match_kind, score, evidence):
    conn.execute(
        "INSERT OR IGNORE INTO dedup_review (work_id_a,work_id_b,match_kind,score,evidence,status,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (work_id_a, work_id_b, match_kind, float(score), (evidence or "")[:500], "pending", now_iso()))


def sync_reading_status(conn):
    """根据现有数据初始化/同步 works.reading_status（v1 阅读状态机）。"""
    from collections import Counter
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(works)")]
    if "reading_status" not in cols:
        conn.execute("ALTER TABLE works ADD COLUMN reading_status TEXT DEFAULT 'discovered'")
    updates = [
        ("""UPDATE works SET reading_status='queued' WHERE queued=1
            AND reading_status IN ('discovered','screened')""", None),
        ("""UPDATE works SET reading_status='screened' WHERE queued=0
            AND reading_status IN ('discovered','queued')
            AND EXISTS (SELECT 1 FROM screenings s WHERE s.work_id=works.work_id)""", None),
        ("""UPDATE works SET reading_status='a_completed'
            WHERE reading_status IN ('discovered','queued','screened','a_queued','A_created')
            AND EXISTS (SELECT 1 FROM processing p
                        WHERE p.work_id=works.work_id AND p.a_status IN ('ok','completed'))""", None),
    ]
    for sql, args in updates:
        conn.execute(sql, args or ())
    conn.commit()
    return dict(conn.execute("SELECT reading_status, COUNT(*) FROM works GROUP BY reading_status").fetchall())


def db_stats(conn):
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(works)")]
    if "reading_status" not in cols:
        sync_reading_status(conn)
    return {
        "works": db_count(conn, "works"),
        "work_sources": db_count(conn, "work_sources"),
        "screenings": db_count(conn, "screenings"),
        "processing": db_count(conn, "processing"),
        "work_versions": db_count(conn, "work_versions"),
        "artifacts": db_count(conn, "artifacts"),
        "errors": db_count(conn, "errors"),
        "source_runs": db_count(conn, "source_runs"),
        "dedup_review_pending": db_count(conn, "dedup_review", "status='pending'"),
        "grades": dict(conn.execute("SELECT grade, COUNT(*) FROM screenings GROUP BY grade").fetchall()),
        "status": dict(conn.execute("SELECT status, COUNT(*) FROM works GROUP BY status").fetchall()),
        "reading_status": dict(conn.execute("SELECT reading_status, COUNT(*) FROM works GROUP BY reading_status").fetchall()),
    }
