"""动作「规范性」判据的回归测试。

区分两件事：
  - 「动作有没有发生」（现有规则/DTW 实现的目标）
  - 「动作做得对不对」（本文件验证的目标）

护理实训的「规范」大多是几何可观测的（屈膝、腰背角度、幅度、左右对称），
因此可以用可解释的阈值判据来做，而不需要训练黑盒模型。
"""
import pytest

from backend.vision.action_recognizer import evaluate_conditions
from backend.vision.pose_engine import PoseEngine
from tests.pose_builder import build_pose


def feat(**kw):
    return PoseEngine.compute_features(build_pose(**kw))


# ---------------------------------------------------------------- 构造器自检
# 先证明「造出来的姿态」与参数一致，否则后面所有断言的失败原因都无法区分
# 是「判据错」还是「造数错」。

@pytest.mark.parametrize("target", [180, 150, 120, 90])
def test_builder_reproduces_knee_angle(target):
    d = feat(knee_deg=target)
    # 左右腿各有 0.04 的横向偏移，允许约 4° 的构造偏差
    assert abs(d["left_knee_angle"] - target) < 5.0
    assert abs(d["right_knee_angle"] - target) < 5.0


@pytest.mark.parametrize("target", [0, 30, 60, 90])
def test_builder_reproduces_trunk_angle(target):
    assert abs(feat(trunk_deg=target)["trunk_inclination"] - target) < 1.0


# ------------------------------------------------------------ 规范判据
# 合格弯腰 = 躯干确实前倾，且双膝屈曲分担负荷（护理搬抬的核心规范）

GOOD_BEND = [
    {"joint": "trunk_inclination", "op": ">=", "value": 30},
    {"joint": "left_knee_angle", "op": "<=", "value": 150},
    {"joint": "right_knee_angle", "op": "<=", "value": 150},
]


def test_qualified_bend_passes():
    """屈膝 + 躯干前倾 = 合格。"""
    assert evaluate_conditions(feat(trunk_deg=40, knee_deg=120), GOOD_BEND)


def test_straight_leg_bend_is_rejected():
    """直腿弯腰（不屈膝）应判不合格——这是实训里最需要抓的危险动作。"""
    d = feat(trunk_deg=40, knee_deg=178)
    assert not evaluate_conditions(d, GOOD_BEND)
    # 而且必须能说清「哪一项不达标」，才能给学生反馈
    assert d["trunk_inclination"] >= 30          # 弯腰幅度是够的
    assert d["left_knee_angle"] > 150            # 不合格的原因是膝没屈


def test_squat_without_bend_is_rejected():
    """只有屈膝、躯干没前倾，不算弯腰。"""
    assert not evaluate_conditions(feat(trunk_deg=15, knee_deg=100), GOOD_BEND)


def test_upright_is_rejected():
    assert not evaluate_conditions(feat(trunk_deg=0, knee_deg=178), GOOD_BEND)


# ------------------------------------------------ 检出式判据的已知缺口
# 现有种子模板只判「发生没发生」，因此「直腿弯腰」会被当成合格的弯腰动作。
# 这个测试把缺口写下来：一旦补上规范判据，它就会失败，提示该更新断言。

NAIVE_BEND = [{"joint": "trunk_inclination", "op": ">=", "value": 30}]


def test_detection_only_cannot_tell_right_from_wrong():
    """记录现状：仅凭「弯腰发生」无法区分标准弯腰与直腿弯腰。"""
    good = feat(trunk_deg=40, knee_deg=120)
    bad = feat(trunk_deg=40, knee_deg=178)
    assert evaluate_conditions(good, NAIVE_BEND)
    assert evaluate_conditions(bad, NAIVE_BEND)      # 两者都被判为「弯腰」
    # 加上膝角条件后才区分得开
    assert evaluate_conditions(good, GOOD_BEND)
    assert not evaluate_conditions(bad, GOOD_BEND)


# ------------------------------------------------------------ 左右对称性

def test_symmetry_check_can_catch_one_sided_pose():
    """单手操作 / 单侧发力：左右肢角度差超过阈值即可判不规范。"""
    d = feat(trunk_deg=30, knee_deg=140)
    diff = abs(d["left_knee_angle"] - d["right_knee_angle"])
    assert diff < 20.0, "构造的对称姿态不应被误判为不对称"


def test_low_visibility_pose_still_yields_finite_features():
    """关键点退化时特征必须是有限值，否则判据会以异常方式失败。"""
    degenerated = [(0.5, 0.5, 0.0)] * 33
    d = PoseEngine.compute_features(degenerated)
    for key, val in d.items():
        assert isinstance(val, float)
        assert val == val, f"{key} 出现 NaN"
