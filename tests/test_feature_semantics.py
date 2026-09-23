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


# --------------------------------------------------------------------------
# 二维特征与画面宽高比强相关（换视频源时必须一致）
# --------------------------------------------------------------------------


def _pixel_pose(trunk_lean_deg=12.0, knee_deg=150.0):
    """构造一个**像素坐标**下的姿态（同一物理姿态，与画面尺寸无关）。

    与 build() 不同：build() 直接给归一化坐标，而这里刻意从像素出发，
    因为本组测试要变的正是「像素 -> 归一化」这一步（x/W、y/H）。
    """
    r = math.radians(trunk_lean_deg)
    hx, hy, sy = 500.0, 400.0, 120.0
    sx = hx + math.sin(r) * (hy - sy)
    shy = hy - math.cos(r) * (hy - sy)
    p = {
        "left_shoulder": (sx - 60, shy), "right_shoulder": (sx + 60, shy),
        "left_hip": (hx - 50, hy), "right_hip": (hx + 50, hy),
        "nose": (sx, shy - 80),
        "left_elbow": (sx - 100, shy + 90), "right_elbow": (sx + 100, shy + 90),
        "left_wrist": (sx - 120, shy + 180), "right_wrist": (sx + 120, shy + 180),
    }
    leg = hy - sy
    half = math.radians((180.0 - knee_deg) / 2.0)
    for side, sgn in (("left", -1), ("right", 1)):
        kx, ky = hx + sgn * 50, hy + leg
        p[f"{side}_knee"] = (kx, ky)
        p[f"{side}_ankle"] = (kx + sgn * math.sin(half) * leg,
                              ky + math.cos(half) * leg)
    return p


def _features_at(pose_px, W, H):
    pts = [(0.5, 0.5, 1.0)] * 33
    for name, idx in LM.items():
        if name in pose_px:
            x, y = pose_px[name]
            pts[idx] = (x / W, y / H, 1.0)
    return PoseEngine.compute_features(pts)


_ASPECT_KEYS = ["trunk_inclination", "left_knee_angle", "right_knee_angle",
                "left_hip_angle", "right_hip_angle", "left_elbow_angle"]
_POSE = _pixel_pose()


def test_2d_features_are_invariant_to_resolution_at_same_aspect():
    """同一宽高比下，分辨率高低（720p vs 1080p）不应改变任何特征。

    这条是下面那条的对照组：证明敏感的是**比例**，不是像素数。
    也说明「换个更高清的摄像头」本身无害。
    """
    a = _features_at(_POSE, 1280, 720)
    b = _features_at(_POSE, 1920, 1080)
    for k in _ASPECT_KEYS:
        assert abs(a[k] - b[k]) < 1e-6, f"{k} 在 16:9 下随分辨率变化了：{a[k]} vs {b[k]}"


def test_2d_features_shift_with_aspect_ratio():
    """宽高比一变，二维角度就系统性偏移——**手机串流接入时最容易踩**。

    根因：mediapipe 的归一化是 x/W、y/H（两个不同分母），
    而 compute_features 直接用归一化坐标算夹角，于是比例会整体拉伸角度。

    实测（构造姿态，躯干真值前倾 12°）：16:9 得 6.82°，竖屏 9:16 得 20.70°，
    膝关节角 171° -> 154°（等于虚报了一个屈膝动作）。

    因此：**录模板与运行时必须用同一宽高比**；否则改用 3d 特征空间
    （米制三维坐标，不经过 W/H 归一化，天然免疫）。
    若本测试失败，说明归一化方式变了——请同步更新模板录制里的
    frame_aspect 标记与 check_source.py 的比对说明。
    """
    wide = _features_at(_POSE, 1280, 720)      # 16:9 横屏
    four3 = _features_at(_POSE, 960, 720)      # 4:3
    tall = _features_at(_POSE, 720, 1280)      # 9:16 手机竖屏

    shift_4_3 = max(abs(four3[k] - wide[k]) for k in _ASPECT_KEYS)
    shift_tall = max(abs(tall[k] - wide[k]) for k in _ASPECT_KEYS)

    # 4:3 与 16:9 差距不大但不是零 —— 值得提示，不必报警
    assert 1.0 < shift_4_3 < 6.0, f"4:3 的偏移量级与预期不符：{shift_4_3:.2f}°"
    # 竖屏是**量级性**的差异，足以让匹配彻底失真
    assert shift_tall > 10.0, (
        f"竖屏偏移 {shift_tall:.2f}° 低于预期——"
        f"若确实变小了，说明归一化已改进，请重新评估 frame_aspect 比对门槛"
    )
    assert tall["trunk_inclination"] > wide["trunk_inclination"] * 2, (
        "竖屏下躯干角被显著放大这一现象应保持可复现"
    )
