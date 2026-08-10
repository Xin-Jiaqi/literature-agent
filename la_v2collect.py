#!/usr/bin/env python3
"""literature-agent V2 collect 集成：把 V2 API 源接入 run_collect 循环。

源：
- crossref_journals：blocked 期刊 Crossref fallback
- openalex_author_watch：tracked 作者新工作
- openalex_citation_watch：引用网络雷达
任何源失败只记录，不影响主流程。
"""
from datetime import date, timedelta


def collect_v2_source(conn, source, today, log, days, index=None):
    if source == "crossref_journals":
        from la_discovery import collect_crossref_journals
        records, per = collect_crossref_journals(conn, today, log=log, days=days or 7)
        return records, per
    if source == "openalex_author_watch":
        from la_tracked import author_watch
        s = author_watch(conn, days=days or 7, log=log, index=index)
        failed = bool(s["failed"])
        per = {"api": {"ok": not failed, "items": s["checked"], "window": s["new"],
                       "latency_ms": 0,
                       "error": ("failed authors: " + ", ".join(s["failed"])) if failed else None,
                       "detail": s["per_author"],
                       "ingested": {k: s.get(k, 0) for k in ("new", "updated", "noop", "requeued")}}}
        return [], per
    if source == "openalex_citation_watch":
        from la_citation import citation_watch
        s = citation_watch(conn, days=days or 7, log=log, index=index)
        failed = bool(s["failed_seeds"])
        per = {"api": {"ok": not failed, "items": s["citing_found"], "window": s["new"],
                       "latency_ms": 0,
                       "error": ("failed seeds: " + ", ".join(s["failed_seeds"])) if failed else None,
                       "detail": s["cited_map"],
                       "ingested": {k: s.get(k, 0) for k in ("new", "updated", "noop", "requeued")}}}
        return [], per
    return [], {}
