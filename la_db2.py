#!/usr/bin/env python3
"""literature-agent V2 数据层：schema v2 增量迁移（无损）+ 新表辅助。

迁移只 ADD TABLE / ADD COLUMN，不重建 v1 表，保证现有 994 works 无损。
"""
import json
import os
import sqlite3
import time

from la_v2common import now_iso, read_json, write_json

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import DB_PATH as _CFG_DB_PATH, GOLD_ROOT as _CFG_GOLD_ROOT
DB_PATH = os.environ.get("LITAGENT_DB", _CFG_DB_PATH)
FEEDBACK_PATH = os.environ.get("LITAGENT_FEEDBACK",
                               os.path.join(os.path.dirname(_CFG_DB_PATH), "queues", "feedback.jsonl"))

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS tracked_entities (
  id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  aliases TEXT DEFAULT '[]',
  openalex_author_id TEXT,
  orcid TEXT,
  affiliation TEXT,
  priority INTEGER DEFAULT 2,
  topics TEXT DEFAULT '[]',
  reason TEXT,
  confidence TEXT DEFAULT 'medium',
  resolution_evidence TEXT DEFAULT '{}',
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS citation_seeds (
  seed_id TEXT PRIMARY KEY,
  work_id TEXT,
  title TEXT,
  doi TEXT,
  arxiv_id TEXT,
  kind TEXT DEFAULT 'core',
  note TEXT,
  added_at TEXT
);

CREATE TABLE IF NOT EXISTS citation_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  seed_work_id TEXT NOT NULL,
  citing_work_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  source TEXT,
  detected_at TEXT,
  UNIQUE(seed_work_id, citing_work_id, relation)
);

CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id TEXT NOT NULL,
  action TEXT NOT NULL,
  grade TEXT,
  note TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_work ON feedback(work_id);

CREATE TABLE IF NOT EXISTS coverage_gold (
  gold_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  year INTEGER,
  doi TEXT,
  arxiv_id TEXT,
  authors TEXT DEFAULT '[]',
  expected_importance TEXT,
  topic TEXT,
  why_should_be_found TEXT,
  known_source TEXT,
  known_first_date TEXT,
  zotero_context TEXT,
  added_at TEXT
);

CREATE TABLE IF NOT EXISTS recommendation_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id TEXT NOT NULL,
  signal_type TEXT NOT NULL,
  source TEXT,
  detail_json TEXT DEFAULT '{}',
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_work ON recommendation_signals(work_id);

CREATE TABLE IF NOT EXISTS overview_syncs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  overview_date TEXT NOT NULL,
  work_id TEXT NOT NULL,
  disposition TEXT,
  chatgpt_deep_read INTEGER DEFAULT 0,
  chatgpt_a INTEGER DEFAULT 0,
  competitor INTEGER DEFAULT 0,
  save_zotero INTEGER DEFAULT 0,
  user_note TEXT,
  direction_feedback TEXT,
  conflict INTEGER DEFAULT 0,
  synced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_overview_syncs ON overview_syncs(overview_date, work_id);

CREATE TABLE IF NOT EXISTS overview_entries (
  work_id TEXT PRIMARY KEY,
  first_overview_date TEXT NOT NULL,
  first_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_overview_entries_date ON overview_entries(first_overview_date);

CREATE TABLE IF NOT EXISTS workflow_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  detail_json TEXT DEFAULT '{}',
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_events_work ON workflow_events(work_id, created_at);
"""


def migrate(conn):
    conn.executescript(SCHEMA_V2)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(works)")}
    if "historical_discovery" not in cols:
        conn.execute("ALTER TABLE works ADD COLUMN historical_discovery INTEGER DEFAULT 0")
    if "signal_grade" not in cols:
        conn.execute("ALTER TABLE works ADD COLUMN signal_grade TEXT DEFAULT ''")
    if "rank_score" not in cols:
        conn.execute("ALTER TABLE works ADD COLUMN rank_score REAL DEFAULT 0")
    if "reading_status" not in cols:
        conn.execute("ALTER TABLE works ADD COLUMN reading_status TEXT DEFAULT 'discovered'")
    priority_map = {"S": "MUST", "A": "WEEKLY", "B": "RADAR", "C": "DROP"}
    for old, new in priority_map.items():
        conn.execute("UPDATE screenings SET grade=? WHERE grade=?", (new, old))
        conn.execute("UPDATE feedback SET grade=? WHERE grade=?", (new, old))
        conn.execute("UPDATE coverage_gold SET expected_importance=? WHERE expected_importance=?", (new, old))
        if "signal_grade" in cols:
            conn.execute("UPDATE works SET signal_grade=? WHERE signal_grade=?", (new, old))
    conn.commit()


def connect(db_path=None):
    db_path = db_path or os.environ.get("LITAGENT_DB") or DB_PATH
    conn = sqlite3.connect(db_path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    import literature_agent as la
    conn.executescript(la.SCHEMA)
    conn.executescript(la.LA_SCHEMA_V1)
    migrate(conn)
    return conn


def record_signal(conn, work_id, signal_type, source, detail=None):
    dj = json.dumps(detail or {}, ensure_ascii=False)
    dup = conn.execute(
        "SELECT 1 FROM recommendation_signals WHERE work_id=? AND signal_type=? AND source=? AND detail_json=?",
        (work_id, signal_type, source or "", dj)).fetchone()
    if dup:
        return False
    conn.execute(
        "INSERT INTO recommendation_signals (work_id,signal_type,source,detail_json,created_at) VALUES (?,?,?,?,?)",
        (work_id, signal_type, source or "", dj, now_iso()))
    conn.commit()
    return True


def get_signals(conn, work_id):
    return conn.execute(
        "SELECT signal_type,source,detail_json,created_at FROM recommendation_signals WHERE work_id=? ORDER BY id",
        (work_id,)).fetchall()


def record_feedback(conn, work_id, action, grade=None, note=""):
    conn.execute("INSERT INTO feedback (work_id,action,grade,note,created_at) VALUES (?,?,?,?,?)",
                 (work_id, action, grade, note, now_iso()))
    conn.commit()
    return True


def record_workflow_event(conn, work_id, event_type, from_status="", to_status="", detail=None):
    conn.execute(
        "INSERT INTO workflow_events (work_id,event_type,from_status,to_status,detail_json,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (work_id, event_type, from_status or "", to_status or "",
         json.dumps(detail or {}, ensure_ascii=False), now_iso()),
    )
    conn.commit()
    return True


def add_tracked_entity(conn, ent):
    conn.execute(
        "INSERT OR REPLACE INTO tracked_entities "
        "(id,canonical_name,aliases,openalex_author_id,orcid,affiliation,priority,topics,reason,confidence,resolution_evidence,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (ent["id"], ent["canonical_name"], json.dumps(ent.get("aliases", []), ensure_ascii=False),
         ent.get("openalex_author_id") or "", ent.get("orcid") or "", ent.get("affiliation") or "",
         int(ent.get("priority", 2)), json.dumps(ent.get("topics", []), ensure_ascii=False),
         ent.get("reason") or "", ent.get("confidence", "medium"),
         json.dumps(ent.get("resolution_evidence", {}), ensure_ascii=False), now_iso()))
    conn.commit()


def tracked_entities(conn):
    return conn.execute("SELECT * FROM tracked_entities ORDER BY priority, canonical_name").fetchall()


def add_seed(conn, seed):
    conn.execute("INSERT OR REPLACE INTO citation_seeds (seed_id,work_id,title,doi,arxiv_id,kind,note,added_at) "
                 "VALUES (?,?,?,?,?,?,?,?)",
                 (seed["seed_id"], seed.get("work_id", ""), seed.get("title", ""), seed.get("doi", ""),
                  seed.get("arxiv_id", ""), seed.get("kind", "core"), seed.get("note", ""), now_iso()))
    conn.commit()


def add_edge(conn, seed_work_id, citing_work_id, relation, source):
    try:
        conn.execute("INSERT OR IGNORE INTO citation_edges (seed_work_id,citing_work_id,relation,source,detected_at) "
                     "VALUES (?,?,?,?,?)", (seed_work_id, citing_work_id, relation, source or "", now_iso()))
        conn.commit()
    except sqlite3.IntegrityError:
        pass


def load_coverage_gold():
    from la_priority import normalize_priority
    path = os.path.join(_CFG_GOLD_ROOT, "coverage_gold.jsonl")
    out = []
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                try:
                    item = json.loads(ln)
                    item["expected_importance"] = normalize_priority(item.get("expected_importance"))
                    out.append(item)
                except Exception:
                    continue
    return out


def write_coverage_gold(entries):
    path = os.path.join(_CFG_GOLD_ROOT, "coverage_gold.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def load_feedback_jsonl():
    path = FEEDBACK_PATH
    out = []
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
    return out


def append_feedback_jsonl(rec):
    path = FEEDBACK_PATH
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
