#!/usr/bin/env python3
"""literature-agent v1 backfill：补抓缺失日期。

按 source 能力：
  arXiv —— 按 submittedDate 窗口回溯（支持 N 天）
  APS RSS —— feed 上限 ~100 条；超过 feed 覆盖范围的日期 coverage 不完整，如实报告
  Nature/Science —— feed 条目数量少（~8/86），仅能覆盖最近条目，超窗如实报告
禁止重复推荐已有 work（走 ingest 去重）。
"""
import json
import os
from datetime import datetime, timedelta

from la_db import record_source_run, record_error
from la_registry import enabled_sources

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import REPORT_AUDIT_ROOT
REPORT_PATH = os.path.join(REPORT_AUDIT_ROOT, "backfill_report.md")


def run_backfill(conn, days=7, log=None, run_id=None):
    import literature_agent as la
    if log is None:
        log = lambda *a: None
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%d")
    summary = {"days": int(days), "cutoff": cutoff, "sources": {}, "new": 0, "updated": 0, "requeued": 0, "noop": 0}
    src = la.get_sources()

    for source, cfg in enabled_sources():
        entry = {"items": 0, "in_window": 0, "new": 0, "coverage": "full", "note": ""}
        try:
            if source == "arxiv":
                records, per = la.collect_arxiv(conn, today, log, days=days)
                window_recs = records
                entry["note"] = "arXiv 按 submittedDate 回溯 %d 天" % int(days)
            else:
                records, per, _ = la.collect_rss_source(conn, source, today, log)
                window_recs = [r for r in records if (r.get("date") or "") >= cutoff]
                # 评估覆盖：feed 最早日期
                dates = sorted(r.get("date", "") for r in records if r.get("date"))
                if dates and dates[0] > cutoff:
                    entry["coverage"] = "incomplete"
                    entry["note"] = ("feed 仅覆盖 %s 起（%d 条），%s 之前日期无法回溯"
                                     % (dates[0], len(records), cutoff))
                elif not dates:
                    entry["coverage"] = "unknown"
                    entry["note"] = "feed 无日期字段，无法验证窗口覆盖"
            for rec in window_recs:
                wid, action, changed = la.ingest_record(conn, rec, source, today, history=False)
                entry["in_window"] += 1
                if action == "new":
                    entry["new"] += 1
                    summary["new"] += 1
                elif action == "requeued":
                    summary["requeued"] += 1
                elif action == "updated":
                    summary["updated"] += 1
                else:
                    summary["noop"] += 1
            entry["items"] = len(records)
        except Exception as e:
            entry["note"] = "FAILED: " + str(e)[:200]
            record_error(conn, source, None, "backfill", str(e)[:500], retryable=True)
        summary["sources"][source] = entry

    conn.commit()
    _write_report(summary)
    return summary


def _write_report(summary):
    lines = ["# Backfill 报告\n",
             "- 时间：" + datetime.now().strftime("%Y-%m-%d %H:%M"),
             "- 窗口：过去 %d 天（≥ %s）\n" % (summary["days"], summary["cutoff"]),
             "- 新 work：%d｜更新：%d｜重入队：%d｜已存在(noop)：%d\n" % (
                 summary["new"], summary["updated"], summary["requeued"], summary["noop"]),
             "## 各来源覆盖\n"]
    lines.append(f"{'Source':<20}{'Items':>7}{'InWin':>7}{'New':>5}  Coverage / Note")
    lines.append("-" * 80)
    for sid, e in sorted(summary["sources"].items()):
        lines.append(f"{sid:<20}{e['items']:>7}{e['in_window']:>7}{e['new']:>5}  {e['coverage']}  {e['note'][:70]}")
    lines.append("")
    lines.append("## 局限说明")
    lines.append("- APS RSS 有 ~100 条上限：超过 feed 覆盖范围的日期**无法完整回溯**，不假装完整。")
    lines.append("- Nature/Science feed 条目少（Nature 系列 ~8 条、Sci Adv ~86 条）：只覆盖最近上线条目。")
    lines.append("- 需要更早补抓时，建议：arXiv 直接加大 --days；期刊走 Crossref/官方存档（后续阶段）。")
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return REPORT_PATH
