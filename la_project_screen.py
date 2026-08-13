#!/usr/bin/env python3
"""Project-explicit screening for the multi-user campus pilot.

The personal classifier remains unchanged. This scorer receives a confirmed
project profile as data, returns only MUST/WEEKLY/RADAR/DROP, and has no access
to Obsidian paths or global personal configuration.
"""
from __future__ import annotations

import json
import re

from la_priority import DROP, MUST, RADAR, WEEKLY


PRIORITY_SCORE = {MUST: 4, WEEKLY: 3, RADAR: 2, DROP: 1}
NOVELTY_TERMS = (
    "new", "novel", "generalized", "unified", "framework", "mechanism",
    "first-principles", "systematic", "we propose", "we establish", "prediction",
)


def _normalize_phrase(value):
    return " ".join((value or "").strip().lower().split())


def _contains(text, phrase):
    phrase = _normalize_phrase(phrase)
    if not phrase:
        return False
    if re.fullmatch(r"[a-z0-9-]+", phrase):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text))
    return phrase in text


def profile_from_row(row):
    """Convert a research_profiles row into an explicit, portable context."""
    if row is None:
        raise ValueError("confirmed research profile required")
    try:
        include_terms = json.loads(row["include_terms_json"] or "[]")
        exclude_terms = json.loads(row["exclude_terms_json"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid research profile terms") from exc
    if (not isinstance(include_terms, list) or not isinstance(exclude_terms, list)
            or any(not isinstance(term, str) for term in include_terms + exclude_terms)):
        raise ValueError("invalid research profile terms")
    if not row["confirmed_at"]:
        raise ValueError("research profile must be confirmed before screening")
    return {
        "summary": row["summary"],
        "include_terms": include_terms,
        "exclude_terms": exclude_terms,
        "revision": int(row["revision"]),
    }


def classify_for_profile(title, abstract, profile, truncated=False):
    """Return priority, explanation, matched terms, confidence and numeric score."""
    title_text = _normalize_phrase(title)
    abstract_text = _normalize_phrase(abstract)
    full_text = f"{title_text} {abstract_text}".strip()
    include_terms = [_normalize_phrase(x) for x in profile.get("include_terms", []) if _normalize_phrase(x)]
    exclude_terms = [_normalize_phrase(x) for x in profile.get("exclude_terms", []) if _normalize_phrase(x)]
    if not include_terms:
        raise ValueError("profile requires at least one include term")

    title_hits = [term for term in include_terms if _contains(title_text, term)]
    abstract_hits = [term for term in include_terms if term not in title_hits and _contains(abstract_text, term)]
    exclude_hits = [term for term in exclude_terms if _contains(full_text, term)]
    novelty = [term for term in NOVELTY_TERMS if _contains(full_text, term)]

    topic_hit = bool(title_hits or abstract_hits)
    score = len(title_hits) * 4 + len(abstract_hits) * 2
    if topic_hit:
        score += min(2, len(novelty))
    if exclude_hits:
        score -= 8 + 2 * len(exclude_hits)

    if exclude_hits and score < 4:
        priority = DROP
        reason = f"命中排除主题：{', '.join(exclude_hits)}"
    elif score >= 8 or (score >= 6 and title_hits and novelty):
        priority = MUST
        reason = "标题/摘要直接命中项目核心主题，且推进信号强"
    elif score >= 4:
        priority = WEEKLY
        reason = "与项目有明确关联，值得进入本周阅读"
    elif score >= 2:
        priority = RADAR
        reason = "存在项目相关信号，但直接推进证据有限"
    else:
        priority = DROP
        reason = "未命中该项目的明确关注主题"

    confidence = "高"
    if truncated:
        confidence = "中"
    if not abstract_text:
        confidence = "低"
    matched = title_hits + abstract_hits
    return {
        "priority": priority,
        "reason": reason,
        "matched_terms": matched,
        "excluded_terms": exclude_hits,
        "confidence": confidence,
        "score": score,
        "profile_revision": int(profile.get("revision", 1)),
    }


def sort_key(result):
    return (PRIORITY_SCORE[result["priority"]], result["score"])
