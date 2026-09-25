"""动作识别器：基于关节特征的规则匹配，支持用户自主添加动作模板。

动作模板结构（actions 表中的 conditions JSON）:
[
    {"joint": "trunk_inclination", "op": ">=", "value": 30},
    {"joint": "right_knee_angle", "op": "<=", "value": 100}
]
duration 为需持续满足的秒数。
"""
import time

import config

# 「持续满足时长」判定的中断容差（秒）。见 ActionRecognizer 的说明。
GAP_TOL = float(getattr(config, "HOLD_GAP_TOL", 0.4))

OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: abs(a - b) < 1e-6,
    "!=": lambda a, b: abs(a - b) >= 1e-6,
}

# 规则条件的关节名必须出现在被判定的特征字典里，否则该条件恒为 False——
# 动作会**静默失效**（既不报错也不命中），是最难排查的一类问题。
# 例：三维特征的键名是 l_knee_angle，而规则里常用的是 left_knee_angle。
def unknown_joints(conditions, available_keys):
    """返回条件里引用了、但不在 available_keys 中的关节名。"""
    out = set()
    for cond in conditions or []:
        j = cond.get("joint")
        if j and j not in available_keys:
            out.add(j)
    return sorted(out)


def evaluate_conditions(features, conditions):
    """判断当前特征是否满足一组条件（全部满足才为 True）。"""
    if not conditions:
        return False
    for cond in conditions:
        joint = cond.get("joint")
        op = cond.get("op", ">=")
        value = cond.get("value", 0)
        if joint not in features:
            return False
        if op not in OPS:
            return False
        if not OPS[op](features.get(joint, 0), value):
            return False
    return True


class ActionRecognizer:
    """对连续帧特征做动作判定，维护「持续满足时长」避免单帧抖动误判。

    ## 短暂中断不清零（治漏报）

    原实现是**严格连续**：只要有一帧不满足就把 `hold_start` 归零、从头计时。
    真人动作很难帧帧都卡在阈值同一侧——关键点抖一下、手抬过临界值再回来，
    计数器就前功尽弃。实测这是规则型动作漏报的主要来源。

    现在允许 `GAP_TOL` 秒以内的中断**保留计时**：中断帧本身照旧不算激活
    （不会因此产生误报），但恢复后接着上次的进度继续累计。
    超过 `GAP_TOL` 才算真正中断，重新计时。

    这个值是有意做得保守的（默认 0.4s）：它换来的是"容忍抖动"，
    放宽过头会让"断续地凑够时长"也算达成 —— 那是误报。
    """

    def __init__(self, gap_tol=None):
        # action_id -> {hold_start: float, gap_start: float, active: bool}
        self._state = {}
        self.gap_tol = GAP_TOL if gap_tol is None else float(gap_tol)

    def reset(self):
        self._state = {}

    def update(self, features, action_templates, now_ts=None):
        """输入一帧特征与全部动作模板，返回当前激活(达成)的动作 id 列表。

        action_templates: [{id, name, conditions, duration}]
        """
        now_ts = now_ts if now_ts is not None else time.time()
        active = []
        for tpl in action_templates:
            aid = tpl["id"]
            conditions = tpl.get("conditions") or []
            duration = float(tpl.get("duration") or 0.5)
            satisfied = evaluate_conditions(features, conditions)
            st = self._state.setdefault(
                aid, {"hold_start": None, "gap_start": None, "active": False})
            if satisfied:
                interrupted = (st["gap_start"] is not None
                               and (now_ts - st["gap_start"]) > self.gap_tol)
                if interrupted or st["hold_start"] is None:
                    st["hold_start"] = now_ts
                st["gap_start"] = None
                st["active"] = (now_ts - st["hold_start"]) >= duration
            else:
                # 中断帧本身一律不算激活（否则会凭空报出动作），
                # 但只有超过容差才作废已累计的时长。
                if st["gap_start"] is None:
                    st["gap_start"] = now_ts
                st["active"] = False
            if st["active"]:
                active.append(aid)
        return active
