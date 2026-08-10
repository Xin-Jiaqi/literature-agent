#!/usr/bin/env python3
"""la_overview synthetic 测试：临时 vault/DB/目录，不碰真实 Obsidian。

场景（对应需求第十八节）：
- A：要读 + ChatGPT 深读 + 竞品 + 存 Zotero
- B：稍后
- C：拒绝
- D：要读 + 拒绝（冲突，不执行）
- E：什么都不选（但写方向反馈 → 只进 pending 候选）

额外验证：rerun 不覆盖批注、移动后 A 链接重新定位、链接全部有效、
无日期子层、blocked PDF 不进正文、原 selftest 不受影响。
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

DATE = "2026-08-09"
TITLES = {
    "a": "Loop Current Chirality in Twisted Bilayer Graphene",
    "b": "Second Harmonic Generation in Sliding Ferroelectrics",
    "c": "Shift Current in Monolayer Transition Metal Dichalcogenides",
    "d": "Photocurrent in Altermagnetic Heterostructures",
    "e": "Quantum Geometry and Rectification Sum Rules",
    "g": "Historical Recovery Example Paper",
}
WIDS = {k: f"10.9999/{k}01" for k in TITLES}

USER_A = """#### 我的决定

- [x] 要读
- [ ] 稍后
- [ ] 拒绝

#### 附加标记

- [x] ChatGPT 深读
- [ ] ChatGPT 做 A
- [x] 竞品
- [x] 存 Zotero

#### 我的批注

这篇和我的 P4 方法直接相关，值得精读。

#### 方向反馈

（留空）
"""
USER_B = """#### 我的决定

- [ ] 要读
- [x] 稍后
- [ ] 拒绝

#### 附加标记

- [ ] ChatGPT 深读
- [ ] ChatGPT 做 A
- [ ] 竞品
- [ ] 存 Zotero

#### 我的批注

（在这里写你的想法；留空也可以）

#### 方向反馈

（可选。只有明确写“方向反馈：……”才会被收集为长期偏好候选）
"""
USER_C = """#### 我的决定

- [ ] 要读
- [ ] 稍后
- [x] 拒绝

#### 附加标记

- [ ] ChatGPT 深读
- [ ] ChatGPT 做 A
- [ ] 竞品
- [ ] 存 Zotero

#### 我的批注

（在这里写你的想法；留空也可以）

#### 方向反馈

（可选。只有明确写“方向反馈：……”才会被收集为长期偏好候选）
"""
USER_D = """#### 我的决定

- [x] 要读
- [ ] 稍后
- [x] 拒绝

#### 附加标记

- [ ] ChatGPT 深读
- [ ] ChatGPT 做 A
- [ ] 竞品
- [ ] 存 Zotero

#### 我的批注

（在这里写你的想法；留空也可以）

#### 方向反馈

（可选。只有明确写“方向反馈：……”才会被收集为长期偏好候选）
"""
USER_E = """#### 我的决定

- [ ] 要读
- [ ] 稍后
- [ ] 拒绝

#### 附加标记

- [ ] ChatGPT 深读
- [ ] ChatGPT 做 A
- [ ] 竞品
- [ ] 存 Zotero

#### 我的批注

（在这里写你的想法；留空也可以）

#### 方向反馈

方向反馈：以后降低纯器件型二维光电探测文章优先级。
"""
USER_G = None  # 不批注


def _a_doc(title):
    body = ("这是一段用于测试的 A 建档内容。" * 20)
    return f"""# 文献快速建档：{title}

## 0. 文献信息

- 标题：{title}
- 作者：Test Author
- 期刊或预印本平台、年份：arXiv 预印本，2026-08-01 v1
- DOI 或 arXiv 编号：测试
- 本次阅读依据：正文 PDF；SI 未获取。

## 1. 一句话总结与研究对象

{body}

## 2. 旧问题与新贡献

{body}

## 3. 核心证据与关键位置

{body}

## 4. 对用户的潜在价值与引用方式

{body}

## 5. 阅读优先级与建议去向

{body}
"""


def _fake_pdf(path, min_bytes=12000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n" + b"0" * (min_bytes - 8))


def _set_user_region(path, work_id, user_text):
    import la_overview as lov
    text = open(path, encoding="utf-8").read()
    pat = re.compile(
        r"(<!--\s*LA:WORK_ID=" + re.escape(work_id) + r"\s*-->\s*" + re.escape(lov.BLOCK_BEGIN) + r".*?)"
        + re.escape(lov.USER_BEGIN) + r"(.*?)" + re.escape(lov.USER_END), re.S)
    repl = lambda m: m.group(1) + lov.USER_BEGIN + "\n" + user_text + "\n" + lov.USER_END
    text, n = pat.subn(repl, text)
    assert n == 1, f"user region not found for {work_id}"
    open(path, "w", encoding="utf-8").write(text)


def _setup(tmp):
    import la_overview as lov
    import la_workflow as lwf

    vault = os.path.join(tmp, "vault")
    os.makedirs(os.path.join(vault, ".obsidian"))
    vault_lit = os.path.join(vault, "Literatures")
    package_root = os.path.join(vault_lit, "每日文献", "_文献包")
    stages = {
        "package": package_root,
        "inbox": os.path.join(vault_lit, "0_待处理"),
        "processing": os.path.join(vault_lit, "1_处理中"),
        "callable": os.path.join(vault_lit, "2-可调用"),
        "read": os.path.join(vault_lit, "00-已阅"),
    }
    for d in stages.values():
        os.makedirs(d, exist_ok=True)
    papers = os.path.join(tmp, "papers")
    os.makedirs(papers, exist_ok=True)

    os.environ["LITAGENT_DB"] = os.path.join(tmp, "t.db")
    os.environ["LITAGENT_VAULT_ROOT"] = vault_lit
    os.environ["LITAGENT_PAPERS_DIR"] = papers
    os.environ["LITAGENT_ZOTERO_QUEUE"] = os.path.join(tmp, "zotero_queue.jsonl")
    os.environ["LITAGENT_FEEDBACK"] = os.path.join(tmp, "feedback.jsonl")
    os.environ["LITAGENT_A_QUEUE"] = os.path.join(tmp, "a_queue.jsonl")

    # 覆盖模块级路径（模块可能已被真实环境导入）
    lwf.VAULT_ROOT = vault_lit
    lwf.PAPERS_DIR = papers
    lwf.VAULT_STAGES = dict(stages)
    lwf.PACKAGE_DIR = package_root
    lwf.A_QUEUE_PATH = os.path.join(tmp, "a_queue.jsonl")
    lov.OVERVIEW_DIR = os.path.join(vault_lit, "每日文献")
    lov.HANDOFF_DIR = os.path.join(lov.OVERVIEW_DIR, "_ChatGPT交接")
    lov.DIRECTION_FEEDBACK_PATH = os.path.join(tmp, "direction_feedback_candidates.md")
    import la_zotero
    la_zotero.QUEUE_PATH = os.environ["LITAGENT_ZOTERO_QUEUE"]

    from la_db2 import connect
    conn = connect()
    hist = {"a": 0, "b": 0, "c": 0, "d": 0, "e": 0, "g": 1}
    for k, title in TITLES.items():
        wid = WIDS[k]
        conn.execute(
            "INSERT INTO works (work_id,title,normalized_title,authors,doi,arxiv_id,journal,status,"
            "first_seen,latest_seen,version_changed,queued,consumed_history,metadata_json,"
            "historical_discovery,reading_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (wid, title, re.sub(r"\s+", " ", title.lower()), "Test A; Test B", f"10.9999/{k}01",
             f"2608.{k}01", "arXiv", "arxiv", "2026-08-08", "2026-08-08", 0, 0, 0, "{}",
             hist[k], "a_completed"))
        conn.execute(
            "INSERT INTO screenings (work_id,grade,reason,matched_profile,confidence,screened_at,source) "
            "VALUES (?,?,?,?,?,?,?)",
            (wid, "MUST", "BPVE/NLO 新理论框架直接支撑 P4；新框架/判据/可迁移方法信号明确",
             "P2-bpve,P4-theory", "高", "2026-08-08", "test"))
        conn.execute(
            "INSERT INTO work_sources (work_id,source,version,title,abstract,date) VALUES (?,?,?,?,?,?)",
            (wid, "arxiv" if hist[k] == 0 else "arxiv_replay", "", title, "test abstract", "2026-08-08"))
        pdir = os.path.join(papers, wid)
        os.makedirs(pdir, exist_ok=True)
        pdf = os.path.join(pdir, "paper.pdf")
        _fake_pdf(pdf)
        a_path = os.path.join(pdir, "A_文献快速建档.md")
        with open(a_path, "w", encoding="utf-8") as f:
            f.write(_a_doc(title))
        si = ""
        if k == "a":
            si = os.path.join(pdir, "supplement.pdf")
            _fake_pdf(si)
        conn.execute(
            "INSERT INTO processing (work_id,run_id,pdf_status,pdf_path,si_status,si_path,a_status,a_path,notes,processed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (wid, f"run-{k}", "ok", pdf, "ok" if si else "none_found", si, "completed", a_path,
             "test", "2026-08-08 10:00:00"))
        target = os.path.join(package_root, wid, "A.md")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(a_path, target)
        shutil.copyfile(pdf, os.path.join(package_root, wid, "paper.pdf"))
        if si:
            shutil.copyfile(si, os.path.join(package_root, wid, "supplement.pdf"))
        conn.execute(
            "INSERT INTO artifacts (work_id,kind,path,status,detail,created_at) VALUES (?,?,?,?,?,?)",
            (wid, "a_vault_note", target, "ok", "test", "2026-08-08"))
    # blocked PDF 论文（不完成 A，不进正文）
    conn.execute(
        "INSERT INTO works (work_id,title,normalized_title,authors,doi,arxiv_id,journal,status,"
        "first_seen,latest_seen,version_changed,queued,consumed_history,metadata_json,"
        "historical_discovery,reading_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("10.9999/blocked", "Blocked Paper Without PDF", "blocked paper without pdf", "T A", "",
         "", "JPCLett", "journal", "2026-08-08", "2026-08-08", 0, 0, 0, "{}", 1, "a_queued"))
    conn.execute(
        "INSERT INTO processing (work_id,run_id,pdf_status,pdf_path,si_status,si_path,a_status,a_path,notes,processed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("10.9999/blocked", "run-blocked", "no_url", "", "none_found", "", "blocked_pdf",
         "", "no pdf", "2026-08-08 11:00:00"))
    conn.commit()
    return conn


def run_all():
    tmp = tempfile.mkdtemp(prefix="la_overview_test_")
    ok = True
    try:
        conn = _setup(tmp)
        import la_overview as lov

        # 1. 生成总览：直接位于 每日文献/ 根目录，无日期子层
        res = lov.run_overview(conn, DATE)
        path = res["path"]
        assert os.path.isdir(os.path.join(lov.OVERVIEW_DIR, DATE)) is False, "日期子层不该存在"
        assert os.path.basename(os.path.dirname(path)) == "每日文献", "总览不在 每日文献/ 根目录"
        text = open(path, encoding="utf-8").read()
        assert "今日新文献：5" in text, text[:400]
        assert "历史补回：1" in text
        assert "Blocked Paper Without PDF" not in text, "blocked PDF 不应进入正文"
        assert "另有 1 篇重要论文全文暂未获取" in text, "blocked 提示应存在"
        assert "## 今日新文献" in text and "## 历史补回" in text
        assert f"![[Literatures/每日文献/_文献包/{WIDS['a']}/A.md]]" in text, "初始 A 链接应指向 _文献包"
        assert f"[[Literatures/每日文献/_文献包/{WIDS['a']}/paper.pdf]]" in text, "PDF 链接应指向 _文献包 副本"

        # 2. 模拟用户批注
        users = {"a": USER_A, "b": USER_B, "c": USER_C, "d": USER_D, "e": USER_E}
        for k, u in users.items():
            _set_user_region(path, WIDS[k], u)

        # 3. 同步
        from la_workflow import current_flags
        summary = lov.run_sync_overview(conn, DATE)
        assert summary["conflicts"] == 1 and summary["conflict_works"] == [WIDS["d"]], summary

        def status(wid):
            return conn.execute("SELECT reading_status FROM works WHERE work_id=?", (wid,)).fetchone()[0]

        def note_stage(title, work_id=None):
            import la_workflow as lwf
            for stage, d in lwf.VAULT_STAGES.items():
                if stage == "package" and work_id:
                    if os.path.exists(os.path.join(d, work_id, "A.md")):
                        return stage
                elif os.path.exists(os.path.join(d, title + ".md")):
                    return stage
            return None

        # A：read + competitor + save_zotero
        assert status(WIDS["a"]) == "processing"
        assert note_stage(TITLES["a"], WIDS["a"]) == "processing"
        flags = current_flags(conn, WIDS["a"])
        assert flags["competitor"] is True and flags["save_zotero"] is True, flags
        hdir = os.path.join(lov.HANDOFF_DIR, DATE, lov._safe_short_title(TITLES["a"]))
        assert os.path.exists(os.path.join(hdir, "paper.pdf"))
        assert os.path.exists(os.path.join(hdir, "supplement.pdf"))
        assert os.path.exists(os.path.join(hdir, "CONTEXT.md"))
        assert os.path.exists(os.path.join(hdir, "A.md"))
        ctx = open(os.path.join(hdir, "CONTEXT.md"), encoding="utf-8").read()
        assert f"work_id：{WIDS['a']}" in ctx and "deep_read" in ctx
        from la_zotero import load_queue
        z = {e["work_id"]: e["status"] for e in load_queue()}
        assert z.get(WIDS["a"]) == "accepted", z

        # B：later，从 _文献包 移入 0_待处理
        assert status(WIDS["b"]) == "deferred"
        assert note_stage(TITLES["b"], WIDS["b"]) == "inbox"
        import la_workflow as _lwf
        assert os.path.exists(os.path.join(_lwf.PACKAGE_DIR, WIDS["b"], "A.md")) is False

        # C：ignore，移动 00-已阅
        assert status(WIDS["c"]) == "ignored"
        assert note_stage(TITLES["c"], WIDS["c"]) == "read"

        # D：冲突，不执行（仍留在 _文献包）
        assert status(WIDS["d"]) == "a_completed"
        assert note_stage(TITLES["d"], WIDS["d"]) == "package"
        fb_d = conn.execute("SELECT COUNT(*) FROM feedback WHERE work_id=?", (WIDS["d"],)).fetchone()[0]
        assert fb_d == 0, "冲突论文不应写 feedback"

        # E：什么都不选（方向反馈只进候选，A 留在 _文献包）
        assert status(WIDS["e"]) == "a_completed"
        assert note_stage(TITLES["e"], WIDS["e"]) == "package"
        fb_e = conn.execute("SELECT COUNT(*) FROM feedback WHERE work_id=?", (WIDS["e"],)).fetchone()[0]
        assert fb_e == 0
        fb_text = open(lov.DIRECTION_FEEDBACK_PATH, encoding="utf-8").read()
        assert "以后降低纯器件型二维光电探测文章优先级" in fb_text
        assert "pending_confirmation" in fb_text
        assert "research_profile" not in fb_text
        assert not os.path.exists(os.path.join(tmp, "research_profile.md"))

        # 4. rerun 不覆盖批注 + A 链接重新定位到 1_处理中
        res2 = lov.run_overview(conn, DATE)
        text2 = open(res2["path"], encoding="utf-8").read()
        assert "[x] 要读" in text2 and "[x] ChatGPT 深读" in text2
        assert "这篇和我的 P4 方法直接相关，值得精读。" in text2
        assert "已决定：要读 → 已移入 1_处理中" in text2
        assert f"![[Literatures/1_处理中/{TITLES['a']}.md]]" in text2, "A 链接应重新定位"
        assert "已决定：拒绝 → 已移入 00-已阅" in text2
        # B 稍后 → 链接指向 0_待处理
        assert f"![[Literatures/0_待处理/{TITLES['b']}.md]]" in text2, "B 链接应指向 0_待处理"
        # E 未决定 → 链接仍指向 _文献包
        assert f"![[Literatures/每日文献/_文献包/{WIDS['e']}/A.md]]" in text2, "E 链接应留在 _文献包"
        # PDF 链接指向 _文献包 内的 vault 副本
        assert f"[[Literatures/每日文献/_文献包/{WIDS['e']}/paper.pdf]]" in text2

        # 5. 链接校验全部有效
        vok, missing = lov.verify_overview_links(res2["path"])
        assert vok, missing

        # 6. 幂等：再次 sync 不重复应用
        summary2 = lov.run_sync_overview(conn, DATE)
        assert summary2["applied"] == 0, summary2
        fb_count = conn.execute("SELECT COUNT(*) FROM feedback WHERE work_id=?", (WIDS["a"],)).fetchone()[0]
        assert fb_count == 2, fb_count  # competitor + save_zotero 各一次，不重复

        # 7. 历史论文也进总览且链接有效
        assert f"{TITLES['g']}" in text2

        # 8. 发布与迁移：自动完成 A 进 _文献包；0_待处理 旧笔记可迁移
        import la_workflow as lwf
        wid_g = WIDS["g"]
        pkg_a_g = os.path.join(lwf.PACKAGE_DIR, wid_g, "A.md")
        inbox_note_g = os.path.join(lwf.VAULT_STAGES["inbox"], TITLES["g"] + ".md")
        # 模拟旧状态：g 的 A 在 0_待处理
        shutil.copyfile(pkg_a_g, inbox_note_g)
        os.unlink(pkg_a_g)
        r = lwf.migrate_inbox_to_package(conn, work_ids=[wid_g])
        assert r["moved_count"] == 1 and os.path.exists(pkg_a_g), r
        assert not os.path.exists(inbox_note_g)
        # publish：A 从 papers 直接发布到 _文献包（不投 0_待处理）
        os.unlink(pkg_a_g)
        r2 = lwf.publish_completed_to_package(conn, wid_g)
        assert r2["stage"] == "package" and os.path.exists(pkg_a_g), r2
        assert not os.path.exists(inbox_note_g)
        # 0_待处理 只保留“稍后”语义（B 已移入），D/E 仍在 _文献包
        assert note_stage(TITLES["b"], WIDS["b"]) == "inbox"
        assert note_stage(TITLES["d"], WIDS["d"]) == "package" and note_stage(TITLES["e"], WIDS["e"]) == "package"

        print("la_overview synthetic test: PASS (A/B/C/D/E 全部符合预期)")
    except Exception as e:
        ok = False
        import traceback
        traceback.print_exc()
        print("la_overview synthetic test: FAIL:", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
