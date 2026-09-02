"""姿态检测引擎：基于 mediapipe Tasks API (PoseLandmarker) + opencv 提取关键点与关节特征

兼容 mediapipe >= 1.0（旧版 mp.solutions 已被移除）。
模型文件缺失时自动从官方源下载。
"""
import math
import os
import urllib.request

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
    _HAS_MP = True
except Exception:  # pragma: no cover
    mp = None
    mp_python = None
    vision = None
    _HAS_MP = False

# 人体 33 关键点索引（与 mediapipe PoseLandmark 顺序一致）
LM = {
    "nose": 0,
    "left_shoulder": 11, "right_shoulder": 12,
    "left_elbow": 13, "right_elbow": 14,
    "left_wrist": 15, "right_wrist": 16,
    "left_hip": 23, "right_hip": 24,
    "left_knee": 25, "right_knee": 26,
    "left_ankle": 27, "right_ankle": 28,
}

_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
              "pose_landmarker_lite/float16/1/pose_landmarker_lite.task")
_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "..", "models", "pose_landmarker_lite.task")


def ensure_model(path=None):
    """模型文件不存在时自动下载，返回模型路径（失败返回 None）。"""
    path = path or _MODEL_PATH
    path = os.path.abspath(path)
    if os.path.exists(path):
        return path
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        print(f"正在下载 PoseLandmarker 模型到 {path} ...")
        urllib.request.urlretrieve(_MODEL_URL, path)
        return path
    except Exception as e:  # pragma: no cover
        print(f"模型下载失败: {e}")
        return None


def _angle(a, b, c):
    """三点夹角（以 b 为顶点，单位：度）"""
    a, b, c = np.array(a), np.array(b), np.array(c)
    ba, bc = a - b, c - b
    if np.linalg.norm(ba) == 0 or np.linalg.norm(bc) == 0:
        return 0.0
    cos = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc))
    cos = max(-1.0, min(1.0, cos))
    return math.degrees(math.acos(cos))


def _dist(a, b):
    return float(np.linalg.norm(np.array(a) - np.array(b)))


class PoseEngine:
    """封装 mediapipe PoseLandmarker，输出标准化关键点与关节特征。"""

    def __init__(self, model_path=None, min_detection=0.5, min_tracking=0.5):
        self.available = False
        self.landmarker = None
        if not _HAS_MP or cv2 is None:
            return
        model = ensure_model(model_path)
        if not model:
            return
        try:
            base = mp_python.BaseOptions(model_asset_path=model)
            options = vision.PoseLandmarkerOptions(
                base_options=base,
                running_mode=vision.RunningMode.IMAGE,
                num_poses=1,
                min_pose_detection_confidence=min_detection,
                min_pose_presence_confidence=0.5,
                min_tracking_confidence=min_tracking,
            )
            self.landmarker = vision.PoseLandmarker.create_from_options(options)
            self.available = True
        except Exception as e:  # pragma: no cover
            print(f"PoseLandmarker 初始化失败: {e}")

    def close(self):
        if self.landmarker is not None:
            try:
                self.landmarker.close()
            except Exception:
                pass

    def landmarks_from_frame(self, frame):
        """返回 (是否检测到, 关键点归一化坐标列表[(x,y,visibility)])"""
        if not self.available or frame is None:
            return False, []
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self.landmarker.detect(mp_image)
        if not result.pose_landmarks:
            return False, []
        pts = [(lm.x, lm.y, getattr(lm, "visibility", 1.0))
               for lm in result.pose_landmarks[0]]
        return True, pts

    @staticmethod
    def compute_features(pts):
        """由 33 个归一化关键点计算可解释的关节特征。"""
        if len(pts) < 33:
            return {}
        p = {k: pts[v] for k, v in LM.items()}

        def xy(k):
            return (p[k][0], p[k][1])

        shoulder_w = _dist(xy("left_shoulder"), xy("right_shoulder")) or 1e-6
        hip_w = _dist(xy("left_hip"), xy("right_hip")) or 1e-6
        scale = max(shoulder_w, hip_w)

        f = {}
        f["left_elbow_angle"] = _angle(xy("left_shoulder"), xy("left_elbow"), xy("left_wrist"))
        f["right_elbow_angle"] = _angle(xy("right_shoulder"), xy("right_elbow"), xy("right_wrist"))
        f["left_shoulder_angle"] = _angle(xy("left_elbow"), xy("left_shoulder"), xy("left_hip"))
        f["right_shoulder_angle"] = _angle(xy("right_elbow"), xy("right_shoulder"), xy("right_hip"))

        f["left_knee_angle"] = _angle(xy("left_hip"), xy("left_knee"), xy("left_ankle"))
        f["right_knee_angle"] = _angle(xy("right_hip"), xy("right_knee"), xy("right_ankle"))
        f["left_hip_angle"] = _angle(xy("left_shoulder"), xy("left_hip"), xy("left_knee"))
        f["right_hip_angle"] = _angle(xy("right_shoulder"), xy("right_hip"), xy("right_knee"))

        mid_shoulder = ((p["left_shoulder"][0] + p["right_shoulder"][0]) / 2,
                        (p["left_shoulder"][1] + p["right_shoulder"][1]) / 2)
        mid_hip = ((p["left_hip"][0] + p["right_hip"][0]) / 2,
                   (p["left_hip"][1] + p["right_hip"][1]) / 2)
        trunk_vec = np.array([mid_shoulder[0] - mid_hip[0], mid_shoulder[1] - mid_hip[1]])
        vertical = np.array([0.0, 1.0])
        trunk_norm = np.linalg.norm(trunk_vec)
        if trunk_norm > 1e-6:
            cos = np.dot(trunk_vec, vertical) / trunk_norm
            cos = max(-1.0, min(1.0, cos))
            f["trunk_inclination"] = math.degrees(math.acos(cos))
        else:
            f["trunk_inclination"] = 0.0

        sh_vec = np.array([p["right_shoulder"][0] - p["left_shoulder"][0],
                           p["right_shoulder"][1] - p["left_shoulder"][1]])
        sh_norm = np.linalg.norm(sh_vec)
        if sh_norm > 1e-6:
            f["trunk_lateral_lean"] = math.degrees(math.asin(max(-1.0, min(1.0, sh_vec[1] / sh_norm))))
        else:
            f["trunk_lateral_lean"] = 0.0

        f["hand_height_left"] = (p["left_wrist"][1] - p["left_shoulder"][1]) / scale
        f["hand_height_right"] = (p["right_wrist"][1] - p["right_shoulder"][1]) / scale
        f["hands_distance"] = _dist(xy("left_wrist"), xy("right_wrist")) / scale
        f["head_height"] = (p["nose"][1] - p["left_shoulder"][1]) / scale

        top_y = min(p["nose"][1], p["left_shoulder"][1], p["right_shoulder"][1])
        bottom_y = max(p["left_ankle"][1], p["right_ankle"][1])
        f["body_height"] = (bottom_y - top_y) / scale
        f["hip_center_height"] = (mid_hip[1] - top_y) / scale
        f["visibility"] = float(np.mean([v for (_, _, v) in pts]))
        return f

    def draw(self, frame, pts, features=None, blur_face=False):
        """在画面叠加骨架。blur_face 默认关闭（人脸不打码，专注动作判定）。"""
        if frame is None:
            return frame
        h, w = frame.shape[:2]
        bone_pairs = [
            ("left_shoulder", "right_shoulder"),
            ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
            ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
            ("left_shoulder", "left_hip"), ("right_shoulder", "right_hip"),
            ("left_hip", "right_hip"),
            ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
            ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
        ]
        if len(pts) >= 33:
            for a, b in bone_pairs:
                pa, pb = pts[LM[a]], pts[LM[b]]
                if pa[2] > 0.3 and pb[2] > 0.3:
                    cv2.line(frame, (int(pa[0] * w), int(pa[1] * h)),
                             (int(pb[0] * w), int(pb[1] * h)), (0, 200, 0), 2)
            for name, idx in LM.items():
                pt = pts[idx]
                if pt[2] > 0.3:
                    cv2.circle(frame, (int(pt[0] * w), int(pt[1] * h)), 4, (0, 255, 255), -1)
        return frame
