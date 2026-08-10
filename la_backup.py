#!/usr/bin/env python3
"""literature-agent v1 backup：备份数据库与配置到 backups/YYYY-MM-DD-HHMM/，保留最近 N 份。"""
import os
import shutil
from datetime import datetime

from literature_config import (PROJECT_ROOT, BACKUP_ROOT, DATA_ROOT, GOLD_ROOT,
                               QUEUES_ROOT, DOCS_ROOT, LOGS_ROOT)
KEEP = 10

FILES = [
    os.path.join(DATA_ROOT, "research_profile.md"),
    os.path.join(DATA_ROOT, "zotero_taxonomy.md"),
    os.path.join(DATA_ROOT, "sources.json"),
    os.path.join(GOLD_ROOT, "screening_gold.jsonl"),
    os.path.join(DOCS_ROOT, "a_stage_rules.md"),
    os.path.join(QUEUES_ROOT, "zotero_queue.jsonl"),
]


def run_backup(conn=None, keep=KEEP, log=None):
    if log is None:
        log = lambda *a: None
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    dest = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(dest)
    copied = []
    # 数据库：SQLite online backup（一致性快照）
    if conn is not None:
        db_dest = os.path.join(dest, "literature.db")
        conn.backup(__import__("sqlite3").connect(db_dest))
        copied.append("literature.db")
    elif os.path.exists(os.path.join(PROJECT_ROOT, "literature.db")) or \
            os.path.exists(os.path.join(DATA_ROOT, "literature.db")):
        from la_db import connect
        c = connect()
        db_dest = os.path.join(dest, "literature.db")
        c.backup(__import__("sqlite3").connect(db_dest))
        c.close()
        copied.append("literature.db")
    for p in FILES:
        if os.path.exists(p):
            shutil.copy2(p, dest)
            copied.append(os.path.basename(p))
    # 日志目录（最近 7 天，避免备份过多）
    if os.path.isdir(LOGS_ROOT):
        log_dest = os.path.join(dest, "logs")
        os.makedirs(log_dest)
        n = 0
        for f in sorted(os.listdir(LOGS_ROOT)):
            if f.startswith("run-") and n < 7:
                shutil.copy2(os.path.join(LOGS_ROOT, f), log_dest)
                n += 1
        copied.append(f"logs/{n} files")
    # 轮转：只保留最近 keep 份
    dirs = sorted(d for d in os.listdir(BACKUP_ROOT) if os.path.isdir(os.path.join(BACKUP_ROOT, d)))
    for d in dirs[:-keep] if len(dirs) > keep else []:
        shutil.rmtree(os.path.join(BACKUP_ROOT, d))
        log("backup", f"pruned {d}")
    log("backup", f"backup -> {dest}: {copied}")
    return dest, copied
