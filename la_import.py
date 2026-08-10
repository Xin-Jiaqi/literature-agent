#!/usr/bin/env python3
"""literature-agent V2 通用导入适配器：
- JSONL / CSV / BibTeX / RIS / 纯文本 DOI/arXiv/URL 列表
- imports/scholar/ 未来手动导出的 Scholar 文件（CSV/BibTeX/邮件文本/URL 列表）
导入后统一 normalize -> dedup(ingest_record) -> queue -> screen。
"""
import csv
import io
import json
import os
import re

from la_v2common import clean_title, norm_arxiv, norm_doi

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import IMPORT_ROOT
SCHOLAR_DIR = os.path.join(IMPORT_ROOT, "scholar")
TESTDATA_DIR = os.path.join(IMPORT_ROOT, "testdata")

SUPPORTED = {"jsonl", "json", "csv", "bib", "ris", "txt", "text"}


def parse_records(path, source_tag="import"):
    """按扩展名解析文件 → 统一 rec 列表（与 ingest_record 兼容）。"""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext not in SUPPORTED:
        raise ValueError(f"unsupported file type .{ext} (支持: {','.join(sorted(SUPPORTED))})")
    text = open(path, encoding="utf-8", errors="replace").read()
    if ext in ("jsonl", "json"):
        recs = _parse_jsonl(text)
    elif ext == "csv":
        recs = _parse_csv(text)
    elif ext == "bib":
        recs = _parse_bibtex(text)
    elif ext == "ris":
        recs = _parse_ris(text)
    else:
        recs = _parse_text(text)
    for r in recs:
        r["status"] = r.get("status") or "journal"
        r["version"] = r.get("version") or ""
        r["abstract_truncated"] = bool(r.get("abstract_truncated", False))
    return recs


def _parse_jsonl(text):
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if isinstance(d, list):
            out.extend(_norm(d))
            continue
        if isinstance(d, dict) and d.get("title"):
            out.append(_norm([d])[0])
    return out


def _norm(rows):
    out = []
    for d in rows:
        title = clean_title(d.get("title") or d.get("Title") or "")
        if not title:
            continue
        authors = d.get("authors") or d.get("Authors") or d.get("author") or ""
        if isinstance(authors, list):
            authors = "; ".join(a if isinstance(a, str) else (a.get("name") or f"{a.get('given','')} {a.get('family','')}") for a in authors)
        rec = {"title": title, "authors": authors,
               "doi": norm_doi(d.get("doi") or d.get("DOI") or ""),
               "arxiv_id": norm_arxiv(d.get("arxiv_id") or d.get("arXiv") or d.get("eprint") or ""),
               "abstract": d.get("abstract") or d.get("Abstract") or "",
               "date": (d.get("date") or d.get("year") or d.get("publication_date") or ""),
               "url": d.get("url") or d.get("URL") or "",
               "journal": d.get("journal") or d.get("Journal") or d.get("venue") or ""}
        out.append(rec)
    return out


def _parse_csv(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    keys = {k.lower(): k for k in rows[0].keys()} if rows else {}
    out = []
    for d in rows:
        title = clean_title(d.get(keys.get("title", "title"), "") if "title" in keys else d.get("Title", ""))
        rec = {"title": title,
               "authors": d.get(keys.get("authors", "authors"), "") if "authors" in keys else "",
               "doi": norm_doi(d.get(keys.get("doi", "doi"), "") if "doi" in keys else ""),
               "arxiv_id": norm_arxiv(d.get(keys.get("arxiv_id", "arxiv_id"), "") if "arxiv_id" in keys else ""),
               "abstract": d.get(keys.get("abstract", "abstract"), "") if "abstract" in keys else "",
               "date": d.get(keys.get("date", "date"), "") if "date" in keys else "",
               "url": d.get(keys.get("url", "url"), "") if "url" in keys else "",
               "journal": d.get(keys.get("journal", "journal"), "") if "journal" in keys else ""}
        if rec["title"]:
            out.append(rec)
    return out


def _parse_bibtex(text):
    out = []
    for m in re.finditer(r"@\w+\s*\{\s*[^,]+,\s*(.*?)\n\s*\}", text, re.S):
        body = m.group(1)
        fields = {}
        for fm in re.finditer(r"(\w+)\s*=\s*[\{](.*?)[\}]", body, re.S):
            fields[fm.group(1).lower()] = fm.group(2).strip()
        title = clean_title(re.sub(r"[\{\}\\]", "", fields.get("title", "")))
        if not title:
            continue
        authors = re.sub(r"[\{\}\\]", "", fields.get("author", "")).replace(" and ", "; ")
        doi = norm_doi(fields.get("doi", ""))
        arx = norm_arxiv(fields.get("eprint", ""))
        rec = {"title": title, "authors": authors, "doi": doi, "arxiv_id": arx,
               "abstract": fields.get("abstract", ""), "date": fields.get("year", ""),
               "url": fields.get("url", ""), "journal": fields.get("journal", fields.get("booktitle", ""))}
        out.append(rec)
    return out


def _parse_ris(text):
    out, cur = [], {}
    for ln in text.splitlines():
        ln = ln.rstrip("\r")
        if not ln.strip():
            continue
        if ln.startswith("ER"):
            if cur.get("title"):
                out.append(cur)
            cur = {}
            continue
        m = re.match(r"^([A-Z0-9]{2})\s*-\s*(.*)$", ln)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TI":
            cur["title"] = (cur.get("title", "") + " " + val).strip()
        elif tag == "AU":
            cur["authors"] = (cur.get("authors", "") + "; " + val).strip("; ")
        elif tag == "DO":
            cur["doi"] = norm_doi(val)
        elif tag == "AB":
            cur["abstract"] = (cur.get("abstract", "") + " " + val).strip()
        elif tag == "PY":
            cur["date"] = val[:4]
        elif tag == "UR":
            cur["url"] = val
        elif tag == "JO":
            cur["journal"] = val
        elif tag == "ID":
            cur["arxiv_id"] = norm_arxiv(val)
    if cur.get("title"):
        out.append(cur)
    for r in out:
        r.setdefault("authors", "")
        r.setdefault("abstract", "")
        r.setdefault("date", "")
        r.setdefault("url", "")
        r.setdefault("journal", "")
        r.setdefault("arxiv_id", "")
    return out


def _parse_text(text):
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        doi = norm_doi(ln)
        arx = norm_arxiv(ln)
        url = ln if ln.startswith("http") else ""
        if doi or arx or url:
            rec = {"title": (url.split("/")[-1].replace("_", " ")[:120] if url else ln[:120]),
                   "authors": "", "doi": doi or None, "arxiv_id": arx or None,
                   "abstract": "", "date": "", "url": url, "journal": ""}
            if arx and not doi and not url:
                rec["url"] = f"https://arxiv.org/abs/{arx}"
            out.append(rec)
    return out


def run_import(path, conn=None, source_tag="import", log=None, history=False):
    """导入文件 → ingest（去重/排队）。返回 summary。"""
    import time
    from datetime import date
    from literature_agent import ingest_record
    log = log or (lambda *a: None)
    own = conn is None
    if own:
        from la_db2 import connect
        conn = connect()
    recs = parse_records(path, source_tag)
    summary = {"file": path, "parsed": len(recs), "new": 0, "updated": 0, "noop": 0, "failed": 0}
    for rec in recs:
        if history:
            rec["status"] = "arxiv" if rec.get("arxiv_id") else "journal"
        try:
            wid, action, _ = ingest_record(conn, rec, source_tag, date.today().isoformat(), history=history)
        except Exception as e:
            summary["failed"] += 1
            log("error", f"import failed: {e}")
            continue
        if action == "new":
            summary["new"] += 1
        elif action in ("updated", "requeued"):
            summary["updated"] += 1
        else:
            summary["noop"] += 1
        log("ingest", f"{source_tag} {action} {wid} :: {rec['title'][:60]}")
    if own:
        conn.close()
    return summary


def run_scholar_import(conn=None, log=None):
    """导入 imports/scholar/ 下所有文件（手动导出的 Scholar 数据）。"""
    log = log or (lambda *a: None)
    if not os.path.isdir(SCHOLAR_DIR):
        return {"files": 0, "note": "imports/scholar/ 不存在，跳过"}
    total = {"files": 0, "parsed": 0, "new": 0, "updated": 0, "noop": 0}
    for fn in sorted(os.listdir(SCHOLAR_DIR)):
        if fn.startswith("."):
            continue
        p = os.path.join(SCHOLAR_DIR, fn)
        if os.path.isdir(p):
            continue
        try:
            s = run_import(p, conn=conn, source_tag="scholar_import", log=log)
        except Exception as e:
            log("error", f"scholar import {fn} failed: {e}")
            continue
        for k in ("parsed", "new", "updated", "noop"):
            total[k] += s.get(k, 0)
        total["files"] += 1
    return total


def generate_synthetic(n=24):
    """synthetic 导入测试数据（覆盖全部格式）。"""
    os.makedirs(TESTDATA_DIR, exist_ok=True)
    out = []
    for i in range(n):
        out.append({
            "title": f"Synthetic Import Work {i:02d} on Sliding Ferroelectricity in Bilayer Systems",
            "authors": [f"Author {i}A", f"Author {i}B"], "doi": f"10.5555/synth.{i:04d}",
            "arxiv_id": f"2608.{1000 + i:04d}", "abstract": f"Synthetic abstract {i} about shift current and sliding ferroelectricity.",
            "date": f"2026-{7 - i % 5:02d}-{1 + i % 27:02d}", "journal": "Synthetic Journal", "url": f"https://doi.org/10.5555/synth.{i:04d}"})
    paths = {}
    p = os.path.join(TESTDATA_DIR, "synthetic.jsonl")
    with open(p, "w") as f:
        for r in out[:8]:
            f.write(json.dumps(r) + "\n")
    paths["jsonl"] = p
    p = os.path.join(TESTDATA_DIR, "synthetic.csv")
    with open(p, "w") as f:
        f.write("title,authors,doi,arxiv_id,abstract,date,journal,url\n")
        for r in out[8:12]:
            f.write(f"{r['title']}|{'; '.join(r['authors'])}|{r['doi']}|{r['arxiv_id']}|{r['abstract']}|{r['date']}|{r['journal']}|{r['url']}\n".replace("|", ","))
    paths["csv"] = p
    p = os.path.join(TESTDATA_DIR, "synthetic.bib")
    with open(p, "w") as f:
        for r in out[12:16]:
            f.write(f"@article{{k{i},\n title = {{{r['title']}}},\n author = {{{'; '.join(r['authors'])}}},\n doi = {{{r['doi']}}},\n eprint = {{{r['arxiv_id']}}},\n year = {{2026}},\n journal = {{{r['journal']}}}\n}}\n")
    paths["bib"] = p
    p = os.path.join(TESTDATA_DIR, "synthetic.ris")
    with open(p, "w") as f:
        for r in out[16:20]:
            f.write(f"TY  - JOUR\nTI  - {r['title']}\nAU  - {r['authors'][0]}\nDO  - {r['doi']}\nAB  - {r['abstract']}\nPY  - 2026\nJO  - {r['journal']}\nER  - \n")
    paths["ris"] = p
    p = os.path.join(TESTDATA_DIR, "synthetic.txt")
    with open(p, "w") as f:
        for r in out[20:]:
            f.write(r["doi"] + "\n")
    paths["text"] = p
    return paths, out
