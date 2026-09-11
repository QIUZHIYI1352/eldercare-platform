"""Web 内嵌实时监控会话：后台线程读视频源 → 识别 → 输出 MJPEG 流 + 状态。

前端「实时监管」页选择设备/流程后，通过 HTTP 接口创建会话，
画面通过 <img src="/api/monitoring/sessions/{sid}/stream"> 展示，
识别状态通过轮询 /sessions/{sid}/state 获取。
"""
import threading
import time
import uuid

from backend import database as db

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

from backend.vision.pose_engine import PoseEngine
from backend.vision.action_recognizer import ActionRecognizer
from backend.vision.sequence_matcher import match_sequence
from backend.vision.template_matcher import TemplateMatcher, features_to_vector
from backend.vision.video_source import VideoSource


def _build_matchers(actions):
    rule, seq = [], {}
    for a in actions:
        if a.get("template_type") == "sequence":
            td = a.get("template_data") or {}
            vectors = td.get("vectors") or []
            if len(vectors) >= 2:
                seq[a["id"]] = TemplateMatcher(vectors, threshold=float(td.get("threshold", 8.0)))
        else:
            rule.append(a)
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
        self.rule_actions, self.seq_matchers = _build_matchers(actions)
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
        self._step_order = None
        self._step_since = None
        self._off_since = None
        self.thresholds = {"step_warn": 30, "step_crit": 60, "off": 5}
        self._state = self._empty_state()

    def _empty_state(self):
        return {
            "running": True, "error": None, "score": 0.0,
            "current_step": 0, "completed_steps": [], "missed_steps": [],
            "steps": [{"order": s["order"], "name": s["name"], "status": "pending"}
                      for s in self.steps],
            "detected_actions": [], "active_actions": [], "record_id": None,
            "step_stay": 0, "off_screen": 0, "alert_level": "ok",
        }

    def start(self):
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
        with self._jpeg_lock:
            return self._jpeg, self._jpeg_ts

    def get_state(self):
        with self._lock:
            return dict(self._state)

    def _set_state(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _publish_jpeg(self, frame):
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

    def _run_impl(self):
        if cv2 is None:
            self._error = "未安装 opencv，无法启动监控"
            self._running = False
            return
        engine = PoseEngine()
        recognizer = ActionRecognizer()
        cap = VideoSource(self.source)
        if not cap.open():
            self._error = f"无法打开视频源: {self.source}"
            self._running = False
            engine.close()
            return

        detected_seq = []
        last_active = set()
        active_since = {}
        recent_names = []

        while self._running:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            if str(self.source).isdigit():
                frame = cv2.flip(frame, 1)

            ok_pose, pts = engine.landmarks_from_frame(frame)
            active = []
            if ok_pose:
                features = PoseEngine.compute_features(pts)
                if self.assess:
                    self._frames["ts"].append(round(time.time() - self._start_wall, 3))
                    self._frames["feats"].append(features_to_vector(features).tolist())
                active = recognizer.update(features, self.rule_actions)
                vec = features_to_vector(features)
                for aid, m in self.seq_matchers.items():
                    dist, hit = m.update(vec)
                    if hit:
                        detected_seq.append(aid)
                        recent_names.append(self.action_map[aid]["name"])
                        if self.assess:
                            idx = self._frame_idx()
                            start = max(0, idx - m.template_len + 1)
                            self._events.append(
                                {"action_id": aid, "start_idx": start, "end_idx": idx})
                if self.assess:
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
            now_ts = time.time()
            if result["current_step"] != self._step_order:
                self._step_order = result["current_step"]
                self._step_since = now_ts
            stay = int(now_ts - (self._step_since or now_ts))
            if ok_pose:
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
