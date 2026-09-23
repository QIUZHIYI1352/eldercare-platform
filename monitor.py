"""实时监控识别脚本（cv2 + mediapipe）

用途：打开视频源（本机摄像头 / RTSP / HTTP / 视频文件），实时识别护理操作动作，
与标准护理流程比对，画面提示「已完成 / 当前 / 漏步」。

支持两类动作模板：
  - rule      角度规则（静态姿态 + 持续时长）
  - sequence  骨骼序列模板（DTW 动态时间规整，识别过程性动作）

用法：
    python monitor.py                                   # 本机摄像头，交互选择流程
    python monitor.py --source 0                        # 指定摄像头索引
    python monitor.py --source rtsp://user:pwd@ip:554/stream1
    python monitor.py --source http://ip:8080/video
    python monitor.py --source demo.mp4                 # 本地视频文件
    python monitor.py --device dev_xxx                  # 读取后台设备表中某设备的接入地址
    python monitor.py --process "协助卧床老人翻身"
    python monitor.py --save                            # 结束保存监控记录

依赖：opencv-contrib-python, mediapipe, numpy
"""
import argparse
import sys
import time

import config
from backend import database as db
from backend.vision.pose_engine import PoseEngine, DEFAULT_RUNNING_MODE
from backend.vision.action_recognizer import ActionRecognizer, unknown_joints
from backend.vision.sequence_matcher import match_sequence
from backend.vision.template_matcher import (TemplateMatcher, features_to_vector,
                                            template_compatible)
from backend.vision.video_source import VideoSource

try:
    import cv2
except Exception as e:  # pragma: no cover
    print("缺少 opencv-contrib-python，请先执行: pip install opencv-contrib-python")
    sys.exit(1)


def load_actions():
    rows = db.query("SELECT * FROM actions ORDER BY created_at")
    for a in rows:
        a["conditions"] = db.json_load(a.get("conditions"))
        a["template_data"] = db.json_load(a.get("template_data"), {})
    return rows


def load_processes():
    rows = db.query("SELECT * FROM processes ORDER BY created_at")
    for p in rows:
        p["steps"] = db.json_load(p.get("steps"))
    return rows


def pick_process(processes, name=None, pid=None):
    if pid:
        for p in processes:
            if p["id"] == pid:
                return p
    if name:
        for p in processes:
            if name in p["name"]:
                return p
    if not processes:
        return None
    print("\n可用的护理流程:")
    for i, p in enumerate(processes, 1):
        print(f"  {i}. {p['name']}  ({len(p['steps'])} 步)")
    while True:
        try:
            idx = int(input("请选择流程编号: ")) - 1
            if 0 <= idx < len(processes):
                return processes[idx]
        except (ValueError, EOFError):
            pass
        print("输入无效，请重试")


def resolve_source(args):
    """确定视频源：--device 优先，其次 --source/--camera。"""
    if args.device:
        dev = db.query_one("SELECT * FROM devices WHERE id = ?", (args.device,))
        if dev:
            print(f"使用设备: {dev['name']}  ({dev['type']}) -> {dev['url']}")
            return dev["url"] or "0"
        print(f"未找到设备 {args.device}，回退到默认摄像头")
        return "0"
    if args.source is not None:
        return args.source
    return str(args.camera)


def build_matchers(actions, space):
    """区分 rule / sequence 动作，返回 (rule_actions, seq_matchers)。

    特征空间或**特征版本**不符的序列模板会被跳过而不是硬匹配——同一空间内特征
    语义变更后旧模板的向量含义已变，拿它凑出的匹配结果就是误报。
    """
    rule_actions = []
    seq_matchers = {}
    skipped = []
    for a in actions:
        if a.get("template_type") == "sequence":
            td = a.get("template_data") or {}
            vectors = td.get("vectors") or []
            if len(vectors) < 2:
                continue
            ok, why = template_compatible(td, space, DEFAULT_RUNNING_MODE)
            if not ok:
                skipped.append((a.get("name") or a.get("id"), why))
                continue
            try:
                th = float(td.get("threshold") or 0) or None
                seq_matchers[a["id"]] = TemplateMatcher(vectors, threshold=th, space=space)
            except ValueError as e:
                skipped.append((a.get("name") or a.get("id"), str(e)))
        else:
            rule_actions.append(a)
    if skipped:
        print(f"  [模板跳过] 当前特征空间 FEATURE_SPACE={space}，以下序列模板不可用，"
              f"需用同一版本重新录制：")
        for name, why in skipped:
            print(f"    - {name}（{why}）")
    valid = set(PoseEngine.compute_features([(0.5, 0.5, 1.0)] * 33).keys())
    bad = [(a.get("name") or a.get("id"), unknown_joints(a.get("conditions"), valid))
           for a in rule_actions]
    bad = [(n, m) for n, m in bad if m]
    if bad:
        print("  [规则告警] 以下动作引用了不存在的关节名，条件将恒不成立（不会被识别）：")
        for name, miss in bad:
            print(f"    - {name}：{', '.join(miss)}")
        print(f"    可用关节名：{', '.join(sorted(valid))}")
    return rule_actions, seq_matchers


def main():
    db.init_db()
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--source", default=None, help="视频源: 0 / rtsp:// / http:// / 文件路径")
    parser.add_argument("--device", default=None, help="后台设备表中的设备 id")
    parser.add_argument("--process", default=None)
    parser.add_argument("--process-id", default=None)
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--no-overlay", action="store_true")
    parser.add_argument("--headless", action="store_true", help="无头模式：不弹窗显示，用于容器/无 GUI 环境（常配 RTSP）")
    args = parser.parse_args()

    actions = load_actions()
    if not actions:
        print("动作模板为空，请先在后台添加识别动作")
        return
    action_map = {a["id"]: a for a in actions}
    space = (getattr(config, "FEATURE_SPACE", "2d") or "2d").strip().lower()
    rule_actions, seq_matchers = build_matchers(actions, space)
    print(f"动作模板: {len(rule_actions)} 个规则型, {len(seq_matchers)} 个序列型"
          f"（特征空间 FEATURE_SPACE={space}）")

    processes = load_processes()
    proc = pick_process(processes, args.process, args.process_id)
    if not proc:
        print("没有可用流程")
        return
    step_actions = [s.get("action_id") for s in proc["steps"]]
    print(f"\n开始监控流程: {proc['name']}")
    print("步骤: " + " → ".join(s["name"] for s in proc["steps"]))
    print("按 q 退出；按 s 保存并结束\n")

    recognizer = ActionRecognizer()
    source = resolve_source(args)
    cap = VideoSource(source)
    if not cap.open():
        print(f"无法打开视频源: {source}")
        return
    # 用源的真实帧率初始化（VIDEO 模式据此生成帧间时间戳）
    engine = PoseEngine(fps=cap.fps)

    detected_seq = []       # 已识别动作 id 序列（按时间）
    last_active = set()
    active_since = {}
    seq_status = {}         # 序列动作最近一次匹配距离（用于画面显示）

    while True:
        ok, frame = cap.read()
        if not ok:
            if cap.eof:
                # 本地文件已读完：正常收尾（此前会被当成断流而无限空转）
                print("\n视频文件已播放完毕，结束监控")
                break
            # 断流重连中，短暂等待
            time.sleep(0.05)
            continue
        if source.isdigit():
            frame = cv2.flip(frame, 1)

        ok_pose, pts, pts3d = engine.detect(frame)
        active = []          # 当前激活的 rule 动作 id
        if ok_pose:
            # 规则条件写在**二维特征名**上（trunk_inclination、left_knee_angle…），
            # 三维特征键名不同（l_knee_angle…）。故规则判定固定用二维特征，
            # 与 FEATURE_SPACE 无关，否则切到 3d 空间会让规则型动作静默失效。
            feats2d = PoseEngine.compute_features(pts)
            features = (PoseEngine.compute_features_3d(pts3d)
                        if space == "3d" and pts3d else feats2d)
            # 1) 规则型动作
            active = recognizer.update(feats2d, rule_actions)
            # 2) 序列型动作（DTW）
            vec = features_to_vector(features, space)
            for aid, matcher in seq_matchers.items():
                dist, hit = matcher.update(vec)
                seq_status[aid] = dist
                if hit:
                    detected_seq.append(aid)
                    print(f"  ✓ [序列] 识别到动作: {action_map[aid]['name']} (DTW={dist:.1f})")

        # 规则动作：进入->离开 记一次完成
        active_set = set(active)
        for aid in active_set - last_active:
            active_since[aid] = time.time()
        for aid in last_active - active_set:
            if aid in active_since:
                detected_seq.append(aid)
                print(f"  ✓ [规则] 识别到动作: {action_map[aid]['name']}")
            active_since.pop(aid, None)
        last_active = active_set

        # 与流程比对
        result = match_sequence(step_actions, detected_seq)
        completed = set(result["completed_steps"])
        missed = result["missed_steps"]
        current = result["current_step"]

        # 画面叠加
        if not args.no_overlay and not args.headless:
            engine.draw(frame, pts) if ok_pose else None
            h, w = frame.shape[:2]
            panel_h = min(320, h)
            cv2.rectangle(frame, (0, 0), (w, panel_h), (20, 20, 20), -1)
            y = 28
            cv2.putText(frame, f"[{proc['name']}] 完成度 {result['score']}%",
                        (15, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            y += 28
            for s in proc["steps"]:
                order = s["order"]
                if order in completed:
                    tag, color = "✓已完成", (0, 255, 0)
                elif order == current:
                    tag, color = "▶当前", (0, 200, 255)
                elif order in missed:
                    tag, color = "⚠漏步", (0, 0, 255)
                else:
                    tag, color = "待执行", (150, 150, 150)
                cv2.putText(frame, f"{order}. {s['name']}  {tag}", (15, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
                y += 24
            # 当前激活动作
            line = ""
            if active:
                line += "规则: " + " + ".join(action_map[a]["name"] for a in active)
            for aid, dist in seq_status.items():
                if dist < 1e8:
                    hit = dist <= (action_map[aid].get("template_data") or {}).get("threshold", 8.0)
                    line += f" | {action_map[aid]['name']} DTW={dist:.0f}{'✓' if hit else ''}"
            if line:
                cv2.putText(frame, line, (15, y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            else:
                cv2.putText(frame, "未检测到人体/未匹配动作", (15, y + 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1)

        if not args.headless:
            cv2.imshow("智慧养老 - 辅助监管与培训 (q退出/s保存)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                args.save = True
                break
        else:
            time.sleep(0.03)

    # 保存记录
    if args.save:
        rid = db.gen_id("mon")
        db.execute(
            "INSERT INTO monitoring_records (id, device_id, process_id, user_id, started_at, ended_at, detected_actions, missed_steps, completed_steps, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, source, proc["id"], "", int(time.time() - 1), int(time.time()),
             db.json_dump(detected_seq), db.json_dump(missed),
             db.json_dump(sorted(completed)), "finished"),
        )
        print(f"\n监控记录已保存: {rid}")
        print(f"完成步骤: {sorted(completed)}  漏步: {missed}  得分: {result['score']}%")

    cap.release()
    engine.close()
    if not args.headless:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
