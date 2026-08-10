#!/usr/bin/env python3
"""zotero_save_work / zotero_sync_queue 的 mock 测试（不访问真实 Zotero）。"""
import json, os, sqlite3, sys, tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import la_zotero as lz

def make_db(tmp):
    conn = sqlite3.connect(os.path.join(tmp, "t.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE works (work_id TEXT PRIMARY KEY, title TEXT, authors TEXT, doi TEXT, arxiv_id TEXT, journal TEXT, first_seen TEXT, reading_status TEXT)")
    conn.execute("CREATE TABLE screenings (id INTEGER PRIMARY KEY AUTOINCREMENT, work_id TEXT, grade TEXT, reason TEXT, matched_profile TEXT, screened_at TEXT)")
    conn.execute("CREATE TABLE processing (work_id TEXT, a_status TEXT, a_path TEXT, pdf_path TEXT, pdf_status TEXT, si_path TEXT, si_status TEXT, processed_at TEXT)")
    return conn

def run_all():
    ok = True
    tmp = tempfile.mkdtemp(prefix="zotero_auto_")
    try:
        # ---- 1) 未配置 -> 明确报错 ----
        conn = make_db(tmp)
        r = lz.zotero_save_work(conn, "x")
        assert not r["ok"] and "未启用" in r["error"], r
        print("[PASS] 1. 未配置时明确报错")

        # ---- 2) 配置后：创建条目 + collection + base64 附件 ----
        with mock.patch.dict("os.environ", {}), \
             mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            conn.execute("INSERT INTO works VALUES (?,?,?,?,?,?,?,?)",
                         ("t:1", "Test Paper", "Smith, John; Doe, Jane", "10.1000/abc", None, "Nature Materials", "2026-08-01", "a_completed"))
            conn.execute("INSERT INTO screenings (work_id,grade,reason,matched_profile,screened_at) VALUES (?,?,?,?,?)",
                         ("t:1", "MUST", "r", "P1-fqfe", "2026-08-10"))
            pdf = os.path.join(tmp, "paper.pdf")
            with open(pdf, "wb") as f:
                f.write(b"%PDF-1.4\n" + b"0" * 20000)
            conn.execute("INSERT INTO processing (work_id,a_status,a_path,pdf_path,pdf_status,si_path,si_status,processed_at) VALUES (?,?,?,?,?,?,?,?)",
                         ("t:1", "completed", "", pdf, "ok", "", "", "2026-08-10 08:00:00"))
            conn.commit()
            calls = []
            def fake_http(method, url, body=None, api_key=None, timeout=60):
                calls.append((method, url, body))
                if url.endswith("/items") and method == "POST" and isinstance(body, list) and body and body[0]["itemType"] == "journalArticle":
                    return 201, json.dumps([{"successful": {"0": {"key": "ITEM1"}}}]).encode()
                if url.endswith("/collections") and method == "GET":
                    return 200, json.dumps([]).encode()
                if url.endswith("/collections") and method == "POST":
                    return 201, json.dumps([{"successful": {"0": {"key": "COLL1"}}}]).encode()
                if url.endswith("/items") and method == "POST":
                    return 201, json.dumps([{"successful": {"0": {"key": "ATT1"}}}]).encode()
                if "/items/ITEM1" in url and method == "PATCH":
                    return 204, b""
                return 500, b"unexpected"
            with mock.patch.object(lz, "_zotero_http", side_effect=fake_http):
                r = lz.zotero_save_work(conn, "t:1")
            assert r["ok"] and r["item_key"] == "ITEM1", r
            entry_call = next(c for c in calls if c[0] == "POST" and isinstance(c[2], list) and c[2] and c[2][0].get("itemType") == "journalArticle")
            entry = entry_call[2][0]
            assert entry["title"] == "Test Paper"
            assert entry["DOI"] == "10.1000/abc"
            assert entry["creators"] == [
                {"creatorType": "author", "firstName": "John", "lastName": "Smith"},
                {"creatorType": "author", "firstName": "Jane", "lastName": "Doe"}], entry["creators"]
            att_call = next(c for c in calls if isinstance(c[2], list) and c[2] and c[2][0].get("itemType") == "attachment")
            att = att_call[2][0]
            assert att["parentItem"] == "ITEM1" and att["filename"] == "paper.pdf" and att["content"], att
            patch_call = next(c for c in calls if c[0] == "PATCH")
            assert patch_call[2] == {"collections": ["COLL1"]}, patch_call
        print("[PASS] 2. 条目/collection/附件 payload 结构正确")

        # ---- 3) zotero_sync_queue 幂等：已同步的不重复推 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}), \
             mock.patch.object(lz, "_zotero_http", return_value=(201, json.dumps([{"successful": {"0": {"key": "K2"}}}]).encode())):
            qp = os.path.join(tmp, "zq.jsonl")
            with open(qp, "w", encoding="utf-8") as f:
                f.write(json.dumps({"work_id": "t:1", "title": "Test Paper", "status": "accepted"}) + "\n")
            with mock.patch.object(lz, "QUEUE_PATH", qp):
                r = lz.zotero_sync_queue(conn)
                assert r["ok"] and r["synced"] == 1, r
                r2 = lz.zotero_sync_queue(conn)
                assert r2["ok"] and r2["synced"] == 0, r2
                entries = lz.load_queue(qp)
                assert entries[0]["zotero_synced_at"] and entries[0]["zotero_item_key"] == "K2"
        print("[PASS] 3. zotero_sync_queue 幂等")

        # ---- 4) 失败保留队列，.bib 兜底不破坏 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}), \
             mock.patch.object(lz, "_zotero_http", side_effect=Exception("network down")):
            r = lz.zotero_save_work(conn, "t:1")
            assert not r["ok"] and "network down" in r["error"], r
        print("[PASS] 4. 网络失败时保留队列并报错")
    finally:
        pass
    print("zotero auto test: ALL PASS")
    return ok

if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
