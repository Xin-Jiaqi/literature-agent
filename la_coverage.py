#!/usr/bin/env python3
"""literature-agent V2 覆盖模块：
- build: 从 Zotero 快照构建 coverage_gold.jsonl（100-200 条 MUST/WEEKLY 期望发现）
- evaluate-coverage: 历史召回评估（exact / enabled source / blocked-only / author-watch / citation-graph / missed）
- coverage-gaps: 覆盖缺口分析
"""
import json
import os
import re
import sqlite3

from la_db2 import load_coverage_gold, write_coverage_gold
from la_priority import MUST, WEEKLY, normalize_priority
from la_v2common import doi_eq, norm_arxiv, norm_doi, read_json, write_json

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import GOLD_ROOT, REPORT_AUDIT_ROOT, DATA_ROOT
GOLD_PATH = os.path.join(GOLD_ROOT, "coverage_gold.jsonl")
EVAL_PATH = os.path.join(REPORT_AUDIT_ROOT, "coverage_eval.md")
GAPS_PATH = os.path.join(REPORT_AUDIT_ROOT, "coverage_gaps.md")

JOURNAL_SOURCES = [
    # (journal_substr, source_id, enabled)
    ("Physical Review Letters", "aps_accepted", True), ("Physical Review Letters", "aps_published", True),
    ("Physical Review B", "aps_accepted", True), ("Physical Review B", "aps_published", True),
    ("Physical Review Materials", "aps_accepted", True), ("Physical Review Materials", "aps_published", True),
    ("npj Computational Materials", "npj_rss", True),
    ("Nature Communications", "nature_rss", True),
    ("Nature Materials", "nmat_rss", True),
    ("Nature Nanotechnology", "nnano_rss", True),
    ("Science Advances", "sciadv_rss", True),
    ("Nano Letters", "acs_rss", False),
    ("ACS Nano", "acs_rss", False),
    ("Journal of Physical Chemistry Letters", "jpcl_rss", False),
    ("JPCL", "jpcl_rss", False),
    ("Advanced Materials", "wiley_rss", False),
    ("Advanced Functional Materials", "wiley_rss", False),
    ("2D Materials", "iop_rss", False),
    ("Applied Physics Letters", "aip_rss", False),
    ("Journal of Applied Physics", "aip_rss", False),
    ("Nano Research", "nanores_rss", False),
]

TOPIC_MAP = [
    (r"分数量子铁电", "fractional_ferroelectric"),
    (r"滑移/堆叠铁电|sliding", "sliding_ferroelectricity"),
    (r"BPVE|bulk photovoltaic|shift current|SHG|injection", "bpve"),
    (r"多重极化|polarization quantum|极化量子", "polarization_quantum"),
    (r"本征铁电|intrinsic", "intrinsic_ferroelectricity"),
    (r"多铁性|multiferroic", "multiferroics"),
    (r"磁性|altermagnet|magnet", "magnetism"),
    (r"对称性|group theory|响应张量|层群|磁群", "symmetry_group_theory"),
    (r"能谷", "valley"),
    (r"拓扑", "topology"),
    (r"激子", "exciton"),
    (r"Wannier|Berry", "wannier_berry"),
    (r"DFT|方法", "method"),
    (r"研究组", "group_work"),
]


def topic_for(collections, title=""):
    text = " ".join(collections) + " " + title
    for pat, t in TOPIC_MAP:
        if re.search(pat, text):
            return t
    return "other"


def journal_sources(journal):
    """Return [(source_id, enabled)] covering this journal (by substring)."""
    if not journal:
        return []
    out = []
    for sub, sid, en in JOURNAL_SOURCES:
        if sub.lower() in journal.lower():
            out.append((sid, en))
    return out


def arxiv_known(entry):
    return bool(entry.get("arxiv_id")) or (entry.get("known_source") or "").startswith("arxiv")


def _lib_entries():
    lib = read_json(os.path.join(DATA_ROOT, "zotero_library.json"), [])
    return [r for r in lib if any(c.startswith("01_核心") for c in r.get("collections", []))]


def _score(r):
    cols = r.get("collections", [])
    tags = r.get("tags", [])
    s = 0
    for c in cols:
        if "02_分数量子铁电" in c:
            s += 8
        elif "02_BPVE" in c:
            s += 6
        elif "01_滑移/堆叠铁电" in c or "sliding" in c:
            s += 5
        elif "04_多铁性" in c:
            s += 4
        elif "05_对称性" in c:
            s += 4
        elif "01_铁电" in c:
            s += 3
        elif "03_磁性" in c:
            s += 2
    for t in tags:
        if t == "可调用":
            s += 5
        if t == "proj-6":
            s += 6
        if t == "proj-5":
            s += 5
    y = r.get("date", "")[:4]
    try:
        yy = int(y)
    except Exception:
        yy = 0
    if yy >= 2026:
        s += 4
    elif yy == 2025:
        s += 3
    elif yy == 2024:
        s += 2
    elif yy == 2023:
        s += 1
    if r.get("journal"):
        js = journal_sources(r["journal"])
        if any(en for _, en in js):
            pass
    return s


def _is_must(r, score):
    topic_in_core = any(c in " ".join(r.get("collections", [])) for c in ("02_BPVE", "04_多铁性", "05_对称性"))
    cols = " ".join(r.get("collections", []))
    tags = r.get("tags", [])
    y = r.get("date", "")[:4]
    core_theory = ("分数量子铁电" in cols) or ("BPVE" in cols and ("基础理论" in cols or "02_计算" in cols))
    strong = ("可调用" in tags) or ("proj-6" in tags) or ("proj-5" in tags)
    recent = y in ("2024", "2025", "2026")
    return ("分数量子铁电" in cols) or ("proj-6" in tags) or         (strong and recent and (score >= 10) and ("BPVE" in cols or topic_in_core))


def build_coverage_gold(force=False, max_entries=150):
    """从 Zotero 快照构建 coverage gold。已有文件且非 force 时只读返回。"""
    if os.path.exists(GOLD_PATH) and not force:
        return load_coverage_gold()
    lib = _lib_entries()
    scored = []
    for r in lib:
        title = (r.get("title") or "").strip()
        if not title:
            continue
        if r.get("type") == "book" and not r.get("DOI"):
            continue
        s = _score(r)
        if s < 4:
            continue
        scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    # 主题配额，避免单一主题灌水
    cap = {"sliding_ferroelectricity": 55, "bpve": 48, "multiferroics": 10, "symmetry_group_theory": 14,
           "magnetism": 20, "intrinsic_ferroelectricity": 8, "fractional_ferroelectric": 6,
           "valley": 8, "topology": 8, "exciton": 8, "wannier_berry": 8, "method": 8, "group_work": 6, "other": 8}
    counts = {}
    entries = []
    for s, r in scored:
        if len(entries) >= max_entries:
            break
        yr = r.get("date", "")[:4]
        if yr.isdigit() and int(yr) < 2020:
            continue
        topic = topic_for(r.get("collections", []), r.get("title", ""))
        if counts.get(topic, 0) >= cap.get(topic, 12):
            continue
        counts[topic] = counts.get(topic, 0) + 1
        e = _entry_from_lib(r, topic, s)
        entries.append(e)
    # 按重要度排序写入
    entries.sort(key=lambda e: (normalize_priority(e["expected_importance"]) == MUST, e["year"] or 0), reverse=True)
    write_coverage_gold(entries)
    return entries


MANUAL_GRADES = {
    "Non-synchronous bulk photovoltaic effect in two-dimensional interlayer-sliding ferroelectrics": MUST,
    "General Theory for Bilayer Stacking Ferroelectricity": MUST,
    "Tailoring Bulk Photovoltaic Effects in Magnetic Sliding Ferroelectric Materials": MUST,
    "Bulk photovoltaic effects in mirror-time symmetric systems": MUST,
}


def _entry_from_lib(r, topic, score):
    title = (r.get("title") or "").strip()
    y = r.get("date", "")[:4]
    try:
        year = int(y)
    except Exception:
        year = None
    doi = norm_doi(r.get("DOI", ""))
    arxiv = ""
    if doi.startswith("10.48550/arxiv."):
        arxiv = norm_arxiv(doi)
    journal = r.get("journal") or ""
    cols = r.get("collections", [])
    tags = r.get("tags", [])
    src = journal_sources(journal)
    known_src = ",".join(sorted(set([sid for sid, _ in src] or ["unknown"])))
    if arxiv:
        known_src = "arxiv," + known_src
    why = _why(topic, journal, cols, tags, year)
    zctx = "Zotero: " + " | ".join(sorted(set(cols))[:4])
    if tags:
        zctx += " | tags: " + ",".join(tags[:6])
    is_must = _is_must(r, score)
    for k, g in MANUAL_GRADES.items():
        if title.startswith(k):
            is_must = (normalize_priority(g) == MUST)
            break
    return {
        "gold_id": "gold_" + (r.get("key") or str(abs(hash((title, doi))) % 10 ** 9)),
        "work_identity": {"title": title, "journal": journal},
        "title": title,
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv,
        "authors": r.get("authors", []),
        "expected_importance": MUST if is_must else WEEKLY,
        "topic": topic,
        "why_should_be_found": why,
        "known_source": known_src,
        "known_first_date": r.get("date", ""),
        "zotero_context": zctx,
        "zotero_key": r.get("key", ""),
        "build_score": score,
    }


def _why(topic, journal, cols, tags, year):
    parts = []
    if "proj-6" in tags:
        parts.append("proj-6 当前项目直接相关")
    if "proj-5" in tags:
        parts.append("project5 直接相关")
    if "可调用" in tags:
        parts.append("用户标记可调用")
    tmap = {
        "fractional_ferroelectric": "FQFE 核心文献（分数/量子铁电理论）",
        "sliding_ferroelectricity": "滑移/堆叠铁电（project5 主线）",
        "bpve": "BPVE/shift current/SHG 理论、计算或实验（P4/P1 主线）",
        "polarization_quantum": "极化量子/多重极化理论",
        "multiferroics": "多铁性与磁电耦合（FQFE×磁性交汇）",
        "symmetry_group_theory": "对称性/群论/响应张量（广义 Neumann 等）",
        "magnetism": "磁性/交错磁（邻接但稳定相关）",
        "intrinsic_ferroelectricity": "本征二维铁电",
    }
    parts.append(tmap.get(topic, "核心相关方向"))
    if journal:
        js = journal_sources(journal)
        if js:
            if any(en for _, en in js):
                parts.append(f"期刊 {journal} 有启用 RSS 覆盖")
            else:
                parts.append(f"期刊 {journal} 目前 blocked，需 Crossref/OpenAlex fallback")
        else:
            parts.append(f"期刊 {journal} 无现成 source，仅靠 arXiv/Crossref/OpenAlex")
    else:
        parts.append("arXiv 预印本，应通过 arXiv API 发现")
    return "；".join(parts)


def _read_gold(path):
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


def load_works(conn):
    return conn.execute("SELECT work_id,title,normalized_title,doi,arxiv_id,journal,historical_discovery,first_seen "
                        "FROM works").fetchall()


def match_work(entry, works):
    """exact discovered: DOI / arXiv / title 匹配现有 works（DOI/arXiv 失败也回退标题）。"""
    doi, arx = norm_doi(entry.get("doi")), norm_arxiv(entry.get("arxiv_id"))
    t = re.sub(r"\s+", " ", (entry.get("title") or "").lower())
    for w in works:
        if doi and doi_eq(w["doi"], doi):
            return w
        if arx and norm_arxiv(w["arxiv_id"]) == arx:
            return w
    # 标题回退（DOI 缺失或版本不同：arXiv↔期刊）
    for w in works:
        wt = re.sub(r"\s+", " ", (w["title"] or "").lower())
        if wt and t and (wt == t or (len(t) > 40 and (t in wt or wt in t))):
            return w
    return None


def author_hits(entry, entity_names):
    hits = []
    for a in entry.get("authors", []):
        al = a.lower()
        for en in entity_names:
            el = en.lower()
            if el and (el in al or al in el) and len(el) >= 4:
                hits.append(en)
    return sorted(set(hits))


def openalex_cites_seed(conn, entry, cache=None):
    """用 OpenAlex 判断 entry 是否引用 citation seed（需 DOI/标题）。"""
    if not entry.get("doi"):
        return None
    from la_v2common import http_get_json
    seeds = [r["doi"] for r in conn.execute(
        "SELECT doi FROM citation_seeds WHERE doi!=''").fetchall()]
    if not seeds:
        return None
    seed_ids = []
    for sdoi in seeds:
        ok, d = http_get_json("https://api.openalex.org/works/https://doi.org/" + norm_doi(sdoi),
                              params={"mailto": "literature-agent@example.com"})
        if ok and d.get("id"):
            seed_ids.append(d["id"].rsplit("/", 1)[-1])
    if not seed_ids:
        return None
    ok, d = http_get_json("https://api.openalex.org/works/https://doi.org/" + norm_doi(entry["doi"]),
                          params={"mailto": "literature-agent@example.com"})
    if not ok or "id" not in d:
        return None
    refs = set((d.get("referenced_works") or []))
    hit = [s for s in seed_ids if ("/W" + s) in refs or s in refs]
    return len(hit) > 0


def evaluate_coverage(conn=None):
    import time
    from la_db2 import connect as db2connect
    own = conn is None
    conn = conn or db2connect()
    gold = _read_gold(GOLD_PATH)
    works = load_works(conn)
    ents = [r["canonical_name"] for r in conn.execute("SELECT canonical_name FROM tracked_entities").fetchall()]
    ent_aliases = []
    for r in conn.execute("SELECT aliases FROM tracked_entities").fetchall():
        ent_aliases += json.loads(r["aliases"] or "[]")
    names = ents + ent_aliases
    t0 = time.time()
    stats = {"total": len(gold), "exact": 0, "inferred_found": 0,
             "enabled_source": 0, "blocked_only": 0,
             "author_watch": 0, "citation_graph": 0, "missed": 0,
             "MUST": 0, "MUST_exact": 0, "MUST_found": 0,
             "WEEKLY": 0, "WEEKLY_exact": 0, "WEEKLY_found": 0,
             "r2026": 0, "r2026_found": 0}
    rows = []
    for e in gold:
        w = match_work(e, works)
        statuses = []
        srcs = journal_sources(e.get("work_identity", {}).get("journal", "") or "")
        enabled = [s for s, en in srcs if en]
        blocked = [s for s, en in srcs if not en]
        if arxiv_known(e) and "arxiv" not in enabled:
            enabled.append("arxiv")
        ah = author_hits(e, names)
        cite = False
        if w is not None:
            stats["exact"] += 1
            statuses.append("exact_discovered")
        if enabled:
            stats["enabled_source"] += 1
            statuses.append("discoverable_by_enabled_source:" + ",".join(enabled))
        elif blocked:
            stats["blocked_only"] += 1
            statuses.append("blocked_source_only:" + ",".join(blocked))
        if ah:
            stats["author_watch"] += 1
            statuses.append("author_watch:" + ",".join(ah[:3]))
        if w is None and not enabled and not ah and (
                normalize_priority(e["expected_importance"]) == MUST or (e.get("year") or 0) >= 2025):
            cite = openalex_cites_seed(conn, e)
            if cite:
                stats["citation_graph"] += 1
                statuses.append("citation_graph")
        if not statuses:
            statuses.append("no_source")
        observed = w is not None
        inferred = observed or bool(enabled) or bool(ah) or cite
        if inferred:
            stats["inferred_found"] += 1
        priority = normalize_priority(e["expected_importance"])
        if priority == MUST:
            stats["MUST"] += 1
            if observed:
                stats["MUST_exact"] += 1
            if inferred:
                stats["MUST_found"] += 1
        else:
            stats["WEEKLY"] += 1
            if observed:
                stats["WEEKLY_exact"] += 1
            if inferred:
                stats["WEEKLY_found"] += 1
        if (e.get("year") or 0) >= 2026:
            stats["r2026"] += 1
            if inferred:
                stats["r2026_found"] += 1
        if not inferred:
            stats["missed"] += 1
        rows.append({"gold_id": e.get("gold_id"), "title": e["title"], "importance": priority,
                     "topic": e.get("topic"), "year": e.get("year"), "doi": e.get("doi"),
                     "known_source": e.get("known_source"), "status": statuses,
                     "found": inferred, "observed": observed, "inferred": inferred,
                     "enabled_discoverable": bool(enabled),
                     "matched_work": w["work_id"] if w else None, "author_watch": ah})
    eval_elapsed = time.time() - t0
    stats["eval_elapsed_s"] = round(eval_elapsed, 1)
    if stats["total"]:
        stats["observed_recall_overall"] = round(stats["exact"] / stats["total"], 4)
        stats["recall_overall"] = round(stats["inferred_found"] / stats["total"], 4)
        stats["observed_recall_MUST"] = round(stats["MUST_exact"] / stats["MUST"], 4) if stats["MUST"] else None
        stats["observed_recall_WEEKLY"] = round(stats["WEEKLY_exact"] / stats["WEEKLY"], 4) if stats["WEEKLY"] else None
        stats["recall_MUST"] = round(stats["MUST_found"] / stats["MUST"], 4) if stats["MUST"] else None
        stats["recall_WEEKLY"] = round(stats["WEEKLY_found"] / stats["WEEKLY"], 4) if stats["WEEKLY"] else None
        stats["recall_2026"] = round(stats["r2026_found"] / stats["r2026"], 4) if stats["r2026"] else None
    _write_eval(stats, rows)
    if own:
        conn.close()
    return stats


def _write_eval(stats, rows):
    lines = ["# Coverage Evaluation（历史召回评估）", "",
             f"- gold 总数：{stats['total']}｜MUST：{stats['MUST']}｜WEEKLY：{stats['WEEKLY']}｜2026 近期：{stats['r2026']}",
             f"- exact discovered（已在 DB）：{stats['exact']}",
             f"- discoverable by enabled sources：{stats['enabled_source']}",
             f"- blocked-source only：{stats['blocked_only']}",
             f"- author-watch discoverable：{stats['author_watch']}",
             f"- citation-graph discoverable：{stats['citation_graph']}",
             f"- completely missed：{stats['missed']}",
             "", "## 指标", "",
             f"- observed overall recall（确已入库）：{stats.get('observed_recall_overall')}",
             f"- observed MUST recall（确已入库）：{stats.get('observed_recall_MUST')}",
             f"- observed WEEKLY recall（确已入库）：{stats.get('observed_recall_WEEKLY')}",
             f"- inferred overall coverage（理论可发现）：{stats.get('recall_overall')}",
             f"- inferred MUST coverage：{stats.get('recall_MUST')}",
             f"- inferred WEEKLY coverage：{stats.get('recall_WEEKLY')}",
             f"- inferred recent-2026 coverage：{stats.get('recall_2026')}",
             "",
             "> 说明：exact/enabled/blocked/author/citation 均为来源**理论覆盖**判断（inferred），",
             "> 历史 RSS 不可回溯，未伪造 observed 数据。citation 判断基于 OpenAlex referenced_works。",
             "", "## 最危险漏检（expected MUST/WEEKLY 且现有启用 source 无法发现）", ""]
    danger = [r for r in rows if not r["found"] and r["importance"] == MUST] + \
             [r for r in rows if not r["found"] and r["importance"] == WEEKLY and (r["year"] or 0) >= 2026]
    if not danger:
        lines.append("（无）")
    for r in danger[:40]:
        src = ",".join(r["status"]) if r["status"] else "no source at all"
        lines.append(f"- [{r['importance']}] {r['title'][:90]}（{r['year']}｜{r['topic']}｜{src}）")
    lines += ["", "## 已可发现但未入库（enabled-source 理论覆盖）", ""]
    cand = [r for r in rows if not r["observed"] and r["enabled_discoverable"]]
    for r in cand[:25]:
        lines.append(f"- [{r['importance']}] {r['title'][:90]}（{r['year']}｜{r['known_source']}）")
    lines += ["", "## 完整明细", ""]
    lines.append("| 重要性 | 标题 | 年份 | 主题 | 状态 | 作者-watch |")
    lines.append("|---|---|---|---|---|---|")
    for r in rows:
        st = ",".join(r["status"]) if r["status"] else "MISSED"
        aw = ",".join(r["author_watch"][:2]) or "-"
        lines.append(f"| {r['importance']} | {r['title'][:70]} | {r['year']} | {r['topic']} | {st[:80]} | {aw} |")
    os.makedirs(os.path.dirname(EVAL_PATH), exist_ok=True)
    with open(EVAL_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return EVAL_PATH


def coverage_gaps(conn=None):
    from la_db2 import connect as db2connect
    own = conn is None
    conn = conn or db2connect()
    gold = load_coverage_gold()
    works = load_works(conn)
    _erows = conn.execute("SELECT canonical_name,aliases,confidence FROM tracked_entities").fetchall()
    ents = [r["canonical_name"] for r in _erows]
    names = ents + [a for r in _erows for a in json.loads(r["aliases"] or "[]")]
    by_topic = {}
    by_blocked_pub = {}
    low_conf_authors = [r["canonical_name"] for r in _erows if r["confidence"] == "low"]
    for e in gold:
        w = match_work(e, works)
        t = e.get("topic", "other")
        d = by_topic.setdefault(t, {"total": 0, "found": 0, "missed": 0, "blocked": 0, "recent": 0})
        d["total"] += 1
        if w is not None:
            d["found"] += 1
        else:
            srcs = journal_sources(e.get("work_identity", {}).get("journal", "") or "")
            en = [s for s, x in srcs if x]
            bl = [s for s, x in srcs if not x]
            if arxiv_known(e):
                en.append("arxiv")
            if en:
                d["found"] += 1
            elif bl:
                d["blocked"] += 1
            else:
                d["missed"] += 1
        if (e.get("year") or 0) >= 2026:
            d["recent"] += 1
        j = (e.get("work_identity") or {}).get("journal", "")
        for sub, sid, en in JOURNAL_SOURCES:
            if sub.lower() in j.lower() and not en:
                p = by_blocked_pub.setdefault(sid, {"journal": sub, "gold": 0, "missed": 0})
                p["gold"] += 1
                if w is None:
                    p["missed"] += 1
    lines = ["# Coverage Gap Analysis", "",
             "按“实际漏掉重要论文的风险”排序（risk = blocked/missed 的 MUST/WEEKLY 数）。", ""]
    risks = []
    for t, d in by_topic.items():
        risk = d["blocked"] + d["missed"]
        risks.append((risk, t, d))
    risks.sort(key=lambda x: -x[0])
    lines.append("## 主题覆盖强度")
    lines.append("| 主题 | 总 | 可发现 | blocked | 完全无源 | 2026+ | 风险 |")
    lines.append("|---|---|---|---|---|---|---|")
    for risk, t, d in risks:
        lines.append(f"| {t} | {d['total']} | {d['found']} | {d['blocked']} | {d['missed']} | {d['recent']} | {'HIGH' if risk >= 4 else 'MID' if risk >= 2 else 'LOW'} |")
    lines += ["", "## blocked 出版社造成的实际漏检", ""]
    for sid, p in sorted(by_blocked_pub.items(), key=lambda x: -x[1]["missed"]):
        lines.append(f"- {p['journal']}（{sid}）：gold {p['gold']} 篇，其中未发现 {p['missed']} 篇")
    lines += ["", "## 重点作者可跟踪性", ""]
    lines.append(f"- 已解析 tracked entities：{len(ents)}；low-confidence（不自动强信号）：{len(low_conf_authors)}")
    if low_conf_authors:
        lines.append("  - " + ", ".join(low_conf_authors))
    lines += ["", "## arXiv 能补的缺口", ""]
    arx_gap = [r for r in gold if not match_work(r, works) and arxiv_known(r)]
    lines.append(f"- gold 中带 arXiv 版本但未入库：{len(arx_gap)} 篇（backfill/每日 arXiv 抓取可覆盖）")
    lines += ["", "## Crossref / OpenAlex 能补的缺口", ""]
    doi_no_src = [r for r in gold if not match_work(r, works) and r.get("doi") and not arxiv_known(r)]
    lines.append(f"- 带 DOI 且无现成启用 source：{len(doi_no_src)} 篇（Crossref 期刊 fallback / OpenAlex DOI 检索可覆盖）")
    lines += ["", "## 只能靠 Scholar / 公众号 / 人工发现的", ""]
    hard = [r for r in gold if not match_work(r, works) and not arxiv_known(r)
            and not journal_sources((r.get("work_identity") or {}).get("journal", ""))]
    for r in hard[:15]:
        lines.append(f"- [{r['expected_importance']}] {r['title'][:90]}（{r.get('year')}｜{(r.get('work_identity') or {}).get('journal', '?')}）")
    os.makedirs(os.path.dirname(GAPS_PATH), exist_ok=True)
    with open(GAPS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if own:
        conn.close()
    return GAPS_PATH


def gold_summary():
    g = load_coverage_gold()
    for item in g:
        item["expected_importance"] = normalize_priority(item.get("expected_importance"))
    from collections import Counter
    return {"total": len(g), "grades": dict(Counter(x["expected_importance"] for x in g)),
            "topics": dict(Counter(x.get("topic") for x in g)),
            "with_doi": sum(1 for x in g if x.get("doi")),
            "with_arxiv": sum(1 for x in g if x.get("arxiv_id"))}
