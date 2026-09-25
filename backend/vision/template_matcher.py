"""骨骼序列模板匹配：DTW 动态时间规整，实现更精细的动作匹配。

与「角度规则」(rule) 不同，序列模板记录一段动作的关节特征时间序列，
通过 DTW 度量实时滑窗与模板的相似度，可识别「翻身、拍背、环抱转移」等
规则难以表达的过程性动作。
"""
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

# 3D「机位无关」特征向量（18 维，由 PoseEngine.compute_features_3d 提供）
FEATURE_ORDER_3D = [
    "l_elbow_angle", "r_elbow_angle", "l_shoulder_angle", "r_shoulder_angle",
    "l_knee_angle", "r_knee_angle", "l_hip_angle", "r_hip_angle",
    "trunk_vs_leg_angle", "hands_distance",
    "l_wrist_to_hip", "r_wrist_to_hip", "leg_extend", "ankle_distance",
    "l_wrist_height", "r_wrist_height", "l_wrist_forward", "r_wrist_forward",
]

# ---- 特征空间版本管理 ----------------------------------------------------
# 2d = 图像空间特征（旧版）：换机位后结构性地失配，只能机位固定时用
# 3d = 三维机位无关特征（新版）：多机位可共用同一套模板
#
# 模板与运行时必须使用**同一版本**才允许匹配。版本不一致时宁可判为不匹配，
# 也不能拿错版本的模板去凑，否则会产生误报。
SPACE_2D = "2d"
SPACE_3D = "3d"
# 两个空间各自独立计数，数值相同不代表是同一套特征。
# 版本 2（2d）：修正 trunk_inclination 的参照轴方向（原来直立=180°，
#   与 actions.py 声明的「0=直立」相反）。版本 1 录制的模板必须重录。
FEATURE_VERSION = {SPACE_2D: 2, SPACE_3D: 2}
FEATURE_ORDER_BY_SPACE = {SPACE_2D: FEATURE_ORDER, SPACE_3D: FEATURE_ORDER_3D}

# 默认 DTW 距离阈值。注意两个空间的距离量级完全不同：
# - 2d 空间经验值 8.0
# - 3d 空间经验值 40.0（仿真标定：同动作约 21，无关动作约 85）
# 上实机后务必用 --calibrate 重新标定，见 record_template.py。
DEFAULT_THRESHOLD = {SPACE_2D: 8.0, SPACE_3D: 40.0}


def feature_order(space=SPACE_2D):
    return FEATURE_ORDER_BY_SPACE.get(space, FEATURE_ORDER)


def feature_dim(space=SPACE_2D):
    return len(feature_order(space))


def features_to_vector(features, space=SPACE_2D, exclude=()):
    """特征 dict -> 定长 numpy 向量。

    缺失字段补 0；`exclude` 里列出的维度**强制置 0**（模板声明"这个动作不用它"）。
    """
    skip = set(exclude or ())
    return np.array([0.0 if k in skip else float(features.get(k, 0.0) or 0.0)
                     for k in feature_order(space)], dtype=np.float32)


# 依赖踝关节的特征：**脚不在画面里时它们全是编造的**（mediapipe 对画面外的关节
# 只打低 visibility 并推测一个位置）。实测（几何仿真）：
#   人在画面里只有腰以上时，站立/下蹲/坐下/转身的三维特征**完全相同**
#   （trunk_vs_leg 180.00、l_knee_angle 180.00、leg_extend 0.06、ankle_distance 0.56）
#   —— 这个视角下关于下蹲的信息量为零，不是算法问题。
#
# 但"拍到膝盖（大腿入画）"就够了：trunk_vs_leg_angle（大腿相对躯干）不需要踝，
# 实测对下蹲的响应 29°（180.0 → 154.9 → 150.9），跨机位漂移 0.000°。
# 于是"不依赖脚"的下蹲判据 = 只用 trunk_vs_leg_angle / l_hip_angle 这类量。
#
# **已知代价（实测，必须知道）**：trunk_vs_leg_angle 同时被"躯干前倾"驱动，
# 所以浅蹲（154.9）与轻弯腰（约 150）会撞车——而「弯腰操作」本来就在动作库里，
# 这会表现为"做弯腰被判成屈膝下蹲"。要区分只能靠序列的时间过程（DTW）
# 并用真实素材标定，不是加一个特征能解决的。
ANKLE_DEPENDENT = {
    SPACE_2D: ("left_knee_angle", "right_knee_angle", "body_height"),
    SPACE_3D: ("l_knee_angle", "r_knee_angle", "leg_extend", "ankle_distance"),
}


def validate_exclude(exclude, space=SPACE_2D):
    """校验要排除的维度名，返回规范化后的元组。

    名字写错必须**报错**而不是忽略：用户排除某个维度是想要"这个动作不要用它"，
    静默忽略会让那个维度继续参与匹配——正是本机制要消除的静默失效。
    """
    order = set(feature_order(space))
    out = []
    for name in (exclude or ()):
        n = str(name).strip()
        if not n:
            continue
        if n not in order:
            raise ValueError(
                f"要排除的维度 {n!r} 不在 {space} 空间的向量里。"
                f"可选：{', '.join(feature_order(space))}")
        if n not in out:
            out.append(n)
    return tuple(out)


def missing_keys(features, space=SPACE_2D):
    """返回该空间需要、但特征字典里没有的键（保持 feature_order 的顺序）。

    ## 为什么必须显式检查，不能靠"补 0"兜住

    本函数的调用方过去直接 `features_to_vector`，缺失字段静默补 0.0。而 **0.0 恰好
    落在一个看起来完全正常的取值范围里**，下游（DTW / 规则）无法区分"这个量没测到"
    和"这个量真的是 0"。

    实测过一个真实后果：`space="3d"` 但拿不到 `pose_world_landmarks` 时，
    代码回落去喂二维特征字典——两个空间的 18 个维度里**只有 `hands_distance`
    同名**，其余 17 个键全部缺失，于是被补成 0.0（整条向量实测全为 0）。
    模板经过 Z-score 归一化后，全零向量会变成一个"看起来很正常"的向量，
    匹配既不报错也不命中，只在日志之外静静发生。

    注意那个同名的 `hands_distance` 反而更危险：它是唯一"键存在"因而
    **不会被本函数发现**的错配维度——它来自二维归一化坐标，与三维的米制距离
    不是同一个量。所以本函数只挡得住"空间搞错了"的一部分，
    整类问题仍要靠特征空间的版本戳拦住（见 `template_compatible`）。

    因此调用方应当在拿不到该空间的完整特征时**跳过这一帧并说出来**，
    而不是让它变成一串 0 混进去。
    """
    return [k for k in feature_order(space) if k not in features]


def template_compatible(template_data, space, engine_mode=None):
    """判断某条序列模板能否在当前特征空间下使用，返回 (是否可用, 原因)。

    **必须同时比对 space 与 version**——只比 space 是不够的：同一空间内特征语义
    变更后（例如修正了某个角的参照轴），旧模板里存的向量含义已经变了，
    拿它去匹配会凑出「看似成功」的假结果，那就是误报。

    保守原则：没有声明 space / version 的旧模板一律判为不可用，宁可被跳过并
    提示重录，也不冒险匹配。

    engine_mode: 当前运行时的推理模式（"video"/"image"）。传入时会额外拒绝
    **用 IMAGE 模式录的模板**。原因：IMAGE 模式没有帧间跟踪，实测约 3.7% 的帧会
    瞬间失稳（躯干角从 4° 跳到 170°+，见 pose_engine 的实测数据），而录制会把
    几百帧下采样到几十帧——失稳向量有相当概率被存进模板，并**永久拉偏 DTW 距离**。
    这类模板的症状是「时好时坏且不可解释」，最难排查，所以宁可跳过并提示重录。

    注意：本项只拒绝**显式声明**为 image 的模板。没有 engine_mode 字段的旧模板
    不因此被拒（录制侧从本次起才会写入该字段），避免把此前录制的正常模板一并作废。
    """
    if not isinstance(template_data, dict):
        return False, "模板数据缺失"
    tpl_space = (template_data.get("space") or "").strip().lower()
    if not tpl_space:
        return False, "未声明特征空间（旧数据）"
    if tpl_space != space:
        return False, f"模板为 {tpl_space} 空间"
    expect = FEATURE_VERSION.get(space)
    raw_ver = template_data.get("version")
    try:
        tpl_ver = int(raw_ver) if raw_ver is not None else None
    except (TypeError, ValueError):
        tpl_ver = None
    if tpl_ver is None:
        return False, "未声明特征版本（旧数据）"
    if tpl_ver != expect:
        return False, f"模板为 v{tpl_ver}，当前需 v{expect}"
    if engine_mode is not None:
        tpl_mode = str(template_data.get("engine_mode") or "").strip().lower()
        running = str(engine_mode).strip().lower()
        if tpl_mode == "image" and running.startswith("v"):
            return False, "模板录自 IMAGE 模式（含失稳帧），请用 VIDEO 模式重录"
    return True, ""


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

    实现说明（性能关键）：代价矩阵用一次广播整体算出，DP 阶段只在 Python
    浮点数上做三次比较。原实现对**每个格子**调用一次 `np.linalg.norm`，
    每次都有 numpy 的 Python 层开销，格子数是 n×m 量级，因此慢得多。
    实测 30×16 的序列从 2.00ms 降到 0.12ms（约 17 倍），结果一致
    （差异仅 float32 舍入，约 1e-5）。

    DP 递推里有 `d[i, j-1]` 项（同一行内依赖），因此**不能**整体向量化，
    只把代价矩阵搬出循环；这是能拿到大部分收益的最小改动。
    """
    s1 = np.asarray(s1, dtype=np.float32)
    s2 = np.asarray(s2, dtype=np.float32)
    n, m = len(s1), len(s2)
    if n == 0 or m == 0:
        return float("inf")
    if window is None:
        window = abs(n - m) + max(1, (n + m) // 3)
    window = max(window, abs(n - m))

    # 一次广播算出 (n, m) 全部格子的欧氏距离，并转成 Python 列表：
    # 列表的标量读取远快于 ndarray 的逐元素索引
    cost = np.linalg.norm(s1[:, None, :] - s2[None, :, :], axis=2).tolist()

    inf = float("inf")
    prev = [inf] * (m + 1)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur = [inf] * (m + 1)
        j_start = max(1, i - window)
        j_end = min(m, i + window)
        ci = cost[i - 1]
        cur_jm1 = inf                      # 即 d[i, j-1]，随内层循环递推
        for j in range(j_start, j_end + 1):
            best = prev[j]                 # d[i-1, j]
            if cur_jm1 < best:             # d[i, j-1]
                best = cur_jm1
            if prev[j - 1] < best:         # d[i-1, j-1]
                best = prev[j - 1]
            cur_jm1 = ci[j - 1] + best
            cur[j] = cur_jm1
        prev = cur
    return float(prev[m])


def normalized_distance(s1, s2, window=None):
    """归一化 DTW 距离（除以模板长度），跨模板可比。"""
    dist = dtw_distance(s1, s2, window)
    n = len(s1)
    return dist / n if n else float("inf")


class TemplateMatcher:
    """对单条模板做实时滑窗匹配，输出归一化距离与是否命中。

    重要：模板与实时滑窗必须处在同一归一化空间。
    模板在构造时按自身统计做 Z-score 归一化，因此实时窗口也要用
    「模板的均值/标准差」做同样变换（注意不能用窗口自身的统计量，
    否则短窗口会把整段动作压平，产生尺度错配导致永远匹配不上）。
    """

    def __init__(self, template_vectors, threshold=None, min_ratio=0.6, space=SPACE_2D,
                 exclude=()):
        """
        template_vectors: (T, D) 模板序列
        threshold:        归一化 DTW 距离阈值（None 时按特征空间取默认值）
        min_ratio:        滑窗至少覆盖模板长度比例才判定
        space:            特征空间（2d / 3d）。维度不符直接拒绝，
                          避免用错版本的模板产生误报。
        exclude:          该模板**不使用**的维度名（录制侧声明、存在模板数据里）。
                          这些维度在这里被置零：均值/标准差都按 0 算，
                          于是"零方差"→ 不会成为判别维度（见 obs_guard），
                          观测门控也就不会因为它们去要求某个部位必须在画面里。
                          运行时必须用同一个 `exclude` 构造向量，否则
                          （真实值 − 0）/ 1.0 会凭空产生距离，把命中变成未命中。
        """
        raw = np.asarray(template_vectors, dtype=np.float32)
        if raw.ndim != 2 or raw.shape[0] == 0:
            raise ValueError("模板序列必须是 (T, D) 的二维数组且非空")
        expect = feature_dim(space)
        if raw.shape[1] != expect:
            raise ValueError(
                f"特征空间版本不符：space={space} 需要 {expect} 维，"
                f"实际模板为 {raw.shape[1]} 维。请用对应版本重新录制模板。")
        self.exclude = validate_exclude(exclude, space)
        if self.exclude:
            raw = raw.copy()
            for name in self.exclude:
                raw[:, feature_order(space).index(name)] = 0.0
        if threshold is None:
            threshold = DEFAULT_THRESHOLD.get(space, 8.0)
        self.space = space
        self._mean = raw.mean(axis=0)
        std = raw.std(axis=0)
        # 真实标准差（未做 1e-6 兜底）：观测可用性判定要用它来判断
        # 「这个维度在这个动作里到底有没有变化」。归一化用的 std 把零方差维度
        # 改成了 1.0，那会让"恒定不变"看起来"变化很大"——不能复用。
        self._std_raw = std.copy()
        std = np.where(std < 1e-6, 1.0, std)
        self._std = std
        self.template = (raw - self._mean) / self._std
        self.template_len = len(self.template)
        self.threshold = threshold
        self.min_ratio = min_ratio
        self.buffer = []
        self.max_len = max(self.template_len * 3, 30)
        self._last_hit = float("-inf")

    def _normalize(self, arr):
        """用模板的统计量归一化实时窗口，保证与模板同尺度。"""
        return (np.asarray(arr, dtype=np.float32) - self._mean) / self._std

    def reset(self):
        self.buffer = []
        self._last_hit = float("-inf")

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

        recent = self._normalize(np.array(self.buffer[-self.template_len:], dtype=np.float32))
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
