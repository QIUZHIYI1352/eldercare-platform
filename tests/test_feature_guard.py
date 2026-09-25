"""失稳帧门控的行为回归：真实快速动作不能被杀，坏帧不能漏放。

护栏的重点是**两侧都要**：
- 只测"坏帧被挡住"是不够的——把阈值调到 0 也能通过，但那样真实动作全被丢掉（漏报）。
- 所以每条"该挡"的用例旁边都有一条"不该挡"的对应用例。

本文件里的夹具数值都来自**真实视频的实测**（526 帧，见 feature_guard 的模块注释），
包括那段被误杀过的平滑加速序列——它必须留在用例里，防止哪天又被"优化"回去。
"""
import pytest

from backend.vision.feature_guard import (RESYNC_AFTER, FeatureGuard,
                                          is_angle_feature, is_monitored)


def feats(trunk=4.0, extra=None):
    f = {"trunk_inclination": trunk, "left_knee_angle": 170.0,
         "body_height": 8.0, "visibility": 0.9}
    if extra:
        f.update(extra)
    return f


def feed(guard, values, dt=1 / 25, t0=1000.0, key="trunk_inclination"):
    """依次喂入一系列值，返回 [(ok, reason), ...]。"""
    return [guard.check(feats(extra={key: v}), t0 + i * dt)[:2]
            for i, v in enumerate(values)]


# ------------------------------------------------- 该挡的：坏帧

def test_single_frame_spike_is_gated():
    """实测签名：躯干角一帧内从 4° 跳到 170°（人不可能在 40ms 里转 166°）。"""
    g = FeatureGuard(fps=25)
    res = feed(g, [4.0] * 6 + [172.0])
    assert all(ok for ok, _r in res[:-1]), "平稳帧不该被挡"
    ok, reason = res[-1]
    assert ok is False
    assert "trunk_inclination" in reason
    assert g.gated == 1


def test_real_glitch_episode_from_video():
    """真实视频第 33~34 帧的左肘角崩坏（导画面比对过：姿势没变，是测量跳了）。

    序列 171.4 → 154.7 → 35.0；中间那一步是平滑的，最后一步是崩坏。
    """
    g = FeatureGuard(fps=25)
    res = feed(g, [171.4, 171.4, 171.4, 154.7, 35.0],
               key="left_elbow_angle")
    assert res[3][0] is True, "平滑那一步不该被挡"
    assert res[4][0] is False, "崩坏那一步必须挡住"


def test_real_glitch_episode_body_height_from_video():
    """真实视频第 119~123 帧的 body_height 崩坏：11.67→44.19→48.31。

    比例型特征的真实动作幅度很小（每帧 0.4~1.8），崩坏是 20~40，
    所以它比角度还好辨认。
    """
    g = FeatureGuard(fps=25)
    res = feed(g, [11.67, 11.67, 11.67, 44.19], key="body_height")
    assert res[-1][0] is False, "body_height 一帧翻四倍必须挡住"


def test_glitch_is_gated_even_at_low_frame_rate():
    """低帧率是"速率"视角的盲区：dt 变大会把速率上限放大。

    166° 的单帧跳变在 fps=10 时对应 1660°/s，仍应被判为不连续。
    """
    g = FeatureGuard(fps=10)
    res = feed(g, [4.0] * 6 + [170.0], dt=0.1)
    assert res[-1][0] is False, "低帧率下漏放了突跳"


def test_injected_glitch_has_no_collateral():
    """注入一帧坏帧，代价必须恰好是 1 帧——不能牵连前后正常帧。

    这是"被拦帧不进预测基准 + 重同步"两条设计的共同结果。
    """
    g = FeatureGuard(fps=25)
    res = feed(g, [4.0] * 6 + [172.0] + [4.0] * 3)
    dropped = [i for i, (ok, _r) in enumerate(res) if not ok]
    assert dropped == [6], f"坏帧牵连了无关帧：{dropped}"


# --------------------------------------------- 不该挡的：真实的快速动作

def test_smooth_acceleration_is_not_gated():
    """真实视频第 42~45 帧：挥壶铃的平滑加速 `18.9→65.2→96.1→128.5→149.0`。

    这一串曾被"与中位数偏离"的判据误杀（它偏离的是运动开始前的中位数）。
    留在用例里防止回退。
    """
    g = FeatureGuard(fps=25)
    res = feed(g, [18.9, 18.9, 18.9, 65.2, 96.1, 128.5, 149.0],
               key="left_elbow_angle")
    bad = [i for i, (ok, _r) in enumerate(res) if not ok]
    assert not bad, f"平滑加速被误杀：{bad}"


def test_fast_but_smooth_motion_is_not_gated():
    """1 秒内躯干从 4° 转到 94° 的真实动作，一帧都不该被挡。"""
    g = FeatureGuard(fps=25)
    res = feed(g, [4.0 + 90.0 * i / 25 for i in range(26)])
    bad = [i for i, (ok, _r) in enumerate(res) if not ok]
    assert not bad, f"真实快速动作被误杀：{bad[:3]}"


def test_slow_drift_is_not_gated():
    """长时间缓慢漂移（人坐下/起身、走近镜头）不该被挡。"""
    g = FeatureGuard(fps=25)
    assert all(ok for ok, _r in feed(g, [4.0 + 0.8 * i for i in range(120)]))


def test_ratio_drift_from_walking_closer_is_not_gated():
    """人走向镜头时 body_height 会持续变化，但变化是平滑的，不该被挡。"""
    g = FeatureGuard(fps=25)
    res = feed(g, [8.0 + 0.6 * i for i in range(40)], key="body_height")
    bad = [i for i, (ok, _r) in enumerate(res) if not ok]
    assert not bad, f"走向镜头被误判成坏帧：{bad[:3]}"


def test_warmup_frames_are_not_gated():
    """开机头一帧没有历史可比，不能判异常。"""
    g = FeatureGuard(fps=25)
    assert g.check(feats(trunk=140.0), 1000.0)[0] is True


# ------------------------------------------------- 卡死比误判更糟

def test_sustained_new_level_recovers_within_bounded_frames():
    """镜头被挪动/人换了站位：特征整体跳档，门控必须在有限帧内恢复。

    「永远挡住所有帧」是最坏的结果——识别彻底失效且不报错。
    代价被 RESYNC_AFTER 钉住，不会无限增长。
    """
    g = FeatureGuard(fps=25)
    feed(g, [4.0] * 6)
    res = feed(g, [170.0] * 40, t0=2000.0)
    accepted = [i for i, (ok, _r) in enumerate(res) if ok]
    assert accepted, "换了站位后门控永久卡死"
    assert accepted[0] < RESYNC_AFTER + 2, (
        f"用了 {accepted[0] + 1} 帧才恢复，超出上限 {RESYNC_AFTER}")
    assert g.resyncs >= 1


def test_alternating_impossible_motion_triggers_resync():
    """每帧都在做"物理上不可能"的动作（4°↔204°）时必须有出口。"""
    g = FeatureGuard(fps=25)
    feed(g, [4.0] * 4)
    res = feed(g, [204.0, 4.0] * 20, t0=3000.0)
    assert any(ok for ok, _r in res), "持续不可能动作时门控永久卡死"
    assert g.resyncs >= 1
    assert any("重置" in r for ok, r in res if ok)


# ------------------------------------------------------------ 统计与外显

def test_gated_count_is_visible():
    """丢帧必须可见，不能静默。"""
    g = FeatureGuard(fps=25)
    assert g.summary() == "" and g.first_bad is None
    feed(g, [4.0] * 6 + [172.0])
    assert g.gated == 1
    assert "1 帧" in g.summary()
    assert g.first_bad and "trunk_inclination" in g.first_bad


def test_reset_clears_state():
    g = FeatureGuard(fps=25)
    feed(g, [4.0] * 6)
    g.reset()
    assert g.check(feats(trunk=170.0), 1100.0)[0] is True


# ------------------------------------------------------------------ 杂项

@pytest.mark.parametrize("name,expect", [
    ("trunk_inclination", True),
    ("left_knee_angle", True),
    ("l_elbow_angle", True),          # 3d 空间的命名
    ("trunk_vs_leg_angle", True),
    ("hands_distance", False),
    ("l_wrist_to_hip", False),
])
def test_angle_feature_classification(name, expect):
    assert is_angle_feature(name) == expect


def test_visibility_is_not_monitored():
    """可见度本身会剧烈波动，拿它当坏帧判据只会天天误拦。"""
    assert is_monitored("visibility") is False
    g = FeatureGuard(fps=25)
    for i in range(8):
        g.check(feats(extra={"visibility": 0.9}), 1000 + i / 25)
    assert g.check(feats(extra={"visibility": 0.05}), 1000 + 8 / 25)[0] is True


def test_empty_features_are_rejected_not_crashed():
    g = FeatureGuard(fps=25)
    ok, reason, bad = g.check({}, 1000.0)
    assert ok is False and bad == []


def test_limits_are_dt_scaled():
    """限值必须随 dt 放宽，否则低帧率下会把真实动作误杀。"""
    slow = FeatureGuard._limits("trunk_inclination", 0.4)   # 2.5fps
    fast = FeatureGuard._limits("trunk_inclination", 0.04)  # 25fps
    assert slow[0] > fast[0] and slow[1] > fast[1]


# ------------------------------------------------- 修复（替代）而不是丢帧

def test_repair_replaces_only_the_bad_features():
    g = FeatureGuard(fps=25)
    feed(g, [4.0] * 6)
    frame = dict(feats(trunk=172.0), left_knee_angle=999.0)
    ok, _reason, bad = g.check(frame, 1000 + 6 / 25)
    assert ok is False
    assert set(bad) == {"trunk_inclination", "left_knee_angle"}
    fixed = g.repair(frame, bad)
    assert fixed["trunk_inclination"] != 172.0, "坏值没有被替换"
    assert fixed["left_knee_angle"] != 999.0, "坏值没有被替换"
    assert fixed["body_height"] == frame["body_height"], "不该动没被判为坏的特征"
    assert frame["trunk_inclination"] == 172.0, "不能改动原字典"


def test_repaired_frame_is_always_safe_to_feed_downstream():
    """修复后必须"键齐全、类型一致"，下游才能无条件继续用这一帧。

    这是「不丢帧」的前提，也是为什么序列匹配可以每帧都喂：
    只要修复结果形状不变，DTW 的滑窗就永远是连续、等间隔的采样
    （实测丢帧会让距离 5.3→16.6，把命中变成未命中，见模块注释）。
    """
    g = FeatureGuard(fps=25)
    feed(g, [4.0] * 6)
    frame = dict(feats(trunk=172.0), left_knee_angle=999.0)
    _ok, _r, bad = g.check(frame, 1000 + 6 / 25)
    fixed = g.repair(frame, bad)
    assert set(fixed) == set(frame), "修复后键集变了，下游拿不到需要字段"
    for k, v in fixed.items():
        assert isinstance(v, (int, float)) and v == v, f"{k}={v!r} 不是有效数值"
    from backend.vision.template_matcher import features_to_vector
    assert len(features_to_vector(fixed, "2d")) == len(features_to_vector(frame, "2d"))


