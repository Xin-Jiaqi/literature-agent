#!/usr/bin/env python3
"""机器相关路径唯一配置入口（PATH CENTRALIZATION）。

优先级：
1. 环境变量（LITAGENT_*，测试/迁移/临时切换用）
2. config/local.json（唯一用户配置文件，只存本机路径）
3. 本文件内的模块相对默认值（不依赖具体机器）

规则：任何模块不得再自行硬编码个人机器绝对路径。
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
LOCAL_CONFIG_PATH = os.path.join(BASE, "config", "local.json")

# 兜底默认值（机器相关值已迁移到 config/local.json，此处仅防配置缺失；
# 使用通用占位，不携带任何个人路径）
_LEGACY_VAULT_ROOT = os.path.expanduser("~/Literatures")


def _load_local():
    try:
        with open(LOCAL_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_LOCAL = _load_local()


def _resolve(key, env_name, default):
    v = os.environ.get(env_name)
    if v:
        return v
    v = _LOCAL.get(key)
    if v:
        return v
    return default


# 唯一集中定义（模块不得再各自写死）
PROJECT_ROOT = _resolve("project_root", "LITAGENT_PROJECT_ROOT", BASE)
VAULT_ROOT = _resolve("literatures_root", "LITAGENT_VAULT_ROOT", _LEGACY_VAULT_ROOT)
DAILY_OVERVIEW_ROOT = _resolve("daily_overview_root", "LITAGENT_OVERVIEW_DIR",
                               os.path.join(VAULT_ROOT, "每日文献"))
A_SKILL_PATH = _resolve(
    "a_skill_path", "LITAGENT_A_SKILL",
    os.path.join(_LEGACY_VAULT_ROOT, "AI system", "A. 重要文献快速建档 Skill.md"),
)

# 派生目录（固定布局，按 PROJECT_ROOT 推导；机器相关覆盖一律走 config/local.json）
DATA_ROOT = os.path.join(PROJECT_ROOT, "data")
QUEUES_ROOT = os.path.join(DATA_ROOT, "queues")
GOLD_ROOT = os.path.join(DATA_ROOT, "gold")
WORKSPACE_ROOT = os.path.join(PROJECT_ROOT, "workspace")
PAPERS_ROOT = os.path.join(WORKSPACE_ROOT, "papers")
REPORT_ROOT = os.path.join(PROJECT_ROOT, "reports")
REPORT_AUDIT_ROOT = os.path.join(REPORT_ROOT, "audit")
REPORT_HISTORICAL_ROOT = os.path.join(REPORT_ROOT, "historical")
RUNTIME_ROOT = os.path.join(PROJECT_ROOT, "runtime")
LOGS_ROOT = os.path.join(RUNTIME_ROOT, "logs")
CACHE_ROOT = os.path.join(RUNTIME_ROOT, "cache")
DOCS_ROOT = os.path.join(PROJECT_ROOT, "docs")
STATE_ROOT = os.path.join(PROJECT_ROOT, "state")
BACKUP_ROOT = os.path.join(PROJECT_ROOT, "backups")
ARCHIVE_ROOT = os.path.join(PROJECT_ROOT, "archive")
IMPORT_ROOT = os.path.join(DATA_ROOT, "imports")
DB_PATH = _resolve("db_path", "LITAGENT_DB", os.path.join(DATA_ROOT, "literature.db"))
