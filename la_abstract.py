#!/usr/bin/env python3
"""literature-agent v1 摘要补全策略。

L0：RSS 摘要足够 → 直接筛
L1：初判 RADAR/DROP → 不补
L2：初判 MUST/WEEKLY 或置信度低 → 自动尝试官方页面完整 abstract
L3：官方页面失败 → Crossref 官方 bibliographic metadata（只补 metadata/abstract）
仍无法获取 → confidence 保持 中/低，不自动升级。
"""
import html
import json
import re
import time
import urllib.parse
import urllib.request

import la_db
from literature_agent import fetch_text, strip_tags


def _meta_from_html(html_txt):
    """从官方文章页提取 abstract meta。返回 (abstract|None, truncated|False)。"""
    for pat in (r'<meta name="dc\.description" content="([^"]+)"',
                r'<meta property="og:description" content="([^"]+)"',
                r'<meta name="citation_abstract" content="([^"]+)"',
                r'<meta name="description" content="([^"]+)"',
                r'<meta property="description" content="([^"]+)"'):
        m = re.search(pat, html_txt, re.I)
        if m and m.group(1).strip():
            return html.unescape(m.group(1)).strip(), False
    # 也尝试 JSON-LD
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html_txt, re.S):
        try:
            data = json.loads(m.group(1))
            for node in (data if isinstance(data, list) else [data]):
                if isinstance(node, dict) and node.get("abstract"):
                    return strip_tags(node["abstract"]), False
        except Exception:
            continue
    return None, False


def _crossref_abstract(doi, timeout=40):
    """Crossref API（官方 bibliographic metadata）。"""
    from la_v2common import http_get_json
    ok, data = http_get_json("https://api.crossref.org/works/" + urllib.parse.quote(doi), timeout=timeout)
    if not ok:
        raise RuntimeError(str(data)[:300])
    msg = data.get("message", {})
    abs_raw = msg.get("abstract")
    if not abs_raw:
        return None, False
    abs_txt = strip_tags(abs_raw)
    return abs_txt, False


def enrich_work_abstract(conn, work_id, log=None):
    """L2/L3 补全：返回 (ok, source_level, abstract, truncated)。失败记 errors，不升级。"""
    if log is None:
        log = lambda *a: None
    ws = conn.execute("SELECT * FROM work_sources WHERE work_id=? ORDER BY length(abstract) DESC LIMIT 1",
                      (work_id,)).fetchone()
    if not ws:
        return False, "no_source", "", False
    current = ws["abstract"] or ""
    if len(current) > 600 and not ws["abstract_truncated"]:
        return True, "l0", current, False
    work = conn.execute("SELECT doi, title FROM works WHERE work_id=?", (work_id,)).fetchone()
    doi = (work["doi"] if work else None)
    url = ws["url"] or ""

    # L2: 官方页面
    if url:
        last_err = None
        for attempt in range(3):
            try:
                page = fetch_text(url, timeout=40)
                abs_txt, trunc = _meta_from_html(page)
                if abs_txt and len(abs_txt) > len(current):
                    _update(conn, work_id, ws, abs_txt, trunc)
                    log("enrich", f"{work_id} L2 官方页面补全 abstract ({len(abs_txt)} chars)")
                    return True, "l2", abs_txt, trunc
                break
            except Exception as e:
                last_err = str(e)[:300]
                time.sleep(1.0 * (attempt + 1))
        if last_err:
            la_db.record_error(conn, ws["source"], work_id, "enrich_l2", last_err, retryable=True)

    # L3: Crossref
    if doi:
        try:
            abs_txt, trunc = _crossref_abstract(doi)
            if abs_txt and len(abs_txt) > len(current):
                _update(conn, work_id, ws, abs_txt, trunc)
                log("enrich", f"{work_id} L3 Crossref 补全 abstract ({len(abs_txt)} chars)")
                return True, "l3", abs_txt, trunc
        except Exception as e:
            la_db.record_error(conn, ws["source"], work_id, "enrich_l3", str(e)[:300], retryable=True)

    return False, "l0", current, bool(ws["abstract_truncated"])


def _update(conn, work_id, ws, abstract, truncated):
    conn.execute("UPDATE work_sources SET abstract=?, abstract_truncated=? WHERE work_id=? AND source=? AND version=?",
                 (abstract, int(bool(truncated)), work_id, ws["source"], ws["version"] or ""))
