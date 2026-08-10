#!/usr/bin/env python3
"""literature-agent V2 prefetch：为 MUST（以及满足条件的 WEEKLY）预取合法 PDF。

规则：
- MUST：自动尝试（official OA / arXiv / 合法作者版）
- WEEKLY：仅当 competitor_level>=2 或 transferability>=3 或 feedback must_read
- 遇 paywall：只记录 URL/DOI，不绕过。
- prefetch != 执行 A：不生成 A 建档。
"""
import json
import os
import re

from la_db2 import connect as db2connect, record_signal
from la_discovery import oa_get_work
from la_v2common import norm_doi

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import PAPERS_ROOT


def _download(url, dest):
    from literature_agent import download
    return download(url, dest, timeout=180)


def _slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80]


def _record_artifact(conn, work_id, kind, path, url, status, detail=""):
    conn.execute("INSERT INTO artifacts (work_id,kind,path,url,sha256,size,status,detail,created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?)",
                 (work_id, kind, path, url, None, None, status, detail[:300],
                  __import__("la_v2common").now_iso()))
    conn.commit()


def prefetch_work(conn, w, log):
    title = w["title"]
    slug = _slug(title)
    dest_dir = os.path.join(PAPERS_ROOT, slug)
    os.makedirs(dest_dir, exist_ok=True)
    pdf_path = os.path.join(dest_dir, "paper.pdf")
    from literature_agent import valid_pdf_file
    if valid_pdf_file(pdf_path):
        return {"work_id": w["work_id"], "status": "already_have", "pdf": pdf_path}
    candidates = []  # (url, kind)
    if w["arxiv_id"]:
        candidates.append((f"https://arxiv.org/pdf/{w['arxiv_id']}", "pdf_arxiv"))
    if w["doi"]:
        oa = oa_get_work("https://doi.org/" + norm_doi(w["doi"]))
        if oa:
            loc = oa.get("best_oa_location") or {}
            pdf_url = loc.get("pdf_url")
            if not pdf_url:
                for l in oa.get("locations") or []:
                    if l.get("is_oa") and l.get("pdf_url"):
                        pdf_url = l["pdf_url"]
                        break
            if pdf_url:
                candidates.append((pdf_url, "pdf_official"))
    result = {"work_id": w["work_id"], "status": "blocked", "urls": []}
    for url, kind in candidates:
        try:
            _download(url, pdf_path)
            size = os.path.getsize(pdf_path)
            if valid_pdf_file(pdf_path):
                _record_artifact(conn, w["work_id"], "pdf", pdf_path, url, "ok",
                                 f"prefetched {size} bytes")
                record_signal(conn, w["work_id"], "prefetch", "la_prefetch",
                              {"status": "ok", "url": url, "kind": kind})
                result = {"work_id": w["work_id"], "status": "ok", "pdf": pdf_path, "kind": kind}
                log("ok", f"prefetch {w['work_id']} {kind}")
                return result
        except Exception as e:
            result["urls"].append({"url": url, "kind": kind, "error": str(e)[:120]})
            log("error", f"prefetch {w['work_id']} {kind} failed: {e}")
    # paywall / 失败：只记录
    if w["doi"]:
        result["urls"].append({"url": f"https://doi.org/{w['doi']}", "kind": "paywall_or_missing",
                               "error": "OA PDF unavailable; recorded only"})
    _record_artifact(conn, w["work_id"], "pdf", None, w["doi"] and f"https://doi.org/{w['doi']}" or None,
                     "blocked", "OA/arXiv PDF unavailable (paywall or not found); URL/DOI recorded")
    record_signal(conn, w["work_id"], "prefetch", "la_prefetch",
                  {"status": "blocked", "note": "no legal OA/arXiv PDF; paywall not bypassed"})
    return result


def run_prefetch(conn=None, log=None, limit=30):
    log = log or (lambda *a: None)
    own = conn is None
    conn = conn or db2connect()
    rows = conn.execute("""SELECT w.work_id,w.title,w.doi,w.arxiv_id FROM works w
        WHERE w.work_id NOT LIKE 'test-%' AND w.work_id NOT LIKE 'sim-%'
        AND (SELECT grade FROM screenings s WHERE s.work_id=w.work_id ORDER BY s.id DESC LIMIT 1)='MUST'
        ORDER BY w.first_seen DESC""").fetchall()
    # WEEKLY 且满足条件：competitor>=2 / transferability>=3 / must_read 反馈
    a_rows = conn.execute("""SELECT w.work_id,w.title,w.doi,w.arxiv_id FROM works w
        WHERE w.work_id NOT LIKE 'test-%' AND w.work_id NOT LIKE 'sim-%'
        AND (SELECT grade FROM screenings s WHERE s.work_id=w.work_id ORDER BY s.id DESC LIMIT 1)='WEEKLY'
        ORDER BY w.first_seen DESC LIMIT 120""").fetchall()
    from la_signals import competitor_level, transferability
    for r in a_rows:
        cl, _ = competitor_level(r["title"], "")
        tr = transferability(r["title"], "")
        must = conn.execute("SELECT 1 FROM feedback WHERE work_id=? AND action='must_read'", (r["work_id"],)).fetchone()
        if cl >= 2 or tr >= 3 or must:
            rows.append(r)
    summary = {"attempted": 0, "ok": 0, "blocked": 0, "already_have": 0}
    for w in rows[:limit]:
        summary["attempted"] += 1
        res = prefetch_work(conn, w, log)
        summary[res.get("status", "blocked")] = summary.get(res.get("status", "blocked"), 0) + 1
    if own:
        conn.close()
    return summary
