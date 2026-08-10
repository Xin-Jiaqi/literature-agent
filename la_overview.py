#!/usr/bin/env python3
"""Obsidian 每日文献总览：用户唯一日常前台。

- overview：生成/刷新 每日文献/YYYY-MM-DD 文献总览.md（直接位于 每日文献/ 根目录，无日期子层）。
- sync-overview：读取用户批注 → disposition/flags → ChatGPT 交接 → 方向反馈候选（pending-only）。
- rerun 保护：机器字段用 HTML comment 标识；USER 区域（决定/附加标记/批注/方向反馈）原样保留。
- 正式 A 存放在 每日文献/_文献包（或用户决定后的当前阶段），总览只做 transclusion 入口，不产生第二个正式版本。
"""
import glob
import json
import os
import re
import shutil

from la_v2common import now_iso
from literature_config import DAILY_OVERVIEW_ROOT as _CFG_OVERVIEW_DIR, DATA_ROOT as _CFG_DATA_ROOT

BASE = os.path.dirname(os.path.abspath(__file__))

OVERVIEW_DIR = _CFG_OVERVIEW_DIR
HANDOFF_DIR = os.path.join(OVERVIEW_DIR, "_ChatGPT交接")
DIRECTION_FEEDBACK_PATH = os.environ.get(
    "LITAGENT_DIRECTION_FEEDBACK", os.path.join(_CFG_DATA_ROOT, "direction_feedback_candidates.md"))

# 相对 Obsidian vault 根（Jiaqixin/）的文献目录前缀，用于 wikilink
VAULT_REL_PREFIX = "orange/Literatures"

def overview_dir():
    """运行时读取 env（daily/selftest 可在进程内切换），默认 每日文献 根目录。"""
    return os.environ.get("LITAGENT_OVERVIEW_DIR") or OVERVIEW_DIR


def handoff_dir():
    return os.path.join(overview_dir(), "_ChatGPT交接")


def direction_feedback_path():
    return os.environ.get("LITAGENT_DIRECTION_FEEDBACK") or DIRECTION_FEEDBACK_PATH


BLOCK_BEGIN = "<!-- LA:BLOCK_BEGIN -->"
BLOCK_END = "<!-- LA:BLOCK_END -->"
USER_BEGIN = "<!-- LA:USER_BEGIN -->"
USER_END = "<!-- LA:USER_END -->"
WORK_MARK_RE = re.compile(r"<!--\s*LA:WORK_ID=([^\s>]+)\s*-->")
OVERVIEW_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) 文献总览\.md$")

DISPOSITION_CHECKS = {"要读": "read", "稍后": "later", "拒绝": "ignore"}
OPTION_HINTS = {
    "要读": "移入 1_处理中，进入深读/后续流程",
    "稍后": "移入 0_待处理，之后再看",
    "拒绝": "移入 00-已阅，不再打扰",
    "ChatGPT 深读": "打包 PDF+A+CONTEXT 供 ChatGPT 深读",
    "ChatGPT 做 A": "让 ChatGPT 独立重做 A，不删现有 A",
    "竞品": "标记竞品，进入竞品提醒",
    "存 Zotero": "自动建档到 Zotero 并上传 PDF",
}
FLAG_CHECKS = {"ChatGPT 深读": "chatgpt_deep_read", "ChatGPT 做 A": "chatgpt_a",
               "竞品": "competitor", "存 Zotero": "save_zotero"}
DECIDED_STATUS_LABEL = {
    "processing": "已决定：要读 → 已移入 1_处理中",
    "deferred": "已决定：稍后 → 留在 0_待处理",
    "ignored": "已决定：拒绝 → 已移入 00-已阅",
    "callable": "已进入 2-可调用",
}
SOURCE_LABELS = {
    "arxiv": "arXiv",
    "arxiv_replay": "arXiv（历史回放）",
    "aps_accepted": "APS accepted",
    "aps_published": "APS published",
    "nature_rss": "Nature/Science RSS",
    "npj_rss": "npj RSS",
    "crossref_fallback": "Crossref 补全",
    "openalex_author_watch": "OpenAlex 作者关注",
    "openalex_replay": "OpenAlex（历史回放）",
    "citation_watch": "引用雷达",
    "zotero": "Zotero",
    "import": "导入",
    "manual": "手动",
}


def _atomic_write(path, content):
    tmp = path + f".tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _safe_short_title(title, limit=60):
    name = re.sub(r'[\\/:*?"<>|\x00]+', "-", (title or "untitled").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:limit].rstrip(" .-") or "untitled"


def _vault_root():
    """从 VAULT_ROOT 向上找含 .obsidian 的目录（vault 根）；找不到就退回 VAULT_ROOT 上级。"""
    d = os.path.dirname(overview_dir())
    cur = d
    while True:
        if os.path.isdir(os.path.join(cur, ".obsidian")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.dirname(overview_dir())
        cur = parent


def _vault_rel_path(path):
    """把 vault 内绝对路径转成 vault-relative wikilink 路径（不含 URL 编码）。

    例如：<vault>/orange/Literatures/0_待处理/foo.md → orange/Literatures/0_待处理/foo.md
    """
    rel = os.path.relpath(path, _vault_root())
    if rel.startswith(".."):
        return None
    return rel.replace(os.sep, "/")


def _sources_label(conn, work_id, historical=False):
    rows = [r["source"] for r in conn.execute(
        "SELECT DISTINCT source FROM work_sources WHERE work_id=?", (work_id,)).fetchall()]
    if not historical:
        live = [src for src in rows if not str(src).endswith("_replay")]
        if live:
            rows = live
    labels = [SOURCE_LABELS.get(src, src) for src in rows]
    return "、".join(labels) if labels else "未知"


def _projects_label(conn, work_id):
    from la_digest import CORE_ORDER, PROFILE_PROJECT
    screen = conn.execute(
        "SELECT matched_profile FROM screenings WHERE work_id=? ORDER BY id DESC LIMIT 1",
        (work_id,)).fetchone()
    tags = [t for t in (screen["matched_profile"] or "").split(",") if t]
    projects = []
    for tag in CORE_ORDER:
        if tag in tags and tag not in projects:
            projects.append(PROFILE_PROJECT.get(tag, tag))
    if not projects:
        return "long_term"
    return "、".join(projects)


def _recommend_reason(conn, work):
    screen = conn.execute(
        "SELECT grade,reason FROM screenings WHERE work_id=? ORDER BY id DESC LIMIT 1",
        (work["work_id"],)).fetchone()
    if screen and screen["reason"]:
        return re.sub(r"\s+", " ", screen["reason"]).strip()[:180]
    return "值得阅读（A 已完成）"


def _priority_label(grade):
    return {"MUST": "MUST", "WEEKLY": "WEEKLY", "RADAR": "RADAR", "DROP": "DROP"}.get(grade, grade or "")


def _completed_a_works(conn):
    return conn.execute(
        """SELECT DISTINCT w.* FROM works w
           JOIN processing p ON p.work_id=w.work_id AND p.a_status='completed'
           ORDER BY w.historical_discovery, p.processed_at, w.work_id"""
    ).fetchall()


def _abs_path(path):
    if not path:
        return ""
    if os.path.isabs(path):
        return path
    from literature_config import PROJECT_ROOT as _root
    return os.path.normpath(os.path.join(_root, path))


def _pdf_paths(conn, work_id):
    """返回 (pdf_path, si_path)（绝对路径），都来自 processing 最新有效记录。"""
    from la_workflow import valid_pdf_file
    procs = conn.execute(
        "SELECT * FROM processing WHERE work_id=? ORDER BY processed_at DESC", (work_id,)).fetchall()
    pdf = next((p for p in procs if p["pdf_status"] == "ok" and valid_pdf_file(_abs_path(p["pdf_path"]))), None)
    if not pdf:
        return None, None
    si = pdf["si_path"] if pdf["si_status"] == "ok" and valid_pdf_file(_abs_path(pdf["si_path"])) else None
    return _abs_path(pdf["pdf_path"]), (_abs_path(si) if si else None)


def _a_note_for_work(conn, work):
    """返回 vault 中当前 A 笔记（任意阶段），与 la_workflow._vault_note_for_work 保持一致。"""
    from la_workflow import _vault_note_for_work
    return _vault_note_for_work(work)


def _fresh_user_region():
    return "\n".join([
        "#### 我的决定", "",
        *[f"- [ ] {k}（{v}）" for k, v in (("要读", OPTION_HINTS["要读"]), ("稍后", OPTION_HINTS["稍后"]), ("拒绝", OPTION_HINTS["拒绝"]))], "",
        "#### 附加标记", "",
        *[f"- [ ] {k}（{v}）" for k, v in (("ChatGPT 深读", OPTION_HINTS["ChatGPT 深读"]), ("ChatGPT 做 A", OPTION_HINTS["ChatGPT 做 A"]), ("竞品", OPTION_HINTS["竞品"]), ("存 Zotero", OPTION_HINTS["存 Zotero"]))], "",
        "#### 我的批注", "",
        "（在这里写你的想法；留空也可以）", "",
        "#### 方向反馈", "",
        "（可选。只有明确写“方向反馈：……”才会被收集为长期偏好候选）",
    ])




def _upgrade_user_region(user):
    """把旧版选项行（无说明）升级为带括号说明，保留 [x]/[ ] 状态与批注原文。"""
    if not user:
        return user
    out = []
    for line in user.splitlines():
        m = re.match(r"^(\s*- \[[ xX]\]\s*)([^（(]+?)(\s*)$", line)
        if m:
            core = m.group(2).strip()
            if core in OPTION_HINTS:
                out.append(f"{m.group(1)}{core}（{OPTION_HINTS[core]}）")
                continue
        out.append(line)
    return "\n".join(out)


def _render_block(conn, work, prev_user_region=None):
    from la_workflow import workflow_item
    wf = workflow_item(conn, work["work_id"]) or {}
    note = _a_note_for_work(conn, work)
    a_link = ""
    if note:
        rel = _vault_rel_path(note["path"])
        a_link = f"![[{rel}]]" if rel else f"[A 建档（本地）](file://{note['path']})"
    else:
        a_link = f"[A 建档（papers 草稿）](file://{wf.get('a_path', '')})"
    pdf_path, si_path = _pdf_paths(conn, work["work_id"])
    pdf_link = ""
    if pdf_path:
        from la_workflow import package_work_dir
        pkg_pdf = os.path.join(overview_dir(), "_文献包", package_work_dir(work["work_id"]), "paper.pdf")
        handoff_pdf = glob.glob(os.path.join(
            handoff_dir(), "*", _safe_short_title(work["title"]), "paper.pdf"))
        if os.path.exists(pkg_pdf):
            rel = _vault_rel_path(pkg_pdf)
            pdf_link = f"[[{rel}]]" if rel else f"[PDF](file://{pkg_pdf})"
        elif handoff_pdf:
            rel = _vault_rel_path(handoff_pdf[0])
            pdf_link = f"[[{rel}]]" if rel else f"[PDF](file://{handoff_pdf[0]})"
        else:
            pdf_link = f"[PDF](file://{pdf_path})"
    si_link = ""
    if si_path:
        pkg_si = os.path.join(overview_dir(), "_文献包", package_work_dir(work["work_id"]), "supplement.pdf")
        if os.path.exists(pkg_si):
            rel = _vault_rel_path(pkg_si)
            si_link = f"[[{rel}]]" if rel else f"[SI](file://{pkg_si})"
        else:
            si_link = f"[SI](file://{si_path})"
    status_line = DECIDED_STATUS_LABEL.get(work["reading_status"] or "", "")
    screen = conn.execute(
        "SELECT grade FROM screenings WHERE work_id=? ORDER BY id DESC LIMIT 1",
        (work["work_id"],)).fetchone()
    user = prev_user_region if prev_user_region is not None else _fresh_user_region()
    lines = [
        f"<!-- LA:WORK_ID={work['work_id']} -->",
        BLOCK_BEGIN,
        "",
        f"**一句话推荐**：{_recommend_reason(conn, work)}",
        "",
        f"**项目**：{_projects_label(conn, work['work_id'])}",
        f"**优先级**：{_priority_label(screen['grade'] if screen else '')}",
        f"**来源**：{_sources_label(conn, work['work_id'], historical=bool(work['historical_discovery'] or 0))}",
    ]
    if status_line:
        lines += ["", f"**状态**：{status_line}"]
    lines += ["", "#### A 建档（先看文章，再决定）", "", a_link]
    zotero_link = _zotero_open_link(work["work_id"])
    if pdf_link:
        extra = []
        if si_link:
            extra.append("SI：" + si_link)
        if zotero_link:
            extra.append(zotero_link)
        line = "PDF：" + pdf_link
        if extra:
            line += "｜" + "｜".join(extra)
        lines += ["", line]
    elif zotero_link:
        lines += ["", zotero_link]
    lines += ["", USER_BEGIN, user, USER_END, "", BLOCK_END]
    return "\n".join(lines)


DECIDED_STATUSES = ("processing", "deferred", "ignored", "callable")


def _decided_on_overview(conn, work_id, date):
    """该论文是否在这份总览（overview_date）上被用户决定。"""
    return conn.execute(
        "SELECT 1 FROM overview_syncs WHERE overview_date=? AND work_id=? LIMIT 1",
        (date, work_id)).fetchone() is not None


def render_overview(conn, date, prev_text=None):
    works = _completed_a_works(conn)
    decided = [w for w in works if (w["reading_status"] or "") in DECIDED_STATUSES]
    # 已处理区只显示“在这份总览上被决定”的，避免每天重复展示已经决定过的论文
    decided_today = [w for w in decided if _decided_on_overview(conn, w["work_id"], date)]
    pending = [w for w in works if (w["reading_status"] or "") not in DECIDED_STATUSES]
    live = [w for w in pending if not (w["historical_discovery"] or 0)]
    hist = [w for w in pending if (w["historical_discovery"] or 0)]
    prev_blocks = parse_overview_text(prev_text) if prev_text else {}
    waiting = 0
    for w in pending:
        prev = prev_blocks.get(w["work_id"])
        checked = _checked_dispositions(prev["user_region"]) if prev else []
        if not checked:
            waiting += 1
    lines = [
        f"# {date} 文献总览", "",
        f"> 今日新文献：{len(live)}",
        f"> 历史补回：{len(hist)}",
        f"> 等待决定：{waiting}",
        f"> 已处理：{len(decided_today)}", "",
        "说明：勾选后运行 `sync-overview`（或由 daily 自动同步）即生效；没有任何勾选 = 什么都不做。",
        "", "---", "",
    ]
    ordered = {wid: prev_blocks.get(wid) for wid in prev_blocks}
    for w in works:
        if w["work_id"] not in ordered:
            ordered[w["work_id"]] = None
    sections = [("今日新文献", live), ("历史补回", hist)]
    for title, items in sections:
        lines.append(f"## {title}\n")
        idx = 0
        for w in items:
            prev = ordered.get(w["work_id"])
            if prev is None and w["work_id"] in prev_blocks:
                prev = prev_blocks[w["work_id"]]
            idx += 1
            lines.append(f"### {idx}. {w['title']}\n")
            user = _upgrade_user_region(prev["user_region"]) if prev and prev["user_region"] else None
            lines.append(_render_block(conn, w, user))
            lines.append("")
        if not items:
            lines.append("（无）\n")
    if decided_today:
        lines.append("## 已处理（不在待办区）\n")
        idx = 0
        for w in decided_today:
            prev = ordered.get(w["work_id"])
            if prev is None and w["work_id"] in prev_blocks:
                prev = prev_blocks[w["work_id"]]
            idx += 1
            lines.append(f"### {idx}. {w['title']}\n")
            user = _upgrade_user_region(prev["user_region"]) if prev and prev["user_region"] else None
            lines.append(_render_block(conn, w, user))
            lines.append("")
    blocked = conn.execute(
        """SELECT COUNT(DISTINCT p.work_id) FROM processing p
           WHERE p.a_status='blocked_pdf' AND p.work_id NOT IN (
             SELECT work_id FROM processing WHERE a_status='completed')"""
    ).fetchone()[0]
    if blocked:
        lines += ["---", "", f"> 另有 {blocked} 篇重要论文全文暂未获取（不在此页占主要位置）。", ""]
    return "\n".join(lines)


def overview_path(date):
    return os.path.join(overview_dir(), f"{date} 文献总览.md")


def run_overview(conn, date):
    os.makedirs(overview_dir(), exist_ok=True)
    path = overview_path(date)
    prev_text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            prev_text = f.read()
    content = render_overview(conn, date, prev_text)
    _atomic_write(path, content)
    return {"path": path, "date": date, "bytes": len(content)}


def list_overview_dates():
    d = overview_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        m = OVERVIEW_NAME_RE.match(name)
        if m:
            out.append(m.group(1))
    return out


# ---------- 解析用户批注 ----------

def parse_overview_text(text):
    """按 BLOCK 切分，返回 {work_id: block}；保留顺序。"""
    blocks = {}
    for m in re.finditer(r"<!--\s*LA:WORK_ID=([^\s>]+)\s*-->(.*?)(?=<!--\s*LA:WORK_ID=|\Z)", text, re.S):
        work_id, body = m.group(1), m.group(2)
        if not re.search(re.escape(BLOCK_BEGIN), body):
            continue
        user = ""
        um = re.search(re.escape(USER_BEGIN) + r"(.*?)" + re.escape(USER_END), body, re.S)
        if um:
            user = um.group(1).strip("\n")
        blocks[work_id] = {
            "work_id": work_id,
            "user_region": user,
            "disposition_checked": _checked_dispositions(user),
            "flags_checked": _checked_flags(user),
            "note": _extract_note(user),
            "direction_feedback": _extract_direction_feedback(user),
        }
    return blocks


def _checked_items(user_region, mapping):
    checked = []
    if not user_region:
        return checked
    for line in user_region.splitlines():
        m = re.match(r"^\s*-\s*\[([xX])\]\s*(.+?)\s*$", line)
        if not m:
            continue
        label = m.group(2).strip()
        core = re.split(r"[（(]", label)[0].strip()
        if core in mapping:
            checked.append(mapping[core])
    return checked


def _checked_dispositions(user_region):
    return _checked_items(user_region, DISPOSITION_CHECKS)


def _checked_flags(user_region):
    return _checked_items(user_region, FLAG_CHECKS)


def _extract_note(user_region):
    m = re.search(r"####\s*我的批注\s*\n(.*?)(?=####\s*方向反馈|\Z)", user_region or "", re.S)
    if not m:
        return ""
    text = m.group(1).strip()
    if re.fullmatch(r"（在这里写你的想法；留空也可以）", text):
        return ""
    return text


def _extract_direction_feedback(user_region):
    m = re.search(r"####\s*方向反馈\s*\n(.*?)(?=<!--\s*LA:USER_END|\Z)", user_region or "", re.S)
    if not m:
        return ""
    section = m.group(1).strip()
    if not section:
        return ""
    lines = []
    for line in section.splitlines():
        fm = re.match(r"^\s*[*-]?\s*方向反馈\s*[：:]\s*(.+)$", line.strip())
        if fm:
            lines.append(fm.group(1).strip())
        elif lines and line.strip():
            lines.append(line.strip())
    text = " ".join(lines).strip()
    if not text:
        return ""
    if re.fullmatch(r"（可选。只有明确写“方向反馈：……”才会被收集为长期偏好候选）", text):
        return ""
    return text


# ---------- 同步 ----------

def _prev_sync(conn, overview_date, work_id):
    return conn.execute(
        "SELECT * FROM overview_syncs WHERE overview_date=? AND work_id=? ORDER BY id DESC LIMIT 1",
        (overview_date, work_id)).fetchone()


def _record_sync(conn, date, work_id, block, conflict=False, handoffs=None):
    conn.execute(
        "INSERT INTO overview_syncs (overview_date,work_id,disposition,chatgpt_deep_read,chatgpt_a,"
        "competitor,save_zotero,user_note,direction_feedback,conflict,synced_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (date, work_id,
         ",".join(block["disposition_checked"]) or None,
         int("chatgpt_deep_read" in block["flags_checked"]),
         int("chatgpt_a" in block["flags_checked"]),
         int("competitor" in block["flags_checked"]),
         int("save_zotero" in block["flags_checked"]),
         block["note"], block["direction_feedback"], int(conflict), now_iso()))
    conn.commit()


def _zotero_queue_state(work_id):
    from la_zotero import load_queue
    for e in load_queue():
        if e["work_id"] == work_id:
            return e["status"]
    return "not_in_queue"


def _zotero_open_link(work_id):
    """若该论文已同步到 Zotero，返回 Obsidian 可点击的 zotero:// 打开链接。"""
    from la_zotero import load_queue
    for e in load_queue():
        if e.get("work_id") == work_id and e.get("zotero_item_key"):
            return f"Zotero：[打开](zotero://select/items/0_{e['zotero_item_key']})"
    return ""


def _ensure_zotero_accepted(conn, work_id):
    from la_zotero import mark
    ok = mark(work_id, "accept")
    if ok:
        return True
    from la_zotero import load_queue, _save
    import literature_agent as la
    work = conn.execute("SELECT * FROM works WHERE work_id=?", (work_id,)).fetchone()
    if work is None:
        return False
    screen = conn.execute(
        "SELECT * FROM screenings WHERE work_id=? ORDER BY id DESC LIMIT 1", (work_id,)).fetchone()
    main, compat, ztags = la.zotero_suggestion(work, screen) if screen else ("", [], [])
    entries = load_queue()
    entries.append({"work_id": work_id, "title": work["title"], "grade": (screen["grade"] if screen else ""),
                    "main_collection": main, "compatible_collections": compat, "tags": ztags,
                    "reason": (screen["reason"] if screen else "")[:200], "status": "pending_user",
                    "added_at": now_iso()[:16]})
    _save(entries)
    mark(work_id, "accept")
    return True


def build_chatgpt_handoff(conn, date, work_id, mode):
    """mode: 'deep_read'（要读+ChatGPT 深读）或 'a_request'（ChatGPT 做 A）。"""
    from la_workflow import workflow_item, valid_pdf_file
    work = conn.execute("SELECT * FROM works WHERE work_id=?", (work_id,)).fetchone()
    if work is None:
        return {"ok": False, "error": "work not found"}
    wf = workflow_item(conn, work_id) or {}
    target = os.path.join(handoff_dir(), date, _safe_short_title(work["title"]))
    os.makedirs(target, exist_ok=True)
    files = []
    pdf_path, si_path = _pdf_paths(conn, work_id)
    if pdf_path and os.path.exists(pdf_path):
        dest = os.path.join(target, "paper.pdf")
        if not os.path.exists(dest):
            shutil.copyfile(pdf_path, dest)
        files.append("paper.pdf")
    if si_path and os.path.exists(si_path):
        dest = os.path.join(target, "supplement.pdf")
        if not os.path.exists(dest):
            shutil.copyfile(si_path, dest)
        files.append("supplement.pdf")
    note = _a_note_for_work(conn, work)
    a_src = note["path"] if note else wf.get("a_path", "")
    if mode == "deep_read":
        if a_src and os.path.exists(a_src):
            dest = os.path.join(target, "A.md")
            if not os.path.exists(dest):
                shutil.copyfile(a_src, dest)
            files.append("A.md")
    screen = conn.execute(
        "SELECT * FROM screenings WHERE work_id=? ORDER BY id DESC LIMIT 1", (work_id,)).fetchone()
    context = {
        "Title": work["title"],
        "DOI": work["doi"] or "未确认",
        "arXiv": work["arxiv_id"] or "",
        "work_id": work_id,
        "推荐原因": (screen["reason"] if screen else "")[:300],
        "对应项目": _projects_label(conn, work_id),
        "discovery path": _sources_label(conn, work_id, historical=bool(work["historical_discovery"] or 0)),
        "competitor": "是" if "competitor" in _checked_flags_any(conn, work_id) else "否",
        "Zotero state": _zotero_queue_state(work_id),
        "A 原始笔记路径": a_src or "（尚未完成正式 A）",
        "交接类型": "deep_read（要读 + ChatGPT 深读）" if mode == "deep_read" else "a_request（ChatGPT 独立执行 A）",
        "a_executor": "chatgpt" if mode == "a_request" else "codex",
    }
    ctx_lines = ["# 交接上下文", ""]
    for k, v in context.items():
        ctx_lines.append(f"- {k}：{v}")
    ctx_lines += ["", "目的：把 PDF + A + CONTEXT 上传 ChatGPT，直接进入深度问答 → B → C → D。", ""]
    if mode == "a_request":
        ctx_lines += [
            "注意：本目录中的 A.md 如果存在，是已有 Codex 版本快照，只作参考；",
            "请独立重新执行 A（见 A_REQUEST.md），不要删除 Codex 版本，也不要覆盖 _文献包 正式 A。",
        ]
    _atomic_write(os.path.join(target, "CONTEXT.md"), "\n".join(ctx_lines))
    files.append("CONTEXT.md")
    if mode == "a_request":
        req = [
            "# A_REQUEST：请独立执行 A 快速建档", "",
            f"- work_id：{work_id}",
            f"- 标题：{work['title']}",
            f"- 依据：{pdf_path or '无 PDF（不可伪造）'}",
            f"- SI：{si_path or '未获取'}",
            f"- 现有 Codex A（参考，不删除）：{a_src or '无'}",
            f"- A Skill：{os.environ.get('LITAGENT_A_SKILL', 'Literatures/AI system/A. 重要文献快速建档 Skill.md')}", "",
            "要求：基于实际取得的正文/SI 完整生成 A（六节齐全、无占位符、≥1000 字符）；",
            "若 PDF/SI 缺失，如实标注；不得虚构。完成后由 literature-agent 校验并发布到 _文献包。",
        ]
        _atomic_write(os.path.join(target, "A_REQUEST.md"), "\n".join(req))
        files.append("A_REQUEST.md")
    return {"ok": True, "dir": target, "files": files, "work_id": work_id, "mode": mode}


def _checked_flags_any(conn, work_id):
    from la_workflow import current_flags
    flags = current_flags(conn, work_id)
    return [k for k, v in flags.items() if v]


def _append_direction_feedback(date, work_id, title, text):
    entry = (f"## {date} · {work_id}\n"
             f"- 论文：{title}\n"
             f"- 用户原文：{text}\n"
             f"- 可能影响的 profile 项：待分析（未应用）\n"
             f"- 状态：pending_confirmation\n")
    existing = ""
    if os.path.exists(direction_feedback_path()):
        with open(direction_feedback_path(), encoding="utf-8") as f:
            existing = f.read()
    if f"{date} · {work_id}" in existing:
        return False
    with open(direction_feedback_path(), "a", encoding="utf-8") as f:
        f.write(entry + "\n")
    return True


def run_sync_overview(conn, date):
    path = overview_path(date)
    if not os.path.exists(path):
        return {"date": date, "path": "", "error": "overview not found", "applied": 0,
                "conflicts": 0, "handoffs": 0, "direction_feedback": 0}
    with open(path, encoding="utf-8") as f:
        blocks = parse_overview_text(f.read())
    summary = {"date": date, "path": path, "applied": 0, "conflicts": 0, "conflict_works": [],
               "handoffs": [], "direction_feedback": 0, "skipped": 0, "applied_works": []}
    for work_id, block in blocks.items():
        work = conn.execute("SELECT title FROM works WHERE work_id=?", (work_id,)).fetchone()
        if work is None:
            summary["skipped"] += 1
            continue
        prev = _prev_sync(conn, date, work_id)
        if len(block["disposition_checked"]) > 1:
            _record_sync(conn, date, work_id, block, conflict=True)
            summary["conflicts"] += 1
            summary["conflict_works"].append(work_id)
            continue
        applied_any = False
        if len(block["disposition_checked"]) == 1:
            decision = block["disposition_checked"][0]
            if prev is None or (prev["disposition"] or "") != decision:
                from la_workflow import apply_decision
                apply_decision(conn, work_id, decision, note=block["note"])
                applied_any = True
        flags = block["flags_checked"]
        from la_workflow import set_flag
        for flag in ("competitor", "save_zotero"):
            if flag in flags and (prev is None or not prev[flag]):
                set_flag(conn, work_id, flag, True, note=block["note"])
                applied_any = True
        if "save_zotero" in flags:
            if prev is None or not prev["save_zotero"]:
                _ensure_zotero_accepted(conn, work_id)
                try:
                    from la_zotero import zotero_auto_enabled, zotero_sync_queue
                    if zotero_auto_enabled():
                        summary["zotero_sync"] = summary.get("zotero_sync", [])
                        summary["zotero_sync"].append(zotero_sync_queue(conn))
                except Exception:
                    pass
        deep_read = "chatgpt_deep_read" in flags
        want_a = "chatgpt_a" in flags
        if deep_read and len(block["disposition_checked"]) == 1 and block["disposition_checked"][0] == "read":
            if prev is None or not prev["chatgpt_deep_read"]:
                r = build_chatgpt_handoff(conn, date, work_id, "deep_read")
                summary["handoffs"].append({"work_id": work_id, "mode": "deep_read", "dir": r.get("dir", "")})
                applied_any = True
        elif deep_read:
            summary["skipped"] += 1
        if want_a:
            if prev is None or not prev["chatgpt_a"]:
                r = build_chatgpt_handoff(conn, date, work_id, "a_request")
                summary["handoffs"].append({"work_id": work_id, "mode": "a_request", "dir": r.get("dir", "")})
                applied_any = True
        fb_added = False
        if block["direction_feedback"]:
            fb_added = _append_direction_feedback(date, work_id, work["title"], block["direction_feedback"])
            if fb_added:
                summary["direction_feedback"] += 1
        if applied_any or fb_added:
            _record_sync(conn, date, work_id, block)
            summary["applied"] += 1
            summary["applied_works"].append(work_id)
        else:
            summary["skipped"] += 1
    return summary


def sync_old_overviews(conn, today):
    """daily 开始时：同步所有已生成总览的既有批注（含当天，幂等）。

    当天首次 daily 时今天的文件尚未生成，不会误同步；后续时间点（或 skipped 分支）
    会把用户当天新勾选的批注应用掉，无需等到明天。
    """
    results = []
    for date in list_overview_dates():
        if date > today:
            continue
        results.append(run_sync_overview(conn, date))
    return {"synced": [r for r in results if r.get("applied")], "total_old": len(results)}


# ---------- 链接校验 ----------

def verify_overview_links(path):
    """校验总览里所有 wikilink 指向真实存在的文件；返回 (ok, missing)。"""
    vault_root = _vault_root()
    with open(path, encoding="utf-8") as f:
        text = f.read()
    missing = []
    for m in re.finditer(r"!?\[\[([^\[\]]+?)\]\]", text):
        target = m.group(1).strip()
        if target.endswith(".md") or target.endswith(".pdf"):
            abs_path = os.path.normpath(os.path.join(vault_root, target))
            if not os.path.exists(abs_path):
                missing.append(target)
    for m in re.finditer(r"\[(?:PDF|SI|A 建档[^）]*)\]\((file://[^)]+)\)", text):
        p = m.group(1).removeprefix("file://")
        if not os.path.exists(p):
            missing.append(p)
    return (len(missing) == 0), missing
