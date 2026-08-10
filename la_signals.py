#!/usr/bin/env python3
"""literature-agent V2 多维信号引擎 + explainable ranking + competitor watch。

原则：信号只用于解释与排序，不替代 MUST/WEEKLY/RADAR/DROP 优先级。
competitor_level：0 none / 1 adjacent / 2 direct / 3 urgent。
"""
import json
import os
import re
import time

from la_db2 import connect as db2connect, record_signal
from la_priority import DROP, MUST, RADAR, WEEKLY, normalize_priority

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import REPORT_AUDIT_ROOT
COMPETITOR_PATH = os.path.join(REPORT_AUDIT_ROOT, "competitor_watch.md")

# 竞品核心清单（V2 §11）
COMPETITOR_TOPICS = [
    (r"fractional quantum ferroelectric|fractional ferroelectricity|FQFE|quantum ferroelectric", 3, "FQFE/QFE 直接竞争"),
    (r"unified definition of ferroelectricity|unified ferroelectric", 2, "统一铁电定义"),
    (r"fractional translation|fractional rotation", 2, "分数平移/旋转切换"),
    (r"sliding ferroelectricity", 1, "滑移铁电"),
    (r"stacking ferroelectricity|bilayer stacking", 1, "堆叠铁电"),
    (r"bulk photovoltaic|shift current|injection current", 1, "BPVE"),
    (r"nonlinear photocurrent.*switch|photocurrent switching|switching.*photocurrent|BPVE.*switch|switch.*BPVE", 3, "BPVE 切换"),
    (r"ferroicity|ferroelectric.*photovoltaic|polarization.*photogalvanic", 2, "铁电驱动非线性光电流"),
    (r"sliding.*magnet|magnetic.*sliding|altermagnet.*ferroelectric|ferroelectric.*altermagnet|multiferroic.*sliding", 2, "滑移铁电×磁性"),
    (r"full-position Wannier|position matrix|Wilson loop.*nonlinear|generalized.*Wilson", 2, "Wannier 非线性光学方法"),
    (r"generalized Neumann", 2, "广义 Neumann 原理"),
    (r"mirror-time|band crossing.*nonlinear", 1, "新对称条件 BPVE"),
]

PROJECT_MAP = [
    (r"fractional quantum ferroelectric|FQFE|quantum ferroelectric|injection current|full.position Wannier|Hg3O3|Wannier interp", "fractional_ferroelectric"),
    (r"sliding ferroelectric|stacking ferroelectric|bilayer stacking|shift current|bulk photovoltaic|BSF|slidetronics", "project5"),
    (r"photogalvanic|photocurrent|nonlinear optical|second harmonic|polarization switching", "both"),
    (r"altermagnet|multiferroic|magnetic space group|spin group|Berry phase|polarization quantum|Neumann", "long_term"),
    (r"exciton|topology|valley|twist|moir", "adjacent"),
]

NOVELTY_MAP = [
    (r"symmetry|space group|point group|group theory|Neumann|magnetic group|spin group", "symmetry"),
    (r"unified definition|framework|general theory|classification|theory of|formalism|principle", "theory"),
    (r"Wannier|tight-binding|first-principles|DFT|calculation|screening|high-throughput|descriptor|machine learning|method|implementation|code", "method"),
    (r"shift current|photocurrent|SHG|second harmonic|photovoltaic|mechanism", "mechanism"),
    (r"monolayer|bilayer|heterostructure|Janus|thin film|device|memory|synapse", "material"),
    (r"measured|experiment|Raman|transport|imaging|CVD|deposition", "experiment"),
    (r"review|perspective|opportunities|challenges", "review"),
]


def text_of(work):
    return ((work["title"] or "") + " " + (work.get("abstract") or "")).lower()


def competitor_level(title, abstract=""):
    t = (title + " " + abstract).lower()
    best = 0
    hits = []
    for pat, lvl, label in COMPETITOR_TOPICS:
        if re.search(pat, t):
            hits.append((lvl, label))
            best = max(best, lvl)
    return best, hits


def project_relevance(title, abstract=""):
    t = (title + " " + abstract).lower()
    for pat, proj in PROJECT_MAP:
        if re.search(pat, t):
            return proj
    return "adjacent"


def novelty_type(title, abstract=""):
    t = (title + " " + abstract).lower()
    for pat, ntype in NOVELTY_MAP:
        if re.search(pat, t):
            return ntype
    return "other"


def transferability(title, abstract=""):
    t = (title + " " + abstract).lower()
    s = 0
    if re.search(r"shift current|injection current|SHG|second harmonic|photocurrent|BPVE", t):
        s += 1
    if re.search(r"Wannier|tight-binding|first-principles|DFT|screening|high-throughput", t):
        s += 1
    if re.search(r"bilayer|monolayer|2D|van der Waals|sliding|stacking", t):
        s += 1
    return min(3, s)


def urgency(title, abstract, grade, competitor, in_zotero=False):
    u = 0
    if normalize_priority(grade) == MUST:
        u += 2
    if competitor >= 2:
        u += 2
    if competitor == 1:
        u += 1
    if re.search(r"2026|unified|generalized|fractional quantum", (title + " " + abstract).lower()):
        u = min(3, u + 1)
    if in_zotero:
        u = min(3, u + 1)
    return min(3, u)


def enrich_work(conn, work, screening=None, zotero_state=None):
    """为 work 计算多维信号并写 recommendation_signals + rank 字段。"""
    work = dict(work) if not isinstance(work, dict) else work
    title = work["title"]
    abstract = work.get("abstract") or ""
    grade = normalize_priority(
        screening["grade"] if screening else (work.get("signal_grade") or DROP)
    )
    cl, hits = competitor_level(title, abstract)
    proj = project_relevance(title, abstract)
    ntype = novelty_type(title, abstract)
    transf = transferability(title, abstract)
    urg = urgency(title, abstract, grade, cl, bool(zotero_state and zotero_state != "not_in_zotero"))
    sig = {
        "competitor_level": cl, "competitor_hits": [h[1] for h in hits],
        "project_relevance": proj, "novelty_type": ntype,
        "transferability": transf, "urgency": urg,
        "group_priority": 0, "confidence": "medium",
    }
    record_signal(conn, work["work_id"], "project_signals", "la_signals", sig)
    # rank
    tracked = conn.execute("SELECT 1 FROM recommendation_signals WHERE work_id=? AND signal_type='tracked_author'",
                           (work["work_id"],)).fetchone()
    citing = conn.execute("SELECT 1 FROM recommendation_signals WHERE work_id=? AND signal_type IN ('citing_core_work','co_cited','related_to_core_work')",
                          (work["work_id"],)).fetchone()
    score = 0.0
    score += {MUST: 100, WEEKLY: 60, RADAR: 20, DROP: 5}.get(grade, 0)
    score += cl * 10
    score += {"both": 12, "fractional_ferroelectric": 14, "project5": 10, "long_term": 6, "adjacent": 2}.get(proj, 0)
    score += 8 if tracked else 0
    score += 6 if citing else 0
    score += transf * 2
    days_ago = 0
    if work.get("first_seen"):
        try:
            days_ago = (time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")) -
                        time.mktime(time.strptime(work["first_seen"][:10], "%Y-%m-%d"))) / 86400
        except Exception:
            days_ago = 0
    score += max(0, 10 - days_ago)
    reasons = []
    reasons.append(f"grade={grade}")
    if cl:
        reasons.append(f"competitor={cl}")
    reasons.append(f"project={proj}")
    if tracked:
        reasons.append("tracked_author")
    if citing:
        reasons.append("citation_signal")
    conn.execute("UPDATE works SET signal_grade=?, rank_score=? WHERE work_id=?",
                 (grade, round(score, 2), work["work_id"]))
    conn.commit()
    return {"competitor_level": cl, "project_relevance": proj, "novelty_type": ntype,
            "transferability": transf, "urgency": urg, "rank_score": round(score, 2),
            "rank_reason": " + ".join(reasons)}


def competitor_watch(conn=None):
    """生成 competitor_watch.md：最近 MUST/WEEKLY 的竞品状态。"""
    own = conn is None
    conn = conn or db2connect()
    rows = conn.execute("""SELECT w.work_id,w.title,w.doi,w.rank_score,w.signal_grade,
        (SELECT abstract FROM work_sources ws WHERE ws.work_id=w.work_id ORDER BY length(abstract) DESC LIMIT 1) AS abstract,
        (SELECT grade FROM screenings s WHERE s.work_id=w.work_id ORDER BY s.id DESC LIMIT 1) AS grade
        FROM works w WHERE w.work_id NOT LIKE 'test-%' AND w.work_id NOT LIKE 'sim-%'
        ORDER BY w.first_seen DESC LIMIT 400""").fetchall()
    alerts = []
    for r in rows:
        cl, hits = competitor_level(r["title"], r["abstract"] or "")
        if cl >= 2:
            alerts.append({"work_id": r["work_id"], "title": r["title"], "level": cl,
                           "hits": hits, "grade": r["grade"], "doi": r["doi"]})
    lines = ["# Competitor Watch（竞品雷达）", "",
             f"> 统计窗口：最近入库 400 篇中 competitor_level>=2 的 {len(alerts)} 篇。",
             "> competitive ≠ MUST：竞品提醒进入 digest 顶部“竞品预警”，但优先级仍走 research_profile。", ""]
    for a in sorted(alerts, key=lambda x: -x["level"])[:40]:
        lines.append(f"- [L{a['level']}] [{a['grade'] or '?'}] {a['title'][:95]}（{a['doi'] or '-'}）")
        lines.append(f"  - 判据：{', '.join(h[1] for h in a['hits'])}")
    os.makedirs(os.path.dirname(COMPETITOR_PATH), exist_ok=True)
    with open(COMPETITOR_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if own:
        conn.close()
    return len(alerts)
