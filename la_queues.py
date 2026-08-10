#!/usr/bin/env python3
"""三个独立队列：live_must / historical_recovery / weekly。

职责边界：
- live_must：今天新发现的 MUST（historical_discovery=0），最高优先，立即执行 A；
- historical_recovery：历史 replay 找回的重要论文（historical_discovery=1），
  不计入今日 MUST 数量、不阻塞 live，后台慢慢完成 A；
- weekly：本周候选（WEEKLY 且非历史），不自动执行 A。

队列是派生视图（不新增数据库表）：由 works + screenings + processing 实时计算。
"""
import json
import os
from datetime import date, datetime, timedelta

from la_priority import MUST, WEEKLY

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import QUEUES_ROOT
A_QUEUE_PATH = os.environ.get("LITAGENT_A_QUEUE", os.path.join(QUEUES_ROOT, "a_queue.jsonl"))

USER_DISPOSED = {"processing", "deferred", "ignored", "callable"}


def _latest_must_rows(conn):
    return conn.execute(
        """SELECT w.*, s.reason, s.matched_profile
           FROM works w JOIN screenings s ON s.work_id=w.work_id
           WHERE s.grade=? AND s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=w.work_id)
           ORDER BY w.historical_discovery ASC, w.rank_score DESC, w.first_seen DESC""",
        (MUST,),
    ).fetchall()


def a_queue_entries(conn):
    """A 执行队列（含状态），顺序：live_must 优先，随后 historical_recovery。

    与 la_workflow.build_a_queue 同一数据源；此处为队列视图，不写文件。
    """
    from la_workflow import DECISIONS, FLAGS, workflow_item

    entries = []
    for work in _latest_must_rows(conn):
        item = workflow_item(conn, work["work_id"])
        if item is None or item["a_status"] in ("completed", "blocked_pdf"):
            continue  # blocked_pdf 单独保留（blocked_pdf_entries），不进 A 执行清单
        kind = "live_must" if not work["historical_discovery"] else "historical_recovery"
        entries.append({
            "work_id": work["work_id"],
            "title": work["title"],
            "authors": work["authors"] or "",
            "doi": work["doi"] or "",
            "arxiv_id": work["arxiv_id"] or "",
            "recommendation_priority": MUST,
            "queue_kind": kind,
            "a_status": item["a_status"],
            "pdf_status": item["pdf_status"],
            "pdf_path": item["pdf_path"],
            "a_output_path": item["a_path"],
            "recommendation_reason": work["reason"] or "",
            "matched_profile": work["matched_profile"] or "",
            "disposition_options_after_A": list(DECISIONS),
            "flag_options_after_A": list(FLAGS),
        })
    return entries


def weekly_candidates(conn, today=None, days=7, limit=50):
    """本周候选：WEEKLY、非历史、最近 days 天内发现、用户尚未决定。"""
    today = today or date.today().isoformat()
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT w.*, s.reason, s.matched_profile
           FROM works w JOIN screenings s ON s.work_id=w.work_id
           WHERE s.grade=? AND s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=w.work_id)
             AND w.historical_discovery=0 AND date(s.screened_at)>=?
             AND COALESCE(w.reading_status,'') NOT IN ('processing','deferred','ignored','callable')
           ORDER BY w.rank_score DESC, w.first_seen DESC LIMIT ?""",
        (WEEKLY, since, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def blocked_pdf_entries(conn):
    from la_workflow import workflow_item

    out = []
    for w in _latest_must_rows(conn):
        item = workflow_item(conn, w["work_id"])
        if item is not None and item["a_status"] == "blocked_pdf":
            out.append({"work_id": w["work_id"], "title": w["title"], "doi": w["doi"] or "",
                        "arxiv_id": w["arxiv_id"] or "", "a_status": "blocked_pdf"})
    return out


def must_counts(conn):
    """MUST 总览（live vs historical，含已完成）。"""
    live = conn.execute(
        """SELECT COUNT(*) FROM works w WHERE w.historical_discovery=0 AND EXISTS (
             SELECT 1 FROM screenings s WHERE s.work_id=w.work_id AND s.grade=?
               AND s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=w.work_id))""",
        (MUST,),
    ).fetchone()[0]
    hist = conn.execute(
        """SELECT COUNT(*) FROM works w WHERE w.historical_discovery=1 AND EXISTS (
             SELECT 1 FROM screenings s WHERE s.work_id=w.work_id AND s.grade=?
               AND s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=w.work_id))""",
        (MUST,),
    ).fetchone()[0]
    return {"live_total": live, "historical_total": hist}


def queue_state(conn, today=None):
    """三队列状态：只统计需要或值得用户/后台处理的部分。"""
    entries = a_queue_entries(conn)
    live = [e for e in entries if e["queue_kind"] == "live_must"]
    hist = [e for e in entries if e["queue_kind"] == "historical_recovery"]
    weekly = weekly_candidates(conn, today)
    blocked = blocked_pdf_entries(conn)
    return {
        "live_must": {
            "pending": len(live),
            "entries": live,
        },
        "historical_recovery": {
            "pending": len(hist),
            "entries": hist,
        },
        "weekly": {
            "pending": len(weekly),
            "entries": [{"work_id": r["work_id"], "title": r["title"],
                         "reason": r["reason"] or "", "matched_profile": r["matched_profile"] or ""}
                        for r in weekly],
        },
        "blocked_pdf": blocked,
        "must_total": must_counts(conn),
    }


def write_a_queue_file(conn):
    """把 A 执行队列写到 a_queue.jsonl（机器清单，live_must 优先）。"""
    entries = a_queue_entries(conn)
    os.makedirs(os.path.dirname(A_QUEUE_PATH) or ".", exist_ok=True)
    tmp = A_QUEUE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, A_QUEUE_PATH)
    return {"path": A_QUEUE_PATH, "pending": len(entries),
            "live_must": sum(1 for e in entries if e["queue_kind"] == "live_must"),
            "historical_recovery": sum(1 for e in entries if e["queue_kind"] == "historical_recovery")}
