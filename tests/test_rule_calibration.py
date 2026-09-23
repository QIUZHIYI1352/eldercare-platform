"""规则型动作阈值标定逻辑的单元测试。

`calibrate_rule.suggest` 的职责：给定「应该命中」与「不应该命中」两组实测取值，
给出一个**偏保守**的建议阈值——宁可漏报也不能误报。
"""
import random

import pytest

pytest.importorskip("cv2", reason="calibrate_rule 需要 opencv")

from calibrate_rule import CONSERVATIVE, HIT_SIDE_PCT, MISS_SIDE_PCT, suggest  # noqa: E402


def _spread(lo, hi, n=300, seed=1):
    r = random.Random(seed)
    return [r.uniform(lo, hi) for _ in range(n)]


def test_le_condition_threshold_sits_between_groups():
    """`<=` 条件：命中组取值小、无关组取值大，阈值应落在两者之间。"""
    hit = {"hands_distance": _spread(0.2, 0.5)}
    miss = {"hands_distance": _spread(1.2, 2.5)}
    conds = [{"joint": "hands_distance", "op": "<=", "value": 0.55}]
    out, problems = suggest(conds, hit, miss)
    assert not problems
    s = out[0]
    assert s["hit_edge"] < s["value"] < s["miss_edge"], \
        f"阈值 {s['value']} 应落在命中侧 {s['hit_edge']} 与无关侧 {s['miss_edge']} 之间"


def test_ge_condition_threshold_sits_between_groups():
    """`>=` 条件：命中组取值大、无关组取值小，阈值应落在两者之间。"""
    hit = {"trunk_inclination": _spread(45, 75)}
    miss = {"trunk_inclination": _spread(0, 15)}
    conds = [{"joint": "trunk_inclination", "op": ">=", "value": 30}]
    out, problems = suggest(conds, hit, miss)
    assert not problems
    s = out[0]
    assert s["miss_edge"] < s["value"] < s["hit_edge"], \
        f"阈值 {s['value']} 应落在无关侧 {s['miss_edge']} 与命中侧 {s['hit_edge']} 之间"


def test_threshold_is_on_the_conservative_side():
    """阈值必须偏向「命中侧」——也就是更难被无关动作满足的一侧。"""
    assert CONSERVATIVE < 0.5, "保守系数应小于 0.5，否则会偏向易误报的一侧"

    hit = {"hands_distance": _spread(0.2, 0.5)}
    miss = {"hands_distance": _spread(1.2, 2.5)}
    s = suggest([{"joint": "hands_distance", "op": "<=", "value": 0.55}], hit, miss)[0][0]
    # 到命中侧的归一化位置应小于 0.5（更靠近命中侧 = 更严格 = 更少误报）
    pos = (s["value"] - s["hit_edge"]) / (s["miss_edge"] - s["hit_edge"])
    assert pos < 0.5, f"阈值偏向无关侧（pos={pos:.2f}），会更容易误报"

    hit2 = {"trunk_inclination": _spread(45, 75)}
    miss2 = {"trunk_inclination": _spread(0, 15)}
    s2 = suggest([{"joint": "trunk_inclination", "op": ">=", "value": 30}], hit2, miss2)[0][0]
    pos2 = (s2["hit_edge"] - s2["value"]) / (s2["hit_edge"] - s2["miss_edge"])
    assert pos2 < 0.5, f"阈值偏向无关侧（pos={pos2:.2f}），会更容易误报"


def test_no_separation_is_reported_not_silently_accepted():
    """两组分布重叠时必须报出「无区分度」，而不是硬给一个阈值。"""
    hit = {"trunk_inclination": _spread(40, 80)}
    miss = {"trunk_inclination": _spread(40, 80, seed=2)}
    out, problems = suggest([{"joint": "trunk_inclination", "op": ">=", "value": 30}],
                            hit, miss)
    assert out == []
    assert problems and "无区分度" in problems[0]


def test_insufficient_samples_are_reported():
    """样本太少时不给阈值，提示重采。"""
    out, problems = suggest([{"joint": "hands_distance", "op": "<=", "value": 0.55}],
                            {"hands_distance": [0.3, 0.4]},
                            {"hands_distance": [1.0, 1.1]})
    assert out == []
    assert problems and "样本不足" in problems[0]


def test_percentiles_ignore_single_frame_outliers():
    """个别检测抖动帧不应带偏结论（用分位而非极值）。"""
    hit = {"hands_distance": _spread(0.2, 0.5, n=200)}
    hit["hands_distance"].append(99.0)          # 一帧关键点错位
    miss = {"hands_distance": _spread(1.2, 2.5, n=200)}
    s = suggest([{"joint": "hands_distance", "op": "<=", "value": 0.55}], hit, miss)[0][0]
    assert s["hit_edge"] < 2.0, f"离群帧污染了命中侧边界：{s['hit_edge']}"
    assert 0 < s["value"] < s["miss_edge"]
