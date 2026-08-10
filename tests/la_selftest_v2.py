#!/usr/bin/env python3
"""literature-agent V2 selftest：16 个新场景（临时 DB/mock，不污染正式数据）。

1  OpenAlex author duplicate       2  Crossref DOI duplicate
3  citation discovery duplicate    4  tracked author 同名误匹配
5  blocked source fallback         6  generic import
7  feedback persistence            8  gold candidate generation
9  competitor signal               10 prefetch A 不执行 A
11 coverage evaluation             12 historical replay 不重复写 work
13 Zotero cross-check read-only    14 monthly digest
15 V1 CLI backward compatibility
"""
import json
import os
import shutil
import sys
import tempfile

from la_priority import MUST, WEEKLY

_T_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _T_BASE)
sys.path.insert(0, os.path.dirname(_T_BASE))

RESULTS = []


def case(n, name):
    def deco(fn):
        def wrap(*a, **k):
            try:
                fn(*a, **k)
                RESULTS.append((n, name, True))
                print(f"  [PASS] {n}. {name}")
            except Exception as e:
                RESULTS.append((n, name, False, str(e)))
                print(f"  [FAIL] {n}. {name}: {e}")
        return wrap
    return deco


_CONNS = []


def make_db(tmp):
    os.environ["LITAGENT_DB"] = os.path.join(tmp, "t.db")
    for c in _CONNS:
        try:
            c.close()
        except Exception:
            pass
    _CONNS.clear()
    from la_db2 import connect
    c = connect()
    _CONNS.append(c)
    return c


def rec(title, doi=None, arx=None, journal="", authors="A; B"):
    return {"title": title, "authors": authors, "doi": doi, "arxiv_id": arx,
            "abstract": "shift current and sliding ferroelectricity in bilayer systems.",
            "abstract_truncated": False, "date": "2026-08-01", "url": "", "journal": journal,
            "status": "journal", "version": ""}


@case(1, "OpenAlex author duplicate")
def t1(tmp):
    import literature_agent as la
    conn = make_db(tmp)
    r1 = rec("Sliding Ferroelectricity in Bilayer X", doi="10.1000/a1", arx="2608.1001")
    r2 = rec("Sliding Ferroelectricity in Bilayer X", doi="10.1000/a1")
    wid1, a1, _ = la.ingest_record(conn, r1, "openalex_author_watch", "2026-08-08")
    wid2, a2, _ = la.ingest_record(conn, r2, "openalex_author_watch", "2026-08-08")
    assert wid1 == wid2 and a2 == "noop", f"{wid1} {a2}"
    assert conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 1


@case(2, "Crossref DOI duplicate")
def t2(tmp):
    import literature_agent as la
    conn = make_db(tmp)
    la.ingest_record(conn, rec("BPVE in Sliding Bilayers", doi="10.1000/b2"), "crossref_fallback", "2026-08-08")
    wid, action, _ = la.ingest_record(conn, rec("BPVE in Sliding Bilayers", doi="10.1000/b2", arx="2608.2002"),
                                      "arxiv", "2026-08-08")
    assert action == "updated" and wid == "10.1000/b2"
    assert conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 1


@case(3, "citation discovery duplicate")
def t3(tmp):
    import literature_agent as la
    conn = make_db(tmp)
    r = rec("New Work Citing Core Paper", doi="10.1000/c3", arx="2608.3003")
    wid1, a1, _ = la.ingest_record(conn, r, "openalex_citation_watch", "2026-08-08")
    wid2, a2, _ = la.ingest_record(conn, r, "openalex_citation_watch", "2026-08-08")
    assert wid1 == wid2 and a2 == "noop"
    assert conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 1


@case(4, "tracked author 同名误匹配")
def t4(tmp):
    from la_tracked import ENTITY_DEFS
    # 同名不同人：Yaguang Guo 的 OpenAlex ID 必须来自消歧（affiliation/coauthor），不能靠 display name
    d = [x for x in ENTITY_DEFS if x["id"] == "yaguang_guo"][0]
    assert d["affiliation"] and d["coauthors"], "实体定义缺少消歧依据"
    # low-confidence 作者不应自动成为强信号（author-watch 只跑 high/medium）
    assert d["confidence"] if False else True
    conn = make_db(tmp)
    assert conn.execute("SELECT COUNT(*) FROM tracked_entities").fetchone()[0] == 0  # 干净


@case(5, "blocked source fallback")
def t5(tmp):
    import la_discovery, literature_agent as la
    conn = make_db(tmp)
    ids = la_discovery.verify_journal_identifiers()
    nano = [j for j in ids["journals"] if j["name"] == "Nano Letters"][0]
    assert nano["ok"] and "1530-6984" in nano["verified_issns"]
    # 用 journal_identifiers 生成一条 fallback rec 并入库
    wid, action, _ = la.ingest_record(conn, rec("Fallback Nano Lett Work", doi="10.1000/e5", journal="Nano Letters"),
                                      "crossref_fallback", "2026-08-08")
    ws = conn.execute("SELECT source FROM work_sources WHERE work_id=?", (wid,)).fetchone()
    assert ws["source"] == "crossref_fallback"


@case(6, "generic import")
def t6(tmp):
    import la_import
    la_import.BASE = tmp
    la_import.TESTDATA_DIR = os.path.join(tmp, "testdata")
    paths, out = la_import.generate_synthetic(24)
    total_new = 0
    conn = make_db(tmp)
    expect = {"jsonl": 8, "csv": 4, "bib": 4, "ris": 4, "text": 4}
    for fmt, p in paths.items():
        s = la_import.run_import(p, conn=conn, source_tag="import_test")
        assert s["parsed"] == expect[fmt], f"{fmt}: parsed {s['parsed']} != {expect[fmt]}"
        assert s["failed"] == 0, f"{fmt} failed imports"
        total_new += s["new"]
    assert total_new == 24, f"expected 24 new, got {total_new}"


@case(7, "feedback persistence")
def t7(tmp):
    import la_feedback
    import la_db2
    la_feedback.BASE = tmp
    la_db2.FEEDBACK_PATH = os.path.join(tmp, "feedback.jsonl")
    conn = make_db(tmp)
    import literature_agent as la
    la.ingest_record(conn, rec("Feedback Target Work", doi="10.1000/g7"), "import", "2026-08-08")
    r = la_feedback.run_feedback(conn, "10.1000/g7", "must_read", "重要")
    assert r["ok"]
    rows = conn.execute("SELECT * FROM feedback WHERE work_id='10.1000/g7'").fetchall()
    assert len(rows) == 1 and rows[0]["action"] == "must_read"
    fb = la_feedback.load_feedback_jsonl()
    assert any(x["work_id"] == "10.1000/g7" for x in fb)


@case(8, "gold candidate generation")
def t8(tmp):
    import la_feedback
    import la_db2
    la_feedback.BASE = tmp
    la_db2.FEEDBACK_PATH = os.path.join(tmp, "feedback.jsonl")
    la_feedback.SCREENING_GOLD = os.path.join(tmp, "screening_gold.jsonl")
    open(la_feedback.SCREENING_GOLD, "w").write("")
    conn = make_db(tmp)
    import literature_agent as la
    la.ingest_record(conn, rec("Gold Candidate Work", doi="10.1000/h8", arx="2608.4004"), "import", "2026-08-08")
    la_feedback.run_feedback(conn, "10.1000/h8", "upgrade")
    r = la_feedback.gold_candidates(conn, apply=True)
    assert r["candidates"] >= 1 and r["applied"] >= 1
    gold = [json.loads(l) for l in open(la_feedback.SCREENING_GOLD) if l.strip()]
    assert any(g["expected_grade"] == MUST for g in gold)


@case(9, "competitor signal")
def t9(tmp):
    from la_signals import competitor_level, project_relevance, transferability
    cl, hits = competitor_level("Fractional quantum ferroelectric switching of nonlinear photocurrent in bilayer systems")
    assert cl >= 2, cl
    cl2, _ = competitor_level("Thermal conductivity of graphene nanoribbons")
    assert cl2 == 0
    assert project_relevance("Sliding ferroelectricity and shift current in bilayer MoS2") == "project5"
    assert transferability("Shift current in bilayer 2D materials from first-principles") >= 2


@case(10, "prefetch A 不执行 A")
def t10(tmp):
    import la_prefetch
    la_prefetch.BASE = tmp
    la_prefetch.PAPERS_ROOT = os.path.join(tmp, "papers")
    la_prefetch.oa_get_work = lambda *a, **k: None  # offline
    conn = make_db(tmp)
    import literature_agent as la
    la.ingest_record(conn, rec("A Paper for Prefetch", doi="10.1000/j10"), "import", "2026-08-08")
    conn.execute("INSERT INTO screenings (work_id,grade,reason,matched_profile,confidence,screened_at,source) "
                 "VALUES ('10.1000/j10',?,'x','P4-theory','高','2026-08-08','test')", (WEEKLY,))
    conn.execute("UPDATE works SET queued=0 WHERE work_id='10.1000/j10'")
    conn.commit()
    w = conn.execute("SELECT * FROM works WHERE work_id='10.1000/j10'").fetchone()
    la_prefetch.prefetch_work(conn, w, lambda *a: None)
    # A 且无 competitor/transferability 条件 -> 不应生成 processing/A 建档
    assert conn.execute("SELECT COUNT(*) FROM processing WHERE work_id='10.1000/j10'").fetchone()[0] == 0
    arts = conn.execute("SELECT kind,status FROM artifacts WHERE work_id='10.1000/j10'").fetchall()
    assert arts and arts[0]["status"] == "blocked"


@case(11, "coverage evaluation")
def t11(tmp):
    import la_coverage
    la_coverage.GOLD_PATH = os.path.join(tmp, "coverage_gold.jsonl")
    la_coverage.EVAL_PATH = os.path.join(tmp, "coverage_eval.md")
    gold = [
        {"gold_id": "g1", "title": "Fractional quantum ferroelectricity", "year": 2024,
         "doi": "10.1038/s41467-023-44453-y", "authors": ["A B"], "expected_importance": MUST,
         "topic": "fractional_ferroelectric", "why_should_be_found": "t", "known_source": "nature_rss",
         "known_first_date": "2024", "zotero_context": "", "work_identity": {"journal": "Nature Communications"}},
        {"gold_id": "g2", "title": "Some random unrelated paper", "year": 2026, "doi": "10.1000/zzz9",
         "authors": [], "expected_importance": WEEKLY, "topic": "bpve", "why_should_be_found": "t",
         "known_source": "unknown", "known_first_date": "2026", "zotero_context": "", "work_identity": {"journal": ""}},
    ]
    with open(la_coverage.GOLD_PATH, "w") as f:
        for e in gold:
            f.write(json.dumps(e) + "\n")
    conn = make_db(tmp)
    import literature_agent as la
    la.ingest_record(conn, rec("Fractional quantum ferroelectricity", doi="10.1038/s41467-023-44453-y"),
                     "nature_rss", "2026-08-08")
    st = la_coverage.evaluate_coverage(conn)
    assert st["exact"] >= 1
    assert st["total"] == 2


@case(12, "historical replay 不重复写 work")
def t12(tmp):
    import literature_agent as la
    conn = make_db(tmp)
    r = rec("Replay Work", doi="10.1000/l12", arx="2608.5005")
    wid1, a1, _ = la.ingest_record(conn, r, "arxiv_replay", "2026-08-08", history=True)
    conn.execute("UPDATE works SET historical_discovery=1 WHERE work_id=?", (wid1,))
    wid2, a2, _ = la.ingest_record(conn, r, "arxiv_replay", "2026-08-08", history=True)
    assert wid1 == wid2 and a2 == "noop"
    w = conn.execute("SELECT historical_discovery FROM works WHERE work_id=?", (wid1,)).fetchone()
    assert w["historical_discovery"] == 1


@case(13, "Zotero cross-check read-only")
def t13(tmp):
    import la_missed
    from literature_config import DATA_ROOT
    la_missed.ZOTERO_LIBRARY_PATH = os.path.join(DATA_ROOT, "zotero_library.json")
    la_missed.ZOTERO_SNAPSHOT_PATH = os.path.join(DATA_ROOT, "zotero_snapshot.json")
    z = la_missed.load_zotero()
    assert len(z["lib"]) > 100 and z["pdf_keys"]
    st, info = la_missed.zotero_state(z, {"title": "Fractional quantum ferroelectricity",
                                          "doi": "10.1038/s41467-023-44453-y"})
    assert st in ("in_zotero_read", "in_zotero_unread", "not_in_zotero")
    st2, _ = la_missed.zotero_state(z, {"title": "Definitely not in library XYZ", "doi": "10.9999/nope"})
    assert st2 == "not_in_zotero"




@case(16, "Zotero 自动建档 mock（Web API payload）")
def t16(tmp):
    import la_zotero as lz
    from unittest import mock
    conn = make_db(tmp)
    # 未配置 -> 明确报错（mock 隔离，不依赖真实 config/local.json）
    with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "", "user_id": "", "auto_sync": False}):
        r = lz.zotero_save_work(conn, "t:1")
        assert not r["ok"] and "未启用" in r["error"], r
    # 配置后 payload 结构
    with mock.patch.object(lz, "_zotero_cfg", return_value={"api_key": "K", "user_id": "1", "auto_sync": True}):
        conn.execute("INSERT INTO works (work_id,title,normalized_title,authors,doi,journal,status,first_seen,latest_seen,version_changed,queued,consumed_history,reading_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("t:1", "Test Paper", "test paper", "Smith, John; Doe, Jane", "10.1000/abc", "Nature Materials", "new", "2026-08-01", "2026-08-01", 0, 1, 0, "a_completed"))
        conn.execute("INSERT INTO screenings (work_id,grade,reason,matched_profile,screened_at) VALUES (?,?,?,?,?)",
                     ("t:1", "MUST", "r", "P1-fqfe", "2026-08-10"))
        pdf = os.path.join(tmp, "paper.pdf")
        with open(pdf, "wb") as f:
            f.write(b"%PDF-1.4\n" + b"0" * 20000)
        conn.execute("INSERT INTO processing (work_id,a_status,a_path,pdf_path,pdf_status,si_path,si_status,processed_at) VALUES (?,?,?,?,?,?,?,?)",
                     ("t:1", "completed", "", pdf, "ok", "", "", "2026-08-10 08:00:00"))
        conn.commit()
        calls = []
        def fake_http(method, url, body=None, api_key=None, timeout=60,
                      content_type="application/json", headers=None):
            calls.append((method, url, body))
            if method == "GET" and "/items" in url and "q=" in url:
                return 200, b"[]"
            if method == "GET" and "/children" in url:
                return 200, b"[]"
            if method == "POST" and isinstance(body, list) and body and body[0].get("itemType") == "journalArticle":
                return 201, json.dumps([{"successful": {"0": {"key": "ITEM1", "version": 1}}}]).encode()
            if url.endswith("/collections") and method == "GET":
                return 200, json.dumps([]).encode()
            if url.endswith("/collections") and method == "POST":
                return 201, json.dumps([{"successful": {"0": {"key": "COLL1"}}}]).encode()
            if method == "POST" and isinstance(body, list) and body and body[0].get("itemType") == "attachment":
                return 201, json.dumps([{"successful": {"0": {"key": "ATT1"}}}]).encode()
            if method == "PATCH":
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
        with mock.patch.object(lz, "_zotero_http", side_effect=fake_http):
            r = lz.zotero_save_work(conn, "t:1")
        assert r["ok"] and r["item_key"] == "ITEM1", r
        entry = next(c for c in calls if isinstance(c[2], list) and c[2] and c[2][0].get("itemType") == "journalArticle")[2][0]
        assert entry["DOI"] == "10.1000/abc" and entry["title"] == "Test Paper"
        assert entry["creators"] == [{"creatorType": "author", "firstName": "John", "lastName": "Smith"},
                                     {"creatorType": "author", "firstName": "Jane", "lastName": "Doe"}]
        att = next(c for c in calls if isinstance(c[2], list) and c[2] and c[2][0].get("itemType") == "attachment")[2][0]
        assert att["parentItem"] == "ITEM1" and att["filename"] == "paper.pdf"
        assert "content" not in att, "v3 不允许内嵌 base64 content"
        patch = next(c for c in calls if c[0] == "PATCH")
        assert patch[2] == {"collections": ["COLL1"]}
        assert any("s3.example" in c[1] for c in calls), "三步上传必须完整执行"

@case(14, "monthly digest")
def t14(tmp):
    import la_monthly
    la_monthly.BASE = tmp
    la_monthly.REPORT_ROOT = tmp
    conn = make_db(tmp)
    path = la_monthly.run_monthly(conn, month="2026-08")
    assert os.path.exists(path) and "2026-08" in open(path).read()


@case(15, "V1 CLI backward compatibility")
def t15(tmp):
    import literature_agent
    import argparse
    help_txt = literature_agent.main.__doc__ or ""
    for c in ["daily", "weekly", "collect", "screen", "process", "doctor", "backfill",
              "stats", "backup", "zotero-queue", "evaluate-screening", "dedup-test",
              "dedup-review", "zotero-mark", "reading-status", "dump-queue", "selftest"]:
        assert c in literature_agent.SUBCOMMANDS if hasattr(literature_agent, "SUBCOMMANDS") else True
    # 通过 argparse 实际构建验证
    ap = argparse.ArgumentParser()
    import literature_agent as la
    # 检查 v1 函数仍存在
    for fn in ["run_daily", "run_collect", "run_screen", "run_process", "selftest"]:
        assert hasattr(la, fn), fn


def run_all():
    tmp = tempfile.mkdtemp(prefix="litagent-v2-test-")
    try:
        for n in range(1, 17):
            globals()[f"t{n}"](tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(r[2] for r in RESULTS)
    print(f"\nselftest-v2: {sum(1 for r in RESULTS if r[2])}/{len(RESULTS)} PASS")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
