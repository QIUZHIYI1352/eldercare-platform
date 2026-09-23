"""DTW 向量化改造的等价性回归测试。

`dtw_distance` 从「逐格调用 np.linalg.norm」改为「代价矩阵一次广播 + Python 标量 DP」，
这是纯性能改造，**数值语义必须完全不变**。本文件用一个直白的朴素参考实现，
在多种尺寸、多种带宽下逐例比对。

为什么必须有这个测试：DTW 的 DP 递推里含 `d[i, j-1]`（同行依赖），
改成滚动数组时极易写错下标或漏掉边界；而这类错误的表现是
「距离略有偏差」→ 匹配阈值悄悄失准 → 误报/漏报，很难在端到端里发现。
"""
import numpy as np
import pytest

from backend.vision.template_matcher import dtw_distance, normalized_distance


def dtw_reference(s1, s2, window=None):
    """朴素参考实现：直接照定义写，可读性优先，不看性能。"""
    s1 = np.asarray(s1, dtype=np.float64)
    s2 = np.asarray(s2, dtype=np.float64)
    n, m = len(s1), len(s2)
    if n == 0 or m == 0:
        return float("inf")
    if window is None:
        window = abs(n - m) + max(1, (n + m) // 3)
    window = max(window, abs(n - m))

    d = np.full((n + 1, m + 1), np.inf, dtype=np.float64)
    d[0, 0] = 0.0
    for i in range(1, n + 1):
        j_start = max(1, i - window)
        j_end = min(m, i + window)
        for j in range(j_start, j_end + 1):
            cost = np.linalg.norm(s1[i - 1] - s2[j - 1])
            d[i, j] = cost + min(d[i - 1, j], d[i, j - 1], d[i - 1, j - 1])
    return float(d[n, m])


CASES = [
    (20, 20, 16), (30, 30, 16), (30, 45, 16), (45, 30, 16),
    (60, 60, 18), (1, 1, 16), (1, 30, 16), (5, 40, 16), (100, 100, 8),
]


@pytest.mark.parametrize("n,m,dim", CASES)
def test_matches_reference(n, m, dim):
    rng = np.random.default_rng(1000 + n * 31 + m * 7 + dim)
    s1 = rng.normal(size=(n, dim)).astype(np.float32)
    s2 = rng.normal(size=(m, dim)).astype(np.float32)
    got = dtw_distance(s1, s2)
    want = dtw_reference(s1, s2)
    assert got == pytest.approx(want, rel=1e-5, abs=1e-5), \
        f"尺寸 {n}x{dim} vs {m}x{dim} 不一致: {got} != {want}"


@pytest.mark.parametrize("window", [1, 3, 5, 10, 50])
def test_matches_reference_with_explicit_window(window):
    rng = np.random.default_rng(7)
    s1 = rng.normal(size=(25, 12)).astype(np.float32)
    s2 = rng.normal(size=(25, 12)).astype(np.float32)
    got = dtw_distance(s1, s2, window=window)
    want = dtw_reference(s1, s2, window=window)
    assert got == pytest.approx(want, rel=1e-5, abs=1e-5)


def test_identical_sequences_give_zero():
    """同一序列距离应为 0——若 DP 边界写错，这里最先炸。"""
    rng = np.random.default_rng(11)
    s = rng.normal(size=(30, 16)).astype(np.float32)
    assert dtw_distance(s, s) == pytest.approx(0.0, abs=1e-4)


def test_empty_returns_inf():
    s = np.zeros((4, 3), dtype=np.float32)
    assert dtw_distance(np.zeros((0, 3), np.float32), s) == float("inf")
    assert dtw_distance(s, np.zeros((0, 3), np.float32)) == float("inf")


def test_normalized_distance_is_dist_over_length():
    rng = np.random.default_rng(3)
    s1 = rng.normal(size=(40, 10)).astype(np.float32)
    s2 = rng.normal(size=(40, 10)).astype(np.float32)
    raw = dtw_distance(s1, s2)
    assert normalized_distance(s1, s2) == pytest.approx(raw / len(s1), rel=1e-6)


def test_scale_invariance_of_normalization():
    """整体缩放输入，DTW 距离应等比缩放（欧氏距离的线性性）。"""
    rng = np.random.default_rng(5)
    s1 = rng.normal(size=(20, 8)).astype(np.float32)
    s2 = rng.normal(size=(20, 8)).astype(np.float32)
    d1 = dtw_distance(s1, s2)
    d2 = dtw_distance(s1 * 3.0, s2 * 3.0)
    assert d2 == pytest.approx(d1 * 3.0, rel=1e-4)
