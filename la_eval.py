#!/usr/bin/env python3
"""literature-agent 筛选优先级回归：gold 集构建 + evaluate-screening。

gold 来源（历史人工认可）：
  arxiv_screening_test_v2.md（S2/A8/B83/C202）
  rss_test（rss_new_sorted.json + rss_labels.json，A4/B60/C103）
  aps_accepted_screening_test（acc_queue.json + acc_labels.json，A7/...）
历史人工样本使用 S/A/B/C；加载后统一映射为 MUST/WEEKLY/RADAR/DROP。
"""
import json
import os
import random
import re
import sys

from la_priority import DROP, MUST, RADAR, WEEKLY, normalize_priority

BASE = os.path.dirname(os.path.abspath(__file__))
from literature_config import GOLD_ROOT, REPORT_AUDIT_ROOT
GOLD_PATH = os.path.join(GOLD_ROOT, "screening_gold.jsonl")
EVAL_PATH = os.path.join(REPORT_AUDIT_ROOT, "screening_eval.md")
ROUNDS_DIR = os.path.join(REPORT_AUDIT_ROOT, "eval_rounds")

KEYWORD_POOL = {
    "altermagnet": r"altermagnet",
    "skyrmion": r"skyrmion",
    "exciton": r"exciton",
    "dft": r"density functional|first-principles|ab initio",
    "ml": r"machine learning|neural network|deep learning",
    "optical": r"second harmonic|shift current|photogalvanic|photocurrent|nonlinear optical|optical response",
    "sliding": r"sliding ferroelectric|sliding|interlayer",
    "bpve": r"bulk photovoltaic|shift current|photogalvanic",
    "spin": r"spin|magnetic",
}
UNRELATED_POOL = r"quantum error|qubit|quantum computation|quark|gluon|neutrino|hadron|dark matter|battery|lithium|"
UNRELATED_POOL += r"electrolyte|polymer|colloid|active matter|superconduct|kondo|catalysis|protein|dna|sensor|transistor"


def _md_sections(path):
    """解析测试 md：返回 {'S': [(title, reason)], 'A': [...], 'B': [...], 'C': [...]}
    支持两种格式：`### title`（S/A）与 `- <arxiv_id> title — reason`（B/C）。"""
    out = {"S": [], "A": [], "B": [], "C": []}
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except Exception:
        return out
    cur = None
    for ln in lines:
        m = re.match(r"^## (S|A|B|C)", ln)
        if m:
            cur = m.group(1)
            continue
        if cur is None:
            continue
        if ln.startswith("### "):
            out[cur].append([ln[4:].strip(), ""])
        elif ln.startswith("- ") and cur in ("B", "C"):
            body = ln[2:].strip()
            mm = re.match(r"^(26\d{2}\.\d{4,5}v?\d*|\d{4}\.\d{4,5}v?\d*)\s+(.*?)\s+[—-]\s+(.*)$", body)
            if mm:
                out[cur].append([mm.group(2).strip(), f"{mm.group(1)} — {mm.group(3).strip()}"])
    return out


def _pick(items, n, rng, prefer=None, avoid=None):
    """代表性子采样：优先 prefer 关键词命中的，再随机补足。avoid 命中者最后选。"""
    if n >= len(items):
        return list(items)
    prefer = prefer or []
    avoid = avoid or []
    scored = []
    for it in items:
        text = (it[0] + " " + it[1]).lower()
        p = sum(1 for kw in prefer if re.search(kw, text, re.I))
        a = sum(1 for kw in avoid if re.search(kw, text, re.I))
        scored.append((p - a * 5, it))
    scored.sort(key=lambda x: -x[0])
    head = [x[1] for x in scored if x[0] > 0][:n]
    rest = [x[1] for x in scored if x[0] <= 0]
    rng.shuffle(rest)
    return (head + rest)[:n]


def _arxiv_lookup(aid):
    try:
        entries = json.load(open("/tmp/arxiv_entries.json"))
    except Exception:
        return None, ""
    for e in entries:
        if re.match(re.escape(aid) + r"v?\d*$", e.get("id", "")):
            return e.get("title", ""), e.get("abs", "")
    return None, ""


def build_gold(force=False):
    """构建 screening_gold.jsonl。返回条目列表。"""
    if os.path.exists(GOLD_PATH) and not force:
        return load_gold()
    rng = random.Random(20260808)
    gold = []

    # ---- arXiv v2 ----
    secs = _md_sections(os.path.join(GOLD_ROOT, "arxiv_screening_test_v2.md"))
    for grade in ("S", "A"):
        for title, reason in secs.get(grade, []):
            aid = ""
            m = re.search(r"arXiv[:：]?\s*(26\d{2}\.\d{4,5})", reason + " " + title)
            if not m:
                t2, _ = _arxiv_lookup("")  # noop
                # try find by title
                try:
                    entries = json.load(open("/tmp/arxiv_entries.json"))
                    for e in entries:
                        if re.sub(r"[^a-z0-9]+", "", e.get("title", "").lower()) == re.sub(r"[^a-z0-9]+", "", title.lower()):
                            aid = re.match(r"(\d{4}\.\d{4,5})", e.get("id", "")).group(1) if re.match(r"(\d{4}\.\d{4,5})", e.get("id", "")) else ""
                            break
                except Exception:
                    pass
            else:
                aid = m.group(1)
            title2, abstract = _arxiv_lookup(aid) if aid else (None, "")
            gold.append({"title": title2 or title, "abstract": abstract, "expected_grade": normalize_priority(grade),
                         "source": "arxiv_test_v2", "reason": reason or f"arXiv {aid}"})
    b_pick = _pick(secs.get("B", []), 40, rng,
                   prefer=[KEYWORD_POOL["altermagnet"], KEYWORD_POOL["skyrmion"], KEYWORD_POOL["exciton"],
                           KEYWORD_POOL["dft"], KEYWORD_POOL["ml"], KEYWORD_POOL["optical"], KEYWORD_POOL["sliding"]],
                   avoid=[UNRELATED_POOL])
    for title, reason in b_pick:
        t2, abstract = _arxiv_lookup("")
        # locate by title
        try:
            entries = json.load(open("/tmp/arxiv_entries.json"))
            for e in entries:
                if re.sub(r"[^a-z0-9]+", "", e.get("title", "").lower()) == re.sub(r"[^a-z0-9]+", "", title.lower()):
                    abstract = e.get("abs", "")
                    break
        except Exception:
            pass
        gold.append({"title": title, "abstract": abstract, "expected_grade": RADAR,
                     "source": "arxiv_test_v2", "reason": reason})
    c_pick = _pick(secs.get("C", []), 25, rng, prefer=[UNRELATED_POOL])
    for title, reason in c_pick:
        abstract = ""
        try:
            entries = json.load(open("/tmp/arxiv_entries.json"))
            for e in entries:
                if re.sub(r"[^a-z0-9]+", "", e.get("title", "").lower()) == re.sub(r"[^a-z0-9]+", "", title.lower()):
                    abstract = e.get("abs", "")
                    break
        except Exception:
            pass
        gold.append({"title": title, "abstract": abstract, "expected_grade": DROP,
                     "source": "arxiv_test_v2", "reason": reason})

    # ---- RSS（期刊） ----
    try:
        rss = json.load(open("/tmp/rss_new_sorted.json"))
        rss_lab = json.load(open("/tmp/rss_labels.json"))
    except Exception:
        rss, rss_lab = [], {}
    by_grade = {"S": [], "A": [], "B": [], "C": []}
    for i, item in enumerate(rss):
        lab = rss_lab.get(str(i))
        if lab:
            by_grade[lab].append(item)
    for grade, n in (("A", 4), ("B", 30), ("C", 35)):
        items = by_grade.get(grade, [])
        rng.shuffle(items)
        for item in items[:n]:
            gold.append({"title": item.get("title", ""), "abstract": item.get("abstract", ""),
                         "expected_grade": normalize_priority(grade), "source": "rss_test",
                         "reason": f"期刊 RSS 历史人工分级（{item.get('source')}）"})

    # ---- APS accepted ----
    try:
        acc = json.load(open("/tmp/acc_queue.json"))
        acc_lab = json.load(open("/tmp/acc_labels.json"))
    except Exception:
        acc, acc_lab = [], {}
    by_grade = {"S": [], "A": [], "B": [], "C": []}
    for i, item in enumerate(acc):
        lab = acc_lab.get(str(i))
        if lab:
            by_grade[lab].append(item)
    for grade, n in (("A", 7), ("B", 25), ("C", 35)):
        items = by_grade.get(grade, [])
        rng.shuffle(items)
        for item in items[:n]:
            meta = item.get("accepted") or item.get("published") or {}
            gold.append({"title": item.get("title", ""), "abstract": meta.get("abstract", ""),
                         "expected_grade": normalize_priority(grade), "source": "aps_accepted_test",
                         "reason": f"APS accepted 历史人工分级（{item.get('journal')}）"})

    os.makedirs(os.path.dirname(GOLD_PATH), exist_ok=True)
    with open(GOLD_PATH, "w", encoding="utf-8") as f:
        for g in gold:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    return gold


def load_gold():
    out = []
    if os.path.exists(GOLD_PATH):
        with open(GOLD_PATH, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    item = json.loads(ln)
                    item["expected_grade"] = normalize_priority(item.get("expected_grade"))
                    out.append(item)
    return out


def run_evaluate(classifier=None, round_label=None):
    """对 gold 集执行当前筛选逻辑，输出指标。返回指标 dict。"""
    import literature_agent as la
    from collections import Counter
    classifier = classifier or la.classify
    gold = build_gold(force=False)
    if not gold:
        print("gold set 为空，先运行 build-gold")
        return None
    rows = []
    for g in gold:
        grade, reason, matched, conf = classifier(g["title"], g["abstract"], "", truncated=False)
        rows.append({**g, "expected_grade": normalize_priority(g.get("expected_grade")),
                     "pred": normalize_priority(grade)})
    conf_m = Counter(r["pred"] for r in rows)
    gold_m = Counter(r["expected_grade"] for r in rows)

    exact = sum(1 for r in rows if r["pred"] == r["expected_grade"])
    acc = exact / len(rows)
    def recall_prec(expected, pred, label):
        tp = sum(1 for r in rows if r["expected_grade"] == expected and r["pred"] == pred)
        n_gold = sum(1 for r in rows if r["expected_grade"] == expected)
        n_pred = sum(1 for r in rows if r["pred"] == pred)
        return (tp / n_gold if n_gold else 1.0, tp / n_pred if n_pred else 1.0)

    must_rec, must_prec = recall_prec(MUST, MUST, MUST)
    actionable_recall = sum(
        1 for r in rows if r["expected_grade"] in (MUST, WEEKLY) and r["pred"] in (MUST, WEEKLY)
    ) / max(1, sum(1 for r in rows if r["expected_grade"] in (MUST, WEEKLY)))
    fp_actionable = [
        r for r in rows if r["expected_grade"] in (RADAR, DROP) and r["pred"] in (MUST, WEEKLY)
    ]
    fn_actionable = [
        r for r in rows if r["expected_grade"] in (MUST, WEEKLY) and r["pred"] in (RADAR, DROP)
    ]

    metrics = {"round": round_label or ("round-%d" % (len(_read_rounds()) + 1)),
               "n": len(rows), "exact_accuracy": round(acc, 4),
               "MUST_recall": round(must_rec, 4), "MUST_precision": round(must_prec, 4),
               "actionable_recall": round(actionable_recall, 4),
               "fp_actionable_count": len(fp_actionable), "fn_actionable_count": len(fn_actionable),
               "pred_dist": dict(conf_m), "gold_dist": dict(gold_m),
               "fp_list": [{"title": r["title"], "expected": r["expected_grade"], "pred": r["pred"]} for r in fp_actionable],
               "fn_list": [{"title": r["title"], "expected": r["expected_grade"], "pred": r["pred"]} for r in fn_actionable]}
    _write_eval(metrics, rows)
    return metrics


def _read_rounds():
    rounds = []
    if os.path.exists(EVAL_PATH):
        for m in re.finditer(r"^## 第 (\d+) 轮", open(EVAL_PATH, encoding="utf-8").read(), re.M):
            rounds.append(int(m.group(1)))
    return rounds


def _write_eval(metrics, rows):
    os.makedirs(ROUNDS_DIR, exist_ok=True)
    rounds = _read_rounds()
    n_round = (rounds[-1] + 1) if rounds else 1
    title = f"第 {n_round} 轮"
    with open(os.path.join(ROUNDS_DIR, f"round-{n_round:02d}.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=1)

    lines = [f"# Screening 回归评估\n", "- 更新：{now}\n".format(now=__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")),
             f"- gold 集：{len(rows)} 条（screening_gold.jsonl）\n"]
    for r in rounds + [n_round]:
        p = os.path.join(ROUNDS_DIR, f"round-{r:02d}.json")
        if not os.path.exists(p):
            continue
        m = json.load(open(p, encoding="utf-8"))
        lines.append(f"## 第 {r} 轮\n")
        must_recall = m.get("MUST_recall", m.get("S_recall"))
        must_precision = m.get("MUST_precision", m.get("S_precision"))
        actionable_recall = m.get("actionable_recall", m.get("AS_recall"))
        fp_count = m.get("fp_actionable_count", m.get("fp_A/S_count", 0))
        fn_count = m.get("fn_actionable_count", m.get("fn_B/C_count", 0))
        lines.append(f"- n={m['n']} exact_accuracy={m['exact_accuracy']} MUST_recall={must_recall} "
                     f"MUST_precision={must_precision} actionable_recall={actionable_recall}")
        lines.append(f"- RADAR/DROP 误升为 actionable：{fp_count} 条")
        lines.append(f"- MUST/WEEKLY 误降为 RADAR/DROP：{fn_count} 条")
        lines.append(f"- 预测分布：{m['pred_dist']} / gold 分布：{m['gold_dist']}\n")
        if m.get("fp_list"):
            lines.append("### 误升（FP）")
            for fp in m["fp_list"]:
                lines.append(f"- [{fp['expected']}→{fp['pred']}] {fp['title'][:90]}")
            lines.append("")
        if m.get("fn_list"):
            lines.append("### 误降（FN）")
            for fn in m["fn_list"]:
                lines.append(f"- [{fn['expected']}→{fn['pred']}] {fn['title'][:90]}")
            lines.append("")
    with open(EVAL_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return EVAL_PATH


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        g = build_gold(force=True)
        print("gold built:", len(g))
        from collections import Counter
        print(Counter(x["expected_grade"] for x in g))
