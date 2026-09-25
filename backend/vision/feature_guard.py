"""失稳帧门控：把「物理上不可能」的单帧特征挡在识别之外。

## 背景

mediapipe 偶发单帧关键点崩坏。实测（526 帧真实视频）有两类：

- 单关节崩坏：第 33~34 帧左肘角 `171.4 → 154.7 → 35.0`，把画面导出来比对，
  **姿势几乎没变**，是测量跳了。
- 成片垂直崩坏：第 119~123 帧 `body_height` 走成
  `11.67 → 44.19 → 48.31 → 18.87 → 54.23` —— 人不可能在 40ms 里长高四倍。

（IMAGE 模式实测约 3.7% 的帧如此，见 pose_engine；VIDEO 模式少得多，但并非没有。）

## 为什么必须在**运行时**也挡

录制侧只做了「拒绝 IMAGE 模式录的模板」（template_compatible 的 engine_mode），
但运行时的坏帧会直接喂进识别：

- 对 DTW：距离是逐格代价的最小累积，**一格巨大代价会抬高整条最优路径**，
  于是一段本该匹配的动作被判成不匹配 —— 漏报。
- 对规则型：坏帧恰好满足某条角度条件，就凭空报出一个动作 —— 误报。

叠加起来症状就是**「时好时坏且不可解释」**，也最难查。

## 判据：只看**不连续**，不看"偏离"

第一版用的是「与最近若干帧中位数的偏离」，在这段视频上误杀了 7.8% 的帧，
其中最典型的一串是第 42~45 帧：`18.9 → 65.2 → 96.1 → 128.5 → 149.0`。
那是挥壶铃的**平滑加速**（真实动作），它之所以"偏离大"，是因为偏离了
**运动开始前**的中位数 —— 即那个判据本质上惩罚的是"运动刚开始"，
而不是"数据坏了"。

正确的问题是「**这一帧和它前后的走势接得上吗**」：用前两帧线性外推得到预测值，
看实际值偏离多少（二阶差分）。

    预测 = x[t-1] + (x[t-1] - x[t-2])      偏离 = |x[t] - 预测|

- 平滑运动（含加速）：二阶差分小 → 不拦。上面那串算出来是 15.4 和 1.5。
- 单帧崩坏：二阶差分巨大 → 拦。

另外限值随 dt 缩放（`加速度上限 × dt²`），所以低帧率下不会把真实动作误杀 ——
代价是低于约 10fps 时瓢帧在物理上已无法与真实动作区分，见下面的「已知边界」。

## 角度和比例特征都要管（修正过一次的错误结论）

我一开始把 `body_height` 这类比例特征排除在外，理由是"实测单帧变化 p95=19，
随人到镜头的距离变化"。**那个统计量本身就是被坏帧污染的**：看逐帧序列，
真实动作里 `body_height` 每帧只变 0.4~1.8，而跳 20~40 的那些全是坏帧。
按 p95 定阈值等于"用坏帧的数据去证明这个特征不可靠"，是典型的数据误读。

所以比例特征不但要管，而且比角度**更灵敏**（真实动作 0.4~1.8 vs 坏帧 20~40）。

## 已知边界：帧率过低时判据失效（**故意不修**）

限值随 dt 放宽，这正是为了不误杀真实动作。代价是**低于约 10fps 时，
166° 的单帧跳变在物理上已经与真实动作无法区分**，门控不再拦它。

不修的理由：3fps 下整条链路本来就是坏的（模板按 25fps 录，DTW 对齐早已失义），
而在那种帧率下"拦掉大变化"必然把真实动作一起拦掉。
正确的修法是提高帧率，`docs/手机当摄像头.md` 里已有排查项。
"""
import config

# 每类特征的 (二阶差分下限, 二阶差分dt缩放系数, 一阶差分下限, 一阶差分dt缩放系数)
#
# 角度：实测真实动作二阶差分 ≤ ~40°、单帧 ≤ ~62°（挥壶铃这种剧烈动作）；
#       崩坏是 119~166°。护理场景动作远慢于此，45° 有充足余量。
# 比例：实测真实动作 body_height 每帧变 0.4~1.8、hip_center_height 更小；
#       崩坏是 19~40（人不可能在 40ms 里长高四倍）。取 10/20 把两者分开。
#
# 注意：这两个值是**基于一段非护理场景素材**定的，上下界之间还有余量空间。
# 拿到真实护理素材后应当重新标定（门控统计会打进会话状态，可直接观察）。
_KIND_LIMITS = {
    "angle": (45.0, 4000.0, 90.0, 1200.0),
    "ratio": (10.0, 800.0, 20.0, 300.0),
}
# 连续被挡多少帧后重置并接受（防"没有出路"的状态）。
# 取 3 而不是 10：被拦帧不进预测基准，所以一次崩坏之后会连带拦住重入的帧；
# 这个值直接决定每次崩坏的代价（3 帧 ≈ 0.12s）。
RESYNC_AFTER = int(getattr(config, "GUARD_RESYNC_AFTER", 3))

_ANGLE_SUFFIX = "_angle"
_ANGLE_EXTRA = ("trunk_inclination", "trunk_lateral_lean", "trunk_vs_leg_angle")


def is_angle_feature(name):
    """按名字判断是否角度量纲（度）。

    用命名规则而不是写死清单：2d 与 3d 两个空间的键名不同
    （`left_knee_angle` vs `l_knee_angle`），写死清单迟早会漏掉一个。
    """
    return str(name).endswith(_ANGLE_SUFFIX) or name in _ANGLE_EXTRA


def is_monitored(name):
    """`visibility` 不参与：它本身会剧烈波动，拿它当坏帧判据只会天天误拦。"""
    return name != "visibility"


class FeatureGuard:
    """逐帧门控。`check(features, now_ts)` -> (是否可用, 原因, 出问题的特征名)。"""

    def __init__(self, fps=30.0):
        self._pair = {}          # 特征名 -> [x[t-2], x[t-1]]（只收**通过**的帧）
        self._gated = 0
        self._resync = 0
        self._streak = 0
        self._first_bad = None
        self._fps_floor = max(1.0, float(fps or 30.0))
        self._last_ts = None

    # ---- 统计（必须外显：静默丢帧和静默失败一样难查） ----
    @property
    def gated(self):
        """被判为失稳、未参与识别的帧数。"""
        return self._gated

    @property
    def resyncs(self):
        return self._resync

    @property
    def first_bad(self):
        return self._first_bad

    def summary(self):
        if not self._gated:
            return ""
        extra = f"，其中 {self._resync} 次因连续失稳而重同步" if self._resync else ""
        return (f"修复了 {self._gated} 帧测量崩坏"
                f"（规则判定跳过这些帧，序列匹配用预测值替代{extra}）")

    def reset(self):
        self._pair.clear()
        self._streak = 0
        self._last_ts = None

    # ---- 限值 ----
    def _dt(self, now_ts):
        if now_ts is None or self._last_ts is None:
            return 1.0 / self._fps_floor
        return max(1.0 / self._fps_floor, min(0.5, abs(now_ts - self._last_ts)))

    @staticmethod
    def _limits(name, dt):
        jerk, accel, jump, rate = _KIND_LIMITS[
            "angle" if is_angle_feature(name) else "ratio"]
        return jerk + accel * dt * dt, max(jump, rate * dt)

    # ---- 门控 ----
    def check(self, features, now_ts=None):
        """返回 (ok, reason, bad_features)。ok=False 表示该帧不应参与识别。"""
        if not features:
            return False, "无特征", []

        dt = self._dt(now_ts)
        bad, reasons = [], []
        for name, value in features.items():
            if not is_monitored(name):
                continue
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            why = self._judge(name, v, dt)
            if why:
                bad.append(name)
                reasons.append(f"{name} {why}")

        if not bad:
            self._advance(features)
            self._last_ts = now_ts
            self._streak = 0
            return True, "", []

        # 被拦的帧**不进**预测基准：否则一个坏帧会把接下来两帧的预测一起带偏。
        # 代价是崩坏之后的头几帧也要拦（重入帧对不上旧基准），
        # 由 RESYNC_AFTER 兜住，代价固定为 RESYNC_AFTER 帧。
        self._streak += 1
        if self._first_bad is None:
            self._first_bad = "；".join(reasons[:3])
        if self._streak >= RESYNC_AFTER:
            self.reset()
            self._advance(features)
            self._last_ts = now_ts
            self._resync += 1
            return True, "连续失稳已达上限，重置门控并接受当前帧", bad
        self._gated += 1
        return False, "；".join(reasons[:3]), bad

    def repair(self, features, bad):
        """把坏值替换成线性预测值，返回新的特征字典。

        ## 为什么是"替代"而不是"丢帧"（实测出来的，不是偏好）

        拿真实视频里一段动作当模板回放，比较三种处理方式的 DTW 距离与命中：

        | 场景 | 不处理 | 丢帧 | 预测值替代 |
        |---|---|---|---|
        | 干净 | 1.8 ✓ | 2.0 ✓ | 2.0 ✓ |
        | 坏 1 帧 | 5.3 ✓ | 2.0 ✓ | **2.1 ✓** |
        | 坏 3 帧 | 5.3 ✓ | 16.6 **✗** | **4.6 ✓** |
        | 坏 6 帧 | 5.3 ✓ | 29.7 **✗** | **4.6 ✓** |

        丢帧会把**命中变成未命中**：DTW 的滑窗少了几帧，等于拿一段残缺的动作去
        比对，距离反而涨到阈值以上。而序列型匹配要的正是"时间上连续、采样完整"，
        把帧抽掉正好破坏了它需要的东西。替代则同时做到两件事：
        去掉坏值 + 保持序列长度与时间间隔。

        替代值取"按上两帧线性外推"再夹到上两帧附近，避免外推跑飞。
        """
        if not bad:
            return features
        out = dict(features)
        for name in bad:
            pair = self._pair.get(name)
            if not pair:
                continue
            if len(pair) == 1:
                out[name] = pair[0]
                continue
            pred = pair[1] + (pair[1] - pair[0])
            floor, ceil = min(pair[0], pair[1]), max(pair[0], pair[1])
            out[name] = max(floor, min(ceil, pred))
        return out

    def _judge(self, name, v, dt):
        pair = self._pair.get(name)
        if not pair:
            return ""
        jerk_limit, jump_limit = self._limits(name, dt)
        if len(pair) == 1:
            dv = abs(v - pair[0])
            if dv > jump_limit:
                return f"单帧突跳 {dv:.1f}（上限 {jump_limit:.0f}）"
            return ""
        pred = pair[1] + (pair[1] - pair[0])
        dev = abs(v - pred)
        if dev > jerk_limit:
            return (f"走势不连续：偏离预测 {dev:.1f}"
                    f"（上两帧 {pair[0]:.1f}→{pair[1]:.1f}，预测 {pred:.1f}，"
                    f"上限 {jerk_limit:.0f}）")
        dv = abs(v - pair[1])
        if dv > jump_limit:
            return f"单帧突跳 {dv:.1f}（上限 {jump_limit:.0f}）"
        return ""

    def _advance(self, features):
        for name, value in features.items():
            if not is_monitored(name):
                continue
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            pair = self._pair.setdefault(name, [])
            pair.append(v)
            if len(pair) > 2:
                del pair[:-2]
