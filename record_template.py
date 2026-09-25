"""骨骼序列动作模板录制脚本（cv2 + mediapipe）

录制一段护理动作的骨骼序列，保存为「序列型」动作模板，
用于 DTW 动态时间规整的精细动作匹配（如翻身、拍背、环抱转移）。

用法：
    python record_template.py --name "协助翻身动作" --duration 5
    python record_template.py --name "拍背排痰" --source rtsp://user:pwd@ip:554/stream1 --duration 6
    python record_template.py --name "环抱转移" --action-id act_xxx   # 覆盖已有动作的模板
    python record_template.py --name "翻身" --space 3d                # 录「机位无关」模板

特征空间（--space）：
    2d  图像空间特征。机位必须固定；换个机位就得重录。
    3d  三维机位无关特征。**多机位可共用同一套模板**，是解决
        "换机位就要重录" 的方案。需要 mediapipe 提供 pose_world_landmarks。

流程：打开视频源 → 倒计时 → 按提示在镜头前完成该动作 → 自动保存模板。
"""
import argparse
import sys
import time

import cv2
import numpy as np

import config
from backend import database as db
from backend.vision.pose_engine import PoseEngine
from backend.vision.template_matcher import (ANKLE_DEPENDENT, FEATURE_VERSION,
                                             DEFAULT_THRESHOLD, SPACE_3D,
                                             down_sample, features_to_vector,
                                             validate_exclude)
from backend.vision.video_source import VideoSource, use_video_clock


def main():
    db.init_db()
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="动作名称")
    parser.add_argument("--source", default="0", help="视频源: 0 / rtsp:// / http:// / 文件路径")
    parser.add_argument("--duration", type=float, default=5.0, help="录制时长(秒)")
    parser.add_argument("--threshold", type=float, default=None,
                        help="DTW 归一化距离阈值（默认按特征空间取值：2d=8.0, 3d=40.0）")
    parser.add_argument("--space", default=None, choices=["2d", "3d"],
                        help="特征空间：2d=机位必须固定；3d=多机位可共用同一套模板")
    parser.add_argument("--countdown", type=float, default=3.0, help="开始前倒计时(秒)")
    parser.add_argument("--frames", type=int, default=40, help="下采样目标帧数")
    parser.add_argument("--category", default="通用")
    parser.add_argument("--description", default="")
    parser.add_argument("--action-id", default=None, help="更新已有动作时传入其 id")
    parser.add_argument("--no-ankle", action="store_true",
                        help="该动作不使用依赖踝关节的维度（脚可以不在画面里，"
                             "但**必须拍到大腿**）")
    parser.add_argument("--exclude", default="",
                        help="要排除的维度名，逗号分隔（该动作不依赖它们）")
    args = parser.parse_args()

    space = (args.space or getattr(config, "FEATURE_SPACE", "2d")).strip().lower()
    if args.threshold is None:
        args.threshold = DEFAULT_THRESHOLD.get(space, 8.0)

    # 该动作不使用的维度：录制侧声明、运行时从模板数据里读，两边必须一致
    exclude = list(validate_exclude(
        [d for d in args.exclude.split(",") if d.strip()], space))
    if args.no_ankle:
        for d in ANKLE_DEPENDENT.get(space, ()):
            if d not in exclude:
                exclude.append(d)
    exclude = validate_exclude(exclude, space)

    cap = VideoSource(args.source)
    if not cap.open():
        print(f"无法打开视频源: {args.source}")
        return
    # 用源的真实帧率初始化（VIDEO 模式据此生成帧间时间戳）；
    # 录制模板与运行时必须同为 VIDEO 模式，特征的时间平滑才一致。
    engine = PoseEngine(fps=cap.fps)

    print(f"\n录制动作: {args.name}")
    print(f"特征空间: {space}（{'机位无关，可多机位共用' if space == SPACE_3D else '机位需固定'}）")
    if exclude:
        print(f"不使用这些维度（脚可以不在画面里）: {', '.join(exclude)}")
        if args.no_ankle and space == SPACE_3D:
            print("  → 用「大腿相对躯干」判定，因此**必须拍到大腿（膝盖入画）**，"
                  "只拍腰以上是测不到的")
            print("  → 已知代价：该量同时被躯干前倾驱动，浅蹲与轻弯腰数值接近，"
                  "跨动作容易混淆；需用真实素材标定阈值")
    print(f"倒计时 {args.countdown} 秒后开始，请在镜头前完整演示该动作（约 {args.duration} 秒）")

    vectors = []
    state = "countdown"
    start_ts = None
    last_frame = None
    saw_world = False
    # 文件源按「视频时间轴」计时，摄像头/流按墙钟。
    # 用墙钟量文件会造成「--duration 5 实际覆盖 4~9 秒不等的素材」，
    # 详见 video_source.use_video_clock 的说明。
    video_clock = use_video_clock(args.source)
    frame_idx = 0
    frame_wh = None          # 录制时的画面尺寸，用于事后比对宽高比

    while True:
        ok, frame = cap.read()
        if not ok:
            if cap.eof:
                print("\n视频文件已读完，结束采集")
                break
            time.sleep(0.05)
            continue
        frame_idx += 1
        if args.source.isdigit():
            frame = cv2.flip(frame, 1)
        display = frame.copy()
        h, w = frame.shape[:2]
        if frame_wh is None:
            frame_wh = (w, h)

        ok_pose, pts, pts3d = engine.detect(frame)
        now = (frame_idx / (cap.fps or 25.0)) if video_clock else time.time()

        if state == "countdown":
            remaining = args.countdown - (now - (start_ts or now))
            if start_ts is None:
                start_ts = now
                remaining = args.countdown
            if remaining <= 0:
                state = "recording"
                rec_start = now
            else:
                cv2.putText(display, f"准备: {int(remaining) + 1} 秒后开始录制",
                            (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
        elif state == "recording":
            elapsed = now - rec_start
            if ok_pose:
                if space == SPACE_3D:
                    if pts3d:
                        saw_world = True
                        features = PoseEngine.compute_features_3d(pts3d)
                        vectors.append(features_to_vector(features, space, exclude))
                else:
                    features = PoseEngine.compute_features(pts)
                    vectors.append(features_to_vector(features, space, exclude))
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
        need = ("确保**大腿（膝盖）入画**" if exclude else "确保全身入镜")
        print(f"录制的有效骨骼帧不足（{len(vectors)} 帧），请{need}并完整演示动作")
        return

    if space == SPACE_3D and not saw_world:
        print("!! 当前 mediapipe 未返回 pose_world_landmarks（三维关键点），无法录制 3D 模板。")
        print("   请确认 mediapipe 版本，或改用 --space 2d。")
        return

    # 下采样到固定帧数
    vectors = down_sample(vectors, args.frames).tolist()
    template_data = {
        "vectors": vectors,
        "threshold": args.threshold,
        "frames": len(vectors),
        "space": space,
        "version": FEATURE_VERSION.get(space, 1),
        # 记录模板是用哪个推理模式录的。IMAGE 模式约 3.7% 的帧会失稳
        # （见 pose_engine），而下采样到几十帧后失稳向量有相当概率留在模板里，
        # 会永久拉偏 DTW。存下这个字段，运行时才能识别并提示重录。
        "engine_mode": engine.mode,
        # 录制时的画面尺寸。2d 空间的关节角由归一化坐标算出，而 mediapipe 用
        # x/W、y/H 两个不同分母——**宽高比一变，角度就系统性偏移**（实测
        # 16:9 -> 竖屏 9:16：躯干角 6.8° 变 20.7°，膝关节角 171° 变 154°，
        # 等于虚报了一个屈膝动作）。分辨率高低本身无影响，只与比例有关。
        # 存下它，才能在接入新视频源（尤其手机串流）时比对出这种静默偏移。
        "frame_size": list(frame_wh) if frame_wh else None,
        "frame_aspect": (round(frame_wh[0] / frame_wh[1], 3)
                         if frame_wh and frame_wh[1] else None),
        # 本模板**不使用**的维度（该动作不依赖它们，例如 --no-ankle 排除踝相关量）。
        # 运行时必须读它并做同样处理，否则那些维度上「真实值 − 模板均值 0」会
        # 凭空产生距离，把命中变成未命中。空列表也要写，便于区分"没声明"与"无"。
        "excluded_dims": list(exclude),
    }

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
        print(f"已更新动作 {args.name} 的骨骼序列模板"
              f"（{len(vectors)} 帧, 空间 {space}, 阈值 {args.threshold}）")
    else:
        aid = db.gen_id("act")
        db.execute(
            "INSERT INTO actions (id, name, category, description, conditions, duration, sample_ref, template_type, template_data, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (aid, args.name, args.category, args.description, "[]", 1.0, "",
             "sequence", db.json_dump(template_data), "recorder", db.now()),
        )
        print(f"已保存序列动作模板: {args.name} "
              f"(id={aid}, {len(vectors)} 帧, 空间 {space}, 阈值 {args.threshold})")

    print("\n提示：阈值请用 calibrate_template.py 在本机实拍标定后再固化——")
    print("      仿真标定的 3d 阈值 40.0 是起点，不是实测值。")


if __name__ == "__main__":
    main()
