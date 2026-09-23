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
