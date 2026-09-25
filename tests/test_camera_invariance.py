"""机位不变性回归测试：锁定「多机位共用一套模板」这条能力。

纯几何仿真，不需要摄像头 / opencv / 真实视频：
造一个米制人体骨架，让同一套动作从三个不同机位投影，再比较
  - 2D 图像空间特征（旧版）：换机位结构性失配
  - 3D 机位无关特征（新版）：换机位几乎不增加距离

这里调用的是**生产代码里的真实实现**（PoseEngine.compute_features /
compute_features_3d、TemplateMatcher），不是另写一份对照实现，
所以这些断言能真正拦住回归。
"""
import math
import sys
import os

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.vision.pose_engine import LM, PoseEngine          # noqa: E402
from backend.vision.template_matcher import (TemplateMatcher,  # noqa: E402
                                             feature_dim, features_to_vector,
                                             SPACE_2D, SPACE_3D)

W, H = 1280, 720

# --------------------------------------------------------------------------
# 人体骨架（身体局部坐标系：原点 = 髋中心，y 向上，x 向人体左侧，z 向前，单位米）
# --------------------------------------------------------------------------
SKELETON = {
    "nose": (0.00, 0.70, 0.10),
    "left_shoulder": (0.18, 0.52, 0.00),
    "right_shoulder": (-0.18, 0.52, 0.00),
    "left_elbow": (0.24, 0.24, 0.01),
    "right_elbow": (-0.24, 0.24, 0.01),
    "left_wrist": (0.26, -0.02, 0.02),
    "right_wrist": (-0.26, -0.02, 0.02),
    "left_hip": (0.10, 0.00, 0.00),
    "right_hip": (-0.10, 0.00, 0.00),
    "left_knee": (0.10, -0.44, 0.00),
    "right_knee": (-0.10, -0.44, 0.00),
    "left_ankle": (0.10, -0.86, 0.00),
    "right_ankle": (-0.10, -0.86, 0.00),
}
BODY = dict(SKELETON)
BODY["mid_hip"] = (0.0, 0.0, 0.0)

CAMERAS = {
    "front": {"eye": (0.0, 1.45, -3.2), "target": (0.0, 0.95, 0.0), "fov": 65.0},
    "side": {"eye": (2.6, 1.05, -1.9), "target": (0.0, 0.95, 0.0), "fov": 65.0},
    "high": {"eye": (0.4, 2.75, -2.6), "target": (0.0, 0.45, 0.0), "fov": 65.0},
}
N_FRAMES = 24


def _rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)


def _rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


def _world_points(names, t):
    """生成 t 时刻的世界坐标（保留：供需要直接取点的场景使用）。"""
    return {k: np.array(v, dtype=float) for k, v in SKELETON.items()}


def _bend_world():
    """动作 1：站立弯腰够取物品（躯干前倾 0→85°，双臂前伸，膝略屈）。"""
    for i in range(N_FRAMES):
        t = i / (N_FRAMES - 1)
        sk = {k: np.array(v, dtype=float) for k, v in SKELETON.items()}
        bend = math.radians(85.0 * t)
        for k in list(sk):
            if sk[k][1] > 0.05:
                sk[k] = _rot_x(bend) @ sk[k]
        for k in ("left_elbow", "right_elbow", "left_wrist", "right_wrist"):
            side = "left" if "left" in k else "right"
            sh = sk[side + "_shoulder"]
            sk[k] = sh + _rot_x(math.radians(35.0 * t)) @ (sk[k] - sh)
        knee = math.radians(20.0 * t)
        for side in ("left", "right"):
            hip, kn, an = sk[side + "_hip"], sk[side + "_knee"], sk[side + "_ankle"]
            kn = hip + _rot_x(-knee) @ (kn - hip)
            an = kn + _rot_x(-knee) @ (an - kn)
            sk[side + "_knee"], sk[side + "_ankle"] = kn, an
        for k in sk:
            sk[k][1] += 0.86
        yield {**sk, "mid_hip": (sk["left_hip"] + sk["right_hip"]) / 2}


def _roll_world():
    """动作 2（无关对照）：床上协助翻身，绕身体长轴滚转 0→165°，双手收拢胸前。"""
    for i in range(N_FRAMES):
        t = i / (N_FRAMES - 1)
        sk = {k: np.array(v, dtype=float) for k, v in SKELETON.items()}
        chest = np.array([0.0, 0.42, 0.14])
        for k in ("left_wrist", "right_wrist", "left_elbow", "right_elbow"):
            s = 1.0 if "left" in k else -1.0
            sk[k] = sk[k] + (chest + np.array([0.10 * s, 0, 0]) - sk[k]) * (0.75 * t)
        for k in sk:
            sk[k] = _rot_z(math.radians(165.0 * t)) @ (_rot_x(math.radians(-90.0)) @ sk[k])
            sk[k][1] += 0.55
            sk[k][2] += 0.30
        yield {**sk, "mid_hip": (sk["left_hip"] + sk["right_hip"]) / 2}


MOTIONS = {"bend": _bend_world, "roll": _roll_world}


def _to_33(coords, mode):
    """把命名的关节放进 33 点数组（其余位置填 0），格式对齐 mediapipe。"""
    out = [(0.0, 0.0, 0.0)] * 33
    for name, idx in LM.items():
        p = coords[name]
        out[idx] = ((float(p[0]), float(p[1]), float(p[2])) if mode == "3d"
                    else (float(p[0]), float(p[1]), 1.0))
    return out


def _project(coords, cam):
    """世界坐标 -> 归一化图像坐标（针孔投影）。"""
    eye = np.array(cam["eye"], dtype=float)
    tgt = np.array(cam["target"], dtype=float)
    zc = eye - tgt
    zc /= np.linalg.norm(zc)
    xc = np.cross(np.array([0.0, 1.0, 0.0]), zc)
    xc /= np.linalg.norm(xc)
    yc = np.cross(zc, xc)
    rel = np.array([coords[n] for n in LM]) - eye
    pc = np.stack([rel @ xc, rel @ yc, rel @ zc], axis=1)
    depth = -pc[:, 2]
    f = (W / 2.0) / math.tan(math.radians(cam["fov"]) / 2.0)
    u = (W / 2.0 + f * pc[:, 0] / depth) / W
    v = (H / 2.0 - f * pc[:, 1] / depth) / H
    img = {n: (u[i], v[i]) for i, n in enumerate(LM)}
    return img


def _to_cam3d(coords, cam):
    """世界坐标 -> 相机坐标系米制点，并以髋中心为原点（模拟 pose_world_landmarks）。"""
    eye = np.array(cam["eye"], dtype=float)
    tgt = np.array(cam["target"], dtype=float)
    zc = eye - tgt
    zc /= np.linalg.norm(zc)
    xc = np.cross(np.array([0.0, 1.0, 0.0]), zc)
    xc /= np.linalg.norm(xc)
    yc = np.cross(zc, xc)
    rel = np.array([coords[n] for n in LM]) - eye
    pc = np.stack([rel @ xc, rel @ yc, rel @ zc], axis=1)
    hip = (coords["left_hip"] + coords["right_hip"]) / 2
    off = hip - eye
    origin = np.array([off @ xc, off @ yc, off @ zc])
    pc = pc - origin
    return {n: pc[i] for i, n in enumerate(LM)}


def _observation(coords, cam, mode, rng, noise):
    """按机位与特征空间产出一帧特征向量（调用生产代码）。"""
    if mode == SPACE_2D:
        img = _project(coords, cam)
        if noise:
            img = {k: (v[0] + rng.normal(0, noise / W),
                       v[1] + rng.normal(0, noise / H)) for k, v in img.items()}
        feats = PoseEngine.compute_features(_to_33(img, "2d"))
    else:
        cam_pts = _to_cam3d(coords, cam)
        if noise:
            cam_pts = {k: v + rng.normal(0, noise, 3) for k, v in cam_pts.items()}
        arr = [(0.0, 0.0, 0.0)] * 33
        for n in LM:
            p = cam_pts[n]
            arr[LM[n]] = (float(p[0]), float(p[1]), float(p[2]))
        feats = PoseEngine.compute_features_3d(arr)
    return features_to_vector(feats, mode)


def _sequence(motion, cam, mode, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    return np.array([_observation(c, cam, mode, rng, noise)
                     for c in MOTIONS[motion]()], dtype=np.float32)


def _dist2(a, b):
    from backend.vision.template_matcher import normalized_distance
    return normalized_distance(a, b)


# --------------------------------------------------------------------------
# 1) 特征维度与版本约定
# --------------------------------------------------------------------------


def test_feature_dims_are_stable():
    assert feature_dim(SPACE_2D) == 16
    assert feature_dim(SPACE_3D) == 18


def test_template_rejects_dimension_mismatch():
    """版本不符必须直接拒绝，绝不能拿错版本的模板去凑——那是误报来源。"""
    vec3d = [[0.0] * feature_dim(SPACE_3D)] * 5
    with pytest.raises(ValueError):
        TemplateMatcher(vec3d, space=SPACE_2D)
    vec2d = [[0.0] * feature_dim(SPACE_2D)] * 5
    with pytest.raises(ValueError):
        TemplateMatcher(vec2d, space=SPACE_3D)
    # 维度对得上则正常构建
    assert TemplateMatcher(vec3d, space=SPACE_3D).space == SPACE_3D


# --------------------------------------------------------------------------
# 2) 核心性质：3D 特征跨机位几乎零代价
# --------------------------------------------------------------------------


def test_3d_features_are_camera_invariant():
    """无噪声时，同一动作换机位应几乎不改变特征（这是「多机位共用模板」的根）。"""
    ref = _sequence("bend", CAMERAS["front"], SPACE_3D)
    for name in ("side", "high"):
        other = _sequence("bend", CAMERAS[name], SPACE_3D)
        d = _dist2(ref, other)
        assert d < 1e-6, f"3D 特征在 {name} 机位下不满足旋转不变性，距离={d}"


def test_3d_features_beat_2d_under_camera_change():
    """换机位时，3D 特征造成的损失必须显著低于 2D 特征。"""
    noise3d, noise2d = 0.012, 3.0
    loss = {}
    for mode, nz in ((SPACE_2D, noise2d), (SPACE_3D, noise3d)):
        base = _dist2(_sequence("bend", CAMERAS["front"], mode, nz, 1),
                      _sequence("bend", CAMERAS["front"], mode, nz, 2))
        cross = _dist2(_sequence("bend", CAMERAS["front"], mode, nz, 1),
                       _sequence("bend", CAMERAS["side"], mode, nz, 2))
        loss[mode] = cross - base
    assert loss[SPACE_3D] < loss[SPACE_2D] * 0.25, (
        f"3D 特征的机位损失应远小于 2D：3d={loss[SPACE_3D]:.2f} vs 2d={loss[SPACE_2D]:.2f}")


def test_2d_features_do_break_under_camera_change():
    """记录旧实现的结构性缺陷，避免有人误以为 2D 特征也能跨机位。"""
    nz = 3.0
    base = _dist2(_sequence("bend", CAMERAS["front"], SPACE_2D, nz, 1),
                  _sequence("bend", CAMERAS["front"], SPACE_2D, nz, 2))
    cross = _dist2(_sequence("bend", CAMERAS["front"], SPACE_2D, nz, 1),
                   _sequence("bend", CAMERAS["side"], SPACE_2D, nz, 2))
    assert cross > base * 1.5, (
        f"2D 特征本应在换机位后显著恶化（同机位 {base:.1f}，跨机位 {cross:.1f}）")


# --------------------------------------------------------------------------
# 3) 放宽机位不能以牺牲判别力为代价（宁可不报也别误报）
# --------------------------------------------------------------------------


def test_3d_features_keep_discrimination_across_cameras():
    """3D 特征下，无关动作的距离必须明显大于同一动作，且不随机位衰减。"""
    nz = 0.012
    tmpl = _sequence("bend", CAMERAS["front"], SPACE_3D, nz, 1)
    for name, cam in CAMERAS.items():
        same = _dist2(tmpl, _sequence("bend", cam, SPACE_3D, nz, 2))
        other = _dist2(tmpl, _sequence("roll", cam, SPACE_3D, nz, 3))
        ratio = other / max(same, 1e-6)
        assert ratio > 3.0, (
            f"{name} 机位下判别力不足：同动作 {same:.2f}，无关动作 {other:.2f}，比值 {ratio:.2f}")


def test_3d_threshold_leaves_margin():
    """默认 3D 阈值必须能容纳跨机位的正确动作，同时挡住无关动作。"""
    from backend.vision.template_matcher import DEFAULT_THRESHOLD
    nz = 0.012
    th = DEFAULT_THRESHOLD[SPACE_3D]
    tmpl = _sequence("bend", CAMERAS["front"], SPACE_3D, nz, 1)
    worst_same = max(_dist2(tmpl, _sequence("bend", c, SPACE_3D, nz, 2))
                     for c in CAMERAS.values())
    best_other = min(_dist2(tmpl, _sequence("roll", c, SPACE_3D, nz, 3))
                     for c in CAMERAS.values())
    assert worst_same < th, f"阈值 {th} 容不下跨机位的正确动作（最大 {worst_same:.2f}）"
    assert best_other > th, f"阈值 {th} 挡不住无关动作（最小 {best_other:.2f}）"


# --------------------------------------------------------------------------
# 4) 版本不符的模板必须被「跳过」而不是硬匹配
# --------------------------------------------------------------------------


def test_mismatched_template_is_skipped(monkeypatch):
    """FEATURE_SPACE=3d 时，2d 版本的历史模板应被跳过并给出提示，而不是照常匹配。

    注意模板必须**同时**声明 space 与 version 才被接受：同一空间内特征语义
    变更后（例如修正了某个角的参照轴），旧向量的含义已变，拿它匹配就是误报。
    """
    from backend.vision import monitor_session as ms
    from backend.vision.template_matcher import (feature_dim, FEATURE_VERSION,
                                                 SPACE_2D, SPACE_3D)

    old = [[1.0] * feature_dim(SPACE_2D)] * 6
    new = [[1.0] * feature_dim(SPACE_3D)] * 6
    actions = [
        {"id": "a_old", "name": "旧翻身模板", "template_type": "sequence",
         "template_data": {"vectors": old, "space": SPACE_2D,
                           "version": FEATURE_VERSION[SPACE_2D]}},
        {"id": "a_new", "name": "新翻身模板", "template_type": "sequence",
         "template_data": {"vectors": new, "space": SPACE_3D,
                           "version": FEATURE_VERSION[SPACE_3D]}},
        {"id": "a_rule", "name": "规则动作", "template_type": "rule",
         "template_data": {}},
    ]
    rule, seq = ms._build_matchers(actions, SPACE_3D)
    assert "a_old" not in seq, "2d 版本的历史模板不得参与 3d 匹配"
    assert "a_new" in seq
    assert [a["id"] for a in rule] == ["a_rule"]


def test_same_space_but_stale_version_is_skipped():
    """同空间但特征版本过期（语义已变）的模板必须被跳过，不能凑出假匹配。"""
    from backend.vision import monitor_session as ms
    from backend.vision.template_matcher import (feature_dim, FEATURE_VERSION,
                                                 SPACE_2D)

    vecs = [[1.0] * feature_dim(SPACE_2D)] * 6
    stale = FEATURE_VERSION[SPACE_2D] - 1
    actions = [
        {"id": "a_stale", "name": "旧版2d模板", "template_type": "sequence",
         "template_data": {"vectors": vecs, "space": SPACE_2D, "version": stale}},
        {"id": "a_cur", "name": "当前2d模板", "template_type": "sequence",
         "template_data": {"vectors": vecs, "space": SPACE_2D,
                           "version": FEATURE_VERSION[SPACE_2D]}},
        {"id": "a_nover", "name": "未声明版本", "template_type": "sequence",
         "template_data": {"vectors": vecs, "space": SPACE_2D}},
    ]
    _rule, seq = ms._build_matchers(actions, SPACE_2D)
    assert "a_stale" not in seq, "版本过期的模板不得参与匹配"
    assert "a_nover" not in seq, "未声明版本的旧模板不得参与匹配（宁可漏报）"
    assert "a_cur" in seq


def test_image_mode_template_is_skipped_under_video_runtime():
    """用 IMAGE 模式录的模板，在 VIDEO 模式运行时必须被跳过。

    理由：IMAGE 模式没有帧间跟踪，实测约 3.7% 的帧会瞬间失稳（躯干角 4°→170°+）。
    而录制会把几百帧下采样到几十帧，失稳向量有相当概率留在模板里，**永久拉偏 DTW**。
    这种模板的症状是「时好时坏且不可解释」，最难排查——宁可跳过并提示重录。
    """
    from backend.vision import monitor_session as ms
    from backend.vision.template_matcher import (feature_dim, FEATURE_VERSION,
                                                 SPACE_2D)

    vecs = [[1.0] * feature_dim(SPACE_2D)] * 6
    base = {"vectors": vecs, "space": SPACE_2D, "version": FEATURE_VERSION[SPACE_2D]}
    actions = [
        {"id": "a_img", "name": "IMAGE 模式录的", "template_type": "sequence",
         "template_data": dict(base, engine_mode="image")},
        {"id": "a_vid", "name": "VIDEO 模式录的", "template_type": "sequence",
         "template_data": dict(base, engine_mode="video")},
        {"id": "a_unmarked", "name": "未标记模式的老模板", "template_type": "sequence",
         "template_data": dict(base)},
    ]
    _rule, seq = ms._build_matchers(actions, SPACE_2D)

    assert "a_img" not in seq, "IMAGE 模式录的模板含失稳帧，不得参与匹配"
    assert "a_vid" in seq
    assert "a_unmarked" in seq, (
        "未声明 engine_mode 的旧模板不应因此被作废——"
        "该字段是本次才加入录制流程的，一律拒绝会把此前录制的正常模板全废掉"
    )


def test_image_template_stays_usable_under_image_runtime():
    """运行模式也是 IMAGE 时，IMAGE 模板不必被拒——此时两侧的噪声特性是一致的。"""
    from backend.vision.template_matcher import (feature_dim, FEATURE_VERSION,
                                                 SPACE_2D, template_compatible)

    td = {"vectors": [[1.0] * feature_dim(SPACE_2D)] * 6, "space": SPACE_2D,
          "version": FEATURE_VERSION[SPACE_2D], "engine_mode": "image"}
    ok, why = template_compatible(td, SPACE_2D, "image")
    assert ok, f"同为 IMAGE 模式时不应拒绝：{why}"


# --------------------------------------------------------------------------
# 「不依赖脚」的下蹲判据：只能靠「拍到大腿」
#
# 实测（本文件末尾的两个测试）：
#   - trunk_vs_leg_angle（大腿相对躯干）不需要踝，对下蹲响应 29°，跨机位漂移 0
#   - 但只有腰以上入画时（膝、踝都不可见），下蹲/坐下/转身与站立**完全不可区分**
#     —— 该视角下关于下蹲的信息量为零，不是算法缺陷
#
# 所以"用间接特征替代膝角"的前提是**拍到大腿**；同时 trunk_vs_leg_angle 与
# "躯干前倾"共线，浅蹲与轻弯腰会撞车，跨动作区分必须靠序列时间过程 + 真实素材标定。
# --------------------------------------------------------------------------
GROUND_Y = -0.86
_L_THIGH, _L_SHIN = 0.44, 0.42


def _legs(sk, hip_y):
    """按腿长不变的约束解出膝/踝（脚固定在地面，膝向前）。"""
    for side in ("left", "right"):
        hx = sk[side + "_hip"][0]
        hip = np.array([hx, hip_y, 0.0])
        ank = np.array([hx, GROUND_Y, 0.0])
        h = hip[1] - ank[1]
        cos_t = (_L_THIGH ** 2 + h ** 2 - _L_SHIN ** 2) / (2 * _L_THIGH * h)
        a = math.acos(max(-1.0, min(1.0, cos_t)))
        sk[side + "_knee"] = np.array(
            [hx, hip[1] - _L_THIGH * math.cos(a), _L_THIGH * math.sin(a)])
        sk[side + "_ankle"] = ank
    return sk


def _stand():
    return {k: np.array(v, dtype=float) for k, v in SKELETON.items()}


def _squat(depth):
    """下蹲：**上半身整体刚性下沉**（含肘、腕），脚固定在地面，膝向前。

    注意不能只下移 y>0 的关节：手腕在 y=-0.02 会被留在原地，于是肘角被拉直
    140°+ —— 那是个**造数器伪信号**（曾把 l_elbow_angle 从 172° 变成 26°）。
    躯干是刚体，上半身所有关节必须一起动。见本文件末尾的自检测试。
    """
    sk = {k: np.array(v, dtype=float) for k, v in SKELETON.items()}
    fixed = ("left_knee", "right_knee", "left_ankle", "right_ankle")
    for k in sk:
        if k not in fixed:
            sk[k][1] -= depth
    return _legs(sk, -depth)


def _sit():
    sk = {k: np.array(v, dtype=float) for k, v in SKELETON.items()}
    for k in sk:
        if sk[k][1] > -0.45:
            sk[k][1] -= 0.44
            sk[k][2] -= 0.30
    for side in ("left", "right"):
        hx = sk[side + "_hip"][0]
        sk[side + "_knee"] = np.array([hx, -0.54, 0.30])
        sk[side + "_ankle"] = np.array([hx, GROUND_Y, 0.10])
    return sk


def _turn():
    a = math.radians(90.0)
    c, s = math.cos(a), math.sin(a)
    R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=float)
    return {k: R @ np.array(v, dtype=float) for k, v in SKELETON.items()}


def _feats(coords, cam):
    """世界坐标 -> 三维特征（调生产代码 compute_features_3d）。"""
    cam_pts = _to_cam3d(coords, cam)
    arr = [(0.0, 0.0, 0.0)] * 33
    for n in LM:
        p = cam_pts[n]
        arr[LM[n]] = (float(p[0]), float(p[1]), float(p[2]))
    return PoseEngine.compute_features_3d(arr)


def _upper_only(coords):
    """模拟「只有腰以上入画」：膝、踝落回髋附近（不可见关节的位置不可信）。

    这是对 mediapipe 行为的**理想化**建模——它对画面外的关节不返回空值，
    只打低 visibility 并给一个无信息量的推测位置。"膝在髋正下方"正是
    "该位置不含下蹲信息"的最简模型（于是 trunk_vs_leg_angle 退化为
    "躯干相对竖直方向"）。
    """
    cc = {k: np.array(v, dtype=float) for k, v in coords.items()}
    for side in ("left", "right"):
        cc[side + "_knee"] = cc[side + "_hip"] + np.array([0.0, -0.01, 0.0])
        cc[side + "_ankle"] = cc[side + "_hip"] + np.array([0.0, -0.02, 0.0])
    return cc


def test_thigh_signal_for_squat_needs_no_ankle_and_is_camera_invariant():
    """下蹲有响应、且**不需要踝**、且跨机位一致 —— "间接特征"的可行性依据。"""
    f_stand = _feats(_stand(), CAMERAS["front"])
    f_deep = _feats(_squat(0.35), CAMERAS["front"])
    resp = f_stand["trunk_vs_leg_angle"] - f_deep["trunk_vs_leg_angle"]
    assert resp > 20.0, f"下蹲对大腿相对躯干的响应太小（{resp:.1f}°），不足以做判据"

    vals = [_feats(_squat(0.35), CAMERAS[k])["trunk_vs_leg_angle"]
            for k in CAMERAS]
    assert max(vals) - min(vals) < 1e-6, f"该判据竟然随机位变化：{vals}"


def test_squat_builder_is_rigid_above_the_hips():
    """造数器自检：下蹲时躯干是刚体，肘角/腕相对躯干的位置不该变。

    这条是为一个真实踩过的坑加的——最初只下移 y>0 的关节，手腕（y=-0.02）
    被留在原地，等于把手臂拉直，`l_elbow_angle` 从 172° 掉到 26°，
    在"下蹲信号"的对比表里凭空造出一个 146° 的巨大响应。
    自检先过，再谈业务结论（见 SKILL: 造数器必须先自检）。
    """
    a = _feats(_stand(), CAMERAS["front"])
    b = _feats(_squat(0.35), CAMERAS["front"])
    for k in ("l_elbow_angle", "r_elbow_angle", "l_shoulder_angle",
              "hands_distance", "l_wrist_to_hip", "l_wrist_height"):
        assert abs(a[k] - b[k]) < 1e-6, (
            f"下蹲不该改变 {k}（{a[k]:.3f} -> {b[k]:.3f}）：造数器把躯干拉变形了")


def test_waist_up_framing_carries_no_squat_information():
    """**决定性断言**：只拍腰以上时，下蹲/坐下/转身与站立完全不可区分。

    所以"用间接特征替代膝角"的前提是**拍到大腿**；
    只拍腰以上时无论换什么算法都测不出下蹲——这是观测能力问题，不是算法问题。
    """
    base = _feats(_upper_only(_stand()), CAMERAS["front"])
    for name, pose in (("下蹲", _squat(0.35)), ("坐下", _sit()), ("转身", _turn())):
        f = _feats(_upper_only(pose), CAMERAS["front"])
        diff = max(abs(f[k] - base[k]) for k in base)
        # 容差 1e-4：特征里的角度由 acos 在 ~100° 量级上算出，
        # float32 关键点转 float64 后必然带 ~1e-5 的舍入，取 1e-6 会假失败。
        assert diff < 1e-4, (
            f"{name} 在「只拍腰以上」的取景下竟然与站立有 {diff:.4f} 的差异 ——"
            f"若真有响应，这段注释与 ANKLE_DEPENDENT 的前提需要重写")


def test_thigh_signal_alone_cannot_separate_shallow_squat_from_mild_bend():
    """记录已知边界：trunk_vs_leg_angle 与「躯干前倾」共线，浅蹲与轻弯腰会撞车。

    实测（修正造数器之后）：站立 180°、浅蹲 0.15m 约 155°、躯干前倾 30° 约 150°
    —— 两者只差几度，而动作库里同时有「弯腰操作」与「屈膝下蹲」，
    表现就是"做弯腰被判成屈膝下蹲"。

    本条**故意断言"分不开"**，作为待办：跨动作区分要靠序列的时间过程（DTW）
    并用真实素材标定，不是加一个特征能解决的。深蹲（0.35m，约 128°）与
    大幅弯腰（85°，约 95°）是可分的，所以混淆只发生在"浅"与"轻"这一带。
    """
    def tvl(coords):
        return _feats(coords, CAMERAS["front"])["trunk_vs_leg_angle"]

    stand = tvl(_stand())
    shallow, deep = tvl(_squat(0.15)), tvl(_squat(0.35))

    sk = _stand()
    a = math.radians(30.0)
    c, s = math.cos(a), math.sin(a)
    for k in list(sk):
        if sk[k][1] > 0.05:
            sk[k] = np.array([[1, 0, 0], [0, c, -s], [0, s, c]],
                             dtype=float) @ sk[k]
    mild_bend = tvl(sk)

    assert stand > 175.0, f"站立应接近 180°，实测 {stand:.1f}°"
    assert deep < 135.0, f"深蹲应有明显响应，实测 {deep:.1f}°"
    assert abs(shallow - mild_bend) < 10.0, (
        f"浅蹲（{shallow:.1f}°）与轻弯腰（{mild_bend:.1f}°）本应撞车；"
        f"若已分开说明有更好的判据，请更新注释与 ANKLE_DEPENDENT 的说明")
