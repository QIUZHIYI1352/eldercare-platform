"""在真实机位上标定**规则型**动作（angles 条件）的阈值。

背景：序列模板（DTW）有 calibrate_template.py 可以标定阈值，但规则型动作
（actions.conditions 里的角度/距离阈值）一直是人工拍的，于是出现
`hand_height <= -0.1`（实测量级在 -3 附近，等于「手腕不低于肩就算抬起」，
命中率 80%+）和 `hands_distance <= 0.55`（偏严）这类明显失准的阈值。

这个脚本补上这一环：分别演示「应该命中」和「不应该命中」两种情况，
采集被判定关节的真实取值分布，给出建议阈值。

用法（同一机位、同一动作，跑两次）：

    # 1) 按这个动作的定义，正确地做一遍
    python calibrate_rule.py --action-id act_xxx --label hit --duration 8

    # 2) 做「不应该被判成这个动作」的其他动作
    python calibrate_rule.py --action-id act_xxx --label miss --duration 8

    # 两次跑完自动给出每个关节的建议阈值，并打印可直接粘贴的 conditions JSON

    # 也可以不依赖已有条件，直接指定要看哪些量（用于新建动作）
    python calibrate_rule.py --joints trunk_inclination,hands_distance --label hit

原则：宁可漏报也不能误报 → 建议阈值取在「命中组最差的那一侧」与
「无关组最好的那一侧」之间、并偏向保守的一侧，留出安全余量。
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
from backend.vision.action_recognizer import evaluate_conditions
from backend.vision.pose_engine import PoseEngine
from backend.vision.video_source import VideoSource, use_video_clock

RESULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".workbuddy", "rule_calibration.json")

# 取值用的分位点：用分位而非极值，避免个别检测抖动帧（偶发关键点错位）带偏结论
HIT_SIDE_PCT = 0.92
MISS_SIDE_PCT = 0.08
# 建议阈值放在两类之间、偏向「命中侧」的比例。
# 偏保守意味着：宁可把阈值定得让真实命中更容易被漏掉，也不让它落到无关动作上。
CONSERVATIVE = 0.3


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


def pick(data, pct):
    if not data:
        return None
    s = sorted(data)
    return s[min(len(s) - 1, int(len(s) * pct))]


def suggest(conds, hit, miss):
    """按每个关节的两组取值给建议阈值。返回 (建议列表, 问题列表)。"""
    out, problems = [], []
    for c in conds:
        j, op = c.get("joint"), c.get("op", ">=")
        h, m = hit.get(j) or [], miss.get(j) or []
        if len(h) < 5 or len(m) < 5:
            problems.append(f"{j}: 采集样本不足（命中 {len(h)} 帧 / 无关 {len(m)} 帧）")
            continue
        # 命中组「最差」的一侧 = 仍希望被判中的边界；无关组「最好」的一侧 = 最容易误判的边界
        if op in ("<=", "<"):
            h_edge, m_edge = pick(h, HIT_SIDE_PCT), pick(m, MISS_SIDE_PCT)
            tighter_is = "smaller"
        else:
            h_edge, m_edge = pick(h, 1 - HIT_SIDE_PCT), pick(m, 1 - MISS_SIDE_PCT)
            tighter_is = "larger"
        gap = (m_edge - h_edge) if tighter_is == "smaller" else (h_edge - m_edge)
        if gap <= 0:
            problems.append(
                f"{j}: 两组无区分度（命中 {h_edge:.3f} vs 无关 {m_edge:.3f}，方向相反或重叠）"
                f"，该关节在此机位不能用来判定这个动作")
            continue
        if tighter_is == "smaller":
            val = h_edge + gap * CONSERVATIVE
        else:
            val = h_edge - gap * CONSERVATIVE
        out.append({
            "joint": j, "op": op,
            "value": round(val, 3),
            "old_value": c.get("value"),
            "hit_edge": round(h_edge, 3), "miss_edge": round(m_edge, 3),
            "margin": round(gap, 3),
            "hit_median": round(statistics.median(h), 3),
            "miss_median": round(statistics.median(m), 3),
        })
    return out, problems


def resolve_joints(args, row):
    if args.joints:
        return [j.strip() for j in args.joints.split(",") if j.strip()]
    conds = db.json_load(row.get("conditions")) or []
    js = []
    for c in conds:
        if c.get("joint") and c["joint"] not in js:
            js.append(c["joint"])
    return js


def print_suggestions(conds, rec, joints):
    """打印建议阈值。采集结束与 --report 两条路径共用，避免逻辑分叉。"""
    if "hit" not in rec or "miss" not in rec:
        missing = "miss" if "hit" in rec else "hit"
        print(f"\n再做一次 --label {missing} 即可自动给出建议阈值。")
        return
    print("\n" + "=" * 66)
    print("标定结果")
    print("=" * 66)
    fallback = conds or [{"joint": j, "op": ">="} for j in joints]
    sugg, problems = suggest(fallback, rec["hit"], rec["miss"])
    for s in sugg:
        flag = ""
        if s.get("old_value") is not None:
            ratio = abs(s["value"]) / max(abs(float(s["old_value"])), 1e-9)
            if ratio > 4 or ratio < 0.25:
                flag = "   <-- 与原值量级差异很大，原阈值很可能失准"
        print(f"  {s['joint']:20s} {s['op']:2s} 建议 {s['value']:>8.3f}"
              f"   (原 {s.get('old_value')})   命中侧 {s['hit_edge']:.3f} / "
              f"无关侧 {s['miss_edge']:.3f} / 间隔 {s['margin']:.3f}{flag}")
    for p in problems:
        print(f"  !! {p}")
    if sugg:
        print("\n可直接粘贴的 conditions:")
        print(json.dumps([{"joint": s["joint"], "op": s["op"], "value": s["value"]}
                          for s in sugg], ensure_ascii=False))
        print("\n固化到动作模板：在后台「培训标准 → 动作模板」里编辑该动作的条件，")
        print("或直接更新 actions.conditions 字段。")


def main():
    db.init_db()
    parser = argparse.ArgumentParser()
    parser.add_argument("--action-id", help="要标定的规则型动作 id")
    parser.add_argument("--joints", default=None,
                        help="直接指定要标定的关节名，逗号分隔（不传则取该动作条件里的关节）")
    parser.add_argument("--source", default="0", help="视频源: 0 / rtsp:// / http:// / 文件")
    parser.add_argument("--duration", type=float, default=8.0, help="每次演示时长(秒)")
    parser.add_argument("--label", choices=["hit", "miss"],
                        help="hit=正确地做该动作；miss=做不该被判成该动作的其他动作")
    parser.add_argument("--report", action="store_true",
                        help="只打印已采集到的标定结果与建议阈值，不采集（需先跑过 hit 与 miss）")
    parser.add_argument("--countdown", type=float, default=3.0)
    parser.add_argument("--headless", action="store_true",
                        help="无窗口模式：不弹预览窗。用于容器/无 GUI 环境，"
                             "或直接用已录好的视频做标定")
    args = parser.parse_args()

    if not args.action_id and not args.joints:
        print("请提供 --action-id 或 --joints（至少一个）")
        return

    # --report 只读结果文件，因此放在数据库查询之前：动作被删掉后，
    # 已经采集到的标定记录仍然应该能查看。
    if args.report:
        key = args.action_id or ("joints:" + args.joints.replace(" ", ""))
        rec = load_results().get(key)
        if not rec:
            print(f"没有找到 {key} 的标定记录，请先跑 --label hit 与 --label miss")
            return
        print(f"\n标定记录: {rec.get('name', '')}  ({key})")
        for lab in ("hit", "miss"):
            samples = rec.get(lab)
            if not samples:
                print(f"  缺少 {lab} 组，请先补跑一次 --label {lab}")
                continue
            for j, vals in samples.items():
                if vals:
                    print(f"  [{lab}] {j:20s} 中位 {statistics.median(vals):8.3f}   "
                          f"范围 {min(vals):8.3f} ~ {max(vals):8.3f}  样本 {len(vals)}")
        print_suggestions(rec.get("conditions") or [], rec,
                          list(next((rec[l] for l in ("hit", "miss") if rec.get(l)), {})))
        return

    row = {}
    if args.action_id:
        row = db.query_one("SELECT * FROM actions WHERE id = ?", (args.action_id,)) or {}
        if not row:
            print(f"动作 {args.action_id} 不存在")
            return

    joints = resolve_joints(args, row)
    if not joints:
        print("没有要标定的关节（该动作没有规则条件，请用 --joints 指定）")
        return
    conds = db.json_load(row.get("conditions")) or []
    name = row.get("name") or "(未指定动作)"
    key = args.action_id or ("joints:" + ",".join(joints))

    if not args.label:
        print("请指定 --label hit|miss（或用 --report 查看已有结果）")
        return

    print(f"\n标定动作: {name}   本次标签: {args.label}")
    print(f"采集关节: {', '.join(joints)}")
    print(f"倒计时 {args.countdown} 秒后开始，请演示 {args.duration} 秒\n")

    cap = VideoSource(args.source)
    if not cap.open():
        print(f"无法打开视频源: {args.source}")
        return
    # 用源的真实帧率初始化（VIDEO 模式据此生成帧间时间戳）
    engine = PoseEngine(fps=cap.fps)

    samples = {j: [] for j in joints}
    pass_frames = total_frames = 0
    state = "countdown"
    # 视频文件用「视频时间轴」计时，让同一段素材的标定结果可复现；
    # 摄像头/RTSP 用墙钟时间。判定规则见 video_source.use_video_clock。
    video_clock = use_video_clock(args.source)
    frame_idx = 0
    start_ts = None
    rec_start = None

    while True:
        ok, frame = cap.read()
        if not ok:
            if cap.eof:
                print("\n视频文件已读完，结束采集")
                break
            time.sleep(0.03)
            continue
        frame_idx += 1
        if str(args.source).isdigit():
            frame = cv2.flip(frame, 1)
        display = frame.copy()
        ok_pose, pts = engine.landmarks_from_frame(frame)
        now = (frame_idx / (cap.fps or 25.0)) if video_clock else time.time()

        if state == "countdown":
            if start_ts is None:
                start_ts = now
            remain = args.countdown - (now - start_ts)
            if remain <= 0:
                state = "recording"
                rec_start = now
            elif not args.headless:
                cv2.putText(display, f"准备 {int(remain) + 1}", (20, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
        else:
            if ok_pose:
                f = PoseEngine.compute_features(pts)
                total_frames += 1
                if conds and evaluate_conditions(f, conds):
                    pass_frames += 1
                for j in joints:
                    v = f.get(j)
                    if v is not None:
                        samples[j].append(float(v))
            el = now - rec_start
            if not args.headless:
                cur = "  ".join(f"{j}={samples[j][-1]:.2f}" if samples[j] else f"{j}=--"
                                for j in joints)
                cv2.putText(display, f"采集中 {el:.1f}s", (20, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
                cv2.putText(display, cur[:78], (20, 95),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
            if el >= args.duration:
                break

        if args.headless:
            continue
        if ok_pose:
            engine.draw(display, pts)
        cv2.imshow("规则标定 (q退出)", display)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    engine.close()
    if not args.headless:
        cv2.destroyAllWindows()

    if total_frames < 5:
        print("没有采集到有效骨骼帧，请确认全身入镜后重试")
        return

    print(f"\n采集 {total_frames} 帧（有效姿态）")
    if conds:
        print(f"  在【当前条件】下通过率: {pass_frames / total_frames * 100:.1f}%"
              f"（{pass_frames}/{total_frames}）")
    for j in joints:
        v = samples[j]
        if v:
            print(f"  {j:20s} 中位 {statistics.median(v):8.3f}   "
                  f"范围 {min(v):8.3f} ~ {max(v):8.3f}")

    data = load_results()
    data.setdefault(key, {"name": name})
    data[key][args.label] = {j: samples[j] for j in joints}
    data[key].setdefault("conditions", conds)
    save_results(data)
    print(f"\n已记录到 {RESULT_FILE}")

    print_suggestions(conds, data[key], joints)


if __name__ == "__main__":
    main()
