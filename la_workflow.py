#!/usr/bin/env python3
"""把研究雷达接到用户的 A/B/C/D 阅读工作流。

边界：
- screenings.grade 的 MUST/WEEKLY/RADAR/DROP 是“推荐优先级”；
- works.reading_status 是“阅读阶段”；
- 本模块只准备/核验 A，不冒充 AI 精读；
- 只有用户显式执行 decide 才记录 read/later/ignore/competitor/save_zotero；
- 完成的 A 自动发布到 每日文献/_文献包/<work_id>/（A.md + paper.pdf），不投递 0_待处理；阶段移动只由用户显式决定触发。
"""
import glob
import json
import os
import re
import shutil

from la_dedup import normalize_title_key
from la_v2common import now_iso, read_vault_file
from literature_config import (VAULT_ROOT as _CFG_VAULT_ROOT, A_SKILL_PATH as _CFG_A_SKILL,
                               PAPERS_ROOT as _CFG_PAPERS_ROOT, QUEUES_ROOT as _CFG_QUEUES_ROOT,
                               PROJECT_ROOT as _CFG_PROJECT_ROOT)

BASE = os.path.dirname(os.path.abspath(__file__))
PAPERS_DIR = os.environ.get("LITAGENT_PAPERS_DIR", _CFG_PAPERS_ROOT)
A_QUEUE_PATH = os.environ.get("LITAGENT_A_QUEUE", os.path.join(_CFG_QUEUES_ROOT, "a_queue.jsonl"))
VAULT_ROOT = _CFG_VAULT_ROOT
A_SKILL_PATH = _CFG_A_SKILL
B_SKILL_PATH = os.path.join(VAULT_ROOT, "AI system", "B. 处理中对话总结 Skill.md")
C_SKILL_PATH = os.path.join(VAULT_ROOT, "AI system", "C. 最终文献笔记融合 Skill.md")
D_SKILL_PATH = os.path.join(VAULT_ROOT, "AI system", "D. 文献发表与汇报转化 Skill.md")

PACKAGE_DIR = os.environ.get(
    "LITAGENT_PACKAGE_DIR", os.path.join(VAULT_ROOT, "每日文献", "_文献包"))

# 阶段语义：
# - package：自动完成 A 的正式存放位置（每日文献/_文献包/<work_id>/A.md），等待用户决定；
# - inbox（0_待处理）：只表示用户明确选择“稍后”的论文；
# - processing（1_处理中）：用户明确 read；
# - read（00-已阅）：用户明确 ignore；
# - callable（2-可调用）：C 成熟且用户确认。
VAULT_STAGES = {
    "package": PACKAGE_DIR,
    "inbox": os.path.join(VAULT_ROOT, "0_待处理"),
    "processing": os.path.join(VAULT_ROOT, "1_处理中"),
    "callable": os.path.join(VAULT_ROOT, "2-可调用"),
    "read": os.path.join(VAULT_ROOT, "00-已阅"),
}

AUTOMATED_STAGES = {
    "", "discovered", "queued", "screened", "a_queued", "a_completed", "A_created",
}
USER_STAGES = {"processing", "callable", "read", "ignored", "deferred"}
DISPOSITIONS = ("read", "later", "ignore")  # 互斥：用户对一篇论文的最终去向
FLAGS = ("save_zotero", "competitor")  # 可叠加动作/标签，不改变去向
DECISIONS = DISPOSITIONS + FLAGS  # 兼容旧命令：decide 仍可传 flag
REQUIRED_A_SECTIONS = tuple(f"## {i}." for i in range(6))
PLACEHOLDER_RE = re.compile(r"待填写|状态：待完成|由 agent 精读正文后填写")


def valid_pdf_file(path, min_bytes=10000):
    try:
        if not path or os.path.getsize(path) < min_bytes:
            return False
        with open(path, "rb") as f:
            return f.read(32).lstrip().startswith(b"%PDF-")
    except OSError:
        return False


def a_document_status(path):
    if not path or not os.path.exists(path):
        return {"status": "missing", "path": path or "", "chars": 0, "reason": "file missing"}
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return {"status": "invalid", "path": path, "chars": 0, "reason": str(e)}
    missing = [h for h in REQUIRED_A_SECTIONS if h not in text]
    if missing:
        return {"status": "template", "path": path, "chars": len(text),
                "reason": "missing sections: " + ",".join(missing)}
    if PLACEHOLDER_RE.search(text):
        return {"status": "template", "path": path, "chars": len(text), "reason": "contains placeholders"}
    if len(text) < 1000:
        return {"status": "template", "path": path, "chars": len(text), "reason": "too short"}
    return {"status": "completed", "path": path, "chars": len(text), "reason": "validated A sections"}


def _safe_filename(title):
    name = re.sub(r"[/:\\\x00]", "-", (title or "untitled").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:180] or "untitled") + ".md"


def _note_title(path):
    head = (read_vault_file(path) or "")[:1600]
    patterns = (
        r"^#\s*文献快速建档[：:]\s*(.+)$",
        r"^-\s*标题[：:]\s*(.+)$",
        r"^title:\s*[\"']?(.+?)[\"']?\s*$",
        r"^#\s+(.+)$",
    )
    for pat in patterns:
        m = re.search(pat, head, re.M)
        if m:
            return m.group(1).strip()
    return ""


def _vault_note_for_work(work):
    title_key = normalize_title_key(work["title"])
    for stage, directory in VAULT_STAGES.items():
        pattern = os.path.join(directory, "**", "*.md") if stage == "package" else os.path.join(directory, "*.md")
        for path in glob.glob(pattern, recursive=(stage == "package")):
            if stage == "package" and _is_package_note_for_work(path, work["work_id"]):
                return {"path": path, "stage": stage}
            if normalize_title_key(_note_title(path)) == title_key:
                return {"path": path, "stage": stage}
            if normalize_title_key(os.path.splitext(os.path.basename(path))[0]) == title_key:
                return {"path": path, "stage": stage}
    return None


def _is_package_note_for_work(path, work_id):
    """_文献包/<安全目录名>/A.md 且目录名匹配该 work（兼容旧冒号目录名）。"""
    if os.path.basename(path) != "A.md":
        return False
    parent = os.path.normpath(os.path.dirname(path))
    expected = os.path.normpath(os.path.join(PACKAGE_DIR, package_work_dir(work_id)))
    legacy = os.path.normpath(os.path.join(PACKAGE_DIR, work_id))
    return parent in (expected, legacy)


def _record_vault_artifact(conn, work_id, path, detail):
    conn.execute(
        "UPDATE artifacts SET status='superseded' WHERE work_id=? AND kind='a_vault_note' AND status='ok'",
        (work_id,),
    )
    exists = conn.execute(
        "SELECT 1 FROM artifacts WHERE work_id=? AND kind='a_vault_note' AND path=? AND status='ok'",
        (work_id, path),
    ).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO artifacts (work_id,kind,path,url,sha256,size,status,detail,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (work_id, "a_vault_note", path, "", "", os.path.getsize(path), "ok", detail, now_iso()),
        )


def package_work_dir(work_id):
    """_文献包 子目录名：work_id 中的冒号替换为 -。

    macOS Finder 会把冒号显示成斜杠，Obsidian wikilink 也无法解析含冒号路径，
    因此目录名统一用安全形式（DB 中 work_id 保持原样）。
    """
    return str(work_id).replace(":", "-")

def publish_completed_to_package(conn, work_id, source_path=None):
    """把已验证 A 原子发布到 每日文献/_文献包/<work_id>/A.md（含 PDF/SI 副本）。

    新规则：自动完成的 A 不再投递 0_待处理；0_待处理 只表示用户明确“稍后”。
    """
    work = conn.execute("SELECT * FROM works WHERE work_id=?", (work_id,)).fetchone()
    if work is None:
        raise ValueError("work_id not found")
    existing = _vault_note_for_work(work)
    if existing:
        _record_vault_artifact(conn, work_id, existing["path"], "existing vault note")
        return {"path": existing["path"], "stage": existing["stage"], "published": False, "existing": True}
    candidate = source_path
    if not candidate:
        candidate = next(
            (p for p in _all_a_paths_for_work(conn, work) if a_document_status(p)["status"] == "completed"),
            "",
        )
    doc = a_document_status(candidate)
    if doc["status"] != "completed":
        raise ValueError("A document is not complete: " + doc["reason"])
    target_dir = os.path.join(PACKAGE_DIR, package_work_dir(work["work_id"]))
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, "A.md")
    if os.path.exists(target):
        _record_vault_artifact(conn, work_id, target,
                               "existing vault note (target exists; not overwritten)")
        return {"path": target, "stage": "package", "published": False, "existing": True}
    tmp = target + f".tmp-{os.getpid()}"
    try:
        shutil.copyfile(candidate, tmp)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _copy_pdf_si_into_package(conn, work_id, target_dir)
    _record_vault_artifact(conn, work_id, target, "A completed and published to _文献包")
    conn.execute(
        "INSERT INTO workflow_events (work_id,event_type,from_status,to_status,detail_json,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (work_id, "a_published", "a_completed", "a_completed",
         json.dumps({"source": candidate, "target": target}, ensure_ascii=False), now_iso()),
    )
    return {"path": target, "stage": "package", "published": True, "existing": False}


def _copy_pdf_si_into_package(conn, work_id, target_dir):
    """把 papers/ 里的 PDF/SI 副本放入 _文献包/<work_id>/（不覆盖已有文件）。"""
    procs = conn.execute(
        "SELECT * FROM processing WHERE work_id=? ORDER BY processed_at DESC", (work_id,)).fetchall()
    pdf = next((p for p in procs if p["pdf_status"] == "ok" and valid_pdf_file(p["pdf_path"])), None)
    pdf_path = pdf["pdf_path"] if pdf else ""
    si_path = pdf["si_path"] if pdf and pdf["si_status"] == "ok" and valid_pdf_file(pdf["si_path"]) else ""
    if not os.path.isabs(pdf_path):
        pdf_path = os.path.join(_CFG_PROJECT_ROOT, pdf_path)
    if si_path and not os.path.isabs(si_path):
        si_path = os.path.join(_CFG_PROJECT_ROOT, si_path)
    for src, name in ((pdf_path, "paper.pdf"), (si_path, "supplement.pdf")):
        if not src or not os.path.exists(src):
            continue
        dest = os.path.join(target_dir, name)
        if os.path.exists(dest):
            continue
        tmp = dest + f".tmp-{os.getpid()}"
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def publish_completed_to_inbox(conn, work_id, source_path=None):
    """兼容旧名：自动完成 A 一律发布到 _文献包（不再投递 0_待处理）。"""
    return publish_completed_to_package(conn, work_id, source_path)


def migrate_inbox_to_package(conn, work_ids=None):
    """把“自动完成 A 且仍留在 0_待处理”的笔记迁移到 _文献包/<work_id>/A.md。

    - 只处理 work_id 对应的自动生成 A（processing.a_status='completed'）；
    - 不覆盖任何已有文件；非自动/无 work_id 的历史人工笔记不动；
    - 更新 artifacts 记录并写入 workflow_events。
    """
    works = conn.execute(
        """SELECT DISTINCT w.* FROM works w
           JOIN processing p ON p.work_id=w.work_id AND p.a_status='completed'"""
    ).fetchall()
    if work_ids:
        works = [w for w in works if w["work_id"] in set(work_ids)]
    moved, skipped = [], []
    for work in works:
        note = _vault_note_for_work(work)
        if not note:
            skipped.append((work["work_id"], "no vault note"))
            continue
        if note["stage"] != "inbox":
            skipped.append((work["work_id"], "already in " + note["stage"]))
            continue
        target_dir = os.path.join(PACKAGE_DIR, package_work_dir(work["work_id"]))
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, "A.md")
        if os.path.exists(target):
            skipped.append((work["work_id"], "target exists; not overwritten"))
            continue
        os.replace(note["path"], target)
        _copy_pdf_si_into_package(conn, work["work_id"], target_dir)
        _record_vault_artifact(conn, work["work_id"], target, "migrated from 0_待处理 to _文献包")
        conn.execute(
            "INSERT INTO workflow_events (work_id,event_type,from_status,to_status,detail_json,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (work["work_id"], "migrated_to_package", "a_completed", "a_completed",
             json.dumps({"from": note["path"], "target": target}, ensure_ascii=False), now_iso()),
        )
        moved.append({"work_id": work["work_id"], "from": note["path"], "to": target})
    conn.commit()
    return {"moved": moved, "skipped": skipped, "moved_count": len(moved), "skipped_count": len(skipped)}


def _all_a_paths_for_work(conn, work):
    paths = []
    for row in conn.execute(
        "SELECT a_path FROM processing WHERE work_id=? AND a_path!='' ORDER BY processed_at DESC",
        (work["work_id"],),
    ).fetchall():
        if row["a_path"] and row["a_path"] not in paths:
            paths.append(row["a_path"])
    title_key = normalize_title_key(work["title"])
    for path in glob.glob(os.path.join(PAPERS_DIR, "*", "A_文献快速建档.md")):
        if path in paths:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                head = f.read(500)
        except OSError:
            continue
        m = re.search(r"^#\s*文献快速建档[：:]\s*(.+)$", head, re.M)
        if m and normalize_title_key(m.group(1)) == title_key:
            paths.append(path)
    for directory in (VAULT_STAGES["package"], VAULT_STAGES["inbox"], VAULT_STAGES["processing"]):
        pattern = os.path.join(directory, "**", "*.md") if directory == VAULT_STAGES["package"] else os.path.join(directory, "*.md")
        for path in glob.glob(pattern, recursive=(directory == VAULT_STAGES["package"])):
            if path not in paths and normalize_title_key(_note_title(path)) == title_key:
                paths.append(path)
    return paths


def workflow_item(conn, work_id):
    work = conn.execute("SELECT * FROM works WHERE work_id=?", (work_id,)).fetchone()
    if work is None:
        return None
    docs = [a_document_status(p) for p in _all_a_paths_for_work(conn, work)]
    completed = next((d for d in docs if d["status"] == "completed"), None)
    best_doc = completed or next((d for d in docs if d["status"] == "template"), None)
    procs = conn.execute(
        "SELECT * FROM processing WHERE work_id=? ORDER BY processed_at DESC", (work_id,)
    ).fetchall()
    pdf = next((p for p in procs if p["pdf_status"] == "ok" and valid_pdf_file(p["pdf_path"])), None)
    vault_note = _vault_note_for_work(work)
    return {
        "work_id": work_id,
        "reading_status": work["reading_status"] or "discovered",
        "a_status": "completed" if completed else ("queued" if best_doc and pdf else "blocked_pdf"),
        "a_path": (completed or best_doc or {}).get("path", ""),
        "pdf_status": "ok" if pdf else "missing",
        "pdf_path": pdf["pdf_path"] if pdf else "",
        "si_status": pdf["si_status"] if pdf else "",
        "vault_note_path": vault_note["path"] if vault_note else "",
        "vault_stage": vault_note["stage"] if vault_note else "",
    }


def _set_status(conn, work_id, new_status, event_type, detail=None, automatic=False):
    row = conn.execute("SELECT reading_status FROM works WHERE work_id=?", (work_id,)).fetchone()
    if row is None:
        return False
    old = row["reading_status"] or "discovered"
    if automatic and old in USER_STAGES:
        return False
    if old == new_status:
        return False
    conn.execute("UPDATE works SET reading_status=? WHERE work_id=?", (new_status, work_id))
    conn.execute(
        "INSERT INTO workflow_events (work_id,event_type,from_status,to_status,detail_json,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (work_id, event_type, old, new_status, json.dumps(detail or {}, ensure_ascii=False), now_iso()),
    )
    return True


def reconcile_a_status(conn):
    """核验磁盘上的 A，而不是相信旧 processing.a_status。"""
    stats = {"completed": 0, "queued": 0, "blocked_pdf": 0, "published": 0, "status_changes": 0}
    works = conn.execute(
        "SELECT DISTINCT w.* FROM works w JOIN processing p ON p.work_id=w.work_id"
    ).fetchall()
    for work in works:
        paths = _all_a_paths_for_work(conn, work)
        completed_path = ""
        for path in paths:
            doc = a_document_status(path)
            status = "completed" if doc["status"] == "completed" else "queued"
            conn.execute(
                "UPDATE processing SET a_status=? WHERE work_id=? AND a_path=?",
                (status, work["work_id"], path),
            )
            if doc["status"] == "completed" and not completed_path:
                completed_path = path
        procs = conn.execute(
            "SELECT * FROM processing WHERE work_id=? ORDER BY processed_at DESC", (work["work_id"],)
        ).fetchall()
        pdf = next((p for p in procs if p["pdf_status"] == "ok" and valid_pdf_file(p["pdf_path"])), None)
        if completed_path:
            stats["completed"] += 1
            stats["status_changes"] += int(_set_status(
                conn, work["work_id"], "a_completed", "a_validated",
                {"a_path": completed_path}, automatic=True,
            ))
            published = publish_completed_to_inbox(conn, work["work_id"], completed_path)
            stats["published"] += int(published["published"])
        elif pdf:
            stats["queued"] += 1
            conn.execute(
                "UPDATE processing SET a_status='queued' WHERE work_id=? AND run_id=?",
                (work["work_id"], pdf["run_id"]),
            )
            stats["status_changes"] += int(_set_status(
                conn, work["work_id"], "a_queued", "a_queued",
                {"pdf_path": pdf["pdf_path"], "a_skill": A_SKILL_PATH}, automatic=True,
            ))
        else:
            stats["blocked_pdf"] += 1
            conn.execute(
                "UPDATE processing SET a_status='blocked_pdf' "
                "WHERE work_id=? AND a_status NOT IN ('completed','ok')",
                (work["work_id"],),
            )
    conn.commit()
    return stats


def build_a_queue(conn):
    """生成机器可执行的 A 清单（live_must 优先，其次 historical_recovery）。

    清单是 AI 精读输入，不是已完成结果；queue_kind 区分 live_must / historical_recovery。
    """
    from la_queues import write_a_queue_file
    result = write_a_queue_file(conn)
    result["reconcile"] = reconcile_a_status(conn)
    return result


def _section(text, number):
    m = re.search(rf"^## {number}\.[^\n]*\n(.*?)(?=^## \d+\.|\Z)", text, re.M | re.S)
    if not m:
        return ""
    content = re.sub(r"\s+", " ", m.group(1)).strip()
    return content


def a_radar_excerpt(path):
    doc = a_document_status(path)
    if doc["status"] != "completed":
        return {}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    limits = {1: 280, 2: 360, 3: 360, 4: 300, 5: 260}
    labels = {1: "one_line", 2: "contribution", 3: "evidence", 4: "value", 5: "priority"}
    return {labels[n]: _section(text, n)[:limits[n]] for n in range(1, 6)}


def mark_a_completed(conn, work_id, path=None):
    item = workflow_item(conn, work_id)
    if item is None:
        raise ValueError("work_id not found")
    candidate = path or item["a_path"]
    doc = a_document_status(candidate)
    if doc["status"] != "completed":
        raise ValueError("A document is not complete: " + doc["reason"])
    conn.execute(
        "UPDATE processing SET a_status='completed' WHERE work_id=? AND a_path=?",
        (work_id, candidate),
    )
    _set_status(conn, work_id, "a_completed", "a_completed", {"a_path": candidate}, automatic=True)
    inbox = publish_completed_to_inbox(conn, work_id, candidate)
    conn.commit()
    return {"work_id": work_id, "a_path": candidate, "status": "completed", "vault": inbox}


def _move_note_for_decision(conn, work, decision):
    target_stage = {"read": "processing", "later": "inbox", "ignore": "read"}.get(decision)
    if not target_stage:
        return {"moved": False, "path": "", "stage": ""}
    found = _vault_note_for_work(work)
    if not found:
        item = workflow_item(conn, work["work_id"])
        if item and item["a_status"] == "completed":
            publish_completed_to_inbox(conn, work["work_id"], item["a_path"])
            found = _vault_note_for_work(work)
    if not found:
        return {"moved": False, "path": "", "stage": "", "reason": "no completed A note in vault"}
    if found["stage"] == target_stage:
        return {"moved": False, "path": found["path"], "stage": target_stage, "reason": "already there"}
    target_dir = VAULT_STAGES[target_stage]
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, _safe_filename(work["title"]))
    if os.path.exists(target):
        raise FileExistsError("vault target exists; refusing to overwrite: " + target)
    os.replace(found["path"], target)
    _record_vault_artifact(conn, work["work_id"], target, f"user decision: {decision}")
    return {"moved": True, "path": target, "stage": target_stage, "from": found["path"]}


def apply_decision(conn, work_id, decision, note=""):
    if decision not in DECISIONS:
        raise ValueError("decision must be one of: " + ", ".join(DECISIONS))
    work = conn.execute("SELECT * FROM works WHERE work_id=?", (work_id,)).fetchone()
    if work is None:
        raise ValueError("work_id not found")
    target = {"read": "processing", "later": "deferred", "ignore": "ignored"}.get(decision)
    changed = False
    if target:
        changed = _set_status(
            conn, work_id, target, "user_decision",
            {"decision": decision, "note": note}, automatic=False,
        )
    else:
        conn.execute(
            "INSERT INTO workflow_events (work_id,event_type,from_status,to_status,detail_json,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (work_id, "user_decision", work["reading_status"], work["reading_status"],
             json.dumps({"decision": decision, "note": note}, ensure_ascii=False), now_iso()),
        )
    if decision == "competitor":
        from la_feedback import run_feedback
        run_feedback(conn, work_id, "competitor", note=note)
    elif decision == "save_zotero":
        from la_feedback import run_feedback
        from la_zotero import mark
        run_feedback(conn, work_id, "save", note=note)
        mark(work_id, "accept")
    move = _move_note_for_decision(conn, work, decision)
    conn.commit()
    row = conn.execute("SELECT reading_status FROM works WHERE work_id=?", (work_id,)).fetchone()
    return {
        "work_id": work_id, "decision": decision, "changed": changed,
        "reading_status": row["reading_status"],
        "note_path": move.get("path", ""), "vault_stage": move.get("stage", ""),
        "files_moved": move.get("moved", False), "move_detail": move,
    }


def _flag_feedback_action(flag):
    return {"save_zotero": "save", "competitor": "competitor"}[flag]


def current_flags(conn, work_id):
    """flags 当前状态：读取 feedback 历史，以每条 flag 的最新记录为准。

    与 disposition（reading_status）完全独立：可同时为 read + save_zotero + competitor。
    """
    flags = {}
    for flag in FLAGS:
        action = _flag_feedback_action(flag)
        rows = conn.execute(
            "SELECT action FROM feedback WHERE work_id=? AND action IN (?,?) ORDER BY id",
            (work_id, action, action + "_off"),
        ).fetchall()
        on = False
        for r in rows:
            on = (r["action"] == action)
        flags[flag] = on
    return flags


def set_flag(conn, work_id, flag, on=True, note=""):
    """设置/取消一个 flag（save_zotero / competitor），不改变 disposition。"""
    if flag not in FLAGS:
        raise ValueError("flag must be one of: " + ", ".join(FLAGS))
    from la_feedback import run_feedback

    action = _flag_feedback_action(flag)
    rec = run_feedback(conn, work_id, action if on else action + "_off", note=note)
    conn.commit()
    flags = current_flags(conn, work_id)
    return {"work_id": work_id, "flag": flag, "on": on, "recorded": rec.get("ok", False), "flags": flags}


def inbox_items(conn):
    """0_待处理 中等待用户决定的已完成 A 笔记（标题→work 映射 + flags）。"""
    works = conn.execute("SELECT work_id,title FROM works").fetchall()
    by_title = {}
    for w in works:
        key = normalize_title_key(w["title"])
        if key and key not in by_title:
            by_title[key] = w["work_id"]
    items = []
    for stage, directory in VAULT_STAGES.items():
        if stage != "inbox":
            continue
        for path in sorted(glob.glob(os.path.join(directory, "*.md"))):
            title = _note_title(path) or os.path.splitext(os.path.basename(path))[0]
            work_id = by_title.get(normalize_title_key(title), "")
            items.append({
                "title": title, "path": path, "stage": stage, "work_id": work_id,
                "flags": current_flags(conn, work_id) if work_id else {},
                "decide": f"python3 literature_agent.py decide {work_id} read|later|ignore" if work_id else "",
            })
    return items


def context_package(conn, work_id):
    """返回接手一篇论文所需的最小上下文，不执行 B/C/D。"""
    work = conn.execute("SELECT * FROM works WHERE work_id=?", (work_id,)).fetchone()
    if work is None:
        raise ValueError("work_id not found")
    screen = conn.execute(
        "SELECT * FROM screenings WHERE work_id=? ORDER BY id DESC LIMIT 1", (work_id,)
    ).fetchone()
    item = workflow_item(conn, work_id)
    excerpt = a_radar_excerpt(item["a_path"]) if item and item["a_status"] == "completed" else {}
    return {
        "work": {"work_id": work_id, "title": work["title"], "doi": work["doi"], "arxiv_id": work["arxiv_id"]},
        "recommendation_priority": screen["grade"] if screen else "",
        "reading_stage": work["reading_status"] or "discovered",
        "pdf_path": item["pdf_path"] if item else "",
        "note_path": item["vault_note_path"] or item["a_path"] if item else "",
        "vault_stage": item["vault_stage"] if item else "",
        "a_excerpt": excerpt,
        "queue_kind": "live_must" if not (work["historical_discovery"] or 0) else "historical_recovery",
        "disposition_options": list(DISPOSITIONS),
        "flag_options": list(FLAGS),
        "current_flags": current_flags(conn, work_id),
        "skills": {"A": A_SKILL_PATH, "B": B_SKILL_PATH, "C": C_SKILL_PATH, "D": D_SKILL_PATH},
        "continuation_contract": {
            "A": "自动精读全文并生成 _文献包/<work_id>/A.md 建档；不得虚构未取得的全文或 SI",
            "B": "在 1_处理中自由问答；只把新增讨论总结追加到同一 Markdown",
            "C": "融合 A+B 为成熟可调用笔记；经用户明确确认后移入 2-可调用",
            "D": "从 C 派生发表/汇报材料；不得反向修改成熟 C 笔记",
        },
    }


def status_summary(conn):
    reconcile = reconcile_a_status(conn)
    stages = dict(conn.execute(
        "SELECT reading_status,COUNT(*) FROM works GROUP BY reading_status"
    ).fetchall())
    return {"reading_stages": stages, "a": reconcile, "queue_path": A_QUEUE_PATH}
