#!/usr/bin/env python3
"""literature-agent V2 重点作者/研究组雷达：
- tracked_entities.json（实体定义）
- OpenAlex 作者消歧（结合 affiliation/coauthors/known papers）→ author_resolution_report.md
- author-watch：每天拉取 tracked 作者新工作，signal=tracked_author（只作发现信号，不直接定级）
"""
import json
import os
import re
import time

from la_db2 import add_tracked_entity, record_signal, tracked_entities
from la_discovery import oa_author_works, oa_work_to_rec, resolve_author
from la_v2common import read_json, write_json

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import DATA_ROOT, REPORT_AUDIT_ROOT
ENTITIES_PATH = os.path.join(DATA_ROOT, "tracked_entities.json")
REPORT_PATH = os.path.join(REPORT_AUDIT_ROOT, "author_resolution_report.md")

_GENERIC_AFFILIATION_WORDS = {
    "university", "institute", "academy", "college", "school", "department",
    "laboratory", "national", "science", "technology", "china", "chinese", "state",
}


def _affiliation_matches(authorship, affiliation_hint):
    """medium 置信度作者必须在论文 authorship 中再次通过机构提示。"""
    hint = re.sub(r"[^a-z0-9 ]+", " ", (affiliation_hint or "").lower())
    hint = re.sub(r"\s+", " ", hint).strip()
    if not hint:
        return False
    names = [
        i.get("display_name", "") for i in (authorship or {}).get("institutions", [])
        if i.get("display_name")
    ]
    names += (authorship or {}).get("raw_affiliation_strings", []) or []
    for name in names:
        inst = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
        inst = re.sub(r"\s+", " ", inst).strip()
        if len(hint) >= 8 and (hint in inst or inst in hint):
            return True
        distinctive = {
            t for t in hint.split()
            if len(t) >= 5 and t not in _GENERIC_AFFILIATION_WORDS
        }
        if distinctive and distinctive.intersection(inst.split()):
            return True
    return False

ENTITY_DEFS = [
    # priority 1
    dict(id="yaguang_guo", canonical_name="Yaguang Guo", aliases=["郭亚光", "Y. Guo"], affiliation="Beijing Jiaotong University",
         coauthors=["Qian Wang", "Jian Zhou", "Jiaqi Xin", "Huaiqiu Zhu"], priority=1,
         topics=["2D pentagonal materials", "SHG", "shift current", "piezoelectric", "BPVE screening"],
         reason="用户导师；2D 五边形材料/BPVE/SHG 直接工作线来源"),
    dict(id="xiaolong_zou", canonical_name="Xiaolong Zou", aliases=["邹小龙", "X. Zou"], affiliation="Tsinghua University",
         coauthors=["Wenhui Duan", "Yong Xu", "Shuhua Guan"], priority=1,
         topics=["2D materials", "electronic ferroelectricity", "stacking", "skyrmion"],
         reason="高活跃作者；电子铁电/堆叠与 P3 交汇"),
    dict(id="hongjun_xiang", canonical_name="Hongjun Xiang", aliases=["向红军", "H. Xiang"], affiliation="Fudan University",
         coauthors=["Wei Luo", "Shihan Deng", "Guoliang Yu", "Junyi Ji"], priority=1,
         topics=["ferroelectricity", "unified ferroelectricity", "magnetic space groups", "PASP"],
         reason="统一铁电定义（Luo 2026）锚点组；FQFE 项目直接参照"),
    dict(id="lixin_he", canonical_name="Lixin He", aliases=["何力新", "L. He"], affiliation="University of Science and Technology of China",
         coauthors=["Hongsheng Pang", "Gan Jin"], priority=1,
         topics=["ferroelectric theory", "shift current", "generalized Neumann principle", "Wannier"],
         reason="广义 Neumann 原理（Pang-He PRL 2025）FQFE 三大理论基础之一"),
    dict(id="menghao_wu", canonical_name="Menghao Wu", aliases=["吴梦昊", "M. Wu"], affiliation="Wuhan University of Technology",
         coauthors=["Lei Li", "Ju Li", "Yutong Yan"], priority=1,
         topics=["sliding ferroelectricity", "2D vertical ferroelectricity", "WTe2"],
         reason="滑移铁电理论最高频作者；project5 直接参照/对手"),
    dict(id="andrew_rappe", canonical_name="Andrew M. Rappe", aliases=["Andrew Rappe", "A. Rappe"], affiliation="University of Pennsylvania",
         coauthors=["Steve M. Young", "Aaron M. Schankler", "Andreas Ghosh", "Yutian Bao"], priority=1,
         topics=["shift current formalism", "ferroelectric BPVE", "TB/Wannier methods"],
         reason="BPVE 理论方法核心源头（P4）"),
    dict(id="xiaofeng_qian", canonical_name="Xiaofeng Qian", aliases=["X. Qian"], affiliation="Texas A&M University",
         coauthors=["Hua Wang"], priority=1,
         topics=["SHG", "nonlinear photocurrent switching", "ferroicity-driven photocurrent"],
         reason="态间光电流张量切换（Sci Adv 2019）与 FQFE 主线高度重合"),
    dict(id="kristian_thygesen", canonical_name="Kristian S. Thygesen", aliases=["Kristian Thygesen", "K. S. Thygesen"], affiliation="Technical University of Denmark",
         coauthors=["Mads Kruse", "Søren Pakdel", "Thomas Olsen", "Morten Gjerding"], priority=1,
         topics=["C2DB", "BIDB", "2D ferroelectric high-throughput screening"],
         reason="BIDB 是 project5 逐条对比的最接近工作"),
    dict(id="jian_zhou", canonical_name="Jian Zhou", aliases=["周健", "J. Zhou"], affiliation="Soochow University",
         coauthors=["Yaguang Guo", "Hua Wang", "Ping Guo"], priority=1,
         topics=["2D ferroelectric shift current", "magnetic sliding ferroelectric BPVE"],
         reason="与郭亚光/用户直接合作；滑移铁电×BPVE 与 project5 同向"),
    # FQFE 直接竞争作者
    dict(id="wei_luo", canonical_name="Wei Luo", aliases=["罗威"], affiliation="Fudan University",
         coauthors=["Hongjun Xiang", "Shihan Deng"], priority=1,
         topics=["unified ferroelectricity", "quantum ferroelectricity"],
         reason="Luo 2026 统一铁电定义第一作者（FQFE 项目锚点/竞争者）"),
    dict(id="guoliang_yu", canonical_name="Guoliang Yu", aliases=["余国亮"], affiliation="Fudan University",
         coauthors=["Hongjun Xiang", "Junyi Ji"], priority=1,
         topics=["fractional quantum ferroelectrics", "stacking ferroelectricity"],
         reason="Symmetry Strategy FQFE PRL 2025 第一作者（直接竞争）"),
    dict(id="junyi_ji", canonical_name="Junyi Ji", aliases=["纪俊屹"], affiliation="Xiamen University",
         coauthors=["Hongjun Xiang", "Guoliang Yu"], priority=1,
         topics=["bilayer stacking ferroelectricity"],
         reason="General Theory for Bilayer Stacking Ferroelectricity PRL 2023 第一作者"),
    dict(id="hongsheng_pang", canonical_name="Hongsheng Pang", aliases=["庞洪升"], affiliation="University of Science and Technology of China",
         coauthors=["Lixin He"], priority=1,
         topics=["generalized Neumann principle", "fractional quantum ferroelectricity"],
         reason="广义 Neumann PRL 2025 第一作者"),
    dict(id="shihan_deng", canonical_name="Shihan Deng", aliases=["邓诗涵"], affiliation="Fudan University",
         coauthors=["Hongjun Xiang", "Wei Luo"], priority=2,
         topics=["unified ferroelectricity"],
         reason="统一铁电定义共同作者"),
    # priority 2
    dict(id="qian_wang", canonical_name="Qian Wang", aliases=["王前", "Q. Wang"], affiliation="Peking University",
         coauthors=["Yaguang Guo", "Huasong Liu"], priority=2,
         topics=["2D materials design", "pentagonal materials", "SHG", "structural prediction"],
         reason="用户 PRM 2023 合作者；郭亚光博士合作方"),
    dict(id="qiang_sun", canonical_name="Qiang Sun", aliases=["孙强"], affiliation="Peking University",
         coauthors=["Qian Wang"], priority=2,
         topics=["2D materials design"],
         reason="PKU 材料设计圈"),
    dict(id="ying_dai", canonical_name="Ying Dai", aliases=["戴瑛"], affiliation="Shandong University",
         coauthors=["Yandong Ma", "Yangyang Feng", "Baibiao Huang"], priority=2,
         topics=["multiferroics", "altermagnetism", "ferroelectrovalley"],
         reason="多铁/交错磁；FQFE×altermagnetism 相关"),
    dict(id="yandong_ma", canonical_name="Yandong Ma", aliases=["马衍东"], affiliation="Shandong University",
         coauthors=["Ying Dai", "Qian Wu"], priority=2,
         topics=["multiferroics", "2D magnetic semiconductors"],
         reason="同上；能谷×磁性"),
    dict(id="yong_xu", canonical_name="Yong Xu", aliases=["徐勇"], affiliation="Tsinghua University",
         coauthors=["Wenhui Duan", "Nianlong Zou", "Xiaolong Zou"], priority=2,
         topics=["deep band crossing NLO", "2D topology", "Wannier NLO"],
         reason="深带交叉 NLO（PRL 2025）与 P4 直接相关"),
    dict(id="wenhui_duan", canonical_name="Wenhui Duan", aliases=["段文晖"], affiliation="Tsinghua University",
         coauthors=["Yong Xu", "Xiaolong Zou"], priority=2,
         topics=["2D topology", "Wannier NLO"],
         reason="Wannier 插值 NLO（PRB 2017）方法谱系"),
    dict(id="yugui_yao", canonical_name="Yugui Yao", aliases=["姚裕贵"], affiliation="Beijing Institute of Technology",
         coauthors=["Zhi-Ming Yu", "Gui-Bin Liu", "Weikang Wu"], priority=2,
         topics=["magnetic layer groups", "ferroelectric metal", "topology"],
         reason="拓扑×铁电×shift current（PRL 2024）P3/P4 交汇"),
    dict(id="barry_bradlyn", canonical_name="Barry Bradlyn", aliases=["B. Bradlyn"], affiliation="University of Illinois Urbana-Champaign",
         coauthors=[], priority=2,
         topics=["band theory", "symmetry", "topological quantum chemistry"],
         reason="对称性/能带理论方法学；FQFE 表述相关邻接"),
    dict(id="hua_wang", canonical_name="Hua Wang", aliases=["王华"], affiliation="Texas A&M University",
         coauthors=["Xiaofeng Qian", "Jian Zhou"], priority=2,
         topics=["nonlinear photocurrent", "SHG", "ferroic switching"],
         reason="非线性光电流切换共同作者"),
    dict(id="zhi_ming_yu", canonical_name="Zhi-Ming Yu", aliases=["于志明"], affiliation="Beijing Institute of Technology",
         coauthors=["Yugui Yao"], priority=2,
         topics=["magnetic space groups", "emergent particles"],
         reason="磁群分类；拓扑铁电金属 shift current 共同作者"),
]


def load_defs():
    f = read_json(ENTITIES_PATH)
    if f and f.get("entities"):
        return f["entities"]
    return ENTITY_DEFS


def resolve_all(force=False, conn=None):
    """消歧全部实体：OpenAlex + affiliation + coauthors → DB + tracked_entities.json + 报告。"""
    if conn is None:
        from la_db2 import connect
        conn = connect()
    defs = load_defs()
    resolved = []
    lines = ["# Author Resolution Report", "",
             "> 消歧依据：OpenAlex display name + affiliation 文本匹配 + 合作者共现 + 已知论文。"]
    lines += ["", "| 实体 | 候选显示名 | OpenAlex ID | 置信度 | 证据 |", "|---|---|---|---|---|"]
    for d in defs:
        known = d.get("openalex_author_id") or ""
        if known:
            r = {"id": d["id"], "canonical_name": d["canonical_name"], "aliases": d.get("aliases", []),
                  "openalex_author_id": known, "orcid": d.get("orcid", ""), "affiliation": d.get("affiliation", ""),
                  "priority": d.get("priority", 2), "topics": d.get("topics", []), "reason": d.get("reason", ""),
                  "confidence": d.get("confidence", "high"),
                  "resolution_evidence": d.get("resolution_evidence") or {"source": "stored_id", "display_name": d["canonical_name"]}}
        else:
            r = resolve_author(d["canonical_name"], aliases=d.get("aliases", []),
                               affiliation=d.get("affiliation", ""), coauthors=d.get("coauthors", []),
                               priority=d.get("priority", 2), reason=d.get("reason", ""))
            r["topics"] = d.get("topics", [])
        add_tracked_entity(conn, r)
        resolved.append(r)
        ev = r["resolution_evidence"]
        reasons = ",".join(ev.get("reasons", [])) or ev.get("source", "")
        lines.append(f"| {d['canonical_name']} | {ev.get('display_name','?')} | {r['openalex_author_id'] or '-'} | "
                     f"{r['confidence']} | {reasons[:60]} |")
    lines += ["", "## 低置信度实体（不自动作为强推荐信号）", ""]
    for r in resolved:
        if r["confidence"] == "low":
            lines.append(f"- {r['canonical_name']}（{r['affiliation'] or '? affiliation'}）")
    write_json(ENTITIES_PATH, {"updated": time.strftime("%Y-%m-%d"), "entities": resolved})
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    conn.close()
    return resolved


def author_watch(conn=None, days=7, log=None, limit_per_author=50, index=None):
    """每天检查 tracked 作者新工作（high/medium 置信度）。signal=tracked_author。"""
    from datetime import date, timedelta
    log = log or (lambda *a: None)
    own = conn is None
    if own:
        from la_db2 import connect
        conn = connect()
    ents = tracked_entities(conn)
    from_date = (date.today() - timedelta(days=int(days))).isoformat()
    summary = {"checked": 0, "new": 0, "updated": 0, "noop": 0, "failed": [], "per_author": {}}
    import literature_agent as la
    for e in ents:
        oaid = e["openalex_author_id"]
        if not oaid or e["confidence"] not in ("high", "medium"):
            continue
        summary["checked"] += 1
        try:
            ws = oa_author_works(oaid, from_date, per_page=limit_per_author)
        except Exception as ex:
            summary["failed"].append(e["canonical_name"])
            log("error", f"author-watch {e['canonical_name']} failed: {ex}")
            continue
        ent_metrics = {"works": len(ws), "new": 0, "existing": 0,
                       "position_filtered": 0, "affiliation_filtered": 0}
        for w in ws:
            rec = oa_work_to_rec(w, journal_hint=None)
            if not rec["title"]:
                continue
            try:
                wid, action, _ = la.ingest_record(
                    conn, rec, "openalex_author_watch", date.today().isoformat(), index=index
                )
            except Exception as ex:
                log("error", f"ingest {rec['title'][:40]} failed: {ex}")
                continue
            if action == "new":
                ent_metrics["new"] += 1
                summary["new"] += 1
            elif action == "noop":
                ent_metrics["existing"] += 1
            else:
                summary["updated"] += 1
            # 精度守卫：同名作者只在 first 3 / last 位置才产生强信号（降低重名误报）
            authorships = w.get("authorships", [])
            pos = None
            matched_authorship = None
            for i, a in enumerate(authorships):
                if a.get("author", {}).get("id", "").endswith("/" + (e["openalex_author_id"] or "ZZZ")):
                    matched_authorship = a
                    pos = a.get("author_position") or ("first" if i == 0 else "last" if i == len(authorships) - 1 else "middle")
                    break
            if pos in ("middle", None) or (pos == "middle"):
                pass
            ok_pos = pos in ("first", "last") or (pos is None and e["confidence"] == "high" and len(authorships) <= 3)
            if not ok_pos:
                ent_metrics["position_filtered"] += 1
                continue
            if e["confidence"] == "medium" and not _affiliation_matches(
                    matched_authorship, e["affiliation"]):
                ent_metrics["affiliation_filtered"] += 1
                continue
            record_signal(conn, wid, "tracked_author", "openalex_author_watch",
                          {"entity": e["canonical_name"], "entity_id": e["id"],
                           "priority": e["priority"], "confidence": e["confidence"], "position": pos,
                           "why": f"{e['canonical_name']} 组新工作（P{e['priority']}，{pos} 作者）"})
        summary["per_author"][e["canonical_name"]] = ent_metrics
        log("info", f"author-watch {e['canonical_name']}: {ent_metrics}")
    if own:
        conn.close()
    return summary
