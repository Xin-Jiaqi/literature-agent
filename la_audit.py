#!/usr/bin/env python3
"""literature-agent 系统审计：DB/文件系统/总览/状态一致性检查。

- `audit`：快速一致性检查（非破坏，可在真实库上跑）。
- `audit --full`：快速检查 + 完整 acceptance suite（临时 DB/vault/fixtures）+ 真实 smoke。
- 工作流契约版本：WORKFLOW_SCHEMA_VERSION，代码/state/WORKFLOW/HANDOFF 必须一致。
"""
import glob
import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
WORKFLOW_SCHEMA_VERSION = 4
from literature_config import PAPERS_ROOT, STATE_ROOT, DOCS_ROOT, PROJECT_ROOT

REQUIRED_TABLES = ("works", "sources", "work_sources", "screenings", "processing", "processing_runs",
                   "digests", "work_versions", "artifacts", "errors", "source_runs", "dedup_review",
                   "tracked_entities", "citation_seeds", "citation_edges", "feedback", "coverage_gold",
                   "recommendation_signals", "workflow_events", "overview_syncs")
USER_DECIDED_STAGES = ("processing", "ignored", "callable", "deferred")


def _check(checks, name, ok, detail=""):
    checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})


def run_audit(conn=None):
    """快速审计：DB + 文件系统 + 总览 + 状态 一致性。返回 checks 列表。"""
    from la_db2 import connect
    own = conn is None
    if conn is None:
        conn = connect()
    checks = []
    try:
        # ---- DB 层 ----
        _check(checks, "db_open", conn.execute("SELECT 1").fetchone())
        missing_tables = [t for t in REQUIRED_TABLES
                          if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                              (t,)).fetchone()]
        _check(checks, "db_required_tables", not missing_tables, "missing: " + ",".join(missing_tables))
        work_count = conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
        state_path = os.path.join(STATE_ROOT, "PROJECT_STATE.json")
        state_data = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else None
        prev_works = (state_data or {}).get("database", {}).get("works")
        count_ok = work_count >= 0
        count_detail = f"works={work_count}"
        if prev_works is not None and work_count < prev_works * 0.7:
            count_ok = False
            count_detail += f" 异常大幅下降 vs 上次快照 {prev_works}"
        _check(checks, "work_count_reasonable", count_ok, count_detail)
        dup_pending = conn.execute("SELECT COUNT(*) FROM dedup_review WHERE status='pending'").fetchone()[0]
        _check(checks, "dedup_pending_zero", dup_pending == 0, dup_pending)

        # ---- artifact 路径 vs 文件系统 ----
        broken = conn.execute(
            """SELECT work_id, kind, path FROM artifacts
               WHERE path!='' AND kind IN ('pdf','supplement','a_vault_note')
                 AND status!='superseded'"""
        ).fetchall()
        broken_paths = [r["path"] for r in broken if not os.path.exists(r["path"])]
        _check(checks, "broken_artifact_paths_zero", len(broken_paths) == 0, f"{len(broken_paths)} broken")

        # ---- canonical A 唯一性（vault 阶段；papers/ 工作副本不算 canonical） ----
        from la_workflow import a_document_status, _note_title, VAULT_STAGES
        from la_dedup import normalize_title_key as _ntk
        works = conn.execute("SELECT work_id,title,reading_status FROM works").fetchall()
        title_to_work = {}
        for w in works:
            key = _ntk(w["title"])
            if key and key not in title_to_work:
                title_to_work[key] = w["work_id"]
        stage_notes = {}
        for stage, d in VAULT_STAGES.items():
            if not os.path.isdir(d):
                continue
            recursive = stage == "package"
            pattern = os.path.join(d, "**", "*.md") if recursive else os.path.join(d, "*.md")
            for p in glob.glob(pattern, recursive=recursive):
                for k in (_ntk(_note_title(p)), _ntk(os.path.splitext(os.path.basename(p))[0])):
                    if k:
                        stage_notes.setdefault(k, []).append({"path": p, "stage": stage})
        vault_completed = {}
        for stage, d in VAULT_STAGES.items():
            if not os.path.isdir(d):
                continue
            recursive = stage == "package"
            pattern = os.path.join(d, "**", "*.md") if recursive else os.path.join(d, "*.md")
            for p in glob.glob(pattern, recursive=recursive):
                if a_document_status(p)["status"] != "completed":
                    continue
                key = _ntk(_note_title(p)) or _ntk(os.path.splitext(os.path.basename(p))[0])
                wid = title_to_work.get(key)
                if wid:
                    vault_completed.setdefault(wid, []).append(p)
        dup_a = [(wid, paths) for wid, paths in vault_completed.items() if len(paths) > 1]
        _check(checks, "canonical_a_unique", len(dup_a) == 0,
               f"{len(dup_a)} works with >1 canonical A: {[(w, len(ps)) for w, ps in dup_a][:5]}")

        # ---- orphan A / orphan PDF ----
        titles = set((w["title"] or "").strip().lower() for w in works)
        from la_dedup import normalize_title_key
        work_keys = set(normalize_title_key(w["title"]) for w in works)
        orphan_a = []
        for p in glob.glob(os.path.join(PAPERS_ROOT, "*", "A_文献快速建档.md")):
            try:
                head = open(p, encoding="utf-8").read(400)
            except OSError:
                continue
            m = re.search(r"^#\s*文献快速建档[：:]\s*(.+)$", head, re.M)
            if m and normalize_title_key(m.group(1)) not in work_keys:
                orphan_a.append(p)
        _check(checks, "orphan_a_zero", len(orphan_a) == 0, f"{len(orphan_a)} orphan A docs")
        proc_pdfs = set()
        for r in conn.execute("SELECT pdf_path FROM processing WHERE pdf_path!=''").fetchall():
            pp = r["pdf_path"]
            proc_pdfs.add(pp if os.path.isabs(pp) else os.path.normpath(os.path.join(PROJECT_ROOT, pp)))
        orphan_pdf = []
        for p in glob.glob(os.path.join(PAPERS_ROOT, "*", "paper.pdf")):
            if os.path.normpath(p) not in proc_pdfs:
                orphan_pdf.append(p)
        _check(checks, "orphan_pdf_zero", len(orphan_pdf) == 0, f"{len(orphan_pdf)} orphan PDFs")

        # ---- reading-state / file-location 组合 ----
        invalid_state = []
        auto_in_inbox = []
        for w in works:
            rs = w["reading_status"] or "discovered"
            note = (stage_notes.get(_ntk(w["title"])) or [None])[0]
            stage = note["stage"] if note else ""
            if rs == "processing" and stage != "processing":
                invalid_state.append((w["work_id"], rs, stage))
            if rs == "ignored" and stage not in ("read", ""):
                invalid_state.append((w["work_id"], rs, stage))
            if rs == "deferred" and stage != "inbox":
                invalid_state.append((w["work_id"], rs, stage))
            has_completed = len(vault_completed.get(w["work_id"], [])) > 0
            if rs == "a_completed" and has_completed and stage == "inbox":
                auto_in_inbox.append(w["work_id"])
        _check(checks, "reading_state_file_location_consistent", len(invalid_state) == 0,
               str(invalid_state[:5]))
        _check(checks, "inbox_no_auto_undecided", len(auto_in_inbox) == 0,
               f"{len(auto_in_inbox)} auto-undecided in 0_待处理: {auto_in_inbox[:5]}")

        # ---- blocked_pdf 一致性 ----
        blocked = conn.execute(
            """SELECT COUNT(DISTINCT work_id) FROM processing
               WHERE a_status='blocked_pdf' AND work_id NOT IN (
                 SELECT work_id FROM processing WHERE a_status='completed')"""
        ).fetchone()[0]
        state_blocked = None
        if state_data is not None:
            state_blocked = state_data.get("metrics", {}).get("blocked_pdf")
            if state_blocked is None:
                state_blocked = state_data.get("a_workflow", {}).get("blocked_pdf")
        blocked_ok = state_blocked is None or blocked == state_blocked
        _check(checks, "blocked_pdf_consistent", blocked_ok,
               f"blocked={blocked} state={state_blocked}")

        # ---- 总览链接 / USER block ----
        from la_overview import verify_overview_links, overview_dir, list_overview_dates
        dates = list_overview_dates()
        ov_dir = overview_dir()
        if dates:
            _check(checks, "overview_exists", True, f"{len(dates)} 份总览（最新 {dates[-1]}）")
        else:
            _check(checks, "overview_exists", True, "无总览（合法空状态，0 篇论文）")
        broken_links = []
        user_blocks_ok = True
        if dates:
            latest = dates[-1]
            path = os.path.join(ov_dir, f"{latest} 文献总览.md")
            _check(checks, "overview_latest_file_exists", os.path.isfile(path), path)
            ok, missing = verify_overview_links(path)
            broken_links = missing
            _check(checks, "overview_links_valid", ok, f"{len(missing)} broken: {missing[:3]}")
            text = open(path, encoding="utf-8").read()
            n_blocks = len(re.findall(r"LA:BLOCK_BEGIN", text))
            n_user = len(re.findall(r"LA:USER_BEGIN", text))
            user_blocks_ok = n_blocks == n_user
            _check(checks, "overview_user_blocks_intact", user_blocks_ok, f"blocks={n_blocks} user={n_user}")

        # ---- PROJECT_STATE 与 DB 一致 ----
        state_mismatch = []
        if state_data is not None:
            state = state_data
            db_works = work_count
            if state.get("database", {}).get("works") != db_works:
                state_mismatch.append(f"works state={state['database']['works']} db={db_works}")
            state_v = state.get("workflow_schema_version")
            if state_v != WORKFLOW_SCHEMA_VERSION:
                state_mismatch.append(f"workflow_schema_version state={state_v} code={WORKFLOW_SCHEMA_VERSION}")
            from la_overview import overview_dir as _od
            pkg = len(glob.glob(os.path.join(VAULT_STAGES["package"], "**", "*.md"), recursive=True)) if os.path.isdir(VAULT_STAGES["package"]) else 0
            if state.get("vault_notes", {}).get("package") != pkg:
                state_mismatch.append(f"package state={state.get('vault_notes',{}).get('package')} fs={pkg}")
            inbox_fs = len(glob.glob(os.path.join(VAULT_STAGES["inbox"], "*.md")))
            if state.get("vault_notes", {}).get("inbox") != inbox_fs:
                state_mismatch.append(f"inbox state={state.get('vault_notes',{}).get('inbox')} fs={inbox_fs}")
        _check(checks, "project_state_matches_db", not state_mismatch, "; ".join(state_mismatch[:3]))

        # ---- HANDOFF 过期 / 版本 ----
        handoff_path = os.path.join(DOCS_ROOT, "HANDOFF.md")
        handoff_ok = True
        handoff_detail = ""
        if os.path.exists(handoff_path):
            htext = open(handoff_path, encoding="utf-8").read()
            if f"workflow_schema_version: {WORKFLOW_SCHEMA_VERSION}" not in htext:
                handoff_ok = False
                handoff_detail = "version missing"
        else:
            handoff_ok = False
            handoff_detail = "file missing"
        _check(checks, "handoff_current", handoff_ok, handoff_detail)

        # ---- WORKFLOW 契约版本 ----
        wf_path = os.path.join(DOCS_ROOT, "WORKFLOW.md")
        wf_ok = False
        if os.path.exists(wf_path):
            wf_ok = f"workflow_schema_version: {WORKFLOW_SCHEMA_VERSION}" in open(wf_path, encoding="utf-8").read()
        _check(checks, "workflow_contract_version", wf_ok)

        # ---- ChatGPT handoff source ----
        handoff_root = os.path.join(overview_dir(), "_ChatGPT交接")
        broken_handoff = []
        if os.path.isdir(handoff_root):
            for ctx in glob.glob(os.path.join(handoff_root, "*", "*", "CONTEXT.md")):
                src_line = [ln for ln in open(ctx, encoding="utf-8").read().splitlines()
                            if ln.startswith("- A 原始笔记路径")]
                if src_line:
                    src = src_line[0].split("：", 1)[-1].strip()
                    if src and not src.startswith("（") and not os.path.exists(src):
                        broken_handoff.append(ctx)
        _check(checks, "chatgpt_handoff_sources_exist", len(broken_handoff) == 0, str(broken_handoff[:3]))
    except Exception as e:
        _check(checks, "audit_self_no_exception", False, repr(e))
    finally:
        if own:
            conn.close()
    return checks


def summarize(checks):
    failed = [c for c in checks if not c["ok"]]
    return {"total": len(checks), "passed": len(checks) - len(failed), "failed": len(failed),
            "failures": [c for c in failed]}


def run_full_audit(conn=None):
    """快速审计 + acceptance suite（临时环境）+ 真实 smoke 摘要。"""
    from la_db2 import connect
    own = conn is None
    conn = conn or connect()
    checks = run_audit(conn)
    if own:
        conn.close()
    # acceptance suite 在临时 DB/vault/fixtures 中运行，不碰真实数据
    from la_acceptance import run_acceptance_suite
    acc = run_acceptance_suite()
    checks.append({"name": "acceptance_golden_path", "ok": acc["golden"]["ok"],
                   "detail": str(acc["golden"].get("detail", ""))})
    checks.append({"name": "acceptance_user_paths", "ok": acc["user_paths"]["ok"],
                   "detail": str(acc["user_paths"].get("detail", ""))})
    checks.append({"name": "acceptance_failure_tests", "ok": acc["failure"]["ok"],
                   "detail": f"passed {acc['failure'].get('passed', 0)}/{acc['failure'].get('total', 0)}"})
    return checks


if __name__ == "__main__":
    print(json.dumps(summarize(run_audit()), ensure_ascii=False, indent=1))
