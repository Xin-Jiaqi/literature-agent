#!/usr/bin/env python3
"""literature-agent V2 引用网络雷达：
- citation_seeds.json（核心种子：coverage MUST + DB 自动 MUST + 核心理论/竞争论文）
- citation-watch：cites:W 查询 → citing_core_work / co_cited / related_to_core_work 信号
局限：OpenAlex citation index 有滞后（lag），新论文引用可能延迟数周，如实记录。
"""
import json
import hashlib
import os
import re
import time

from la_db2 import add_edge, add_seed, connect as db2connect, record_signal
from la_discovery import oa_citing_works, oa_get_work, oa_related_works, oa_work_to_rec
from la_v2common import norm_doi, read_json, write_json
from la_dedup import normalize_title_key, title_similarity
from la_priority import MUST, normalize_priority

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import DATA_ROOT
SEEDS_PATH = os.path.join(DATA_ROOT, "citation_seeds.json")

# 核心理论/竞争论文（不在 coverage gold S 里的补充）
CORE_TITLES = [
    "Fractional quantum ferroelectricity",
    "Symmetry Strategy for Rapid Discovery of Abundant Fractional Quantum Ferroelectrics",
    "Generalized Neumann's Principle as a Unified Framework",
    "Unified definition of ferroelectricity",
    "General Theory for Bilayer Stacking Ferroelectricity",
    "Non-synchronous bulk photovoltaic effect",
    "Bulk photovoltaic effects in mirror-time symmetric systems",
    "Fractional Quantum Multiferroics from Coupling",
    "A unified symmetry framework for spin",
    "Symmetry-locked six-state control of altermagnetism via sliding ferroelectricity",
    "Tailoring Bulk Photovoltaic Effects in Magnetic Sliding Ferroelectric Materials",
    "Excitonic Shift Current in Monolayer MoS2",
]


def _work_id_of(conn, doi=None, title=None, arxiv=None):
    if doi:
        r = conn.execute("SELECT work_id FROM works WHERE doi=?", (norm_doi(doi),)).fetchone()
        if r:
            return r["work_id"]
    if arxiv:
        r = conn.execute("SELECT work_id FROM works WHERE arxiv_id=?", (arxiv,)).fetchone()
        if r:
            return r["work_id"]
    if title:
        t = re.sub(r"\s+", " ", title.lower())
        for w in conn.execute("SELECT work_id,title FROM works").fetchall():
            wt = re.sub(r"\s+", " ", (w["title"] or "").lower())
            if wt == t or (len(t) > 40 and (t in wt or wt in t)):
                return w["work_id"]
    return None


def build_seeds(conn=None, force=False):
    """构建 citation seeds：coverage gold MUST + DB 自动 MUST（排除测试）+ 核心理论/竞争论文。"""
    own = conn is None
    conn = conn or db2connect()
    if os.path.exists(SEEDS_PATH) and not force:
        return read_json(SEEDS_PATH).get("seeds", [])
    from la_coverage import load_coverage_gold
    seeds = []
    used = set()
    gold = load_coverage_gold()
    for e in gold:
        if normalize_priority(e["expected_importance"]) != MUST:
            continue
        wid = _work_id_of(conn, doi=e.get("doi"), title=e["title"])
        seeds.append({"seed_id": "covMUST_" + (e.get("gold_id", "")), "work_id": wid or "",
                      "title": e["title"], "doi": e.get("doi"), "arxiv_id": e.get("arxiv_id"),
                      "kind": "coverage_MUST", "note": "coverage gold MUST"})
        used.add(normalize_title_key(e["title"]))
    srows = conn.execute("""SELECT w.work_id,w.title,w.doi,w.arxiv_id FROM works w
        JOIN screenings s ON s.work_id=w.work_id
        WHERE s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=w.work_id)
        AND s.grade='MUST' AND w.historical_discovery=0
        AND w.work_id NOT LIKE 'test-%' AND w.work_id NOT LIKE 'sim-%'""").fetchall()
    for w in srows:
        title_key = normalize_title_key(w["title"])
        if title_key in used:
            continue
        seeds.append({"seed_id": "autoMUST_" + w["work_id"].replace(":", "_")[:40], "work_id": w["work_id"],
                      "title": w["title"], "doi": w["doi"], "arxiv_id": w["arxiv_id"], "kind": "auto_MUST",
                      "note": "自动 MUST 论文"})
        used.add(title_key)
    for t in CORE_TITLES:
        key = normalize_title_key(t)
        if key in used:
            continue
        wid = _work_id_of(conn, title=t)
        e = None
        for g in gold:
            if g["title"].startswith(t[:40]):
                e = g
                break
        stable_id = hashlib.sha1(t.encode("utf-8")).hexdigest()[:10]
        seeds.append({"seed_id": "core_" + stable_id, "work_id": wid or "",
                      "title": (e or {}).get("title", t), "doi": (e or {}).get("doi", ""),
                      "arxiv_id": (e or {}).get("arxiv_id", ""), "kind": "core_theory",
                      "note": "核心理论/竞争论文"})
        used.add(key)
    # 解析 OpenAlex ID（按 DOI 或标题搜索）
    resolved = []
    for s in seeds:
        oaid = ""
        if s.get("doi"):
            w = oa_get_work("https://doi.org/" + norm_doi(s["doi"]))
            if w and w.get("id"):
                oaid = w["id"].rsplit("/", 1)[-1]
        if not oaid and s.get("title"):
            from la_discovery import oa_search_works
            res = oa_search_works(s["title"], per_page=3)
            ranked = sorted(
                ((title_similarity(r.get("title", ""), s["title"]), r) for r in res if r.get("title")),
                key=lambda x: -x[0],
            )
            if ranked and ranked[0][0] >= 0.92:
                r = ranked[0][1]
                oaid = r["id"].rsplit("/", 1)[-1]
                s["doi"] = s.get("doi") or norm_doi((r.get("doi") or "").replace("https://doi.org/", ""))
        s["openalex_id"] = oaid
        resolved.append(s)
        for k in ("seed_id", "work_id", "title", "doi", "arxiv_id", "kind", "note"):
            pass
        add_seed(conn, {k: s.get(k, "") for k in ("seed_id", "work_id", "title", "doi", "arxiv_id", "kind", "note")})
    if not any(s.get("openalex_id") for s in resolved):
        raise RuntimeError("OpenAlex unavailable — citation seeds not written (no openalex_id resolved)")
    write_json(SEEDS_PATH, {"updated": time.strftime("%Y-%m-%d"), "seeds": resolved})
    if own:
        conn.close()
    return resolved


def citation_watch(conn=None, days=7, log=None, related_cap=15, max_citing_per_seed=200, index=None):
    """查询 citing seeds 的新论文。signal=citing_core_work / co_cited / related_to_core_work。"""
    from datetime import date, timedelta
    log = log or (lambda *a: None)
    own = conn is None
    conn = conn or db2connect()
    seeds = read_json(SEEDS_PATH, {}).get("seeds", [])
    if not seeds:
        seeds = build_seeds(conn)
    from_date = (date.today() - timedelta(days=int(days))).isoformat()
    summary = {"seeds": len(seeds), "with_oaid": 0, "citing_found": 0, "new": 0, "updated": 0,
               "noop": 0, "co_cited": 0, "related": 0, "failed_seeds": [], "cited_map": {}}
    import literature_agent as la
    for s in seeds:
        oaid = s.get("openalex_id")
        if not oaid:
            continue
        summary["with_oaid"] += 1
        try:
            ws = oa_citing_works(oaid, from_date=from_date, per_page=100)
        except Exception as ex:
            summary["failed_seeds"].append(s.get("title", "")[:50])
            log("error", f"citation-watch seed {s.get('title','')[:40]} failed: {ex}")
            continue
        for w in ws[:max_citing_per_seed]:
            summary["citing_found"] += 1
            rec = oa_work_to_rec(w)
            if not rec["title"]:
                continue
            try:
                wid, action, _ = la.ingest_record(
                    conn, rec, "openalex_citation_watch", date.today().isoformat(), index=index
                )
            except Exception as ex:
                log("error", f"ingest citing work failed: {ex}")
                continue
            if action == "new":
                summary["new"] += 1
            elif action == "noop":
                summary["noop"] += 1
            else:
                summary["updated"] += 1
            add_edge(conn, s.get("work_id") or s["seed_id"], wid, "cites_core_work", "openalex_citation_watch")
            record_signal(conn, wid, "citing_core_work", "openalex_citation_watch",
                          {"seed": s.get("title", "")[:90], "seed_kind": s.get("kind", "")})
            summary["cited_map"][wid] = summary["cited_map"].get(wid, 0) + 1
            # related works（仅对新的重要候选，限制数量）
            if action == "new" and summary["related"] < related_cap:
                for rw in oa_related_works(w["id"].rsplit("/", 1)[-1], cap=10)[:5]:
                    rrec = oa_work_to_rec(rw)
                    if not rrec["title"]:
                        continue
                    rwid, raction, _ = la.ingest_record(
                        conn, rrec, "openalex_citation_watch", date.today().isoformat(), index=index
                    )
                    if raction == "new":
                        summary["new"] += 1
                    elif raction == "noop":
                        summary["noop"] += 1
                    else:
                        summary["updated"] += 1
                    add_edge(conn, wid, rwid, "related_to_core_work", "openalex_related")
                    record_signal(conn, rwid, "related_to_core_work", "openalex_related",
                                  {"from": rec["title"][:90]})
                    summary["related"] += 1
    co = {wid: n for wid, n in summary["cited_map"].items() if n >= 2}
    for wid in co:
        record_signal(conn, wid, "co_cited", "openalex_citation_watch",
                      {"citing_seed_count": co[wid]})
        summary["co_cited"] += 1
    if own:
        conn.close()
    return summary
