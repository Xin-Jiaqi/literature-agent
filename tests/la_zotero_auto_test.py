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


def upload_flow():
    """模拟 v3 三步上传：条目/collection/附件创建 + 授权 + S3 + 注册。"""
    calls = []
    def handler(method, url, body=None, api_key=None, timeout=60,
                content_type="application/json", headers=None):
        calls.append((method, url, body, headers))
        if url.endswith("/items") and method == "POST":
            if isinstance(body, list) and body and body[0]["itemType"] == "journalArticle":
                return 201, json.dumps({"successful": {"0": {"key": "ITEM1", "version": 1}}}).encode()
            if isinstance(body, list) and body and body[0]["itemType"] == "attachment":
                return 201, json.dumps({"successful": {"0": {"key": "ATT1", "version": 2}}}).encode()
        if url.endswith("/collections") and method == "GET":
            return 200, json.dumps([]).encode()
        if url.endswith("/collections") and method == "POST":
            return 201, json.dumps({"successful": {"0": {"key": "COLL1"}}}).encode()
        if "/users/1/items/ITEM1" in url and method == "PATCH":
            return 204, b""
        if url.endswith("/items/ATT1/file") and method == "POST":
            if isinstance(body, bytes) and body.startswith(b"upload="):
                return 204, b""
            return 200, json.dumps({"exists": False, "url": "https://s3.example/x",
                                    "prefix": "PRE", "suffix": "POST", "uploadKey": "UK",
                                    "contentType": "application/octet-stream"}).encode()
        if "s3.example" in url and method == "POST":
            return 201, b""
        if url.endswith("/items") and method == "DELETE":
            return 204, b""
        return 500, b"unexpected: " + url.encode()
    return handler, calls


def run_all():
    ok = True
    tmp = tempfile.mkdtemp(prefix="zotero_auto_")
    try:
        # ---- 1) 未配置 -> 明确报错 ----
        conn = make_db(tmp)
        with mock.patch.object(lz, "_zotero_cfg", return_value={}):
            r = lz.zotero_save_work(conn, "x")
        assert not r["ok"] and "未启用" in r["error"], r
        print("[PASS] 1. 未配置时明确报错")

        # ---- 2) 配置后：条目 + collection + v3 三步 PDF 上传 ----
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
            handler, calls = upload_flow()
            with mock.patch.object(lz, "_zotero_http", side_effect=handler):
                r = lz.zotero_save_work(conn, "t:1")
            assert r["ok"] and r["item_key"] == "ITEM1", r
            entry_call = next(c for c in calls if c[0] == "POST" and isinstance(c[2], list) and c[2] and c[2][0].get("itemType") == "journalArticle")
            entry = entry_call[2][0]
            assert entry["title"] == "Test Paper"
            assert entry["DOI"] == "10.1000/abc"
            assert entry["creators"] == [
                {"creatorType": "author", "firstName": "John", "lastName": "Smith"},
                {"creatorType": "author", "firstName": "Jane", "lastName": "Doe"}], entry["creators"]
            att_call = next(c for c in calls if c[0] == "POST" and isinstance(c[2], list) and c[2] and c[2][0].get("itemType") == "attachment")
            att = att_call[2][0]
            assert att["parentItem"] == "ITEM1" and att["filename"] == "paper.pdf"
            assert "content" not in att, "v3 不允许内嵌 base64 content"
            patch_call = next(c for c in calls if c[0] == "PATCH")
            assert "/users/1/items/ITEM1" in patch_call[1], patch_call
            assert patch_call[3].get("If-Unmodified-Since-Version") == "1", patch_call
            auth = next(c for c in calls if c[1].endswith("/items/ATT1/file") and c[0] == "POST" and not (isinstance(c[2], bytes) and c[2].startswith(b"upload=")))
            s3 = next(c for c in calls if "s3.example" in c[1])
            reg = next(c for c in calls if c[0] == "POST" and isinstance(c[2], bytes) and c[2].startswith(b"upload="))
            assert auth and s3 and reg, "三步上传必须完整执行"
        print("[PASS] 2. 条目/collection/三步 PDF 上传 payload 正确")

        # ---- 3) zotero_sync_queue 幂等：已同步的不重复推 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            handler, _calls = upload_flow()
            qp = os.path.join(tmp, "zq.jsonl")
            with open(qp, "w", encoding="utf-8") as f:
                f.write(json.dumps({"work_id": "t:1", "title": "Test Paper", "status": "accepted"}) + "\n")
            with mock.patch.object(lz, "QUEUE_PATH", qp), mock.patch.object(lz, "_zotero_http", side_effect=handler):
                r = lz.zotero_sync_queue(conn)
                assert r["ok"] and r["synced"] == 1, r
                r2 = lz.zotero_sync_queue(conn)
                assert r2["ok"] and r2["synced"] == 0, r2
                entries = lz.load_queue(qp)
                assert entries[0]["zotero_synced_at"] and entries[0]["zotero_item_key"] == "ITEM1"
        print("[PASS] 3. zotero_sync_queue 幂等")

        # ---- 4) 失败保留队列，.bib 兜底不破坏 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}), \
             mock.patch.object(lz, "_zotero_http", side_effect=Exception("network down")):
            r = lz.zotero_save_work(conn, "t:1")
            assert not r["ok"] and "network down" in r["error"], r
        print("[PASS] 4. 网络失败时保留队列并报错")

        # ---- 5) 配额失败：清理半成品 + 队列退避 24h ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            calls = []
            def quota_handler(method, url, body=None, api_key=None, timeout=60,
                              content_type="application/json", headers=None):
                calls.append((method, url, body))
                if url.endswith("/items") and method == "POST":
                    if isinstance(body, list) and body and body[0]["itemType"] == "journalArticle":
                        return 201, json.dumps({"successful": {"0": {"key": "ITEMQ", "version": 1}}}).encode()
                    if isinstance(body, list) and body and body[0]["itemType"] == "attachment":
                        return 201, json.dumps({"successful": {"0": {"key": "ATTQ", "version": 2}}}).encode()
                if "/collections" in url and method == "GET":
                    return 200, json.dumps([]).encode()
                if "/users/1/items/ITEMQ" in url and method == "PATCH":
                    return 204, b""
                if url.endswith("/items/ATTQ/file") and method == "POST":
                    return 413, b"File would exceed quota"
                if url.endswith("/items") and method == "DELETE":
                    return 204, b""
                return 500, b"unexpected"
            qp = os.path.join(tmp, "zq2.jsonl")
            with open(qp, "w", encoding="utf-8") as f:
                f.write(json.dumps({"work_id": "t:1", "title": "Test Paper", "status": "accepted"}) + "\n")
            with mock.patch.object(lz, "QUEUE_PATH", qp), mock.patch.object(lz, "_zotero_http", side_effect=quota_handler):
                r1 = lz.zotero_sync_queue(conn)
                assert r1["failed"] == 1 and "quota" in r1["errors"][0]["error"], r1
                deleted = [c[1] for c in calls if c[0] == "DELETE"]
                assert any("ATTQ" in u for u in deleted), "附件半成品必须清理"
                assert any("ITEMQ" in u for u in deleted), "父条目半成品必须清理"
                entries = lz.load_queue(qp)
                assert entries[0]["zotero_backoff_hours"] == 24, entries[0]
                r2 = lz.zotero_sync_queue(conn)
                assert r2["failed"] == 0 and r2["synced"] == 0, "退避期内不得重试"
        print("[PASS] 5. 配额失败清理半成品 + 24h 退避")
    finally:
        pass
        # ---- 6) collection 后缀匹配：命中已有扁平名，不新建 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            calls = []
            def coll_handler(method, url, body=None, api_key=None, timeout=60,
                             content_type="application/json", headers=None):
                calls.append((method, url, body))
                if "/collections" in url and method == "GET":
                    return 200, json.dumps([{"key": "VNN", "data": {"name": "04_injection/Ballistic"}},
                                            {"key": "KEE", "data": {"name": "02_计算"}}]).encode()
                return 500, b"unexpected"
            with mock.patch.object(lz, "_zotero_http", side_effect=coll_handler):
                k1 = lz._zotero_ensure_collection("K", "1", "01_核心/02_BPVE/04_injection/Ballistic")
                k2 = lz._zotero_ensure_collection("K", "1", "01_核心/02_BPVE/02_计算")
            assert k1 == "VNN", k1
            assert k2 == "KEE", k2
            assert not [c for c in calls if c[0] == "POST"], "已有 collection 时不得新建"
            assert any("/collections" in c[1] and c[0] == "GET" for c in calls)
        print("[PASS] 6. collection 后缀匹配命中已有扁平名")

        # ---- 7) collection 无匹配时只新建叶子名 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            calls = []
            def create_handler(method, url, body=None, api_key=None, timeout=60,
                               content_type="application/json", headers=None):
                calls.append((method, url, body))
                if "/collections" in url and method == "GET":
                    return 200, json.dumps([]).encode()
                if url.endswith("/collections") and method == "POST":
                    return 201, json.dumps({"successful": {"0": {"key": "NEW1"}}}).encode()
                return 500, b"unexpected"
            with mock.patch.object(lz, "_zotero_http", side_effect=create_handler):
                k = lz._zotero_ensure_collection("K", "1", "01_核心/02_BPVE/NewArea")
            assert k == "NEW1", k
            created = [c for c in calls if c[0] == "POST"]
            assert len(created) == 1 and created[0][2] == [{"name": "NewArea"}], created
        print("[PASS] 7. collection 无匹配时只新建叶子名")
    print("zotero auto test: ALL PASS")
    return ok

if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
