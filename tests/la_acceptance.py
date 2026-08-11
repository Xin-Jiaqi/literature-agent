#!/usr/bin/env python3
"""literature-agent acceptance suite：全部在临时 DB/vault/fixtures 中运行。

- golden path：live MUST 全链路（ingest→dedup→screen→PDF→A→canonical→overview→sync→handoff）。
- 四条用户路径：later / ignore / 无操作 / ChatGPT 做 A。
- failure/recovery：网络失败、PDF 403/HTML、invalid PDF、arXiv→accepted→published 去重、
  多 disposition 冲突、rerun overview、rerun daily、A 移动重定位、原子写、DB 回滚、
  blocked PDF、historical 不进入 live、handoff 重复生成。
"""
import json
import os
import re
import shutil
import sys
import tempfile

_T_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _T_BASE)
sys.path.insert(0, os.path.dirname(_T_BASE))

RESULTS = []


def _fake_pdf(path, min_bytes=12000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n" + b"0" * (min_bytes - 8))


def _a_doc(title):
    body = "这是一段用于 acceptance 测试的 A 建档内容。" * 22
    return (f"# 文献快速建档：{title}\n\n## 0. 文献信息\n\n- 标题：{title}\n"
            f"- 作者：T Author\n- 期刊或预印本平台、年份：arXiv，2026-08-01 v1\n"
            f"- DOI 或 arXiv 编号：测试\n- 本次阅读依据：正文 PDF。\n\n"
            f"## 1. 一句话总结与研究对象\n\n{body}\n\n## 2. 旧问题与新贡献\n\n{body}\n\n"
            f"## 3. 核心证据与关键位置\n\n{body}\n\n## 4. 对用户的潜在价值与引用方式\n\n{body}\n\n"
            f"## 5. 阅读优先级与建议去向\n\n{body}\n")


_ENV_KEYS = [
    "LITAGENT_DB", "LITAGENT_VAULT_ROOT", "LITAGENT_PAPERS_DIR", "LITAGENT_ZOTERO_QUEUE",
    "LITAGENT_FEEDBACK", "LITAGENT_A_QUEUE", "LITAGENT_A_JOBS", "LITAGENT_OVERVIEW_DIR",
    "LITAGENT_DIRECTION_FEEDBACK", "LITAGENT_DAILY_DIR", "LITAGENT_HANDOFF_DIR",
    "LITAGENT_REGISTRY",
]


def make_env(tmp):
    """临时 DB + vault + papers + 各队列；返回 (conn, env, restore)。

    restore() 恢复调用前的 os.environ 与模块级路径常量，避免内层 make_env
    （如 rerun daily 用例）污染后续用例。
    """
    import la_overview as lov
    import la_workflow as lwf
    import la_zotero
    import la_digest
    import la_handoff as lh
    import literature_agent as _la
    _MOD_ATTRS = {
        lwf: ["VAULT_ROOT", "PAPERS_DIR", "VAULT_STAGES", "PACKAGE_DIR", "A_QUEUE_PATH"],
        lov: ["OVERVIEW_DIR", "HANDOFF_DIR", "DIRECTION_FEEDBACK_PATH"],
        la_zotero: ["QUEUE_PATH"],
        la_digest: ["DAILY_DIR"],
        lh: ["HANDOFF_DIR", "HANDOFF_PATH", "STATE_PATH", "CODEX_HANDOFF_PATH"],
        _la: ["LOG_DIR", "SOURCES_CACHE", "SOURCES"],
    }
    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    saved_mod = {m: {a: getattr(m, a, None) for a in attrs} for m, attrs in _MOD_ATTRS.items()}
    vault = os.path.join(tmp, "vault")
    os.makedirs(os.path.join(vault, ".obsidian"))
    vault_lit = os.path.join(vault, "Literatures")
    package = os.path.join(vault_lit, "每日文献", "_文献包")
    stages = {"package": package,
              "inbox": os.path.join(vault_lit, "0_待处理"),
              "processing": os.path.join(vault_lit, "1_处理中"),
              "callable": os.path.join(vault_lit, "2-可调用"),
              "read": os.path.join(vault_lit, "00-已阅")}
    for d in stages.values():
        os.makedirs(d, exist_ok=True)
    papers = os.path.join(tmp, "papers")
    os.makedirs(papers, exist_ok=True)
    env = {
        "LITAGENT_DB": os.path.join(tmp, "t.db"),
        "LITAGENT_VAULT_ROOT": vault_lit,
        "LITAGENT_PAPERS_DIR": papers,
        "LITAGENT_ZOTERO_QUEUE": os.path.join(tmp, "zotero_queue.jsonl"),
        "LITAGENT_FEEDBACK": os.path.join(tmp, "feedback.jsonl"),
        "LITAGENT_A_QUEUE": os.path.join(tmp, "a_queue.jsonl"),
        "LITAGENT_A_JOBS": os.path.join(tmp, "a_jobs"),
        "LITAGENT_OVERVIEW_DIR": os.path.join(vault_lit, "每日文献"),
        "LITAGENT_DIRECTION_FEEDBACK": os.path.join(tmp, "direction_feedback_candidates.md"),
        "LITAGENT_DAILY_DIR": os.path.join(tmp, "daily"),
        "LITAGENT_HANDOFF_DIR": os.path.join(tmp, "state"),
        "LITAGENT_REGISTRY": os.path.join(tmp, "sources.json"),
    }
    for k, v in env.items():
        os.environ[k] = v
    lwf.VAULT_ROOT = vault_lit
    lwf.PAPERS_DIR = papers
    lwf.VAULT_STAGES = dict(stages)
    lwf.PACKAGE_DIR = package
    lwf.A_QUEUE_PATH = env["LITAGENT_A_QUEUE"]
    lov.OVERVIEW_DIR = env["LITAGENT_OVERVIEW_DIR"]
    lov.HANDOFF_DIR = os.path.join(lov.OVERVIEW_DIR, "_ChatGPT交接")
    lov.DIRECTION_FEEDBACK_PATH = env["LITAGENT_DIRECTION_FEEDBACK"]
    la_zotero.QUEUE_PATH = env["LITAGENT_ZOTERO_QUEUE"]
    la_digest.DAILY_DIR = env["LITAGENT_DAILY_DIR"]
    lh.HANDOFF_DIR = env["LITAGENT_HANDOFF_DIR"]
    os.makedirs(lh.HANDOFF_DIR, exist_ok=True)
    lh.HANDOFF_PATH = os.path.join(env["LITAGENT_HANDOFF_DIR"], "HANDOFF.md")
    lh.STATE_PATH = os.path.join(env["LITAGENT_HANDOFF_DIR"], "PROJECT_STATE.json")
    lh.CODEX_HANDOFF_PATH = os.path.join(env["LITAGENT_HANDOFF_DIR"], "CODEX_HANDOFF.md")
    # 最小来源注册表 fixture：让 collect 有源可跑（fetch 由各用例 mock）
    reg = {"schema_version": 1, "updated": "2026-08-09", "note": "acceptance fixture", "sources": [
        {"id": "aps_accepted", "type": "rss", "role": "journal", "priority": 10, "enabled": True,
         "feeds": [{"journal": "Physical Review Letters", "url": "https://example.invalid/aps.rss",
                    "parser": "rss1"}]}]}
    with open(env["LITAGENT_REGISTRY"], "w", encoding="utf-8") as f:
        json.dump(reg, f)
    _la.LOG_DIR = os.path.join(tmp, "logs")
    os.makedirs(_la.LOG_DIR, exist_ok=True)
    # 重建 SOURCES（模块可能已按旧 registry 缓存）
    _la.SOURCES_CACHE = None
    _la.SOURCES = _la.get_sources()
    from la_db2 import connect
    conn = connect()

    def restore():
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for m, attrs in _MOD_ATTRS.items():
            for a in attrs:
                setattr(m, a, saved_mod[m][a])

    return conn, env, restore


def _insert_work(conn, work_id, title, historical=0, reading_status="a_completed"):
    conn.execute(
        "INSERT OR REPLACE INTO works (work_id,title,normalized_title,authors,doi,arxiv_id,journal,status,"
        "first_seen,latest_seen,version_changed,queued,consumed_history,metadata_json,"
        "historical_discovery,reading_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (work_id, title, re.sub(r"\s+", " ", title.lower()), "T A; T B", f"10.5000/{work_id[-4:]}",
         f"26{work_id[-6:-2]}.{work_id[-2:]}", "arXiv", "arxiv", "2026-08-08", "2026-08-08",
         0, 0, 0, "{}", historical, reading_status))
    conn.execute(
        "INSERT INTO screenings (work_id,grade,reason,matched_profile,confidence,screened_at,source) "
        "VALUES (?,?,?,?,?,?,?)",
        (work_id, "MUST", "BPVE/NLO 新理论框架直接支撑 P4", "P2-bpve,P4-theory", "高",
         "2026-08-08", "arxiv"))
    conn.execute(
        "INSERT INTO work_sources (work_id,source,version,title,abstract,date) VALUES (?,?,?,?,?,?)",
        (work_id, "arxiv" if not historical else "arxiv_replay", "", title, "abstract shift current", "2026-08-08"))
    conn.commit()


def _complete_a(conn, work_id, title, papers, package_root, with_si=False):
    """在 papers/ 写 A 工作副本 + PDF(+SI)，processing 标记 completed，并发布到 _文献包。"""
    from la_workflow import publish_completed_to_package
    pdir = os.path.join(papers, work_id.replace(":", "_"))
    os.makedirs(pdir, exist_ok=True)
    pdf = os.path.join(pdir, "paper.pdf")
    _fake_pdf(pdf)
    si = ""
    if with_si:
        si = os.path.join(pdir, "supplement.pdf")
        _fake_pdf(si)
    a_path = os.path.join(pdir, "A_文献快速建档.md")
    with open(a_path, "w", encoding="utf-8") as f:
        f.write(_a_doc(title))
    conn.execute(
        "INSERT INTO processing (work_id,run_id,pdf_status,pdf_path,si_status,si_path,a_status,a_path,notes,processed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (work_id, "acc-run", "ok", pdf, "ok" if si else "none_found", si, "completed", a_path,
         "acceptance", "2026-08-08 10:00:00"))
    conn.commit()
    return publish_completed_to_package(conn, work_id, a_path)


def _set_user(path, work_id, user_text):
    import la_overview as lov
    text = open(path, encoding="utf-8").read()
    pat = re.compile(
        r"(<!--\s*LA:WORK_ID=" + re.escape(work_id) + r"\s*-->\s*" + re.escape(lov.BLOCK_BEGIN) + r".*?)"
        + re.escape(lov.USER_BEGIN) + r"(.*?)" + re.escape(lov.USER_END), re.S)
    text, n = pat.subn(lambda m: m.group(1) + lov.USER_BEGIN + "\n" + user_text + "\n" + lov.USER_END, text)
    assert n == 1, f"block not found: {work_id}"
    open(path, "w", encoding="utf-8").write(text)


def _user(d=None, note="", deep_read=False, chatgpt_a=False, competitor=False, zotero=False, fb=""):
    import la_overview as _lov
    def cb(label, on):
        return f"- [x] {label}" if on else f"- [ ] {label}"
    def sel(label):
        key = _lov.DISPOSITION_CHECKS[label]
        if isinstance(d, (list, tuple)):
            return key in d
        return key == d
    dlines = "\n".join([cb(x, sel(x)) for x in ("要读", "稍后", "拒绝")])
    flines = "\n".join([cb("ChatGPT 深读", deep_read), cb("ChatGPT 做 A", chatgpt_a),
                        cb("竞品", competitor), cb("存 Zotero", zotero)])
    return (f"#### 我的决定\n\n{dlines}\n\n#### 附加标记\n\n{flines}\n\n"
            f"#### 我的批注\n\n{note or '（在这里写你的想法；留空也可以）'}\n\n"
            f"#### 方向反馈\n\n{fb or '（可选。只有明确写“方向反馈：……”才会被收集为长期偏好候选）'}")


# ---------------- golden path ----------------

def golden_path():
    tmp = tempfile.mkdtemp(prefix="acc_golden_")
    try:
        conn, env, restore = make_env(tmp)
        import la_overview as lov
        import la_workflow as lwf
        TITLE = "Golden Path Shift Current in Sliding Bilayers"
        WID = "10.5000/gold01"
        _insert_work(conn, WID, TITLE)
        # ingest 同一篇（模拟重复来源）→ dedup noop
        rec = {"title": TITLE, "authors": "T A; T B", "doi": f"10.5000/gold01", "arxiv_id": "2610.5501",
               "abstract": "shift current in sliding bilayers", "abstract_truncated": False,
               "date": "2026-08-09", "url": "", "journal": "arXiv", "status": "arxiv", "version": "v1"}
        import literature_agent as la
        wid2, action, _ = la.ingest_record(conn, rec, "arxiv", "2026-08-09")
        assert wid2 == WID, (wid2, WID)
        # 已完成 A（canonical → _文献包）
        pkg = _complete_a(conn, WID, TITLE, env["LITAGENT_PAPERS_DIR"], lwf.PACKAGE_DIR, with_si=True)
        assert pkg["stage"] == "package", pkg
        assert os.path.exists(os.path.join(lwf.PACKAGE_DIR, WID, "A.md"))
        assert os.path.exists(os.path.join(lwf.PACKAGE_DIR, WID, "paper.pdf"))
        # overview → link 有效
        ov = lov.run_overview(conn, "2026-08-09")
        ok, missing = lov.verify_overview_links(ov["path"])
        assert ok, missing
        text = open(ov["path"], encoding="utf-8").read()
        assert f"![[Literatures/每日文献/_文献包/{WID}/A.md]]" in text
        # 用户勾选：要读 + ChatGPT 深读 + 竞品
        _set_user(ov["path"], WID, _user(d="read", deep_read=True, competitor=True,
                                         note="golden path 精读"))
        res = lov.run_sync_overview(conn, "2026-08-09")
        assert WID in res["applied_works"], res
        assert conn.execute("SELECT reading_status FROM works WHERE work_id=?", (WID,)).fetchone()[0] == "processing"
        from la_workflow import current_flags
        flags = current_flags(conn, WID)
        assert flags["competitor"] is True, flags
        # 笔记在 1_处理中
        note = lwf._vault_note_for_work(conn.execute("SELECT * FROM works WHERE work_id=?", (WID,)).fetchone())
        assert note["stage"] == "processing", note
        # ChatGPT handoff：paper.pdf / supplement.pdf / A.md / CONTEXT.md
        hdir = os.path.join(lov.handoff_dir(), "2026-08-09", lov._safe_short_title(TITLE))
        for f in ("paper.pdf", "supplement.pdf", "A.md", "CONTEXT.md"):
            assert os.path.exists(os.path.join(hdir, f)), f
        ctx = open(os.path.join(hdir, "CONTEXT.md"), encoding="utf-8").read()
        assert f"work_id：{WID}" in ctx and "deep_read" in ctx
        # overview rerun：批注保留 + A 链接重定位到 1_处理中
        ov2 = lov.run_overview(conn, "2026-08-09")
        text2 = open(ov2["path"], encoding="utf-8").read()
        assert "golden path 精读" in text2 and "[x] 要读" in text2
        assert f"![[Literatures/1_处理中/{TITLE}.md]]" in text2
        # PROJECT_STATE 一致性（临时 state 由 handoff 生成）
        from la_handoff import run_handoff
        run_handoff(conn)
        st = json.load(open(os.path.join(env["LITAGENT_HANDOFF_DIR"], "PROJECT_STATE.json"), encoding="utf-8"))
        assert st["workflow_schema_version"] == 4
        assert st["vault_notes"]["package"] == 0 and st["vault_notes"]["processing"] == 1
        return True, "golden path full PASS"
    except Exception as e:
        return False, repr(e)
    finally:
        restore()
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- 用户路径 ----------------

def user_paths():
    tmp = tempfile.mkdtemp(prefix="acc_user_")
    try:
        conn, env, restore = make_env(tmp)
        import la_overview as lov
        import la_workflow as lwf
        cases = [("later", "later"), ("ignore", "ignore"), ("noop", None), ("ca", "read")]
        for k, _ in cases:
            _insert_work(conn, f"10.5000/{k}01", f"User Path {k} Paper")
            _complete_a(conn, f"10.5000/{k}01", f"User Path {k} Paper", env["LITAGENT_PAPERS_DIR"], lwf.PACKAGE_DIR)
        ov = lov.run_overview(conn, "2026-08-09")
        _set_user(ov["path"], "10.5000/later01", _user(d="later"))
        _set_user(ov["path"], "10.5000/ignore01", _user(d="ignore"))
        # noop：不勾选
        _set_user(ov["path"], "10.5000/noop01", _user())
        # chatgpt_a：ChatGPT 做 A（不生成正式 A）
        _set_user(ov["path"], "10.5000/ca01", _user(d="read", chatgpt_a=True))
        res = lov.run_sync_overview(conn, "2026-08-09")
        assert "10.5000/later01" in res["applied_works"]
        assert "10.5000/ca01" in res["applied_works"]
        rs = {k: conn.execute("SELECT reading_status FROM works WHERE work_id=?", (f"10.5000/{k}01",)).fetchone()[0]
              for k in ("later", "ignore", "noop", "ca")}
        assert rs["later"] == "deferred", rs
        assert rs["ignore"] == "ignored", rs
        assert rs["noop"] == "a_completed", rs
        assert rs["ca"] == "processing", rs
        # later → 0_待处理；ignore → 00-已阅；noop → 留在 _文献包；ca → 1_处理中
        def stage(k):
            w = conn.execute("SELECT * FROM works WHERE work_id=?", (f"10.5000/{k}01",)).fetchone()
            return lwf._vault_note_for_work(w)["stage"]
        assert stage("later") == "inbox"
        assert stage("ignore") == "read"
        assert stage("noop") == "package"
        assert stage("ca") == "processing"
        # chatgpt_a：有 A_REQUEST.md，没有 A.md；不误标 a_completed
        hdir = os.path.join(lov.handoff_dir(), "2026-08-09", lov._safe_short_title("User Path ca Paper"))
        assert os.path.exists(os.path.join(hdir, "A_REQUEST.md"))
        assert os.path.exists(os.path.join(hdir, "paper.pdf"))
        assert not os.path.exists(os.path.join(hdir, "A.md"))
        # later 论文从 _文献包 移走
        assert not os.path.exists(os.path.join(lwf.PACKAGE_DIR, "10.5000/later01", "A.md"))
        return True, "user paths PASS (later/ignore/noop/chatgpt_a)"
    except Exception as e:
        return False, repr(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- failure / recovery ----------------

def failure_tests():
    tmp = tempfile.mkdtemp(prefix="acc_fail_")
    passed, total = 0, 0
    fails = []
    try:
        conn, env, restore = make_env(tmp)
        import la_overview as lov
        import la_workflow as lwf

        def case(name, fn):
            nonlocal passed, total
            total += 1
            try:
                fn()
                passed += 1
            except Exception as e:
                fails.append(f"{name}: {e!r}")

        # 1) 网络失败：run_collect mock fetch 抛异常 → 不崩溃、记录失败
        def t1():
            import literature_agent as la
            orig = la.fetch
            def boom(url, timeout=60, binary=False):
                raise OSError("network down")
            la.fetch = boom
            try:
                r = la.run_collect(conn=conn, today="2026-08-09", log=lambda *a: None, run_id="acc-netfail")
            finally:
                la.fetch = orig
            assert len(r["failed_sources"]) > 0, r
        case("source_network_failure", t1)

        # 2) PDF 403 / HTML challenge：不生成 A，不伪造完成
        def t2():
            import literature_agent as la
            orig = la.fetch
            def html_challenge(url, timeout=60, binary=False):
                return b"<html><body>403 Forbidden challenge</body></html>"
            la.fetch = html_challenge
            try:
                r = la.run_process(conn=conn, run_id="acc-html", today="2026-08-09",
                                   log=lambda *a: None, force_work="10.5000/html01")
            finally:
                la.fetch = orig
            procs = conn.execute("SELECT pdf_status, a_status FROM processing WHERE work_id='10.5000/html01'").fetchall()
            assert procs and all(p["pdf_status"] != "ok" for p in procs), procs
            assert conn.execute("SELECT COUNT(*) FROM processing WHERE work_id='10.5000/html01' AND a_status='completed'").fetchone()[0] == 0
        _insert_work(conn, "10.5000/html01", "HTML Challenge Paper")
        case("pdf_403_html_challenge", t2)

        # 3) invalid PDF：文件存在但非 %PDF → blocked，不完成
        def t3():
            p = os.path.join(env["LITAGENT_PAPERS_DIR"], "bad", "paper.pdf")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(b"<html>not a pdf</html>" * 3000)
            conn.execute("INSERT INTO processing (work_id,run_id,pdf_status,pdf_path,si_status,si_path,a_status,a_path,notes,processed_at) "
                         "VALUES ('10.5000/bad01','r','ok',?, 'none_found','','queued','','',?)",
                         (p, "2026-08-08"))
            conn.commit()
            item = lwf.workflow_item(conn, "10.5000/bad01")
            assert item["pdf_status"] != "ok" and item["a_status"] == "blocked_pdf", item
        _insert_work(conn, "10.5000/bad01", "Invalid PDF Paper")
        case("invalid_pdf_blocked", t3)

        # 4) duplicate arXiv → accepted → published 同一 work 不重复
        def t4():
            import literature_agent as la
            rec1 = {"title": "Dup Paper arXiv", "authors": "A", "arxiv_id": "2609.7001",
                    "abstract": "x", "abstract_truncated": False, "date": "2026-08-09", "url": "",
                    "journal": "arXiv", "status": "arxiv", "version": "v1"}
            rec2 = {"title": "Dup Paper arXiv", "authors": "A", "doi": "10.5000/dup01",
                    "abstract": "x", "abstract_truncated": False, "date": "2026-08-09", "url": "",
                    "journal": "Physical Review Letters", "status": "journal", "version": ""}
            w1, a1, _ = la.ingest_record(conn, rec1, "arxiv", "2026-08-09")
            w2, a2, _ = la.ingest_record(conn, rec2, "aps_accepted", "2026-08-09")
            assert w1 == w2, (w1, w2)
            assert conn.execute("SELECT COUNT(*) FROM works WHERE work_id=?", (w1,)).fetchone()[0] == 1
        case("duplicate_arxiv_accepted_published", t4)

        # 5) overview 多 disposition 冲突
        def t5():
            _insert_work(conn, "10.5000/conf01", "Conflict Paper")
            _complete_a(conn, "10.5000/conf01", "Conflict Paper", env["LITAGENT_PAPERS_DIR"], lwf.PACKAGE_DIR)
            ov = lov.run_overview(conn, "2026-08-09")
            _set_user(ov["path"], "10.5000/conf01", _user(d=["read", "ignore"]))
            res = lov.run_sync_overview(conn, "2026-08-09")
            assert res["conflicts"] >= 1, res
            assert conn.execute("SELECT reading_status FROM works WHERE work_id='10.5000/conf01'").fetchone()[0] == "a_completed"
        case("overview_disposition_conflict", t5)

        # 6) rerun overview 不覆盖批注
        def t6():
            _insert_work(conn, "10.5000/rerun01", "Rerun Paper")
            _complete_a(conn, "10.5000/rerun01", "Rerun Paper", env["LITAGENT_PAPERS_DIR"], lwf.PACKAGE_DIR)
            ov = lov.run_overview(conn, "2026-08-09")
            _set_user(ov["path"], "10.5000/rerun01", _user(note="rerun 保留批注测试"))
            ov2 = lov.run_overview(conn, "2026-08-09")
            assert "rerun 保留批注测试" in open(ov2["path"], encoding="utf-8").read()
        case("rerun_overview_preserves_annotation", t6)

        # 7) rerun daily 不重复处理（独立临时环境，空源 mock）
        def t7():
            import literature_agent as la
            tmp7 = tempfile.mkdtemp(prefix="acc_daily_")
            try:
                c7, env7, restore7 = make_env(tmp7)
                c7.close()
                orig_fetch = la.fetch
                la.fetch = lambda url, timeout=60, binary=False: b"<feed/>"
                try:
                    s1 = la.run_daily(today="2026-08-09", run_id="acc-daily-1")
                    s2 = la.run_daily(today="2026-08-09", run_id="acc-daily-2")
                finally:
                    la.fetch = orig_fetch
                import sqlite3
                c2 = sqlite3.connect(env7["LITAGENT_DB"])
                n_screen = c2.execute("SELECT COUNT(*) FROM screenings").fetchone()[0]
                n_runs = c2.execute("SELECT COUNT(*) FROM processing_runs").fetchone()[0]
                c2.close()
                assert s2["new_works"] == 0 and s2["processed"] == 0, s2
                assert n_runs == 2, n_runs
            finally:
                restore7()
                shutil.rmtree(tmp7, ignore_errors=True)
        case("rerun_daily_idempotent", t7)

        # 8) A 被移动后 overview 自动重新定位
        def t8():
            _insert_work(conn, "10.5000/move01", "Moved Paper")
            _complete_a(conn, "10.5000/move01", "Moved Paper", env["LITAGENT_PAPERS_DIR"], lwf.PACKAGE_DIR)
            ov = lov.run_overview(conn, "2026-08-09")
            _set_user(ov["path"], "10.5000/move01", _user(d="read"))
            lov.run_sync_overview(conn, "2026-08-09")
            ov2 = lov.run_overview(conn, "2026-08-09")
            text = open(ov2["path"], encoding="utf-8").read()
            assert f"![[Literatures/1_处理中/Moved Paper.md]]" in text
        case("a_moved_relocation", t8)

        # 9) iCloud 原子写：中断（陈旧 tmp）不影响 rerun，主文件永远完整
        def t9():
            import la_overview as _lov
            path = os.path.join(env["LITAGENT_OVERVIEW_DIR"], "2026-08-09 文献总览.md")
            _lov.run_overview(conn, "2026-08-09")
            stale = path + ".tmp-999999"
            open(stale, "w").write("stale")
            _lov.run_overview(conn, "2026-08-09")
            content = open(path, encoding="utf-8").read()
            assert content.startswith("# 2026-08-09 文献总览")
            assert "stale" not in content
            os.unlink(stale)
        case("atomic_write_recovery", t9)

        # 10) DB 事务中断可回滚
        def t10():
            before = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
            try:
                conn.execute("INSERT INTO works (work_id,title,normalized_title) VALUES ('x','y','z')")
                conn.rollback()
            except Exception:
                conn.rollback()
            after = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
            assert before == after
        case("db_transaction_rollback", t10)

        # 11) blocked PDF：无 URL → blocked_pdf，不伪造 A
        def t11():
            _insert_work(conn, "10.5000/block01", "Blocked PDF Paper")
            conn.execute("INSERT INTO processing (work_id,run_id,pdf_status,pdf_path,si_status,si_path,a_status,a_path,notes,processed_at) "
                         "VALUES ('10.5000/block01','r','no_url','','none_found','','blocked_pdf','','',?)",
                         ("2026-08-08",))
            conn.commit()
            item = lwf.workflow_item(conn, "10.5000/block01")
            assert item["a_status"] == "blocked_pdf"
            ov = lov.run_overview(conn, "2026-08-09")
            text = open(ov["path"], encoding="utf-8").read()
            assert "Blocked PDF Paper" not in text
        case("blocked_pdf_no_fake_a", t11)

        # 12) historical recovery 不进入今日 live
        def t12():
            _insert_work(conn, "10.5000/hist01", "Historical Replay Paper", historical=1)
            _complete_a(conn, "10.5000/hist01", "Historical Replay Paper", env["LITAGENT_PAPERS_DIR"], lwf.PACKAGE_DIR)
            ov = lov.run_overview(conn, "2026-08-09")
            text = open(ov["path"], encoding="utf-8").read()
            assert "Historical Replay Paper" in text
            i_live = text.find("## 今日新文献")
            i_hist = text.find("## 今日历史补回")
            assert i_live < i_hist < text.find("Historical Replay Paper")
        case("historical_not_in_today_live", t12)

        # 13) ChatGPT handoff 重复生成幂等
        def t13():
            _insert_work(conn, "10.5000/hand01", "Handoff Idempotent Paper")
            _complete_a(conn, "10.5000/hand01", "Handoff Idempotent Paper", env["LITAGENT_PAPERS_DIR"], lwf.PACKAGE_DIR)
            ov = lov.run_overview(conn, "2026-08-09")
            _set_user(ov["path"], "10.5000/hand01", _user(d="read", deep_read=True))
            lov.run_sync_overview(conn, "2026-08-09")
            n1 = len(glob_files(os.path.join(lov.handoff_dir(), "2026-08-09", "*", "paper.pdf")))
            fb1 = conn.execute("SELECT COUNT(*) FROM feedback WHERE work_id='10.5000/hand01'").fetchone()[0]
            lov.run_sync_overview(conn, "2026-08-09")  # 重复 sync
            n2 = len(glob_files(os.path.join(lov.handoff_dir(), "2026-08-09", "*", "paper.pdf")))
            fb2 = conn.execute("SELECT COUNT(*) FROM feedback WHERE work_id='10.5000/hand01'").fetchone()[0]
            assert n1 == n2 and fb1 == fb2, (n1, n2, fb1, fb2)
        case("chatgpt_handoff_idempotent", t13)

        # 14) 已发布 A 但 vault 笔记不可读（iCloud dataless 占位）：reconcile 不崩溃、不覆盖
        def t14():
            import stat as _stat
            _insert_work(conn, "10.5000/place01", "Placeholder Note Paper")
            _complete_a(conn, "10.5000/place01", "Placeholder Note Paper",
                        env["LITAGENT_PAPERS_DIR"], lwf.PACKAGE_DIR)
            pkg_a = os.path.join(lwf.PACKAGE_DIR, "10.5000/place01", "A.md")
            assert os.path.exists(pkg_a)
            os.chmod(pkg_a, 0)  # 模拟不可读（与 dataless 占位同类的 OSError 路径）
            try:
                w = conn.execute("SELECT * FROM works WHERE work_id='10.5000/place01'").fetchone()
                note = lwf._vault_note_for_work(w)
                assert note and note["stage"] == "package", note
                r = lwf.publish_completed_to_package(conn, "10.5000/place01")
                assert r["existing"] is True and r["published"] is False, r
                assert os.path.exists(pkg_a)
            finally:
                os.chmod(pkg_a, 0o644)
        case("unreadable_package_note_no_crash", t14)

    finally:
        restore()
        shutil.rmtree(tmp, ignore_errors=True)
    return {"ok": passed == total, "passed": passed, "total": total, "fails": fails}


def glob_files(pat):
    import glob
    return glob.glob(pat)


def run_acceptance_suite():
    golden = {"ok": False, "detail": ""}
    try:
        ok, detail = golden_path()
        golden = {"ok": ok, "detail": detail}
    except Exception as e:
        golden = {"ok": False, "detail": repr(e)}
    user = {"ok": False, "detail": ""}
    try:
        ok, detail = user_paths()
        user = {"ok": ok, "detail": detail}
    except Exception as e:
        user = {"ok": False, "detail": repr(e)}
    failure = failure_tests()
    print("acceptance golden:", golden["ok"], golden["detail"])
    print("acceptance user paths:", user["ok"], user["detail"])
    print("acceptance failure:", failure["ok"], f"{failure['passed']}/{failure['total']}", failure["fails"][:5])
    return {"golden": golden, "user_paths": user, "failure": failure}


if __name__ == "__main__":
    import json as _j
    print(_j.dumps(run_acceptance_suite(), ensure_ascii=False, indent=1))
