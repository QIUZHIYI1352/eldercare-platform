"""对比评估核心逻辑：步骤分类 + 每步 DTW 质量分。

标准步骤序列 vs 实际识别事件序列采用「带占用标记的贪心顺序匹配」：
按标准顺序为每步寻找其动作的下一个未占用事件 → matched；
动作执行过但没排进正确位置 → order_error；动作从未出现或次数不足 → missed。
不用 LCS：种子流程里同一动作会重复出现，LCS 无法区分「缺一次执行」和「乱序」。
"""
import numpy as np

from backend.vision.template_matcher import dtw_distance


def classify_steps(step_actions, events):
    """events: [{action_id, start_idx, end_idx}, ...]（按时间升序）"""
    m = len(events)
    used = [False] * m
    ptr = 0
    outcomes = []
    for order, aid in enumerate(step_actions, 1):
        ev_idx = None
        for j in range(ptr, m):
            if not used[j] and events[j]["action_id"] == aid:
                ev_idx = j
                break
        if ev_idx is not None:
            used[ev_idx] = True
            ptr = ev_idx + 1
            outcomes.append({"order": order, "action_id": aid,
                             "result": "matched", "event_index": ev_idx})
        else:
            outcomes.append({"order": order, "action_id": aid,
                             "result": "pending", "event_index": None})

    for o in outcomes:
        if o["result"] != "pending":
            continue
        ev_idx = None
        for j in range(m):
            if not used[j] and events[j]["action_id"] == o["action_id"]:
                ev_idx = j
                break
        if ev_idx is not None:
            used[ev_idx] = True
            o["result"] = "order_error"
            o["event_index"] = ev_idx
        else:
            o["result"] = "missed"
    return outcomes


def segment_score(segment_vectors, template_vectors, threshold=8.0):
    """DTW 距离按模板长度归一后映射为 0~100 质量分。"""
    s1 = np.asarray(segment_vectors, dtype=np.float32)
    s2 = np.asarray(template_vectors, dtype=np.float32)
    if len(s1) < 1 or len(s2) < 2:
        return None
    dist = dtw_distance(s1, s2) / len(s2)
    score = 100.0 * (1.0 - min(dist / max(float(threshold), 1e-9), 1.0))
    return round(max(0.0, score), 1)


def _template_of(action):
    """取动作的序列模板向量；无标准返回 []。"""
    if not action or action.get("template_type") != "sequence":
        return []
    td = action.get("template_data") or {}
    vectors = td.get("vectors") or []
    return vectors if len(vectors) >= 2 else []


def build_assessment(steps, actions_map, events, frames):
    """组装报告数据。

    steps: [{order, name, action_id}]（流程步骤快照）
    actions_map: {action_id: action}（含 template_type/template_data，由调用方从库中加载）
    events: [{action_id, start_idx, end_idx}]，索引指向 frames["feats"]
    frames: {"ts": [...], "feats": [[16 维], ...]}
    返回 (steps_snapshot, segments, score)。
    """
    step_actions = [s["action_id"] for s in steps]
    outcomes = classify_steps(step_actions, events)
    ts = frames.get("ts") or []
    feats = frames.get("feats") or []

    scored = []
    segments = []
    for step, outcome in zip(steps, outcomes):
        action = actions_map.get(step["action_id"]) or {}
        template = _template_of(action)
        td = action.get("template_data") or {}
        threshold = float(td.get("threshold", 8.0))
        ev_idx = outcome["event_index"]
        seg = {"order": step["order"], "name": step.get("name", ""),
               "action_id": step["action_id"], "result": outcome["result"],
               "event_index": ev_idx, "start_idx": None, "end_idx": None,
               "start_ts": None, "end_ts": None, "dist": None,
               "score": None, "comparable": bool(template)}

        if ev_idx is not None and 0 <= ev_idx < len(events):
            ev = events[ev_idx]
            start, end = ev["start_idx"], ev["end_idx"]
            seg["start_idx"], seg["end_idx"] = start, end
            if 0 <= start < len(ts):
                seg["start_ts"] = round(ts[start], 2)
            if 0 <= end < len(ts):
                seg["end_ts"] = round(ts[end], 2)

        if template:
            if outcome["result"] == "matched" and ev_idx is not None:
                segment = feats[seg["start_idx"]:seg["end_idx"] + 1]
                if len(segment) >= 1:
                    seg["score"] = segment_score(segment, template, threshold)
                    seg["dist"] = round(
                        float(dtw_distance(
                            np.asarray(segment, dtype=np.float32),
                            np.asarray(template, dtype=np.float32)) / len(template)), 2)
            else:
                seg["score"] = 0.0
            scored.append(seg["score"] if seg["score"] is not None else 0.0)
        segments.append(seg)

    score = round(sum(scored) / len(scored), 1) if scored else None
    snapshot = [{"order": s["order"], "name": s.get("name", ""),
                 "action_id": s.get("action_id", "")} for s in steps]
    return snapshot, segments, score
