"""骨骼序列动作模板录制脚本（cv2 + mediapipe）

录制一段护理动作的骨骼序列，保存为「序列型」动作模板，
用于 DTW 动态时间规整的精细动作匹配（如翻身、拍背、环抱转移）。

用法：
    python record_template.py --name "协助翻身动作" --duration 5
    python record_template.py --name "拍背排痰" --source rtsp://user:pwd@ip:554/stream1 --duration 6
    python record_template.py --name "环抱转移" --action-id act_xxx   # 覆盖已有动作的模板

流程：打开视频源 → 倒计时 → 按提示在镜头前完成该动作 → 自动保存模板。
"""
import argparse
import sys
import time

import cv2
import numpy as np

from backend import database as db
from backend.vision.pose_engine import PoseEngine
from backend.vision.template_matcher import features_to_vector, down_sample
from backend.vision.video_source import VideoSource


def main():
    db.init_db()
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="动作名称")
    parser.add_argument("--source", default="0", help="视频源: 0 / rtsp:// / http:// / 文件路径")
    parser.add_argument("--duration", type=float, default=5.0, help="录制时长(秒)")
    parser.add_argument("--threshold", type=float, default=8.0, help="DTW 归一化距离阈值")
    parser.add_argument("--countdown", type=float, default=3.0, help="开始前倒计时(秒)")
    parser.add_argument("--frames", type=int, default=40, help="下采样目标帧数")
    parser.add_argument("--category", default="通用")
    parser.add_argument("--description", default="")
    parser.add_argument("--action-id", default=None, help="更新已有动作时传入其 id")
    args = parser.parse_args()

    engine = PoseEngine()
    cap = VideoSource(args.source)
    if not cap.open():
        print(f"无法打开视频源: {args.source}")
        return

    print(f"\n录制动作: {args.name}")
    print(f"倒计时 {args.countdown} 秒后开始，请在镜头前完整演示该动作（约 {args.duration} 秒）")

    vectors = []
    state = "countdown"
    start_ts = None
    last_frame = None

    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.05)
            continue
        if args.source.isdigit():
            frame = cv2.flip(frame, 1)
        display = frame.copy()
        h, w = frame.shape[:2]

        ok_pose, pts = engine.landmarks_from_frame(frame)
        now = time.time()

        if state == "countdown":
            remaining = args.countdown - (now - (start_ts or now))
            if start_ts is None:
                start_ts = now
                remaining = args.countdown
            if remaining <= 0:
                state = "recording"
                rec_start = time.time()
            else:
                cv2.putText(display, f"准备: {int(remaining) + 1} 秒后开始录制",
                            (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
        elif state == "recording":
            elapsed = time.time() - rec_start
            if ok_pose:
                features = PoseEngine.compute_features(pts)
                vectors.append(features_to_vector(features))
            cv2.putText(display, f"录制中... {elapsed:.1f}s / 已采集 {len(vectors)} 帧",
                        (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
            if elapsed >= args.duration:
                state = "done"
                break

        if ok_pose:
            engine.draw(display, pts)
        cv2.imshow("录制动作模板 (q退出)", display)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            state = "abort"
            break

    cap.release()
    engine.close()
    cv2.destroyAllWindows()

    if state == "abort":
        print("已取消录制")
        return
    if state != "done" or len(vectors) < 2:
        print(f"录制的有效骨骼帧不足（{len(vectors)} 帧），请确保全身入镜并完整演示动作")
        return

    # 下采样到固定帧数
    vectors = down_sample(vectors, args.frames).tolist()
    template_data = {"vectors": vectors, "threshold": args.threshold, "frames": len(vectors)}

    if args.action_id:
        row = db.query_one("SELECT * FROM actions WHERE id = ?", (args.action_id,))
        if not row:
            print(f"动作 {args.action_id} 不存在")
            return
        db.execute(
            "UPDATE actions SET template_type=?, template_data=?, name=?, category=?, description=? WHERE id=?",
            ("sequence", db.json_dump(template_data),
             args.name, args.category, args.description or row["description"], args.action_id),
        )
        print(f"已更新动作 {args.name} 的骨骼序列模板（{len(vectors)} 帧）")
    else:
        aid = db.gen_id("act")
        db.execute(
            "INSERT INTO actions (id, name, category, description, conditions, duration, sample_ref, template_type, template_data, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (aid, args.name, args.category, args.description, "[]", 1.0, "",
             "sequence", db.json_dump(template_data), "recorder", db.now()),
        )
        print(f"已保存序列动作模板: {args.name} (id={aid}, {len(vectors)} 帧, 阈值 {args.threshold})")


if __name__ == "__main__":
    main()
