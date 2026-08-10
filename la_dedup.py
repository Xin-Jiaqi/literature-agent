#!/usr/bin/env python3
"""literature-agent v1 去重强化。

三级匹配：
  exact_match     —— DOI / arXiv ID / 规范化标题精确一致 → 自动合并
  probable_match  —— 模糊（标题相似+作者姓重叠 / 措辞改写 / 摘要重合）→ dedup_review，不静默合并
  manual_review   —— 高风险（同名不同文 / review-comment-erratum / 系列尾缀 / 缩写全称）→ dedup_review

规范化：Unicode NFKC、组合音标、HTML 实体、LaTeX 数学/希腊字母、化学式上下标、破折号族。
"""
import difflib
import re
import unicodedata
from html import unescape as html_unescape

LETTER_RANGE = r"0-9a-zA-Z\u00c0-\u024f\u0370-\u03ff\u0400-\u04ff"

GREEK = {"alpha": "\u03b1", "beta": "\u03b2", "gamma": "\u03b3", "delta": "\u03b4",
         "epsilon": "\u03b5", "zeta": "\u03b6", "eta": "\u03b7", "theta": "\u03b8",
         "iota": "\u03b9", "kappa": "\u03ba", "lambda": "\u03bb", "mu": "\u03bc",
         "nu": "\u03bd", "xi": "\u03be", "omicron": "\u03bf", "pi": "\u03c0",
         "rho": "\u03c1", "sigma": "\u03c3", "tau": "\u03c4", "upsilon": "\u03c5",
         "phi": "\u03c6", "chi": "\u03c7", "psi": "\u03c8", "omega": "\u03c9",
         "Gamma": "\u0393", "Delta": "\u0394", "Theta": "\u0398", "Lambda": "\u039b",
         "Xi": "\u039e", "Pi": "\u03a0", "Sigma": "\u03a3", "Phi": "\u03a6",
         "Psi": "\u03a8", "Omega": "\u03a9"}


def strip_tags_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return html_unescape(s)


def latex_to_plain(s):
    s = s or ""
    for cmd in ("text", "mathrm", "mathbf", "mathit", "ce", "emph", "textit", "bm", "textrm", "mathsf"):
        s = re.sub(r"\\" + cmd + r"\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\^\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"_\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\^([0-9a-zA-Z+\-])", r"\1", s)
    s = re.sub(r"_([0-9a-zA-Z])", r"\1", s)
    for name, sym in GREEK.items():
        s = s.replace("\\" + name + "{", sym + "{").replace("\\" + name, sym)
    s = re.sub(r"\$([^$]*)\$", lambda m: m.group(1).replace("{", "").replace("}", ""), s)
    s = re.sub(r"\\[a-zA-Z]+", " ", s)
    return s.replace("{", "").replace("}", "")


def _base_normalized(s):
    s = unicodedata.normalize("NFKC", strip_tags_html(s))
    s = latex_to_plain(s)
    return s


def normalize_title_key(s):
    s = _base_normalized(s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"\s*\(v\d+\)\s*$", "", s)
    return re.sub(r"[^" + LETTER_RANGE + r"]+", "", s.lower())


def normalize_title_sim(s):
    s = _base_normalized(s)
    s = s.replace("\u2013", " ").replace("\u2014", " ").replace("\u2010", " ").replace("\u2011", " ").replace("\u2212", " ").replace("-", " ")
    s = re.sub(r"[^" + LETTER_RANGE + r"\s]+", " ", s.lower())
    s = re.sub(r"\s*\(v\d+\)\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def first_author_surname(authors):
    if not authors:
        return ""
    first = (authors or "").split(";")[0].split(",")[0].strip()
    first = re.split(r"\s+and\s+", first, flags=re.I)[0].strip()
    parts = re.split(r"\s+", first)
    if not parts:
        return ""
    surname = parts[-1].lower()
    surname = unicodedata.normalize("NFD", surname)
    surname = "".join(c for c in surname if not unicodedata.combining(c))
    return re.sub(r"[^" + LETTER_RANGE + r"]", "", surname)


def title_similarity(a, b):
    a, b = normalize_title_sim(a), normalize_title_sim(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def abstract_similarity(a, b):
    a, b = (a or "").lower(), (b or "").lower()
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def token_overlap(a, b):
    ta, tb = set(normalize_title_sim(a).split()), set(normalize_title_sim(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def initials_style(name):
    toks = [t for t in re.split(r"[^a-z]+", (name or "").lower()) if t]
    if len(toks) < 2:
        return False
    return all(len(t) <= 2 for t in toks[:-1])


SERIES_MARKER = re.compile(r"^(i{1,3}|iv|v|vi{0,3}|part [ivx1-9]|[1-9])$")


def series_suffix_diff(a, b):
    ta, tb = normalize_title_sim(a).split(), normalize_title_sim(b).split()
    if not ta or not tb:
        return False
    if len(ta) == len(tb):
        return (ta[:-1] == tb[:-1] and ta[-1] != tb[-1]
                and (SERIES_MARKER.match(ta[-1]) or SERIES_MARKER.match(tb[-1])))
    longer, shorter = (ta, tb) if len(ta) > len(tb) else (tb, ta)
    if len(longer) != len(shorter) + 1 or longer[:-1] != shorter:
        return False
    return SERIES_MARKER.match(longer[-1]) is not None


def norm_doi(s):
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    m = re.search(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", s, re.I)
    return m.group(0).rstrip(".,;") if m else None


def norm_arxiv_id(s):
    s = (s or "").strip().lower()
    m = re.match(r"((?:abs/)?(?:arxiv:)?\d{4}\.\d{4,5})(?:v\d+)?$", s)
    if m:
        return m.group(1)
    m = re.match(r"(\d{4}\.\d{4,5})(?:v\d+)?", s)
    return m.group(1) if m else None


PROBABLE_SIM = 0.92
ABS_SIM_PROBABLE = 0.80


def _review_stripped(t):
    s = re.sub(r"^(comment on|erratum|addendum|correction|reply to)[:\s'\"]+", "", (t or "").lower().strip("'\""))
    s = re.sub(r"[:'\s]*(a |the )?review$", "", s)
    return s.strip()


def classify_pair(rec_a, rec_b):
    """对两条来源记录判断匹配类型。返回 (kind|None, score, evidence)。"""
    return classify_entries(WorkEntry.from_rec(rec_a), WorkEntry.from_rec(rec_b))


class WorkEntry:
    """一条 work（DB 侧或来源记录侧）的预计算规范化快照。"""

    def __init__(self, work_id, title, authors, doi, arxiv_id, abstract=""):
        self.work_id = work_id
        self.raw_title = title or ""
        self.raw_authors = authors or ""
        self.key = normalize_title_key(title) or None
        self.sim = normalize_title_sim(title)
        self.tokens = frozenset(self.sim.split())
        self.surname = first_author_surname(authors) or None
        self.doi = norm_doi(doi)
        self.arxiv = norm_arxiv_id(arxiv_id)
        self.abstract = abstract or ""

    @classmethod
    def from_rec(cls, rec, work_id=None):
        return cls(work_id or rec.get("work_id") or "",
                   rec.get("title", ""), rec.get("authors") or "", rec.get("doi"),
                   rec.get("arxiv_id"), rec.get("abstract") or "")


def classify_entries(ea, eb):
    """对两个 WorkEntry 判断匹配类型。返回 (kind|None, score, evidence)。"""
    if ea.doi and eb.doi and ea.doi == eb.doi:
        return "exact_match", 1.0, "DOI 一致: " + ea.doi
    if ea.arxiv and eb.arxiv and ea.arxiv == eb.arxiv:
        return "exact_match", 1.0, "arXiv ID 一致: " + ea.arxiv

    surname_ok = bool(ea.surname and eb.surname and ea.surname == eb.surname)
    if ea.key and eb.key and ea.key == eb.key:
        if ea.raw_authors and eb.raw_authors:
            if not surname_ok:
                return "manual_review", 1.0, "标题精确一致但第一作者姓不同(%s vs %s)" % (ea.surname or "?", eb.surname or "?")
            if initials_style(ea.raw_authors) and initials_style(eb.raw_authors) and normalize_title_key(ea.raw_authors) != normalize_title_key(eb.raw_authors):
                return "manual_review", 1.0, "标题精确一致但作者缩写形式不同(%s vs %s)" % (ea.raw_authors, eb.raw_authors)
        return "exact_match", 1.0, "规范化标题精确一致"

    if not ea.sim or not eb.sim:
        return None, 0.0, ""
    sim = difflib.SequenceMatcher(None, ea.sim, eb.sim).ratio()
    if sim <= 0.3:
        return None, sim, ""
    inter = len(ea.tokens & eb.tokens)
    toks = inter / len(ea.tokens | eb.tokens) if (ea.tokens | eb.tokens) else 0.0
    abs_sim = abstract_similarity(ea.abstract, eb.abstract) if (ea.abstract and eb.abstract) else 0.0

    norm_a, norm_b = ea.sim, eb.sim
    rv_a = normalize_title_sim(_review_stripped(ea.raw_title))
    rv_b = normalize_title_sim(_review_stripped(eb.raw_title))
    is_rev_a, is_rev_b = rv_a != norm_a, rv_b != norm_b
    if is_rev_a != is_rev_b:
        s2 = title_similarity(rv_a, rv_b)
        if s2 >= 0.6 or toks >= 0.4:
            return "manual_review", max(sim, s2), "review/comment/erratum 与正文标题相近(sim=%.2f)" % max(sim, s2)

    if series_suffix_diff(ea.raw_title, eb.raw_title):
        return "manual_review", sim, "标题仅差系列尾缀(sim=%.2f)，疑似系列论文" % sim

    if surname_ok:
        if sim >= PROBABLE_SIM:
            return "probable_match", sim, "标题相似 sim=%.2f + 第一作者姓一致(%s)" % (sim, ea.surname)
        if toks >= 0.65 and sim >= 0.55:
            return "probable_match", sim, "措辞/语序改写但主题词重合(sim=%.2f, tokens=%.2f)" % (sim, toks)
        if abs_sim >= ABS_SIM_PROBABLE and sim >= 0.5:
            return "probable_match", max(sim, abs_sim), "摘要高度重合 abs_sim=%.2f + 作者姓一致" % abs_sim
        if sim >= 0.45 and toks >= 0.4:
            return "manual_review", sim, "标题部分重合需人工确认(sim=%.2f, tokens=%.2f)" % (sim, toks)
        return None, sim, ""
    else:
        if sim >= PROBABLE_SIM:
            return "manual_review", sim, "标题高度相似 sim=%.2f 但第一作者姓不同(%s vs %s)" % (sim, ea.surname or "?", eb.surname or "?")
        if toks >= 0.7:
            return "manual_review", sim, "主题词高度重合但无作者佐证(tokens=%.2f)" % toks
        return None, sim, ""


class DedupIndex:
    """全库 work 索引：DOI/arXiv/标题键 O(1) 精确查找 + token 倒排候选过滤。"""

    def __init__(self, conn):
        self.entries = []
        self.by_work_id = {}
        self.by_doi = {}
        self.by_arxiv = {}
        self.by_key = {}
        self.token_index = {}
        for row in conn.execute("SELECT work_id, title, authors, doi, arxiv_id FROM works").fetchall():
            self._add(WorkEntry(row["work_id"], row["title"], row["authors"], row["doi"], row["arxiv_id"]))

    @staticmethod
    def _append(mapping, key, entry):
        if key:
            mapping.setdefault(key, []).append(entry)

    @staticmethod
    def _discard(mapping, key, entry):
        if not key or key not in mapping:
            return
        mapping[key] = [e for e in mapping[key] if e is not entry]
        if not mapping[key]:
            del mapping[key]

    def _add(self, entry):
        self.entries.append(entry)
        self.by_work_id[entry.work_id] = entry
        self._append(self.by_doi, entry.doi, entry)
        self._append(self.by_arxiv, entry.arxiv, entry)
        self._append(self.by_key, entry.key, entry)
        for token in entry.tokens:
            self._append(self.token_index, token, entry)

    def _remove(self, entry):
        self.entries = [e for e in self.entries if e is not entry]
        self.by_work_id.pop(entry.work_id, None)
        self._discard(self.by_doi, entry.doi, entry)
        self._discard(self.by_arxiv, entry.arxiv, entry)
        self._discard(self.by_key, entry.key, entry)
        for token in entry.tokens:
            self._discard(self.token_index, token, entry)

    def refresh(self, conn, work_id):
        """把本次 collect 刚插入/更新的 work 立即加入索引，避免同批重复建 work。"""
        old = self.by_work_id.get(work_id)
        if old is not None:
            self._remove(old)
        row = conn.execute(
            "SELECT work_id, title, authors, doi, arxiv_id FROM works WHERE work_id=?",
            (work_id,),
        ).fetchone()
        if row is not None:
            self._add(WorkEntry(row["work_id"], row["title"], row["authors"], row["doi"], row["arxiv_id"]))

    def find(self, rec):
        """返回 (exact_wid, [(work_id, kind, score, evidence), ...])。"""
        e = WorkEntry.from_rec(rec)
        exact_wid, exact_entries, review = None, [], []

        def consider(entry):
            kind, score, ev = classify_entries(e, entry)
            if kind == "exact_match":
                exact_entries.append((entry, score, ev))
            elif kind:
                review.append((entry.work_id, kind, score, ev))

        for group in (self.by_doi.get(e.doi, []) if e.doi else [],
                      self.by_arxiv.get(e.arxiv, []) if e.arxiv else [],
                      self.by_key.get(e.key, []) if e.key else []):
            for entry in group:
                consider(entry)

        if not exact_entries and e.tokens:
            scored = []
            for t in e.tokens:
                for entry in self.token_index.get(t, []):
                    scored.append(entry)
            seen = set()
            candidates = []
            for entry in scored:
                if entry.work_id in seen:
                    continue
                seen.add(entry.work_id)
                inter = len(e.tokens & entry.tokens)
                if inter >= 3:
                    candidates.append(entry)
            if not candidates:
                cand2 = []
                seen2 = set()
                for entry in scored:
                    if entry.work_id in seen2:
                        continue
                    seen2.add(entry.work_id)
                    inter = len(e.tokens & entry.tokens)
                    if inter >= 2:
                        cand2.append(entry)
                candidates = cand2[:30]
            for entry in candidates:
                consider(entry)

        if exact_entries:
            exact_wid = exact_entries[0][0].work_id
            for entry, score, ev in exact_entries[1:]:
                review.append((entry.work_id, "manual_review", score, "多个精确候选冲突 " + ev))
        return exact_wid, review


def find_matches(conn, rec, index=None):
    """便捷入口：精确匹配返回 work_id；probable/manual 写入候选列表。"""
    index = index or DedupIndex(conn)
    return index.find(rec)


def _case(name, a, b, expected, note=""):
    return {"name": name, "a": a, "b": b, "expected": expected, "note": note}


SYNTHETIC_CASES = [
    _case("arxiv_journal_exact_doi",
          {"title": "Shift Current in Sliding Bilayers", "authors": "Alice Zhang; Bob Li", "doi": "10.1103/prb.1234"},
          {"title": "Shift Current in Sliding Bilayers", "authors": "Alice Zhang; Bob Li", "doi": "10.1103/prb.1234"},
          "exact_match", "arXiv 与期刊标题完全相同，DOI 一致"),
    _case("arxiv_journal_exact_title_only",
          {"title": "Shift Current in Sliding Bilayers", "authors": "Alice Zhang", "doi": None},
          {"title": "Shift Current in Sliding Bilayers", "authors": "Alice Zhang", "doi": "10.1103/prb.1234"},
          "exact_match", "arXiv 无 DOI，靠规范化标题精确合并"),
    _case("doi_url_variants",
          {"title": "X", "authors": "A. Qiu", "doi": "https://doi.org/10.1103/PRB.9999."},
          {"title": "X", "authors": "A. Qiu", "doi": "10.1103/prb.9999"},
          "exact_match", "DOI URL/大小写/尾点差异"),
    _case("minor_edit_probable",
          {"title": "A Novel Framework for Shift Currents", "authors": "A. Zhang"},
          {"title": "Novel Framework for Shift Currents", "authors": "A. Zhang"},
          "probable_match", "小标题修改 + 作者姓一致"),
    _case("colon_rewrite_probable",
          {"title": "Shift currents in bilayer semiconductors: a first-principles study", "authors": "A. Zhang"},
          {"title": "First-principles study of shift currents in bilayer semiconductors", "authors": "A. Zhang"},
          "probable_match", "colon 改写"),
    _case("article_drop_probable",
          {"title": "A unified theory of sliding ferroelectricity", "authors": "W. Li"},
          {"title": "Unified theory of sliding ferroelectricity", "authors": "W. Li"},
          "probable_match", "冠词 A 差异"),
    _case("chem_latex_unicode_exact",
          {"title": "Shift current in monolayer MoS$_2$", "authors": "A. Zhang"},
          {"title": "Shift current in monolayer MoS\u2082", "authors": "A. Zhang"},
          "exact_match", "化学式 LaTeX 下标 vs Unicode 下标"),
    _case("chem_superscript_exact",
          {"title": "Fe$^{2+}$ doped sliding ferroelectrics", "authors": "B. Li"},
          {"title": "Fe\u00b2\u207a doped sliding ferroelectrics", "authors": "B. Li"},
          "exact_match", "上标 ^ vs Unicode ²⁺"),
    _case("chem_nfkc_exact",
          {"title": "MoS\u2082 bilayers", "authors": "B. Li"},
          {"title": "MoS2 bilayers", "authors": "B. Li"},
          "exact_match", "NFKC 统一数字下标"),
    _case("unicode_composed_exact",
          {"title": "R\u00e9sum\u00e9 of ferroelectric switching", "authors": "C. Ren\u00e9"},
          {"title": "Re\u0301sume\u0301 of ferroelectric switching", "authors": "C. Rene\u0301"},
          "exact_match", "组合音标 vs 预组合"),
    _case("html_entity_exact",
          {"title": "Materials &amp; Design for BPVE", "authors": "D. Wu"},
          {"title": "Materials & Design for BPVE", "authors": "D. Wu"},
          "exact_match", "HTML 实体"),
    _case("dash_variants_exact",
          {"title": "Non-linear optical response of altermagnets", "authors": "E. Sun"},
          {"title": "Nonlinear optical response of altermagnets", "authors": "E. Sun"},
          "exact_match", "连字符差异（键规范化后一致）"),
    _case("case_insensitive_exact",
          {"title": "SHIFT CURRENT IN BILAYERS", "authors": "F. Ma"},
          {"title": "Shift Current in Bilayers", "authors": "F. Ma"},
          "exact_match", "大小写差异"),
    _case("arxiv_v1_v2_exact",
          {"title": "Quantum Geometry of Sliding Bilayers", "authors": "G. Hu", "arxiv_id": "2608.12345"},
          {"title": "Quantum Geometry of Sliding Bilayers", "authors": "G. Hu", "arxiv_id": "2608.12345v2"},
          "exact_match", "arXiv v1/v2 同 work"),
    _case("accepted_published_exact",
          {"title": "Injection current in Hg3O3", "authors": "H. Chen", "doi": "10.1103/prl.7777"},
          {"title": "Injection current in Hg3O3", "authors": "H. Chen", "doi": "10.1103/prl.7777"},
          "exact_match", "accepted→published 同 DOI"),
    _case("same_title_diff_paper",
          {"title": "Machine learning for materials discovery", "authors": "I. Johnson"},
          {"title": "Machine learning for materials discovery", "authors": "J. Smith"},
          "manual_review", "同名不同论文（作者姓不同）"),
    _case("review_vs_article",
          {"title": "Sliding ferroelectricity: a review", "authors": "K. Wang"},
          {"title": "Sliding ferroelectricity in van der Waals bilayers", "authors": "K. Wang"},
          "manual_review", "review 与 article 标题相似"),
    _case("erratum_vs_original",
          {"title": "Erratum: Giant shift current in SnTe", "authors": "L. Jin"},
          {"title": "Giant shift current in SnTe", "authors": "L. Jin"},
          "manual_review", "erratum 与原文"),
    _case("diff_title_same_authors",
          {"title": "Nonlinear Hall effect in layered materials II", "authors": "M. Zhao"},
          {"title": "Nonlinear Hall effect in layered materials I", "authors": "M. Zhao"},
          "manual_review", "系列论文 II vs I（标题仅差尾缀，不可自动合并）"),
    _case("completely_different",
          {"title": "Topological phonon modes in monolayer boron", "authors": "N. Liu"},
          {"title": "Exciton binding energies in MoSe2 heterobilayers", "authors": "O. Bai"},
          "no_match", "完全不相关"),
    _case("whitespace_punct_exact",
          {"title": "Shift  current ,  in bilayers", "authors": "P. Qiu"},
          {"title": "Shift current in bilayers", "authors": "P. Qiu"},
          "exact_match", "多余空白与标点"),
    _case("latex_mathrm_exact",
          {"title": r"$\mathrm{BPVE}$ in two-dimensional materials", "authors": "R. Tang"},
          {"title": "BPVE in two-dimensional materials", "authors": "R. Tang"},
          "exact_match", "\\mathrm 去壳"),
    _case("latex_greek_exact",
          {"title": r"$\alpha$-phase antiferromagnets", "authors": "S. Xu"},
          {"title": "\u03b1-phase antiferromagnets", "authors": "S. Xu"},
          "exact_match", "LaTeX 希腊字母 vs Unicode"),
    _case("shg_abbrev_manual",
          {"title": "SHG in sliding ferroelectrics", "authors": "T. Gao"},
          {"title": "Second harmonic generation in sliding ferroelectrics", "authors": "T. Gao"},
          "manual_review", "缩写与全称（标题不精确）"),
    _case("version_suffix_exact",
          {"title": "Skyrmion dynamics (v2)", "authors": "U. He"},
          {"title": "Skyrmion dynamics", "authors": "U. He"},
          "exact_match", "标题带版本后缀"),
    _case("author_order_doi_exact",
          {"title": "Photocurrent switching in InSe", "authors": "V. Luo; W. Yang", "doi": "10.1021/acs.nanolett.5b01234"},
          {"title": "Photocurrent switching in InSe", "authors": "W. Yang; V. Luo", "doi": "10.1021/acs.nanolett.5b01234"},
          "exact_match", "作者顺序变化（DOI 一致）"),
    _case("surname_initial_diff_probable",
          {"title": "Generalized Neumann principle for shift currents", "authors": "X. Wang"},
          {"title": "Generalized Neumann principle for shift currents", "authors": "Y. Wang"},
          "manual_review", "同名不同人（X. Wang vs Y. Wang）"),
    _case("abstract_overlap_probable",
          {"title": "Optical rectification in twisted bilayers", "authors": "Z. Qian",
           "abstract": "We study optical rectification and injection currents in twisted bilayer systems using Wannier interpolation."},
          {"title": "Rectification photocurrents of twisted bilayer stacks", "authors": "Z. Qian",
           "abstract": "We study optical rectification and injection currents in twisted bilayer systems using Wannier interpolation."},
          "probable_match", "标题不同但摘要高度重合 + 作者姓一致"),
    _case("no_abstract_no_surname",
          {"title": "Multiferroic topological materials", "authors": ""},
          {"title": "Multiferroic topological materials", "authors": ""},
          "exact_match", "缺作者仍按标题精确合并"),
    _case("journal_rename_title_probable",
          {"title": "Unified framework for fractional ferroelectricity", "authors": "AA. Xiang"},
          {"title": "Fractional ferroelectricity: a unified framework", "authors": "AA. Xiang"},
          "probable_match", "期刊改标题（同作者）"),
    _case("en_dash_vs_hyphen_sim",
          {"title": "Sliding ferroelectricity \u2013 a new twist", "authors": "AB. Chen"},
          {"title": "Sliding ferroelectricity - a new twist", "authors": "AB. Chen"},
          "exact_match", "en-dash vs 连字符"),
    _case("doi_missing_both_title_exact",
          {"title": "Berry phase theory of polarization", "authors": "AC. Sun"},
          {"title": "Berry phase theory of polarization", "authors": "AC. Sun"},
          "exact_match", "双方均无 DOI/arXiv，标题精确"),
    _case("arxiv_abs_url_variant",
          {"title": "Wannier interpolation of shift currents", "authors": "AD. Lin", "arxiv_id": "2608.77777"},
          {"title": "Wannier interpolation of shift currents", "authors": "AD. Lin", "arxiv_id": "https://arxiv.org/abs/2608.77777v1"},
          "exact_match", "arXiv URL 变体"),
    _case("comment_similar_title",
          {"title": "Comment on 'Nonlinear Hall effect in twisted bilayer graphene'", "authors": "AE. Zhou"},
          {"title": "Nonlinear Hall effect in twisted bilayer graphene", "authors": "AF. Zhou"},
          "manual_review", "comment 与原文"),
    _case("two_d_vs_words",
          {"title": "2D materials for bulk photovoltaic effect", "authors": "AG. Feng"},
          {"title": "Two-dimensional materials for bulk photovoltaic effect", "authors": "AG. Feng"},
          "manual_review", "2D vs two-dimensional（需人工确认）"),
    _case("title_misspelling_probable",
          {"title": "Slidding ferroelectricity in hBN bilayers", "authors": "AH. Kong"},
          {"title": "Sliding ferroelectricity in hBN bilayers", "authors": "AH. Kong"},
          "probable_match", "拼写错误"),
    _case("totally_different_same_first_author",
          {"title": "Giant shift current in GeS", "authors": "AI. Xu"},
          {"title": "Thermal conductivity of high-entropy alloys", "authors": "AI. Xu"},
          "no_match", "同作者不同论文（sim 低）"),
]


def run_synthetic_tests(conn=None, log=None):
    passed, failed = 0, []
    for case in SYNTHETIC_CASES:
        kind, score, ev = classify_pair(case["a"], case["b"])
        expected = case["expected"]
        ok = (kind == expected) or (expected == "no_match" and kind is None)
        if ok:
            passed += 1
        else:
            failed.append({"name": case["name"], "expected": expected, "got": kind,
                           "score": round(score, 3), "evidence": ev, "note": case["note"]})
    return passed, failed


def run_dedup_audit(conn):
    return conn.execute("SELECT * FROM dedup_review WHERE status='pending' ORDER BY score DESC").fetchall()


def dedup_report(conn):
    from datetime import datetime
    from la_db import db_count
    passed, failed = run_synthetic_tests(conn)
    lines = ["# Dedup 评估（v1）\n",
             "- 日期：" + datetime.now().strftime("%Y-%m-%d %H:%M"),
             "- synthetic 用例：%d 条，通过 %d / 失败 %d\n" % (len(SYNTHETIC_CASES), passed, len(failed))]
    if failed:
        lines.append("## 失败用例\n")
        for f in failed:
            lines.append("- **%s**：期望 %s，得到 %s（score=%s）%s" % (f["name"], f["expected"], f["got"], f["score"], f["evidence"]))
            lines.append("  - " + f["note"])
        lines.append("")
    lines.append("## 匹配规则")
    lines.append("- exact_match：DOI / arXiv ID / 规范化标题精确一致 → 自动合并")
    lines.append("- probable_match：标题相似+作者姓一致 / 措辞改写 / 摘要高度重合 → dedup_review")
    lines.append("- manual_review：同名不同作者 / review-comment-erratum / 系列尾缀 / 缩写全称 → dedup_review")
    lines.append("")
    lines.append("## 规范化")
    lines.append("- Unicode NFKC + 组合音标消除；HTML 实体；LaTeX 命令/希腊字母/上下标；化学式数字下标；破折号族")
    lines.append("")
    pend = db_count(conn, "dedup_review", "status='pending'")
    lines.append("## 当前 pending 对")
    lines.append("- %d 条等待人工裁决（`literature_agent.py dedup-review` 查看）" % pend)
    return "\n".join(lines)
