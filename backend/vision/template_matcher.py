"""骨骼序列模板匹配：DTW 动态时间规整，实现更精细的动作匹配。

与「角度规则」(rule) 不同，序列模板记录一段动作的关节特征时间序列，
通过 DTW 度量实时滑窗与模板的相似度，可识别「翻身、拍背、环抱转移」等
规则难以表达的过程性动作。
"""
import math

import numpy as np

# 参与匹配的特征向量（固定顺序，共 16 维，由 PoseEngine.compute_features 提供）
FEATURE_ORDER = [
    "trunk_inclination", "trunk_lateral_lean",
    "left_elbow_angle", "right_elbow_angle",
    "left_shoulder_angle", "right_shoulder_angle",
    "left_knee_angle", "right_knee_angle",
    "left_hip_angle", "right_hip_angle",
    "hand_height_left", "hand_height_right",
    "hands_distance", "body_height", "hip_center_height",
    "head_height",
]


def features_to_vector(features):
    """特征 dict -> 定长 numpy 向量（缺失字段补 0）。"""
    return np.array([float(features.get(k, 0.0) or 0.0) for k in FEATURE_ORDER],
                    dtype=np.float32)


def normalize_vectors(vectors):
    """对每个特征维度做 Z-score 归一化（按模板自身统计），提升尺度一致性。"""
    arr = np.array(vectors, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] == 0:
        return arr
    mean = arr.mean(axis=0)
    std = arr.std(axis=0)
    std[std < 1e-6] = 1.0
    return (arr - mean) / std


def dtw_distance(s1, s2, window=None):
    """计算两条序列 (T, D) 的动态时间规整距离。

    window: 约束搜索带宽（默认 = 序列长度差 + 总长的 1/3），降低误配。
    """
    s1 = np.asarray(s1, dtype=np.float32)
    s2 = np.asarray(s2, dtype=np.float32)
    n, m = len(s1), len(s2)
    if n == 0 or m == 0:
        return float("inf")
    if window is None:
        window = abs(n - m) + max(1, (n + m) // 3)
    window = max(window, abs(n - m))

    d = np.full((n + 1, m + 1), np.inf, dtype=np.float32)
    d[0, 0] = 0.0
    for i in range(1, n + 1):
        j_start = max(1, i - window)
        j_end = min(m, i + window)
        for j in range(j_start, j_end + 1):
            cost = np.linalg.norm(s1[i - 1] - s2[j - 1])
            d[i, j] = cost + min(d[i - 1, j], d[i, j - 1], d[i - 1, j - 1])
    return float(d[n, m])


def normalized_distance(s1, s2, window=None):
    """归一化 DTW 距离（除以模板长度），跨模板可比。"""
    dist = dtw_distance(s1, s2, window)
    n = len(s1)
    return dist / n if n else float("inf")


class TemplateMatcher:
    """对单条模板做实时滑窗匹配，输出归一化距离与是否命中。"""

    def __init__(self, template_vectors, threshold=8.0, min_ratio=0.6):
        """
        template_vectors: (T, D) 模板序列
        threshold:        归一化 DTW 距离阈值（越小越严格，默认 8.0）
        min_ratio:        滑窗至少覆盖模板长度比例才判定
        """
        self.template = normalize_vectors(template_vectors)
        self.template_len = len(self.template)
        self.threshold = threshold
        self.min_ratio = min_ratio
        self.buffer = []
        self.max_len = max(self.template_len * 3, 30)
        self._last_hit = 0.0

    def reset(self):
        self.buffer = []
        self._last_hit = 0.0

    def update(self, feature_vector, now_ts=None, cooldown=2.0):
        """输入一帧特征向量，返回 (距离, 是否命中)。

        命中后进入 cooldown 秒冷却，避免同一动作重复触发。
        """
        import time
        now_ts = now_ts if now_ts is not None else time.time()
        self.buffer.append(np.asarray(feature_vector, dtype=np.float32))
        if len(self.buffer) > self.max_len:
            self.buffer = self.buffer[-self.max_len:]

        need = int(self.template_len * self.min_ratio)
        if self.template_len == 0 or len(self.buffer) < need:
            return float("inf"), False

        recent = np.array(self.buffer[-self.template_len:], dtype=np.float32)
        dist = normalized_distance(recent, self.template)
        hit = dist <= self.threshold and (now_ts - self._last_hit) >= cooldown
        if hit:
            self._last_hit = now_ts
        return dist, hit


def down_sample(vectors, target_frames=30):
    """将不定长序列下采样到固定帧数（线性插值），保证模板长度一致。"""
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim != 2 or len(vectors) == 0:
        return vectors
    if len(vectors) <= target_frames:
        return vectors
    idx = np.linspace(0, len(vectors) - 1, target_frames)
    out = []
    for i in idx:
        lo, hi = int(np.floor(i)), int(np.ceil(i))
        w = i - lo
        out.append(vectors[lo] * (1 - w) + vectors[hi] * w)
    return np.array(out, dtype=np.float32)
