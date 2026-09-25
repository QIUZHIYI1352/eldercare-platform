"""观测可用性门控：画面没拍全的部位，相关特征一律不参与判定。

## 为什么必须做（"手持跟拍 + 时全时半"的必需品）

mediapipe 在人体有一部分**不在画面里**时，**不会返回空值**——它按可见部分
**推测**出画面外的关键点，只把该点的 `visibility` 打低。于是
`compute_features_3d` 照样能算出 `l_knee_angle`，只是那个值是**编造的**：

- 对规则型：编造值恰好落在某条条件的阈值区间内 → 凭空报出动作（**误报**）
- 对序列型：DTW 把编造值当真实观测，距离被拉偏 → 时好时坏

而 `pose_engine.compute_features` 每帧算出的 `visibility` **从没被用来判定过**
（它甚至不在 `FEATURE_ORDER` 里）。所以整套链路对"这个关节到底有没有被看到"
是完全不知情的。

叠加 `features_to_vector` 对缺失字段补 0.0（0.0 落在一个看起来完全正常的取值
范围里），"缺数据"与"数据为 0"在下游无法区分。

## 判据：这个动作真正依赖的部位，都看清了吗

不是"所有关节都要看清"——那会过度拦截（站立时鼻子看不清并不影响膝角判定），
而是**该模板真正有变化的那些维度**所依赖的部位：

    维度的判别力 ← 该维度在模板里的**真实**标准差（未做 1e-6 兜底的那个）
    std 很小的维度对这个动作没有判别力，缺了不影响；std 大的维度才是判据所在

`TemplateMatcher._std_raw` 保留了真实标准差，因此本模块直接**从即将参与匹配的
那个 matcher 上**取判别维度——不重新读一遍模板数据。两处各读一遍迟早会分叉，
而分叉的后果是"某动作在命令行能识别、在平台里识别不出"这类极难查的问题。

## 取保守方向（与"宁可不报也别误报"一致）

一个动作的判别维度里只要有**任意一个**依赖部位没看清，就判定该动作
**当前不可观测**，这一帧不参与它的匹配。代价是漏报，换来的是不会拿
编造出来的膝角去报一个"屈膝下蹲"。
"""

# 关节被认定为「看清了」的 visibility 下限（环境变量 OBS_VIS_LIMIT 覆盖）
try:
    import config
    VIS_LIMIT = float(getattr(config, "OBS_VIS_LIMIT", 0.5))
except Exception:  # pragma: no cover - 脱离项目环境时仍可用
    VIS_LIMIT = 0.5

# 特征 -> 计算它所需的关节（两个空间的键名不同，故分开列）
LANDMARK_DEPS = {
    # ---- 二维图像空间（compute_features）----
    "left_elbow_angle": ("left_shoulder", "left_elbow", "left_wrist"),
    "right_elbow_angle": ("right_shoulder", "right_elbow", "right_wrist"),
    "left_shoulder_angle": ("left_elbow", "left_shoulder", "left_hip"),
    "right_shoulder_angle": ("right_elbow", "right_shoulder", "right_hip"),
    "left_knee_angle": ("left_hip", "left_knee", "left_ankle"),
    "right_knee_angle": ("right_hip", "right_knee", "right_ankle"),
    "left_hip_angle": ("left_shoulder", "left_hip", "left_knee"),
    "right_hip_angle": ("right_shoulder", "right_hip", "right_knee"),
    "trunk_inclination": ("left_shoulder", "right_shoulder",
                          "left_hip", "right_hip"),
    "trunk_lateral_lean": ("left_shoulder", "right_shoulder"),
    "hand_height_left": ("left_wrist", "left_shoulder"),
    "hand_height_right": ("right_wrist", "right_shoulder"),
    "head_height": ("nose", "left_shoulder"),
    "body_height": ("nose", "left_shoulder", "right_shoulder",
                    "left_ankle", "right_ankle"),
    "hip_center_height": ("left_hip", "right_hip", "nose",
                          "left_shoulder", "right_shoulder"),
    # ---- 三维机位无关空间（compute_features_3d）----
    "l_elbow_angle": ("left_shoulder", "left_elbow", "left_wrist"),
    "r_elbow_angle": ("right_shoulder", "right_elbow", "right_wrist"),
    "l_shoulder_angle": ("left_elbow", "left_shoulder", "left_hip"),
    "r_shoulder_angle": ("right_elbow", "right_shoulder", "right_hip"),
    "l_knee_angle": ("left_hip", "left_knee", "left_ankle"),
    "r_knee_angle": ("right_hip", "right_knee", "right_ankle"),
    "l_hip_angle": ("left_shoulder", "left_hip", "left_knee"),
    "r_hip_angle": ("right_shoulder", "right_hip", "right_knee"),
    "trunk_vs_leg_angle": ("left_shoulder", "right_shoulder",
                           "left_hip", "right_hip",
                           "left_knee", "right_knee"),
    "hands_distance": ("left_wrist", "right_wrist"),
    "l_wrist_to_hip": ("left_wrist", "left_hip", "right_hip"),
    "r_wrist_to_hip": ("right_wrist", "left_hip", "right_hip"),
    "leg_extend": ("left_hip", "right_hip", "left_ankle", "right_ankle"),
    "ankle_distance": ("left_ankle", "right_ankle"),
    "l_wrist_height": ("left_wrist", "left_shoulder", "right_shoulder",
                       "left_hip", "right_hip"),
    "r_wrist_height": ("right_wrist", "left_shoulder", "right_shoulder",
                       "left_hip", "right_hip"),
    "l_wrist_forward": ("left_wrist", "left_shoulder", "right_shoulder",
                        "left_hip", "right_hip"),
    "r_wrist_forward": ("right_wrist", "left_shoulder", "right_shoulder",
                        "left_hip", "right_hip"),
}

# 中文名，只为把提示写成"看不到脚踝"而不是"left_ankle 不可见"
_CN = {
    "nose": "头", "left_shoulder": "左肩", "right_shoulder": "右肩",
    "left_elbow": "左肘", "right_elbow": "右肘",
    "left_wrist": "左手", "right_wrist": "右手",
    "left_hip": "左髋", "right_hip": "右髋",
    "left_knee": "左膝", "right_knee": "右膝",
    "left_ankle": "左脚", "right_ankle": "右脚",
}

# 判别力阈值：该维度的**真实** std 超过它才算"这个动作真正在用的量"。
# 角度用度、比例用无量纲比值，量纲不同所以分开定。
# 角度 8°：一段动作里某个关节角变化不到 8°，说明这个动作没怎么用它。
# 比例 0.08：躯干自洽坐标系下的归一化量，0.08 约相当于一个身宽的 8%。
DISC_ANGLE_STD = 8.0
DISC_RATIO_STD = 0.08

# 这些键（见 pose_engine.compute_features）。单独 import 不引入 mediapipe：
# obs_guard -> feature_guard -> config，无环。
from backend.vision.feature_guard import is_angle_feature  # noqa: E402
from backend.vision.template_matcher import feature_order  # noqa: E402


def cn(name):
    return _CN.get(name, name)


# 与 pose_engine.LM 一致（这里独立列一份，避免为取索引而加载 mediapipe：
# 本模块要能在没装 mediapipe 的机器上被导入与单测）
_LM_INDEX = {
    "nose": 0,
    "left_shoulder": 11, "right_shoulder": 12,
    "left_elbow": 13, "right_elbow": 14,
    "left_wrist": 15, "right_wrist": 16,
    "left_hip": 23, "right_hip": 24,
    "left_knee": 25, "right_knee": 26,
    "left_ankle": 27, "right_ankle": 28,
}


def visibility_map(pts2d):
    """从 pose_engine.detect 的二维关键点里取「关节名 -> visibility」。

    二维与三维关键点是**同一次推理**的两个输出（见 PoseEngine.detect），
    画面外的关节在二维输出里同样会被打低 visibility。因此这里用二维的
    visibility 来判断三维特征是否可信是成立的，且不需要改动 detect 的返回值。
    """
    out = {}
    if not pts2d:
        return out
    for name, idx in _LM_INDEX.items():
        if idx < len(pts2d):
            pt = pts2d[idx]
            try:
                out[name] = float(pt[2])
            except (IndexError, TypeError, ValueError):
                out[name] = 1.0
    return out


def lowest_visibility(names, vis, default=1.0):
    """一组关节里最低的 visibility（None 表示这组里没有可查的关节）。

    vis 里查不到的关节（例如旧数据没写 visibility 字段）按 `default` 处理：
    保守方向上这里给 1.0（=视为可见），与 template_compatible 处理旧模板字段
    的先例一致——不能因为新加的一项判定就把此前能用的输入一并作废。
    真正"看不到"的情况 mediapipe 会给低分，不会走这个分支。
    """
    vals = [vis.get(n, default) for n in names]
    if not vals:
        return None
    try:
        return min(float(v) for v in vals)
    except (TypeError, ValueError):
        return None


def feature_visibility(feature_name, vis, default=1.0):
    """算某个特征的可信度 = 它依赖的关节里最低的那个 visibility。"""
    deps = LANDMARK_DEPS.get(feature_name)
    if not deps:
        return None
    return lowest_visibility(deps, vis, default)


def discriminative_names(std_raw, space, angle_min=DISC_ANGLE_STD,
                         ratio_min=DISC_RATIO_STD):
    """模板里真正有变化的维度名（即这个动作的判据所在）。

    std_raw: TemplateMatcher._std_raw —— **未经 1e-6 兜底**的真实标准差。

    注意不能用归一化用的那个 std：它把零方差维度改成了 1.0，于是"恒定不变"
    会被判成"变化很大"，判别维度会退化成"模板里所有维度"，
    门控就变成了"全身每一处都必须看清"——过度拦截，等于把可用性判定废掉。
    """
    order = feature_order(space)
    out = []
    for i, name in enumerate(order):
        if i >= len(std_raw):
            continue
        std = float(std_raw[i])
        limit = angle_min if is_angle_feature(name) else ratio_min
        if std > limit:
            out.append(name)
    return out


class ObsGate:
    """某个动作的观测可用性判定（由它自己的 matcher 构造）。

    `check(pts2d)` -> (ok, 缺失部位的中文名列表)
    ok=False 表示画面里看不全这个动作所依赖的部位，**当前帧不应参与它的匹配**。
    """

    def __init__(self, name, discriminative, required, vis_limit=VIS_LIMIT):
        self.name = name                      # 动作名（用于提示）
        self.discriminative = list(discriminative)   # 该动作真正在用的维度
        self.required = list(required)               # 这些维度依赖的关节（去重保序）
        self.vis_limit = float(vis_limit)

    @classmethod
    def from_matcher(cls, matcher, name="", vis_limit=VIS_LIMIT,
                     angle_min=DISC_ANGLE_STD, ratio_min=DISC_RATIO_STD):
        """从 TemplateMatcher 直接构造——保证与真正参与匹配的模板同名同源。"""
        discs = discriminative_names(matcher._std_raw, matcher.space,
                                     angle_min, ratio_min)
        req = []
        for feat in discs:
            for lm in LANDMARK_DEPS.get(feat, ()):
                if lm not in req:
                    req.append(lm)
        return cls(name, discs, req, vis_limit)

    def check(self, pts2d, vis=None):
        """返回 (该动作当前是否可观测, 缺失部位的中文名列表)。"""
        if not self.required:
            # 模板里所有维度都没有判别力（整段动作几乎没动）——无判据可言。
            # 不拦：拦住它只会让一个本就没判别力的模板永远不生效，
            # 而它即便生效也匹配不出什么（模板恒定 → 与任何东西距离都不小）。
            return True, []
        if vis is None:
            vis = visibility_map(pts2d)
        if not vis:
            return False, [cn(n) for n in self.required]
        low = [cn(n) for n in self.required
               if vis.get(n, 1.0) < self.vis_limit]
        return (not low), low

    def describe(self):
        if not self.required:
            return f"{self.name}：模板内无判别维度，不做观测门控"
        return (f"{self.name}：依赖 {('、'.join(cn(n) for n in self.required))}"
                f"（判别维度 {len(self.discriminative)} 个）")


def build_gates(seq_matchers, action_names=None, vis_limit=VIS_LIMIT):
    """为全部序列模板建门控：action_id -> ObsGate。"""
    names = action_names or {}
    out = {}
    for aid, m in (seq_matchers or {}).items():
        out[aid] = ObsGate.from_matcher(m, names.get(aid, aid), vis_limit)
    return out


def visibility_report(pts2d, vis_limit=VIS_LIMIT):
    """一行观测质量摘要（标定用）。

    手持跟拍 + 时全时半时，最需要知道的其实是"到底哪些部位经常看不到"——
    有了这个统计才能判断某个动作是不是**原理上**就判不了，
    而不是把它当成算法调不好的问题反复调阈值。
    """
    vis = visibility_map(pts2d)
    if not vis:
        return "无可观测关键点"
    items = sorted(vis.items(), key=lambda kv: kv[1])
    low = [f"{cn(n)}{v:.2f}" for n, v in items if v < vis_limit]
    worst = "、".join(f"{cn(n)}{v:.2f}" for n, v in items[:3])
    if low:
        return f"看不清 {len(low)} 处：{'、'.join(low)}"
    return f"观测良好（最低三处：{worst}）"
