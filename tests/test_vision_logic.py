"""视觉识别相关纯逻辑测试（不依赖摄像头 / 数据库）。"""
import numpy as np
import pytest

from backend.vision.action_recognizer import ActionRecognizer, evaluate_conditions
from backend.vision.sequence_matcher import match_sequence
from backend.vision.template_matcher import (
    FEATURE_ORDER, TemplateMatcher, dtw_distance, down_sample,
    features_to_vector, normalize_vectors,
)


# ---------- 规则条件判定 ----------

def test_evaluate_conditions_all_ops():
    f = {"a": 10.0, "b": 5.0}
    assert evaluate_conditions(f, [{"joint": "a", "op": ">=", "value": 10}])
    assert evaluate_conditions(f, [{"joint": "a", "op": ">", "value": 9.9}])
    assert evaluate_conditions(f, [{"joint": "b", "op": "<=", "value": 5}])
    assert evaluate_conditions(f, [{"joint": "b", "op": "<", "value": 5.1}])
    assert evaluate_conditions(f, [{"joint": "a", "op": "==", "value": 10}])
    assert evaluate_conditions(f, [{"joint": "a", "op": "!=", "value": 11}])
    assert not evaluate_conditions(f, [{"joint": "a", "op": ">", "value": 10}])


def test_evaluate_conditions_requires_all():
    f = {"a": 10.0, "b": 5.0}
    ok = [{"joint": "a", "op": ">=", "value": 5}, {"joint": "b", "op": "<=", "value": 6}]
    bad = [{"joint": "a", "op": ">=", "value": 5}, {"joint": "b", "op": ">=", "value": 6}]
    assert evaluate_conditions(f, ok)
    assert not evaluate_conditions(f, bad)


def test_evaluate_conditions_rejects_unknown_joint_and_op():
    f = {"a": 10.0}
    assert not evaluate_conditions(f, [{"joint": "nope", "op": ">=", "value": 1}])
    assert not evaluate_conditions(f, [{"joint": "a", "op": "??", "value": 1}])
    assert not evaluate_conditions(f, [])


def test_action_recognizer_requires_duration():
    """必须持续满足指定时长才算达成，单帧抖动不算。"""
    tpl = [{"id": "a1", "conditions": [{"joint": "trunk", "op": ">=", "value": 30}],
            "duration": 1.0}]
    rec = ActionRecognizer()
    assert rec.update({"trunk": 45}, tpl, now_ts=0) == []          # 首帧：刚开始计时
    assert rec.update({"trunk": 45}, tpl, now_ts=0.5) == []        # 未达 1 秒
    assert rec.update({"trunk": 45}, tpl, now_ts=1.0) == ["a1"]    # 满 1 秒 → 达成
    # 中途被打断后需重新计时
    assert rec.update({"trunk": 0}, tpl, now_ts=2.0) == []
    assert rec.update({"trunk": 45}, tpl, now_ts=3.0) == []
    assert rec.update({"trunk": 45}, tpl, now_ts=4.5) == ["a1"]


# ---------- 流程漏步比对（LCS） ----------

def test_match_sequence_full_and_partial():
    r = match_sequence(["A", "B", "C"], ["A", "B", "C"])
    assert r["completed_steps"] == [0, 1, 2] and r["missed_steps"] == []
    assert r["current_step"] == -1 and r["score"] == 100.0

    r = match_sequence(["A", "B", "C"], ["A", "C"])
    assert r["completed_steps"] == [0, 2], "LCS 应把已按顺序出现的步骤算作完成"
    assert r["missed_steps"] == [1]
    assert r["current_step"] == 1
    assert r["score"] == pytest.approx(66.7, abs=0.1)


def test_match_sequence_edge_cases():
    assert match_sequence([], ["A"])["score"] == 100.0
    r = match_sequence(["A", "B"], [])
    assert r["completed_steps"] == [] and r["missed_steps"] == [0, 1]
    assert r["current_step"] == 0 and r["score"] == 0.0
    # 乱序执行不应被算作完成
    r = match_sequence(["A", "B"], ["B"])
    assert r["completed_steps"] == [1] and r["missed_steps"] == [0]


# ---------- 特征向量 ----------

def test_features_to_vector_shape_and_defaults():
    v = features_to_vector({})
    assert v.shape == (len(FEATURE_ORDER),)
    assert np.all(v == 0)
    v2 = features_to_vector({"trunk_inclination": 42})
    assert v2[FEATURE_ORDER.index("trunk_inclination")] == 42


def test_normalize_vectors_zero_variance_safe():
    """全常量维度标准差为 0，不能产生 NaN / Inf。"""
    arr = np.ones((5, 4), dtype=np.float32) * 7
    out = normalize_vectors(arr)
    assert np.all(np.isfinite(out))


def test_dtw_distance_identity_and_shift():
    a = np.array([[0.0], [1.0], [2.0], [3.0]], dtype=np.float32)
    assert dtw_distance(a, a) == pytest.approx(0.0)
    # 序列整体平移，DTW 可以做时间对齐 → 距离应很小
    b = np.array([[0.0], [0.0], [1.0], [2.0], [3.0]], dtype=np.float32)
    assert dtw_distance(a, b) < 1.0


def test_down_sample_limits_length():
    seq = [[float(i)] * 3 for i in range(100)]
    out = down_sample(seq, target_frames=30)
    assert len(out) == 30
    assert len(down_sample(seq[:10], target_frames=30)) == 10  # 短序列保持原样


# ---------- 序列模板匹配（回归测试） ----------

def _fake_motion(seed=0, n=24, d=16):
    """构造一段量级接近真实关节特征的动作序列。"""
    rng = np.random.default_rng(seed)
    base = np.zeros((n, d), dtype=np.float32)
    base[:, 0] = np.linspace(10, 60, n)     # 躯干前倾（度）
    base[:, 2] = np.linspace(170, 90, n)    # 肘角（度）
    base[:, 6] = np.linspace(175, 110, n)   # 膝角（度）
    base[:, 12] = np.linspace(1.2, 0.4, n)  # 双手距离（肩宽比）
    return base + rng.normal(0, 0.5, base.shape)


def _feed(template, sequence=None, threshold=8.0, start_ts=1000.0):
    """以 template 为模板，逐帧喂入 sequence（默认喂模板自身），返回 (最小距离, 是否命中)。"""
    seq = template if sequence is None else sequence
    m = TemplateMatcher(template, threshold=threshold)
    best, hit = float("inf"), False
    for i, v in enumerate(seq):
        dist, h = m.update(v, now_ts=start_ts + i)
        best = min(best, dist)
        hit = hit or h
    return best, hit


def test_template_matcher_hits_identical_motion():
    """回归：模板做过 Z-score 归一化，若实时窗口不归一化会尺度错配，
    导致完全一致的动作也算出几百的距离、永远不命中。"""
    motion = _fake_motion()
    best, hit = _feed(motion)
    assert hit, f"与模板完全一致的动作必须命中（最小距离={best:.2f}）"
    assert best < 1e-3


def test_template_matcher_tolerates_jitter():
    motion = _fake_motion()
    noisy = motion + np.random.default_rng(7).normal(0, 0.4, motion.shape)
    _best, hit = _feed(motion, noisy)
    assert hit, "同一动作的轻微抖动应当仍能命中"


def test_template_matcher_rejects_unrelated_motion():
    motion = _fake_motion()
    other = np.zeros_like(motion)
    other[:, 3] = np.linspace(20, 170, motion.shape[0])
    other[:, 8] = np.linspace(90, 175, motion.shape[0])
    best, hit = _feed(motion, other)
    assert not hit, f"无关动作不应命中（最小距离={best:.2f}）"


def test_template_matcher_cooldown_suppresses_repeats():
    motion = _fake_motion(seed=3)
    m = TemplateMatcher(motion, threshold=8.0)
    hits = [h for i, v in enumerate(list(motion) * 2)
            for _d, h in [m.update(v, now_ts=1000 + i * 0.05, cooldown=2.0)]]
    assert hits.count(True) <= 1, "冷却期内不应重复触发"


def test_template_matcher_rejects_bad_template():
    with pytest.raises(ValueError):
        TemplateMatcher([])          # 一维空数组
    with pytest.raises(ValueError):
        TemplateMatcher(np.zeros((0, 16)))  # 零帧


def test_template_matcher_min_ratio_gate():
    motion = _fake_motion(seed=11)
    m = TemplateMatcher(motion, threshold=8.0)
    # 不足最小比例前，距离为无穷大且不命中
    for i in range(max(1, int(len(motion) * 0.6)) - 1):
        dist, hit = m.update(motion[i], now_ts=100 + i)
        assert dist == float("inf") and hit is False
