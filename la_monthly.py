#!/usr/bin/env python3
"""literature-agent V2 monthly：月度复盘（真实数据驱动）。"""
import json
import os
import time

from la_db2 import connect as db2connect

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import REPORT_ROOT


def run_monthly(conn=None, month=None):
    own = conn is None
    conn = conn or db2connect()
    try:
        from la_db import sync_reading_status
        sync_reading_status(conn)
    except Exception:
        pass
    month = month or time.strftime("%Y-%m")
    path = os.path.join(REPORT_ROOT, "monthly", month + ".md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mstart = month + "-01"
    mnext = f"{int(month[:4]) + (1 if month[5:] == '12' else 0)}-{'01' if month[5:] == '12' else str(int(month[5:]) + 1).zfill(2)}"
    w = conn.execute("SELECT COUNT(*) n FROM works WHERE first_seen>=? AND first_seen<?", (mstart, mnext)).fetchone()["n"]
    grades = {}
    for r in conn.execute("""SELECT s.grade g, COUNT(*) n FROM screenings s
        WHERE s.screened_at>=? AND s.screened_at<? GROUP BY s.grade""", (mstart, mnext)).fetchall():
        grades[r["g"]] = r["n"]
    fb = conn.execute("""SELECT action, COUNT(*) n FROM feedback WHERE created_at>=? AND created_at<? GROUP BY action""",
                      (mstart, mnext)).fetchall()
    src_hits = {}
    for r in conn.execute("""SELECT ws.source src, COUNT(DISTINCT ws.work_id) n FROM work_sources ws
        WHERE ws.date>=? AND ws.date<? GROUP BY ws.source""", (mstart, mnext)).fetchall():
        src_hits[r["src"]] = r["n"]
    backlog = conn.execute("""SELECT COUNT(*) n FROM works w WHERE w.reading_status='discovered' AND w.queued=1
        AND w.work_id NOT LIKE 'test-%'""").fetchone()["n"]
    from la_db2 import load_coverage_gold
    gold = len(load_coverage_gold())
    lines = [f"# 月度复盘 {month}", "", "## 数据", "",
             f"- 新 work：{w}｜分级：{grades or '（无）'}",
             f"- 阅读/保存/跳过反馈：{dict(fb) or '（无）'}",
             f"- 来源产出（work_sources.date 口径）：{src_hits}",
             f"- 未读 backlog（queued 未处理）：{backlog}",
             f"- coverage gold：{gold} 篇（累计）",
             f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M')}", "",
             "## 趋势（本月 vs 上月）", ""]
    last = f"{int(month[:4]) - (1 if month[5:] == '01' else 0)}-{'12' if month[5:] == '01' else str(int(month[5:]) - 1).zfill(2)}"
    lstart = last + "-01"
    wl = conn.execute("SELECT COUNT(*) n FROM works WHERE first_seen>=? AND first_seen<?", (lstart, mstart)).fetchone()["n"]
    lines.append(f"- 新 work：本月 {w} vs 上月 {wl}（{'↑' if w > wl else '↓' if w < wl else '='}）")
    lines += ["", "## 竞品演化", ""]
    lines.append("- 见 `competitor_watch.md`（按当周/当月 MUST/WEEKLY 竞品级别统计）。")
    lines += ["", "## 建议", ""]
    lines.append("- 若 backlog 持续增长：检查 screen 阈值或来源噪音。")
    lines.append("- 若某 source 连续两月 0 命中：考虑降频。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if own:
        conn.close()
    return path
