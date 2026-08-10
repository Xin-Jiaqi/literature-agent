#!/usr/bin/env python3
"""literature-agent 来源注册表（sources.json 读取与旧式 SOURCES 结构兼容层）。

role: discovery | metadata_update | preprint | journal
priority 仅用于同等级排序与 digest 展示，不参与 MUST/WEEKLY 判定。
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))


def registry_path():
    """运行时解析注册表路径（env 优先），避免 import 时绑定导致测试/迁移环境失效。"""
    from literature_config import DATA_ROOT
    return os.environ.get("LITAGENT_REGISTRY", os.path.join(DATA_ROOT, "sources.json"))

# 旧式硬编码兜底（sources.json 缺失时使用，保证脚本永不因配置丢失崩溃）
FALLBACK_SOURCES = {
    "arxiv": {"kind": "api", "url": "https://export.arxiv.org/api/query",
              "params": {"cats": ["cond-mat.mtrl-sci", "cond-mat.mes-hall", "cond-mat.str-el", "physics.comp-ph"],
                         "days": 7, "max_results": 300}},
    "aps_accepted": {"kind": "rss", "feeds": {
        "Physical Review Letters": "https://feeds.aps.org/rss/accepted/prl.xml",
        "Physical Review B": "https://feeds.aps.org/rss/accepted/prb.xml"}},
    "aps_published": {"kind": "rss", "feeds": {
        "Physical Review Letters": "https://feeds.aps.org/rss/recent/prl.xml",
        "Physical Review B": "https://feeds.aps.org/rss/recent/prb.xml"}},
    "npj_rss": {"kind": "rss", "feeds": {
        "npj Computational Materials": "https://www.nature.com/npjcompumats.rss"}},
    "nature_rss": {"kind": "rss", "feeds": {
        "Nature Communications": "https://www.nature.com/ncomms.rss"}},
    "acs_rss": {"kind": "rss", "feeds": {
        "Nano Letters": "https://pubs.acs.org/action/showFeed?type=etoc&feed=rss&jc=nalefd",
        "ACS Nano": "https://pubs.acs.org/action/showFeed?type=etoc&feed=rss&jc=ancac3"}},
}


def load_registry(path=None):
    path = path or registry_path()
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {"schema_version": 0, "sources": []}


def build_sources_config(registry=None):
    """把 sources.json 转成旧式 SOURCES dict（literature_agent.py 兼容）。"""
    registry = registry if registry is not None else load_registry()
    out = {}
    for s in registry.get("sources", []):
        sid = s.get("id")
        if not sid:
            continue
        cfg = {"kind": s.get("type", "rss"), "role": s.get("role", "journal"),
               "priority": s.get("priority", 50), "enabled": bool(s.get("enabled", False)),
               "supports_backfill": bool(s.get("supports_backfill", False)),
               "needs_abstract_enrichment": bool(s.get("needs_abstract_enrichment", False))}
        if s.get("type") == "api":
            cfg["url"] = s.get("endpoint", "")
            cfg["params"] = s.get("params", {})
        feeds = s.get("feeds", [])
        if feeds:
            cfg["feeds"] = {f["journal"]: f["url"] for f in feeds}
            cfg["parsers"] = {f["journal"]: f.get("parser", "rss1") for f in feeds}
            cfg["feed_urls"] = {f["journal"]: {"url": f["url"], "parser": f.get("parser", "rss1")} for f in feeds}
        if s.get("journal"):
            cfg["journal"] = s["journal"]
        out[sid] = cfg
    return out


def enabled_sources(registry=None):
    """返回 [(source_id, cfg), ...]，按 priority 升序，只含 enabled。"""
    registry = registry if registry is not None else load_registry()
    items = []
    for s in registry.get("sources", []):
        if s.get("enabled") and s.get("id"):
            cfg = build_sources_config({"sources": [s]})[s["id"]]
            items.append((s["id"], cfg))
    items.sort(key=lambda x: x[1].get("priority", 99))
    return items


def source_meta(registry=None):
    """返回 {source_id: {publisher, journal, role, priority, enabled, blocked, notes}}，用于 doctor/digest。"""
    registry = registry if registry is not None else load_registry()
    out = {}
    for s in registry.get("sources", []):
        health = s.get("health", {})
        out[s["id"]] = {
            "publisher": s.get("publisher", ""),
            "journal": s.get("journal"),
            "role": s.get("role", "journal"),
            "priority": s.get("priority", 50),
            "enabled": bool(s.get("enabled", False)),
            "blocked": health.get("blocked"),
            "blocked_detail": health.get("detail", ""),
            "notes": s.get("notes", ""),
            "feeds": [f.get("journal") for f in s.get("feeds", [])],
        }
    return out


def summary(registry=None):
    registry = registry if registry is not None else load_registry()
    srcs = registry.get("sources", [])
    enabled = [s for s in srcs if s.get("enabled")]
    blocked = [s for s in srcs if not s.get("enabled")]
    return {
        "total": len(srcs),
        "enabled": len(enabled),
        "blocked": len(blocked),
        "blocked_ids": [s["id"] for s in blocked],
        "enabled_ids": [s["id"] for s in enabled],
    }
