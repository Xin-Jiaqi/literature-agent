#!/usr/bin/env python3
"""literature-agent digest：daily + weekly。

推荐优先级始终使用 MUST/WEEKLY/RADAR/DROP；A/B/C/D 只指阅读工作流。
  ## Zotero 决策队列
  ## 版本更新 / SI / 低置信度
  ## Source Health
"""
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta

from la_registry import source_meta
from la_priority import DROP, MUST, RADAR, WEEKLY

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import REPORT_ROOT, QUEUES_ROOT
DAILY_DIR = os.environ.get("LITAGENT_DAILY_DIR", os.path.join(REPORT_ROOT, "daily"))
WEEKLY_DIR = os.environ.get("LITAGENT_WEEKLY_DIR", os.path.join(REPORT_ROOT, "weekly"))
ZOTERO_QUEUE_PATH = os.path.join(QUEUES_ROOT, "zotero_queue.jsonl")

PROFILE_PROJECT = {
    "P1-fqfe": "P1 FQFE×NLO（态间切换规则）",
    "P1-injection": "P1 FQFE×NLO（circular injection）",
    "P1-wannier": "P4 方法（Wannier 插值）",
    "P1-neumann": "P1/P4 理论（Neumann 原理）",
    "P2-bpve": "P2 单层 BPVE 材料发现",
    "P2-sliding": "P2 滑移铁电×BPVE",
    "P2-stack": "P2 堆叠对称性",
    "P3-switch": "P3 切换耦合",
    "P4-theory": "P4 BPVE 理论",
    "P4-method": "P4 计算方法",
}
CORE_ORDER = ["P1-fqfe", "P1-injection", "P2-sliding", "P2-bpve", "P3-switch", "P2-stack", "P1-wannier", "P1-neumann", "P4-theory", "P4-method"]


def _latest_screening(conn, work_id):
    return conn.execute("SELECT * FROM screenings WHERE work_id=? ORDER BY id DESC LIMIT 1", (work_id,)).fetchone()


def _project_of(matched):
    tags = [t for t in (matched or "").split(",") if t]
    for t in CORE_ORDER:
        if t in tags:
            return PROFILE_PROJECT.get(t, t)
    return None


def _competitor_hint(matched):
    tags = set((matched or "").split(","))
    if {"P2-sliding", "P2-bpve"} & tags:
        return "可能（与 project5 直接重叠，建议核对）"
    if {"P1-fqfe", "P1-injection"} & tags:
        return "可能（与 P1 直接重叠，建议核对）"
    return "待定（无明显直接重叠）"


def _interesting_radar(conn, today, rows, limit=3):
    """从本轮 RADAR 中选最多 3 篇值得扫一眼的。"""
    scored = []
    for s in rows:
        if s["grade"] != RADAR:
            continue
        tags = s["matched_profile"] or ""
        score = 0
        for i, t in enumerate(CORE_ORDER):
            if t in tags:
                score += (10 - i)
        if re.search(r"sliding|shift current|photogalvanic|altermagnet|fractional|ferroelectric|wannier|quantum geometr", (s["title"] or ""), re.I):
            score += 5
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:limit]]


def _source_health_table(conn):
    from la_health import source_health
    h = source_health(conn)
    lines = ["| Source | Status | Items | Lat(ms) | 说明 |", "|---|---|---|---|---|"]
    for sid in sorted(h, key=lambda k: (h[k]["status"] != "OK", h[k]["priority"])):
        x = h[sid]
        lines.append(f"| {sid} | {x['status']} | {x['last_items']} | {x['last_latency_ms']} | {x['detail'][:40]} |")
    return "\n".join(lines)


def _zotero_queue_section(conn, today):
    rows = conn.execute("""SELECT s.*, w.title, w.doi, w.arxiv_id FROM screenings s
        JOIN works w ON w.work_id = s.work_id
        WHERE w.consumed_history=0 AND date(s.screened_at)=? AND s.grade IN ('MUST','WEEKLY')
          AND s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=s.work_id)
        ORDER BY s.id""", (today,)).fetchall()
    if not rows:
        return "本轮无新入队条目。"
    import literature_agent as la
    lines = []
    for s in rows:
        work = conn.execute("SELECT * FROM works WHERE work_id=?", (s["work_id"],)).fetchone()
        main, compat, ztags = la.zotero_suggestion(work, s)
        lines.append(f"- {s['title']}｜{s['grade']}｜主 Collection：{main}｜Tags：{'、'.join(ztags)}")
        lines.append(f"  - 处理：`python3 literature_agent.py zotero-mark {s['work_id']} accept|reject|defer`")
    return "\n".join(lines)


def render_daily_v2(conn, today):
    import literature_agent as la
    new_works = conn.execute("SELECT * FROM works WHERE consumed_history=0 AND first_seen=?", (today,)).fetchall()
    screenings = conn.execute("""SELECT s.*, w.title, w.authors, w.doi, w.arxiv_id, w.journal
        FROM screenings s JOIN works w ON w.work_id=s.work_id
        WHERE w.consumed_history=0 AND date(s.screened_at)=? ORDER BY s.id""", (today,)).fetchall()
    proc = conn.execute("""SELECT p.*, w.title FROM processing p JOIN works w ON w.work_id=p.work_id
        WHERE date(p.processed_at)=? ORDER BY p.processed_at""", (today,)).fetchall()
    version_updates = conn.execute("""SELECT work_id,title FROM works WHERE version_changed=1
        AND date(latest_seen)=?""", (today,)).fetchall()
    by_grade = Counter(s["grade"] for s in screenings)
    must_rows = [s for s in screenings if s["grade"] == MUST]
    weekly_rows = [s for s in screenings if s["grade"] == WEEKLY]
    proc_by_work = {p["work_id"]: p for p in proc}
    pend_dedup = conn.execute("SELECT COUNT(*) c FROM dedup_review WHERE status='pending'").fetchone()["c"]

    lines = [f"# 每日文献简报 {today}\n", "## 今天真正需要我看的\n"]
    if must_rows:
        lines.append(f"- **{len(must_rows)} 篇 MUST**，优先查看 A 结果：")
        for s in must_rows:
            lines.append(f"  - {s['title']}（{s['reason'][:60]}）")
    else:
        lines.append("**今天没有必须立即阅读的论文。**")
    if pend_dedup:
        lines.append(f"- ⚠️ 去重待裁决 {pend_dedup} 对（`python3 literature_agent.py dedup-review`）")
    lines.append("")
    lines.append("## 今日概览")
    lines.append(
        f"- 新 work：{len(new_works)}｜MUST：{by_grade.get(MUST, 0)}｜"
        f"WEEKLY：{by_grade.get(WEEKLY, 0)}｜RADAR：{by_grade.get(RADAR, 0)}｜"
        f"DROP：{by_grade.get(DROP, 0)}\n"
    )

    lines.append("## MUST｜必须知道\n")
    if not must_rows:
        lines.append("本轮无 MUST。\n")
    for s in must_rows:
        work = conn.execute("SELECT * FROM works WHERE work_id=?", (s["work_id"],)).fetchone()
        main, compat, ztags = la.zotero_suggestion(work, s)
        p = proc_by_work.get(s["work_id"])
        lines.append(f"### {s['title']}\n")
        lines.append(f"- 来源 / 期刊：{s['journal'] or '未确认'}（screened_at: {s['screened_at']}）")
        lines.append(f"- DOI / arXiv：{s['doi'] or '—'} / {s['arxiv_id'] or '—'}")
        lines.append(f"- 为什么现在值得读：{s['reason']}")
        lines.append(f"- 对应我的研究：{s['matched_profile']}")
        lines.append(f"- A 建档路径：`{p['a_path'] if p else '未处理'}`")
        lines.append(f"- PDF：{'成功' if p and p['pdf_status']=='ok' else (p['pdf_status'] if p else '未处理')} `{p['pdf_path'] if p else ''}`")
        lines.append(f"- SI：{'成功' if p and p['si_status']=='ok' else (p['si_status'] if p else '未检查')} `{p['si_path'] if p else ''}`")
        lines.append(f"- 建议动作：精读 → 进 Zotero（主 Collection：{main}）\n")

    lines.append("## WEEKLY｜本周值得读\n")
    if not weekly_rows:
        lines.append("本轮无 WEEKLY。\n")
    for s in weekly_rows:
        work = conn.execute("SELECT * FROM works WHERE work_id=?", (s["work_id"],)).fetchone()
        main, compat, ztags = la.zotero_suggestion(work, s)
        proj = _project_of(s["matched_profile"])
        lines.append(f"### {s['title']}")
        lines.append(f"- 来源 / 期刊：{s['journal'] or '未确认'}｜DOI/arXiv：{s['doi'] or s['arxiv_id'] or '—'}")
        lines.append(f"- 最相关项目：{proj or '待定'}")
        lines.append(f"- 阅读理由：{s['reason']}")
        lines.append(f"- 竞品提示：{_competitor_hint(s['matched_profile'])}")
        lines.append("- 本周建议：建议本周阅读")
        lines.append(f"- 主 Collection：{main}｜兼容：{'；'.join(compat) if compat else '—'}｜Tags：{'、'.join(ztags)}\n")

    ib = _interesting_radar(conn, today, screenings)
    lines.append("## RADAR 精选（值得扫一眼）\n")
    if ib:
        for s in ib:
            lines.append(f"- {s['title']}（{s['matched_profile'] or '邻接'}）")
    else:
        lines.append("本轮没有值得扫一眼的 RADAR 条目。")
    lines.append("")

    lines.append("## Zotero 决策队列（MUST + 可收藏 WEEKLY）\n")
    lines.append(_zotero_queue_section(conn, today))
    lines.append("")

    lines.append("## 系统提示\n")
    fails = [s for s in screenings if s["confidence"] in ("中", "低")]
    if fails:
        lines.append("- 低置信度判断：")
        for s in fails:
            lines.append(f"  - {s['title'][:80]}（{s['confidence']}）")
    no_si = [p for p in proc if p["si_status"] != "ok"]
    if no_si:
        lines.append("- SI 未获取：")
        for p in no_si:
            lines.append(f"  - {p['title'][:80]}（{p['si_status']}）")
    if version_updates:
        lines.append("- 版本更新：")
        for v in version_updates:
            lines.append(f"  - {v['title'][:80]}（work_id: {v['work_id']}）")
    lines.append("")
    lines.append("## Source Health\n")
    lines.append(_source_health_table(conn))
    lines.append("")
    return "\n".join(lines)


def run_daily_v2(conn, today):
    os.makedirs(DAILY_DIR, exist_ok=True)
    path = os.path.join(DAILY_DIR, f"{today}.md")
    content = render_daily_v2(conn, today)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    conn.execute("INSERT OR REPLACE INTO digests (date,path,summary_json) VALUES (?,?,?)",
                 (today, path, json.dumps({"bytes": len(content), "version": 2}, ensure_ascii=False)))
    conn.commit()
    return path


# ---------------- weekly ----------------

TREND_BUCKETS = {
    "FQFE": r"fractional (quantum )?ferroelectric|quantum ferroelectric|polarization quantum",
    "sliding FE": r"sliding ferroelectric|interlayer sliding|stacking ferroelectric",
    "BPVE / NLO": r"shift current|bulk photovoltaic|photogalvanic|injection current|second harmonic|nonlinear optical|rectification",
    "magnetism": r"altermagnet|skyrmion|spin group|magnetic space group|multiferroic",
    "methods": r"wannier|quantum metric|berry phase|first-principles framework|gw-bse|tight-binding",
    "stacking/2D": r"bilayer|heterobilayer|twisted|moir\\u00e9|van der waals|monolayer",
}


def _iso_week(day=None):
    day = day or datetime.now()
    return day.strftime("%Y-W%W")


def render_weekly(conn, today=None):
    today = today or datetime.now().strftime("%Y-%m-%d")
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    new_works = conn.execute("SELECT * FROM works WHERE consumed_history=0 AND first_seen>=?", (since,)).fetchall()
    screenings = conn.execute("""SELECT s.*, w.title, w.authors, w.doi, w.arxiv_id, w.journal
        FROM screenings s JOIN works w ON w.work_id=s.work_id
        WHERE w.consumed_history=0 AND date(s.screened_at)>=?
          AND s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=s.work_id)
        ORDER BY s.id""", (since,)).fetchall()
    by_grade = Counter(s["grade"] for s in screenings)
    must_rows = [s for s in screenings if s["grade"] == MUST]
    weekly_rows = [s for s in screenings if s["grade"] == WEEKLY]
    radar_rows = [s for s in screenings if s["grade"] == RADAR]

    # 来源贡献（按新 work 首见来源）
    src_new = Counter()
    for nw in new_works:
        for r in conn.execute("SELECT DISTINCT source FROM work_sources WHERE work_id=?", (nw["work_id"],)).fetchall():
            src_new[r["source"]] += 1

    # 有效命中率：该来源新 work 中 MUST/WEEKLY 数量
    src_hits = Counter()
    for s in screenings:
        for r in conn.execute("SELECT DISTINCT source FROM work_sources WHERE work_id=?", (s["work_id"],)).fetchall():
            if s["grade"] in (MUST, WEEKLY):
                src_hits[r["source"]] += 1

    processed_a = conn.execute("""SELECT COUNT(*) FROM processing p JOIN works w ON w.work_id=p.work_id
        WHERE p.a_status='ok' AND date(p.processed_at)>=?""", (since,)).fetchone()[0]

    # 本周最重要 5 篇：MUST 优先，其次 WEEKLY，按 profile 优先级排序
    def rank(s):
        tags = s["matched_profile"] or ""
        score = 0 if s["grade"] == MUST else 5
        for i, t in enumerate(CORE_ORDER):
            if t in tags:
                score += i
        return score
    top = sorted(must_rows + weekly_rows, key=rank)[:5]

    # 趋势：只统计本周真实 MUST/WEEKLY/RADAR
    def _text_of(s):
        row = conn.execute("SELECT abstract FROM work_sources WHERE work_id=? LIMIT 1", (s["work_id"],)).fetchone()
        return ((s["title"] or "") + " " + (row["abstract"] if row else "")).lower()
    trend = Counter()
    for s in must_rows + weekly_rows + radar_rows:
        text = _text_of(s)
        for bucket, pat in TREND_BUCKETS.items():
            if re.search(pat, text):
                trend[bucket] += 1

    lines = [f"# 本周文献复盘 {_iso_week()}\n", f"- 统计窗口：{since} ~ {today}\n",
             "## 概览\n",
             f"- 新 work 总数：{len(new_works)}",
             f"- MUST：{by_grade.get(MUST, 0)}｜WEEKLY：{by_grade.get(WEEKLY, 0)}｜"
             f"RADAR：{by_grade.get(RADAR, 0)}｜DROP：{by_grade.get(DROP, 0)}",
             f"- 实际完成 A 建档：{processed_a}",
             f"- 来源贡献（新 work 数）：{dict(src_new)}",
             "- 有效命中率（来源产出 MUST/WEEKLY 数 / 该来源新增 work）："]
    for sid, n in sorted(src_new.items(), key=lambda x: -x[1]):
        hits = src_hits.get(sid, 0)
        lines.append(f"  - {sid}：{hits}/{n}（MUST/WEEKLY 命中）")
    lines.append("")
    lines.append("## 本周最重要 5 篇（按 research_profile 价值，非期刊）\n")
    if not top:
        lines.append("本周没有 MUST/WEEKLY 论文。\n")
    for i, s in enumerate(top, 1):
        proj = _project_of(s["matched_profile"])
        lines.append(f"{i}. **[{s['grade']}] {s['title']}**")
        lines.append(f"   - {proj or '待定'}｜{s['reason'][:100]}")
    lines.append("")
    lines.append("## 趋势（仅依据本周真实候选）\n")
    if not trend:
        lines.append("本周 MUST/WEEKLY/RADAR 候选太少，不足以形成趋势判断。\n")
    else:
        lines.append("| 方向 | 本周出现次数 |")
        lines.append("|---|---|")
        for bucket, n in trend.most_common():
            lines.append(f"| {bucket} | {n} |")
        lines.append("")
        lines.append("观察：")
        hot = trend.most_common(2)
        for bucket, n in hot:
            lines.append(f"- {bucket} 出现 {n} 次，是本周最活跃方向之一。")
    return "\n".join(lines)


def run_weekly(conn, today=None):
    os.makedirs(WEEKLY_DIR, exist_ok=True)
    today = today or datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(WEEKLY_DIR, f"{_iso_week(datetime.strptime(today, '%Y-%m-%d'))}.md")
    content = render_weekly(conn, today)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ================= V3 daily / V2 weekly =================

def _explain(conn, work, screening):
    """每篇 MUST/WEEKLY 的推荐解释。"""
    sigs = conn.execute("SELECT signal_type,source,detail_json FROM recommendation_signals WHERE work_id=?",
                        (work["work_id"],)).fetchall()
    extra = []
    for s in sigs:
        d = json.loads(s["detail_json"] or "{}")
        if s["signal_type"] == "tracked_author":
            extra.append(f"tracked_author（{d.get('entity','?')}）")
        elif s["signal_type"] == "citing_core_work":
            extra.append(f"引用核心工作（{d.get('seed','?')[:40]}）")
        elif s["signal_type"] == "co_cited":
            extra.append("co-cited（多核心种子共同被引）")
        elif s["signal_type"] == "related_to_core_work":
            extra.append("related-to-core")
        elif s["signal_type"] == "prefetch":
            extra.append("已 prefetch")
        elif s["signal_type"] == "competitor":
            extra.append("竞品信号")
    extra = list(dict.fromkeys(extra))
    proj_sig = [s for s in sigs if s["signal_type"] == "project_signals"]
    if proj_sig:
        d = json.loads(proj_sig[-1]["detail_json"] or "{}")
        extra.append(f"competitor={d.get('competitor_level',0)} project={d.get('project_relevance','?')}")
    why_not_must = ""
    if screening["grade"] == WEEKLY:
        why_not_must = "未直接命中当前项目核心推进点（达到 WEEKLY，但未达 MUST）"
    return extra, why_not_must


def _pick_one_weekly(weekly_rows, conn):
    """每日最多 1 篇 WEEKLY 快读建议（rank_score 最高且非 already-read）。"""
    if not weekly_rows:
        return None
    best = None
    for s in weekly_rows:
        w = conn.execute("SELECT work_id,rank_score,title FROM works WHERE work_id=?", (s["work_id"],)).fetchone()
        if w is None:
            continue
        if best is None or (w["rank_score"] or 0) > best[1]:
            best = (s, w["rank_score"] or 0)
    return best[0] if best else weekly_rows[0]


def _group_dynamics(conn, today):
    rows = conn.execute("""SELECT DISTINCT w.work_id,w.title,w.journal,w.doi FROM works w
        JOIN recommendation_signals sg ON sg.work_id=w.work_id AND sg.signal_type='tracked_author'
        WHERE w.historical_discovery=0 AND w.first_seen=? ORDER BY w.first_seen DESC LIMIT 8""", (today,)).fetchall()
    out = []
    for r in rows:
        ents = [json.loads(s["detail_json"] or "{}").get("entity", "?")
                for s in conn.execute("SELECT detail_json FROM recommendation_signals WHERE work_id=? AND signal_type='tracked_author'",
                                      (r["work_id"],)).fetchall()]
        out.append((r, sorted(set(ents))))
    return out


def _citation_finds(conn, today):
    rows = conn.execute("""SELECT DISTINCT w.work_id,w.title,w.doi FROM works w
        JOIN recommendation_signals sg ON sg.work_id=w.work_id
          AND sg.signal_type IN ('citing_core_work','co_cited','related_to_core_work')
        WHERE w.historical_discovery=0 AND w.first_seen=?
        AND (SELECT grade FROM screenings s WHERE s.work_id=w.work_id ORDER BY s.id DESC LIMIT 1) IN ('MUST','WEEKLY')
        ORDER BY w.first_seen DESC LIMIT 6""", (today,)).fetchall()
    return rows


def _discovery_path(conn, work_id):
    sources = [
        r["source"] for r in conn.execute(
            "SELECT DISTINCT source FROM work_sources WHERE work_id=? ORDER BY source", (work_id,)
        ).fetchall()
    ]
    return " + ".join(sources) if sources else "unknown"


def render_daily_v3(conn, today):
    import literature_agent as la
    from la_workflow import a_radar_excerpt, workflow_item
    new_works = conn.execute("""SELECT * FROM works WHERE consumed_history=0 AND first_seen=? AND historical_discovery=0""",
                             (today,)).fetchall()
    screenings = conn.execute("""SELECT s.*, w.title, w.authors, w.doi, w.arxiv_id, w.journal
        FROM screenings s JOIN works w ON w.work_id=s.work_id
        WHERE w.consumed_history=0 AND date(s.screened_at)=? AND w.historical_discovery=0
          AND s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=s.work_id)
        ORDER BY s.id""",
                              (today,)).fetchall()
    proc = conn.execute("""SELECT p.*, w.title FROM processing p JOIN works w ON w.work_id=p.work_id
        WHERE date(p.processed_at)=? ORDER BY p.processed_at""", (today,)).fetchall()
    by_grade = Counter(s["grade"] for s in screenings)
    must_rows = [s for s in screenings if s["grade"] == MUST]
    weekly_rows = [s for s in screenings if s["grade"] == WEEKLY]
    proc_by_work = {p["work_id"]: p for p in proc}
    pend_dedup = conn.execute("SELECT COUNT(*) c FROM dedup_review WHERE status='pending'").fetchone()["c"]
    # 竞品预警（今日 MUST/WEEKLY + competitor>=2）
    comp_rows = []
    for s in must_rows + weekly_rows:
        from la_signals import competitor_level
        w = conn.execute("SELECT title FROM works WHERE work_id=?", (s["work_id"],)).fetchone()
        cl, hits = competitor_level(s["title"], "")
        if cl >= 2:
            comp_rows.append((s, cl, hits))
    # 信号 enrich（MUST/WEEKLY）
    for s in must_rows + weekly_rows:
        w = conn.execute("SELECT * FROM works WHERE work_id=?", (s["work_id"],)).fetchone()
        if w is not None:
            try:
                from la_signals import enrich_work
                enrich_work(conn, w, screening=s)
            except Exception:
                pass
    one_weekly = _pick_one_weekly(weekly_rows, conn)

    lines = ["# 每日文献雷达 " + today, ""]
    # 今天必须知道的
    lines.append("## 今天必须知道的\n")
    must = must_rows + [c[0] for c in comp_rows if c[0] not in must_rows]
    if must:
        for s in must:
            wf = workflow_item(conn, s["work_id"])
            excerpt = a_radar_excerpt(wf["a_path"]) if wf and wf["a_status"] == "completed" else {}
            lines.append(f"- **[{s['grade']}] {s['title']}**")
            if excerpt:
                lines.append(f"  - A 一句话：{excerpt.get('one_line', '')}")
                lines.append(f"  - 对你的价值：{excerpt.get('value', '')}")
            else:
                lines.append(f"  - {s['reason'][:120]}")
                lines.append(f"  - A 状态：{wf['a_status'] if wf else '未准备'}")
    else:
        lines.append("今天没有必须立即处理的新论文。")
    if pend_dedup:
        lines.append(f"- ⚠️ 去重待裁决 {pend_dedup} 对（`python3 literature_agent.py dedup-review`）")
    lines.append("")
    # 竞品预警
    lines.append("## 竞品预警\n")
    if comp_rows:
        for s, cl, hits in sorted(comp_rows, key=lambda x: -x[1]):
            lines.append(f"- [L{cl}] **{s['title']}**")
            lines.append(f"  - 判据：{', '.join(h[1] for h in hits)}｜优先级仍是 {s['grade']}（竞品 ≠ 自动升级 MUST）")
    else:
        lines.append("今日无 competitor_level>=2 的论文。")
    lines.append("")
    # MUST
    lines.append("## MUST｜必须知道（自动执行 A）\n")
    if not must_rows:
        lines.append("本轮无 MUST。\n")
    for s in must_rows:
        work = conn.execute("SELECT * FROM works WHERE work_id=?", (s["work_id"],)).fetchone()
        if work is None:
            continue
        extra, why_not_must = _explain(conn, work, s)
        wf = workflow_item(conn, s["work_id"])
        excerpt = a_radar_excerpt(wf["a_path"]) if wf and wf["a_status"] == "completed" else {}
        lines.append(f"### {s['title']}\n")
        lines.append(f"- 发现路径：{_discovery_path(conn, s['work_id'])}｜screened_at: {s['screened_at']}")
        lines.append(f"- 额外信号：{('；'.join(extra)) if extra else '（无，纯语义命中）'}")
        lines.append(f"- 为什么与你相关：{s['reason']}")
        lines.append(f"- 为什么不是 MUST：{why_not_must or '已是 MUST'}")
        lines.append(f"- DOI / arXiv：{s['doi'] or '—'} / {s['arxiv_id'] or '—'}")
        lines.append(f"- A 建档：{wf['a_status'] if wf else '未准备'}｜{wf['a_path'] if wf else ''}")
        lines.append(f"- PDF：{wf['pdf_status'] if wf else '未处理'}｜SI：{wf['si_status'] if wf else '未检查'}")
        if excerpt:
            lines.append(f"- A 一句话：{excerpt.get('one_line', '')}")
            lines.append(f"- 新贡献：{excerpt.get('contribution', '')}")
            lines.append(f"- 核心证据：{excerpt.get('evidence', '')}")
            lines.append(f"- 对你的价值：{excerpt.get('value', '')}")
            lines.append(f"- A 建议：{excerpt.get('priority', '')}")
        lines.append(
            f"- 你的决定：python3 literature_agent.py decide {s['work_id']} "
            "read|save_zotero|later|ignore|competitor\n"
        )
    # WEEKLY
    lines.append("## WEEKLY｜本周值得读\n")
    if not weekly_rows:
        lines.append("本轮无 WEEKLY。\n")
    for s in weekly_rows:
        work = conn.execute("SELECT * FROM works WHERE work_id=?", (s["work_id"],)).fetchone()
        if work is None:
            continue
        extra, why_not_must = _explain(conn, work, s)
        rank = work["rank_score"] or 0
        proj_sig = [x for x in conn.execute(
            "SELECT detail_json FROM recommendation_signals WHERE work_id=? AND signal_type='project_signals'",
            (work["work_id"],)).fetchall()]
        proj = "?"
        cl = 0
        if proj_sig:
            d = json.loads(proj_sig[-1]["detail_json"] or "{}")
            proj = d.get("project_relevance", "?")
            cl = d.get("competitor_level", 0)
        upgrade = (rank >= 80 and proj in ("both", "fractional_ferroelectric", "project5")) or cl >= 2
        adv = "建议升级为 MUST" if upgrade else "建议保持 WEEKLY"
        lines.append(f"### {s['title']}")
        lines.append(f"- 发现路径：{_discovery_path(conn, s['work_id'])}｜额外信号：{('；'.join(extra)) if extra else '（无）'}")
        lines.append(f"- 阅读理由：{s['reason']}｜{why_not_must}")
        lines.append(f"- {adv}（rank {rank}）｜DOI/arXiv：{s['doi'] or s['arxiv_id'] or '—'}\n")
    # 如果今天只多读一篇
    lines.append("## 如果今天只多读一篇\n")
    if one_weekly is not None:
        lines.append(f"- **{one_weekly['title']}**")
        lines.append(f"  - 理由：{one_weekly['reason'][:140]}")
    else:
        lines.append("- 今天没有值得多读的 WEEKLY（或已读完）。")
    lines.append("")
    # 重点研究组动态
    lines.append("## 重点研究组动态\n")
    gd = _group_dynamics(conn, today)
    if gd:
        for r, ents in gd:
            lines.append(f"- **{r['title'][:95]}**")
            lines.append(f"  - 相关研究组：{', '.join(ents)}｜{r['journal'] or r['doi'] or ''}")
    else:
        lines.append("今日无重点研究组新工作。")
    lines.append("")
    # 引用网络发现
    lines.append("## 引用网络发现\n")
    cf = _citation_finds(conn, today)
    if cf:
        for r in cf:
            lines.append(f"- [{r['title'][:95]}]({r['doi'] or r['work_id']})")
    else:
        lines.append("今日引用网络无 MUST/WEEKLY 新发现。")
    lines.append("")
    # RADAR 精选
    ib = _interesting_radar(conn, today, screenings)
    lines.append("## RADAR 精选（最多 3 篇）\n")
    if ib:
        for s in ib:
            lines.append(f"- {s['title']}（{s['matched_profile'] or '邻接'}）")
    else:
        lines.append("本轮没有值得扫一眼的 RADAR 条目。")
    lines.append("")
    lines.append("## Zotero 决策\n")
    lines.append(_zotero_queue_section(conn, today))
    lines.append("")
    lines.append("## Source Health\n")
    lines.append(_source_health_table(conn))
    lines.append("")
    return "\n".join(lines)


def run_daily_v3(conn, today):
    os.makedirs(DAILY_DIR, exist_ok=True)
    path = os.path.join(DAILY_DIR, f"{today}.md")
    content = render_daily_v3(conn, today)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    conn.execute("INSERT OR REPLACE INTO digests (date,path,summary_json) VALUES (?,?,?)",
                 (today, path, json.dumps({"bytes": len(content), "version": 3}, ensure_ascii=False)))
    conn.commit()
    return path


def render_daily_v4(conn, today):
    """用户向 daily：只显示用户需要知道/决定的内容，不显示后台工程状态。

    结构：今天必须知道（live MUST，0-3 篇）→ 已完成 A 等你决定 → 竞品提醒 → 本周候选。
    """
    from la_queues import weekly_candidates
    from la_workflow import a_radar_excerpt, inbox_items, workflow_item

    lines = [f"# 每日文献简报 {today}", ""]

    # ---- 1. 今天必须知道（live MUST，最多 3 篇） ----
    rows = conn.execute(
        """SELECT s.*, w.title, w.historical_discovery FROM screenings s
           JOIN works w ON w.work_id=s.work_id
           WHERE w.historical_discovery=0 AND date(s.screened_at)=? AND s.grade=?
             AND s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=s.work_id)
           ORDER BY s.id""", (today, MUST)).fetchall()
    lines.append("## 今天必须知道\n")
    if rows:
        for s in rows[:3]:
            wf = workflow_item(conn, s["work_id"])
            excerpt = a_radar_excerpt(wf["a_path"]) if wf and wf["a_status"] == "completed" else {}
            lines.append(f"- **{s['title']}**")
            if excerpt:
                lines.append(f"  - {excerpt.get('one_line', '')}")
                lines.append(f"  - 对你的价值：{excerpt.get('value', '')}")
            else:
                lines.append(f"  - {s['reason'][:140]}")
            lines.append("")
    else:
        lines.append("今天没有必须立即知道的新论文。\n")

    # ---- 2. 已完成 A，等你决定（前台在 Obsidian 每日总览） ----
    items = [i for i in inbox_items(conn) if i["work_id"]]
    from la_overview import _completed_a_works
    pending = [w for w in _completed_a_works(conn)
               if (w["reading_status"] or "") not in ("processing", "ignored", "callable", "deferred")]
    lines.append("## 已完成 A，等你决定\n")
    if pending:
        from la_overview import overview_path
        lines.append(f"请在 Obsidian 每日总览处理（{len(pending)} 篇等待决定）：`每日文献/{today} 文献总览.md`\n")
    else:
        lines.append("当前没有等待决定的已完成 A。\n")
    if items:
        lines.append("稍后处理（0_待处理）：")
        for i in items:
            flags = "｜".join(k.replace("save_zotero", "Zotero").replace("competitor", "竞品")
                              for k, v in i["flags"].items() if v)
            lines.append(f"- **{i['title']}**")
            if flags:
                lines.append(f"  - 已打标签：{flags}")
            lines.append(f"  - `decide {i['work_id']} read|later|ignore`（`flag {i['work_id']} save_zotero|competitor`）")
            lines.append("")
        lines.append("")

    # ---- 3. 竞品提醒（如有） ----
    comp = []
    for s in rows:
        from la_signals import competitor_level
        cl, hits = competitor_level(s["title"], "")
        if cl >= 2:
            comp.append((s["title"], cl, [h[1] for h in hits]))
    for i in items:
        if i["flags"].get("competitor"):
            comp.append((i["title"], 2, ["用户标记"]))
    lines.append("## 竞品提醒\n")
    if comp:
        seen = set()
        for title, cl, why in comp:
            if title in seen:
                continue
            seen.add(title)
            lines.append(f"- [L{cl}] {title}｜{', '.join(why)[:120]}")
    else:
        lines.append("今日无竞品级新论文。")
    lines.append("")

    # ---- 4. 本周候选（最多 5 篇） ----
    weekly = weekly_candidates(conn, today, days=7, limit=5)
    lines.append("## 本周候选\n")
    if weekly:
        for w in weekly:
            lines.append(f"- **{w['title']}**")
            if w.get("reason"):
                lines.append(f"  - {w['reason'][:120]}")
            lines.append("")
    else:
        lines.append("本周暂无新的候选论文。")
    lines.append("")
    return "\n".join(lines)


def run_daily_v4(conn, today):
    os.makedirs(DAILY_DIR, exist_ok=True)
    path = os.path.join(DAILY_DIR, f"{today}.md")
    content = render_daily_v4(conn, today)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    conn.execute("INSERT OR REPLACE INTO digests (date,path,summary_json) VALUES (?,?,?)",
                 (today, path, json.dumps({"bytes": len(content), "version": 4}, ensure_ascii=False)))
    conn.commit()
    return path


def render_weekly_v2(conn, today=None):
    today = today or datetime.now().strftime("%Y-%m-%d")
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    new_works = conn.execute("""SELECT * FROM works WHERE consumed_history=0 AND first_seen>=? AND historical_discovery=0""",
                             (since,)).fetchall()
    screenings = conn.execute("""SELECT s.*, w.title, w.authors, w.doi, w.arxiv_id, w.journal
        FROM screenings s JOIN works w ON w.work_id=s.work_id
        WHERE w.consumed_history=0 AND date(s.screened_at)>=?
          AND s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=s.work_id)
        ORDER BY s.id""", (since,)).fetchall()
    by_grade = Counter(s["grade"] for s in screenings)
    must_rows = [s for s in screenings if s["grade"] == MUST]
    weekly_rows = [s for s in screenings if s["grade"] == WEEKLY]
    radar_rows = [s for s in screenings if s["grade"] == RADAR]
    src_new = Counter()
    for nw in new_works:
        for r in conn.execute("SELECT DISTINCT source FROM work_sources WHERE work_id=?", (nw["work_id"],)).fetchall():
            src_new[r["source"]] += 1
    src_hits = Counter()
    for s in screenings:
        for r in conn.execute("SELECT DISTINCT source FROM work_sources WHERE work_id=?", (s["work_id"],)).fetchall():
            if s["grade"] in (MUST, WEEKLY):
                src_hits[r["source"]] += 1
    def _text_of(s):
        row = conn.execute("SELECT abstract FROM work_sources WHERE work_id=? LIMIT 1", (s["work_id"],)).fetchone()
        return ((s["title"] or "") + " " + (row["abstract"] if row else "")).lower()
    def rank(s):
        tags = s["matched_profile"] or ""
        score = 0 if s["grade"] == MUST else 5
        for i, t in enumerate(CORE_ORDER):
            if t in tags:
                score += i
        return score
    top = sorted(must_rows + weekly_rows, key=rank)[:5]
    lines = [f"# 本周文献复盘 {_iso_week()}", f"- 统计窗口：{since} ~ {today}\n",
             "## 概览\n",
             f"- 新 work 总数：{len(new_works)}",
             f"- MUST：{by_grade.get(MUST, 0)}｜WEEKLY：{by_grade.get(WEEKLY, 0)}｜"
             f"RADAR：{by_grade.get(RADAR, 0)}｜DROP：{by_grade.get(DROP, 0)}",
             "- 来源贡献："]
    for sid, n in sorted(src_new.items(), key=lambda x: -x[1]):
        lines.append(f"  - {sid}：{n}（MUST/WEEKLY 命中 {src_hits.get(sid, 0)}）")
    lines += ["", "## 本周真正影响当前研究的变化\n"]
    fqfe = [s for s in must_rows + weekly_rows if re.search(r"fractional|quantum ferroelectric|injection|Wannier|Neumann|mirror-time", _text_of(s))]
    p5 = [s for s in must_rows + weekly_rows if re.search(r"sliding ferroelectric|stacking ferroelectric|shift current|bilayer|BPVE|photovoltaic", _text_of(s))]
    if fqfe:
        lines.append("**fractional_ferroelectric（P1）：**")
        for s in fqfe[:4]:
            lines.append(f"- [{s['grade']}] {s['title'][:90]}")
    else:
        lines.append("- P1 无直接影响本周的变化。")
    if p5:
        lines.append("**project5（P2）：**")
        for s in p5[:4]:
            lines.append(f"- [{s['grade']}] {s['title'][:90]}")
    else:
        lines.append("- project5 无直接影响本周的变化。")
    lines += ["", "## 竞品变化\n"]
    comps = []
    from la_signals import competitor_level
    for s in must_rows + weekly_rows:
        cl, hits = competitor_level(s["title"], "")
        if cl >= 2:
            comps.append((s, cl))
    if comps:
        for s, cl in sorted(comps, key=lambda x: -x[1]):
            lines.append(f"- [L{cl}] [{s['grade']}] {s['title'][:90]}")
    else:
        lines.append("本周无直接竞品级别（>=2）新论文。")
    lines += ["", "## 方法学变化\n"]
    meth = [s for s in must_rows + weekly_rows if re.search(r"Wannier|tight-binding|screening|high-throughput|descriptor|machine learning|method|framework|Wilson loop", _text_of(s))]
    for s in meth[:5]:
        lines.append(f"- [{s['grade']}] {s['title'][:90]}")
    if not meth:
        lines.append("- 本周无方法学级 MUST/WEEKLY 变化。")
    lines += ["", "## 重点研究组\n"]
    gd = conn.execute("""SELECT DISTINCT w.work_id,w.title FROM works w
        JOIN recommendation_signals sg ON sg.work_id=w.work_id AND sg.signal_type='tracked_author'
        WHERE w.historical_discovery=0 AND w.first_seen>=? LIMIT 8""", (since,)).fetchall()
    for r in gd:
        ents = [json.loads(s["detail_json"] or "{}").get("entity", "?")
                for s in conn.execute("SELECT detail_json FROM recommendation_signals WHERE work_id=? AND signal_type='tracked_author'",
                                      (r["work_id"],)).fetchall()]
        lines.append(f"- {r['title'][:90]}（{'、'.join(sorted(set(ents)))}）")
    if not gd:
        lines.append("- 本周无重点研究组新工作。")
    lines += ["", "## 可能漏读\n"]
    lines.append("- 见 `missed_literature_report.md`（MUST/WEEKLY 且不在 Zotero/未读）。")
    lines += ["", "## source performance\n"]
    lines.append("见 `source_value.md`（按命中率排序）。")
    lines += ["", "## 下周阅读建议（最多 5 篇）\n"]
    for i, s in enumerate(top[:5], 1):
        lines.append(f"{i}. [{s['grade']}] {s['title']}")
    if not top:
        lines.append("本周无 MUST/WEEKLY 建议。")
    return "\n".join(lines)


def run_weekly_v2(conn, today=None):
    os.makedirs(WEEKLY_DIR, exist_ok=True)
    today = today or datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(WEEKLY_DIR, f"{_iso_week(datetime.strptime(today, '%Y-%m-%d'))}.md")
    content = render_weekly_v2(conn, today)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
