#!/usr/bin/env python3
"""literature-agent v1 source health / doctor。

状态：
  OK       —— 最近一次成功且无连续失败
  BLOCKED  —— 注册表 disabled（blocked_by_cloudflare 等，人工判定）
  FAILING  —— 连续失败 >=2 或最近一次失败
  STALE    —— 超过 3 天没有运行记录
"""
import os
from datetime import datetime, timedelta

from la_registry import source_meta

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import REPORT_ROOT, REPORT_AUDIT_ROOT
DAILY_DIR = os.path.join(REPORT_ROOT, "daily")


def _iso_days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")


def source_health(conn):
    """返回 {source_id: {...}}，结合注册表与 source_runs。"""
    meta = source_meta()
    rows = conn.execute(
        """SELECT source, feed, ok, items, latency_ms, error, fetched_at, new_works
           FROM source_runs ORDER BY id DESC""").fetchall()
    per = {}
    for r in rows:
        d = per.setdefault(r["source"], [])
        d.append({"feed": r["feed"], "ok": bool(r["ok"]), "items": r["items"] or 0,
                  "latency_ms": r["latency_ms"] or 0, "error": r["error"] or "",
                  "fetched_at": r["fetched_at"] or "", "new_works": r["new_works"] or 0})
    out = {}
    cutoff = _iso_days_ago(3)
    for sid, m in meta.items():
        runs = per.get(sid, [])
        ok_runs = [r for r in runs if r["ok"]]
        fail_runs = [r for r in runs if not r["ok"]]
        consec = 0
        for r in runs:  # 最新在列表头
            if not r["ok"]:
                consec += 1
            else:
                break
        last_ok = ok_runs[0]["fetched_at"] if ok_runs else None
        last_fail = fail_runs[0]["fetched_at"] if fail_runs else None
        last_latency = sum(r["latency_ms"] for r in runs[:5]) / max(1, len(runs[:5]))
        last_items = runs[0]["items"] if runs else 0
        last_err = fail_runs[0]["error"][:120] if fail_runs else ""
        if not m["enabled"]:
            status = "BLOCKED"
            detail = m.get("blocked") or m.get("blocked_detail") or "disabled"
        elif not runs:
            status = "STALE"
            detail = "no run records"
        elif consec >= 2:
            status = "FAILING"
            detail = f"{consec} consecutive failures" + (": " + last_err if last_err else "")
        elif not last_ok or last_ok < cutoff:
            status = "STALE"
            detail = "last success older than 3 days"
        else:
            status = "OK"
            detail = ""
        out[sid] = {"status": status, "detail": detail, "last_ok": last_ok, "last_fail": last_fail,
                    "last_items": last_items, "last_latency_ms": int(last_latency),
                    "last_error": last_err, "consecutive_failures": consec,
                    "enabled": m["enabled"], "role": m["role"], "priority": m["priority"],
                    "journal": m["journal"], "feeds": m["feeds"], "blocked": m["blocked"]}
    return out


def doctor(conn, log=None):
    """输出 doctor 报告（表格 + 数据库统计）。返回 dict。"""
    if log is None:
        log = lambda *a: None
    health = source_health(conn)
    lines = ["# literature-agent doctor\n", "- 时间：" + datetime.now().strftime("%Y-%m-%d %H:%M") + "\n"]
    lines.append("```")
    lines.append(f"{'Source':<22}{'Status':<10}{'Items':>6}{'Lat(ms)':>8}  Detail")
    lines.append("-" * 78)
    order = sorted(health, key=lambda k: (health[k]["status"] != "OK", health[k]["priority"]))
    for sid in order:
        h = health[sid]
        lines.append(f"{sid:<22}{h['status']:<10}{h['last_items']:>6}{h['last_latency_ms']:>8}  {h['detail'][:50]}")
    lines.append("```\n")
    # 数据库统计
    from la_db import db_stats
    st = db_stats(conn)
    queued = conn.execute("SELECT COUNT(*) FROM works WHERE queued=1").fetchone()[0]
    lines.append("## 数据库\n")
    lines.append(f"- works: {st['works']}（queued: {queued}）")
    lines.append(f"- screenings: {st['screenings']}（{st['grades']}）")
    lines.append(f"- work_sources: {st['work_sources']} / work_versions: {st['work_versions']}")
    lines.append(f"- source_runs: {st['source_runs']} / errors: {st['errors']}")
    lines.append(f"- dedup_review pending: {st['dedup_review_pending']} / artifacts: {st['artifacts']}")
    lines.append(f"- 最近错误：")
    errs = conn.execute("SELECT source, work_id, stage, message, created_at FROM errors "
                        "ORDER BY id DESC LIMIT 8").fetchall()
    if errs:
        for e in errs:
            lines.append(f"  - [{e['created_at'][:16]}] {e['source'] or '?'}::{e['stage']} {e['message'][:90]}")
    else:
        lines.append("  - 无")
    blocked = [sid for sid, h in health.items() if h["status"] == "BLOCKED"]
    lines.append("\n## blocked\n")
    for sid in blocked:
        h = health[sid]
        lines.append(f"- {sid}: {h.get('blocked') or 'disabled'} — {h['detail'][:120]}")
    report = "\n".join(lines)
    os.makedirs(BASE, exist_ok=True)
    os.makedirs(REPORT_AUDIT_ROOT, exist_ok=True)
    with open(os.path.join(REPORT_AUDIT_ROOT, "doctor_report.md"), "w", encoding="utf-8") as f:
        f.write(report)
    return health, report
