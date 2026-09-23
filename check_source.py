"""视频源体检：在把某个源接进「监控设备」之前，先确认它到底能不能用。

为什么需要它：视频源的坑几乎都是**静默**的——能打开但帧率只有 3fps、
画面能看但姿态检出率只有 40%、宽高比与录模板时不同导致角度系统性偏移。
这些在网页上只表现为「识别不准」，很难反查到根因。

支持 摄像头索引 / rtsp:// / http(s)://（含手机串流）/ 本地文件。

用法：
    python check_source.py --source 0
    python check_source.py --source http://192.168.1.23:8080/video   # 手机
    python check_source.py --source rtsp://user:pwd@192.168.1.10:554/stream1
    python check_source.py --source demo.mp4
"""
import argparse
import statistics
import sys
import time

sys.path.insert(0, ".")

import cv2  # noqa: E402

import config  # noqa: E402
from backend.vision.pose_engine import PoseEngine  # noqa: E402
from backend.vision.template_matcher import SPACE_2D, SPACE_3D  # noqa: E402
from backend.vision.video_source import VideoSource  # noqa: E402

# 判定门槛（保守，宁可提示风险也不要放过）
MIN_FPS = 12.0            # 低于此值 hold-duration 类判定会明显变钝
WARN_FPS = 20.0
MIN_POSE_RATE = 0.90      # 姿态检出率低于此值基本没法用
WARN_POSE_RATE = 0.98
MIN_VISIBILITY = 0.80
WARN_JITTER_MS = 120.0    # 帧间隔抖动中位数

KNOWN_CAMERA_APPS = {
    8080: "IP Webcam（Android）",
    8081: "IP Webcam 备用端口",
    4747: "DroidCam",
    8082: "部分 RTSP App",
}


def fmt(v, unit="", nd=2):
    return f"{v:.{nd}f}{unit}"


def check(source, frames=60, timeout=20.0, skip_pose=False, space=None):
    space = space or getattr(config, "FEATURE_SPACE", SPACE_2D)
    problems, warns = [], []

    print("=" * 70)
    print(f"视频源体检：{source}")
    print("=" * 70)

    vs = VideoSource(source)
    kind = ("本机摄像头" if vs.is_camera else "RTSP 流" if vs.is_rtsp
            else "HTTP 流" if vs.is_http else "本地视频文件")
    print(f"  类型识别      {kind}")
    print(f"  EOF 语义      {'文件（读完即结束）' if vs.is_file else '实时（断流则重连）'}")

    t0 = time.time()
    ok = vs.open()
    open_ms = (time.time() - t0) * 1000
    if not ok:
        print(f"\n✗ 无法打开（耗时 {open_ms:.0f} ms）")
        print("  手机上请确认：① 与电脑在同一 WiFi；② App 的串流已启动；")
        print("  ③ 路由器未开启「AP 隔离」；④ 地址/端口与 App 显示的一致。")
        vs.release()
        return False
    print(f"  打开耗时      {fmt(open_ms, ' ms', 0)}")

    W = int(vs.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    H = int(vs.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    declared_fps = vs.fps
    if W and H:
        aspect = W / H
        orient = "横屏" if W >= H else "竖屏"
        print(f"  分辨率        {W}x{H}（{orient}，宽高比 {aspect:.3f}）")
    else:
        aspect = None
        print("  分辨率        上游未声明（以实际帧为准）")

    # ---------------- 采帧 ----------------
    intervals, sizes = [], []
    pose_hits = 0
    vis_sum = 0.0
    vis_n = 0
    read_fail = 0
    got = 0
    engine = None if skip_pose else PoseEngine(fps=declared_fps)
    t0 = time.time()
    prev = None
    while got < frames:
        if time.time() - t0 > timeout:
            warns.append(f"采集超时（{timeout:.0f}s 内只拿到 {got} 帧）——"
                         f"源可能很慢或已断流")
            break
        ok, frame = vs.read()
        now = time.time()
        if not ok:
            if vs.eof:
                break
            read_fail += 1
            if read_fail > 30:
                problems.append("连续读失败 30 次，源不可用")
                break
            time.sleep(0.02)
            continue
        got += 1
        if prev is not None:
            intervals.append((now - prev) * 1000)
        prev = now
        sizes.append((frame.shape[1], frame.shape[0]))
        if engine is not None:
            okp, pts, pts3d = engine.detect(frame, int((now - t0) * 1000))
            if okp:
                pose_hits += 1
                v = PoseEngine.compute_features(pts).get("visibility")
                if v is not None:
                    vis_sum += v
                    vis_n += 1
    elapsed = time.time() - t0
    vs.release()
    if engine is not None:
        engine.close()

    if got < 5:
        problems.append(f"只采到 {got} 帧，无法评估")
        _report(problems, warns, source, space)
        return False

    print(f"  实际采到      {got} 帧，耗时 {fmt(elapsed, 's')}")

    eff_fps = got / elapsed if elapsed > 0 else 0.0
    if vs.is_file:
        # 文件是按 CPU 速度解码的，这个数只反映处理性能，
        # 与「流的帧率」是两回事——不要拿它去判断源好不好。
        print(f"  实测处理速度  {fmt(eff_fps, ' fps')}"
              f"（文件源按 CPU 速度解码，与素材帧率 {fmt(declared_fps)} 无关）")
    else:
        print(f"  实测帧率      {fmt(eff_fps, ' fps')}"
              f"（上游声明 {fmt(declared_fps, ' fps')}）")
        if eff_fps < MIN_FPS:
            problems.append(f"实测帧率仅 {eff_fps:.1f} fps（< {MIN_FPS:.0f}）——"
                            f"「持续时长」类判定会失效，动作会被漏掉")
        elif eff_fps < WARN_FPS:
            warns.append(f"实测帧率 {eff_fps:.1f} fps 偏低（< {WARN_FPS:.0f}），"
                         f"快速动作可能采不到关键帧")

    # 帧间隔抖动
    if intervals:
        jit = statistics.median(abs(i - statistics.median(intervals))
                                for i in intervals)
        mx = max(intervals)
        print(f"  帧间隔抖动    中位 {fmt(jit, ' ms', 0)}，最大 {fmt(mx, ' ms', 0)}")
        if jit > WARN_JITTER_MS and not vs.is_file:
            warns.append(f"帧间隔抖动中位 {jit:.0f} ms 偏大——"
                         f"手机端省电/丢帧或 WiFi 不稳，会造成动作时长判定飘忽")

    # 分辨率一致性（中途变化说明流在自适应，会让特征偏移）
    if len(set(sizes)) > 1:
        problems.append(f"采集期间分辨率发生变化 {sorted(set(sizes))}——"
                        f"宽高比一变，2d 特征就会系统性偏移，绝不能用于匹配")

    # ---------------- 姿态 ----------------
    if engine is None:
        print("  姿态检测      已跳过（--no-pose）")
        rate = None
    else:
        rate = pose_hits / got
        print(f"  姿态检出率    {fmt(rate * 100, '%', 1)}（{pose_hits}/{got}）")
        if vis_n:
            mv = vis_sum / vis_n
            print(f"  平均关键点可见度 {fmt(mv, '', 3)}")
            if mv < MIN_VISIBILITY:
                warns.append(f"平均可见度 {mv:.3f} 偏低——"
                             f"可能离得太远、逆光或被遮挡")
        if rate < MIN_POSE_RATE:
            problems.append(f"姿态检出率仅 {rate * 100:.1f}%——")
            problems[-1] += "请确认全身入镜（含脚），并避免逆光/过曝"
        elif rate < WARN_POSE_RATE:
            warns.append(f"姿态检出率 {rate * 100:.1f}% 略低，"
                         f"漏检的帧会让动作时长判定不完整")

    tpls, tpl_err = _load_template_aspects(space)
    if tpl_err:
        print(f"  模板比对      跳过（读库失败：{tpl_err.splitlines()[0][:60]}）")
    else:
        _compare_aspect(aspect, tpls, problems, warns)

    _report(problems, warns, source, space, aspect, (W, H))
    return not problems


def _load_template_aspects(space):
    """读出库中序列模板记录的宽高比，用于与本次视频源比对。

    返回 (列表, 错误说明)。列表元素为 (名称, 宽高比, 画面尺寸, 推理模式)。
    """
    try:
        from backend import database as db
        db.init_db()
        rows = db.query(
            "SELECT name, template_data FROM actions "
            "WHERE template_type = 'sequence' AND template_data IS NOT NULL")
    except Exception as e:
        return None, str(e)

    out = []
    for r in rows:
        try:
            td = db.json_load(r.get("template_data")) or {}
        except Exception:
            continue
        if not isinstance(td, dict):
            continue
        if str(td.get("space") or "").lower() != space:
            continue
        out.append((r.get("name") or "?", td.get("frame_aspect"),
                    td.get("frame_size"), td.get("engine_mode")))
    return out, None


def _compare_aspect(aspect, tpls, problems, warns):
    """把本次源的宽高比与模板录制时的宽高比比对。"""
    if not tpls:
        print("  模板比对      库中没有可用的序列模板，无需比对")
        return
    print(f"  模板比对      找到 {len(tpls)} 条序列模板")
    unmarked = [t for t in tpls if not t[1]]
    if unmarked:
        print(f"                其中 {len(unmarked)} 条未记录宽高比"
              f"（较早版本录制），无法比对")
    for name, ta, tsize, tmode in tpls:
        if not ta or not aspect:
            continue
        rel = abs(aspect - ta) / min(aspect, ta)
        flag = "✓" if rel <= 0.05 else ("△" if rel <= 0.30 else "✗")
        print(f"                {flag} {name}: 模板 {ta:.3f} vs 本源 "
              f"{aspect:.3f}（相差 {rel * 100:.0f}%）")
        if rel > 0.30:
            problems.append(
                f"模板「{name}」录制于宽高比 {ta:.3f}，本源为 {aspect:.3f}"
                f"（相差 {rel * 100:.0f}%）——2d 特征会系统性偏移，"
                f"匹配结果不可信。请用本源重录模板，或改用 --space 3d")
        elif rel > 0.05:
            warns.append(
                f"模板「{name}」宽高比与本源相差 {rel * 100:.0f}%，"
                f"2d 特征会有小幅偏移，建议重录或改用 --space 3d")
        if tmode == "image":
            warns.append(f"模板「{name}」录自 IMAGE 模式（含失稳帧），"
                         f"运行时会跳过它，请用当前版本重录")


def _report(problems, warns, source, space, aspect=None, size=None):
    print("\n" + "-" * 70)
    if problems:
        print("✗ 结论：不可用（先解决下列问题）")
        for p in problems:
            print(f"    - {p}")
    elif warns:
        print("△ 结论：基本可用，但有风险项")
        for w in warns:
            print(f"    - {w}")
    else:
        print("✓ 结论：该视频源可用")

    if problems:
        return
    print("\n下一步：")
    print(f"    python monitor.py --source {source}")
    print("    （网页「监控设备」里把该地址填进 url 字段即可）")

    print("\n宽高比提醒：")
    if aspect is not None:
        print(f"    本源宽高比 {aspect:.3f}"
              f"（{size[0]}x{size[1]}）")
        print("    2d 空间（FEATURE_SPACE=2d）的关节角是按归一化坐标算的，")
        print("    而 mediapipe 用 x/W、y/H 两个不同分母——因此**宽高比一变，")
        print("    角度就系统性偏移**（实测 16:9 -> 竖屏 9:16，躯干角 6.8° 变 20.7°，")
        print("    膝关节角 171° 变 154°，等于虚报了一个屈膝动作）。")
        print("    分辨率高低本身无影响（同为 16:9 时 720p 与 1080p 完全一致）。")
    if space == SPACE_3D:
        print("    当前 FEATURE_SPACE=3d：使用米制三维坐标，**不受宽高比影响**。")
    else:
        print("    → 请确保**录模板与运行时用同一宽高比**，否则改用 --space 3d：")
        print("      FEATURE_SPACE=3d python monitor.py --source " + str(source))


def main():
    ap = argparse.ArgumentParser(description="视频源体检（接入前先跑一遍）")
    ap.add_argument("--source", required=True,
                    help="0 / rtsp:// / http:// / 文件路径")
    ap.add_argument("--frames", type=int, default=60, help="采集帧数（默认 60）")
    ap.add_argument("--timeout", type=float, default=20.0, help="采集超时秒数")
    ap.add_argument("--space", choices=[SPACE_2D, SPACE_3D], default=None,
                    help="特征空间（默认取 config.FEATURE_SPACE）")
    ap.add_argument("--no-pose", action="store_true",
                    help="只测通路，不加载姿态模型")
    args = ap.parse_args()

    space = args.space or getattr(config, "FEATURE_SPACE", SPACE_2D)
    ok = check(args.source, frames=args.frames, timeout=args.timeout,
               skip_pose=args.no_pose, space=space)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
