#!/usr/bin/env python3
"""A worker：把 a_queue 做成 Codex 可以连续消费的 agent job。

当前技术条件下 Python 侧不调用模型，因此：
- `a-job` 生成一次任务清单（manifest，含协议与逐篇最小上下文）；
- 一次任务自动逐篇完成整个队列：读 PDF → 按 A Skill 写 A → a-complete → 下一篇，
  不每篇等待用户确认；
- 任何一篇 PDF 无法读取/正文不完整：如实跳过并在 job 报告说明，不伪造。

产物目录：a_jobs/<job_id>.json；README 描述协议。
"""
import json
import os
from datetime import datetime

from la_v2common import now_iso

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import QUEUES_ROOT
A_JOBS_DIR = os.environ.get("LITAGENT_A_JOBS", os.path.join(QUEUES_ROOT, "a_jobs"))

PROTOCOL = {
    "worker": "codex",
    "mode": "one_shot_consume_all_without_confirmation",
    "steps": [
        "1. 按 entries 顺序逐篇执行（顺序即优先级：live_must 在前，historical_recovery 在后）。",
        "2. 每篇：",
        "   a. 读取 pdf_path 的全文（PDF 已校验 %PDF- 与最小体积，必须真实阅读，不得跳过）；",
        "   b. 按 a_skill_path 的 A Skill，用中文填写 a_output_path 的 A_文献快速建档.md，",
        "      必须包含 ## 0. 到 ## 5. 六节，禁止占位符（待填写/状态：待完成）；",
        "   c. 运行 `python3 literature_agent.py a-complete <work_id>` 完成内容核验并发布到 `每日文献/_文献包/<work_id>/`（A.md + paper.pdf）；",
        "   d. 完成后继续下一篇，不要等待用户确认。",
        "3. 全部完成后运行 `python3 literature_agent.py a-queue` 与 `python3 literature_agent.py handoff` 刷新状态。",
    ],
    "honesty_rules": [
        "不得虚构未取得的正文、SI 或数据；SI 未获取时在 §0 与 §3 明确标注",
        "模板不算完成；只有通过 a_document_status 校验（六节齐备、无占位符、≥1000 字符）才算完成",
        "无法真实完成的条目：保留在队列中，在 job 报告里说明原因",
    ],
    "complete_command": "python3 literature_agent.py a-complete {work_id}",
}


def create_job(conn, kind="all", note=""):
    """生成一次 A worker 任务清单。kind: all | live_must | historical_recovery | weekly(仅清单，不执行 A)。"""
    from la_queues import a_queue_entries, weekly_candidates

    os.makedirs(A_JOBS_DIR, exist_ok=True)
    job_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    entries = a_queue_entries(conn)
    if kind == "live_must":
        entries = [e for e in entries if e["queue_kind"] == "live_must"]
    elif kind == "historical_recovery":
        entries = [e for e in entries if e["queue_kind"] == "historical_recovery"]
    elif kind == "weekly":
        entries = []  # weekly 不自动执行 A
    elif kind != "all":
        raise ValueError("kind must be all|live_must|historical_recovery|weekly")

    manifest = {
        "job_id": job_id,
        "created_at": now_iso(),
        "kind": kind,
        "note": note,
        "protocol": PROTOCOL,
        "entries": [
            {k: e[k] for k in ("work_id", "title", "arxiv_id", "doi", "queue_kind",
                               "a_status", "pdf_path", "a_output_path",
                               "a_skill_path" if "a_skill_path" in e else "pdf_path") if k in e}
            for e in entries
        ],
        "summary": {
            "total": len(entries),
            "live_must": sum(1 for e in entries if e["queue_kind"] == "live_must"),
            "historical_recovery": sum(1 for e in entries if e["queue_kind"] == "historical_recovery"),
            "weekly_candidates": len(weekly_candidates(conn)),
        },
    }
    path = os.path.join(A_JOBS_DIR, f"{job_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return {"job_id": job_id, "path": path, "summary": manifest["summary"]}


def latest_job_path():
    if not os.path.isdir(A_JOBS_DIR):
        return ""
    jobs = sorted(f for f in os.listdir(A_JOBS_DIR) if f.endswith(".json"))
    return os.path.join(A_JOBS_DIR, jobs[-1]) if jobs else ""


def job_status(conn, job_id=None):
    """最新（或指定）job 的逐篇执行状态。"""
    from la_workflow import workflow_item

    path = latest_job_path()
    if not path:
        return {"found": False}
    if job_id:
        cand = os.path.join(A_JOBS_DIR, job_id + ".json")
        if os.path.exists(cand):
            path = cand
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    statuses = []
    done = pending = blocked = 0
    for e in manifest["entries"]:
        item = workflow_item(conn, e["work_id"])
        st = item["a_status"] if item else "missing"
        statuses.append({"work_id": e["work_id"], "title": e["title"][:90],
                         "queue_kind": e.get("queue_kind", ""), "a_status": st})
        done += int(st == "completed")
        pending += int(st in ("queued",))
        blocked += int(st == "blocked_pdf")
    return {
        "found": True, "job_id": manifest["job_id"], "path": path,
        "created_at": manifest["created_at"], "kind": manifest["kind"],
        "total": len(manifest["entries"]), "completed": done,
        "pending": pending, "blocked_pdf": blocked,
        "entries": statuses,
    }


def render_README():
    lines = [
        "# A worker 任务队列",
        "",
        "A 建档由 Codex（本 agent）消费这里的 job manifest 连续执行，一次任务自动逐篇完成整个队列，",
        "不每篇等待用户确认。",
        "",
        "## 消费方式",
        "",
        "```bash",
        "python3 literature_agent.py a-job --kind all          # 生成任务清单",
        "python3 literature_agent.py a-job-status              # 查看逐篇进度",
        "python3 literature_agent.py a-queue                   # 查看三队列状态",
        "python3 literature_agent.py handoff                   # 完成后刷新状态",
        "```",
        "",
        "## 每篇执行步骤（按 manifest.protocol）",
        "",
        "1. 读取 `pdf_path` 全文（不得跳过）。",
        "2. 按 `a_skill_path` 的 A Skill 填写 `a_output_path` 的 A_文献快速建档.md（## 0.~## 5. 六节，无占位符）。",
        "3. `python3 literature_agent.py a-complete <work_id>` 核验并发布到 `_文献包/<work_id>/`。",
        "4. 继续下一篇，不等待用户确认。",
        "",
        "## 诚实性约束",
        "",
        "- 不虚构未取得的正文/SI；SI 未获取时明确标注。",
        "- 模板不算完成；无法真实完成时保留队列并在报告说明。",
    ]
    os.makedirs(A_JOBS_DIR, exist_ok=True)
    path = os.path.join(A_JOBS_DIR, "README.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path
