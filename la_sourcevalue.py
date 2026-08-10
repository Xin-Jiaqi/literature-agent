#!/usr/bin/env python3
"""literature-agent V2 source-value：来源质量比较（不止条数）。

每 source：candidate / MUST / WEEKLY / actionable hit rate / earliest detection / duplicate rate。
"""
import json
import os
import time

from la_db2 import connect as db2connect

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import REPORT_AUDIT_ROOT
PATH = os.path.join(REPORT_AUDIT_ROOT, "source_value.md")


def run_source_value(conn=None):
    own = conn is None
    conn = conn or db2connect()
    src_rows = conn.execute("""SELECT source, COUNT(*) n FROM work_sources GROUP BY source""").fetchall()
    src_map = {r["source"]: {"candidates": r["n"], "new_works": 0,
                             "must": 0, "weekly": 0, "actionable_ids": set(),
                             "first_detection": {}, "dup": 0} for r in src_rows}
    # 每个 source 的首次发现（earliest detection）
    for r in conn.execute("""SELECT ws.source, ws.work_id, w.first_seen FROM work_sources ws
        JOIN works w ON w.work_id=ws.work_id WHERE w.work_id NOT LIKE 'test-%' AND w.work_id NOT LIKE 'sim-%'""").fetchall():
        m = src_map.get(r["source"])
        if not m:
            continue
        fs = r["first_seen"] or ""
        if r["work_id"] not in m["first_detection"] or fs < m["first_detection"][r["work_id"]]:
            m["first_detection"][r["work_id"]] = fs
    # 每篇 work 的推荐优先级（取最新）
    grade = {}
    for r in conn.execute("""SELECT work_id, grade FROM screenings s
        WHERE s.id=(SELECT MAX(id) FROM screenings WHERE work_id=s.work_id)""").fetchall():
        grade[r["work_id"]] = r["grade"]
    for src, m in src_map.items():
        for wid, fs in m["first_detection"].items():
            g = grade.get(wid, "")
            if g == "MUST":
                m["must"] += 1
                m["actionable_ids"].add(wid)
            elif g == "WEEKLY":
                m["weekly"] += 1
                m["actionable_ids"].add(wid)
    # duplicate rate：同一 work 在 source 内的重复（work_sources 多版本）
    for r in conn.execute("""SELECT source, work_id, COUNT(*) n FROM work_sources GROUP BY source, work_id HAVING n>1""").fetchall():
        m = src_map.get(r["source"])
        if m:
            m["dup"] += r["n"] - 1
    lines = ["# Source Value Analysis", "",
             "> 价值 = 命中 MUST/WEEKLY 的能力，不是条数。", "",
             "| source | candidates | MUST | WEEKLY | actionable | hit rate | 首个 actionable | dup |",
             "|---|---|---|---|---|---|---|---|"]
    rows = []
    for src, m in src_map.items():
        if not m["candidates"]:
            continue
        n = m["candidates"]
        actionable = m["must"] + m["weekly"]
        rate = actionable / n if n else 0
        first = min((m["first_detection"].get(wid, "") for wid in m["actionable_ids"]), default="")
        rows.append((rate, src, n, m["must"], m["weekly"], actionable, rate, first, m["dup"]))
    rows.sort(key=lambda x: -x[0])
    for rate, src, n, must, weekly, actionable, r, first, dup in rows:
        lines.append(f"| {src} | {n} | {must} | {weekly} | {actionable} | {r:.3f} | {first} | {dup} |")
    lines += ["", "## 结论", ""]
    lines.append("- hit rate 最高的源 = 长期值得保留的源；0 命中源需观察，不急于关闭。")
    lines.append("- blocked 期刊由 crossref_fallback 代偿；其 hit rate 反映代偿质量。")
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if own:
        conn.close()
    return PATH
