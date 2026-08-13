#!/usr/bin/env python3
"""Multi-user product data boundary for the campus pilot.

Public paper facts stay in ``works``. Every private record carries an owner and
all access goes through owner-scoped helpers. This module is intentionally
stdlib-only so the model can be exercised with SQLite before the web service is
moved to PostgreSQL.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone


PRIORITIES = ("MUST", "WEEKLY", "RADAR", "DROP")
DISPOSITIONS = ("read", "later", "ignore")
ASSET_KINDS = ("pdf", "a_note", "b_note", "c_note", "d_output")
USAGE_KINDS = ("screen", "a_read", "embedding", "ocr")

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
_WORK_ID_RE = re.compile(r"^[^\x00-\x20\x7f]{1,512}$")


class ValidationError(ValueError):
    """Input is missing or outside the product contract."""


class OwnershipError(PermissionError):
    """The authenticated user does not own the requested private resource."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS product_users (
  user_id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('invited', 'active', 'disabled', 'deleted')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_projects (
  project_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES product_users(user_id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  daily_attention_limit INTEGER NOT NULL DEFAULT 3
    CHECK (daily_attention_limit BETWEEN 1 AND 20),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'archived')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (project_id, owner_user_id)
);
CREATE INDEX IF NOT EXISTS idx_projects_owner
  ON research_projects(owner_user_id, status);

CREATE TABLE IF NOT EXISTS research_profiles (
  project_id TEXT PRIMARY KEY REFERENCES research_projects(project_id) ON DELETE CASCADE,
  owner_user_id TEXT NOT NULL REFERENCES product_users(user_id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  include_terms_json TEXT NOT NULL DEFAULT '[]',
  exclude_terms_json TEXT NOT NULL DEFAULT '[]',
  revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  confirmed_at TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (project_id, owner_user_id)
    REFERENCES research_projects(project_id, owner_user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_profiles_owner
  ON research_profiles(owner_user_id);

CREATE TABLE IF NOT EXISTS project_seeds (
  project_id TEXT NOT NULL REFERENCES research_projects(project_id) ON DELETE CASCADE,
  owner_user_id TEXT NOT NULL REFERENCES product_users(user_id) ON DELETE CASCADE,
  work_id TEXT NOT NULL REFERENCES works(work_id) ON DELETE RESTRICT,
  source TEXT NOT NULL DEFAULT 'manual'
    CHECK (source IN ('manual', 'bibtex', 'ris', 'zotero')),
  added_at TEXT NOT NULL,
  PRIMARY KEY (project_id, work_id),
  FOREIGN KEY (project_id, owner_user_id)
    REFERENCES research_projects(project_id, owner_user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_project_seeds_owner
  ON project_seeds(owner_user_id, project_id);

CREATE TABLE IF NOT EXISTS user_recommendations (
  recommendation_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES product_users(user_id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES research_projects(project_id) ON DELETE CASCADE,
  work_id TEXT NOT NULL REFERENCES works(work_id) ON DELETE RESTRICT,
  recommendation_date TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('MUST', 'WEEKLY', 'RADAR', 'DROP')),
  reason TEXT NOT NULL DEFAULT '',
  disposition TEXT CHECK (disposition IS NULL OR disposition IN ('read', 'later', 'ignore')),
  user_note TEXT NOT NULL DEFAULT '',
  decided_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (owner_user_id, project_id, work_id, recommendation_date),
  FOREIGN KEY (project_id, owner_user_id)
    REFERENCES research_projects(project_id, owner_user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_recommendations_owner_date
  ON user_recommendations(owner_user_id, recommendation_date, priority);

CREATE TABLE IF NOT EXISTS private_assets (
  asset_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES product_users(user_id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES research_projects(project_id) ON DELETE CASCADE,
  work_id TEXT REFERENCES works(work_id) ON DELETE RESTRICT,
  kind TEXT NOT NULL CHECK (kind IN ('pdf', 'a_note', 'b_note', 'c_note', 'd_output')),
  object_key TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL DEFAULT '',
  size_bytes INTEGER NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
  created_at TEXT NOT NULL,
  FOREIGN KEY (project_id, owner_user_id)
    REFERENCES research_projects(project_id, owner_user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_private_assets_owner
  ON private_assets(owner_user_id, project_id, work_id);

CREATE TABLE IF NOT EXISTS usage_events (
  usage_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL REFERENCES product_users(user_id) ON DELETE CASCADE,
  project_id TEXT REFERENCES research_projects(project_id) ON DELETE SET NULL,
  work_id TEXT REFERENCES works(work_id) ON DELETE SET NULL,
  kind TEXT NOT NULL CHECK (kind IN ('screen', 'a_read', 'embedding', 'ocr')),
  provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  input_units INTEGER NOT NULL DEFAULT 0 CHECK (input_units >= 0),
  output_units INTEGER NOT NULL DEFAULT 0 CHECK (output_units >= 0),
  estimated_cost_micros INTEGER NOT NULL DEFAULT 0 CHECK (estimated_cost_micros >= 0),
  status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'failed', 'cancelled')),
  created_at TEXT NOT NULL,
  FOREIGN KEY (project_id, owner_user_id)
    REFERENCES research_projects(project_id, owner_user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_usage_owner_created
  ON usage_events(owner_user_id, created_at);

-- Additive guards also protect databases that created an earlier pilot draft
-- before composite foreign keys were introduced.
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_id_owner
  ON research_projects(project_id, owner_user_id);

CREATE TRIGGER IF NOT EXISTS trg_profile_owner_insert
BEFORE INSERT ON research_profiles
WHEN NOT EXISTS (
  SELECT 1 FROM research_projects p
  WHERE p.project_id=NEW.project_id AND p.owner_user_id=NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'project owner mismatch'); END;

CREATE TRIGGER IF NOT EXISTS trg_profile_owner_update
BEFORE UPDATE OF project_id, owner_user_id ON research_profiles
WHEN NOT EXISTS (
  SELECT 1 FROM research_projects p
  WHERE p.project_id=NEW.project_id AND p.owner_user_id=NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'project owner mismatch'); END;

CREATE TRIGGER IF NOT EXISTS trg_seed_owner_insert
BEFORE INSERT ON project_seeds
WHEN NOT EXISTS (
  SELECT 1 FROM research_projects p
  WHERE p.project_id=NEW.project_id AND p.owner_user_id=NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'project owner mismatch'); END;

CREATE TRIGGER IF NOT EXISTS trg_seed_owner_update
BEFORE UPDATE OF project_id, owner_user_id ON project_seeds
WHEN NOT EXISTS (
  SELECT 1 FROM research_projects p
  WHERE p.project_id=NEW.project_id AND p.owner_user_id=NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'project owner mismatch'); END;

CREATE TRIGGER IF NOT EXISTS trg_recommendation_owner_insert
BEFORE INSERT ON user_recommendations
WHEN NOT EXISTS (
  SELECT 1 FROM research_projects p
  WHERE p.project_id=NEW.project_id AND p.owner_user_id=NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'project owner mismatch'); END;

CREATE TRIGGER IF NOT EXISTS trg_recommendation_owner_update
BEFORE UPDATE OF project_id, owner_user_id ON user_recommendations
WHEN NOT EXISTS (
  SELECT 1 FROM research_projects p
  WHERE p.project_id=NEW.project_id AND p.owner_user_id=NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'project owner mismatch'); END;

CREATE TRIGGER IF NOT EXISTS trg_asset_owner_insert
BEFORE INSERT ON private_assets
WHEN NOT EXISTS (
  SELECT 1 FROM research_projects p
  WHERE p.project_id=NEW.project_id AND p.owner_user_id=NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'project owner mismatch'); END;

CREATE TRIGGER IF NOT EXISTS trg_asset_owner_update
BEFORE UPDATE OF project_id, owner_user_id ON private_assets
WHEN NOT EXISTS (
  SELECT 1 FROM research_projects p
  WHERE p.project_id=NEW.project_id AND p.owner_user_id=NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'project owner mismatch'); END;

CREATE TRIGGER IF NOT EXISTS trg_usage_owner_insert
BEFORE INSERT ON usage_events
WHEN NEW.project_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM research_projects p
  WHERE p.project_id=NEW.project_id AND p.owner_user_id=NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'project owner mismatch'); END;

CREATE TRIGGER IF NOT EXISTS trg_usage_owner_update
BEFORE UPDATE OF project_id, owner_user_id ON usage_events
WHEN NEW.project_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM research_projects p
  WHERE p.project_id=NEW.project_id AND p.owner_user_id=NEW.owner_user_id
)
BEGIN SELECT RAISE(ABORT, 'project owner mismatch'); END;
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def _clean_id(value, field):
    value = (value or "").strip()
    if not _ID_RE.fullmatch(value):
        raise ValidationError(f"invalid {field}")
    return value


def _clean_text(value, field, minimum=1, maximum=4000):
    value = (value or "").strip()
    if not minimum <= len(value) <= maximum:
        raise ValidationError(f"{field} must be {minimum}-{maximum} characters")
    return value


def _clean_work_id(value):
    value = (value or "").strip()
    if not _WORK_ID_RE.fullmatch(value):
        raise ValidationError("invalid work_id")
    return value


def migrate(conn):
    """Install the additive pilot schema without changing personal-alpha data."""
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()


class ProductStore:
    """Owner-scoped repository; callers never query private tables directly."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        migrate(conn)

    def create_user(self, email, display_name="", user_id=None):
        email = (email or "").strip().lower()
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValidationError("invalid email")
        display_name = _clean_text(display_name, "display_name", minimum=0, maximum=100)
        user_id = _clean_id(user_id or _new_id("usr"), "user_id")
        now = now_iso()
        self.conn.execute(
            "INSERT INTO product_users "
            "(user_id,email,display_name,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (user_id, email, display_name, "active", now, now),
        )
        self.conn.commit()
        return user_id

    def create_project(self, owner_user_id, title, description, daily_attention_limit=3, project_id=None):
        owner_user_id = self._require_user(owner_user_id)
        title = _clean_text(title, "title", maximum=200)
        description = _clean_text(description, "description", maximum=8000)
        if not isinstance(daily_attention_limit, int) or not 1 <= daily_attention_limit <= 20:
            raise ValidationError("daily_attention_limit must be 1-20")
        project_id = _clean_id(project_id or _new_id("prj"), "project_id")
        now = now_iso()
        self.conn.execute(
            "INSERT INTO research_projects "
            "(project_id,owner_user_id,title,description,daily_attention_limit,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (project_id, owner_user_id, title, description, daily_attention_limit, "active", now, now),
        )
        self.conn.commit()
        return project_id

    def get_project(self, owner_user_id, project_id):
        owner_user_id = self._require_user(owner_user_id)
        project_id = _clean_id(project_id, "project_id")
        return self.conn.execute(
            "SELECT * FROM research_projects WHERE project_id=? AND owner_user_id=?",
            (project_id, owner_user_id),
        ).fetchone()

    def set_profile(self, owner_user_id, project_id, summary, include_terms=None, exclude_terms=None, confirmed=False):
        self._require_project(owner_user_id, project_id)
        summary = _clean_text(summary, "summary", maximum=12000)
        include_terms = self._clean_terms(include_terms or [], "include_terms")
        exclude_terms = self._clean_terms(exclude_terms or [], "exclude_terms")
        now = now_iso()
        current = self.conn.execute(
            "SELECT revision FROM research_profiles WHERE project_id=? AND owner_user_id=?",
            (project_id, owner_user_id),
        ).fetchone()
        revision = (current["revision"] + 1) if current else 1
        self.conn.execute(
            "INSERT INTO research_profiles "
            "(project_id,owner_user_id,summary,include_terms_json,exclude_terms_json,revision,confirmed_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET summary=excluded.summary, "
            "include_terms_json=excluded.include_terms_json, exclude_terms_json=excluded.exclude_terms_json, "
            "revision=excluded.revision, confirmed_at=excluded.confirmed_at, updated_at=excluded.updated_at",
            (project_id, owner_user_id, summary, json.dumps(include_terms, ensure_ascii=False),
             json.dumps(exclude_terms, ensure_ascii=False), revision, now if confirmed else None, now),
        )
        self.conn.commit()
        return revision

    def get_profile(self, owner_user_id, project_id):
        self._require_project(owner_user_id, project_id)
        return self.conn.execute(
            "SELECT * FROM research_profiles WHERE project_id=? AND owner_user_id=?",
            (project_id, owner_user_id),
        ).fetchone()

    def add_seed(self, owner_user_id, project_id, work_id, source="manual"):
        self._require_project(owner_user_id, project_id)
        work_id = self._require_work(work_id)
        if source not in ("manual", "bibtex", "ris", "zotero"):
            raise ValidationError("invalid seed source")
        self.conn.execute(
            "INSERT OR IGNORE INTO project_seeds "
            "(project_id,owner_user_id,work_id,source,added_at) VALUES (?,?,?,?,?)",
            (project_id, owner_user_id, work_id, source, now_iso()),
        )
        self.conn.commit()

    def list_seeds(self, owner_user_id, project_id):
        self._require_project(owner_user_id, project_id)
        return self.conn.execute(
            "SELECT s.*, w.title, w.doi, w.arxiv_id FROM project_seeds s "
            "JOIN works w ON w.work_id=s.work_id "
            "WHERE s.owner_user_id=? AND s.project_id=? ORDER BY s.added_at, s.work_id",
            (owner_user_id, project_id),
        ).fetchall()

    def add_recommendation(self, owner_user_id, project_id, work_id, recommendation_date, priority, reason=""):
        self._require_project(owner_user_id, project_id)
        work_id = self._require_work(work_id)
        if priority not in PRIORITIES:
            raise ValidationError("invalid recommendation priority")
        try:
            date.fromisoformat(recommendation_date)
        except (TypeError, ValueError):
            raise ValidationError("recommendation_date must be YYYY-MM-DD") from None
        reason = _clean_text(reason, "reason", minimum=0, maximum=4000)
        recommendation_id = _new_id("rec")
        self.conn.execute(
            "INSERT INTO user_recommendations "
            "(recommendation_id,owner_user_id,project_id,work_id,recommendation_date,priority,reason,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (recommendation_id, owner_user_id, project_id, work_id,
             recommendation_date, priority, reason, now_iso()),
        )
        self.conn.commit()
        return recommendation_id

    def decide(self, owner_user_id, recommendation_id, disposition, note=""):
        owner_user_id = self._require_user(owner_user_id)
        recommendation_id = _clean_id(recommendation_id, "recommendation_id")
        if disposition not in DISPOSITIONS:
            raise ValidationError("invalid disposition")
        note = _clean_text(note, "note", minimum=0, maximum=4000)
        cur = self.conn.execute(
            "UPDATE user_recommendations SET disposition=?, user_note=?, decided_at=? "
            "WHERE recommendation_id=? AND owner_user_id=?",
            (disposition, note, now_iso(), recommendation_id, owner_user_id),
        )
        if cur.rowcount != 1:
            self.conn.rollback()
            raise OwnershipError("recommendation not owned by user")
        self.conn.commit()

    def list_recommendations(self, owner_user_id, recommendation_date=None):
        owner_user_id = self._require_user(owner_user_id)
        sql = (
            "SELECT r.*, w.title, w.doi, w.arxiv_id FROM user_recommendations r "
            "JOIN works w ON w.work_id=r.work_id WHERE r.owner_user_id=?"
        )
        args = [owner_user_id]
        if recommendation_date:
            sql += " AND r.recommendation_date=?"
            args.append(recommendation_date)
        sql += " ORDER BY r.recommendation_date DESC, r.created_at, r.recommendation_id"
        return self.conn.execute(sql, args).fetchall()

    def add_private_asset(self, owner_user_id, project_id, kind, object_key, work_id=None,
                          sha256="", size_bytes=0):
        self._require_project(owner_user_id, project_id)
        if kind not in ASSET_KINDS:
            raise ValidationError("invalid asset kind")
        object_key = _clean_text(object_key, "object_key", maximum=1000)
        key_parts = object_key.split("/")
        if (not object_key.startswith(f"users/{owner_user_id}/") or "\\" in object_key
                or any(part in ("", ".", "..") for part in key_parts)):
            raise ValidationError("object_key must be inside the owner's namespace")
        work_id = self._require_work(work_id) if work_id else None
        if not isinstance(size_bytes, int) or size_bytes < 0:
            raise ValidationError("invalid size_bytes")
        sha256 = (sha256 or "").strip().lower()
        if sha256 and not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValidationError("invalid sha256")
        asset_id = _new_id("ast")
        self.conn.execute(
            "INSERT INTO private_assets "
            "(asset_id,owner_user_id,project_id,work_id,kind,object_key,sha256,size_bytes,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (asset_id, owner_user_id, project_id, work_id, kind, object_key,
             sha256, size_bytes, now_iso()),
        )
        self.conn.commit()
        return asset_id

    def list_private_assets(self, owner_user_id, project_id):
        self._require_project(owner_user_id, project_id)
        return self.conn.execute(
            "SELECT * FROM private_assets WHERE owner_user_id=? AND project_id=? "
            "ORDER BY created_at, asset_id",
            (owner_user_id, project_id),
        ).fetchall()

    def record_usage(self, owner_user_id, kind, estimated_cost_micros, project_id=None,
                     work_id=None, provider="", model="", input_units=0, output_units=0, status="ok"):
        owner_user_id = self._require_user(owner_user_id)
        if project_id:
            self._require_project(owner_user_id, project_id)
        if work_id:
            work_id = self._require_work(work_id)
        if kind not in USAGE_KINDS or status not in ("ok", "failed", "cancelled"):
            raise ValidationError("invalid usage event")
        values = (estimated_cost_micros, input_units, output_units)
        if any(not isinstance(value, int) or value < 0 for value in values):
            raise ValidationError("usage values must be non-negative integers")
        usage_id = _new_id("use")
        self.conn.execute(
            "INSERT INTO usage_events "
            "(usage_id,owner_user_id,project_id,work_id,kind,provider,model,input_units,output_units,"
            "estimated_cost_micros,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (usage_id, owner_user_id, project_id, work_id, kind, provider, model,
             input_units, output_units, estimated_cost_micros, status, now_iso()),
        )
        self.conn.commit()
        return usage_id

    def usage_total_micros(self, owner_user_id):
        owner_user_id = self._require_user(owner_user_id)
        row = self.conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_micros), 0) AS total FROM usage_events "
            "WHERE owner_user_id=? AND status='ok'",
            (owner_user_id,),
        ).fetchone()
        return int(row["total"])

    def _require_user(self, owner_user_id):
        owner_user_id = _clean_id(owner_user_id, "owner_user_id")
        row = self.conn.execute(
            "SELECT 1 FROM product_users WHERE user_id=? AND status='active'", (owner_user_id,)
        ).fetchone()
        if not row:
            raise OwnershipError("active user not found")
        return owner_user_id

    def _require_project(self, owner_user_id, project_id):
        owner_user_id = self._require_user(owner_user_id)
        project_id = _clean_id(project_id, "project_id")
        row = self.conn.execute(
            "SELECT * FROM research_projects WHERE project_id=? AND owner_user_id=?",
            (project_id, owner_user_id),
        ).fetchone()
        if not row:
            raise OwnershipError("project not owned by user")
        return row

    def _require_work(self, work_id):
        work_id = _clean_work_id(work_id)
        if not self.conn.execute("SELECT 1 FROM works WHERE work_id=?", (work_id,)).fetchone():
            raise ValidationError("unknown work_id")
        return work_id

    @staticmethod
    def _clean_terms(terms, field):
        if not isinstance(terms, list) or len(terms) > 100:
            raise ValidationError(f"invalid {field}")
        return [_clean_text(term, field, maximum=200) for term in terms]
