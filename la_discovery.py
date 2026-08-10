#!/usr/bin/env python3
"""literature-agent V2 公开元数据发现层（只做 metadata discovery，不做全文）：

- Crossref journal fallback：blocked 期刊按 ISSN 查最近 works（crossref_fallback 标记）
- OpenAlex：author watch / citation radar / related works / DOI 检索
规则：公开 API、rate limit + 磁盘缓存、无 key 可运行、网络失败不影响主流程、abstract 缺失不猜测。
"""
import json
import os
import re
import time

from la_v2common import (MAILTO, clean_title, http_get_json, norm_arxiv, norm_doi,
                         now_iso, read_json, write_json)

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import DATA_ROOT
JOURNAL_IDENTIFIERS_PATH = os.path.join(DATA_ROOT, "journal_identifiers.json")

# blocked 期刊目标（V2 §5）
BLOCKED_JOURNALS = [
    {"name": "Nano Letters", "issns": ["1530-6984", "1530-6992"], "publisher": "American Chemical Society"},
    {"name": "ACS Nano", "issns": ["1936-0851", "1936-086X"], "publisher": "American Chemical Society"},
    {"name": "Journal of Physical Chemistry Letters", "issns": ["1948-7185"], "publisher": "American Chemical Society"},
    {"name": "Advanced Materials", "issns": ["0935-9648", "1521-4095"], "publisher": "Wiley"},
    {"name": "Advanced Functional Materials", "issns": ["1616-301X", "1616-3028"], "publisher": "Wiley"},
    {"name": "2D Materials", "issns": ["2053-1583"], "publisher": "IOP Publishing"},
    {"name": "Applied Physics Letters", "issns": ["0003-6951", "1077-3118"], "publisher": "AIP Publishing"},
    {"name": "Journal of Applied Physics", "issns": ["0021-8979", "1089-7550"], "publisher": "AIP Publishing"},
    {"name": "Nano Research", "issns": ["1998-0124", "1998-0000"], "publisher": "Springer"},
]

# 额外纳入 Crossref 日期回补的期刊：RSS 正常但不可回补（只显示最新条目），
# 漏抓期间依赖 Crossref 按日期窗口回补。role 仅用于标注，采集逻辑一致。
CROSSREF_BACKFILL_JOURNALS = [
    {"name": "Nature Communications", "issns": ["2041-1723"], "publisher": "Springer Nature", "role": "backfill", "rows": 400},
    {"name": "Nature Materials", "issns": ["1476-1122", "1476-4660"], "publisher": "Springer Nature", "role": "backfill", "rows": 200},
    {"name": "Nature Nanotechnology", "issns": ["1748-3387", "1748-3395"], "publisher": "Springer Nature", "role": "backfill", "rows": 200},
]

# 期刊 issue 封面/目录页等非论文记录（Crossref 也会返回，type 仍标 journal-article）
ISSUE_NOISE_TITLES = re.compile(
    r"^(issue (publication )?information|issue editorial masthead|issue masthead|"
    r"front cover|back cover|cover image|cover picture|cover story|inside cover|"
    r"inside back cover|table of contents|contents|masthead|editorial board|"
    r"graphical abstract|author index|subject index|keyword index|index)$",
    re.I)

_RATE = None


def _rl():
    global _RATE
    if _RATE is None:
        from la_v2common import RateLimiter
        _RATE = RateLimiter(min_gap=0.7)
    return _RATE


# ---------------- Crossref ----------------

def crossref_journal_info(issn):
    _rl().wait()
    ok, d = http_get_json(f"https://api.crossref.org/journals/{issn}", timeout=30)
    if not ok:
        return {"ok": False, "error": d}
    m = d.get("message", {})
    return {"ok": True, "title": m.get("title"), "publisher": m.get("publisher"),
            "issn": m.get("ISSN", []), "counts": m.get("counts", {})}


def verify_journal_identifiers(force=False):
    """核实每本 blocked 期刊的 ISSN/publisher/Crossref coverage，写 journal_identifiers.json。"""
    if os.path.exists(JOURNAL_IDENTIFIERS_PATH) and not force:
        return read_json(JOURNAL_IDENTIFIERS_PATH)
    out = []
    for j in BLOCKED_JOURNALS + CROSSREF_BACKFILL_JOURNALS:
        info = {"name": j["name"], "expected_issns": j["issns"], "expected_publisher": j["publisher"],
                "role": j.get("role", "blocked_fallback"), "rows": j.get("rows", 200),
                "verified_issns": [], "publisher_confirmed": None, "crossref_works_total": None,
                "latest_work_date": None, "sample_recent": [], "ok": False, "error": None}
        for issn in j["issns"]:
            r = crossref_journal_info(issn)
            if r["ok"]:
                info["ok"] = True
                info["verified_issns"].append(issn)
                info["publisher_confirmed"] = r["publisher"]
                info["crossref_works_total"] = r["counts"].get("total-dois")
                # 最近作品
                _rl().wait()
                ok2, d2 = http_get_json(f"https://api.crossref.org/journals/{issn}/works",
                                        params={"rows": 3, "sort": "published", "order": "desc",
                                                "select": "DOI,title,published-online,issued,publisher,type"})
                if ok2:
                    for it in d2.get("message", {}).get("items", []):
                        dates = it.get("published-online", {}).get("date-parts", [[None]])[0]
                        y = dates[0] if dates else None
                        info["sample_recent"].append({"doi": it.get("DOI"), "title": (it.get("title") or [""])[0],
                                                      "year": y, "type": it.get("type")})
                        if y and (info["latest_work_date"] is None or str(y) > str(info["latest_work_date"])):
                            info["latest_work_date"] = str(y)
            if info["ok"]:
                break
        if not info["ok"]:
            info["error"] = "both ISSNs failed"
        out.append(info)
    write_json(JOURNAL_IDENTIFIERS_PATH, {"verified_at": now_iso(), "journals": out})
    return read_json(JOURNAL_IDENTIFIERS_PATH)


def _cr_date_parts(it):
    for k in ("published-online", "published-print", "issued", "created"):
        v = it.get(k, {}).get("date-parts", [[None]])[0]
        if v and v[0]:
            return "-".join(str(x) for x in v[:3])
    return ""


def collect_crossref_journals(conn, today, log=None, days=7, journal_filter=None):
    """按 ISSN 查询最近 works（blocked 期刊 fallback）。返回 (records, per_journal, signals)。"""
    log = log or (lambda *a: None)
    ids = read_json(JOURNAL_IDENTIFIERS_PATH)
    journals = ids.get("journals", []) if ids else []
    if not journals:
        journals = verify_journal_identifiers()
        journals = journals.get("journals", [])
    from datetime import date, timedelta
    from_date = (date.today() - timedelta(days=int(days))).isoformat()
    records, per = [], {}
    for j in journals:
        if journal_filter and j["name"] not in journal_filter:
            continue
        jname = j["name"]
        if not j.get("ok") or not j.get("verified_issns"):
            per[jname] = {"ok": False, "items": 0, "error": "journal not verified on Crossref"}
            continue
        issn = j["verified_issns"][0]
        got = 0
        per[jname] = {"ok": True, "items": 0, "window": 0}
        rows = int(j.get("rows") or 200)
        for attempt in range(2):
            _rl().wait()
            ok, d = http_get_json(f"https://api.crossref.org/journals/{issn}/works",
                                  params={"rows": rows, "filter": f"from-pub-date:{from_date}",
                                          "sort": "published", "order": "desc",
                                          "select": "DOI,title,author,published-online,published-print,issued,type,URL"})
            if not ok:
                per[jname] = {"ok": False, "items": got, "error": str(d)[:200]}
                log("error", f"crossref {jname} failed: {d}")
                break
            items = d.get("message", {}).get("items", [])
            if not items:
                break
            for it in items:
                title = clean_title((it.get("title") or [""])[0])
                if not title or ISSUE_NOISE_TITLES.match(title.strip()):
                    continue
                doi = norm_doi(it.get("DOI"))
                authors = "; ".join(f"{a.get('given','')} {a.get('family','')}".strip()
                                    for a in it.get("author", []) if a.get("family"))
                date = _cr_date_parts(it)
                rec = {"title": title, "authors": authors, "doi": doi, "arxiv_id": None,
                       "abstract": "", "abstract_truncated": False, "date": date, "url": it.get("URL") or f"https://doi.org/{doi}",
                       "journal": jname, "status": "journal", "version": ""}
                records.append(rec)
                got += 1
            per[jname]["items"] = got
            per[jname]["window"] = got
            if len(items) < 200:
                break
    return records, per


def crossref_by_doi(doi):
    _rl().wait()
    ok, d = http_get_json("https://api.crossref.org/works/" + norm_doi(doi), timeout=30)
    if not ok:
        return None
    return d.get("message")


# ---------------- OpenAlex ----------------

def _oa_authors(w):
    out = []
    for a in w.get("authorships", []):
        au = a.get("author", {})
        name = au.get("display_name", "")
        if name:
            out.append(name)
    return out


def _oa_journal(w):
    src = ((w.get("primary_location") or {}).get("source") or {})
    return src.get("display_name") or ""


def _oa_date(w):
    return (w.get("publication_date") or "")[:10]


def _oa_abstract(w):
    inv = w.get("abstract_inverted_index")
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def oa_work_to_rec(w, journal_hint=None):
    doi = norm_doi((w.get("doi") or "").replace("https://doi.org/", ""))
    arx = ""
    for loc in (w.get("locations") or []):
        la = (loc.get("landing_page_url") or "") or (loc.get("pdf_url") or "")
        m = re.search(r"arxiv\.org/(abs|pdf)/(\d{4}\.\d{4,5})", la)
        if m:
            arx = m.group(2)
            break
    if not arx and doi.startswith("10.48550/arxiv."):
        arx = norm_arxiv(doi)
    return {"title": clean_title(w.get("title") or ""), "authors": "; ".join(_oa_authors(w)),
            "doi": doi or None, "arxiv_id": arx or None, "abstract": _oa_abstract(w),
            "abstract_truncated": False, "date": _oa_date(w),
            "url": (w.get("doi") or "").replace("https://doi.org/", "https://doi.org/") or "",
            "journal": _oa_journal(w) or journal_hint or "", "status": "journal", "version": ""}


def oa_get_work(work_id_or_doi):
    _rl().wait()
    url = f"https://api.openalex.org/works/{work_id_or_doi}"
    ok, d = http_get_json(url, params={"mailto": MAILTO}, timeout=30)
    if not ok:
        return None
    return d


def oa_search_works(query, from_date=None, per_page=25, filter_extra=None):
    params = {"search": query, "per-page": per_page, "mailto": MAILTO}
    if from_date:
        params["filter"] = f"from_publication_date:{from_date}"
        if filter_extra:
            params["filter"] += "," + filter_extra
    _rl().wait()
    ok, d = http_get_json("https://api.openalex.org/works", params=params, timeout=30)
    if not ok:
        return []
    return d.get("results", [])


def oa_author_works(author_id, from_date, per_page=50):
    """tracked author 新工作。author_id 形如 A2208157607。"""
    filt = f"author.id:{author_id}"
    if from_date:
        filt += f",from_publication_date:{from_date}"
    params = {"filter": filt, "per-page": per_page, "sort": "publication_date:desc", "mailto": MAILTO}
    _rl().wait()
    ok, d = http_get_json("https://api.openalex.org/works", params=params, timeout=30)
    if not ok:
        raise RuntimeError("OpenAlex author works failed: " + str(d))
    return d.get("results", [])


def oa_citing_works(seed_id, from_date=None, per_page=50):
    filt = f"cites:{seed_id}"
    if from_date:
        filt += f",from_publication_date:{from_date}"
    params = {"filter": filt, "per-page": per_page, "sort": "publication_date:desc", "mailto": MAILTO}
    _rl().wait()
    ok, d = http_get_json("https://api.openalex.org/works", params=params, timeout=30)
    if not ok:
        raise RuntimeError("OpenAlex citing works failed: " + str(d))
    return d.get("results", [])


def oa_related_works(work_id, cap=15):
    w = oa_get_work(work_id)
    if not w:
        return []
    out = []
    for rw in (w.get("related_works") or [])[:cap]:
        rid = rw.rsplit("/", 1)[-1]
        rw_obj = oa_get_work(rid)
        if rw_obj and rw_obj.get("title"):
            out.append(rw_obj)
    return out


def oa_search_authors(name, affiliation_hint="", coauthors=(), per_page=10):
    """作者消歧候选。返回按分数排序的 [(author_obj, score, reasons)]。"""
    params = {"search": name, "per-page": per_page, "mailto": MAILTO}
    _rl().wait()
    ok, d = http_get_json("https://api.openalex.org/authors", params=params, timeout=30)
    if not ok:
        return []
    results = d.get("results", [])
    scored = []
    name_l = name.lower()
    for a in results:
        disp = (a.get("display_name") or "").lower()
        score = 0
        reasons = []
        if disp == name_l:
            score += 3
        elif name_l.split()[-1] in disp and name_l.split()[0] in disp:
            score += 2
        elif name_l.split()[-1] in disp:
            score += 1
        insts = [i.get("display_name", "") for i in (a.get("last_known_institutions") or [])]
        inst_txt = " ".join(insts).lower()
        if affiliation_hint and affiliation_hint.lower() in inst_txt:
            score += 2
            reasons.append("affiliation_match")
        for kw in ("university", "institute", "academy", "college"):
            if kw in inst_txt:
                reasons.append("has_institution")
        # 合作者验证：取该作者近期 works 的 coauthor 姓名
        if coauthors:
            ws = oa_author_works(a["id"].rsplit("/", 1)[-1], from_date="2020-01-01", per_page=25)
            allnames = " ".join(a.lower() for w in ws for a in _oa_authors(w))
            co_hits = [c.lower() for c in coauthors if c.lower() in allnames]
            if co_hits:
                score += min(3, len(co_hits))
                reasons.append("coauthor:" + ",".join(co_hits[:3]))
            ws_cache[a["id"]] = ws
        scored.append((a, score, reasons))
    scored.sort(key=lambda x: -x[1])
    return scored


ws_cache = {}


def resolve_author(name, aliases=(), affiliation="", coauthors=(), known_openalex_id=None, priority=2, reason=""):
    """返回 {id, canonical_name, openalex_author_id, confidence, evidence}。"""
    if known_openalex_id:
        a = oa_get_work  # noqa
        _rl().wait()
        ok, d = http_get_json("https://api.openalex.org/authors/" + known_openalex_id,
                              params={"mailto": MAILTO}, timeout=30)
        if ok and d.get("display_name"):
            return {"id": re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"),
                    "canonical_name": name, "aliases": list(aliases), "openalex_author_id": known_openalex_id,
                    "affiliation": affiliation or (d.get("last_known_institutions") or [{}])[0].get("display_name", ""),
                    "priority": priority, "topics": [], "reason": reason,
                    "confidence": "high", "resolution_evidence": {"source": "explicit_id", "display_name": d.get("display_name")}}
    cands = oa_search_authors(name, affiliation_hint=affiliation, coauthors=coauthors)
    if not cands:
        return {"id": re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"), "canonical_name": name,
                "aliases": list(aliases), "openalex_author_id": "", "affiliation": affiliation,
                "priority": priority, "topics": [], "reason": reason, "confidence": "low",
                "resolution_evidence": {"source": "no_match"}}
    top, score, reasons = cands[0]
    aid = top["id"].rsplit("/", 1)[-1]
    insts = [i.get("display_name", "") for i in (top.get("last_known_institutions") or [])]
    conf = "high" if score >= 6 else ("medium" if score >= 4 else "low")
    return {"id": re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"), "canonical_name": name,
            "aliases": list(aliases), "openalex_author_id": aid, "affiliation": affiliation or "; ".join(insts),
            "priority": priority, "topics": [], "reason": reason, "confidence": conf,
            "resolution_evidence": {"score": score, "display_name": top.get("display_name"),
                                    "institutions": insts, "reasons": reasons}}
