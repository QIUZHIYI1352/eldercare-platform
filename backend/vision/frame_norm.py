"""画面宽高比归一化：把输入画面**补边**到模板录制时的比例。

## 为什么必须做

2d 特征里的关节角由 mediapipe 的归一化坐标算出，而 mediapipe 用
`x/W`、`y/H` **两个不同的分母**。于是宽高比一变，所有角度就系统性偏移。
实测（见 record_template.py 的注释）16:9 → 竖屏 9:16：

    躯干角 6.8° → 20.7°，膝关节角 171° → 154°

后者等于**虚报了一个屈膝动作**——不是精度问题，是直接产生误报。
分辨率高低本身无影响，只与比例有关。

## 为什么是「补边」而不是「拉伸」

拉伸会改变人的像素几何，本来就偏移的角度会再偏一次（且方向不可预测）。
补边只改画布的 W/H，**不动人的像素尺寸**：补到模板比例后，
`x/W`、`y/H` 的两个分母关系就与录模板时一致，角度随之回到同一尺度。

数学上（Δ 表示同一物体上两点的差，下标 s/t 为源/模板）：

    补边到 A_t 后   Δx_norm = Δx_px / W_t' = Δx_px / (A_t · H_s)
    模板录制时      Δx_norm = Δx_px / W_t  = Δx_px / (A_t · H_t)

两者都等于 `A_t · Δy_px / Δx_px` 的关系式，因此角度只差 H 的**共同因子**，
而角度只依赖 Δy/Δx 的**比值** —— H 被约掉，于是两者完全相等。

`tests/test_frame_norm.py` 用构造姿态把这个等式钉住了（不是"跑起来没报错"）。

## 黑边是特性，不是缺陷

补边后会看到画面两侧（或上下）多出黑边。这是**可见的归一化证据**：
一眼就能看出归一化是否生效、补了多少。刻意不做"自动裁剪"——
裁剪会切掉真实画面内容，那才是真的丢信息。
"""
import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover - 环境未装 opencv 时降级
    cv2 = None

# 低于这个相对差异就不补边：1.92:1 与 1.78:1 这种差 8% 的比例才值得动画面，
# 差 1~2% 的（例如 1280x720 与 1920x1080 的舍入）补出来的边细得看不见，
# 却会让每一帧都多一次拷贝。
ASPECT_TOL = 0.02

# 模板比例聚类时，两个比例相对差小于它就算同一组
ASPECT_GROUP_TOL = 0.02


def aspect_of(size):
    """(宽, 高) -> 宽高比；未知/非法返回 None。"""
    if not size or len(size) < 2:
        return None
    try:
        w, h = float(size[0]), float(size[1])
    except (TypeError, ValueError):
        return None
    return w / h if w > 0 and h > 0 else None


def group_aspects(aspects, tol=ASPECT_GROUP_TOL):
    """把模板记录的宽高比聚类，返回 [{"aspect": x, "count": n}]，按票数降序。

    None 是"未记录"（较早版本录的模板），不参与投票也不因此被跳过——
    与 template_compatible 处理 engine_mode 的先例一致：不能因为新加的字段
    就把此前录制的正常模板一并作废。
    """
    groups = []
    for a in aspects:
        if not a:
            continue
        for g in groups:
            if abs(a - g["aspect"]) / min(a, g["aspect"]) <= tol:
                g["values"].append(a)
                g["aspect"] = sum(g["values"]) / len(g["values"])
                g["count"] += 1
                break
        else:
            groups.append({"aspect": float(a), "count": 1, "values": [float(a)]})
    groups.sort(key=lambda g: (-g["count"], g["aspect"]))
    return groups


class AspectPlan:
    """本次会话该用哪个宽高比、要不要补边、哪些模板必须跳过。

    构造后只读；`apply(frame)` 每帧调用一次（不需要补边时是零开销的直通）。
    """

    def __init__(self, source_aspect=None, target_aspect=None, pad=False,
                 note="", unmarked=0, skipped=(), groups=()):
        self.source_aspect = source_aspect
        self.target_aspect = target_aspect
        self.pad = bool(pad)
        self.note = note
        self.unmarked = unmarked          # 未记录 frame_aspect 的模板条数
        self.skipped = list(skipped)      # [(名称, 模板比例, 相对差异)]
        self.groups = list(groups)        # 全部候选组，供日志展示

    def apply(self, frame):
        """按计划补边。不需要补边时原样返回（同一个对象，不做拷贝）。"""
        if not self.pad or frame is None:
            return frame
        return pad_to_aspect(frame, self.target_aspect)

    def warns(self):
        """除"剔除清单"之外的提示行（剔除由 describe 统一渲染，避免说两遍）。"""
        out = []
        if self.unmarked:
            out.append(
                f"有 {self.unmarked} 条模板未记录录制宽高比（较早版本录的），"
                f"无法比对，本次按可用处理；建议重录以纳入校验")
        return out


def aspect_compatible(template_aspect, target, tol=ASPECT_TOL):
    """模板录制比例是否落在目标比例的容差内。

    没有记录（None）一律视为**兼容**——与 template_compatible 处理 engine_mode
    的先例一致：不能因为新加的字段就把此前录制的正常模板作废。
    """
    if not template_aspect or not target:
        return True
    return abs(template_aspect - target) / min(template_aspect, target) <= tol


def _skip_list(template_aspects, target, tol=ASPECT_TOL):
    """哪些模板的比例与目标不一致（要被跳过）。"""
    out = []
    for name, a in template_aspects:
        if not aspect_compatible(a, target, tol):
            out.append((name, float(a), abs(a - target) / min(a, target)))
    out.sort(key=lambda t: -t[2])
    return out


def plan_for(space, source_size, template_aspects, tol=ASPECT_TOL):
    """决定本次会话的宽高比方案。

    space:            特征空间（"2d" / "3d"）
    source_size:      视频源的真实画面尺寸 (宽, 高)，未知可传 None
    template_aspects: [(模板名, frame_aspect 或 None), ...]，只含当前空间下
                      通过 template_compatible 的模板
    """
    aspect = aspect_of(source_size)
    unmarked = sum(1 for _, a in template_aspects if not a)
    groups = group_aspects([a for _, a in template_aspects])

    # 3d 特征用米制三维坐标，本来就不受宽高比影响。补边会改变 mediapipe 看到的
    # 画面（世界关键点由裁剪区域推断），反而引入未知变化——因此不动。
    if str(space).lower() == "3d":
        return AspectPlan(aspect, None, False,
                          note="3d 特征空间用米制三维坐标，不受宽高比影响，不补边",
                          unmarked=unmarked, groups=groups)

    if aspect is None:
        return AspectPlan(aspect, None, False,
                          note="拿不到视频源分辨率，无法比对宽高比（无画面？）",
                          unmarked=unmarked, groups=groups)

    if not groups:
        return AspectPlan(aspect, None, False,
                          note="库中没有记录宽高比的模板，无需归一化",
                          unmarked=unmarked, groups=groups)

    # 目标比例取票数最多的那一组；票数相同时取离本源最近的（少补一点）
    top = [g for g in groups if g["count"] == groups[0]["count"]]
    target = min(top, key=lambda g: abs(g["aspect"] - aspect))["aspect"]

    skipped = _skip_list(template_aspects, target, tol)
    rel = abs(aspect - target) / min(aspect, target)
    if rel <= tol:
        return AspectPlan(aspect, target, False,
                          note=f"源宽高比 {aspect:.3f} 与模板 {target:.3f} 一致，"
                               f"无需补边",
                          unmarked=unmarked, skipped=skipped, groups=groups)

    note = (f"源宽高比 {aspect:.3f} → 补边到模板的 {target:.3f}"
            f"（相差 {rel * 100:.0f}%）；补边只加黑边、不改人的像素尺寸，"
            f"使 x/W 与 y/H 的分母关系与录模板时一致")
    return AspectPlan(aspect, target, True, note=note,
                      unmarked=unmarked, skipped=skipped, groups=groups)


def select(space, frame_size, templates, tol=ASPECT_TOL):
    """从模板列表定出方案并挑出必须剔除的模板。

    templates: [(key, 名称, frame_aspect 或 None), ...]，key 由调用方定义
               （平台侧是 action_id）。**只传已经通过 template_compatible 的模板**。

    返回 (plan, [(key, 名称, 相对差异), ...])。

    放在这里而不是各调用方各写一遍：`monitor.py` 与 `backend/vision/monitor_session.py`
    的匹配器是两份独立实现，同一套决策逻辑写两遍迟早会分叉——那时候"同一个动作
    在命令行能识别、在平台里识别不出"这类问题就会出现，而且极难查。
    """
    plan = plan_for(space, frame_size, [(n, a) for _k, n, a in templates], tol)
    dropped = []
    for key, name, a in templates:
        if not aspect_compatible(a, plan.target_aspect, tol):
            dropped.append((key, name, abs(a - plan.target_aspect)
                            / min(a, plan.target_aspect)))
    dropped.sort(key=lambda t: -t[2])
    return plan, dropped


def describe(plan, dropped=()):
    """打印方案与剔除情况。剔除必须说出来——静默失效是最难查的一类问题。"""
    src = f"{plan.source_aspect:.3f}" if plan.source_aspect else "未知"
    print(f"  [宽高比] 源比例 {src}：{plan.note}")
    if dropped:
        names = "、".join(f"{n}({r * 100:.0f}%)" for _k, n, r in dropped)
        print(f"  [模板跳过] 与目标比例 {plan.target_aspect:.3f} 不一致，"
              f"已剔除：{names}")
        print("    2d 特征在错配下是系统性偏移（不是噪声），匹配结果不可信；"
              "请统一录制比例后重录，或改用 3d 特征空间")
    for line in plan.warns():
        print(f"  [宽高比告警] {line}")


def pad_to_aspect(frame, target_aspect):
    """把画面居中补边到目标宽高比，返回新画面（不缩放、不裁剪）。

    只在必要时补：两侧差得极小时直接返回原画面。
    """
    if frame is None or not target_aspect or target_aspect <= 0:
        return frame
    h, w = frame.shape[:2]
    if h <= 0 or w <= 0:
        return frame
    cur = w / h
    rel = abs(cur - target_aspect) / min(cur, target_aspect)
    if rel <= ASPECT_TOL:
        return frame

    if target_aspect > cur:
        # 画面太窄 -> 左右补
        new_w = int(round(h * target_aspect))
        left = (new_w - w) // 2
        right = new_w - w - left
        pad = ((0, 0), (left, right)) + ((0, 0),) * (frame.ndim - 2)
    else:
        # 画面太宽 -> 上下补
        new_h = int(round(w / target_aspect))
        top = (new_h - h) // 2
        bottom = new_h - h - top
        pad = ((top, bottom), (0, 0)) + ((0, 0),) * (frame.ndim - 2)

    if cv2 is not None:
        return cv2.copyMakeBorder(frame, pad[0][0], pad[0][1], pad[1][0],
                                  pad[1][1], cv2.BORDER_CONSTANT, value=0)
    return np.pad(frame, pad, mode="constant", constant_values=0)


def normalized_pad(pts, source_aspect, target_aspect):
    """把归一化关键点按「补边到目标比例」换算（供离线分析与测试使用）。

    补边后 x 的归一化分母从 `W_s` 变成 `W_t' = A_t · H_s`，因此在新画布里
    `x = x_s · A_s / A_t + 偏移`；y 的分母不变。偏移是为了居中，**做差时会消掉**，
    所以对特征（全是相对量）没有影响，但仍按真实补边补上以便逐点比对。
    """
    if not source_aspect or not target_aspect:
        return pts
    ratio = source_aspect / target_aspect
    off = (1.0 - ratio) / 2.0
    out = []
    for pt in pts:
        if len(pt) >= 3:
            out.append((pt[0] * ratio + off, pt[1], pt[2]))
        else:
            out.append((pt[0] * ratio + off, pt[1]))
    return out
