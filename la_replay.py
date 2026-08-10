#!/usr/bin/env python3
"""literature-agent V2 历史回放：2026-06-01 → 今天。

来源：arXiv（按日期区间，完整回溯）+ Crossref（tracked/blocked 期刊 ISSN）+ OpenAlex（tracked authors + 核心 seeds 引用）。
不做历史 RSS 回溯；来源理论覆盖以 API metadata 为准，标注 inferred。
所有 replay 发现：historical_discovery=1，绝不混入每日新增。
"""
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from la_db2 import connect as db2connect, record_signal
from la_discovery import (BLOCKED_JOURNALS, collect_crossref_journals, oa_author_works,
                          oa_citing_works, oa_work_to_rec)
from la_v2common import norm_arxiv, read_json, write_json

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import REPORT_HISTORICAL_ROOT, DATA_ROOT
REPLAY_PATH = os.path.join(REPORT_HISTORICAL_ROOT, "historical_replay.md")


def _arxiv_range(from_date, to_date):
    """按 submittedDate 区间查询 arXiv（分页）。"""
    import urllib.request
    from la_v2common import ssl_ctx
    cats = ["cond-mat.mtrl-sci", "cond-mat.mes-hall", "cond-mat.str-el", "physics.comp-ph"]
    out = []
    for cat in cats:
        start = 0
        while start < 6000:
            q = (f"http://export.arxiv.org/api/query?search_query=cat:{cat}"
                 f"+AND+submittedDate:[{from_date}0000+TO+{to_date}2359]"
                 f"&sortBy=submittedDate&sortOrder=descending&start={start}&max_results=200")
            try:
                req = urllib.request.Request(q)
                with urllib.request.urlopen(req, timeout=90, context=ssl_ctx()) as r:
                    data = r.read()
            except Exception as e:
                break
            root = ET.fromstring(data)
            ns = {"a": "http://www.w3.org/2005/Atom"}
            entries = root.findall("a:entry", ns)
            if not entries:
                break
            for e in entries:
                aid_el = e.find("a:id", ns)
                aid = aid_el.text.split("/abs/")[-1] if aid_el is not None and aid_el.text else ""
                aid = norm_arxiv(aid)
                if not aid:
                    continue
                title = re.sub(r"\s+", " ", (e.findtext("a:title", default="", namespaces=ns) or "").strip())
                abstract = re.sub(r"\s+", " ", (e.findtext("a:summary", default="", namespaces=ns) or "").strip())
                authors = "; ".join(a.text for a in e.findall("a:author/a:name", ns) if a.text)
                pub = e.findtext("a:published", default="", namespaces=ns)[:10]
                out.append({"title": title, "authors": authors, "arxiv_id": aid, "abstract": abstract,
                            "abstract_truncated": False, "date": pub,
                            "url": f"https://arxiv.org/abs/{aid}", "journal": "arXiv",
                            "status": "arxiv", "version": ""})
            start += len(entries)
            time.sleep(0.5)
    return out


def run_replay(conn=None, log=None, from_date="2026-06-01", to_date=None):
    from datetime import date
    log = log or (lambda *a: None)
    own = conn is None
    conn = conn or db2connect()
    to_date = to_date or date.today().isoformat()
    import literature_agent as la
    from la_dedup import DedupIndex
    index = DedupIndex(conn)  # 复用索引，避免 O(N^2)
    summary = {"sources": {}, "total_new": 0, "total_existing": 0, "total_records": 0}
    # 1) arXiv
    if os.environ.get("LITAGENT_REPLAY_SKIP_ARXIV") == "1":
        recs = []
        log("info", "skip arXiv phase (LITAGENT_REPLAY_SKIP_ARXIV=1)")
    else:
        recs = _arxiv_range(from_date.replace("-", ""), to_date.replace("-", ""))
    summary["sources"]["arxiv"] = {"records": len(recs)}
    n_new = n_ex = 0
    for rec in recs:
        try:
            wid, action, _ = la.ingest_record(conn, rec, "arxiv_replay", date.today().isoformat(), history=True, index=index)
        except Exception as e:
            log("error", f"replay arxiv ingest failed: {e}")
            continue
        if action == "new":
            conn.execute("UPDATE works SET historical_discovery=1 WHERE work_id=?", (wid,))
            n_new += 1
        else:
            n_ex += 1
    summary["sources"]["arxiv"]["new"] = n_new
    summary["total_new"] += n_new
    summary["total_existing"] += n_ex
    summary["total_records"] += len(recs)
    conn.commit()
    # 2) Crossref blocked journals
    recs, per = collect_crossref_journals(conn, date.today().isoformat(), log=log, days=(date.today() - date.fromisoformat(from_date)).days)
    n_new = n_ex = 0
    for rec in recs:
        try:
            wid, action, _ = la.ingest_record(conn, rec, "crossref_replay", date.today().isoformat(), history=True, index=index)
        except Exception:
            continue
        if action == "new":
            conn.execute("UPDATE works SET historical_discovery=1 WHERE work_id=?", (wid,))
            n_new += 1
        else:
            n_ex += 1
    summary["sources"]["crossref"] = {"records": len(recs), "new": n_new}
    summary["total_new"] += n_new
    summary["total_existing"] += n_ex
    summary["total_records"] += len(recs)
    conn.commit()
    # 3) OpenAlex tracked authors（high/medium）
    ents = conn.execute("SELECT canonical_name,openalex_author_id,confidence FROM tracked_entities").fetchall()
    n_new = n_ex = 0
    per_author = {}
    for e in ents:
        if not e["openalex_author_id"] or e["confidence"] not in ("high", "medium"):
            continue
        try:
            ws = oa_author_works(e["openalex_author_id"], from_date=from_date, per_page=100)
        except Exception:
            continue
        a_new = a_ex = 0
        for w in ws:
            rec = oa_work_to_rec(w)
            if not rec["title"]:
                continue
            try:
                wid, action, _ = la.ingest_record(conn, rec, "openalex_replay", date.today().isoformat(), history=True, index=index)
            except Exception:
                continue
            if action == "new":
                conn.execute("UPDATE works SET historical_discovery=1 WHERE work_id=?", (wid,))
            record_signal(conn, wid, "tracked_author", "openalex_replay", {"entity": e["canonical_name"]})
            if action == "new":
                n_new += 1
                a_new += 1
            else:
                n_ex += 1
                a_ex += 1
        per_author[e["canonical_name"]] = {"works": len(ws), "new": a_new}
    summary["sources"]["openalex_author"] = {"records": sum(v["works"] for v in per_author.values()), "new": n_new,
                                             "per_author": per_author}
    summary["total_new"] += n_new
    summary["total_existing"] += n_ex
    summary["total_records"] += sum(v["works"] for v in per_author.values())
    conn.commit()
    # 4) citation replay（top 12 seeds, cites:W）
    seeds = read_json(os.path.join(DATA_ROOT, "citation_seeds.json"), {}).get("seeds", [])
    n_new = 0
    per_seed = {}
    for s in seeds[:12]:
        oaid = s.get("openalex_id")
        if not oaid:
            continue
        try:
            ws = oa_citing_works(oaid, from_date=from_date, per_page=100)
        except Exception:
            continue
        s_new = 0
        for w in ws:
            rec = oa_work_to_rec(w)
            if not rec["title"]:
                continue
            try:
                wid, action, _ = la.ingest_record(conn, rec, "citation_replay", date.today().isoformat(), history=True, index=index)
            except Exception:
                continue
            if action == "new":
                conn.execute("UPDATE works SET historical_discovery=1 WHERE work_id=?", (wid,))
            from la_db2 import add_edge
            add_edge(conn, s.get("work_id") or s["seed_id"], wid, "cites_core_work", "citation_replay")
            record_signal(conn, wid, "citing_core_work", "citation_replay", {"seed": s.get("title", "")[:90]})
            if action == "new":
                n_new += 1
                s_new += 1
        per_seed[s.get("title", "")[:50]] = {"works": len(ws), "new": s_new}
    summary["sources"]["citation"] = {"new": n_new, "per_seed": per_seed}
    summary["total_new"] += n_new
    conn.commit()
    # replay 出的全部入队待筛选（screen --historical 处理）
    conn.execute("UPDATE works SET queued=1 WHERE historical_discovery=1 AND queued=0")
    conn.commit()
    if own:
        conn.close()
    return summary


def write_replay_report(conn, summary, coverage_matches=None, missed=None):
    lines = ["# Historical Replay（2026-06-01 → 今天）", "",
             "> 来源：arXiv（submittedDate 区间）、Crossref（blocked 期刊 ISSN）、OpenAlex（tracked authors + 核心 seeds 引用）。",
             "> 历史 RSS 不可回溯，覆盖判断为 **inferred**（基于 API metadata），非 observed。",
             "> replay 发现的新 work 全部标记 `historical_discovery`，不混入每日新增。", ""]
    lines.append("## 统计")
    lines.append(f"- 总记录：{summary['total_records']}｜新入库：{summary['total_new']}｜已存在：{summary['total_existing']}")
    for k, v in summary["sources"].items():
        lines.append(f"- {k}: records={v.get('records', v.get('new'))} new={v.get('new', 0)}")
    # screening 后交叉检查
    if coverage_matches is not None:
        lines += ["", "## 与 coverage gold / 已知近期重要论文交叉", ""]
        lines.append(f"- replay 命中 coverage gold：{len(coverage_matches)} 篇")
        lines.append(f"- gold 中仍未发现：{len(missed or [])} 篇")
        for t in (missed or [])[:15]:
            lines.append(f"  - {t}")
    lines += ["", "## 重要发现（MUST/WEEKLY）", ""]
    rows = conn.execute("""SELECT w.work_id,w.title,w.doi,w.arxiv_id,w.historical_discovery,
        (SELECT grade FROM screenings s WHERE s.work_id=w.work_id ORDER BY s.id DESC LIMIT 1) AS grade
        FROM works w WHERE w.historical_discovery=1 ORDER BY w.first_seen""").fetchall()
    imp = [r for r in rows if r["grade"] in ("MUST", "WEEKLY")]
    lines.append(f"- replay 工作中共 {len(imp)} 篇为 MUST/WEEKLY（新发现会进 missed 报告）")
    for r in imp[:20]:
        lines.append(f"- [{r['grade']}] {r['title'][:90]}（{r['doi'] or r['arxiv_id'] or '-'}）")
    lines += ["", "## 局限", "",
              "- OpenAlex citation index 存在滞后（lag），近 2-4 周引用可能尚未收录。",
              "- Crossref 只覆盖有 DOI 且已注册的记录；无 abstract 时未猜测。",
              "- arXiv 查询按 submittedDate，可能与实际 publishedDate 有差异。"]
    os.makedirs(os.path.dirname(REPLAY_PATH), exist_ok=True)
    with open(REPLAY_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return REPLAY_PATH
