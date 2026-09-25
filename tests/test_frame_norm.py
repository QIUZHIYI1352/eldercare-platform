"""宽高比归一化（补边）的几何不变性验证。

核心断言只有一条：**同一姿态在任意宽高比下录制，补边到模板比例后算出的
特征必须与"直接在模板比例下录制"完全一致**。这是几何恒等式，不是经验规律，
因此可以逐位比对，而不是只看"没报错"。

顺带把 record_template.py 里记录的实测事实也复现出来当护栏：
16:9 → 竖屏 9:16 时躯干角 6.8°→20.7°（同一物理姿态），补边后必须回到 6.8°。
"""
import math

import numpy as np
import pytest

from backend.vision.frame_norm import (ASPECT_TOL, AspectPlan, aspect_of,
                                       group_aspects, normalized_pad,
                                       pad_to_aspect, plan_for)
from backend.vision.pose_engine import PoseEngine
from tests.pose_builder import build_pose

A_169 = 16 / 9
A_43 = 4 / 3
A_916 = 9 / 16


def _pose_at_aspect(pts_sq, aspect):
    """把「正方画布」下构造的姿态换算到指定宽高比的画布。

    物理上同一个点：正方画布下 x_norm = px/H，而 W = A·H 时 x_norm = px/(A·H)，
    因此 x_norm_A = x_norm_sq / A。这正是 mediapipe 归一化坐标的实际行为。
    """
    return [(p[0] / aspect, p[1], p[2]) for p in pts_sq]


def _feat(pts):
    return PoseEngine.compute_features(pts)


# ------------------------------------------- 恒等式：补边后 == 模板比例下录制

@pytest.mark.parametrize("source_aspect,target_aspect", [
    (A_916, A_169),   # 竖屏手机 → 横屏模板（最常见）
    (A_43, A_169),    # 4:3 网络摄像头 → 16:9 模板
    (A_169, A_43),    # 反方向：要上下补边
    (1.0, A_169),     # 方画布
])
def test_padding_makes_features_identical_to_template_aspect(source_aspect, target_aspect):
    """补边后的特征必须与「就在模板比例下录制」的一模一样。"""
    pts_sq = build_pose(trunk_deg=12.0, knee_deg=150.0)
    feat_target = _feat(_pose_at_aspect(pts_sq, target_aspect))

    pts_src = _pose_at_aspect(pts_sq, source_aspect)
    feat_src = _feat(pts_src)
    feat_pad = _feat(normalized_pad(pts_src, source_aspect, target_aspect))

    # 前提：不补边时确实有系统性偏移（否则这条测试什么也没证明）
    assert abs(feat_src["trunk_inclination"] - feat_target["trunk_inclination"]) > 1.0

    for k, want in feat_target.items():
        got = feat_pad[k]
        assert got == pytest.approx(want, abs=1e-6), (
            f"{k} 补边后 {got} != 模板比例下 {want}：补边没有真正消除宽高比影响"
        )


def test_documented_vertical_video_shift_is_reproduced_and_fixed():
    """复现记录在案的实测值：同一姿态 16:9 量到 6.8°，竖屏 9:16 量到 20.7°。

    这两个数字来自真实视频（见 record_template.py 的注释），这里用构造姿态
    独立复算——若哪天 mediapipe 的归一化方式或特征实现变了，这条会先炸。
    """
    # 反解：16:9 下量到 6.8° 对应的物理姿态
    theta_phys = math.degrees(math.atan(A_169 * math.tan(math.radians(6.8))))
    pts_sq = build_pose(trunk_deg=theta_phys)

    got_169 = _feat(_pose_at_aspect(pts_sq, A_169))["trunk_inclination"]
    got_916 = _feat(_pose_at_aspect(pts_sq, A_916))["trunk_inclination"]
    assert got_169 == pytest.approx(6.8, abs=0.2)
    assert got_916 == pytest.approx(20.7, abs=0.4)

    # 竖屏源补边到 16:9 后必须回到模板的值
    fixed = _feat(normalized_pad(_pose_at_aspect(pts_sq, A_916), A_916, A_169))
    assert fixed["trunk_inclination"] == pytest.approx(got_169, abs=1e-6)


def test_vertical_video_falsely_reports_knee_bend():
    """竖屏串流会把"腿伸直"虚报成"屈膝"——这是误报，不是精度问题。

    真实视频记录：膝关节角 171° → 154°。这里只钉住**方向**（竖屏会把膝角
    算小，即虚报屈膝）与**量级**（十几度），补边后必须消失。
    """
    pts_sq = build_pose(knee_deg=170.0)
    k_169 = _feat(_pose_at_aspect(pts_sq, A_169))["left_knee_angle"]
    k_916 = _feat(_pose_at_aspect(pts_sq, A_916))["left_knee_angle"]
    assert k_916 < k_169 - 5, "竖屏应当把膝角算得明显更小（虚报屈膝）"

    fixed = _feat(normalized_pad(_pose_at_aspect(pts_sq, A_916), A_916, A_169))
    assert fixed["left_knee_angle"] == pytest.approx(k_169, abs=1e-6)


# ------------------------------------------------------------ 图像级补边本身

def test_pad_keeps_pixels_untouched_and_centered():
    """补边只加黑边：原画面内容必须逐像素不变，且居中。"""
    frame = np.full((90, 120, 3), 7, dtype=np.uint8)   # 4:3
    frame[40:50, 50:60] = 200                           # 一个可定位的方块
    out = pad_to_aspect(frame, A_169)

    assert out.shape[0] == 90
    assert out.shape[1] == pytest.approx(160, abs=1)    # 90 * 16/9
    left = (out.shape[1] - 120) // 2
    assert np.array_equal(out[:, left:left + 120], frame), "原画面内容被改动了"
    assert np.all(out[:, :left] == 0) and np.all(out[:, left + 120:] == 0), "补的边不是黑的"


def test_pad_adds_bars_top_and_bottom_for_wide_source():
    frame = np.full((60, 200, 3), 7, dtype=np.uint8)    # 过宽
    out = pad_to_aspect(frame, A_43)
    assert out.shape[1] == 200
    assert out.shape[0] == pytest.approx(150, abs=1)     # 200 / (4/3)
    top = (out.shape[0] - 60) // 2
    assert np.array_equal(out[top:top + 60, :], frame)
    assert np.all(out[:top, :] == 0) and np.all(out[top + 60:, :] == 0)


def test_pad_returns_same_object_when_aspect_already_matches():
    """比例已经一致时必须是零开销直通（不做拷贝）。"""
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    assert pad_to_aspect(frame, A_169) is frame
    # 1~2% 的舍入差也不该补（1280x720 与 1920x1080 的比值差就在这个量级）
    frame2 = np.zeros((720, 1276, 3), dtype=np.uint8)
    assert pad_to_aspect(frame2, A_169) is frame2


def _dot_angle(img, a=200, b=250):
    """从图像里定位两个标记点，返回它们在**归一化坐标**下的方向角。

    走图像而不是直接算：这样会把补边的偏移、居中、取整都一起验掉。
    """
    pa = np.argwhere(img[:, :, 0] == a)[0]
    pb = np.argwhere(img[:, :, 0] == b)[0]
    dy = (pb[0] - pa[0]) / img.shape[0]
    dx = (pb[1] - pa[1]) / img.shape[1]
    return math.degrees(math.atan2(dy, dx))


def test_padded_image_yields_same_angle_as_native_aspect():
    """走一遍图像：4:3 画布上同一条线，补边后算角度应与直接画在 16:9 里一致。

    物理前提：同一台相机切 4:3 / 16:9 时，**每度像素数不变**，只是画布变宽，
    因此同一物理方向的像素增量 (dx, dy) 相同，只有归一化分母 W 变了。
    这正是 2d 特征失配的根源。
    """
    dx, dy = 60, -60            # 45° 的线：对宽高比最敏感（sinθcosθ 最大）
    h = 90
    w43, w169 = 120, 160        # 恰好是 4:3 与 16:9（90*4/3=120, 90*16/9=160）
    x0, y0 = 20, 70

    def draw(w):
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[y0, x0] = 200
        img[y0 + dy, x0 + dx] = 250
        return img

    img43, img169 = draw(w43), draw(w169)
    padded = pad_to_aspect(img43, A_169)
    assert padded.shape[1] == w169, "补边后的画布宽度不对"

    ang_src = _dot_angle(img43)
    ang_native = _dot_angle(img169)
    ang_padded = _dot_angle(padded)

    assert abs(ang_src - ang_native) > 5, (
        f"前提不成立：4:3 与 16:9 下角度本就相近（{ang_src:.1f} vs {ang_native:.1f}），"
        f"这条测试将无法证明任何事")
    assert ang_padded == pytest.approx(ang_native, abs=1e-9), (
        f"补边后角度 {ang_padded:.3f} 未回到 16:9 的 {ang_native:.3f}")



# --------------------------------------------------------------- 方案决策

def test_majority_aspect_wins_and_minority_is_skipped(capsys):
    """模板票数优先：3 条 16:9 + 1 条 4:3 → 目标 16:9，4:3 那条跳过并说明原因。"""
    tpls = [("翻身A", A_169), ("拍背B", A_169), ("转移C", A_169), ("旧动作D", A_43)]
    plan = plan_for("2d", (1280, 720), tpls)
    assert plan.target_aspect == pytest.approx(A_169, rel=1e-6)
    assert plan.pad is False, "源本来就是 16:9，不该补边"
    assert [n for n, _a, _r in plan.skipped] == ["旧动作D"]
    assert plan.skipped[0][2] > 0.3

    # 剔除必须**说出来**：静默剔除等于静默失效
    from backend.vision.frame_norm import describe
    describe(plan, [("d", "旧动作D", plan.skipped[0][2])])
    out = capsys.readouterr().out
    assert "旧动作D" in out and "剔除" in out
    assert "重录" in out, "只说要剔除、不说怎么补救，用户不知道下一步做什么"


def test_tie_prefers_aspect_closest_to_source():
    """票数相同时选离本源最近的，少补一点。"""
    tpls = [("A", A_169), ("B", A_43)]
    plan = plan_for("2d", (960, 720), tpls)     # 4:3 源
    assert plan.target_aspect == pytest.approx(A_43, rel=1e-6)
    assert plan.pad is False


def test_unmarked_templates_neither_vote_nor_get_skipped():
    """没记录宽高比的老模板不参与投票，也不因此被跳过（与 engine_mode 同一口径）。"""
    tpls = [("新模板", A_169), ("老模板1", None), ("老模板2", None)]
    plan = plan_for("2d", (720, 1280), tpls)    # 竖屏源
    assert plan.target_aspect == pytest.approx(A_169, rel=1e-6)
    assert plan.pad is True
    assert plan.skipped == [], "老模板不能因为缺字段就被跳过"
    assert plan.unmarked == 2
    assert any("未记录录制宽高比" in w for w in plan.warns())


def test_no_pad_when_difference_within_tolerance():
    """1280x720 与 1920x1080 这类舍入差异不该触发补边。"""
    plan = plan_for("2d", (1276, 720), [("x", 1920 / 1080)])
    assert plan.pad is False, f"差异在容差内仍补边了（容差 {ASPECT_TOL}）"


def test_3d_space_never_pads():
    """3d 特征是米制三维坐标，不受宽高比影响；补边会改变 mediapipe 看到的画面，不做。"""
    plan = plan_for("3d", (720, 1280), [("x", A_169)])
    assert plan.pad is False
    assert plan.target_aspect is None
    assert "3d" in plan.note


def test_unknown_source_size_does_not_pad():
    plan = plan_for("2d", None, [("x", A_169)])
    assert plan.pad is False
    assert "分辨率" in plan.note


def test_empty_and_no_vote_inputs():
    assert plan_for("2d", (1280, 720), []).pad is False
    assert plan_for("2d", (1280, 720), [("a", None)]).pad is False
    assert aspect_of(None) is None
    assert aspect_of((0, 720)) is None
    assert aspect_of(("x", "y")) is None


def test_grouping_tolerates_small_drift():
    """同一比例录两遍会有末位差（1.778 vs 1.7778），必须归到同一组。"""
    groups = group_aspects([16 / 9, 1.7778, 1920 / 1080, A_43])
    assert [g["count"] for g in groups] == [3, 1]


def test_apply_is_identity_without_plan_padding():
    frame = np.zeros((90, 120, 3), dtype=np.uint8)
    plan = AspectPlan((120, 90), A_169, pad=False)
    assert plan.apply(frame) is frame
    assert plan.warns() == []
