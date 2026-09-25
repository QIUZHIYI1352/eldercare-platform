"""Web 内嵌实时监控会话：后台线程读视频源 → 识别 → 输出 MJPEG 流 + 状态。

前端「实时监管」页选择设备/流程后，通过 HTTP 接口创建会话，
画面通过 <img src="/api/monitoring/sessions/{sid}/stream"> 展示，
识别状态通过轮询 /sessions/{sid}/state 获取。
"""
import threading
import time
import uuid

import config
from backend import database as db

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

from backend.vision.video_source import VideoSource
from backend.vision.pose_engine import PoseEngine, DEFAULT_RUNNING_MODE
from backend.vision.action_recognizer import ActionRecognizer, unknown_joints
from backend.vision.sequence_matcher import match_sequence
from backend.vision.template_matcher import (TemplateMatcher, features_to_vector,
                                            missing_keys, template_compatible)
from backend.vision import frame_norm
from backend.vision import feature_guard as guard_mod
from backend.vision import obs_guard


def _warn_unknown_joints(rule_actions):
    """对引用了不存在关节名的规则型动作给出告警。

    这类动作的条件恒为 False，既不报错也不命中——纯静默失效，最难排查。
    宁可启动时刷一行日志，也不要让它无声地什么都不做。
    """
    try:
        valid = set(PoseEngine.compute_features([(0.5, 0.5, 1.0)] * 33).keys())
    except Exception:
        return
    bad = []
    for a in rule_actions:
        miss = unknown_joints(a.get("conditions"), valid)
        if miss:
            bad.append((a.get("name") or a.get("id"), miss))
    if bad:
        print("  [规则告警] 以下动作引用了不存在的关节名，条件将恒不成立（不会被识别）：")
        for name, miss in bad:
            print(f"    - {name}：{', '.join(miss)}")
        print(f"    可用关节名：{', '.join(sorted(valid))}")


def _build_matchers(actions, space, engine_mode=DEFAULT_RUNNING_MODE):
    """构建匹配器。

    特征空间或**特征版本**不符的模板会被**跳过**而不是拿去硬匹配——同一空间内
    特征语义变更后，旧模板存的向量含义已经变了；宁可不报警，也不能用它凑出一个
    匹配结果（那等于误报）。
    注意：必须同时比对 space 与 version，只比 space 挡不住语义变更。

    engine_mode 用于额外拒绝「用 IMAGE 模式录的模板」——那种模板可能含失稳向量，
    会永久拉偏 DTW。详见 template_compatible。
    """
    rule, seq, skipped = [], {}, []
    for a in actions:
        if a.get("template_type") == "sequence":
            td = a.get("template_data") or {}
            vectors = td.get("vectors") or []
            if len(vectors) < 2:
                continue
            ok, why = template_compatible(td, space, engine_mode)
            if not ok:
                skipped.append((a.get("name") or a.get("id"), why))
                continue
            try:
                th = float(td.get("threshold") or 0) or None
                seq[a["id"]] = TemplateMatcher(vectors, threshold=th, space=space)
            except ValueError as e:
                skipped.append((a.get("name") or a.get("id"), str(e)))
        else:
            rule.append(a)
    if skipped:
        print(f"  [模板跳过] 以下序列模板在当前环境下不可用"
              f"（特征空间 FEATURE_SPACE={space}，推理模式 {engine_mode}），"
              f"需重新录制：")
        for name, why in skipped:
            print(f"    - {name}（{why}）")
    return rule, seq


class MonitorSession:
    def __init__(self, source, process, actions, user_id="", task_id="", assess=False):
        self.source = source
        self.process = process
        self.actions = actions
        self.user_id = user_id
        self.task_id = task_id
        self.assess = assess
        self.record_id = None
        self.record = None
        self._finish_at = 0.0
        self._rule_state = {
            a["id"]: {"begin": None, "first_active": None, "active": False}
            for a in actions if a.get("template_type") != "sequence"
        }
        self.action_map = {a["id"]: a for a in actions}
        self.space = getattr(config, "FEATURE_SPACE", "2d")
        self.rule_actions, self.seq_matchers = _build_matchers(actions, self.space)
        _warn_unknown_joints(self.rule_actions)
        # 观测门控：直接从即将参与匹配的 matcher 上取判别维度，不重读一遍模板数据
        # （两处各读一遍迟早分叉，后果是"命令行能识别、平台里识别不出"）。
        _names = {a["id"]: (a.get("name") or a["id"]) for a in actions}
        self.obs_gates = obs_guard.build_gates(self.seq_matchers, _names)
        if self.obs_gates:
            print(f"  [观测门控] 已为 {len(self.obs_gates)} 个序列模板建立"
                  f"（可见度下限 {obs_guard.VIS_LIMIT}）")
            for g in self.obs_gates.values():
                print(f"    - {g.describe()}")
        self.step_actions = [s.get("action_id") for s in process.get("steps", [])]
        self.steps = process.get("steps", [])
        if assess:
            self._frames = {"ts": [], "feats": []}
            self._events = []

        self._running = False
        self._thread = None
        self._error = None
        self._lock = threading.Lock()
        self._jpeg = None
        self._jpeg_lock = threading.Lock()
        self._jpeg_ts = 0.0
        # JPEG 编码是整条链路里第二大的开销（实测 720p 约 9.4ms/帧，占 25%）。
        # 前端是**轮询** /frame 取画面（约 120ms 一次 ≈ 8fps），
        # 因此按源帧率逐帧编码是纯浪费；更糟的是无人观看时（页面已关、
        # 或用 monitor.py 跑批）也照样在编。这里改为「有观众 + 限频」才编。
        self._last_consumer = 0.0
        self._step_order = None
        self._step_since = None
        self._off_since = None
        self.thresholds = {"step_warn": 30, "step_crit": 60, "off": 5}
        self._state = self._empty_state()
        # 失稳帧门控，_run_impl 里按源的真实帧率创建
        self._guard = None

    def _empty_state(self):
        return {
            "running": True, "error": None, "score": 0.0,
            "current_step": 0, "completed_steps": [], "missed_steps": [],
            "steps": [{"order": s["order"], "name": s["name"], "status": "pending"}
                      for s in self.steps],
            "detected_actions": [], "active_actions": [], "record_id": None,
            "step_stay": 0, "off_screen": 0, "alert_level": "ok",
            # 宽高比归一化用了什么比例；跳过/失稳计数必须外显，
            # 这些"被丢弃的帧和模板"一旦不可见，就等于静默失效。
            "aspect": None, "aspect_skipped": [], "unstable_frames": 0,
            # 观测门控与三维缺帧同样必须外显：手持跟拍下"识别不到"的真实原因
            # 往往是"画面没拍全"，不报出来就会被当成算法问题反复调阈值。
            "obs_note": None, "obs_skipped": [], "obs_frames": 0,
            "no_world_frames": 0, "missing_features": [],
        }

    def start(self):
        # TODO(时间轴): 本会话的「事件时间轴」目前**一律用墙钟**（_start_wall /
        # time.time()），包括 _frames["ts"]、停留时长 step_stay、离屏时长 off_screen。
        #
        # 对**实时摄像头**这是正确的：帧的到达节奏就等于真实时间。
        # 但对**视频文件**是错的：文件按 CPU 速度解码，处理多快就吞多少素材，
        # 实测同一段 720p 素材 5s 墙钟覆盖 8.88s 视频（VIDEO 模式）/ 4.20s（IMAGE），
        # 偏差 111%（数据见 video_source.use_video_clock）。后果是拿视频文件做
        # 评估时，step_stay / off_screen 以及 threshold 里的时长门槛会按约 1.8×
        # 的比例失真，且随机器快慢变化——与录制/标定侧已经改成视频时间轴不一致。
        #
        # 已与需求方确认「先标注不改」：改它会动到评分口径，需单独评审。
        # 录制/标定侧的同类问题已修（record_template / calibrate_template /
        # calibrate_rule 走 video_source.use_video_clock）。此处改动前请先更新
        # tests/test_engine_runtime.py::test_assessment_timeline_is_deliberately_wall_clock。
        self._start_wall = time.time()
        self._load_thresholds()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _load_thresholds(self):
        """预警阈值来自 privacy_settings（键不存在时用默认值）。"""
        try:
            rows = db.query("SELECT `key`, value FROM privacy_settings")
            conf = {r["key"]: r["value"] for r in rows}
            self.thresholds = {
                "step_warn": int(conf.get("alert_step_warn", 30)),
                "step_crit": int(conf.get("alert_step_crit", 60)),
                "off": int(conf.get("alert_off_seconds", 5)),
            }
        except Exception:
            pass

    def wait_finished(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline and self._running:
            time.sleep(0.05)
        return self.record_id

    def _frame_idx(self):
        return len(self._frames["ts"]) - 1

    def _capture_rule_events(self, features, active):
        """规则型动作：满足起始帧 → 离开帧 作为一个事件。"""
        from backend.vision.action_recognizer import evaluate_conditions
        idx = self._frame_idx()
        active_set = set(active)
        for aid, st in self._rule_state.items():
            action = self.action_map[aid]
            satisfied = evaluate_conditions(features, action.get("conditions") or [])
            if satisfied and st["begin"] is None:
                st["begin"] = idx
            if not satisfied:
                st["begin"] = None
            if aid in active_set and not st["active"]:
                st["active"] = True
                st["first_active"] = idx
            elif aid not in active_set and st["active"]:
                start = st["begin"] if st["begin"] is not None else st["first_active"]
                end = max(start, idx - 1)
                self._events.append({"action_id": aid, "start_idx": start, "end_idx": end})
                st["begin"] = None
                st["first_active"] = None
                st["active"] = False

    def _save_record(self):
        from backend.vision.assessment import build_assessment
        duration = max(1, int(time.time() - self._start_wall))
        events = sorted(self._events, key=lambda e: (e["start_idx"], e["end_idx"]))
        actions_map = {a["id"]: a for a in self.actions}
        snapshot, segments, score = build_assessment(
            self.steps, actions_map, events, self._frames)
        self.record = {"steps": snapshot, "segments": segments,
                       "score": score, "events": events, "duration": duration}
        rid = db.gen_id("cap")
        db.execute(
            "INSERT INTO motion_capture_records "
            "(id, user_id, task_id, process_id, source, source_type, status, "
            "started_at, ended_at, steps, segments, frames, score, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, self.user_id, self.task_id, self.process.get("id", ""),
             str(self.source), "mediapipe", "finished",
             int(self._start_wall), int(time.time()),
             db.json_dump(snapshot), db.json_dump(segments),
             db.json_dump(self._frames), score, db.now()),
        )
        if self.task_id:
            matched = [s["order"] for s in segments if s["result"] == "matched"]
            missed = [s["order"] for s in segments
                      if s["result"] in ("missed", "order_error")]
            trid = db.gen_id("train")
            db.execute(
                "INSERT INTO training_records "
                "(id, user_id, process_id, task_id, capture_record_id, score, "
                "completed_steps, missed_steps, duration, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (trid, self.user_id, self.process.get("id", ""), self.task_id, rid,
                 score if score is not None else 0.0,
                 db.json_dump(matched), db.json_dump(missed),
                 duration, db.now()),
            )
        self.record_id = rid
        self._set_state(record_id=rid)

    def stop(self):
        self._running = False

    @property
    def error(self):
        return self._error

    def get_jpeg(self):
        """取最新画面。调用即视为「有观众」，用于决定是否继续编码。"""
        self._last_consumer = time.time()
        with self._jpeg_lock:
            return self._jpeg, self._jpeg_ts

    def get_state(self):
        with self._lock:
            return dict(self._state)

    def _set_state(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _publish_jpeg(self, frame):
        """编码并发布最新画面；无人观看或未到预览帧率时直接跳过。

        跳过是有意为之：识别结果不受影响（识别用的是原始帧），
        只是画面刷新率降低。相比每帧都编码，可省下约 25% 的单帧开销。
        """
        now = time.time()
        # 无人观看（页面已关 / headless 跑批）→ 不编码。
        # 例外：一帧都还没有时先编一帧，保证前端首次轮询就能拿到画面。
        if self._jpeg is not None and (now - self._last_consumer) > config.PREVIEW_IDLE_SEC:
            return
        # 限频：预览不需要跟满源帧率
        min_interval = 1.0 / max(1.0, config.PREVIEW_FPS)
        if (now - self._jpeg_ts) < min_interval:
            return
        try:
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                with self._jpeg_lock:
                    self._jpeg = buf.tobytes()
                    self._jpeg_ts = time.time()
        except Exception:
            pass

    def _run(self):
        """外层兜底：帧处理异常必须打印并落状态，避免线程静默死亡。"""
        try:
            self._run_impl()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._error = str(e)
            self._set_state(running=False, error=str(e))
            self._running = False

    def _init_aspect_plan(self, frame):
        """按第一帧的尺寸定下宽高比方案，并剔除比例不符的序列模板。

        为什么等第一帧：源的真实比例只有拿到画面才知道——手机串流/网络摄像头
        会协商出与预期不同的分辨率（免费版 DroidCam 就是 4:3）。

        为什么比例不符的模板要**剔除**而不是"照样匹配"：2d 特征的偏移是
        **系统性**的，不是噪声。实测同一姿态 16:9 量到躯干角 6.8°、竖屏量到
        20.7°，膝关节角 171° → 154°——等于把站立判成屈膝。留着它只会产出误报。
        剔除后必须打印它剔了谁，否则就成了静默失效。
        """
        h, w = frame.shape[:2]
        tpls = []
        for aid in self.seq_matchers:
            a = self.action_map.get(aid) or {}
            tpls.append((aid, a.get("name") or aid,
                         (a.get("template_data") or {}).get("frame_aspect")))
        plan, dropped = frame_norm.select(self.space, (w, h), tpls)
        for aid, _name, _r in dropped:
            self.seq_matchers.pop(aid, None)
            self.obs_gates.pop(aid, None)   # 模板被剔除，门控同步移除

        frame_norm.describe(plan, dropped)
        self._set_state(aspect=plan.note,
                        aspect_skipped=[n for _k, n, _r in dropped])
        return plan

    def _note_unstable(self, why, bad):
        """失稳帧必须可见：静默修复和静默失败一样难查。

        注意这里的语义是**修复**而不是丢弃：坏值会被线性预测替代后继续参与
        序列匹配（丢帧会把 DTW 命中变成未命中，见 feature_guard.repair）。
        只有规则型判定会跳过这一帧。

        只在第一帧和每 100 帧打印一次。帧级日志在 30fps 下几分钟就是几万行，
        真出问题时反而没人看——而这几行恰恰是排查"时好时坏"的入口。
        """
        n = self._guard.gated
        if n == 1 or n % 100 == 0:
            print(f"  [失稳帧] 第 {n} 帧测量崩坏，已按预测值修复：{why}")
            if self._guard.resyncs:
                print(f"    已重同步 {self._guard.resyncs} 次")
        self._set_state(unstable_frames=n,
                        unstable_note=self._guard.summary(),
                        unstable_first=self._guard.first_bad)

    def _note_no_world(self, now_ts):
        """三维特征空间下拿不到世界关键点——必须说出来。

        这时候序列匹配整帧都被跳过。若不报，"识别不到"会被误以为是动作没做对，
        而真实原因是这条源（或这台设备）没给出 pose_world_landmarks。
        """
        self._no_world = getattr(self, "_no_world", 0) + 1
        n = self._no_world
        if n == 1 or n % 300 == 0:
            print(f"  [三维缺帧] 第 {n} 帧没有世界关键点（pose_world_landmarks），"
                  f"该帧不参与序列匹配")
        self._set_state(no_world_frames=n)

    def _note_missing(self, miss):
        """该空间的必填特征缺失——同样是静默失效的高发区，报一次就够。"""
        key = tuple(miss)
        if getattr(self, "_missing_key", None) != key:
            self._missing_key = key
            print(f"  [特征缺失] 当前特征空间缺少 {len(miss)} 个字段"
                  f"（{', '.join(miss[:6])}{'…' if len(miss) > 6 else ''}），"
                  f"该帧不参与序列匹配")
        self._set_state(missing_features=list(miss))

    def _note_obs(self, aid, low):
        """观测门控拦下的原因必须可见，否则"漏报"永远查不出根因。

        手持跟拍时人常常半身入画，此时正确的行为是**明确说不出话**：
        告诉使用者"画面里看不到左脚/右脚，暂时判不了这个动作"，
        而不是拿 mediapipe 推测出来的膝角报一个假动作。
        """
        name = (self.action_map.get(aid) or {}).get("name") or aid
        self._obs_frames = getattr(self, "_obs_frames", 0) + 1
        n = self._obs_frames
        seen = getattr(self, "_obs_seen", None)
        if seen is None:
            seen = self._obs_seen = set()
        key = (aid, tuple(low))
        first = key not in seen
        seen.add(key)
        if first or n % 300 == 0:
            print(f"  [观测门控] 画面里看不清 {'、'.join(low)}，"
                  f"「{name}」暂不参与判定（累计 {n} 帧）")
        skipped = sorted({(self.action_map.get(k) or {}).get("name") or k
                          for k, _l in seen})
        self._set_state(obs_frames=n, obs_skipped=skipped,
                        obs_note=f"画面未拍全，暂不判定：{'、'.join(skipped)}")

    def _run_impl(self):
        if cv2 is None:
            self._error = "未安装 opencv，无法启动监控"
            self._running = False
            return
        recognizer = ActionRecognizer()
        cap = VideoSource(self.source)
        if not cap.open():
            self._error = f"无法打开视频源: {self.source}"
            self._running = False
            return
        # 用视频源的真实帧率初始化：VIDEO 模式据此生成时间戳，
        # 使帧间平滑的时间尺度与实际播放一致。
        # 引擎不可用时 detect() 会返回未检出，循环照常推流画面（视觉是软依赖）。
        engine = PoseEngine(fps=cap.fps)
        # 门控按源的真实帧率建：单帧变化量上限要用 dt 换算，
        # 而手机串流实测能从 30fps 掉到 3fps。
        self._guard = guard_mod.FeatureGuard(fps=cap.fps)

        detected_seq = []
        last_active = set()
        active_since = {}
        recent_names = []
        plan = None      # 宽高比方案：拿到第一帧（知道画面尺寸）之后才能定

        while self._running:
            ok, frame = cap.read()
            if not ok:
                if cap.eof:
                    # 视频文件播完：会话正常结束（否则会一直空转到进程被强杀）
                    break
                time.sleep(0.05)
                continue
            if plan is None:
                plan = self._init_aspect_plan(frame)
            # 必须在 detect 之前补边：mediapipe 的归一化坐标以整幅画布为分母，
            # 补边之后 x/W、y/H 才与录模板时同尺度。
            frame = plan.apply(frame)
            if str(self.source).isdigit():
                frame = cv2.flip(frame, 1)

            ok_pose, pts, pts3d = engine.detect(frame)
            active = []
            gate_ok = True
            vis = {}
            if ok_pose:
                # 规则型动作的条件写在**二维特征名**上（trunk_inclination、
                # left_knee_angle…），而三维特征的键名不同（l_knee_angle…）。
                # 因此规则判定一律用二维特征，与 FEATURE_SPACE 无关；
                # 否则切到 3d 空间会让所有规则型动作静默失效。
                feats2d = PoseEngine.compute_features(pts)
                vis = obs_guard.visibility_map(pts)
                gate_ok, why, bad = self._guard.check(feats2d, time.time())
                if not gate_ok:
                    # 序列匹配要的是"时间上连续、采样完整"，丢帧会把命中变成
                    # 未命中（实测：坏 3 帧时距离 5.3→16.6，直接不命中）。
                    # 所以坏值用线性预测替代，而不是把这一帧抽掉。
                    feats2d = self._guard.repair(feats2d, bad)
                    self._note_unstable(why, bad)
            if ok_pose:
                # 三维空间必须真的拿到世界关键点，否则**跳过**序列匹配。
                # 曾经的写法是回落去喂二维特征：两个空间的 18 个维度里只有
                # hands_distance 同名，其余 17 个键全部缺失 → features_to_vector
                # 补 0.0，模板经 Z-score 归一化后得到一个"看起来很正常的向量"。
                # 实测该帧向量全为 0，不报错、不命中、也不进任何日志——
                # 这是纯粹静默的漏报来源。
                space_ok = True
                if self.space == "3d":
                    space_ok = bool(pts3d)
                    features = PoseEngine.compute_features_3d(pts3d) if space_ok else {}
                else:
                    features = feats2d
                if not space_ok:
                    self._note_no_world(time.time())
                if self.assess and space_ok:
                    # TODO(时间轴): 见 start() 的说明——文件源下墙钟会按机器快慢
                    # 失真约 1.8×，此处应先确认为何「先标注不改」再动。
                    self._frames["ts"].append(round(time.time() - self._start_wall, 3))
                    self._frames["feats"].append(
                        features_to_vector(features, self.space).tolist())
                if gate_ok:
                    # 规则型另作处理：条件是**瞬时阈值**，拿外推值去凑可能凭空
                    # 报出一个动作（误报）。所以失稳帧干脆不参与规则判定——
                    # 漏掉一帧的代价被"中断容差"吸收，见 action_recognizer。
                    active = recognizer.update(feats2d, self.rule_actions)
                if space_ok:
                    miss = missing_keys(features, self.space)
                    if miss:
                        self._note_missing(miss)
                    else:
                        vec = features_to_vector(features, self.space)
                        for aid, m in self.seq_matchers.items():
                            # 观测门控：该动作真正依赖的部位没拍全时，这一帧不参与
                            # 它的匹配。mediapipe 对画面外的关节只打低 visibility
                            # 并**推测**一个位置，拿推测值去匹配就是误报。
                            g = self.obs_gates.get(aid)
                            if g is not None:
                                obs_ok, low = g.check(pts, vis)
                                if not obs_ok:
                                    self._note_obs(aid, low)
                                    continue
                            dist, hit = m.update(vec)
                            if hit:
                                detected_seq.append(aid)
                                recent_names.append(self.action_map[aid]["name"])
                                if self.assess:
                                    idx = self._frame_idx()
                                    start = max(0, idx - m.template_len + 1)
                                    self._events.append(
                                        {"action_id": aid, "start_idx": start,
                                         "end_idx": idx})
                if self.assess and space_ok:
                    self._capture_rule_events(features, active)

            active_set = set(active)
            for aid in active_set - last_active:
                active_since[aid] = time.time()
            for aid in last_active - active_set:
                if aid in active_since:
                    detected_seq.append(aid)
                    recent_names.append(self.action_map[aid]["name"])
                active_since.pop(aid, None)
            last_active = active_set

            result = match_sequence(self.step_actions, detected_seq)
            completed = set(result["completed_steps"])
            missed = set(result["missed_steps"])

            engine.draw(frame, pts) if ok_pose else None
            self._publish_jpeg(frame)

            steps_status = [{
                "order": s["order"], "name": s["name"],
                "status": ("done" if s["order"] in completed
                           else "current" if s["order"] == result["current_step"]
                           else "miss" if s["order"] in missed else "pending"),
            } for s in self.steps]

            self._set_state(
                score=result["score"], current_step=result["current_step"],
                completed_steps=sorted(completed), missed_steps=missed,
                steps=steps_status,
                detected_actions=recent_names[-8:],
                active_actions=[self.action_map[a]["name"] for a in active],
            )

            # 停留时长 / 离屏时长 / 预警级别（供教师看板使用）
            # TODO(时间轴): 同 start() 的说明——文件源下这两项按墙钟计会失真约 1.8×。
            now_ts = time.time()
            if result["current_step"] != self._step_order:
                self._step_order = result["current_step"]
                self._step_since = now_ts
            stay = int(now_ts - (self._step_since or now_ts))
            if ok_pose:
                # 检出人就算在画面内。个别关节的测量崩坏不影响"人在不在"这个判断，
                # 因此失稳帧同样可以清掉离屏计时。
                self._off_since = None
            elif self._off_since is None:
                self._off_since = now_ts
            off = int(now_ts - self._off_since) if self._off_since else 0
            if off >= self.thresholds["off"]:
                level = "off"
            elif stay >= self.thresholds["step_crit"]:
                level = "critical"
            elif stay >= self.thresholds["step_warn"]:
                level = "warn"
            else:
                level = "ok"
            self._set_state(step_stay=stay, off_screen=off, alert_level=level)

            if self.assess and result["score"] >= 100 and not self._finish_at:
                self._finish_at = time.time() + 1.2
            if self._finish_at and time.time() >= self._finish_at:
                break

        if self.assess:
            self._save_record()
        cap.release()
        engine.close()
        self._set_state(running=False)
        self._running = False


class SessionManager:
    _instance = None

    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self, source, process, actions, user_id="", task_id="", assess=False):
        sid = "sess_" + uuid.uuid4().hex[:12]
        sess = MonitorSession(source, process, actions, user_id, task_id, assess)
        sess.start()
        with self._lock:
            self._sessions[sid] = sess
        return sid

    def stop(self, sid):
        with self._lock:
            sess = self._sessions.pop(sid, None)
        if sess:
            sess.stop()

    def get(self, sid):
        return self._sessions.get(sid)

    def list_sessions(self):
        with self._lock:
            return list(self._sessions.keys())

    def frame_generator(self, sid):
        """MJPEG 流生成器。session 停止且无帧后退出。"""
        sess = self.get(sid)
        if sess is None:
            return
        last_ts = 0.0
        while True:
            jpeg, ts = sess.get_jpeg()
            if jpeg and ts != last_ts:
                last_ts = ts
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
            elif not sess._running and jpeg is None:
                break
            time.sleep(0.08)  # ~12 fps


manager = SessionManager.instance()
