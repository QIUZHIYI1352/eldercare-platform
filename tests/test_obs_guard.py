"""观测可用性门控的回归测试。

这里的用例多数来自两个**真实缺陷**，不是假想的边界：

1. `space="3d"` 但拿不到 `pose_world_landmarks` 时，旧代码回落去喂二维特征字典，
   而两个空间的键名几乎不重叠 → `features_to_vector` 把 18 维**全部**填成 0.0
   （实测 18/18），经 Z-score 归一化后是个"看起来很正常的向量"：
   不报错、不命中、也不进任何日志。纯静默的漏报。
2. mediapipe 对**画面外**的关节**不返回空值**，只把 `visibility` 打低并推测一个位置，
   于是 `l_knee_angle` 照样算得出来，只是值是编造的。拿它去匹配就是误报。
"""
import numpy as np
import pytest

from backend.vision import obs_guard
from backend.vision.template_matcher import (ANKLE_DEPENDENT, TemplateMatcher,
                                            features_to_vector, missing_keys,
                                            normalized_distance, feature_order,
                                            validate_exclude, SPACE_2D, SPACE_3D)


# --------------------------------------------------------------------------
# 造数器
# --------------------------------------------------------------------------
def pts2d(**vis):
    """33 个二维关键点，(x, y, visibility)；vis 用关节名指定要改的可见度。"""
    out = [(0.5, 0.5, 1.0)] * 33
    out = [list(p) for p in out]
    for name, v in vis.items():
        idx = obs_guard._LM_INDEX[name]
        out[idx][2] = v
    return [tuple(p) for p in out]


def hidden(*names):
    """把给定关节设为"看不见"。"""
    return pts2d(**{n: 0.1 for n in names})


def knee_template(space=SPACE_3D, t=20):
    """只有「膝角」在变化的模板——即一个"屈膝下蹲"动作。

    其余维度保持恒定，因此它们的真实 std = 0，**不是**判别维度。
    这正是防"门控退化成全身必须看清"的关键构造。
    """
    order = feature_order(space)
    knee = "l_knee_angle" if space == SPACE_3D else "left_knee_angle"
    arr = np.zeros((t, len(order)), dtype=np.float32)
    arr[:, :] = 5.0                      # 所有维度取一个非零的恒定值
    arr[:, order.index(knee)] = np.linspace(175.0, 100.0, t)
    return arr, knee


def squat_template(space=SPACE_3D, t=20):
    """一个**真实**的下蹲模板：膝角与「大腿相对躯干」同时变化。

    对应"拍到了脚、按普通方式录"的情形。排除踝相关维度之后，
    膝角（需要踝）被剔除，剩下的 `trunk_vs_leg_angle`（只需肩/髋/膝）继续承担判据
    —— 这就是"用间接特征替代"的确切含义。
    """
    order = feature_order(space)
    arr = np.zeros((t, len(order)), dtype=np.float32)
    arr[:, :] = 5.0
    if space == SPACE_3D:
        arr[:, order.index("l_knee_angle")] = np.linspace(180.0, 95.0, t)
        arr[:, order.index("r_knee_angle")] = np.linspace(180.0, 95.0, t)
        arr[:, order.index("leg_extend")] = np.linspace(2.39, 1.90, t)
        arr[:, order.index("trunk_vs_leg_angle")] = np.linspace(180.0, 150.9, t)
    else:
        arr[:, order.index("left_knee_angle")] = np.linspace(180.0, 95.0, t)
        arr[:, order.index("right_knee_angle")] = np.linspace(180.0, 95.0, t)
        arr[:, order.index("body_height")] = np.linspace(2.39, 1.90, t)
        arr[:, order.index("trunk_inclination")] = np.linspace(0.0, 40.0, t)
    return arr


# --------------------------------------------------------------------------
# 缺陷 1：静默填零（space 与实际特征不匹配）
# --------------------------------------------------------------------------
def test_missing_keys_detects_wrong_space_features():
    """把二维特征喂给三维空间——18 个维度里 17 个键缺失，必须能检出来。

    只有 `hands_distance` 在两个空间同名，因此它是唯一"键存在"的那个维度，
    但它的值来自二维归一化坐标、与三维的米制距离**不是同一个量**——
    键存在反而比缺失更危险：它是唯一一个"不会被 missing_keys 发现"的错配维度。
    """
    from backend.vision.pose_engine import PoseEngine
    f2d = PoseEngine.compute_features([(0.5, 0.5, 0.9)] * 33)
    miss = missing_keys(f2d, SPACE_3D)
    assert len(miss) == 17
    assert "hands_distance" not in miss, "两个空间同名的维度应在键层面漏过检查"


def test_the_only_shared_key_between_spaces_is_hands_distance():
    """把"两个空间只有一个同名维度"这件事钉住。

    它解释了两件事：为什么错配时 missing_keys 是 17 而非 18；
    以及为什么**不能只靠 missing_keys**——那个同名维度要另想办法
    （用特征空间的版本戳拦住，见 template_compatible）。
    """
    assert set(feature_order(SPACE_2D)) & set(feature_order(SPACE_3D)) == {"hands_distance"}


def test_wrong_space_vector_is_silently_all_zero():
    """记录旧行为：这正是为什么调用方必须先查 missing_keys 再取向量。

    断言"全零"本身不是目的，而是把"补 0 会让缺数据变得不可分辨"钉成事实——
    调用方一旦忘了查，DTW 收到的就是一个既非报错也非命中的向量。
    """
    from backend.vision.pose_engine import PoseEngine
    f2d = PoseEngine.compute_features([(0.5, 0.5, 0.9)] * 33)
    vec = features_to_vector(f2d, SPACE_3D)
    assert len(vec) == 18
    assert np.allclose(vec, 0.0)


def test_missing_keys_is_empty_for_correct_space():
    from backend.vision.pose_engine import PoseEngine
    f2d = PoseEngine.compute_features([(0.5, 0.5, 0.9)] * 33)
    assert missing_keys(f2d, SPACE_2D) == []


# --------------------------------------------------------------------------
# 缺陷 2：画面外的关节被推测，visibility 没被用于判定
# --------------------------------------------------------------------------
def test_half_body_frame_makes_knee_action_unobservable():
    """半身入画（脚看不到）→ 依赖膝/踝的动作必须被判为不可观测。"""
    arr, _knee = knee_template()
    m = TemplateMatcher(arr, threshold=1e9, space=SPACE_3D)
    gate = obs_guard.ObsGate.from_matcher(m, "屈膝下蹲")
    ok, low = gate.check(hidden("left_ankle", "right_ankle"))
    assert ok is False
    assert "左脚" in low or "右脚" in low


def test_full_body_frame_is_observable():
    arr, _knee = knee_template()
    m = TemplateMatcher(arr, threshold=1e9, space=SPACE_3D)
    gate = obs_guard.ObsGate.from_matcher(m, "屈膝下蹲")
    assert gate.check(pts2d())[0] is True
    assert gate.check(pts2d())[1] == []


def test_gate_only_requires_what_the_action_actually_uses():
    """只在上肢有变化的动作，脚看不到也不该被拦——否则门控就成了"全身必须看清"。"""
    arr, _ = knee_template()
    order = feature_order(SPACE_3D)
    # 抹掉膝角的变化，改成只有手腕高度在变
    arr[:, order.index("l_knee_angle")] = 5.0
    arr[:, order.index("l_wrist_height")] = np.linspace(-0.5, 0.8, len(arr))
    m = TemplateMatcher(arr, threshold=1e9, space=SPACE_3D)
    gate = obs_guard.ObsGate.from_matcher(m, "双臂抬起")
    assert "l_knee_angle" not in gate.discriminative
    assert gate.check(hidden("left_ankle", "right_ankle"))[0] is True


def test_constant_dimension_is_not_discriminative():
    """恒定维度（真实 std=0）不得进入判别集。

    归一化用的 std 会把零方差维度改成 1.0，若误用它，这个"恒定"维度会被判成
    "变化很大" → 判别集退化为全部维度 → 门控要求全身每一处都看清。
    这是本模块最容易写错的一处。
    """
    arr, knee = knee_template()
    m = TemplateMatcher(arr, threshold=1e9, space=SPACE_3D)
    gate = obs_guard.ObsGate.from_matcher(m, "屈膝下蹲")
    assert gate.discriminative == [knee]


def test_template_without_variation_does_not_block():
    """整段动作几乎没动的模板没有判据可言，不拦（拦了它也不会有任何效果）。"""
    order = feature_order(SPACE_3D)
    arr = np.full((10, len(order)), 5.0, dtype=np.float32)
    m = TemplateMatcher(arr, threshold=1e9, space=SPACE_3D)
    gate = obs_guard.ObsGate.from_matcher(m, "静止")
    assert gate.discriminative == []
    assert gate.check(hidden("left_ankle"))[0] is True
    assert "无判别维度" in gate.describe()


def test_gate_is_load_bearing_not_decorative():
    """门控必须是"承重"的：这一帧本来会被判成命中，是门控把它拦下来的。

    只断言 `check()` 返回 False 不足以说明问题——还要证明**没有门控就会命中**，
    否则这个门控可能只是装饰。所以这里同时断言 DTW 距离确实在阈值以内。
    """
    arr, knee = knee_template()
    m = TemplateMatcher(arr, threshold=40.0, space=SPACE_3D)
    # 运行时窗口：膝角轨迹与模板一致（会被判成匹配），但脚在画面外
    window = np.full_like(arr, 5.0)
    window[:, feature_order(SPACE_3D).index(knee)] = arr[:, feature_order(SPACE_3D).index(knee)]
    dist = normalized_distance(m._normalize(window), m.template)
    assert dist <= m.threshold, f"前置条件不成立：这一帧本该命中（距离 {dist:.2f}）"

    gate = obs_guard.ObsGate.from_matcher(m, "屈膝下蹲")
    ok, low = gate.check(hidden("left_ankle", "right_ankle"))
    assert ok is False, "门控没有拦住这一帧，误报就会发生"
    assert low


# --------------------------------------------------------------------------
# 造数器自检 + 基础行为
# --------------------------------------------------------------------------
def test_visibility_map_reads_the_third_component():
    vis = obs_guard.visibility_map(pts2d(left_knee=0.2, nose=0.9))
    assert vis["left_knee"] == pytest.approx(0.2)
    assert vis["nose"] == pytest.approx(0.9)
    assert vis["right_knee"] == pytest.approx(1.0)


def test_visibility_map_tolerates_bad_input():
    assert obs_guard.visibility_map(None) == {}
    assert obs_guard.visibility_map([]) == {}
    # 关键点不足 33 个时只取存在的部分，不抛异常
    vis = obs_guard.visibility_map([(0.5, 0.5, 1.0)] * 12)
    assert "nose" in vis and "left_knee" not in vis


def test_missing_visibility_field_is_treated_as_visible():
    """旧数据没有 visibility 时按"可见"处理，不因此误杀。

    与 template_compatible 处理旧模板字段的先例一致：不能因为新加的一项判定
    就把此前能用的输入一并作废。真正看不到的情况 mediapipe 会给低分。
    """
    assert obs_guard.lowest_visibility(("left_ankle",), {}) == pytest.approx(1.0)
    assert obs_guard.lowest_visibility((), {}) is None
    gate = obs_guard.ObsGate("x", ["l_knee_angle"],
                             ["left_hip", "left_knee", "left_ankle"])
    assert gate.check([(0.5, 0.5)] * 33)[0] is True


def test_lowest_visibility_takes_the_minimum():
    v = {"a": 0.9, "b": 0.3, "c": 0.7}
    assert obs_guard.lowest_visibility(("a", "b", "c"), v) == pytest.approx(0.3)


def test_vis_limit_is_respected():
    """"看不清"的判定必须跟着阈值走，而不是硬编码一个数。"""
    gate = obs_guard.ObsGate("x", ["l_knee_angle"],
                             ["left_ankle"], vis_limit=0.5)
    assert gate.check(hidden("left_ankle"))[0] is False          # 0.1 < 0.5
    gate_lax = obs_guard.ObsGate("x", ["l_knee_angle"],
                                 ["left_ankle"], vis_limit=0.05)
    assert gate_lax.check(hidden("left_ankle"))[0] is True       # 0.1 > 0.05


# --------------------------------------------------------------------------
# 判别阈值的分类
# --------------------------------------------------------------------------
@pytest.mark.parametrize("std,expect_disc", [
    (0.0, False), (7.9, False), (8.1, True), (30.0, True)])
def test_angle_discriminative_threshold(std, expect_disc):
    order = feature_order(SPACE_3D)
    std_raw = np.zeros(len(order), dtype=np.float32)
    std_raw[order.index("l_knee_angle")] = std
    names = obs_guard.discriminative_names(std_raw, SPACE_3D)
    assert ("l_knee_angle" in names) is expect_disc


@pytest.mark.parametrize("std,expect_disc", [
    (0.0, False), (0.07, False), (0.09, True), (0.5, True)])
def test_ratio_discriminative_threshold(std, expect_disc):
    order = feature_order(SPACE_3D)
    std_raw = np.zeros(len(order), dtype=np.float32)
    std_raw[order.index("hands_distance")] = std
    names = obs_guard.discriminative_names(std_raw, SPACE_3D)
    assert ("hands_distance" in names) is expect_disc


def test_unknown_feature_name_has_no_deps():
    assert obs_guard.LANDMARK_DEPS.get("不存在的特征") is None
    assert obs_guard.feature_visibility("不存在的特征", {}) is None


def test_every_vector_dimension_declares_its_landmarks():
    """两个空间的每个维度都必须声明依赖关节，否则门控对它形同虚设。

    这条是元测试：新加特征时若忘了登记依赖，门控会静默地不保护它——
    而"静默不生效"正是本模块存在的理由，不能自己犯。
    """
    for space in (SPACE_2D, SPACE_3D):
        for name in feature_order(space):
            assert name in obs_guard.LANDMARK_DEPS, f"{space} 的 {name} 未声明依赖关节"
            assert obs_guard.LANDMARK_DEPS[name], f"{name} 的依赖关节为空"


def test_deps_reference_real_landmark_names():
    valid = set(obs_guard._LM_INDEX)
    for feat, deps in obs_guard.LANDMARK_DEPS.items():
        for d in deps:
            assert d in valid, f"{feat} 引用了不存在的关节 {d}"


# --------------------------------------------------------------------------
# 提示与统计（排查"为什么识别不到"的入口）
# --------------------------------------------------------------------------
def test_describe_names_the_required_body_parts_in_chinese():
    arr, _ = knee_template()
    m = TemplateMatcher(arr, threshold=1e9, space=SPACE_3D)
    txt = obs_guard.ObsGate.from_matcher(m, "屈膝下蹲").describe()
    assert "屈膝下蹲" in txt
    assert "左脚" in txt or "右脚" in txt


def test_visibility_report_flags_what_is_not_visible():
    txt = obs_guard.visibility_report(hidden("left_ankle", "right_ankle"))
    assert "看不清" in txt
    assert "左脚" in txt or "右脚" in txt


def test_visibility_report_says_ok_when_everything_is_visible():
    assert "观测良好" in obs_guard.visibility_report(pts2d())
    assert "无可观测关键点" in obs_guard.visibility_report([])


def test_build_gates_covers_every_matcher():
    arr, _ = knee_template()
    ms = {"a1": TemplateMatcher(arr, threshold=1e9, space=SPACE_3D)}
    gates = obs_guard.build_gates(ms, {"a1": "屈膝下蹲"})
    assert set(gates) == {"a1"}
    assert gates["a1"].name == "屈膝下蹲"
    assert obs_guard.build_gates({}) == {}
    assert obs_guard.build_gates(None) == {}


# --------------------------------------------------------------------------
# 「间接特征」：模板声明自己不用哪些维度（脚可以不在画面里）
#
# 几何仿真实测：人在画面里只有腰以上时，站立/下蹲/坐下/转身的三维特征
# **完全相同** —— 该视角下关于下蹲的信息量为零。但只要**拍到大腿（膝可见）**，
# trunk_vs_leg_angle（大腿相对躯干，不需要踝）对下蹲的响应就有 29°，
# 且跨机位漂移 0.000°。所以"不依赖脚"的做法是：模板排除踝相关维度。
# --------------------------------------------------------------------------
def test_validate_exclude_rejects_typo():
    """排除清单里的名字写错必须报错。

    静默忽略会让那个维度继续参与匹配——正是本机制要消除的静默失效。
    """
    with pytest.raises(ValueError, match="不在"):
        validate_exclude(["l_kne_angle"], SPACE_3D)      # 拼错
    with pytest.raises(ValueError):
        validate_exclude(["trunk_inclination"], SPACE_3D)  # 那是二维的名字


def test_validate_exclude_normalizes():
    assert validate_exclude([], SPACE_3D) == ()
    assert validate_exclude(None, SPACE_3D) == ()
    assert validate_exclude(["ankle_distance", "ankle_distance"], SPACE_3D) == ("ankle_distance",)
    assert validate_exclude([" ankle_distance "], SPACE_3D) == ("ankle_distance",)
    assert validate_exclude(["", "  "], SPACE_3D) == ()


def test_excluded_dimensions_are_zeroed_in_the_vector():
    feats = {k: 3.0 for k in feature_order(SPACE_3D)}
    vec = features_to_vector(feats, SPACE_3D, exclude=["ankle_distance", "leg_extend"])
    order = feature_order(SPACE_3D)
    assert vec[order.index("ankle_distance")] == 0.0
    assert vec[order.index("leg_extend")] == 0.0
    assert vec[order.index("trunk_vs_leg_angle")] == pytest.approx(3.0)


def test_excluded_dims_have_zero_std_so_they_are_not_discriminative():
    arr, knee = knee_template()
    order = feature_order(SPACE_3D)
    arr[:, order.index("leg_extend")] = 99.0      # 即使模板里有极值
    m = TemplateMatcher(arr, threshold=1e9, space=SPACE_3D,
                        exclude=["leg_extend"])
    assert m._std_raw[order.index("leg_extend")] == 0.0
    assert "leg_extend" not in obs_guard.discriminative_names(m._std_raw, SPACE_3D)


def test_no_ankle_template_does_not_require_the_feet():
    """**核心断言**：排除了踝相关维度后，脚不在画面里也能判定。

    这正是"用间接特征替代"要买到的能力——同一帧，不排除时被拦、排除后放行。
    """
    arr = squat_template()
    hidden_feet = hidden("left_ankle", "right_ankle")

    strict = TemplateMatcher(arr, threshold=1e9, space=SPACE_3D)
    gate_strict = obs_guard.ObsGate.from_matcher(strict, "屈膝下蹲")
    obs_ok, low = gate_strict.check(hidden_feet)
    assert obs_ok is False, "前置条件：这一帧本该被判为看不全"
    assert "左脚" in low or "右脚" in low

    relaxed = TemplateMatcher(arr, threshold=1e9, space=SPACE_3D,
                              exclude=ANKLE_DEPENDENT[SPACE_3D])
    gate_relaxed = obs_guard.ObsGate.from_matcher(relaxed, "屈膝下蹲")
    assert gate_relaxed.required, "排除踝之后仍应要求大腿入画"
    assert "left_ankle" not in gate_relaxed.required
    assert "right_ankle" not in gate_relaxed.required
    assert gate_relaxed.check(hidden_feet)[0] is True, "排除踝相关维度后不该再要求脚"


def test_no_ankle_template_still_uses_the_thigh_signal():
    """排除踝 ≠ 什么都不看：大腿相对躯干的信号必须还在判别集里。

    否则这个模板会退化成"无判别维度"，门控不拦但也不可能有意义。
    """
    arr = squat_template()
    m = TemplateMatcher(arr, threshold=1e9, space=SPACE_3D,
                        exclude=ANKLE_DEPENDENT[SPACE_3D])
    discs = obs_guard.discriminative_names(m._std_raw, SPACE_3D)
    assert "trunk_vs_leg_angle" in discs, "大腿相对躯干必须留作判据"
    for gone in ANKLE_DEPENDENT[SPACE_3D]:
        assert gone not in discs
    gate = obs_guard.ObsGate.from_matcher(m, "屈膝下蹲")
    assert "left_knee" in gate.required
    assert "left_hip" in gate.required


def test_ankle_dependent_list_matches_the_dependency_map():
    """元测试：把"哪些维度依赖踝"与依赖关系表钉在一起。

    以后有人加了一个用踝的特征却忘了登记进 ANKLE_DEPENDENT，
    `--no-ankle` 就会漏掉它、脚不在画面时那维度仍是编造值 —— 静默失效。
    """
    for space in (SPACE_2D, SPACE_3D):
        declared = set(ANKLE_DEPENDENT[space])
        actual = {name for name in feature_order(space)
                  if any("ankle" in d for d in obs_guard.LANDMARK_DEPS.get(name, ()))}
        assert actual == declared, (
            f"{space} 空间依赖踝的维度应恰好是 {sorted(actual)}，"
            f"但 ANKLE_DEPENDENT 声明为 {sorted(declared)}")


def test_ankle_dependent_names_all_exist_in_their_space():
    for space, names in ANKLE_DEPENDENT.items():
        order = set(feature_order(space))
        for n in names:
            assert n in order, f"{space} 空间没有 {n} 这个维度"
