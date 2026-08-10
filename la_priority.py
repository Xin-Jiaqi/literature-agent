#!/usr/bin/env python3
"""推荐优先级轴；与 AI system 的 A/B/C/D 阅读阶段完全独立。"""

MUST = "MUST"
WEEKLY = "WEEKLY"
RADAR = "RADAR"
DROP = "DROP"

ORDER = (MUST, WEEKLY, RADAR, DROP)
LEGACY_MAP = {"S": MUST, "A": WEEKLY, "B": RADAR, "C": DROP}
LABELS = {
    MUST: "必须知道（自动获取全文并执行 A）",
    WEEKLY: "本周值得读",
    RADAR: "雷达信号（最多扫一眼）",
    DROP: "不占注意力",
}


def normalize_priority(value):
    value = (value or "").strip().upper()
    return LEGACY_MAP.get(value, value)


def is_actionable(value):
    return normalize_priority(value) in (MUST, WEEKLY)


def legacy_compatible(value, expected):
    return normalize_priority(value) == normalize_priority(expected)
