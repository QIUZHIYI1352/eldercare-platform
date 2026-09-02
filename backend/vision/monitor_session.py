"""Web 内嵌实时监控会话：后台线程读视频源 → 识别 → 输出 MJPEG 流 + 状态。

前端「实时监管」页选择设备/流程后，通过 HTTP 接口创建会话，
画面通过 <img src="/api/monitoring/sessions/{sid}/stream"> 展示，
识别状态通过轮询 /sessions/{sid}/state 获取。
"""
import threading
import time
import uuid

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
    def __init__(self, source, process, actions):
        self.source = source
        self.process = process
        self.actions = actions
        self.action_map = {a["id"]: a for a in actions}
        self.rule_actions, self.seq_matchers = _build_matchers(actions)
        self.step_actions = [s.get("action_id") for s in process.get("steps", [])]
        self.steps = process.get("steps", [])

        self._running = False
        self._thread = None
        self._error = None
        self._lock = threading.Lock()
        self._jpeg = None
        self._jpeg_lock = threading.Lock()
        self._jpeg_ts = 0.0
        self._state = self._empty_state()

    def _empty_state(self):
        return {
            "running": True, "error": None, "score": 0.0,
            "current_step": 0, "completed_steps": [], "missed_steps": [],
            "steps": [{"order": s["order"], "name": s["name"], "status": "pending"}
                      for s in self.steps],
            "detected_actions": [], "active_actions": [],
        }

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

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

    def _draw_status(self, frame, result, active, seq_status):
        h, w = frame.shape[:2]
        panel_h = min(300, h)
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, panel_h), (18, 18, 18), -1)
        y = 26
        cv2.putText(overlay, f"[{self.process.get('name','')}] 完成度 {result['score']}%",
                    (15, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
        y += 26
        completed = set(result["completed_steps"])
        missed = set(result["missed_steps"])
        current = result["current_step"]
        for s in self.steps:
            o = s["order"]
            if o in completed:
                tag, color = "✓已完成", (0, 255, 0)
            elif o == current:
                tag, color = "▶当前", (0, 200, 255)
            elif o in missed:
                tag, color = "⚠漏步", (0, 0, 255)
            else:
                tag, color = "待执行", (150, 150, 150)
            cv2.putText(overlay, f"{o}. {s['name']}  {tag}", (15, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
            y += 22
        line = ""
        if active:
            line += "规则: " + " + ".join(self.action_map[a]["name"] for a in active)
        for aid, dist in seq_status.items():
            thr = (self.action_map[aid].get("template_data") or {}).get("threshold", 8.0)
            line += f" | {self.action_map[aid]['name']} DTW={dist:.0f}{'✓' if dist <= thr else ''}"
        if line:
            cv2.putText(overlay, line[:70], (15, y + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
        return overlay

    def _run(self):
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
        seq_status = {}
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
                active = recognizer.update(features, self.rule_actions)
                vec = features_to_vector(features)
                for aid, m in self.seq_matchers.items():
                    dist, hit = m.update(vec)
                    seq_status[aid] = dist
                    if hit:
                        detected_seq.append(aid)
                        recent_names.append(self.action_map[aid]["name"])

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
            frame = self._draw_status(frame, result, active, seq_status)
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

    def start(self, source, process, actions):
        sid = "sess_" + uuid.uuid4().hex[:12]
        sess = MonitorSession(source, process, actions)
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
