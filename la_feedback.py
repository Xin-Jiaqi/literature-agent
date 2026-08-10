#!/usr/bin/env python3
"""literature-agent V2 用户反馈闭环 + gold 自动生长。

feedback actions: must_read / read / save / skip / not_relevant / upgrade / downgrade / competitor / method_useful
update-gold: 只有 upgrade/downgrade/must_read/not_relevant 可候选进 gold；
先输出 gold_candidates.md，--apply 才写 screening_gold.jsonl。
"""
import json
import os

from la_db2 import (append_feedback_jsonl, connect as db2connect,
                    load_feedback_jsonl, record_feedback)
from la_priority import DROP, MUST

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import REPORT_AUDIT_ROOT, GOLD_ROOT
GOLD_CAND_PATH = os.path.join(REPORT_AUDIT_ROOT, "gold_candidates.md")
SCREENING_GOLD = os.path.join(GOLD_ROOT, "screening_gold.jsonl")

ACTIONS = ("must_read", "read", "save", "save_off", "skip", "not_relevant", "upgrade", "downgrade", "competitor", "competitor_off", "method_useful")

GOLD_ACTIONS = {"upgrade": MUST, "downgrade": DROP, "must_read": MUST, "not_relevant": DROP}


def run_feedback(conn, work_id, action, note=""):
    if action not in ACTIONS:
        raise ValueError(f"ACTION 必须是 {ACTIONS}")
    w = conn.execute("SELECT work_id,title,grade FROM works WHERE work_id=?",
                     (work_id,)).fetchone() if False else None
    row = conn.execute("SELECT work_id,title FROM works WHERE work_id=?", (work_id,)).fetchone()
    if row is None:
        row = conn.execute("SELECT work_id,title FROM works WHERE work_id LIKE ?", (f"%{work_id}%",)).fetchone()
    if row is None:
        return {"ok": False, "error": f"work_id 未找到: {work_id}"}
    cur = conn.execute("SELECT grade FROM screenings WHERE work_id=? ORDER BY id DESC LIMIT 1",
                       (row["work_id"],)).fetchone()
    grade = cur["grade"] if cur else ""
    rec = {"work_id": row["work_id"], "title": row["title"], "action": action,
           "grade": grade, "note": note, "created_at": __import__("la_v2common").now_iso()}
    record_feedback(conn, row["work_id"], action, grade, note)
    append_feedback_jsonl(rec)
    return {"ok": True, "work_id": row["work_id"], "action": action, "grade": grade}


def _abstract_of(conn, work_id):
    r = conn.execute("SELECT abstract FROM work_sources WHERE work_id=? ORDER BY length(abstract) DESC LIMIT 1",
                     (work_id,)).fetchone()
    return r["abstract"] if r else ""


def gold_candidates(conn=None, apply=False):
    """生成 gold_candidates.md；--apply 把候选写入 screening_gold.jsonl。"""
    own = conn is None
    conn = conn or db2connect()
    fb = load_feedback_jsonl()
    cands = []
    seen = set()
    for r in fb:
        if r["action"] not in GOLD_ACTIONS:
            continue
        key = (r["work_id"], r["action"])
        if key in seen:
            continue
        seen.add(key)
        w = conn.execute("SELECT title FROM works WHERE work_id=?", (r["work_id"],)).fetchone()
        if not w:
            continue
        cands.append({"work_id": r["work_id"], "title": w["title"],
                      "expected_grade": GOLD_ACTIONS[r["action"]],
                      "action": r["action"], "abstract": _abstract_of(conn, r["work_id"]),
                      "reason": f"user feedback {r['action']}"})
    lines = ["# Gold Candidates（来自人工反馈）", "",
             f"> 候选数：{len(cands)}。只有 upgrade/downgrade/must_read/not_relevant 会进入候选。",
             "> 应用：`python3 literature_agent.py update-gold --apply`", ""]
    if cands:
        lines.append("| work_id | 期望分级 | 反馈 | 标题 |")
        lines.append("|---|---|---|---|")
        for c in cands:
            lines.append(f"| {c['work_id']} | {c['expected_grade']} | {c['action']} | {c['title'][:70]} |")
    else:
        lines.append("（暂无候选——先对论文做 upgrade/downgrade/must_read/not_relevant 反馈）")
    os.makedirs(os.path.dirname(GOLD_CAND_PATH), exist_ok=True)
    with open(GOLD_CAND_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if apply and cands:
        new_gold = 0
        existing = set()
        if os.path.exists(SCREENING_GOLD):
            with open(SCREENING_GOLD, encoding="utf-8") as current:
                for line in current:
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    key = (" ".join((item.get("title") or "").lower().split()),
                           item.get("expected_grade") or "")
                    existing.add(key)
        with open(SCREENING_GOLD, "a", encoding="utf-8") as f:
            for c in cands:
                key = (" ".join(c["title"].lower().split()), c["expected_grade"])
                if key in existing:
                    continue
                f.write(json.dumps({"title": c["title"], "abstract": c["abstract"],
                                    "expected_grade": c["expected_grade"],
                                    "source": "user_feedback", "reason": c["reason"]},
                                   ensure_ascii=False) + "\n")
                existing.add(key)
                new_gold += 1
        if own:
            conn.close()
        return {"candidates": len(cands), "applied": new_gold}
    if own:
        conn.close()
    return {"candidates": len(cands), "applied": 0}
