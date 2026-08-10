#!/usr/bin/env python3
"""多日总览“今日”语义 + sync 后立即刷新测试（临时 vault/DB，不碰真实 Obsidian）。

场景：
- Day 1：A/B 今日完成 → 今日新文献 2
- Day 1 用户 read B → sync 后总览立即把 B 移入“今日已处理”，链接指向 1_处理中
- Day 2：A 未决定、B 不重复出现、C 新完成 → 今日新文献=C、之前未决定=A、今日已处理≠B
- Day 2 用户 read A → 刷新后 A 进入“今日已处理”
- historical 论文同样进入“今日历史补回”，决定后不再重复
- rerun：0 duplicate WORK_ID block、USER 区域不丢
"""
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import la_overview as lov
import la_workflow as lwf


def _fake_pdf(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n" + b"0" * 12000)


def _a_doc(title):
    body = "这是一段用于多日总览测试的 A 建档内容。" * 20
    return (f"# 文献快速建档：{title}\n\n## 0. 文献信息\n\n- 标题：{title}\n"
            f"- 作者：T Author\n- 期刊或预印本平台、年份：arXiv，2026-08-01 v1\n"
            f"- DOI 或 arXiv 编号：测试\n- 本次阅读依据：正文 PDF。\n\n"
            f"## 1. 一句话总结与研究对象\n\n{body}\n\n## 2. 旧问题与新贡献\n\n{body}\n\n"
            f"## 3. 核心证据与关键位置\n\n{body}\n\n## 4. 对用户的潜在价值与引用方式\n\n{body}\n\n"
            f"## 5. 阅读优先级与建议去向\n\n{body}\n")


def _user(disposition=None, note=""):
    def cb(label, core, checked):
        return f"- [x] {label}" if checked else f"- [ ] {label}"
    lines = ["#### 我的决定", ""]
    lines.append(cb("要读", "read", disposition == "read"))
    lines.append(cb("稍后", "later", disposition == "later"))
    lines.append(cb("拒绝", "ignore", disposition == "ignore"))
    lines += ["", "#### 附加标记", "",
              "- [ ] ChatGPT 深读", "- [ ] ChatGPT 做 A", "- [ ] 竞品", "- [ ] 存 Zotero",
              "", "#### 我的批注", "", note or "（在这里写你的想法；留空也可以）", "",
              "#### 方向反馈", "", "（可选。只有明确写“方向反馈：……”才会被收集为长期偏好候选）"]
    return "\n".join(lines)


def _set_user_region(path, work_id, user_text):
    text = open(path, encoding="utf-8").read()
    pat = re.compile(
        r"(<!--\s*LA:WORK_ID=" + re.escape(work_id) + r"\s*-->\s*" + re.escape(lov.BLOCK_BEGIN) + r".*?)"
        + re.escape(lov.USER_BEGIN) + r"(.*?)" + re.escape(lov.USER_END), re.S)
    repl = lambda m: m.group(1) + lov.USER_BEGIN + "\n" + user_text + "\n" + lov.USER_END
    text, n = pat.subn(repl, text)
    assert n == 1, f"user region not found for {work_id}"
    open(path, "w", encoding="utf-8").write(text)


def _setup(tmp):
    from la_db2 import connect
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
    for k, v in {
        "LITAGENT_DB": os.path.join(tmp, "t.db"),
        "LITAGENT_VAULT_ROOT": vault_lit,
        "LITAGENT_PAPERS_DIR": papers,
        "LITAGENT_ZOTERO_QUEUE": os.path.join(tmp, "zotero_queue.jsonl"),
        "LITAGENT_FEEDBACK": os.path.join(tmp, "feedback.jsonl"),
        "LITAGENT_A_QUEUE": os.path.join(tmp, "a_queue.jsonl"),
        "LITAGENT_OVERVIEW_DIR": os.path.join(vault_lit, "每日文献"),
        "LITAGENT_HANDOFF_DIR": os.path.join(tmp, "state"),
        "LITAGENT_DIRECTION_FEEDBACK": os.path.join(tmp, "direction_feedback_candidates.md"),
    }.items():
        os.environ[k] = v
    lwf.VAULT_ROOT = vault_lit
    lwf.PAPERS_DIR = papers
    lwf.VAULT_STAGES = dict(stages)
    lwf.PACKAGE_DIR = package_root
    lov.OVERVIEW_DIR = os.path.join(vault_lit, "每日文献")
    lov.HANDOFF_DIR = os.path.join(lov.OVERVIEW_DIR, "_ChatGPT交接")
    lov.DIRECTION_FEEDBACK_PATH = os.path.join(tmp, "direction_feedback_candidates.md")
    os.makedirs(os.path.dirname(lov.HANDOFF_DIR), exist_ok=True)
    import la_zotero
    la_zotero.QUEUE_PATH = os.environ["LITAGENT_ZOTERO_QUEUE"]
    conn = connect()
    works = {
        "A": ("10.2000/a", "Paper Alpha", 0),
        "B": ("10.2000/b", "Paper Beta", 0),
    }
    for k, (wid, title, hist) in works.items():
        conn.execute(
            "INSERT INTO works (work_id,title,normalized_title,authors,doi,arxiv_id,journal,status,"
            "first_seen,latest_seen,version_changed,queued,consumed_history,metadata_json,"
            "historical_discovery,reading_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (wid, title, re.sub(r"\s+", " ", title.lower()), "T Author", f"10.2000/{k.lower()}",
             "", "arXiv", "arxiv", "2026-08-01", "2026-08-01", 0, 0, 0, "{}", hist, "a_completed"))
        conn.execute(
            "INSERT INTO screenings (work_id,grade,reason,matched_profile,confidence,screened_at,source) "
            "VALUES (?,?,?,?,?,?,?)",
            (wid, "MUST", "测试推荐原因", "P4-theory", "高", "2026-08-01", "test"))
        conn.execute(
            "INSERT INTO work_sources (work_id,source,version,title,abstract,date) VALUES (?,?,?,?,?,?)",
            (wid, "arxiv" if not hist else "arxiv_replay", "", title, "test abstract", "2026-08-01"))
        pdir = os.path.join(papers, wid)
        os.makedirs(pdir, exist_ok=True)
        pdf = os.path.join(pdir, "paper.pdf")
        _fake_pdf(pdf)
        a_path = os.path.join(pdir, "A_文献快速建档.md")
        with open(a_path, "w", encoding="utf-8") as f:
            f.write(_a_doc(title))
        conn.execute(
            "INSERT INTO processing (work_id,run_id,pdf_status,pdf_path,si_status,si_path,a_status,a_path,notes,processed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (wid, "run", "ok", pdf, "none_found", "", "completed", a_path, "test", "2026-08-01 10:00:00"))
        target = os.path.join(package_root, wid, "A.md")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(a_path, target)
        shutil.copyfile(pdf, os.path.join(package_root, wid, "paper.pdf"))
        conn.execute(
            "INSERT INTO artifacts (work_id,kind,path,status,detail,created_at) VALUES (?,?,?,?,?,?)",
            (wid, "a_vault_note", target, "ok", "test", "2026-08-01"))
    conn.commit()
    return conn


def _complete_new_work(conn, tmp, wid, title, hist):
    """模拟 a-complete：新论文完成 A 并发布到 _文献包。"""
    from la_db2 import connect as _c
    papers = os.environ["LITAGENT_PAPERS_DIR"]
    pdir = os.path.join(papers, wid)
    os.makedirs(pdir, exist_ok=True)
    pdf = os.path.join(pdir, "paper.pdf")
    _fake_pdf(pdf)
    a_path = os.path.join(pdir, "A_文献快速建档.md")
    with open(a_path, "w", encoding="utf-8") as f:
        f.write(_a_doc(title))
    conn.execute(
        "INSERT INTO works (work_id,title,normalized_title,authors,doi,arxiv_id,journal,status,"
        "first_seen,latest_seen,version_changed,queued,consumed_history,metadata_json,"
        "historical_discovery,reading_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (wid, title, re.sub(r"\s+", " ", title.lower()), "T Author", "",
         "2608.0001", "arXiv", "arxiv", "2026-08-02", "2026-08-02", 0, 0, 0, "{}", hist, "a_completed"))
    conn.execute(
        "INSERT INTO screenings (work_id,grade,reason,matched_profile,confidence,screened_at,source) "
        "VALUES (?,?,?,?,?,?,?)",
        (wid, "MUST", "测试推荐原因", "P4-theory", "高", "2026-08-02", "test"))
    conn.execute(
        "INSERT INTO work_sources (work_id,source,version,title,abstract,date) VALUES (?,?,?,?,?,?)",
        (wid, "arxiv" if not hist else "arxiv_replay", "", title, "test abstract", "2026-08-02"))
    conn.execute(
        "INSERT INTO processing (work_id,run_id,pdf_status,pdf_path,si_status,si_path,a_status,a_path,notes,processed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (wid, "run2", "ok", pdf, "none_found", "", "completed", a_path, "test", "2026-08-02 10:00:00"))
    target = os.path.join(lwf.PACKAGE_DIR, wid, "A.md")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(a_path, target)
    shutil.copyfile(pdf, os.path.join(lwf.PACKAGE_DIR, wid, "paper.pdf"))
    conn.execute(
        "INSERT INTO artifacts (work_id,kind,path,status,detail,created_at) VALUES (?,?,?,?,?,?)",
        (wid, "a_vault_note", target, "ok", "test", "2026-08-02"))
    conn.commit()


def _read(path):
    return open(path, encoding="utf-8").read()


def run_all():
    tmp = tempfile.mkdtemp(prefix="la_overview_days_")
    try:
        conn = _setup(tmp)
        # ---- Day 1：A/B 今日完成 ----
        r1 = lov.run_overview(conn, "2026-08-09")
        t1 = _read(r1["path"])
        assert "> 今日新文献：2" in t1, t1[:200]
        assert "> 今日历史补回：0" in t1 and "> 之前未决定：0" in t1 and "> 今日已处理：0" in t1
        assert "> 等待决定：2" in t1
        assert "## 今日新文献" in t1 and "## 今日历史补回" in t1 and "## 之前未决定" in t1
        assert "Paper Alpha" in t1 and "Paper Beta" in t1
        print("[PASS] Day1: A/B 今日新文献 2")

        # ---- Day 1：用户 read B → sync 后立即刷新 ----
        _set_user_region(r1["path"], "10.2000/b", _user("read", "B 的批注"))
        s1 = lov.run_sync_overview(conn, "2026-08-09")
        assert s1["applied"] == 1, s1
        status = conn.execute("SELECT reading_status FROM works WHERE work_id='10.2000/b'").fetchone()[0]
        assert status == "processing", status
        t1b = _read(r1["path"])
        assert "Paper Beta" in t1b
        i_done = t1b.find("## 今日已处理")
        assert i_done >= 0 and t1b.find("Paper Beta", i_done) > 0, "B 应出现在今日已处理"
        assert "B 的批注" in t1b, "批注保留"
        assert f"![[Literatures/1_处理中/Paper Beta.md]]" in t1b, "B 链接应立即指向 1_处理中"
        okv, missing = lov.verify_overview_links(r1["path"])
        assert okv, missing
        print("[PASS] Day1: read B 后 sync 立即刷新，链接指向 1_处理中")

        # ---- Day 2：A 未决定、B 不重复、C 新完成 ----
        _complete_new_work(conn, tmp, "10.2000/c", "Paper Gamma", 0)
        r2 = lov.run_overview(conn, "2026-08-10")
        t2 = _read(r2["path"])
        assert "> 今日新文献：1" in t2, t2[:300]
        assert "> 今日历史补回：0" in t2
        assert "> 之前未决定：1" in t2, "A 应进入之前未决定"
        assert "> 今日已处理：0" in t2, "B 不应在 Day2 已处理重复出现"
        assert "> 等待决定：2" in t2
        i_new = t2.find("## 今日新文献")
        i_prev = t2.find("## 之前未决定")
        assert i_new < t2.find("Paper Gamma") < i_prev, "C 应在今日新文献"
        assert t2.find("Paper Alpha", i_prev) > 0, "A 应在之前未决定"
        assert "Paper Beta" not in t2, "已处理论文不得在未来总览重复"
        print("[PASS] Day2: C 今日新文献、A 之前未决定、B 不重复")

        # ---- Day 2：用户 read A → 刷新后进入今日已处理 ----
        _set_user_region(r2["path"], "10.2000/a", _user("read", "A 的批注"))
        s2 = lov.run_sync_overview(conn, "2026-08-10")
        assert s2["applied"] == 1, s2
        t2b = _read(r2["path"])
        i_done2 = t2b.find("## 今日已处理")
        assert i_done2 > 0 and t2b.find("Paper Alpha", i_done2) > 0, "A 应进入今日已处理"
        assert f"![[Literatures/1_处理中/Paper Alpha.md]]" in t2b
        assert "A 的批注" in t2b
        okv, missing = lov.verify_overview_links(r2["path"])
        assert okv, missing
        print("[PASS] Day2: read A 后立即进入今日已处理，链接有效")

        # ---- historical：Day 2 新完成的 historical 论文 → 今日历史补回 ----
        _complete_new_work(conn, tmp, "10.2000/h", "Paper Historical", 1)
        r3 = lov.run_overview(conn, "2026-08-10")
        t3 = _read(r3["path"])
        assert "> 今日历史补回：1" in t3, t3[:300]
        i_hist = t3.find("## 今日历史补回")
        assert i_hist > 0 and t3.find("Paper Historical", i_hist) > 0
        print("[PASS] Day2: historical 论文进入今日历史补回")

        # ---- rerun：0 duplicate block、0 lost USER ----
        r4 = lov.run_overview(conn, "2026-08-10")
        t4 = _read(r4["path"])
        for wid in ("10.2000/a", "10.2000/c", "10.2000/h"):
            n = len(re.findall(r"<!--\s*LA:WORK_ID=" + re.escape(wid) + r"\s*-->", t4))
            assert n == 1, f"{wid} block 出现 {n} 次"
        assert "A 的批注" in t4 and "B 的批注" in _read(r1["path"])
        okv, missing = lov.verify_overview_links(r4["path"])
        assert okv, missing
        print("[PASS] rerun: 0 duplicate / 0 lost USER block / links valid")
        print("overview days test: ALL PASS")
        return True
    except Exception:
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
