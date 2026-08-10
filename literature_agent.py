#!/usr/bin/env python3
"""
literature-agent v0 — 统一文献收集/筛选/全文处理/每日简报流水线。

命令：
  python3 literature_agent.py bootstrap          # 把测试阶段历史迁移为“已消费”，建立 literature.db
  python3 literature_agent.py collect            # 抓取全部官方来源，写入 works/work_sources
  python3 literature_agent.py screen             # 对 queued work 自动分级（MUST/WEEKLY/RADAR/DROP）
  python3 literature_agent.py screen --grade <work_id> <PRIORITY> --reason "..."
  python3 literature_agent.py process            # 处理本轮新 MUST：PDF + SI 检查 + A 建档目录
  python3 literature_agent.py digest             # 生成 daily/YYYY-MM-DD.md
  python3 literature_agent.py daily              # collect -> screen -> process -> digest（带容错）
  python3 literature_agent.py selftest           # 六项自检
  python3 literature_agent.py stats              # 数据库统计
  python3 literature_agent.py dump-queue         # 打印待筛选队列（便于人工复核）

数据：literature.db（works / sources / work_sources / screenings / processing_runs / processing / digests）
日志：logs/run-<run_id>.jsonl
"""
import argparse
import difflib
import hashlib
import html
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime

from la_db import SCHEMA_V1 as LA_SCHEMA_V1
from la_db import record_source_run, record_error, record_work_version, record_dedup_review
from la_dedup import find_matches as dedup_find_matches, DedupIndex
from la_priority import DROP, MUST, RADAR, WEEKLY, normalize_priority
from la_registry import build_sources_config, enabled_sources, load_registry
from literature_config import (A_SKILL_PATH, DB_PATH as _CFG_DB_PATH, DATA_ROOT,
                               LOGS_ROOT, REPORT_ROOT, PAPERS_ROOT, ARCHIVE_ROOT)

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "tests"))
DB_PATH = os.environ.get("LITAGENT_DB", _CFG_DB_PATH)
LOG_DIR = os.path.join(LOGS_ROOT)
DAILY_DIR = os.path.join(REPORT_ROOT, "daily")
PAPERS_DIR = PAPERS_ROOT
PROFILE_PATH = os.path.join(DATA_ROOT, "research_profile.md")
TAXONOMY_PATH = os.path.join(DATA_ROOT, "zotero_taxonomy.md")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
ARXIV_CATS = ["cond-mat.mtrl-sci", "cond-mat.mes-hall", "cond-mat.str-el", "physics.comp-ph"]
ARXIV_DAYS = 7

SOURCES_CACHE = None


def get_sources():
    """从 sources.json 注册表构建来源配置；注册表缺失时退回旧式硬编码。"""
    global SOURCES_CACHE
    if SOURCES_CACHE is None:
        cfg = build_sources_config()
        if not cfg:
            from la_registry import FALLBACK_SOURCES
            cfg = dict(FALLBACK_SOURCES)
        SOURCES_CACHE = cfg
    return SOURCES_CACHE


SOURCES = get_sources()

RESEARCH_GROUPS = [
    "Yaguang Guo", "Xiaolong Zou", "Hongjun Xiang", "Wei Luo", "Shihan Deng", "Junyi Ji",
    "Andrew M. Rappe", "Steve M. Young", "Xiaofeng Qian", "Hua Wang",
    "Kristian S. Thygesen", "Mads Kruse", "Søren Pakdel", "Thomas Olsen", "Morten Gjerding",
    "Jian Zhou", "Qian Wang", "Qiang Sun", "Huasong Liu", "Ying Dai", "Yandong Ma",
    "Yong Xu", "Wenhui Duan", "Yugui Yao", "Jin Zhao", "Qijing Zheng",
    "Zhi-Ming Yu", "Weikang Wu", "Gui-Bin Liu", "Xiao-Ping Li", "Nianlong Zou",
]

HOT_MATERIAL_TAGS = {
    "c-MoS2": ["mos2", "molybdenum disulfide"], "c-hBN": ["h-bn", "hexagonal boron nitride", "hbn"],
    "c-Graphene": ["graphene"], "c-BlackP": ["black phosphorus", "phosphorene"],
    "c-In2Se3": ["in2se3", "indium selenide"], "c-InSe": ["inse"], "c-WTe2": ["wte2"],
    "c-PdSe2": ["pdse2"], "c-PdPSe": ["pdpse"], "c-CrI3": ["cri3"],
    "c-SnTe": ["snte"], "c-MoSSe": ["mosse"], "c-C3N": ["c3n", "c3n4"],
}

NS = {"rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
      "dc": "http://purl.org/dc/elements/1.1/",
      "prism": "http://prismstandard.org/namespaces/basic/2.0/",
      "rss": "http://purl.org/rss/1.0/",
      "content": "http://purl.org/rss/1.0/modules/content/",
      "atom": "http://www.w3.org/2005/Atom",
      "arxiv": "http://arxiv.org/schemas/atom"}


# ================= 基础工具 =================

def fetch(url, timeout=60, binary=False):
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/xml,text/xml,text/html,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def fetch_text(url, timeout=60):
    return fetch(url, timeout=timeout).decode("utf-8", "ignore")


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_id_now():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(re.sub(r"\s+", " ", s)).strip()


def clean_latex(s):
    s = re.sub(r"\$[^$]*\$", " ", s)
    s = re.sub(r"\\[a-zA-Z]+", " ", s)
    s = re.sub(r"\{|\}", " ", s)
    return s


def norm_title(t):
    t = strip_tags(t)
    t = clean_latex(t)
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def norm_arxiv_id(s):
    s = (s or "").strip().lower()
    m = re.match(r"((?:abs/)?(?:arxiv:)?\d{4}\.\d{4,5})(?:v\d+)?$", s)
    if m:
        return m.group(1)
    m = re.match(r"(\d{4}\.\d{4,5})(?:v\d+)?", s)
    return m.group(1) if m else s


def arxiv_version(s):
    m = re.search(r"v(\d+)$", (s or "").strip())
    return int(m.group(1)) if m else 1


def text_similarity(a, b):
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def slugify(title):
    s = clean_latex(strip_tags(title)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:70].rstrip("-")


# ================= 数据库 =================

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
  work_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  normalized_title TEXT NOT NULL,
  authors TEXT DEFAULT '',
  doi TEXT,
  arxiv_id TEXT,
  journal TEXT,
  status TEXT DEFAULT 'new',
  first_seen TEXT,
  latest_seen TEXT,
  version_changed INTEGER DEFAULT 0,
  queued INTEGER DEFAULT 0,
  consumed_history INTEGER DEFAULT 0,
  metadata_json TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  feed_url TEXT,
  kind TEXT,
  ok INTEGER,
  items INTEGER,
  new_works INTEGER DEFAULT 0,
  updated_works INTEGER DEFAULT 0,
  error TEXT,
  fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS work_sources (
  work_id TEXT NOT NULL,
  source TEXT NOT NULL,
  version TEXT DEFAULT '',
  title TEXT,
  url TEXT,
  abstract TEXT,
  abstract_truncated INTEGER DEFAULT 0,
  date TEXT,
  PRIMARY KEY (work_id, source, version)
);
CREATE TABLE IF NOT EXISTS screenings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id TEXT NOT NULL,
  grade TEXT NOT NULL,
  reason TEXT,
  matched_profile TEXT,
  confidence TEXT,
  screened_at TEXT,
  source TEXT
);
CREATE TABLE IF NOT EXISTS processing_runs (
  run_id TEXT PRIMARY KEY,
  command TEXT,
  started_at TEXT,
  finished_at TEXT,
  status TEXT,
  summary_json TEXT
);
CREATE TABLE IF NOT EXISTS processing (
  work_id TEXT NOT NULL,
  run_id TEXT,
  pdf_status TEXT,
  pdf_path TEXT,
  si_status TEXT,
  si_path TEXT,
  a_status TEXT,
  a_path TEXT,
  notes TEXT,
  processed_at TEXT,
  PRIMARY KEY (work_id, run_id)
);
CREATE TABLE IF NOT EXISTS digests (
  date TEXT PRIMARY KEY,
  path TEXT,
  summary_json TEXT
);
"""


def connect(db_path=DB_PATH):
    db_path = os.environ.get("LITAGENT_DB") or db_path
    conn = sqlite3.connect(db_path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    conn.executescript(SCHEMA)
    conn.executescript(LA_SCHEMA_V1)
    try:
        from la_db2 import migrate
        migrate(conn)
    except Exception:
        pass
    return conn


def db_count(conn, table, where="1=1", args=()):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]


# ================= 来源解析 =================

def parse_arxiv_api(data):
    root = ET.fromstring(data)
    out = []
    for e in root.findall("atom:entry", NS):
        aid = e.find("atom:id", NS)
        if aid is None or not aid.text:
            continue
        base = norm_arxiv_id(aid.text.split("/abs/")[-1])
        ver = arxiv_version(aid.text.split("/abs/")[-1])
        title = strip_tags(e.findtext("atom:title", default="", namespaces=NS))
        abstract = strip_tags(e.findtext("atom:summary", default="", namespaces=NS))
        authors = "; ".join(a.findtext("atom:name", default="", namespaces=NS)
                            for a in e.findall("atom:author", NS) if a.findtext("atom:name", default="", namespaces=NS))
        cats = [c.get("term", "") for c in e.findall("atom:category", NS)]
        pub = e.findtext("atom:published", default="", namespaces=NS)[:10]
        rec = {"title": title, "authors": authors, "arxiv_id": base, "version": f"v{ver}",
               "abstract": abstract, "abstract_truncated": False, "date": pub,
               "url": f"https://arxiv.org/abs/{base}", "journal": None,
               "categories": cats, "doi": None}
        out.append(rec)
    return out


def _el(item, tag):
    el = item.find(tag, NS)
    return (el.text or "").strip() if el is not None and el.text else ""


def _clean_aps_abstract(desc_raw):
    m = re.match(r"^Author\(s\):.*?(?:<br\s*/?>|</p>|</span>)\s*(.*)$", desc_raw, re.S)
    if m:
        desc_raw = m.group(1)
    desc_raw = re.sub(r"\[[^\]]*\]\s*Published[^<]*$", "", desc_raw)
    return strip_tags(desc_raw)


def parse_rss1(data, journal_prefix=""):
    """RSS 1.0 (RDF)。返回统一 rec 列表。"""
    root = ET.fromstring(data)
    out = []
    for it in root.findall("rss:item", NS):
        title = strip_tags(_el(it, "rss:title") or _el(it, "dc:title"))
        link = _el(it, "rss:link") or _el(it, "prism:url")
        doi = (_el(it, "prism:doi") or "").lower()
        if not doi:
            ident = _el(it, "dc:identifier")
            if ident.startswith("doi:"):
                doi = ident[4:].lower()
        desc = _el(it, "rss:description")
        enc = _el(it, "content:encoded")
        abstract, truncated = "", False
        if desc:
            abstract = _clean_aps_abstract(desc)
            if abstract.endswith("…"):
                truncated = True
        elif enc:
            abstract = strip_tags(enc)
        out.append({"title": title, "authors": _el(it, "dc:creator"), "doi": doi,
                    "version": "rss", "abstract": abstract, "abstract_truncated": truncated,
                    "date": _el(it, "dc:date")[:10], "url": link,
                    "journal": journal_prefix or _el(it, "prism:publicationName") or _el(it, "dc:source"),
                    "arxiv_id": None})
    return out


def nature_abstract_from_page(article_url, timeout=40):
    try:
        html_txt = fetch_text(article_url, timeout=timeout)
    except Exception:
        return None, False
    m = re.search(r'<meta name="dc.description" content="([^"]+)"', html_txt)
    if m:
        return html.unescape(m.group(1)).strip(), False
    m = re.search(r'<meta property="og:description" content="([^"]+)"', html_txt)
    if m:
        return html.unescape(m.group(1)).strip(), False
    return None, False


def parse_acs_rss(data, journal):
    """ACS feed（RSS 2.0，含 atom/guid）。字段尽力而为；当前环境被 Cloudflare 拦截，仅备用。"""
    root = ET.fromstring(data)
    out = []
    for it in root.findall(".//item"):
        title = strip_tags(it.findtext("title", default="", namespaces=NS))
        link = it.findtext("link", default="")
        guid = it.findtext("guid", default="")
        doi = ""
        m = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", guid + " " + link, re.I)
        if m:
            doi = m.group(0).lower()
        desc = strip_tags(it.findtext("description", default="", namespaces=NS))
        out.append({"title": title, "authors": "", "doi": doi, "version": "rss",
                    "abstract": desc, "abstract_truncated": desc.endswith("…"),
                    "date": it.findtext("pubDate", default="")[:16], "url": link,
                    "journal": journal, "arxiv_id": None})
    return out


# ================= work 身份与合并 =================

def first_author_surname(authors):
    if not authors:
        return ""
    first = authors.split(";")[0].split(",")[0].strip()
    parts = re.split(r"\s+", first)
    return parts[-1].lower() if parts else ""


def find_existing_work(conn, rec, index=None):
    """精确匹配返回已有 work_id；probable/manual 候选写入 dedup_review（不静默合并）。"""
    exact_wid, review = dedup_find_matches(conn, rec, index=index)
    return exact_wid, review


def _title_significant_change(old_title, new_title):
    return norm_title(old_title) != norm_title(new_title)


def _abstract_significant_change(old, new):
    if not old or not new:
        return False
    return text_similarity(old, new) < 0.6


def ingest_record(conn, rec, source, today, history=False, index=None):
    """把一条来源记录并入 works。返回 (work_id, action, changed)。"""
    wid, review_pairs = find_existing_work(conn, rec, index=index)
    changed = False
    version = rec.get("version") or ""
    if rec.get("arxiv_id") and not version:
        version = f"v{arxiv_version(rec['arxiv_id'])}"
    if rec.get("doi"):
        doi = rec["doi"].lower()
    else:
        doi = None

    if wid is None:
        wid = doi or (rec.get("arxiv_id") and f"arxiv:{norm_arxiv_id(rec['arxiv_id'])}") or f"t:{norm_title(rec['title'])[:64]}"
        queued = 0 if history else 1
        conn.execute(
            "INSERT OR IGNORE INTO works (work_id,title,normalized_title,authors,doi,arxiv_id,journal,"
            "status,first_seen,latest_seen,version_changed,queued,consumed_history) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (wid, rec["title"], norm_title(rec["title"]), rec.get("authors") or "", doi,
             norm_arxiv_id(rec["arxiv_id"]) if rec.get("arxiv_id") else None,
             rec.get("journal"), rec.get("status", "new"), today, today, 0, queued, 1 if history else 0))
        action = "new"
    else:
        w = conn.execute("SELECT * FROM works WHERE work_id=?", (wid,)).fetchone()
        action = "noop"
        updates = {"latest_seen": today}
        if len(rec["title"]) > len(w["title"] or ""):
            updates["title"] = rec["title"]
            updates["normalized_title"] = norm_title(rec["title"])
        if rec.get("doi") and not w["doi"]:
            updates["doi"] = doi
        if rec.get("arxiv_id") and not w["arxiv_id"]:
            updates["arxiv_id"] = norm_arxiv_id(rec["arxiv_id"])
        if rec.get("journal") and (not w["journal"] or w["journal"] == "arxiv"):
            updates["journal"] = rec["journal"]

        # 版本/状态迁移
        old_ver = w["status"]
        new_stage = None
        if source == "aps_accepted":
            new_stage = "accepted"
        elif source == "aps_published":
            new_stage = "published"
        elif source in ("npj_rss", "nature_rss", "acs_rss"):
            new_stage = "journal"
        elif source == "arxiv":
            new_stage = "arxiv"
        if new_stage:
            stage_rank = {"arxiv": 1, "accepted": 2, "published": 3, "journal": 3, "new": 0}
            if stage_rank.get(new_stage, 0) > stage_rank.get(w["status"], 0):
                updates["status"] = new_stage
                changed = True

        # 重入队规则
        requeue = False
        if source == "arxiv" and w["arxiv_id"] == norm_arxiv_id(rec["arxiv_id"]):
            old_versions = conn.execute(
                "SELECT version FROM work_sources WHERE work_id=? AND source='arxiv'",
                (wid,),
            ).fetchall()
            old_ver_n = max(
                [arxiv_version(r["version"]) for r in old_versions],
                default=(1 if w["arxiv_id"] else 0),
            )
            new_ver_n = arxiv_version(version or rec.get("arxiv_id"))
            if new_ver_n >= old_ver_n or not w["arxiv_id"]:
                old_abs = conn.execute(
                    "SELECT abstract, abstract_truncated FROM work_sources WHERE work_id=? AND source='arxiv' "
                    "ORDER BY version DESC LIMIT 1", (wid,)).fetchone()
                old_abs_txt = old_abs["abstract"] if old_abs else ""
                if (_title_significant_change(w["title"], rec["title"])
                        or _abstract_significant_change(old_abs_txt, rec.get("abstract") or "")):
                    requeue = True
                    updates["version_changed"] = 1
                    changed = True
        elif source in ("aps_published", "npj_rss", "nature_rss", "acs_rss"):
            if _title_significant_change(w["title"], rec["title"]):
                requeue = True
                updates["version_changed"] = 1
                changed = True
        if requeue and not w["queued"]:
            updates["queued"] = 1
            action = "requeued"
        elif action == "noop" and (set(updates) - {"latest_seen"}):
            action = "updated"
        elif action == "noop" and changed:
            action = "updated"

        sets = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE works SET {sets} WHERE work_id=?", (*updates.values(), wid))

    conn.execute(
        "INSERT OR REPLACE INTO work_sources (work_id,source,version,title,url,abstract,abstract_truncated,date) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (wid, source, version, rec["title"], rec.get("url") or "", rec.get("abstract") or "",
         int(bool(rec.get("abstract_truncated"))), rec.get("date") or today))
    meta = {"doi": doi, "arxiv_id": norm_arxiv_id(rec["arxiv_id"]) if rec.get("arxiv_id") else None,
            "journal": rec.get("journal"), "authors": rec.get("authors") or "", "url": rec.get("url") or "",
            "date": rec.get("date") or today}
    _record_version_snapshot(conn, wid, source, version, rec["title"], rec.get("abstract") or "", meta)
    if not history:
        for other_wid, kind, score, evidence in review_pairs:
            if other_wid != wid:
                record_dedup_review(conn, min(wid, other_wid), max(wid, other_wid), kind, score, evidence)
    if index is not None:
        index.refresh(conn, wid)
    return wid, action, changed


def _record_version_snapshot(conn, wid, source, version, title, abstract, meta):
    """记录 work 在 (source, version) 的摘要/元数据哈希快照；内容变化时标记。"""
    import hashlib
    new_abs = hashlib.sha256((abstract or "").encode("utf-8", "ignore")).hexdigest()[:16]
    new_meta = hashlib.sha256(json.dumps(meta, sort_keys=True, ensure_ascii=False).encode("utf-8", "ignore")).hexdigest()[:16]
    old = conn.execute("SELECT abstract_hash, metadata_hash, title FROM work_versions WHERE work_id=? AND source=? AND source_version=?",
                       (wid, source, version)).fetchone()
    changed = 0
    if old is not None:
        changed = int(old["abstract_hash"] != new_abs or (old["title"] or "") != (title or ""))
    record_work_version(conn, wid, source, version, title, abstract, meta, changed)


# ================= 收集器 =================

def collect_arxiv(conn, today, log, days=None):
    cfg = SOURCES.get("arxiv", {})
    params = cfg.get("params", {}) if cfg else {}
    cats = params.get("cats") or ARXIV_CATS
    days = days or params.get("days") or ARXIV_DAYS
    max_results = params.get("max_results", 300)
    endpoint = cfg.get("url") or "http://export.arxiv.org/api/query"
    start = (datetime.now().timestamp() - int(days) * 86400)
    start_iso = datetime.fromtimestamp(start).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen_ids, records = set(), []
    per_cat = {}
    for cat in cats:
        url = (endpoint + "?search_query=cat:%s&sortBy=submittedDate&sortOrder=descending"
               "&start=0&max_results=%d" % (cat, max_results))
        try:
            data = fetch(url, timeout=90)
            recs = parse_arxiv_api(data)
        except Exception as e:
            log("error", f"arxiv {cat} fetch failed: {e}")
            per_cat[cat] = {"ok": False, "items": 0, "error": str(e)[:200]}
            continue
        win = [r for r in recs if r["date"] >= start_iso[:10]]
        per_cat[cat] = {"ok": True, "items": len(recs), "window": len(win)}
        for r in win:
            if r["arxiv_id"] not in seen_ids:
                seen_ids.add(r["arxiv_id"])
                r["status"] = "arxiv"
                records.append(r)
    return records, per_cat


def parse_rss2(data, journal_prefix=""):
    """通用 RSS 2.0。返回统一 rec 列表。"""
    root = ET.fromstring(data)
    out = []
    for it in root.findall(".//item"):
        title = strip_tags(it.findtext("title", default="") or "")
        link = it.findtext("link", default="") or ""
        guid = it.findtext("guid", default="") or ""
        doi = ""
        m = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", guid + " " + link, re.I)
        if m:
            doi = m.group(0).lower()
        desc = strip_tags(it.findtext("description", default="") or "")
        pub = it.findtext("pubDate", default="") or ""
        mdate = re.search(r"\d{4}-\d{2}-\d{2}", pub)
        out.append({"title": title, "authors": "", "doi": doi, "version": "rss",
                    "abstract": desc, "abstract_truncated": bool(desc) and desc.endswith("…"),
                    "date": mdate.group(0) if mdate else "", "url": link,
                    "journal": journal_prefix or "", "arxiv_id": None})
    return out


def collect_rss_source(conn, source, today, log, days=None):
    """抓取一个 RSS 源（含多 feed），返回 (records, per_feed, latency_ms)。失败记录错误，不中断。"""
    records, per_feed = [], {}
    cfg = SOURCES.get(source, {})
    feeds = cfg.get("feeds", {}) if cfg else {}
    parsers = cfg.get("parsers", {}) if cfg else {}
    total_ms = 0
    for jname, url in feeds.items():
        parser = parsers.get(jname, "rss1")
        t0 = time.time()
        try:
            data = fetch(url, timeout=60)
            head = data[:2000].decode("utf-8", "ignore")
            if "Just a moment" in head or data[:15] == b"<html>" or "Radware Bot Manager" in head:
                raise RuntimeError("bot challenge page (blocked)")
            if parser == "acs":
                recs = parse_acs_rss(data, jname)
            elif parser == "rss2":
                recs = parse_rss2(data, journal_prefix=jname)
            else:
                recs = parse_rss1(data, journal_prefix=jname)
            if cfg.get("needs_abstract_enrichment"):
                missing_abs = [r for r in recs if not r["abstract"]]
                for r in missing_abs:
                    if r["url"]:
                        r["abstract"], r["abstract_truncated"] = nature_abstract_from_page(r["url"])
            per_feed[jname] = {"ok": True, "items": len(recs), "latency_ms": int((time.time() - t0) * 1000)}
            for r in recs:
                r["status"] = "journal"
                records.append(r)
        except Exception as e:
            per_feed[jname] = {"ok": False, "items": 0, "error": str(e)[:300],
                               "latency_ms": int((time.time() - t0) * 1000)}
            log("error", f"{source}/{jname} fetch failed: {e}")
        total_ms += per_feed[jname].get("latency_ms", 0)
    return records, per_feed, total_ms


def run_collect(conn=None, today=None, history=False, log=None, run_id=None, days=None):
    today = today or date.today().isoformat()
    own_conn = conn is None
    if own_conn:
        conn = connect()
    if log is None:
        log = lambda *a: None
    summary = {"sources": {}, "new": 0, "updated": 0, "requeued": 0, "noop": 0, "failed_sources": []}
    dedup_index = DedupIndex(conn)

    v2_api = {"crossref_journals", "openalex_author_watch", "openalex_citation_watch"}
    for source, cfg in enabled_sources():
        t0 = time.time()
        try:
            if source in v2_api:
                from la_v2collect import collect_v2_source
                records, per = collect_v2_source(conn, source, today, log, days, index=dedup_index)
                total_ms = int((time.time() - t0) * 1000)
            elif source == "arxiv":
                records, per = collect_arxiv(conn, today, log, days=days)
                total_ms = int((time.time() - t0) * 1000)
            else:
                records, per, total_ms = collect_rss_source(conn, source, today, log)
            for feed, st in per.items():
                key = f"{source}/{feed}"
                summary["sources"][key] = st
                if not st.get("ok"):
                    summary["failed_sources"].append(key)
                    try:
                        record_error(conn, source, None, "collect", st.get("error", ""), retryable=True)
                    except sqlite3.Error as e:
                        log("error", f"record_error failed: {e}")
            n_new = sum(int(st.get("ingested", {}).get("new", 0)) for st in per.values())
            n_upd = sum(int(st.get("ingested", {}).get("updated", 0)) for st in per.values())
            n_req = sum(int(st.get("ingested", {}).get("requeued", 0)) for st in per.values())
            n_noop = sum(int(st.get("ingested", {}).get("noop", 0)) for st in per.values())
            for rec in records:
                wid, action, changed = ingest_record(conn, rec, source, today, history=history, index=dedup_index)
                if action == "new":
                    n_new += 1
                elif action == "requeued":
                    n_req += 1
                elif action == "updated":
                    n_upd += 1
                else:
                    n_noop += 1
                log("ingest", f"{source} {action} {wid} :: {rec['title'][:70]}")
            summary["new"] += n_new
            summary["updated"] += n_upd
            summary["requeued"] += n_req
            summary["noop"] += n_noop
            for feed_idx, (feed, st) in enumerate(per.items()):
                feed_new = n_new if feed_idx == 0 else 0
                feed_updated = n_upd if feed_idx == 0 else 0
                conn.execute("INSERT INTO sources (name,feed_url,kind,ok,items,new_works,updated_works,error,fetched_at) "
                             "VALUES (?,?,?,?,?,?,?,?,?)",
                             (f"{source}/{feed}", SOURCES[source].get("url") or SOURCES[source].get("endpoint") or "",
                              SOURCES[source]["kind"], 1 if st.get("ok") else 0,
                              st.get("window") if "window" in st else st.get("items", 0),
                              feed_new, feed_updated, st.get("error"), now_iso()))
                try:
                    record_source_run(conn, source, feed, run_id, st.get("ok"), st.get("items", 0),
                                      st.get("latency_ms", 0), st.get("error"), feed_new)
                except sqlite3.Error as e:
                    log("error", f"record_source_run failed: {e}")
        except Exception as e:
            summary["failed_sources"].append(source)
            summary["sources"][source] = {"ok": False, "error": str(e)[:300]}
            try:
                record_error(conn, source, None, "collect", str(e)[:500], retryable=True)
            except sqlite3.Error as e2:
                log("error", f"record_error failed: {e2}")
            try:
                record_source_run(conn, source, "", run_id, False, 0, int((time.time() - t0) * 1000), str(e)[:300])
            except sqlite3.Error as e2:
                log("error", f"record_source_run failed: {e2}")
            log("error", f"{source} FAILED: {e}")
    conn.commit()
    if own_conn:
        conn.close()
    return summary


# ================= 筛选分类器 =================

# 关键词表：profile tag -> 关键词列表
KW = {
    "P1-fqfe": ["fractional quantum ferroelectric", "fractional ferroelectric", "quantum ferroelectric",
                "ferroelectricity quantization", "quantized polarization", "polarization quantum"],
    "P1-injection": ["injection current", "circular photogalvanic", "circular injection", "injection photocurrent"],
    "P1-wannier": ["full position", "position matrix", "wannier interpolation", "wannier"],
    "P1-neumann": ["neumann", "generalized neumann", "polarization quantization"],
    "P2-sliding": ["sliding ferroelectric", "sliding ferroelectricity", "interlayer sliding", "sliding-induced",
                   "stacking ferroelectric", "sliding multiferroic", "interlayer registry", "slid ferroelectric"],
    "P2-bpve": ["shift current", "shift conductivity", "bulk photovoltaic", "photogalvanic", "bpve",
                "shift photocurrent", "photovoltaic effect", "photocurrent"],
    "P2-stack": ["interlayer stacking", "stacking order", "homobilayer", "heterobilayer", "registry",
                 "twisted bilayer", "stacking engineering", "stacking-dependent", "layer group"],
    "P3-switch": ["polarization switching", "polarization reversal", "polarization flip", "multistate polarization",
                  "ferroelectric switching", "switchable", "switching of the", "photocurrent switching",
                  "nonsynchronous", "non-synchronous"],
    "P4-theory": ["sum rule", "rectification", "quantized shift", "quantization of", "second-order response",
                  "nonlinear response", "nonlinear optical", "nonlinear optics", "optical rectification",
                  "second harmonic", "shg", "third-order", "nonlinear conductivity", "nonlinear hall"],
    "P4-method": ["first-principles", "ab initio", "density functional", "gw-bse", "bethe-salpeter",
                  "tight-binding", "tb model", "berry phase", "berry curvature", "quantum metric",
                  "wannier", "kubo", "linear response"],
    "adj-magnetic": ["altermagnet", "altermagnetism", "skyrmion", "magnetic space group", "spin group",
                     "magnetoelectric", "spin splitting", "magnon", "magnetism", "magnet", "spin texture",
                     "spin-polarized", "spin current", "antiferromagnet", "ferromagnet"],
    "adj-multiferroic": ["multiferroic"],
    "adj-topology": ["topological", "topology", "chern", "berry phase", "berry curvature", "quantum geometry"],
    "adj-exciton": ["exciton", "excitonic", "bound exciton", "biexciton"],
    "adj-valley": ["valley", "valleytronic"],
    "adj-ml": ["machine learning", "high-throughput", "high throughput", "neural network", "deep learning",
               "database", "descriptor", "bayesian optimization", "active learning", "large language model"],
    "adj-2d": ["monolayer", "bilayer", "two-dimensional", "2d material", "van der waals", "heterostructure",
               "vdw", "few-layer", "2d "],
    "adj-superconductivity": ["superconduct", "pair density wave", "kondo"],
    "adj-quantum-info": ["quantum error", "quantum computation", "qubit", "quantum information", "quantum metrology",
                         "quantum algorithm", "quantum circuit", "quantum teleportation", "quantum key"],
    "adj-high-energy": ["quark", "gluon", "neutrino", "hadron", "lattice qcd", "nuclear", "plasma", "stellar",
                        "cosmolog", "dark matter", "particle physics", "grand unification", "inflation"],
    "adj-softmatter": ["colloid", "polymer", "active matter", "liquid crystal", "rheolog", "granular", "foam",
                       "microfluidic", "droplet", "self-assembl", "protein", "dna", "lipid"],
    "adj-chemistry": ["catalysis", "electrolyte", "battery", "lithium", "sodium-ion", "co2 reduction",
                      "hydrogen evolution", "oxygen evolution", "catalyst", "molecular dynamics of"],
    "adj-optics": ["laser", "cavity", "photon", "plasmon", "metasurface", "nanophoton", "soliton", "waveguide"],
    "adj-device": ["transistor", "memory device", "memristor", "field-effect", "logic gate", "sensor"],
    "adj-other": ["phonon", "thermal", "thermoelectric", "heat transport", "tribolog", "mechanical",
                  "acoustic", "elastic", "dislocation", "fracture", "wear", "lubrication"],
}

# 新颖性/框架信号词（A 的必要条件之一）
NOVELTY = ["new ", "novel", "generalized", "unified", "general framework", "general theory", "universal",
           "selection rule", "sum rule", "exact", "analytical", "proof", "first-principles framework",
           "systematic", "complete ", "unifying", "scale-free", "new concept", "new mechanism",
           "new class", "prediction of", "we predict", "we establish", "we propose",
           "first realization", "multipolization", "quasisymmetry", "full ", "obstructed",
           "unifies", "introduc", "we construct", "symmetry-based framework", "concept of"]

# 结构性强新颖信号（用于“可迁移方法”等易误升条件；弱词如 systematic/full 不算）
NOVELTY_STRONG = ["new ", "novel", "generalized", "unified", "general framework", "general theory",
                  "universal", "selection rule", "sum rule", "exact", "analytical", "proof",
                  "first-principles framework", "unifying", "new concept", "new mechanism", "new class",
                  "we establish", "we propose", "we introduce", "introduc", "we construct",
                  "symmetry-based framework", "concept of", "first realization", "multipolization",
                  "quasisymmetry"]

SYMMETRY_WORDS = ["group theory", "space group", "spin group", "layer group",
                  "magnetic space group", "selection rule", "multipol", "magnetoelectric",
                  "octupol", "quasisymmetry", "symmetry classification", "symmetry framework",
                  "spin-group", "spin space group", "spin symmetry"]

STRONG_CORE = {"P1-fqfe", "P1-injection", "P2-bpve", "P2-sliding", "P3-switch",
               "P1-wannier", "P1-neumann"}

IRRELEVANT = {"adj-superconductivity", "adj-quantum-info", "adj-high-energy", "adj-softmatter",
              "adj-chemistry", "adj-device", "adj-optics"}

MOLECULAR_PHOTONIC = ["polariton", "molecular", "molecule", "quantum dot", "atomic ensemble",
                      "cavity qed", "molecular dynamics"]
SOLID_STATE_MARKERS = ["solid", "crystal", "monolayer", "bilayer", "two-dimensional", "2d ",
                       "material", "bulk", "perovskite", "semiconductor", "magnet", "ferroelectric",
                       "superconduct", "lattice", "band structure", "first-principles"]

EXPERIMENT_WORDS = ["experiment", "measured", "measurement", "observed", "fabricat", "device", "transport measurements"]


def profile_match(title, abstract):
    text = f"{title} {abstract}".lower()
    tags = []
    for tag, kws in KW.items():
        for kw in kws:
            k = kw.strip().rstrip(" ")
            if k and (re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", text) or kw in text):
                tags.append(tag)
                break
    return tags


def classify(title, abstract, authors, truncated=False):
    """推荐优先级分类。返回 (MUST/WEEKLY/RADAR/DROP, reason, profile, confidence)。"""
    tags = profile_match(title, abstract)
    direct = [t for t in tags if not t.startswith("adj-")]
    adj = [t for t in tags if t.startswith("adj-")]
    text = f"{title} {abstract}".lower()
    ttl = title.lower()

    # 结构手性新机制（阴离子/原子有序产生手性，与 P1 手性材料与 circular injection 相关）
    chiral_order = bool(re.search(r"chiral", text)) and bool(re.search(r"ordering", text)) and bool(
        re.search(r"we show|we demonstrate|we establish|we find", text)) and bool(
        re.search(r"achiral|anion|cation|over the sites", text))
    # C：无任何主题命中，或命中明显无关域
    if not direct and not adj and not chiral_order:
        return DROP, "标题/摘要无研究画像相关主题命中", ",".join(tags), "高"
    if any(m in text for m in MOLECULAR_PHOTONIC) and not any(s in text for s in SOLID_STATE_MARKERS):
        return DROP, "分子/光子学语境（极化激元/分子/量子点），非凝聚态材料光响应", ",".join(tags), "高"
    strong_core = [t for t in direct if t in STRONG_CORE]
    unrelated = [t for t in tags if t in IRRELEVANT]
    if unrelated and not strong_core:
        return DROP, f"命中无关领域（{'/'.join(t.replace('adj-','') for t in unrelated)}），与研究画像无实质重叠", ",".join(tags), "高"

    # 热/声子主题（标题级）或正文热输运语境下，P4-theory 不计为核心（如非线性热导、声子退相干）
    thermal = bool(re.search(r"thermal|heat|thermoelectric|phonon", ttl)) or (
        bool(re.search(r"thermal conduct|heat transport|thermoelectric|phonon dephasing", text)) and not bool(
            re.search(r"shift current|photogalvanic|photovoltaic|injection current|second harmonic|nonlinear optical",
                      text)))
    theory_ok = "P4-theory" in direct and not thermal
    core_eligible = [t for t in strong_core if t != "P1-wannier"]
    if theory_ok:
        core_eligible.append("P4-theory")
    core_set = set(core_eligible)

    novelty_hit = any(re.search(r"(?<![a-z])" + re.escape(w.strip()) + r"(?![a-z])", text) for w in NOVELTY)
    novelty_strong = any(re.search(r"(?<![a-z])" + re.escape(w.strip()) + r"(?![a-z])", text) for w in NOVELTY_STRONG)
    # A 条件 1：新框架/判据 —— 核心主题 + 新颖性信号
    framework = novelty_hit and bool(core_set & {"P1-fqfe", "P1-injection", "P2-bpve", "P2-sliding",
                                                 "P3-switch", "P1-neumann", "P2-stack", "P4-theory"})
    # A 条件 2：可迁移方法 —— Wannier 方法 + 另一核心主题；或手性/激子光学方法
    method_context = bool(re.search(
        r"circular dichroism|chiral|exciton|shift current|photogalvanic|second harmonic|optical response|photocurrent|nonlinear optical",
        text))
    wannier_method = ("P1-wannier" in direct and bool(
        (set(strong_core) - {"P1-wannier"}) or theory_ok or "P2-bpve" in direct
        or "P1-injection" in direct or "quantum geometry" in text or method_context))
    chiral_optical_method = ("P4-method" in direct and method_context and novelty_strong and not thermal)
    method_transfer = novelty_hit and not thermal and (wannier_method or chiral_optical_method)
    # A 条件 3：核心主题直接交叉（含 NLO×磁性、FE×BPVE 等）
    cross_set = core_set | ({"adj-multiferroic"} if "adj-multiferroic" in tags else set())
    direct_cross = len(cross_set) >= 2
    nlo_mag_cross = novelty_hit and (theory_ok or "P2-bpve" in direct or "P1-injection" in direct) and (
        "adj-magnetic" in tags) and bool(re.search(r"optical|photocurrent|photovoltaic|shift current|second harmonic|injection", ttl))
    # A 条件 4：磁性/拓扑/堆叠对称性新框架（摘要信号或标题直接信号）
    title_sym = bool(re.search(
        r"symmetry classification|symmetry framework|spin-group|magnetic space group|layer group|spin group",
        ttl)) or ("altermagnet" in ttl and bool(re.search(r"odd-parity|general ", ttl)))
    sym_novelty = bool(re.search(
        r"introduc|concept of|we construct|symmetry-based framework|general framework|unified framework|"
        r"generalized|unified construction|quasisymmetry|multipol|we predict|we propose|we establish|new ", text))
    # 结构对称性词（不含裸 magnetoelectric：普通磁电材料研究不应仅凭此升 A）
    magsym_words = [w for w in SYMMETRY_WORDS if w != "magnetoelectric"]
    magsym = (bool(set(adj) & {"adj-magnetic", "adj-topology"}) or "P2-stack" in direct) and (
        (bool(re.search(r"|".join(re.escape(w.strip()) for w in magsym_words), text)) and sym_novelty)
        or title_sym)
    # 非阿贝尔/Kramers 量子几何新理论框架（P4 谱系）
    qgeom_framework = bool(set(adj) & {"adj-topology"}) and bool(
        re.search(r"non-abelian|kramers|quaternionic|su\(2\)", text)) and bool(
        re.search(r"quantum metric|quantum geometr", text))

    if not (framework or method_transfer or direct_cross or nlo_mag_cross or magsym or chiral_order or qgeom_framework):
        reason = "有核心主题命中但缺乏新框架/新判据/可迁移方法/直接交叉"
        if not direct:
            reason = "仅邻接主题重合（" + "/".join(t.replace("adj-", "") for t in adj) + "），无核心方向直接交叉"
        return RADAR, reason, ",".join(tags), "高"

    # 到这里可入 A；再判断是否 S
    s_signals = []
    if "P1-fqfe" in direct and ("P2-bpve" in direct or "P1-injection" in direct or "P4-theory" in direct or "P3-switch" in direct):
        s_signals.append("FQFE×光响应直接推进 P1")
    if "P2-sliding" in direct and "P2-bpve" in direct:
        s_signals.append("滑移铁电×BPVE 直接推进 P2")
    if "P3-switch" in direct and ("P2-bpve" in direct or "P1-injection" in direct):
        s_signals.append("极化切换×光电流耦合直接推进 P3")
    if "P4-theory" in direct and ("P2-bpve" in direct or "P1-injection" in direct) and novelty_hit:
        s_signals.append("BPVE/NLO 新理论框架直接支撑 P4")
    if "P1-wannier" in direct and "P2-bpve" in direct and novelty_hit:
        s_signals.append("Wannier 方法×BPVE 直接推进 P4 方法学")
    if "P1-injection" in direct and "P2-bpve" in direct and bool(re.search(r"magnetic|chiral|spin", text)):
        s_signals.append("磁性/手性 BPVE 双通道（shift+injection 并存）直接推进 P1")

    if s_signals:
        grade = MUST
        reason = "；".join(s_signals) + "；新框架/判据/可迁移方法信号明确"
    else:
        grade = WEEKLY
        reason = "满足 A 标准（" + "、".join(sorted(core_set) or [t.replace("adj-", "") for t in adj]) + "），但尚未直接命中当前项目核心推进点"

    conf = "高"
    if truncated:
        conf = "中"
    if not abstract:
        conf = "低"
    return grade, reason, ",".join(tags), conf


def run_screen(conn=None, today=None, log=None, historical=False):
    today = today or date.today().isoformat()
    own_conn = conn is None
    if own_conn:
        conn = connect()
    if log is None:
        log = lambda *a: None
    from la_abstract import enrich_work_abstract
    rows = conn.execute(
        "SELECT * FROM works WHERE queued=1 AND historical_discovery=? ORDER BY first_seen",
        (1 if historical else 0,)).fetchall()
    counts = Counter()
    for w in rows:
        ws = conn.execute("SELECT abstract, abstract_truncated FROM work_sources WHERE work_id=? "
                          "ORDER BY abstract_truncated ASC, length(abstract) DESC LIMIT 1", (w["work_id"],)).fetchone()
        abstract = ws["abstract"] if ws else ""
        truncated = bool(ws and ws["abstract_truncated"]) or not abstract
        grade, reason, matched, conf = classify(w["title"], abstract, w["authors"], truncated=truncated)
        # L1/L2/L3 摘要补全：初判 MUST/WEEKLY 或置信度低时才补；RADAR/DROP 不补
        if grade in (MUST, WEEKLY) and (truncated or not abstract or conf != "高"):
            ok, level, new_abs, new_trunc = enrich_work_abstract(conn, w["work_id"], log=log)
            if ok and (new_abs or "") != abstract:
                abstract = new_abs
                truncated = new_trunc
                grade, reason, matched, conf = classify(w["title"], abstract, w["authors"], truncated=truncated)
        conn.execute("INSERT INTO screenings (work_id,grade,reason,matched_profile,confidence,screened_at,source) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (w["work_id"], grade, reason, matched, conf, now_iso(), "auto"))
        conn.execute("UPDATE works SET queued=0 WHERE work_id=?", (w["work_id"],))
        counts[grade] += 1
        log("screen", f"{w['work_id']} -> {grade} ({conf}) :: {w['title'][:70]}")
    conn.commit()
    if own_conn:
        conn.close()
    return dict(counts)


def override_grade(conn, work_id, grade, reason):
    grade = normalize_priority(grade)
    if grade not in (MUST, WEEKLY, RADAR, DROP):
        raise ValueError("priority must be MUST|WEEKLY|RADAR|DROP")
    conn.execute("INSERT INTO screenings (work_id,grade,reason,matched_profile,confidence,screened_at,source) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (work_id, grade, reason, "", "人工复核", now_iso(), "manual"))
    conn.execute("UPDATE works SET queued=0 WHERE work_id=?", (work_id,))
    conn.commit()


# ================= 全文处理（MUST） =================

def get_arxiv_anc_files(arxiv_id):
    """返回 arXiv abs 页面 anc 附属文件列表。"""
    try:
        page = fetch_text(f"https://arxiv.org/abs/{arxiv_id}", timeout=60)
    except Exception:
        return []
    links = re.findall(r'href="(/src/[^"]+/anc[^"]*)"[^>]*>([^<]+)<', page)
    return [(f"https://arxiv.org{l}", name.strip()) for l, name in links]


def get_nature_si(article_id):
    try:
        page = fetch_text(f"https://www.nature.com/articles/{article_id}", timeout=60)
    except Exception:
        return []
    links = []
    for m in sorted(set(re.findall(r'href="([^"]*(?:MOESM|supplementary)[^"]*\.pdf)"', page, re.I))):
        if m.startswith("/articles/"):
            m = "https://www.nature.com" + m
        links.append((m, os.path.basename(m)))
    return links


def valid_pdf_file(path, min_bytes=10000):
    try:
        if not path or os.path.getsize(path) < min_bytes:
            return False
        with open(path, "rb") as f:
            return f.read(32).lstrip().startswith(b"%PDF-")
    except OSError:
        return False


def download(url, dest, timeout=120):
    """原子下载并验证 PDF；HTML challenge/paywall 页面绝不写成 paper.pdf。"""
    data = fetch(url, timeout=timeout)
    if len(data) < 10000 or not data[:32].lstrip().startswith(b"%PDF-"):
        raise ValueError(f"response is not a valid PDF ({len(data)} bytes)")
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dest)
    return len(data)


def process_work(conn, w, run_id, log=None, today=None):
    """对一篇 MUST work：PDF + SI 检查 + A 输入骨架。返回 processing dict。"""
    today = today or date.today().isoformat()
    if log is None:
        log = lambda *a: None
    title = w["title"]
    slug = slugify(title)
    wdir = os.path.join(PAPERS_DIR, slug)
    os.makedirs(wdir, exist_ok=True)

    pdf_status, pdf_path, pdf_note = "pending", None, ""
    pdf_url = None
    if w["arxiv_id"]:
        pdf_url = f"https://arxiv.org/pdf/{w['arxiv_id']}"
        pdf_note = "arxiv"
    else:
        ws = conn.execute("SELECT source, url FROM work_sources WHERE work_id=? ORDER BY source", (w["work_id"],)).fetchall()
        for row in ws:
            if row["source"] == "nature_rss":
                m = re.search(r"/articles/([^/]+)", row["url"] or "")
                if m:
                    pdf_url = f"https://www.nature.com/articles/{m.group(1)}.pdf"
                    pdf_note = "nature"
                    break
            elif row["source"] == "aps_accepted":
                pdf_note = "aps_accepted(期刊页被 Cloudflare 拦截，可能失败)"
                pdf_url = (row["url"] or "").replace("http://journals.aps.org/", "https://journals.aps.org/")
            elif row["source"] == "acs_rss":
                pdf_note = "acs(被 Cloudflare 拦截，可能失败)"
                if w["doi"]:
                    pdf_url = f"https://pubs.acs.org/doi/pdf/{w['doi']}"
                    break
            elif row["source"] in ("aps_published", "npj_rss"):
                if row["url"] and "nature.com" in (row["url"] or ""):
                    m = re.search(r"/articles/([^/]+)", row["url"])
                    if m:
                        pdf_url = f"https://www.nature.com/articles/{m.group(1)}.pdf"
                        pdf_note = "nature"
                elif w["doi"]:
                    pdf_url = f"https://link.aps.org/pdf/{w['doi']}"
                    pdf_note = "aps"
                break
    from la_db import record_artifact
    if pdf_url:
        dest = os.path.join(wdir, "paper.pdf")
        existing = conn.execute("SELECT * FROM artifacts WHERE work_id=? AND kind='pdf' AND status='ok'",
                                (w["work_id"],)).fetchone()
        if existing and valid_pdf_file(existing["path"]):
            pdf_status, pdf_path, pdf_note = "ok", existing["path"], pdf_note + " | reused"
            log("process", f"{w['work_id']} PDF 已存在，跳过下载（artifact 复用）")
        else:
            if existing:
                conn.execute("UPDATE artifacts SET status='invalid' WHERE id=?", (existing["id"],))
            try:
                n = download(pdf_url, dest)
                pdf_status = "ok"
                pdf_path = dest
                import hashlib
                sha = hashlib.sha256(open(dest, "rb").read()).hexdigest()
                record_artifact(conn, w["work_id"], "pdf", dest, pdf_url, sha, n, pdf_status, pdf_note)
            except Exception as e:
                pdf_status = "failed"
                pdf_note += f" | error: {e}"
                log("process", f"PDF download failed for {w['work_id']}: {e}")
    else:
        pdf_status = "no_url"
        pdf_note = "无可用官方/arXiv PDF 地址"

    # SI 检查
    si_status, si_path, si_note = "none_found", None, ""
    si_links = []
    if w["arxiv_id"]:
        si_links = get_arxiv_anc_files(w["arxiv_id"])
        if si_links:
            si_status = "found"
    if not si_links:
        ws = conn.execute("SELECT url FROM work_sources WHERE work_id=?", (w["work_id"],)).fetchall()
        for row in ws:
            m = re.search(r"/articles/([^/]+)", row["url"] or "")
            if m:
                si_links = get_nature_si(m.group(1))
                if si_links:
                    si_status = "found"
                break
    if si_links:
        dest = os.path.join(wdir, "supplement.pdf")
        existing_si = conn.execute("SELECT * FROM artifacts WHERE work_id=? AND kind='supplement' AND status='ok'",
                                   (w["work_id"],)).fetchone()
        if existing_si and valid_pdf_file(existing_si["path"]):
            si_status, si_path, si_note = "ok", existing_si["path"], "reused"
        else:
            if existing_si:
                conn.execute("UPDATE artifacts SET status='invalid' WHERE id=?", (existing_si["id"],))
            try:
                url, name = si_links[0]
                if not url.startswith("http"):
                    url = "https://www.nature.com" + url
                download(url, dest)
                si_status = "ok"
                si_path = dest
                import hashlib
                sha = hashlib.sha256(open(dest, "rb").read()).hexdigest()
                record_artifact(conn, w["work_id"], "supplement", dest, url, sha, os.path.getsize(dest), "ok", "")
            except Exception as e:
                si_status = "failed"
                si_note = f"error: {e}"
                log("process", f"SI download failed for {w['work_id']}: {e}")

    # A 建档输入骨架；这里只排队，不能把模板冒充为已执行 A。
    a_path = os.path.join(wdir, "A_文献快速建档.md")
    if pdf_status == "ok" and not os.path.exists(a_path):
        with open(a_path, "w") as f:
            f.write(f"# 文献快速建档：{title}\n\n"
                    f"> 状态：待完成（由 agent 精读正文后填写）｜work_id: {w['work_id']}\n\n"
                    f"## 0. 文献信息\n\n- 标题：{title}\n- 作者：{w['authors'] or '未确认'}\n"
                    f"- 期刊/平台：{w['journal'] or '未确认'}\n- DOI/arXiv：{w['doi'] or w['arxiv_id'] or '未确认'}\n"
                    f"- 本次阅读依据：待确认（PDF {'已获取' if pdf_status=='ok' else '未获取'}；SI {'已获取' if si_status=='ok' else '未获取'}）\n\n"
                    f"## 1. 一句话总结与研究对象\n\n待填写。\n\n## 2. 旧问题与新贡献\n\n待填写。\n\n"
                    f"## 3. 核心证据与关键位置\n\n待填写。\n\n## 4. 对用户的潜在价值与引用方式\n\n待填写。\n\n"
                    f"## 5. 阅读优先级与建议去向\n\n待填写。\n\n")
    a_status = "queued" if pdf_status == "ok" and os.path.exists(a_path) else "blocked_pdf"
    conn.execute("INSERT OR REPLACE INTO processing (work_id,run_id,pdf_status,pdf_path,si_status,si_path,a_status,a_path,notes,processed_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (w["work_id"], run_id, pdf_status, pdf_path, si_status, si_path, a_status, a_path,
                  f"pdf_note={pdf_note} | si_note={si_note}", now_iso()))
    conn.commit()
    return {"work_id": w["work_id"], "pdf": pdf_status, "si": si_status, "a": a_status, "dir": wdir}


def run_process(conn=None, run_id=None, today=None, log=None, force_work=None):
    today = today or date.today().isoformat()
    run_id = run_id or run_id_now()
    own_conn = conn is None
    if own_conn:
        conn = connect()
    if log is None:
        log = lambda *a: None
    if force_work:
        rows = conn.execute("SELECT w.* FROM works w JOIN screenings s ON s.work_id=w.work_id "
                            "WHERE w.work_id=? AND s.grade='MUST' ORDER BY s.id DESC LIMIT 1", (force_work,)).fetchall()
    else:
        rows = conn.execute("""SELECT w.* FROM works w
            JOIN screenings s ON s.work_id = w.work_id AND s.id = (
                SELECT MAX(id) FROM screenings s2 WHERE s2.work_id = w.work_id)
            WHERE s.grade = 'MUST' AND w.historical_discovery = 0
              AND NOT EXISTS (
                SELECT 1 FROM processing p
                WHERE p.work_id=w.work_id AND p.pdf_status='ok')
            ORDER BY w.first_seen""").fetchall()
    results = []
    for w in rows:
        r = process_work(conn, w, run_id, log=log, today=today)
        results.append(r)
    conn.commit()
    if own_conn:
        conn.close()
    return results


# ================= Zotero 归类建议 =================

def zotero_suggestion(work, screening):
    tags = [t.strip() for t in (screening["matched_profile"] or "").split(",") if t.strip()]
    text = f"{work['title']} {work['authors']}".lower()
    main, compat = None, []

    def add_comp(c):
        if c not in compat:
            compat.append(c)

    if "P1-fqfe" in tags:
        main = "01_核心/01_铁电/02_分数量子铁电"
    elif "P2-sliding" in tags:
        main = "01_核心/01_铁电/01_滑移/堆叠铁电"
    elif "adj-multiferroic" in tags or "multiferroic" in text:
        main = "01_核心/04_多铁性与磁电耦合"
    elif "P3-switch" in tags:
        main = "01_核心/01_铁电/01_多重极化"
    elif "P1-injection" in tags:
        main = "01_核心/02_BPVE/04_injection/Ballistic"
    elif "P4-theory" in tags and "shg" in text:
        main = "01_核心/02_BPVE/05_SHG"
    elif "P2-bpve" in tags or "P4-theory" in tags:
        if any(x in text for x in ("experiment", "measured", "fabricat", "device")):
            main = "01_核心/02_BPVE/03_实验与器件"
        elif "P4-method" in tags or "wannier" in tags:
            main = "01_核心/02_BPVE/02_计算"
        else:
            main = "01_核心/02_BPVE/01_基础理论"
    elif "P1-neumann" in tags or "P2-stack" in tags:
        main = "01_核心/05_对称性与群论/01_基础理论"
    elif "adj-magnetic" in tags:
        if "altermagnet" in text:
            main = "01_核心/03_磁性/01_交错磁性"
        elif "skyrmion" in text:
            main = "01_核心/03_磁性/03_非共线磁性与自旋纹理"
        else:
            main = "01_核心/03_磁性/02_交换作用与磁相变"
    elif "adj-valley" in tags:
        main = "02_邻接/01_能谷物理"
    elif "adj-exciton" in tags:
        main = "02_邻接/03_激子"
    elif "adj-topology" in tags:
        main = "02_邻接/02_拓扑物态"
    elif "adj-ml" in tags:
        main = "02_邻接/05_ML与高通量"
    elif "P1-wannier" in tags or "P4-method" in tags:
        main = "03_方法与资源/06_Wannier"
    else:
        main = "90_归档/98_超出当前研究范围"

    if "P2-bpve" in tags and main != "01_核心/02_BPVE/01_基础理论":
        add_comp("01_核心/02_BPVE/01_基础理论")
    if "P1-wannier" in tags or "P4-method" in tags:
        add_comp("03_方法与资源/06_Wannier")
    if "adj-magnetic" in tags:
        if "altermagnet" in text:
            add_comp("01_核心/03_磁性/01_交错磁性")
        elif "skyrmion" in text:
            add_comp("01_核心/03_磁性/03_非共线磁性与自旋纹理")
    if "P2-stack" in tags:
        add_comp("01_核心/05_对称性与群论/03_堆叠")
    if "adj-2d" in tags:
        add_comp("02_邻接/05_ML与高通量" if "adj-ml" in tags else "03_方法与资源/01_DFT")
    for g in RESEARCH_GROUPS:
        if g.lower() in (work["authors"] or "").lower():
            add_comp("04_研究组/" + g)
            break
    if main.startswith("01_核心/01_铁电") and "adj-multiferroic" in tags:
        add_comp("01_核心/04_多铁性与磁电耦合")

    ztags = ["待读"]
    if re.search(r"review|perspective|overview|survey", work["title"], re.I):
        ztags.append("综述")
    if any(x in text for x in ("experiment", "measured", "fabricat", "device", "observation of")):
        ztags.append("实验")
    for tag, kws in HOT_MATERIAL_TAGS.items():
        if any(k in text for k in kws):
            ztags.append(tag)
    return main, compat, ztags


# ================= 简报 =================

def render_digest(conn, today):
    new_works = conn.execute("SELECT * FROM works WHERE consumed_history=0 AND first_seen=? ORDER BY title", (today,)).fetchall()
    screenings = conn.execute("""SELECT s.*, w.title, w.authors, w.doi, w.arxiv_id, w.journal
        FROM screenings s JOIN works w ON w.work_id=s.work_id
        WHERE w.consumed_history=0 AND date(s.screened_at)=? ORDER BY s.id""", (today,)).fetchall()
    proc = conn.execute("""SELECT p.*, w.title FROM processing p JOIN works w ON w.work_id=p.work_id
        WHERE date(p.processed_at)=? ORDER BY p.processed_at""", (today,)).fetchall()
    src = conn.execute("""SELECT * FROM sources WHERE id IN (SELECT MAX(id) FROM sources GROUP BY name)""").fetchall()
    version_updates = conn.execute("""SELECT work_id,title FROM works WHERE version_changed=1
        AND date(latest_seen)=?""", (today,)).fetchall()

    by_grade = Counter(s["grade"] for s in screenings)
    src_items, src_new = Counter(), Counter()
    for s in src:
        name = s["name"].split("/")[0]
        src_items[name] += s["items"]
    for nw in new_works:
        ws_rows = conn.execute("SELECT DISTINCT source FROM work_sources WHERE work_id=?", (nw["work_id"],)).fetchall()
        for wsr in ws_rows:
            src_new[wsr["source"]] += 1

    s_rows = [s for s in screenings if s["grade"] == MUST]
    a_rows = [s for s in screenings if s["grade"] == WEEKLY]
    proc_by_work = {p["work_id"]: p for p in proc}

    lines = []
    lines.append(f"# 每日文献简报 {today}\n")
    lines.append("## 今日概览\n")
    lines.append(f"- 新 work：{len(new_works)}")
    lines.append(f"- MUST：{by_grade.get(MUST, 0)}")
    lines.append(f"- WEEKLY：{by_grade.get(WEEKLY, 0)}")
    lines.append(f"- RADAR：{by_grade.get(RADAR, 0)}")
    lines.append(f"- DROP：{by_grade.get(DROP, 0)}")
    lines.append("- 来源统计（抓取条目 / 本次新增 work）：")
    for name in sorted(src_items):
        lines.append(f"  - {name}：{src_items[name]} 条 / {src_new.get(name, 0)} 新增")
    lines.append("")
    lines.append("## MUST｜必须知道\n")
    if not s_rows:
        lines.append("本轮无 MUST。\n")
    for s in s_rows:
        main, compat, ztags = zotero_suggestion(
            conn.execute("SELECT * FROM works WHERE work_id=?", (s["work_id"],)).fetchone(), s)
        p = proc_by_work.get(s["work_id"])
        lines.append(f"### {s['title']}\n")
        lines.append(f"- 来源 / 期刊：{s['journal'] or '未确认'}（screened_at: {s['screened_at']}）")
        lines.append(f"- DOI / arXiv：{s['doi'] or '—'} / {s['arxiv_id'] or '—'}")
        lines.append(f"- 为什么现在值得读：{s['reason']}")
        lines.append(f"- 对应我的研究：{s['matched_profile']}")
        lines.append(f"- A 建档路径：`{p['a_path'] if p else '未处理'}`")
        lines.append(f"- PDF：{'成功' if p and p['pdf_status']=='ok' else (p['pdf_status'] if p else '未处理')} "
                     f"`{p['pdf_path'] if p else ''}`")
        lines.append(f"- SI：{'成功' if p and p['si_status']=='ok' else (p['si_status'] if p else '未检查')} "
                     f"`{p['si_path'] if p else ''}`")
        lines.append(f"- 建议动作：精读 → 进 Zotero（主 Collection：{main}）\n")
    lines.append("## WEEKLY｜本周值得读\n")
    if not a_rows:
        lines.append("本轮无 WEEKLY。\n")
    for s in a_rows:
        main, compat, ztags = zotero_suggestion(
            conn.execute("SELECT * FROM works WHERE work_id=?", (s["work_id"],)).fetchone(), s)
        lines.append(f"### {s['title']}")
        lines.append(f"- 来源 / 期刊：{s['journal'] or '未确认'}｜DOI/arXiv：{s['doi'] or s['arxiv_id'] or '—'}")
        lines.append(f"- 一句话理由：{s['reason']}")
        lines.append(f"- 主 Collection：{main}")
        lines.append(f"- 兼容 Collection：{'；'.join(compat) if compat else '—'}")
        lines.append(f"- Tags：{'、'.join(ztags)}\n")
    lines.append("## 系统提示\n")
    fails = [s for s in src if not s["ok"]]
    if fails:
        lines.append("- 抓取失败来源：")
        for f in fails:
            lines.append(f"  - {f['name']}：{f['error']}")
    else:
        lines.append("- 抓取失败来源：无")
    trunc = [s for s in screenings if s["confidence"] in ("中", "低")]
    if trunc:
        lines.append("- 低置信度判断：")
        for s in trunc:
            lines.append(f"  - {s['title'][:80]}（{s['confidence']}）")
    no_si = [p for p in proc if p["si_status"] != "ok"]
    if no_si:
        lines.append("- SI 未获取：")
        for p in no_si:
            lines.append(f"  - {p['title'][:80]}（{p['si_status']}）")
    if version_updates:
        lines.append("- 版本更新：")
        for v in version_updates:
            lines.append(f"  - {v['title'][:80]}（work_id: {v['work_id']}）")
    lines.append("")
    return "\n".join(lines)


def run_digest(conn=None, today=None):
    today = today or date.today().isoformat()
    own_conn = conn is None
    if own_conn:
        conn = connect()
    os.makedirs(DAILY_DIR, exist_ok=True)
    path = os.path.join(DAILY_DIR, f"{today}.md")
    content = render_digest(conn, today)
    with open(path, "w") as f:
        f.write(content)
    conn.execute("INSERT OR REPLACE INTO digests (date,path,summary_json) VALUES (?,?,?)",
                 (today, path, json.dumps({"bytes": len(content)}, ensure_ascii=False)))
    conn.commit()
    if own_conn:
        conn.close()
    return path


# ================= 日志 =================

def prune_logs(days=30):
    """日志轮转：删除超过 days 天的 run-*.jsonl。"""
    import glob
    cutoff = time.time() - int(days) * 86400
    for p in glob.glob(os.path.join(LOG_DIR, "run-*.jsonl")):
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


class RunLogger:
    def __init__(self, run_id):
        os.makedirs(LOG_DIR, exist_ok=True)
        self.path = os.path.join(LOG_DIR, f"run-{run_id}.jsonl")
        self.fh = open(self.path, "a")

    def log(self, kind, message):
        self.fh.write(json.dumps({"ts": now_iso(), "kind": kind, "msg": message}, ensure_ascii=False) + "\n")
        self.fh.flush()

    def close(self):
        self.fh.close()


# ================= daily 总入口 =================

def _last_ok_daily_date(conn):
    """最近一次成功/部分成功的 daily 运行日期；没有记录则返回 None。"""
    row = conn.execute(
        "SELECT finished_at FROM processing_runs WHERE command='daily' "
        "AND status IN ('ok','partial') ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if not row or not row["finished_at"]:
        return None
    try:
        return date.fromisoformat(str(row["finished_at"])[:10])
    except ValueError:
        return None


def _daily_ok_today(conn, today):
    """当天是否已有成功/部分成功的 daily 运行（launchd 多时间点守卫）。"""
    row = conn.execute(
        "SELECT 1 FROM processing_runs WHERE command='daily' AND status IN ('ok','partial') "
        "AND date(finished_at)=? LIMIT 1", (today,)
    ).fetchone()
    return row is not None


def run_daily(today=None, run_id=None, force=False):
    today = today or date.today().isoformat()
    run_id = run_id or run_id_now()
    conn = connect()
    logger = RunLogger(run_id)
    summary = {"run_id": run_id, "today": today, "phases": {}}
    summary["catchup"] = {}
    try:
        try:
            from la_overview import sync_old_overviews
            summary["overview_sync"] = sync_old_overviews(conn, today)
            logger.log("phase", f"overview sync old done: {summary['overview_sync']}")
        except Exception as e:
            logger.log("error", f"overview sync old failed: {e}")
        if not force and _daily_ok_today(conn, today):
            # launchd 7/8/9/10 多时间点：当天已成功一次即跳过重活，
            # 只保留上面的轻量批注同步；失败/部分成功由后续时间点自动重试。
            summary["skipped"] = True
            summary["new_works"] = 0
            summary["processed"] = 0
            summary["failed_sources"] = []
            logger.log("phase", "daily already ok today; skipped (轻量 overview sync 已完成)")
            conn.close()
            logger.close()
            return summary
        logger.log("phase", "collect start")
        # 自动补课：距上次成功运行超过 1 天时，把可回溯源（arXiv/Crossref/OpenAlex）的
        # 时间窗口拉大到缺口区间；超过 7 天先跑历史回放再正常收集。全程幂等。
        last_ok = _last_ok_daily_date(conn)
        gap_days = None
        if last_ok:
            gap_days = (date.fromisoformat(today) - last_ok).days
        if gap_days and gap_days > 1:
            if gap_days <= 7:
                days = gap_days + 1
                logger.log("phase", f"catchup: 距上次成功 {gap_days} 天，窗口拉大到 {days} 天")
                c = run_collect(conn=conn, today=today, log=logger.log, run_id=run_id, days=days)
                summary["catchup"] = {"mode": "extend_window", "gap_days": gap_days, "window_days": days}
            else:
                logger.log("phase", f"catchup: 距上次成功 {gap_days} 天，触发历史回放 {last_ok} → {today}")
                from la_replay import run_replay
                r = run_replay(conn=conn, log=logger.log, from_date=last_ok.isoformat(), to_date=today)
                summary["catchup"] = {
                    "mode": "historical_replay", "gap_days": gap_days,
                    "replay": {k: v for k, v in r.items() if k != "sources"},
                }
                c = run_collect(conn=conn, today=today, log=logger.log, run_id=run_id)
        else:
            c = run_collect(conn=conn, today=today, log=logger.log, run_id=run_id)
        if summary["catchup"]:
            logger.log("phase", f"catchup done: {summary['catchup']['mode']} gap={gap_days}")
        summary["phases"]["collect"] = c
        summary["new_works"] = c["new"]
        summary["failed_sources"] = c["failed_sources"]
        logger.log("phase", f"collect done: new={c['new']} updated={c['updated']} requeued={c['requeued']} failed={c['failed_sources']}")

        logger.log("phase", "screen start")
        counts = run_screen(conn=conn, today=today, log=logger.log)
        summary["grades"] = counts
        summary["phases"]["screen"] = counts
        logger.log("phase", f"screen done: {counts}")

        logger.log("phase", "process start")
        results = run_process(conn=conn, run_id=run_id, today=today, log=logger.log)
        summary["processed"] = len(results)
        summary["phases"]["process"] = results
        logger.log("phase", f"process done: {len(results)} MUST works")

        logger.log("phase", "A workflow reconcile/queue start")
        from la_workflow import build_a_queue
        a_queue = build_a_queue(conn)
        summary["a_queue"] = a_queue
        summary["phases"]["a_workflow"] = a_queue
        logger.log("phase", f"A workflow queued={a_queue['pending']} completed={a_queue['reconcile']['completed']}")

        logger.log("phase", "digest start")
        from la_digest import run_daily_v4
        from la_zotero import generate_queue
        path = run_daily_v4(conn=conn, today=today)
        summary["digest_path"] = path
        summary["phases"]["digest"] = {"path": path}
        summary["zotero_queued"] = generate_queue(conn, today, log=logger.log)
        try:
            from la_zotero import export_bibtex
            summary["zotero_export"] = export_bibtex(conn)
        except Exception as e:
            logger.log("error", f"zotero export failed: {e}")
        logger.log("phase", f"digest done: {path}")
        try:
            from la_overview import run_overview
            summary["overview"] = run_overview(conn=conn, date=today)
            logger.log("phase", f"overview done: {summary['overview']['path']}")
        except Exception as e:
            logger.log("error", f"overview failed: {e}")
            summary["overview_error"] = str(e)

        from la_db import sync_reading_status
        summary["reading_status"] = sync_reading_status(conn)
        prune_logs(30)
        status = "ok" if not summary["failed_sources"] else "partial"
    except Exception as e:
        status = "failed"
        summary["error"] = str(e)
        logger.log("error", f"daily crashed: {e}")
    conn.execute("INSERT INTO processing_runs (run_id,command,started_at,finished_at,status,summary_json) "
                 "VALUES (?,?,?,?,?,?)",
                 (run_id, "daily", now_iso(), now_iso(), status, json.dumps(summary, ensure_ascii=False, default=str)))
    conn.commit()
    try:
        from la_handoff import run_handoff
        summary["handoff"] = run_handoff(conn)
        logger.log("phase", f"handoff refreshed: {summary['handoff']['handoff']}")
    except Exception as e:
        logger.log("error", f"handoff refresh failed: {e}")
    conn.close()
    logger.close()
    return summary


# ================= bootstrap（历史迁移） =================

def bootstrap(conn=None):
    """把测试阶段产物标记为已消费，写入 DB（不删除任何原文件）。"""
    own_conn = conn is None
    if own_conn:
        conn = connect()
    today = date.today().isoformat()
    stats = {"arxiv": 0, "aps_seen": 0, "rss": 0, "npj": 0, "screening_rows": 0}

    # 1. arXiv 历史（295）
    arx_path = "/tmp/arxiv_entries.json"
    if os.path.exists(arx_path):
        for e in json.load(open(arx_path)):
            base = norm_arxiv_id(e["id"])
            hist_date = (e.get("published") or today)[:10]
            rec = {"title": e["title"], "authors": e.get("authors") or e.get("author") or "",
                   "arxiv_id": base, "version": f"v{arxiv_version(e['id'])}",
                   "abstract": e.get("abs") or e.get("abstract") or "", "abstract_truncated": False,
                   "date": hist_date,
                   "url": f"https://arxiv.org/abs/{base}", "journal": "arXiv", "status": "arxiv"}
            wid, action, _ = ingest_record(conn, rec, "arxiv", today, history=True)
            if action == "new":
                conn.execute("UPDATE works SET first_seen=? WHERE work_id=?", (hist_date, wid))
            if action == "new":
                stats["arxiv"] += 1

    # 2. APS seen-works（400：200 accepted + 200 published）
    aps_path = os.path.join(ARCHIVE_ROOT, "legacy_scripts", "aps_seen_works.json")
    if os.path.exists(aps_path):
        store = json.load(open(aps_path))
        for wid, w in store.get("works", {}).items():
            if w.get("first_seen_source") == "accepted" or w.get("status") == "accepted":
                src, ver = "aps_accepted", "accepted"
            else:
                src, ver = "aps_published", "published"
            meta = w.get("accepted") or w.get("published") or {}
            hist_date = meta.get("date") or w.get("first_seen", today)
            rec = {"title": w["title"], "authors": w.get("authors", ""), "doi": w.get("doi"),
                   "arxiv_id": None, "version": ver, "abstract": meta.get("abstract", ""),
                   "abstract_truncated": bool(meta.get("abstract_truncated")),
                   "date": hist_date, "url": meta.get("url", ""),
                   "journal": w.get("journal", "APS"), "status": w.get("status", "published")}
            wid2, action, _ = ingest_record(conn, rec, src, today, history=True)
            if action == "new":
                conn.execute("UPDATE works SET first_seen=? WHERE work_id=?", (hist_date, wid2))
            if action == "new":
                stats["aps_seen"] += 1

    # 3. RSS 历史（167，含分级；rss_new_sorted + rss_labels）
    rss_path, lbl_path = "/tmp/rss_new_sorted.json", "/tmp/rss_labels.json"
    if os.path.exists(rss_path) and os.path.exists(lbl_path):
        rss = json.load(open(rss_path))
        labels = json.load(open(lbl_path))
        for i, r in enumerate(rss):
            if r["source"] == "npj Computational Materials":
                src, journal = "npj_rss", "npj Computational Materials"
            else:
                src, journal = "aps_published", r["source"]
            rec = {"title": r["title"], "authors": r.get("creators", ""), "doi": r.get("doi"),
                   "arxiv_id": None, "version": "rss", "abstract": r.get("abstract", ""),
                   "abstract_truncated": bool(r.get("abstract_truncated")),
                   "date": (r.get("date") or today)[:10], "url": r.get("link", ""),
                   "journal": journal, "status": "published" if src == "aps_published" else "journal"}
            wid, action, _ = ingest_record(conn, rec, src, today, history=True)
            if action == "new":
                conn.execute("UPDATE works SET first_seen=? WHERE work_id=?", ((r.get("date") or today)[:10], wid))
            grade = normalize_priority(labels.get(str(i)))
            if grade:
                matched = ",".join(profile_match(rec["title"], rec.get("abstract") or ""))
                conn.execute("INSERT INTO screenings (work_id,grade,reason,matched_profile,confidence,screened_at,source) "
                             "VALUES (?,?,?,?,?,?,?)",
                             (wid, grade, "历史测试轮次分级（已消费）", matched, "高", today, "bootstrap"))
                stats["screening_rows"] += 1
            if action == "new":
                stats["rss"] += 1

    # 4. APS accepted 队列分级（200）
    acc_path, accl_path = "/tmp/acc_queue.json", "/tmp/acc_labels.json"
    if os.path.exists(acc_path) and os.path.exists(accl_path):
        acc = json.load(open(acc_path))
        labels = json.load(open(accl_path))
        for i, w in enumerate(acc):
            wid = w.get("wid") or w.get("work_id")
            if not wid:
                continue
            grade = normalize_priority(labels.get(str(i)))
            if grade:
                wabs = ""
                for meta_key in ("accepted", "published"):
                    meta = w.get(meta_key) or {}
                    if meta.get("abstract"):
                        wabs = meta["abstract"]
                        break
                matched = ",".join(profile_match(w.get("title", ""), wabs))
                conn.execute("INSERT OR IGNORE INTO screenings (work_id,grade,reason,matched_profile,confidence,screened_at,source) "
                             "VALUES (?,?,?,?,?,?,?)",
                             (wid, grade, "历史测试轮次分级（已消费）", matched, "高", today, "bootstrap"))
                stats["screening_rows"] += 1

    conn.commit()
    if own_conn:
        conn.close()
    return stats


# ================= 自检 =================

def selftest():
    import tempfile
    tmp = tempfile.mktemp(suffix=".db")
    conn = connect(tmp)
    ok = []
    global PAPERS_DIR, DAILY_DIR, LOG_DIR
    _saved = (PAPERS_DIR, DAILY_DIR, LOG_DIR)
    tmp_root = tempfile.mkdtemp(prefix="litagent-selftest-")
    PAPERS_DIR = os.path.join(tmp_root, "papers")
    DAILY_DIR = os.path.join(tmp_root, "daily")
    LOG_DIR = os.path.join(tmp_root, "logs")
    os.makedirs(PAPERS_DIR)
    os.makedirs(DAILY_DIR)

    # 场景 1：同一 work arxiv -> accepted -> published 不重复推荐
    today = "2026-08-08"
    arx = {"title": "Unified Shift Current Framework in Layered Materials", "authors": "A. Author; B. Coauthor",
           "arxiv_id": "2608.99999", "version": "v1", "abstract": "We establish a general shift current framework for layered crystals with full Wannier position matrices and show new sum rules.", "abstract_truncated": False, "date": today, "url": "https://arxiv.org/abs/2608.99999", "journal": "arXiv", "status": "arxiv"}
    acc = {"title": "Unified Shift Current Framework in Layered Materials", "authors": "A. Author; B. Coauthor",
           "doi": "10.1103/test-abc1", "version": "accepted", "abstract": "We establish a general shift current…", "abstract_truncated": True, "date": today, "url": "http://journals.aps.org/prl/accepted/x", "journal": "PRL", "status": "accepted"}
    pub = {"title": "Unified Shift Current Framework in Layered Materials", "authors": "A. Author; B. Coauthor",
           "doi": "10.1103/test-abc1", "version": "published", "abstract": "We establish a general shift current framework for layered crystals with full Wannier position matrices and show new sum rules.", "abstract_truncated": False, "date": today, "url": "https://link.aps.org/doi/10.1103/test-abc1", "journal": "PRL", "status": "published"}
    wid1, a1, _ = ingest_record(conn, arx, "arxiv", today)
    wid2, a2, _ = ingest_record(conn, acc, "aps_accepted", today)
    wid3, a3, _ = ingest_record(conn, pub, "aps_published", today)
    assert wid1 == wid2 == wid3, (wid1, wid2, wid3)
    w = conn.execute("SELECT * FROM works WHERE work_id=?", (wid1,)).fetchone()
    assert w["status"] == "published" and w["queued"] == 1, dict(w)
    run_screen(conn, today)  # 消费
    wid4, a4, _ = ingest_record(conn, pub, "aps_published", today)
    w2 = conn.execute("SELECT queued FROM works WHERE work_id=?", (wid1,)).fetchone()
    assert w2["queued"] == 0, "accepted->published 不应重新入队"
    n_screen = conn.execute("SELECT COUNT(*) FROM screenings WHERE work_id=?", (wid1,)).fetchone()[0]
    assert n_screen == 1, "不应产生第二次筛选"
    ok.append("scenario1: arxiv->accepted->published 同一 work，不重复推荐 PASS")

    # 场景 2：arxiv v1 -> v2 版本号不造成重复；abstract 变化才重入队
    v1 = {"title": "Quantum Geometry of Sliding Bilayers", "authors": "C. Author",
          "arxiv_id": "2608.88888", "version": "v1", "abstract": "We study quantum geometry of sliding bilayers with focus on shift current response.", "abstract_truncated": False, "date": today, "url": "https://arxiv.org/abs/2608.88888", "journal": "arXiv", "status": "arxiv"}
    v2_same = dict(v1, version="v2", arxiv_id="2608.88888v2", abstract=v1["abstract"] + " We also add minor clarifications.")
    v2_diff = dict(v1, version="v2", arxiv_id="2608.88888v2", title="Completely New Title: Topology of Quantum Circuits")
    wid, a, _ = ingest_record(conn, v1, "arxiv", today)
    run_screen(conn, today)
    wid, a, _ = ingest_record(conn, v2_same, "arxiv", today)
    assert conn.execute("SELECT queued FROM works WHERE work_id=?", (wid,)).fetchone()["queued"] == 0, "v2 无实质变化不应重入队"
    wid, a, _ = ingest_record(conn, v2_diff, "arxiv", today)
    wv = conn.execute("SELECT queued, version_changed FROM works WHERE work_id=?", (wid,)).fetchone()
    assert wv["queued"] == 1 and wv["version_changed"] == 1, dict(wv)
    assert conn.execute("SELECT COUNT(*) FROM works WHERE work_id=?", (wid,)).fetchone()[0] == 1
    conn.commit()
    ok.append("scenario2: v1->v2 同一 work，实质变化才重入队 PASS")

    # 场景 3：某 source 网络失败，daily 继续（V2 API 源同样模拟网络失败）
    def bad_fetch(url, *a, **k):
        raise RuntimeError("network down")
    orig = globals()["fetch"]
    globals()["fetch"] = bad_fetch
    import la_v2common as _v2c
    _v2_orig = _v2c.http_get_json
    _v2c.http_get_json = lambda *a, **k: (False, "network down")
    _nocache = os.environ.get("LITAGENT_NO_CACHE")
    os.environ["LITAGENT_NO_CACHE"] = "1"
    try:
        conn2 = connect(tmp)
        summary = run_collect(conn=conn2, today=today, log=lambda *a: None)
        conn2.close()
    finally:
        globals()["fetch"] = orig
        _v2c.http_get_json = _v2_orig
        if _nocache is None:
            os.environ.pop("LITAGENT_NO_CACHE", None)
        else:
            os.environ["LITAGENT_NO_CACHE"] = _nocache
    assert summary["failed_sources"], summary
    assert "new" in summary and summary["new"] == 0, summary
    ok.append("scenario3: source 网络失败不崩溃，错误记录 PASS")

    # 场景 4：没有 S，process 正常跳过（先把场景 1 的 S 标记为已处理）
    conn.execute("INSERT INTO processing (work_id,run_id,pdf_status,si_status,a_status,processed_at) "
                 "VALUES ('arxiv:2608.99999','test-run-1','ok','ok','done',?)", (now_iso(),))
    r1 = run_process(conn=conn, run_id="test-run-1", today=today)
    assert r1 == [], r1
    ok.append("scenario4: 无 S 时 process 正常跳过 PASS")

    # 场景 5：模拟 S，PDF/A 路由正确
    conn.execute("INSERT INTO works (work_id,title,normalized_title,authors,arxiv_id,journal,status,first_seen,latest_seen,queued) "
                 "VALUES ('test-s1','Simulated S Work on Shift Current in Sliding Bilayers','simulatedsworkonshiftcurrentinslidingbilayers','D. Author','2608.77777','arXiv','arxiv',?,?,0)", (today, today))
    conn.execute("INSERT INTO work_sources (work_id,source,version,title,url,abstract,abstract_truncated,date) VALUES (?,?,?,?,?,?,?,?)",
                 ("test-s1", "arxiv", "v1", "Simulated S Work on Shift Current in Sliding Bilayers",
                  "https://arxiv.org/abs/2608.77777", "We compute shift current in sliding bilayers with a new general framework.", 0, today))
    conn.execute("INSERT INTO screenings (work_id,grade,reason,matched_profile,confidence,screened_at,source) VALUES (?,?,?,?,?,?,?)",
                 ("test-s1", MUST, "test", "P2-bpve,P2-sliding", "高", now_iso(), "test"))
    def fake_fetch(url, timeout=60, binary=False):
        if "arxiv.org/pdf" in url:
            return b"%PDF-1.4 fake pdf content " * 5000
        if url.startswith("https://arxiv.org/abs/"):
            return b'<html><div class="full-text"><h1>Ancillary files</h1><a href="/src/2608.77777/anc/supp.pdf">supp.pdf</a></div></html>'
        if "arxiv.org/src" in url:
            return b"%PDF-1.4 fake si content " * 3000
        return b"<html>generic</html>"
    orig2 = globals()["fetch"]
    globals()["fetch"] = fake_fetch
    try:
        results = run_process(conn=conn, run_id="test-run-2", today=today, log=lambda *a: None)
    finally:
        globals()["fetch"] = orig2
    assert len(results) == 1, results
    assert results[0]["pdf"] == "ok" and results[0]["si"] == "ok", results[0]
    wdir = results[0]["dir"]
    assert os.path.exists(os.path.join(wdir, "paper.pdf"))
    assert os.path.exists(os.path.join(wdir, "supplement.pdf"))
    assert os.path.exists(os.path.join(wdir, "A_文献快速建档.md"))
    assert "%20" not in wdir
    ok.append("scenario5: 模拟 S 的 PDF/SI/A 路由正确 PASS")

    # 场景 6：重复运行 daily 不重复处理已消费 work
    conn.execute("DELETE FROM processing_runs")
    conn.commit()
    empty_atom = ("<feed xmlns='http://www.w3.org/2005/Atom'><title>empty</title></feed>").encode()
    empty_rss = ("<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#' "
                 "xmlns:rss='http://purl.org/rss/1.0/'></rdf:RDF>").encode()

    def empty_fetch(url, timeout=60, binary=False):
        if "export.arxiv.org" in url:
            return empty_atom
        return empty_rss

    orig3 = globals()["fetch"]
    globals()["fetch"] = empty_fetch
    _saved_db_env = os.environ.get("LITAGENT_DB")
    _saved_daily_env = os.environ.get("LITAGENT_DAILY_DIR")
    _saved_q_env = os.environ.get("LITAGENT_ZOTERO_QUEUE")
    _saved_a_env = os.environ.get("LITAGENT_A_QUEUE")
    _saved_hf_env = os.environ.get("LITAGENT_HANDOFF_DIR")
    _saved_papers_env = os.environ.get("LITAGENT_PAPERS_DIR")
    _saved_vault_env = os.environ.get("LITAGENT_VAULT_ROOT")
    _saved_askill_env = os.environ.get("LITAGENT_A_SKILL")
    _saved_ov_env = os.environ.get("LITAGENT_OVERVIEW_DIR")
    _saved_df_env = os.environ.get("LITAGENT_DIRECTION_FEEDBACK")
    os.environ["LITAGENT_DB"] = tmp
    os.environ["LITAGENT_DAILY_DIR"] = os.path.join(tmp_root, "daily")
    os.environ["LITAGENT_ZOTERO_QUEUE"] = os.path.join(tmp_root, "zotero_queue.jsonl")
    os.environ["LITAGENT_A_QUEUE"] = os.path.join(tmp_root, "a_queue.jsonl")
    os.environ["LITAGENT_HANDOFF_DIR"] = tmp_root
    os.environ["LITAGENT_PAPERS_DIR"] = PAPERS_DIR
    os.environ["LITAGENT_VAULT_ROOT"] = tmp_root
    os.environ["LITAGENT_A_SKILL"] = os.path.join(tmp_root, "A skill.md")
    os.environ["LITAGENT_OVERVIEW_DIR"] = os.path.join(tmp_root, "每日文献")
    os.environ["LITAGENT_DIRECTION_FEEDBACK"] = os.path.join(tmp_root, "direction_feedback_candidates.md")
    for _sd in ("0_待处理", "1_处理中", "2-可调用", "00-已阅"):
        os.makedirs(os.path.join(tmp_root, _sd), exist_ok=True)
    try:
        import la_digest as _ld, la_zotero as _lz
        _ld.DAILY_DIR = os.path.join(tmp_root, "daily")
        _lz.QUEUE_PATH = os.path.join(tmp_root, "zotero_queue.jsonl")
        summary1 = run_daily(today=today, run_id="test-daily-1")
        summary2 = run_daily(today=today, run_id="test-daily-2")
    finally:
        globals()["fetch"] = orig3
        for _k, _v in (("LITAGENT_DB", _saved_db_env), ("LITAGENT_DAILY_DIR", _saved_daily_env),
                       ("LITAGENT_ZOTERO_QUEUE", _saved_q_env), ("LITAGENT_A_QUEUE", _saved_a_env),
                       ("LITAGENT_HANDOFF_DIR", _saved_hf_env), ("LITAGENT_PAPERS_DIR", _saved_papers_env),
                       ("LITAGENT_VAULT_ROOT", _saved_vault_env), ("LITAGENT_A_SKILL", _saved_askill_env),
                       ("LITAGENT_OVERVIEW_DIR", _saved_ov_env), ("LITAGENT_DIRECTION_FEEDBACK", _saved_df_env)):
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
    assert summary1["new_works"] == 0, summary1
    assert summary2["new_works"] == 0, summary2
    assert summary2["processed"] == 0, summary2
    n_screen2 = conn.execute("SELECT COUNT(*) FROM screenings WHERE date(screened_at)=? AND source!='bootstrap' AND source!='manual'", (today,)).fetchone()[0]
    ok.append(f"scenario6: 重复 daily 不重复处理 PASS（screen rows={n_screen2}）")

    # 场景 7：去重 synthetic 全量用例
    import la_dedup
    dp, df = la_dedup.run_synthetic_tests()
    assert df == [], [x["name"] for x in df]
    ok.append(f"scenario7: dedup synthetic {dp}/{dp + len(df)} PASS")

    # 场景 8：process 幂等 + artifact 复用 + SI missing 状态保留
    calls = {"download": 0}
    def counting_download(url, dest, timeout=120):
        calls["download"] += 1
        return download(url, dest, timeout=timeout)
    _orig_dl = globals()["download"]
    globals()["download"] = counting_download
    try:
        r2 = run_process(conn=conn, run_id="test-run-3", today=today, log=lambda *a: None)
        assert r2 == [], "已有 processing 记录不应重复处理"
        r3 = run_process(conn=conn, run_id="test-run-4", today=today, force_work="test-s1", log=lambda *a: None)
        assert len(r3) == 1 and r3[0]["pdf"] == "ok", r3
        art = conn.execute("SELECT * FROM artifacts WHERE work_id='test-s1' AND kind='pdf'").fetchall()
        assert len(art) == 1 and art[0]["sha256"], "PDF artifact 应记录 sha256"
    finally:
        globals()["download"] = _orig_dl
    assert calls["download"] == 0, f"force 重跑不应重新下载（artifact 复用），实际 {calls['download']}"
    conn.execute("INSERT INTO works (work_id,title,normalized_title,authors,arxiv_id,journal,status,first_seen,latest_seen,queued) "
                 "VALUES ('test-s2','Simulated S Without Supplement','simulatedswithoutsupplement','E. Author','2608.66666','arXiv','arxiv',?,?,0)", (today, today))
    conn.execute("INSERT INTO work_sources (work_id,source,version,title,url,abstract,abstract_truncated,date) VALUES (?,?,?,?,?,?,?,?)",
                 ("test-s2", "arxiv", "v1", "Simulated S Without Supplement", "https://arxiv.org/abs/2608.66666", "Shift current in sliding bilayers with a general framework.", 0, today))
    conn.execute("INSERT INTO screenings (work_id,grade,reason,matched_profile,confidence,screened_at,source) VALUES (?,?,?,?,?,?,?)",
                 ("test-s2", MUST, "test", "P2-bpve", "高", now_iso(), "test"))
    def fake_fetch2(url, timeout=60, binary=False):
        if "arxiv.org/pdf" in url:
            return b"%PDF-1.4 fake pdf " * 5000
        if url.startswith("https://arxiv.org/abs/"):
            return b"<html>no ancillary files here</html>"
        return b"<html>generic</html>"
    _orig_f = globals()["fetch"]
    globals()["fetch"] = fake_fetch2
    try:
        r4 = run_process(conn=conn, run_id="test-run-5", today=today, log=lambda *a: None)
    finally:
        globals()["fetch"] = _orig_f
    assert len(r4) == 1 and r4[0]["pdf"] == "ok" and r4[0]["si"] == "none_found", r4
    r5 = run_process(conn=conn, run_id="test-run-6", today=today, log=lambda *a: None)
    assert r5 == [], "SI missing 状态应保留且不重复处理"
    ok.append("scenario8: process 幂等 + artifact 复用 + SI missing 保留 PASS")

    # 场景 9：筛选回归守卫（不写文件，直接算指标）
    import la_eval
    gold = la_eval.build_gold(force=False)
    assert len(gold) >= 100, f"gold 集过小: {len(gold)}"
    tp_s = sum(1 for g in gold if normalize_priority(g["expected_grade"]) == MUST
               and classify(g["title"], g["abstract"], "", truncated=False)[0] == MUST)
    tp_as = sum(1 for g in gold if normalize_priority(g["expected_grade"]) in (MUST, WEEKLY)
                and classify(g["title"], g["abstract"], "", truncated=False)[0] in (MUST, WEEKLY))
    n_s = sum(1 for g in gold if normalize_priority(g["expected_grade"]) == MUST)
    n_as = sum(1 for g in gold if normalize_priority(g["expected_grade"]) in (MUST, WEEKLY))
    assert n_s >= 2 and tp_s / n_s >= 0.9, f"MUST recall 退化: {tp_s}/{n_s}"
    assert tp_as / n_as >= 0.9, f"MUST+WEEKLY recall 退化: {tp_as}/{n_as}"
    ok.append(f"scenario9: screening 回归守卫 PASS（MUST={tp_s}/{n_s}，MUST+WEEKLY={tp_as}/{n_as}）")

    # 场景 10：同一 collect 批次中的 arXiv→DOI 立即共享去重索引
    batch_index = DedupIndex(conn)
    batch_arx = {
        "title": "Same-Batch Identity Test for Shift Current",
        "authors": "F. Author", "arxiv_id": "2608.55555", "version": "v2",
        "abstract": "A shift-current identity test.", "abstract_truncated": False,
        "date": today, "url": "https://arxiv.org/abs/2608.55555", "journal": "arXiv",
    }
    batch_doi = dict(batch_arx, arxiv_id=None, version="published",
                     doi="10.1234/samebatch", journal="Test Journal")
    bw1, _, _ = ingest_record(conn, batch_arx, "arxiv", today, index=batch_index)
    bw2, _, _ = ingest_record(conn, batch_doi, "crossref_journals", today, index=batch_index)
    assert bw1 == bw2, (bw1, bw2)
    assert conn.execute(
        "SELECT COUNT(*) FROM works WHERE normalized_title=?",
        (norm_title(batch_arx["title"]),),
    ).fetchone()[0] == 1
    stored_versions = {
        r["version"] for r in conn.execute(
            "SELECT version FROM work_sources WHERE work_id=? AND source='arxiv'", (bw1,)
        ).fetchall()
    }
    assert "v2" in stored_versions, stored_versions
    ok.append("scenario10: 同批去重索引刷新 + arXiv v2 保留 PASS")

    # 场景 11：作者同名只能是发现信号，不能绕过主题门槛直接升 A
    false_author_grade = classify(
        "Nonlinear Thermal Transport in Phase-Change Materials",
        "We establish a general framework for nonlinear thermal response and heat storage.",
        "Qiang Sun",
        truncated=False,
    )[0]
    assert false_author_grade not in (MUST, WEEKLY), false_author_grade
    ok.append("scenario11: tracked 作者名不再绕过主题门槛 PASS")

    conn.close()
    os.remove(tmp)
    import shutil
    shutil.rmtree(tmp_root, ignore_errors=True)
    PAPERS_DIR, DAILY_DIR, LOG_DIR = _saved
    print("\n".join(ok))
    print("SELFTEST ALL PASS")
    return True


# ================= CLI =================

def main():
    ap = argparse.ArgumentParser(
        prog="literature_agent.py",
        description="literature-agent V2 (Research Radar) — 文献收集/筛选/处理/简报/覆盖评估/作者与引用雷达/反馈闭环",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""命令示例:
  python3 literature_agent.py daily                   # 整链：发现 -> 优先级 -> A 队列 -> 用户向 digest
  python3 literature_agent.py collect --days 7        # 仅抓取（--days 用于 arXiv 窗口/backfill 语义）
  python3 literature_agent.py backfill --days 7       # 补抓缺失日期
  python3 literature_agent.py screen --grade <work_id> <MUST|WEEKLY|RADAR|DROP> --reason "..."
  python3 literature_agent.py evaluate-screening      # 对 gold 集跑回归
  python3 literature_agent.py doctor                  # 来源健康 + 数据库状态
  python3 literature_agent.py zotero-mark <work_id> accept|reject|defer
""")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--days", type=int, default=None, help="collect/backfill 天数窗口")
    common.add_argument("--grade", nargs=2, metavar=("WORK_ID", "GRADE"), help="人工覆盖分级")
    common.add_argument("--reason", default="", help="人工覆盖理由")
    common.add_argument("--work", default=None, help="仅处理指定 work_id")
    common.add_argument("--resolve", nargs=3, metavar=("WID_A", "WID_B", "ACTION"), help="dedup 裁决 merge|reject")
    common.add_argument("--rebuild-gold", action="store_true", help="重建 gold 集")
    common.add_argument("--historical", action="store_true", help="screen: 处理 historical replay 工作")
    common.add_argument("--apply", action="store_true", help="update-gold: 真正写入 screening_gold.jsonl")
    common.add_argument("--note", default="", help="feedback 备注")

    daily_parser = sub.add_parser("daily", parents=[common], help="collect -> screen -> process -> A 队列 -> digest（日常唯一入口；当天已成功则自动跳过，--force 强制重跑）")
    daily_parser.add_argument("--force", action="store_true", help="当天已成功运行也强制重跑完整流程")
    sub.add_parser("bootstrap", parents=[common], help="把测试阶段历史迁移为已消费记录")
    sub.add_parser("collect", parents=[common], help="抓取全部启用来源（--days 覆盖 arXiv 窗口）")
    sub.add_parser("screen", parents=[common], help="对 queued work 自动分级（--grade 人工覆盖）")
    sub.add_parser("process", parents=[common], help="处理未处理的 MUST（PDF + SI + A 建档目录，幂等）")
    sub.add_parser("digest", parents=[common], help="生成用户向 daily/YYYY-MM-DD.md（v4 格式）")
    overview_parser = sub.add_parser("overview", parents=[common], help="生成/刷新 Obsidian 每日文献总览（每日文献/YYYY-MM-DD 文献总览.md）")
    overview_parser.add_argument("--date", default=None, help="YYYY-MM-DD（默认今天）")
    sync_ov_parser = sub.add_parser("sync-overview", parents=[common], help="读取用户在每日总览中的批注并应用（disposition/flags/交接/方向反馈候选）")
    sync_ov_parser.add_argument("--date", default=None, help="YYYY-MM-DD（默认今天）")
    sync_ov_parser.add_argument("--all", action="store_true", help="同步所有已生成总览的批注（供每分钟轻量 agent 使用）")
    sub.add_parser("overview-test", parents=[common], help="synthetic 批注测试（临时目录，不碰真实 vault）")
    audit_parser = sub.add_parser("audit", parents=[common],
                                  help="系统审计：DB/文件系统/总览/状态一致性（非破坏）")
    audit_parser.add_argument("--full", action="store_true",
                              help="快速审计 + acceptance 全套（临时 DB/vault）+ smoke 摘要")
    sub.add_parser("weekly", parents=[common], help="生成 weekly/YYYY-Www.md 周复盘")
    sub.add_parser("doctor", parents=[common], help="来源健康检查 + 数据库统计")
    sub.add_parser("backfill", parents=[common], help="补抓缺失日期（--days N；覆盖能力如实报告）")
    sub.add_parser("evaluate-screening", parents=[common], help="对 screening_gold.jsonl 回归评估，输出 screening_eval.md")
    sub.add_parser("build-gold", parents=[common], help="重建 screening_gold.jsonl（从历史测试产物）")
    sub.add_parser("dedup-test", parents=[common], help="运行 synthetic 去重测试（37 用例）")
    sub.add_parser("dedup-review", parents=[common], help="查看 pending 去重对（--resolve A B merge|reject）")
    sub.add_parser("zotero-queue", parents=[common], help="把当日 MUST/WEEKLY 写入本地 Zotero 决策队列")
    sub.add_parser("zotero-mark", parents=[common], help="本地队列标记 WORK_ID accept|reject|defer")
    sub.add_parser("zotero-export", parents=[common], help="把已勾选“存 Zotero”的论文导出为 BibTeX，拖入 Zotero 即自动建档")
    sub.add_parser("zotero-sync", parents=[common], help="自动推送本地队列到 Zotero（需 config/local.json 配置 zotero api_key/user_id/auto_sync）")
    sub.add_parser("reading-status", parents=[common], help="同步/显示阅读状态机")
    a_queue_parser = sub.add_parser("a-queue", parents=[common], help="核验 A 并生成待执行清单（三队列视图）")
    a_queue_parser.add_argument("--kind", choices=("live_must", "historical_recovery", "weekly"), default=None,
                                help="只看某个队列（默认全部）")
    a_complete_parser = sub.add_parser("a-complete", parents=[common], help="核验并标记一篇 A 已真正完成")
    a_complete_parser.add_argument("work_id")
    a_complete_parser.add_argument("--path", default=None, help="A Markdown 路径；默认自动寻找")
    decide_parser = sub.add_parser(
        "decide", parents=[common],
        help="disposition（互斥）：read/later/ignore；兼容旧用法可传 save_zotero/competitor（等价 flag）")
    decide_parser.add_argument("work_id")
    decide_parser.add_argument("decision", choices=("read", "later", "ignore", "save_zotero", "competitor"))
    flag_parser = sub.add_parser("flag", parents=[common], help="设置/取消 flags（save_zotero|competitor），不改变 disposition")
    flag_parser.add_argument("work_id")
    flag_parser.add_argument("flag", choices=("save_zotero", "competitor"))
    flag_parser.add_argument("--unset", action="store_true", help="取消该 flag（默认设置）")
    sub.add_parser("a-job", parents=[common], help="生成一次 A worker 任务清单（Codex 连续消费，不逐篇确认）").add_argument(
        "--kind", choices=("all", "live_must", "historical_recovery", "weekly"), default="all")
    sub.add_parser("a-job-status", parents=[common], help="查看最新 A worker job 的逐篇进度")
    context_parser = sub.add_parser("context", parents=[common], help="输出一篇论文继续 A/B/C/D 所需的最小上下文")
    context_parser.add_argument("work_id")
    sub.add_parser("a-status", parents=[common], help="显示推荐等级之外的 A/B/C/D 阅读阶段")
    sub.add_parser("handoff", parents=[common], help="刷新 HANDOFF.md 与 PROJECT_STATE.json")
    sub.add_parser("stats", parents=[common], help="数据库统计")
    sub.add_parser("backup", parents=[common], help="备份 DB 与配置到 backups/（保留最近 10 份）")
    sub.add_parser("dump-queue", parents=[common], help="打印待筛选队列")
    sub.add_parser("selftest", parents=[common], help="自检（临时 DB/mock，不污染正式数据）")
    sub.add_parser("selftest-v2", parents=[common], help="V2 自检（15 个新场景，临时 DB/mock）")
    sub.add_parser("evaluate-coverage", parents=[common], help="coverage gold 历史召回评估 → coverage_eval.md")
    sub.add_parser("build-coverage-gold", parents=[common], help="从 Zotero 快照重建 coverage_gold.jsonl")
    sub.add_parser("coverage-gaps", parents=[common], help="覆盖缺口分析 → coverage_gaps.md")
    sub.add_parser("author-watch", parents=[common], help="tracked 作者新工作（OpenAlex，signal=tracked_author）")
    sub.add_parser("citation-watch", parents=[common], help="引用网络雷达（cites core seeds）")
    sub.add_parser("crossref-fallback", parents=[common], help="blocked 期刊 Crossref metadata fallback")
    import_parser = sub.add_parser("import", parents=[common], help="通用导入 JSONL/CSV/BibTeX/RIS/文本（--scholar 扫描 imports/scholar/）")
    import_parser.add_argument("file", nargs="?")
    import_parser.add_argument("--scholar", action="store_true", help="导入 imports/scholar/ 全部文件")
    feedback_parser = sub.add_parser("feedback", parents=[common], help="记录反馈 feedback <work_id> <ACTION>")
    feedback_parser.add_argument("work_id", nargs="?")
    feedback_parser.add_argument("action", nargs="?")
    sub.add_parser("update-gold", parents=[common], help="反馈→gold 候选（--apply 写入 screening_gold.jsonl）")
    sub.add_parser("prefetch", parents=[common], help="为 MUST（及合格 WEEKLY）预取合法 OA/arXiv PDF")
    sub.add_parser("monthly", parents=[common], help="生成月度复盘 monthly/YYYY-MM.md")
    sub.add_parser("source-value", parents=[common], help="来源价值分析 → source_value.md")
    sub.add_parser("historical-replay", parents=[common], help="历史回放 2026-06-01→今天（arXiv/Crossref/OpenAlex）")
    sub.add_parser("missed-report", parents=[common], help="“你可能漏读了什么” → missed_literature_report.md")
    sub.add_parser("competitor-watch", parents=[common], help="竞品雷达 → competitor_watch.md")
    sub.add_parser("resolve-authors", parents=[common], help="重新解析 tracked 作者（OpenAlex）")

    args = ap.parse_args()
    cmd = args.cmd

    if cmd is None:
        ap.print_help()
        return
    if cmd == "bootstrap":
        st = bootstrap()
        print(json.dumps(st, ensure_ascii=False, indent=1))
    elif cmd == "collect":
        s = run_collect(days=args.days)
        print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    elif cmd == "screen":
        if args.grade:
            conn = connect()
            priority = normalize_priority(args.grade[1])
            override_grade(conn, args.grade[0], priority, args.reason)
            print(f"override {args.grade[0]} -> {priority}")
        else:
            c = run_screen(historical=bool(args.historical))
            print("screen grades:", dict(c))
    elif cmd == "process":
        r = run_process(force_work=args.work)
        print(json.dumps(r, ensure_ascii=False, indent=1))
    elif cmd == "digest":
        from la_digest import run_daily_v4
        print("digest:", run_daily_v4(connect(), date.today().isoformat()))
    elif cmd == "overview":
        from la_overview import run_overview
        print(json.dumps(run_overview(connect(), args.date or date.today().isoformat()), ensure_ascii=False, indent=1))
    elif cmd == "sync-overview":
        from la_overview import run_sync_overview, sync_old_overviews
        conn = connect()
        if getattr(args, "all", False):
            result = sync_old_overviews(conn, "9999-12-31")
        else:
            result = run_sync_overview(conn, args.date or date.today().isoformat())
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif cmd == "overview-test":
        import la_overview_test
        ok = la_overview_test.run_all()
        print("overview-test:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    elif cmd == "audit":
        from la_audit import run_audit, run_full_audit, summarize
        checks = run_full_audit() if args.full else run_audit()
        s = summarize(checks)
        print(json.dumps({
            "audit": "PASS" if s["failed"] == 0 else "FAIL",
            "summary": {"total": s["total"], "passed": s["passed"], "failed": s["failed"]},
            "failures": [{"name": c["name"], "detail": c["detail"]} for c in s["failures"]],
        }, ensure_ascii=False, indent=1))
        if s["failed"]:
            sys.exit(1)
    elif cmd == "weekly":
        from la_digest import run_weekly_v2
        print("weekly:", run_weekly_v2(connect()))
    elif cmd == "daily":
        s = run_daily(force=getattr(args, "force", False))
        print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    elif cmd == "doctor":
        from la_health import doctor
        health, report = doctor(connect())
        print(report)
    elif cmd == "backfill":
        from la_backfill import run_backfill
        s = run_backfill(connect(), days=args.days or 7)
        print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
        print("report:", la_backfill.REPORT_PATH)
    elif cmd == "evaluate-screening":
        import la_eval
        m = la_eval.run_evaluate()
        print(json.dumps({k: v for k, v in m.items() if not k.endswith("list")}, ensure_ascii=False, indent=1))
        print("report:", la_eval.EVAL_PATH)
    elif cmd == "build-gold":
        import la_eval
        g = la_eval.build_gold(force=True)
        from collections import Counter
        print("gold:", len(g), dict(Counter(x["expected_grade"] for x in g)))
    elif cmd == "dedup-test":
        import la_dedup
        p, f = la_dedup.run_synthetic_tests()
        print(f"dedup synthetic: {p}/{p + len(f)} passed")
        for x in f:
            print("  FAIL:", x)
        if f:
            sys.exit(1)
    elif cmd == "dedup-review":
        conn = connect()
        if args.resolve:
            a, b, action = args.resolve
            if action not in ("merge", "reject"):
                print("ACTION must be merge|reject")
                sys.exit(1)
            row = conn.execute(
                "SELECT * FROM dedup_review WHERE (work_id_a=? AND work_id_b=?) OR (work_id_a=? AND work_id_b=?)",
                (a, b, b, a)).fetchone()
            if not row:
                print("pair not found")
                sys.exit(1)
            if action == "merge":
                _merge_works(conn, a, b)
                print(f"merged {a} <- {b}")
            conn.execute("UPDATE dedup_review SET status=?, reviewed_at=? WHERE id=?",
                         ("merged" if action == "merge" else "rejected", now_iso(), row["id"]))
            conn.commit()
        rows = conn.execute("SELECT * FROM dedup_review WHERE status='pending' ORDER BY score DESC").fetchall()
        print(f"pending pairs: {len(rows)}")
        for r in rows:
            wa = conn.execute("SELECT title FROM works WHERE work_id=?", (r["work_id_a"],)).fetchone()
            wb = conn.execute("SELECT title FROM works WHERE work_id=?", (r["work_id_b"],)).fetchone()
            print(f"- [{r['match_kind']} {r['score']:.2f}] {r['work_id_a']} <-> {r['work_id_b']}")
            print(f"    A: {wa['title'][:70] if wa else '?'}")
            print(f"    B: {wb['title'][:70] if wb else '?'}")
            print(f"    {r['evidence']}")
    elif cmd == "zotero-queue":
        from la_zotero import generate_queue, load_queue
        conn = connect()
        n = generate_queue(conn, log=lambda *a: print(" ", *a))
        entries = load_queue()
        print(f"queued today: {n}; total in queue: {len(entries)}")
        for e in entries:
            if e["status"] == "pending_user":
                print(f"- [{e['grade']}] {e['title'][:70]} | {e['main_collection']} | status={e['status']}")
    elif cmd == "zotero-export":
        from la_zotero import export_bibtex
        conn = connect()
        print(json.dumps(export_bibtex(conn), ensure_ascii=False, indent=1))
    elif cmd == "zotero-sync":
        from la_zotero import zotero_sync_queue
        conn = connect()
        print(json.dumps(zotero_sync_queue(conn), ensure_ascii=False, indent=1))
    elif cmd == "zotero-mark":
        from la_zotero import mark
        if len(sys.argv) < 4:
            print("usage: zotero-mark <work_id> accept|reject|defer")
            sys.exit(1)
        work_id, status = sys.argv[2], sys.argv[3]
        ok = mark(work_id, status)
        print("marked" if ok else "work_id not in queue")
    elif cmd == "reading-status":
        from la_db import sync_reading_status, db_stats
        conn = connect()
        st = sync_reading_status(conn)
        print("reading status:", st)
        print("grades:", db_stats(conn)["grades"])
    elif cmd == "a-queue":
        from la_queues import queue_state
        from la_workflow import build_a_queue
        conn = connect()
        build_a_queue(conn)
        q = queue_state(conn)
        if args.kind:
            key = args.kind
            print(json.dumps({key: q[key]}, ensure_ascii=False, indent=1))
        else:
            print(json.dumps({
                "live_must": {"pending": q["live_must"]["pending"]},
                "historical_recovery": {"pending": q["historical_recovery"]["pending"]},
                "weekly": {"pending": q["weekly"]["pending"]},
                "blocked_pdf": q["blocked_pdf"],
                "must_total": q["must_total"],
                "queue_path": "a_queue.jsonl",
            }, ensure_ascii=False, indent=1))
            for kind in ("live_must", "historical_recovery"):
                for e in q[kind]["entries"]:
                    print(f"- [{kind}] {e['a_status']} {e['work_id']} {e['title'][:80]}")
    elif cmd == "a-complete":
        from la_workflow import build_a_queue, mark_a_completed
        conn = connect()
        result = mark_a_completed(conn, args.work_id, path=args.path)
        result["queue"] = build_a_queue(conn)
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif cmd == "decide":
        from la_workflow import apply_decision
        print(json.dumps(
            apply_decision(connect(), args.work_id, args.decision, note=args.note),
            ensure_ascii=False, indent=1,
        ))
    elif cmd == "flag":
        from la_workflow import set_flag
        print(json.dumps(
            set_flag(connect(), args.work_id, args.flag, on=not args.unset, note=args.note),
            ensure_ascii=False, indent=1,
        ))
    elif cmd == "a-job":
        from la_aworker import create_job, render_README
        conn = connect()
        render_README()
        print(json.dumps(create_job(conn, kind=args.kind), ensure_ascii=False, indent=1))
    elif cmd == "a-job-status":
        from la_aworker import job_status
        print(json.dumps(job_status(connect()), ensure_ascii=False, indent=1))
    elif cmd == "context":
        from la_workflow import context_package
        print(json.dumps(context_package(connect(), args.work_id), ensure_ascii=False, indent=1))
    elif cmd == "a-status":
        from la_workflow import status_summary
        print(json.dumps(status_summary(connect()), ensure_ascii=False, indent=1))
    elif cmd == "handoff":
        from la_handoff import run_handoff
        print(json.dumps(run_handoff(), ensure_ascii=False, indent=1))
    elif cmd == "stats":
        from la_db import db_stats
        conn = connect()
        st = db_stats(conn)
        for k in ("works", "work_sources", "screenings", "processing", "work_versions",
                  "artifacts", "errors", "source_runs", "dedup_review_pending"):
            print(f"{k}: {st[k]}")
        print("status:", st["status"])
        print("grades:", st["grades"])
        print("reading_status:", st["reading_status"])
        conn.close()
    elif cmd == "backup":
        from la_backup import run_backup
        dest, copied = run_backup(connect())
        print("backup ->", dest)
        print("  files:", ", ".join(copied))
    elif cmd == "dump-queue":
        conn = connect()
        rows = conn.execute("""SELECT w.work_id, w.title, w.journal, w.status, w.first_seen,
            (SELECT abstract FROM work_sources ws WHERE ws.work_id=w.work_id ORDER BY length(abstract) DESC LIMIT 1) AS abstract
            FROM works w WHERE w.queued=1 ORDER BY w.first_seen""").fetchall()
        for r in rows:
            print(f"\n[{r['work_id']}] ({r['status']} / {r['journal']}) {r['title']}")
            if r["abstract"]:
                print("  abs:", r["abstract"][:400].replace("\n", " "))
        print(f"\nTOTAL QUEUED: {len(rows)}")
        conn.close()
    elif cmd == "selftest-v2":
        import la_selftest_v2
        ok = la_selftest_v2.run_all()
        print("selftest-v2:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    elif cmd == "evaluate-coverage":
        import la_coverage
        st = la_coverage.evaluate_coverage()
        print(json.dumps({k: v for k, v in st.items() if not isinstance(v, (list, dict))}, ensure_ascii=False, indent=1))
        print("report:", la_coverage.EVAL_PATH)
    elif cmd == "build-coverage-gold":
        import la_coverage
        e = la_coverage.build_coverage_gold(force=True)
        print("coverage gold:", la_coverage.gold_summary())
    elif cmd == "coverage-gaps":
        import la_coverage
        print("report:", la_coverage.coverage_gaps(connect()))
    elif cmd == "author-watch":
        import la_tracked
        s = la_tracked.author_watch(connect(), days=args.days or 7)
        print(json.dumps({k: v for k, v in s.items() if k != "per_author"}, ensure_ascii=False, indent=1, default=str))
    elif cmd == "citation-watch":
        import la_citation
        s = la_citation.citation_watch(connect(), days=args.days or 7)
        print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    elif cmd == "crossref-fallback":
        import la_discovery
        records, per = la_discovery.collect_crossref_journals(connect(), date.today().isoformat(),
                                                              days=args.days or 7)
        print(json.dumps(per, ensure_ascii=False, indent=1))
        print("records:", len(records))
    elif cmd == "import":
        import la_import
        conn = connect()
        if args.scholar:
            s = la_import.run_scholar_import(conn)
        elif args.file:
            s = la_import.run_import(args.file, conn=conn)
        else:
            print("usage: import <file> 或 import --scholar")
            sys.exit(1)
        print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
    elif cmd == "feedback":
        import la_feedback
        if not args.work_id or not args.action:
            print("usage: feedback <work_id> <ACTION> [--note ...]")
            sys.exit(1)
        r = la_feedback.run_feedback(connect(), args.work_id, args.action, args.note)
        print(json.dumps(r, ensure_ascii=False, indent=1))
    elif cmd == "update-gold":
        import la_feedback
        r = la_feedback.gold_candidates(connect(), apply=args.apply)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        print("candidates:", la_feedback.GOLD_CAND_PATH)
    elif cmd == "prefetch":
        import la_prefetch
        s = la_prefetch.run_prefetch(connect())
        print(json.dumps(s, ensure_ascii=False, indent=1))
    elif cmd == "monthly":
        import la_monthly
        print("monthly:", la_monthly.run_monthly(connect()))
    elif cmd == "source-value":
        import la_sourcevalue
        print("report:", la_sourcevalue.run_source_value(connect()))
    elif cmd == "historical-replay":
        import la_replay
        conn = connect()
        s = la_replay.run_replay(conn)
        print(json.dumps({k: v for k, v in s.items() if k != "sources"}, ensure_ascii=False, indent=1))
        print("report:", la_replay.write_replay_report(conn, s))
    elif cmd == "missed-report":
        import la_missed
        print("report:", la_missed.missed_report(connect()))
    elif cmd == "competitor-watch":
        import la_signals
        print("alerts:", la_signals.competitor_watch(connect()))
    elif cmd == "resolve-authors":
        import la_tracked
        la_tracked.resolve_all(force=True, conn=connect())
        print("tracked entities resolved ->", la_tracked.REPORT_PATH)
    elif cmd == "selftest":
        selftest()


def _merge_works(conn, keep_wid, drop_wid):
    """事务化合并所有引用 work_id 的表；任一冲突失败就整体回滚。"""
    if keep_wid == drop_wid:
        raise ValueError("cannot merge a work into itself")
    keep = conn.execute("SELECT * FROM works WHERE work_id=?", (keep_wid,)).fetchone()
    drop = conn.execute("SELECT * FROM works WHERE work_id=?", (drop_wid,)).fetchone()
    if keep is None or drop is None:
        raise ValueError("both keep/drop works must exist")
    conn.execute("SAVEPOINT merge_works")
    try:
        title = drop["title"] if len(drop["title"] or "") > len(keep["title"] or "") else keep["title"]
        stage_rank = {"new": 0, "arxiv": 1, "accepted": 2, "published": 3, "journal": 3}
        stage = max((keep["status"], drop["status"]), key=lambda s: stage_rank.get(s or "", 0))
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(works)")}
        updates = {
            "title": title,
            "normalized_title": norm_title(title),
            "authors": keep["authors"] or drop["authors"],
            "doi": keep["doi"] or drop["doi"],
            "arxiv_id": keep["arxiv_id"] or drop["arxiv_id"],
            "journal": keep["journal"] or drop["journal"],
            "status": stage,
            "first_seen": min(x for x in (keep["first_seen"], drop["first_seen"]) if x),
            "latest_seen": max(x for x in (keep["latest_seen"], drop["latest_seen"]) if x),
            "queued": max(keep["queued"] or 0, drop["queued"] or 0),
            "consumed_history": min(keep["consumed_history"] or 0, drop["consumed_history"] or 0),
        }
        if "historical_discovery" in cols:
            updates["historical_discovery"] = min(
                keep["historical_discovery"] or 0, drop["historical_discovery"] or 0)
        if "rank_score" in cols:
            updates["rank_score"] = max(keep["rank_score"] or 0, drop["rank_score"] or 0)
        if "signal_grade" in cols:
            updates["signal_grade"] = keep["signal_grade"] or drop["signal_grade"]
        if "reading_status" in cols:
            user_stages = {"processing", "callable", "read", "ignored", "deferred"}
            updates["reading_status"] = (
                keep["reading_status"] if keep["reading_status"] in user_stages
                else drop["reading_status"] if drop["reading_status"] in user_stages
                else keep["reading_status"] or drop["reading_status"]
            )
        sets = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE works SET {sets} WHERE work_id=?", (*updates.values(), keep_wid))

        for row in conn.execute("SELECT * FROM work_sources WHERE work_id=?", (drop_wid,)).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO work_sources "
                "(work_id,source,version,title,url,abstract,abstract_truncated,date) VALUES (?,?,?,?,?,?,?,?)",
                (keep_wid, row["source"], row["version"], row["title"], row["url"], row["abstract"],
                 row["abstract_truncated"], row["date"]),
            )
        conn.execute("DELETE FROM work_sources WHERE work_id=?", (drop_wid,))

        for row in conn.execute("SELECT * FROM work_versions WHERE work_id=?", (drop_wid,)).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO work_versions "
                "(work_id,source,source_version,title,abstract_hash,metadata_hash,seen_at,content_changed) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (keep_wid, row["source"], row["source_version"], row["title"], row["abstract_hash"],
                 row["metadata_hash"], row["seen_at"], row["content_changed"]),
            )
        conn.execute("DELETE FROM work_versions WHERE work_id=?", (drop_wid,))

        for row in conn.execute("SELECT * FROM processing WHERE work_id=?", (drop_wid,)).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO processing "
                "(work_id,run_id,pdf_status,pdf_path,si_status,si_path,a_status,a_path,notes,processed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (keep_wid, row["run_id"], row["pdf_status"], row["pdf_path"], row["si_status"],
                 row["si_path"], row["a_status"], row["a_path"], row["notes"], row["processed_at"]),
            )
        conn.execute("DELETE FROM processing WHERE work_id=?", (drop_wid,))

        for table in ("artifacts", "screenings", "errors", "feedback",
                      "recommendation_signals", "workflow_events"):
            conn.execute(f"UPDATE {table} SET work_id=? WHERE work_id=?", (keep_wid, drop_wid))
        conn.execute("UPDATE citation_seeds SET work_id=? WHERE work_id=?", (keep_wid, drop_wid))

        edges = conn.execute(
            "SELECT * FROM citation_edges WHERE seed_work_id=? OR citing_work_id=?",
            (drop_wid, drop_wid),
        ).fetchall()
        conn.execute(
            "DELETE FROM citation_edges WHERE seed_work_id=? OR citing_work_id=?",
            (drop_wid, drop_wid),
        )
        for row in edges:
            seed = keep_wid if row["seed_work_id"] == drop_wid else row["seed_work_id"]
            citing = keep_wid if row["citing_work_id"] == drop_wid else row["citing_work_id"]
            if seed != citing:
                conn.execute(
                    "INSERT OR IGNORE INTO citation_edges "
                    "(seed_work_id,citing_work_id,relation,source,detected_at) VALUES (?,?,?,?,?)",
                    (seed, citing, row["relation"], row["source"], row["detected_at"]),
                )

        reviews = conn.execute(
            "SELECT * FROM dedup_review WHERE work_id_a=? OR work_id_b=?",
            (drop_wid, drop_wid),
        ).fetchall()
        conn.execute(
            "DELETE FROM dedup_review WHERE work_id_a=? OR work_id_b=?",
            (drop_wid, drop_wid),
        )
        for row in reviews:
            a = keep_wid if row["work_id_a"] == drop_wid else row["work_id_a"]
            b = keep_wid if row["work_id_b"] == drop_wid else row["work_id_b"]
            if a == b:
                continue
            a, b = sorted((a, b))
            conn.execute(
                "INSERT OR IGNORE INTO dedup_review "
                "(work_id_a,work_id_b,match_kind,score,evidence,status,created_at,reviewed_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (a, b, row["match_kind"], row["score"], row["evidence"], row["status"],
                 row["created_at"], row["reviewed_at"]),
            )
        conn.execute("DELETE FROM works WHERE work_id=?", (drop_wid,))
        conn.execute("RELEASE SAVEPOINT merge_works")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT merge_works")
        conn.execute("RELEASE SAVEPOINT merge_works")
        raise


if __name__ == "__main__":
    main()
