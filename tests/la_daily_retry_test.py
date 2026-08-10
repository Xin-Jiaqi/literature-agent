#!/usr/bin/env python3
"""partial/ok daily 重试语义测试（临时 DB，不跑真实 daily）。

规则（2026-08-10 确认）：
- status='ok'    = 当天真正完整成功，后续时间点可 skip
- status='partial' = 不算成功，后续 08/09/10 必须允许重跑
- status='failed'  = 必须允许后续重跑
- catchup 窗口按最后一次 status='ok' 计算
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import literature_agent as la


_db_n = [0]
def _make_db(tmp):
    _db_n[0] += 1
    conn = sqlite3.connect(os.path.join(tmp, f"t{_db_n[0]}.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE processing_runs (
        run_id TEXT PRIMARY KEY, command TEXT, started_at TEXT,
        finished_at TEXT, status TEXT, summary_json TEXT)""")
    return conn


def _add_run(conn, finished_at, status):
    conn.execute(
        "INSERT INTO processing_runs (run_id,command,started_at,finished_at,status,summary_json) "
        "VALUES (?,?,?,?,?,?)",
        (f"r-{finished_at}-{status}", "daily", finished_at, finished_at, status, "{}"))
    conn.commit()


def run_all():
    tmp = tempfile.mkdtemp(prefix="daily_retry_")
    ok = True
    try:
        # A. 07:00 partial -> 08:00 不 skip
        conn = _make_db(tmp)
        _add_run(conn, "2026-08-10 07:00:00", "partial")
        assert la._daily_ok_today(conn, "2026-08-10") is False, "partial 不能算当天成功"
        print("[PASS] A. 07:00 partial -> 08:00 不 skip")

        # B. 07:00 failed -> 08:00 不 skip
        conn = _make_db(tmp)
        _add_run(conn, "2026-08-10 07:00:00", "failed")
        assert la._daily_ok_today(conn, "2026-08-10") is False, "failed 不能算当天成功"
        print("[PASS] B. 07:00 failed -> 08:00 不 skip")

        # C. 07:00 ok -> 08:00 skip
        conn = _make_db(tmp)
        _add_run(conn, "2026-08-10 07:00:00", "ok")
        assert la._daily_ok_today(conn, "2026-08-10") is True, "ok 才算当天成功"
        assert la._daily_ok_today(conn, "2026-08-11") is False, "只对当天有效"
        print("[PASS] C. 07:00 ok -> 08:00 skip")

        # D. 昨天 partial、前天 ok -> catchup 依据前天最后一次真正 ok
        conn = _make_db(tmp)
        _add_run(conn, "2026-08-08 09:00:00", "ok")
        _add_run(conn, "2026-08-09 07:30:00", "partial")
        _add_run(conn, "2026-08-09 08:30:00", "partial")
        last = la._last_ok_daily_date(conn)
        assert last is not None and last.isoformat() == "2026-08-08", last
        # 只有 partial 的历史：视为从未完整成功（gap 从最早的 ok 计算，没有则全量窗口）
        conn2 = _make_db(tmp)
        _add_run(conn2, "2026-08-09 07:00:00", "partial")
        assert la._last_ok_daily_date(conn2) is None, "只有 partial 时不得当作最近成功"
        print("[PASS] D. catchup 依据最后一次真正 ok（partial 不算）")
    finally:
        pass
    print("daily retry test: ALL PASS")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
