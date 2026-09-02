"""动作识别器：基于关节特征的规则匹配，支持用户自主添加动作模板。

动作模板结构（actions 表中的 conditions JSON）:
[
    {"joint": "trunk_inclination", "op": ">=", "value": 30},
    {"joint": "right_knee_angle", "op": "<=", "value": 100}
]
duration 为需持续满足的秒数。
"""
import time

OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: abs(a - b) < 1e-6,
    "!=": lambda a, b: abs(a - b) >= 1e-6,
}


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
    """对连续帧特征做动作判定，维护「持续满足时长」避免单帧抖动误判。"""

    def __init__(self):
        # action_id -> {hold_start: float, active: bool}
        self._state = {}

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
            st = self._state.setdefault(aid, {"hold_start": None, "active": False})
            if satisfied:
                if st["hold_start"] is None:
                    st["hold_start"] = now_ts
                st["active"] = (now_ts - st["hold_start"]) >= duration
            else:
                st["hold_start"] = None
                st["active"] = False
            if st["active"]:
                active.append(aid)
        return active
