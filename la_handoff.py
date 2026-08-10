#!/usr/bin/env python3
"""生成唯一、实时、可供人和 AI 接手的项目状态。

核心指标（用户向，不再以 works 总数/抓取量/数据库大小为核心）：
- 今日 live MUST 数；live MUST → A 完成率；A 平均完成时间；blocked PDF 数；
- 用户 accept/read 比例；ignore 比例；historical backlog；用户 Inbox backlog。
"""
import glob
import json
import os
import re
from datetime import datetime

from la_priority import DROP, MUST, RADAR, WEEKLY
from literature_config import DOCS_ROOT, STATE_ROOT

BASE = os.path.dirname(os.path.abspath(__file__))
HANDOFF_DIR = os.environ.get("LITAGENT_HANDOFF_DIR", DOCS_ROOT)
HANDOFF_PATH = os.path.join(HANDOFF_DIR, "HANDOFF.md")
CODEX_HANDOFF_PATH = os.path.join(HANDOFF_DIR, "CODEX_HANDOFF.md")
# 隔离语义：测试设置 LITAGENT_HANDOFF_DIR 时 trio 同目录；生产默认 state/PROJECT_STATE.json
_HANDOFF_TMP = os.environ.get("LITAGENT_HANDOFF_DIR")
STATE_PATH = os.environ.get(
    "LITAGENT_STATE_PATH",
    os.path.join(_HANDOFF_TMP, "PROJECT_STATE.json") if _HANDOFF_TMP else os.path.join(STATE_ROOT, "PROJECT_STATE.json"),
)


def _atomic_text(path, content):
    tmp = path + f".tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _dict_rows(conn, sql, args=()):
    return {str(r[0] or ""): r[1] for r in conn.execute(sql, args).fetchall()}


def _queue_count(path):
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def _must_count(conn, historical):
    return conn.execute(
        """SELECT COUNT(DISTINCT w.work_id) FROM works w
           WHERE w.historical_discovery=? AND EXISTS (
             SELECT 1 FROM screenings s WHERE s.work_id=w.work_id AND s.grade=?
               AND s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=w.work_id))""",
        (int(historical), MUST),
    ).fetchone()[0]


def _must_completed_count(conn, historical):
    return conn.execute(
        """SELECT COUNT(DISTINCT w.work_id) FROM works w
           JOIN processing p ON p.work_id=w.work_id AND p.a_status='completed'
           WHERE w.historical_discovery=? AND EXISTS (
             SELECT 1 FROM screenings s WHERE s.work_id=w.work_id AND s.grade=?
               AND s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=w.work_id))""",
        (int(historical), MUST),
    ).fetchone()[0]


WORKFLOW_SCHEMA_VERSION = 4


def collect_state(conn):
    from la_audit import WORKFLOW_SCHEMA_VERSION
    from la_queues import queue_state
    from la_workflow import A_QUEUE_PATH, VAULT_STAGES, reconcile_a_status

    a_state = reconcile_a_status(conn)
    q = queue_state(conn)
    priorities = _dict_rows(conn, """SELECT grade,COUNT(*) FROM screenings s
        WHERE s.id=(SELECT MAX(s2.id) FROM screenings s2 WHERE s2.work_id=s.work_id)
        GROUP BY grade""")
    stages = _dict_rows(conn, "SELECT reading_status,COUNT(*) FROM works GROUP BY reading_status")
    last_run = conn.execute(
        "SELECT run_id,command,finished_at,status,summary_json FROM processing_runs "
        "ORDER BY finished_at DESC, run_id DESC LIMIT 1"
    ).fetchone()
    latest_source_failures = conn.execute("""SELECT sr.source,sr.error,sr.fetched_at FROM source_runs sr
        WHERE sr.id=(SELECT MAX(sr2.id) FROM source_runs sr2 WHERE sr2.source=sr.source)
          AND sr.ok=0 ORDER BY sr.fetched_at DESC""").fetchall()
    vault_notes = {}
    for stage, path in VAULT_STAGES.items():
        if stage == "package":
            vault_notes[stage] = len(glob.glob(os.path.join(path, "**", "*.md"), recursive=True))
        else:
            vault_notes[stage] = len(glob.glob(os.path.join(path, "*.md")))

    live_total = _must_count(conn, historical=False)
    live_completed = _must_completed_count(conn, historical=False)
    hist_total = _must_count(conn, historical=True)
    hist_completed = _must_completed_count(conn, historical=True)

    avg_hours = conn.execute(
        """SELECT AVG((julianday(e.created_at) - julianday(p.processed_at)) * 24)
           FROM workflow_events e JOIN processing p
             ON p.work_id=e.work_id AND p.a_status='completed'
           WHERE e.event_type='a_completed' AND e.created_at!='' AND p.processed_at!=''"""
    ).fetchone()[0]

    dec = dict(conn.execute(
        "SELECT reading_status,COUNT(*) FROM works "
        "WHERE reading_status IN ('processing','deferred','ignored','callable') GROUP BY reading_status"
    ).fetchall())
    decided_total = sum(dec.values())
    read_n = dec.get("processing", 0) + dec.get("callable", 0)
    ignored_n = dec.get("ignored", 0)

    metrics = {
        "package_pending": vault_notes.get("package", 0),
        "live_must_backlog": q["live_must"]["pending"],
        "live_must_total": live_total,
        "live_must_completed": live_completed,
        "live_must_a_completion_rate": round(live_completed / live_total, 3) if live_total else None,
        "historical_recovery_backlog": q["historical_recovery"]["pending"],
        "historical_total": hist_total,
        "historical_completed": hist_completed,
        "weekly_candidates": q["weekly"]["pending"],
        "blocked_pdf": a_state["blocked_pdf"],
        "avg_a_completion_hours": round(avg_hours, 2) if avg_hours else None,
        "user_inbox_backlog": vault_notes["inbox"],
        "user_processing": dec.get("processing", 0),
        "user_decided_total": decided_total,
        "user_accept_ratio": round(read_n / decided_total, 3) if decided_total else None,
        "user_ignore_ratio": round(ignored_n / decided_total, 3) if decided_total else None,
    }

    from la_overview import OVERVIEW_DIR, list_overview_dates, overview_path

    overview = {"last_date": "", "path": "", "live": 0, "historical": 0, "waiting": 0}
    dates = list_overview_dates()
    if dates:
        last = dates[-1]
        overview.update({"last_date": last, "path": overview_path(last)})
        try:
            with open(overview_path(last), encoding="utf-8") as f:
                head = f.read(1200)
            for key, label in (("live", "今日新文献"), ("historical", "历史补回"), ("waiting", "等待决定")):
                m = re.search(r"> " + label + r"：(\d+)", head)
                if m:
                    overview[key] = int(m.group(1))
        except OSError:
            pass

    state = {
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        "overview": overview,
        "project": "personal_research_literature_radar",
        "metrics": metrics,
        "database": {
            "works": conn.execute("SELECT COUNT(*) FROM works").fetchone()[0],
            "historical_works": conn.execute("SELECT COUNT(*) FROM works WHERE historical_discovery=1").fetchone()[0],
            "latest_recommendation_priorities": priorities,
            "reading_stages": stages,
        },
        "a_workflow": {**a_state, "queue_pending": _queue_count(A_QUEUE_PATH), "queue_path": A_QUEUE_PATH},
        "queues": {
            "live_must_pending": q["live_must"]["pending"],
            "historical_recovery_pending": q["historical_recovery"]["pending"],
            "weekly_pending": q["weekly"]["pending"],
            "blocked_pdf": q["blocked_pdf"],
        },
        "vault_notes": vault_notes,
        "dedup_pending": conn.execute("SELECT COUNT(*) FROM dedup_review WHERE status='pending'").fetchone()[0],
        "latest_source_failures": [
            {"source": r["source"], "error": r["error"] or "", "fetched_at": r["fetched_at"] or ""}
            for r in latest_source_failures
        ],
        "last_run": dict(last_run) if last_run else None,
    }
    if state["last_run"]:
        state["last_run"].pop("summary_json", None)
    return state


def _next_actions(state):
    m = state.get("metrics", {})
    actions = []
    if m.get("live_must_backlog"):
        actions.append(f"执行 live MUST：{m['live_must_backlog']} 篇（`a-job --kind live_must`）")
    if m.get("historical_recovery_backlog"):
        actions.append(f"后台补 historical recovery A：{m['historical_recovery_backlog']} 篇（`a-job --kind historical_recovery`）")
    if m.get("blocked_pdf"):
        actions.append(f"补全文：{m['blocked_pdf']} 篇 MUST 被 PDF 阻塞（单独保留，不伪造）")
    if m.get("user_inbox_backlog") or m.get("package_pending"):
        actions.append(f"用户待决定：打开每日总览（`每日文献/`）勾选，或 `sync-overview` 后 `decide`（_文献包 {m.get('package_pending', 0)} 篇）")
    if not actions:
        actions.append("后台无 backlog；运行 daily 持续观察。")
    return actions


def render_handoff(state):
    m = state["metrics"]
    failures = state["latest_source_failures"]
    lines = [
        "# Literature Agent — 实时接手说明",
        "",
        f"> 自动更新：{state['updated_at']}。机器状态见 `PROJECT_STATE.json`。",
        "",
        "## 系统成功的标准",
        "",
        "**用户每天需要处理的东西少，而且都值得看。**",
        "",
        f"> workflow_schema_version: {WORKFLOW_SCHEMA_VERSION}",
        "",
        "## 后台 vs 前台",
        "",
        "- 后台自动：collect → dedup → screen → PDF/SI → A job 准备（live MUST 优先，historical 空闲补）→ digest；A 的生成由 Codex/ChatGPT executor 完成（`a-job` 只是生成任务清单，不等于已执行 A）",
        "- **Obsidian 每日总览是用户唯一日常前台**：`Literatures/每日文献/YYYY-MM-DD 文献总览.md`（vault 内直接生成，无日期子层）",
        "- 自动完成的 A 正式存放：`Literatures/每日文献/_文献包/<work_id>/`（A.md + paper.pdf），不再投递 `0_待处理`",
        "- `0_待处理` 只表示用户明确选择“稍后”的论文；`1_处理中`=read；`00-已阅`=ignore",
        "- 前台只有：打开每日总览 → 勾选决定/标记/批注 → `sync-overview`（或 daily 自动同步）；`save_zotero`、`competitor` 是可叠加标签",
        "- 定时：每天 07:00 / 08:00 / 09:00 / 10:00 各跑一次 `daily`（launchd，当天成功一次后自动跳过，失败重试）；距上次成功 >1 天自动拉大窗口，>7 天自动 historical-replay",
        "- 存 Zotero：勾选后 `zotero-export` 生成 `data/queues/zotero_export.bib`（daily 自动刷新），拖入 Zotero 即自动建档，无需回浏览器",
        "",
        "## 今日状态",
        "",
        f"- 每日总览：`{state['overview']['path']}`（今日新文献 {state['overview']['live']}｜历史补回 {state['overview']['historical']}｜等待决定 {state['overview']['waiting']}）",
        "",
        f"- 今天必须知道（live MUST）：待 A {m['live_must_backlog']} 篇｜A 完成率 "
        f"{round((m['live_must_a_completion_rate'] or 0) * 100)}%（{m['live_must_completed']}/{m['live_must_total']}）",
        f"- 已完成 A、等你决定：{m.get('package_pending', 0)} 篇（每日总览 / _文献包）",
        f"- 稍后处理（0_待处理）：{m['user_inbox_backlog']} 篇",
        f"- 本周候选：{m['weekly_candidates']} 篇",
        f"- historical recovery A backlog：{m['historical_recovery_backlog']} 篇（后台补，不阻塞 live）",
        f"- blocked PDF：{m['blocked_pdf']} 篇（单独保留，不伪造）",
        "",
        "## 三个队列",
        "",
        "| 队列 | 内容 | A 是否自动 |",
        "|---|---|---|",
        "| live_must | 今天新发现的 MUST | 立即执行 A |",
        "| historical_recovery | 历史回放找回的重要论文 | 空闲时后台补 A，不计入今日 MUST |",
        "| weekly | 本周候选 | 不自动执行 A |",
        "",
        "## A worker 协议",
        "",
        "- `python3 literature_agent.py a-job --kind all` 生成 `a_jobs/<job_id>.json` 任务清单。",
        "- 一次任务自动逐篇消费整个队列：读 PDF → 按 A Skill 写 A → `a-complete` → 投递 `每日文献/_文献包/<work_id>/`，不逐篇等确认。",
        "- 模板不算完成；PDF 无法读取/SI 缺失如实标注，不伪造。",
        "- `a-job-status` 查看逐篇进度；全部完成后 `handoff` 刷新。",
        "",
        "## 决定与标签（分离）",
        "",
        "- disposition（互斥，只选一个）：`python3 literature_agent.py decide <work_id> read|later|ignore`",
        "- flags（可与 disposition 同时存在）：`python3 literature_agent.py flag <work_id> save_zotero|competitor [--unset]`",
        "- 例：同一篇论文可以同时是 read + save_zotero + competitor。",
        "",
        "## 核心指标（最新）",
        "",
        f"- 今日 live MUST 数：{m['live_must_total']}｜待 A backlog：{m['live_must_backlog']}",
        f"- live MUST → A 完成率：{m['live_must_completed']}/{m['live_must_total']}"
        f"（{round((m['live_must_a_completion_rate'] or 0) * 100)}%）",
        f"- A 平均完成时间：{m['avg_a_completion_hours'] if m['avg_a_completion_hours'] is not None else '暂无'} 小时",
        f"- blocked PDF：{m['blocked_pdf']}",
        f"- 用户 accept/read 比例：{round((m['user_accept_ratio'] or 0) * 100)}%（{m['user_processing']} 篇处理中 / 共决定 {m['user_decided_total']}）",
        f"- ignore 比例：{round((m['user_ignore_ratio'] or 0) * 100)}%",
        f"- historical backlog：{m['historical_recovery_backlog']} 篇",
        f"- 用户待决定（_文献包）：{m.get('package_pending', 0)} 篇｜稍后（0_待处理）：{m['user_inbox_backlog']} 篇",
        "",
        "## 现在应继续做什么",
        "",
    ]
    for action in _next_actions(state):
        lines.append(f"- {action}")
    lines += ["", "## A/B/C/D 衔接契约", ""]
    lines += [
        "1. A：只基于实际取得的正文/SI 生成完整建档；完成后安全投递到 `每日文献/_文献包/<work_id>/`，不得把模板当完成。",
        "2. 用户决定 `read`：笔记进入 `1_处理中`，随后可围绕原文持续问答；B 只追加本轮新增讨论总结。",
        "3. C：融合 A 与历次 B，形成成熟笔记；只有用户明确确认后才移动到 `2-可调用`。",
        "4. D：从成熟 C 派生论文、汇报或公开内容；不得反向改写 C 的事实底稿。",
        "5. `later` 从 `_文献包` 移入 `0_待处理`；`read` 移入 `1_处理中`；`ignore` 移到 `00-已阅`；竞品和 Zotero 是独立标签/动作。",
        "",
        "## 当前可靠性约束",
        "",
        "- 同批次 arXiv/DOI 会实时刷新去重索引；arXiv 版本号保留并检测更新。",
        "- PDF 必须是有效 `%PDF-` 且达到最小体积；HTML challenge 不得冒充全文，失败可重试。",
        "- coverage 同时报告 observed recall 与 inferred discoverability，不能把“理论可发现”写成“已发现”。",
        "- Obsidian 投递和移动不覆盖已有文件；A/B/C/D 阶段移动遵守用户明确决定。",
        "",
        "## 目录地图",
        "",
        "- `literature_agent.py`：唯一 CLI 与 daily 总入口。",
        "- `la_queues.py`：live_must / historical_recovery / weekly 三队列。",
        "- `la_aworker.py` + `a_jobs/`：A worker 任务清单与消费协议。",
        "- `papers/`：PDF/SI/A 草稿工作区；只有通过内容校验的 A 才算完成。",
        "- `daily/`、`weekly/`、`monthly/`：生成的简报（daily 为 v4 用户向格式）。",
        "- `每日文献/`（vault）：用户唯一日常前台总览；`_ChatGPT交接/` 为临时交接区。",
        "- `automation/`：定时运行入口；`logs/`：运行日志；`backups/`：修改前与运行备份。",
        "- `a_queue.jsonl`：A 执行队列机器清单；`PROJECT_STATE.json`：机器接手状态。",
        "- 顶层旧 `V*_REPORT.md`、测试报告属于历史快照，不代表当前运行契约。",
        "",
        "## 当前来源异常",
        "",
    ]
    if failures:
        for failure in failures:
            lines.append(f"- {failure['source']}（{failure['fetched_at']}）：{failure['error'][:180] or 'unknown error'}")
    else:
        lines.append("- 当前各来源最近一次运行均未标记失败。")
    return "\n".join(lines) + "\n"


def run_handoff(conn=None):
    from la_db2 import connect

    own = conn is None
    conn = conn or connect()
    state = collect_state(conn)
    _atomic_text(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _atomic_text(HANDOFF_PATH, render_handoff(state))
    _atomic_text(
        CODEX_HANDOFF_PATH,
        "# Codex 接手入口\n\n"
        "唯一实时状态：[`docs/HANDOFF.md`](docs/HANDOFF.md)；"
        "机器状态：[`state/PROJECT_STATE.json`](state/PROJECT_STATE.json)。\n\n"
        "开始任何工作前先读 `README.md` 与 `docs/HANDOFF.md`；完成改动后运行 "
        "`python3 literature_agent.py handoff`。\n",
    )
    if own:
        conn.close()
    return {"handoff": HANDOFF_PATH, "state": STATE_PATH, "updated_at": state["updated_at"]}


if __name__ == "__main__":
    print(json.dumps(run_handoff(), ensure_ascii=False, indent=2))
