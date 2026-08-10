#!/usr/bin/env python3
"""literature-agent V2 “你可能漏读了什么” + Zotero 交叉检查（read-only）。

zotero_state: not_in_zotero / in_zotero_unread / in_zotero_read / unknown
只使用本地只读快照 zotero_snapshot.json + zotero_library.json，绝不写 Zotero。
"""
import json
import os
import re

from la_db2 import connect as db2connect
from la_v2common import norm_doi, read_json

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import REPORT_HISTORICAL_ROOT, DATA_ROOT
REPORT_PATH = os.path.join(REPORT_HISTORICAL_ROOT, "missed_literature_report.md")
ZOTERO_LIBRARY_PATH = os.path.join(DATA_ROOT, "zotero_library.json")
ZOTERO_SNAPSHOT_PATH = os.path.join(DATA_ROOT, "zotero_snapshot.json")

READ_TAGS = {"已读", "在读", "略读"}
UNREAD_TAGS = {"待读"}


def load_zotero():
    lib = read_json(ZOTERO_LIBRARY_PATH, [])
    snap = read_json(ZOTERO_SNAPSHOT_PATH, {})
    pdf_keys = set()
    for it in snap.get("items", []):
        d = it.get("data", {})
        if d.get("itemType") == "attachment" and d.get("contentType", "").startswith("application/pdf"):
            pk = d.get("parentItem")
            if pk:
                pdf_keys.add(pk)
    by_doi = {}
    by_title = {}
    for r in lib:
        doi = norm_doi(r.get("DOI", ""))
        if doi:
            by_doi.setdefault(doi, []).append(r)
        t = re.sub(r"\s+", " ", (r.get("title") or "").lower())
        if t:
            by_title.setdefault(t, []).append(r)
    return {"lib": lib, "pdf_keys": pdf_keys, "by_doi": by_doi, "by_title": by_title}


def zotero_state(z, work):
    doi = norm_doi(work.get("doi") or "")
    hits = []
    if doi and doi in z["by_doi"]:
        hits = z["by_doi"][doi]
    if not hits:
        t = re.sub(r"\s+", " ", (work.get("title") or "").lower())
        if t in z["by_title"]:
            hits = z["by_title"][t]
    if not hits:
        return "not_in_zotero", None
    r = hits[0]
    tags = set(r.get("tags", []))
    has_pdf = r.get("key") in z["pdf_keys"]
    if tags & READ_TAGS:
        return "in_zotero_read", {"key": r.get("key"), "tags": sorted(tags), "pdf": has_pdf}
    return "in_zotero_unread", {"key": r.get("key"), "tags": sorted(tags), "pdf": has_pdf}


def missed_report(conn=None):
    own = conn is None
    conn = conn or db2connect()
    z = load_zotero()
    rows = conn.execute("""SELECT w.work_id,w.title,w.doi,w.arxiv_id,w.journal,w.first_seen,w.rank_score,
        (SELECT grade FROM screenings s WHERE s.work_id=w.work_id ORDER BY s.id DESC LIMIT 1) AS grade,
        (SELECT detail_json FROM recommendation_signals sg WHERE sg.work_id=w.work_id
           AND sg.signal_type='project_signals' ORDER BY sg.id DESC LIMIT 1) AS sig
        FROM works w
        WHERE w.work_id NOT LIKE 'test-%' AND w.work_id NOT LIKE 'sim-%'
          AND (SELECT grade FROM screenings s WHERE s.work_id=w.work_id ORDER BY s.id DESC LIMIT 1) IN ('MUST','WEEKLY')
        ORDER BY w.first_seen DESC LIMIT 300""").fetchall()
    items = []
    for r in rows:
        st, zinfo = zotero_state(z, dict(r))
        items.append({"work_id": r["work_id"], "title": r["title"], "doi": r["doi"],
                      "arxiv_id": r["arxiv_id"], "journal": r["journal"], "grade": r["grade"],
                      "first_seen": r["first_seen"], "rank": r["rank_score"] or 0,
                      "zotero_state": st, "zotero_info": zinfo})
    high = [i for i in items if i["zotero_state"] == "not_in_zotero"]
    maybe = [i for i in items if i["zotero_state"] == "in_zotero_unread"]
    have = [i for i in items if i["zotero_state"] == "in_zotero_read"]
    # 未计算 rank 的 MUST/WEEKLY（历史批次）先 enrich，保证排序公平
    from la_signals import enrich_work
    from la_db2 import record_signal
    for r in rows:
        if not (r["rank_score"] or 0) and r["grade"] in ("MUST", "WEEKLY"):
            try:
                w = conn.execute("SELECT * FROM works WHERE work_id=?", (r["work_id"],)).fetchone()
                if w is not None:
                    enrich_work(conn, dict(w), screening=dict(r))
            except Exception:
                pass
    rows = conn.execute("""SELECT w.work_id,w.title,w.doi,w.arxiv_id,w.journal,w.first_seen,w.rank_score,
        (SELECT grade FROM screenings s WHERE s.work_id=w.work_id ORDER BY s.id DESC LIMIT 1) AS grade
        FROM works w
        WHERE w.work_id NOT LIKE 'test-%' AND w.work_id NOT LIKE 'sim-%'
          AND (SELECT grade FROM screenings s WHERE s.work_id=w.work_id ORDER BY s.id DESC LIMIT 1) IN ('MUST','WEEKLY')
        ORDER BY w.first_seen DESC LIMIT 300""").fetchall()
    items = []
    for r in rows:
        st, zinfo = zotero_state(z, dict(r))
        items.append({"work_id": r["work_id"], "title": r["title"], "doi": r["doi"],
                      "arxiv_id": r["arxiv_id"], "journal": r["journal"], "grade": r["grade"],
                      "first_seen": r["first_seen"], "rank": r["rank_score"] or 0,
                      "zotero_state": st, "zotero_info": zinfo})
    high = [i for i in items if i["zotero_state"] == "not_in_zotero"]
    maybe = [i for i in items if i["zotero_state"] == "in_zotero_unread"]
    have = [i for i in items if i["zotero_state"] == "in_zotero_read"]
    lines = ["# 你可能漏读了什么（missed literature report）", "",
             f"> MUST/WEEKLY 共 {len(items)}：不在 Zotero {len(high)}｜在 Zotero 未读 {len(maybe)}｜已读 {len(have)}。",
             "> 只列 MUST/WEEKLY；高优先级最多 12 条、可能漏读最多 8 条。", ""]
    lines += ["## 高优先级漏读（MUST/WEEKLY 且不在 Zotero）", ""]
    if not high:
        lines.append("（无）")
    for i in sorted(high, key=lambda x: (x["grade"] != "MUST", -x["rank"], x["first_seen"] or ""))[:12]:
        lines.append(f"- [{i['grade']}] {i['title'][:100]}")
        lines.append(f"  - {i['journal'] or '?'}｜{i['doi'] or i['arxiv_id'] or 'no id'}｜seen {i['first_seen']}｜rank {i['rank']}")
    lines += ["", "## 可能漏读（在 Zotero 但未读）", ""]
    for i in sorted(maybe, key=lambda x: (x["grade"] != "MUST", -x["rank"], x["first_seen"] or ""))[:8]:
        lines.append(f"- [{i['grade']}] {i['title'][:100]}")
        lines.append(f"  - {i['journal'] or '?'}｜{i['doi'] or i['arxiv_id'] or 'no id'}｜tags {i['zotero_info']['tags']}")
    lines += ["", "## 已经在 Zotero / 已读", ""]
    for i in sorted(have, key=lambda x: x["first_seen"] or "")[:8]:
        lines.append(f"- [{i['grade']}] {i['title'][:95]}（{i['doi'] or '-'}）")
    lines += ["", "## 系统无法确认", ""]
    lines.append("- Zotero 快照只含 360 篇顶层条目；附件/笔记关联以 PDF 附件为准，非 PDF 附件未计。")
    lines.append("- 新入库的 MUST/WEEKLY 未人工确认时按 not_in_zotero 处理（可能实际已读）。")
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if own:
        conn.close()
    return REPORT_PATH
