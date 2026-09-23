"""在真实机位上标定序列模板的匹配阈值。

背景：仿真只能给出量级参考（3d 空间同动作约 21、无关动作约 85，对应阈值约 40）。
真实的 MediaPipe 三维估计精度和你的机位、光照、人数都有关，所以阈值必须在
**你的机器上实测**。这个脚本就是干这件事的。

用法（在同一机位、同一模板下，跑两次）：

    # 1) 做「正确动作」，量出真实匹配距离的下限
    python calibrate_template.py --action-id act_xxx --duration 6 --label right

    # 2) 做「别的动作」，量出误报的距离下限
    python calibrate_template.py --action-id act_xxx --duration 6 --label wrong

    # 两次跑完会自动给出建议阈值（取两组之间、偏保守的一侧）

判定原则：宁可漏报也不能误报 → 建议阈值取「正确动作最大值」与
「错误动作最小值」之间靠近前者的一侧（留出安全余量）。
"""
import argparse
import json
import os
import statistics
import sys
import time

import cv2

import config
from backend import database as db
from backend.vision.pose_engine import PoseEngine
from backend.vision.template_matcher import TemplateMatcher, features_to_vector
from backend.vision.video_source import VideoSource, use_video_clock

RESULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".workbuddy", "calibration.json")


def load_results():
    if os.path.exists(RESULT_FILE):
        try:
            with open(RESULT_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_results(data):
    os.makedirs(os.path.dirname(RESULT_FILE), exist_ok=True)
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    db.init_db()
    parser = argparse.ArgumentParser()
    parser.add_argument("--action-id", required=True, help="要标定的序列动作模板 id")
    parser.add_argument("--source", default="0", help="视频源: 0 / rtsp:// / http:// / 文件")
    parser.add_argument("--duration", type=float, default=6.0, help="每次演示时长(秒)")
    parser.add_argument("--label", required=True, choices=["right", "wrong"],
                        help="right=演示正确动作；wrong=演示无关动作")
    parser.add_argument("--countdown", type=float, default=3.0)
    args = parser.parse_args()

    row = db.query_one("SELECT * FROM actions WHERE id = ?", (args.action_id,))
    if not row:
        print(f"动作 {args.action_id} 不存在")
        return
    td = db.json_load(row.get("template_data")) or {}
    vectors = td.get("vectors") or []
    if len(vectors) < 2:
        print("该动作不是序列模板（没有骨骼序列数据），无法标定")
        return
    space = (td.get("space") or getattr(config, "FEATURE_SPACE", "2d")).strip().lower()
    matcher = TemplateMatcher(vectors, threshold=1e9, space=space)

    print(f"\n标定动作: {row.get('name')}")
    print(f"模板空间: {space}   模板帧数: {len(vectors)}   本次标签: {args.label}")
    print(f"倒计时 {args.countdown} 秒后开始，请演示 {args.duration} 秒\n")

    cap = VideoSource(args.source)
    if not cap.open():
        print(f"无法打开视频源: {args.source}")
        return
    # 用源的真实帧率初始化（VIDEO 模式据此生成帧间时间戳）
    engine = PoseEngine(fps=cap.fps)

    dists = []
    state = "countdown"
    # 文件源按「视频时间轴」计时，摄像头/流按墙钟（与 record_template 一致，
    # 否则标定时的录制跨度与录制模板时的跨度不可比）。详见 use_video_clock。
    video_clock = use_video_clock(args.source)
    frame_idx = 0
    start_ts = None
    rec_start = None

    while True:
        ok, frame = cap.read()
        if not ok:
            if cap.eof:
                print("\n视频文件已读完，结束标定")
                break
            time.sleep(0.03)
            continue
        frame_idx += 1
        if str(args.source).isdigit():
            frame = cv2.flip(frame, 1)
        display = frame.copy()
        ok_pose, pts, pts3d = engine.detect(frame)
        now = (frame_idx / (cap.fps or 25.0)) if video_clock else time.time()

        if state == "countdown":
            remain = args.countdown - (now - (start_ts or now))
            if start_ts is None:
                start_ts = now
                remain = args.countdown
            if remain <= 0:
                state = "recording"
                rec_start = now
            else:
                cv2.putText(display, f"准备 {int(remain) + 1}", (20, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
        else:
            if ok_pose:
                feats = (PoseEngine.compute_features_3d(pts3d)
                         if space == "3d" and pts3d else PoseEngine.compute_features(pts))
                dist, _hit = matcher.update(features_to_vector(feats, space),
                                            now_ts=now, cooldown=0.0)
                if dist != float("inf"):
                    dists.append(dist)
            el = now - rec_start
            cur = f"{dists[-1]:.1f}" if dists else "--"
            cv2.putText(display, f"标定中 {el:.1f}s  当前距离 {cur}", (20, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
            if el >= args.duration:
                break

        if ok_pose:
            engine.draw(display, pts)
        cv2.imshow("标定 (q退出)", display)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    engine.close()
    cv2.destroyAllWindows()

    if not dists:
        print("没有采集到有效骨骼帧，请确认全身入镜后重试")
        return

    dists.sort()
    n = len(dists)
    # 取第 10 百分位作为「最佳匹配水平」：动作做完一遍时必然出现最接近的时刻
    best = dists[max(0, int(n * 0.10) - 1)]
    median = statistics.median(dists)
    print(f"\n采集 {n} 帧")
    print(f"  最小距离   {dists[0]:8.2f}")
    print(f"  10% 分位   {best:8.2f}   <- 代表「动作做对了」时能达到的水平")
    print(f"  中位距离   {median:8.2f}")
    print(f"  最大距离   {dists[-1]:8.2f}")

    data = load_results()
    key = args.action_id
    data.setdefault(key, {"name": row.get("name"), "space": space})
    data[key][args.label] = {"best": best, "median": median, "n": n,
                             "threshold_at_record": td.get("threshold")}
    save_results(data)
    print(f"\n已记录到 {RESULT_FILE}")

    rec = data[key]
    if "right" in rec and "wrong" in rec:
        r, w = rec["right"]["best"], rec["wrong"]["best"]
        print("\n" + "=" * 60)
        print("标定结果")
        print("=" * 60)
        print(f"  正确动作最佳距离: {r:8.2f}")
        print(f"  无关动作最佳距离: {w:8.2f}")
        if w <= r * 1.2:
            print("  !! 两者过于接近，该动作在此机位区分度不足。")
            print("     建议：调整机位让动作更完整入镜，或改用规则型动作。")
        else:
            # 宁可漏报不误报：取偏保守（靠近正确动作一侧）的位置
            suggested = r + (w - r) * 0.35
            print(f"  建议阈值        : {suggested:8.2f}")
            print(f"      （设在正确动作上方 35% 处，留出正常动作波动余量，")
            print(f"        同时与无关动作保持 {(w - suggested) / max(suggested, 1e-6):.0%} 的间隔）")
            print(f"\n  固化该阈值：")
            print(f"    python record_template.py --name \"{row.get('name')}\" \\")
            print(f"        --action-id {args.action_id} --space {space} "
                  f"--threshold {suggested:.1f} --duration <时长>")
    else:
        missing = "wrong" if "right" in rec else "right"
        print(f"\n再做一次 --label {missing} 即可自动给出建议阈值。")


if __name__ == "__main__":
    main()
