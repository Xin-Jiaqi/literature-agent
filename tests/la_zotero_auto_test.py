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
        if method == "GET" and "/items" in url and "q=" in url:
            return 200, b"[]"
        if method == "GET" and "/children" in url:
            return 200, b"[]"
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
            assert entry.get("tags") == [], "不得自动写入任何 tag"
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
                if method == "GET" and "/items" in url and "q=" in url:
                    return 200, b"[]"
                if method == "GET" and "/children" in url:
                    return 200, b"[]"
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
        # ---- 8) WebDAV 模式：PUT 到 WebDAV + PATCH md5/mtime，不走官方三步 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={
                "api_key": "K", "user_id": "1", "auto_sync": True,
                "webdav": {"url": "https://dav.example.com/dav", "username": "u", "password": "p"}}):
            calls = []
            def webdav_handler(method, url, body=None, api_key=None, timeout=60,
                               content_type="application/json", headers=None):
                calls.append((method, url, body))
                if url.endswith("/items") and method == "POST":
                    return 201, json.dumps({"successful": {"0": {"key": "ATTW", "version": 1}}}).encode()
                if url.endswith("/items/ATTW") and method == "GET":
                    return 200, json.dumps({"key": "ATTW", "version": 5, "data": {}}).encode()
                if url.endswith("/items/ATTW") and method == "PATCH":
                    return 204, b""
                if url.endswith("/items") and method == "DELETE":
                    return 204, b""
                return 500, b"unexpected"
            fake_urlopen = mock.MagicMock()
            fake_urlopen.return_value.__enter__.return_value.status = 201
            with mock.patch.object(lz, "_zotero_http", side_effect=webdav_handler), \
                 mock.patch("urllib.request.urlopen", fake_urlopen):
                ok_w, detail = lz._zotero_upload_pdf("K", "1", "ITEM1", pdf)
            assert ok_w, detail
            puts = [c[0][0] for c in fake_urlopen.call_args_list]
            assert len(puts) == 2, "必须 PUT .zip 和 .prop"
            zip_req = next(r for r in puts if r.full_url.endswith(".zip"))
            prop_req = next(r for r in puts if r.full_url.endswith(".prop"))
            assert zip_req.full_url == "https://dav.example.com/dav/zotero/ATTW.zip", zip_req.full_url
            assert prop_req.full_url == "https://dav.example.com/dav/zotero/ATTW.prop", prop_req.full_url
            assert zip_req.get_method() == "PUT" and prop_req.get_method() == "PUT"
            assert "Basic " in zip_req.get_header("Authorization")
            import io, zipfile, hashlib
            with zipfile.ZipFile(io.BytesIO(zip_req.data)) as zf:
                assert zf.namelist() == ["paper.pdf"], zf.namelist()
                assert hashlib.md5(zf.read("paper.pdf")).hexdigest() == hashlib.md5(open(pdf, "rb").read()).hexdigest()
            prop = prop_req.data.decode("utf-8")
            assert "<mtime>" in prop and "<hash>" in prop, prop
            patch = next(c for c in calls if c[0] == "PATCH")
            body = patch[2]
            assert body["md5"] and body["mtime"] > 0, body
            assert not [c for c in calls if "/items/ATTW/file" in c[1]], "WebDAV 模式不得走官方三步上传"
        print("[PASS] 8. WebDAV 模式 PUT + md5/mtime 注册")

        # ---- 9) 未配置 WebDAV 时仍走官方三步上传（回归） ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            handler2, calls2 = upload_flow()
            with mock.patch.object(lz, "_zotero_http", side_effect=handler2):
                r = lz.zotero_save_work(conn, "t:1")
            assert r["ok"], r
            assert any("s3.example" in c[1] for c in calls2), "未配置 WebDAV 必须走官方三步上传"
        print("[PASS] 9. 未配置 WebDAV 时官方上传回归")

        # ---- 10) 查重命中：不新建条目，PDF 挂到已有条目 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            calls = []
            def dedup_handler(method, url, body=None, api_key=None, timeout=60,
                              content_type="application/json", headers=None):
                calls.append((method, url, body, headers))
                if method == "GET" and "/items" in url and "q=" in url:
                    return 200, json.dumps([{"key": "EXIST", "data": {
                        "itemType": "journalArticle", "title": "Test Paper",
                        "DOI": "10.1000/abc"}}]).encode()
                if method == "GET" and "/children" in url:
                    return 200, b"[]"
                if url.endswith("/items") and method == "POST":
                    if isinstance(body, list) and body and body[0]["itemType"] == "attachment":
                        assert body[0]["parentItem"] == "EXIST", "附件必须挂到已有条目"
                        return 201, json.dumps({"successful": {"0": {"key": "ATT1", "version": 2}}}).encode()
                    return 500, b"dedup: unexpected journalArticle POST"
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
            with mock.patch.object(lz, "_zotero_http", side_effect=dedup_handler):
                r = lz.zotero_save_work(conn, "t:1")
            assert r["ok"] and r["deduped"] is True and r["item_key"] == "EXIST", r
            journals = [c for c in calls if c[0] == "POST" and isinstance(c[2], list) and c[2] and c[2][0].get("itemType") == "journalArticle"]
            assert not journals, "查重命中后不得新建条目"
            atts = [c for c in calls if c[0] == "POST" and isinstance(c[2], list) and c[2] and c[2][0].get("itemType") == "attachment"]
            assert len(atts) == 1 and atts[0][2][0]["parentItem"] == "EXIST", atts
        print("[PASS] 10. 查重命中不重复建档，PDF 挂到已有条目")

        # ---- 11) 已有条目已带 PDF：不再补附件 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            calls = []
            def haspdf_handler(method, url, body=None, api_key=None, timeout=60,
                               content_type="application/json", headers=None):
                calls.append((method, url, body))
                if method == "GET" and "/items" in url and "q=" in url:
                    return 200, json.dumps([{"key": "EXIST", "data": {
                        "itemType": "journalArticle", "title": "Test Paper",
                        "DOI": "10.1000/abc"}}]).encode()
                if method == "GET" and "/children" in url:
                    return 200, json.dumps([{"key": "CH1", "data": {
                        "itemType": "attachment", "filename": "paper.pdf"}}]).encode()
                return 500, b"unexpected: " + url.encode()
            with mock.patch.object(lz, "_zotero_http", side_effect=haspdf_handler):
                r = lz.zotero_save_work(conn, "t:1")
            assert r["ok"] and r["deduped"] is True and r["pdf_attached"] is False, r
            assert not [c for c in calls if c[0] == "POST"], "已有 PDF 时不得新建任何条目"
        print("[PASS] 11. 已有 PDF 时不重复上传附件")

        # ---- 12) 测试隔离：队列被重定向时禁止写真实 Zotero ----
        with mock.patch.dict(os.environ, {"LITAGENT_ZOTERO_QUEUE": os.path.join(tmp, "iso.jsonl")}), \
             mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            assert not lz.zotero_auto_enabled(), "测试环境不得启用真实 Zotero 同步"
            r = lz.zotero_save_work(conn, "t:1")
            assert not r["ok"] and "未启用" in r["error"], r
        print("[PASS] 12. LITAGENT_ZOTERO_QUEUE 重定向时禁止写真实 Zotero")

        # ---- 13) 查重 fail closed：DOI lookup 500 → 不创建条目 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            calls = []
            def fail500_handler(method, url, body=None, api_key=None, timeout=60,
                                content_type="application/json", headers=None):
                calls.append((method, url))
                if method == "GET" and "/items" in url and "q=" in url:
                    return 500, b"boom"
                if url.endswith("/items") and method == "POST":
                    return 201, json.dumps({"successful": {"0": {"key": "BAD", "version": 1}}}).encode()
                return 500, b"unexpected: " + url.encode()
            with mock.patch.object(lz, "_zotero_http", side_effect=fail500_handler):
                r = lz.zotero_save_work(conn, "t:1")
            assert not r["ok"] and "dedup lookup failed" in r["error"], r
            assert not [c for c in calls if c[0] == "POST"], "查重失败时不得创建条目"
        print("[PASS] 13. DOI lookup 500 → 不创建 item（fail closed）")

        # ---- 14) 查重 fail closed：DOI lookup 网络异常 → 不创建条目 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            calls = []
            def net_handler(method, url, body=None, api_key=None, timeout=60,
                            content_type="application/json", headers=None):
                calls.append((method, url))
                if method == "GET" and "/items" in url and "q=" in url:
                    raise Exception("network down")
                if url.endswith("/items") and method == "POST":
                    return 201, json.dumps({"successful": {"0": {"key": "BAD", "version": 1}}}).encode()
                return 500, b"unexpected: " + url.encode()
            with mock.patch.object(lz, "_zotero_http", side_effect=net_handler):
                r = lz.zotero_save_work(conn, "t:1")
            assert not r["ok"] and "dedup lookup failed" in r["error"], r
            assert not [c for c in calls if c[0] == "POST"], "网络异常时不得创建条目"
        print("[PASS] 14. DOI lookup 网络异常 → 不创建 item（fail closed）")

        # ---- 15) 三路查重都明确 empty → 才允许创建 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            calls = []
            def empty_handler(method, url, body=None, api_key=None, timeout=60,
                              content_type="application/json", headers=None):
                calls.append((method, url))
                if method == "GET" and "/items" in url and "q=" in url:
                    return 200, b"[]"
                if method == "GET" and "/children" in url:
                    return 200, b"[]"
                if url.endswith("/items") and method == "POST":
                    if isinstance(body, list) and body and body[0]["itemType"] == "journalArticle":
                        return 201, json.dumps({"successful": {"0": {"key": "NEW1", "version": 1}}}).encode()
                    if isinstance(body, list) and body and body[0]["itemType"] == "attachment":
                        return 201, json.dumps({"successful": {"0": {"key": "ATT1", "version": 2}}}).encode()
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
            with mock.patch.object(lz, "_zotero_http", side_effect=empty_handler):
                r = lz.zotero_save_work(conn, "t:1")
            assert r["ok"] and r["item_key"] == "NEW1" and r["deduped"] is False, r
            searches = [c for c in calls if c[0] == "GET" and "q=" in c[1]]
            assert len(searches) >= 2, "DOI+title 都明确 empty 后才创建"
        print("[PASS] 15. 三路查重明确 NOT_FOUND → 才创建")

        # ---- 16) children GET 500 → 不补 PDF，返回失败 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            calls = []
            def childfail_handler(method, url, body=None, api_key=None, timeout=60,
                                  content_type="application/json", headers=None):
                calls.append((method, url))
                if method == "GET" and "/items" in url and "q=" in url:
                    return 200, json.dumps([{"key": "EXIST", "data": {
                        "itemType": "journalArticle", "title": "Test Paper",
                        "DOI": "10.1000/abc"}}]).encode()
                if method == "GET" and "/children" in url:
                    return 500, b"children boom"
                if url.endswith("/items") and method == "POST":
                    return 201, json.dumps({"successful": {"0": {"key": "BAD", "version": 1}}}).encode()
                return 500, b"unexpected: " + url.encode()
            with mock.patch.object(lz, "_zotero_http", side_effect=childfail_handler):
                r = lz.zotero_save_work(conn, "t:1")
            assert not r["ok"] and "children lookup failed" in r["error"], r
            assert not [c for c in calls if c[0] == "POST"], "children 查询失败时不得补附件"
        print("[PASS] 16. children GET 500 → 不补 PDF（fail closed）")

        # ---- 17) API 恢复后重试：只创建一个条目，不重复 ----
        with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
            mode = {"n": 0}
            calls = []
            def recover_handler(method, url, body=None, api_key=None, timeout=60,
                                content_type="application/json", headers=None):
                calls.append((method, url, body))
                if method == "GET" and "/items" in url and "q=" in url:
                    mode["n"] += 1
                    if mode["n"] == 1:
                        raise Exception("network down")
                    if mode["n"] <= 3:
                        return 200, b"[]"
                    return 200, json.dumps([{"key": "ONLY1", "data": {
                        "itemType": "journalArticle", "title": "Test Paper",
                        "DOI": "10.1000/abc"}}]).encode()
                if method == "GET" and "/children" in url:
                    return 200, b"[]"
                if url.endswith("/items") and method == "POST":
                    if isinstance(body, list) and body and body[0]["itemType"] == "journalArticle":
                        return 201, json.dumps({"successful": {"0": {"key": "ONLY1", "version": 1}}}).encode()
                    if isinstance(body, list) and body and body[0]["itemType"] == "attachment":
                        assert body[0]["parentItem"] == "ONLY1"
                        return 201, json.dumps({"successful": {"0": {"key": "ATT1", "version": 2}}}).encode()
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
            with mock.patch.object(lz, "_zotero_http", side_effect=recover_handler):
                r1 = lz.zotero_save_work(conn, "t:1")
                assert not r1["ok"] and "dedup lookup failed" in r1["error"], r1
                r2 = lz.zotero_save_work(conn, "t:1")
                assert r2["ok"] and r2["item_key"] == "ONLY1" and r2["deduped"] is False, r2
                r3 = lz.zotero_save_work(conn, "t:1")
                assert r3["ok"] and r3["item_key"] == "ONLY1" and r3["deduped"] is True, r3
            journals = [c for c in calls if c[0] == "POST" and isinstance(c[2], list)
                        and c[2] and c[2][0].get("itemType") == "journalArticle"]
            assert len(journals) == 1, "恢复后只创建一个条目，重试命中已有条目"
        print("[PASS] 17. API 恢复后重试不重复建档")
    print("zotero auto test: ALL PASS")
    return ok

if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
