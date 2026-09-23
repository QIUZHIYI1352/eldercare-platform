"""特征语义方向回归测试（确定性，不依赖 mediapipe / 摄像头 / 数据库）。

背景：`trunk_inclination` 曾经把参照轴取成「图像向下」，导致
**直立 = 180°、水平弯腰 = 90°**，与 `backend/routers/actions.py` 里
JOINT_FIELDS 声明的「0=直立，越大越前倾」正好相反。
后果是「弯腰操作 ≥30」「深弯腰 ≥55」在所有人站着的画面里持续命中（误报），
而「站立准备 ≤20」永远不命中（漏报）。

这里用构造关键点把语义方向钉死，防止以后被人改回去。
"""
import math

import pytest

from backend.vision.action_recognizer import evaluate_conditions, unknown_joints
from backend.vision.pose_engine import LM, PoseEngine

HIP = (0.50, 0.65)
TORSO = 0.35


def build(lean_deg, invert=False):
    """构造 33 个关键点：髋固定，躯干相对竖直方向前倾 lean_deg。

    图像坐标 y 向下，因此「向上」是 y 减小。
    """
    a = math.radians(lean_deg)
    sx = HIP[0] + TORSO * math.sin(a)
    sy = HIP[1] - TORSO * math.cos(a)
    if invert:
        sy = HIP[1] + TORSO * math.cos(a)     # 肩跑到髋下方（倒立）

    pts = [(0.5, 0.5, 1.0)] * 33
    pts[LM["left_shoulder"]] = (sx - 0.05, sy, 1.0)
    pts[LM["right_shoulder"]] = (sx + 0.05, sy, 1.0)
    pts[LM["left_hip"]] = (HIP[0] - 0.03, HIP[1], 1.0)
    pts[LM["right_hip"]] = (HIP[0] + 0.03, HIP[1], 1.0)
    for k, y in [("left_knee", 0.80), ("right_knee", 0.80),
                 ("left_ankle", 0.95), ("right_ankle", 0.95),
                 ("left_elbow", sy + 0.05), ("right_elbow", sy + 0.05),
                 ("left_wrist", sy + 0.10), ("right_wrist", sy + 0.10)]:
        pts[LM[k]] = (sx, y, 1.0)
    return pts


def trunk(pts):
    return PoseEngine.compute_features(pts)["trunk_inclination"]


def test_trunk_inclination_is_zero_when_upright():
    """直立必须是 0°，不是 180°。这条就是当初那个方向 bug 的哨兵。"""
    v = trunk(build(0))
    assert v == pytest.approx(0.0, abs=0.5), (
        f"直立时 trunk_inclination={v:.1f}°，应≈0°。"
        f"若得到 180°，说明参照轴被改成了「图像向下」(0,1)。")


def test_trunk_inclination_tracks_real_lean_angle():
    """返回值应等于真实前倾角（构造角 ↔ 测量角 1:1）。"""
    for deg in (15, 45, 75):
        v = trunk(build(deg))
        assert v == pytest.approx(deg, abs=1.0), f"前倾 {deg}° 测得 {v:.1f}°"


def test_trunk_inclination_is_90_when_horizontal():
    v = trunk(build(90))
    assert v == pytest.approx(90.0, abs=1.0), f"躯干水平应≈90°，实测 {v:.1f}°"


def test_trunk_inclination_over_90_when_inverted():
    """肩在髋下方（倒立）应 >90°，趋近 180°。"""
    v = trunk(build(0, invert=True))
    assert v == pytest.approx(180.0, abs=1.0), f"倒立应≈180°，实测 {v:.1f}°"


def test_seeded_trunk_conditions_classify_correctly():
    """用 seed.py 里的真实条件，验证直立/弯腰/深弯腰能被正确区分。"""
    stand = [{"joint": "trunk_inclination", "op": "<=", "value": 20}]
    bend = [{"joint": "trunk_inclination", "op": ">=", "value": 30}]
    deep = [{"joint": "trunk_inclination", "op": ">=", "value": 55}]

    f_stand = PoseEngine.compute_features(build(0))
    f_bend = PoseEngine.compute_features(build(45))
    f_deep = PoseEngine.compute_features(build(75))

    assert evaluate_conditions(f_stand, stand), "直立时应判为「站立准备」"
    assert not evaluate_conditions(f_stand, bend), "直立时不得判为「弯腰操作」"
    assert not evaluate_conditions(f_stand, deep), "直立时不得判为「深弯腰」"

    assert evaluate_conditions(f_bend, bend), "前倾 45° 应判为「弯腰操作」"
    assert not evaluate_conditions(f_bend, deep), "前倾 45° 不应判为「深弯腰」"

    assert evaluate_conditions(f_deep, deep), "前倾 75° 应判为「深弯腰」"


def test_degenerate_trunk_does_not_crash():
    """全部关键点重合时不得抛异常（肩髋中心重合->无方向可算）。"""
    pts = [(0.5, 0.5, 1.0)] * 33
    v = trunk(pts)
    assert v == pytest.approx(0.0, abs=1e-6)


def test_hand_height_negative_means_above_shoulder():
    """hand_height 的约定：负值 = 手腕高于肩（actions.py 中「负=高于肩」）。"""
    pts = build(0)
    sy = pts[LM["left_shoulder"]][1]
    pts[LM["left_wrist"]] = (pts[LM["left_wrist"]][0], sy - 0.15, 1.0)
    pts[LM["right_wrist"]] = (pts[LM["right_wrist"]][0], sy - 0.15, 1.0)
    f = PoseEngine.compute_features(pts)
    assert f["hand_height_left"] < 0 and f["hand_height_right"] < 0
    raised = [{"joint": "hand_height_left", "op": "<=", "value": -0.1},
              {"joint": "hand_height_right", "op": "<=", "value": -0.1}]
    assert evaluate_conditions(f, raised), "双腕高于肩应判为「双臂抬起」"


def test_hands_distance_zero_when_wrists_meet():
    pts = build(0)
    meet = (0.5, 0.7, 1.0)
    pts[LM["left_wrist"]] = meet
    pts[LM["right_wrist"]] = meet
    f = PoseEngine.compute_features(pts)
    assert f["hands_distance"] == pytest.approx(0.0, abs=1e-6)
    close = [{"joint": "hands_distance", "op": "<=", "value": 0.55}]
    assert evaluate_conditions(f, close), "双手重合应判为「双手靠近」"


def test_rule_joint_names_diverge_between_2d_and_3d():
    """规则条件写在二维特征名上；三维特征键名不同。

    这条测试是为了让「规则判定必须用二维特征」这个约束显式化：
    一旦有人把规则判定改成用三维特征，5/6 个动作会静默失效
    （unknown_joints 会返回非空）。
    """
    f2d = PoseEngine.compute_features(build(0))
    f3d = PoseEngine.compute_features_3d(build(0))
    seeded = [
        [{"joint": "trunk_inclination", "op": "<=", "value": 20}],
        [{"joint": "left_knee_angle", "op": "<=", "value": 110},
         {"joint": "right_knee_angle", "op": "<=", "value": 110}],
        [{"joint": "hand_height_left", "op": "<=", "value": -0.1},
         {"joint": "hand_height_right", "op": "<=", "value": -0.1}],
    ]
    for conds in seeded:
        assert not unknown_joints(conds, set(f2d)), "规则条件必须能在二维特征里找到"
    # 其中有条件在三维特征里是找不到的——这正是不能用三维特征判定规则的原因
    assert unknown_joints(seeded[0], set(f3d)), \
        "trunk_inclination 不应存在于三维特征里；若存在说明特征命名约定已变，请复核规则判定路径"
