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

# 默认运行模式。改动它等于改动整条链路的特征质量与误报率，
# 因此单独抽出来做单一事实来源（模板录制的 engine_mode 标记也引用它）。
DEFAULT_RUNNING_MODE = "video"


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
    """封装 mediapipe PoseLandmarker，输出标准化关键点与关节特征。

    **运行模式默认 VIDEO（视频流），而不是 IMAGE。** 这一点对结果质量影响很大：

    IMAGE 模式每帧都当作互不相关的独立图片做一次全量检测，没有帧间跟踪，
    因此单帧关键点会突然失稳（实测约 1% 的帧躯干角从 4° 跳到 170°+），
    而且每帧都要跑完整的检测流程，更慢。

    VIDEO 模式由 mediapipe 维护帧间跟踪与时间平滑，实测（同一段 720p 视频）：

        单帧推理     23.07 ms  ->  9.74 ms    (2.37 倍)
        处理帧率     38.3 fps   ->  77.5 fps   (2.02 倍)
        姿态检出率    98.7 %    -> 100.0 %
        躯干角 p99   171.82°    ->   4.88°    (35 倍)
        失稳帧(>55°) 19 帧      ->      0 帧    (完全消除)
        抖动中位数    0.281°    ->   0.134°   (2.11 倍)

    失稳帧正是「把正常站姿判成深弯腰」的来源，所以这不只是提速，
    也直接减少误报——符合本项目「宁可不报也别误报」的原则。

    调用方无需关心时间戳：`_next_ts` 会按 fps 自动生成**严格递增**的毫秒时间戳
    （VIDEO 模式要求单调递增，否则 mediapipe 会抛错）。也支持显式传入 ts_ms。
    """

    def __init__(self, model_path=None, min_detection=0.5, min_tracking=0.5,
                 running_mode=DEFAULT_RUNNING_MODE, fps=30.0):
        self.available = False
        self.landmarker = None
        self._mode = "image"
        self._fps = float(fps) if fps and fps > 0 else 30.0
        self._tick = 0
        self._last_ts = -1
        if not _HAS_MP or cv2 is None:
            return
        model = ensure_model(model_path)
        if not model:
            return

        want_video = str(running_mode).lower().startswith("v")
        # 先按请求的模式创建；VIDEO 失败则退回 IMAGE，保证整条链路不被拖垮
        # （视觉能力在本项目里是「软依赖」，降级但可用的原则贯穿始终）。
        attempts = [("video", vision.RunningMode.VIDEO),
                    ("image", vision.RunningMode.IMAGE)] if want_video \
            else [("image", vision.RunningMode.IMAGE)]
        last_err = None
        for name, mode in attempts:
            try:
                base = mp_python.BaseOptions(model_asset_path=model)
                options = vision.PoseLandmarkerOptions(
                    base_options=base,
                    running_mode=mode,
                    num_poses=1,
                    min_pose_detection_confidence=min_detection,
                    min_pose_presence_confidence=0.5,
                    min_tracking_confidence=min_tracking,
                )
                self.landmarker = vision.PoseLandmarker.create_from_options(options)
                self._mode = name
                self.available = True
                break
            except Exception as e:  # pragma: no cover
                last_err = e
        if not self.available and last_err is not None:  # pragma: no cover
            print(f"PoseLandmarker 初始化失败: {last_err}")
        elif want_video and self._mode != "video":  # pragma: no cover
            print("提示：VIDEO 模式不可用，已退回 IMAGE 模式（精度与帧率会下降）")

    @property
    def mode(self):
        """实际生效的运行模式（"video" / "image"）。

        与请求的模式可能不同——VIDEO 初始化失败会退回 IMAGE。
        模板录制会把该值写进 template_data，供后续判断模板是否含失稳帧。
        """
        return self._mode

    def _next_ts(self, ts_ms=None):
        """生成严格递增的时间戳（毫秒）。VIDEO 模式对此有硬要求。"""
        self._tick += 1
        ts = int(ts_ms) if ts_ms is not None else int(self._tick * 1000.0 / self._fps)
        if ts <= self._last_ts:
            ts = self._last_ts + 1
        self._last_ts = ts
        return ts

    def _infer(self, frame, ts_ms=None):
        """统一推理入口：按运行模式选择 detect / detect_for_video。"""
        if not self.available or frame is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        if self._mode == "video":
            return self.landmarker.detect_for_video(mp_image, self._next_ts(ts_ms))
        return self.landmarker.detect(mp_image)

    def close(self):
        if self.landmarker is not None:
            try:
                self.landmarker.close()
            except Exception:
                pass

    def detect(self, frame, ts_ms=None):
        """一次推理同时返回 (是否检测到, 2D归一化关键点, 3D米制关键点)。

        2D 用于画面叠加显示与旧版图像空间特征；3D 用于机位无关特征。
        只跑一次推理，避免为了同时拿到两者而重复检测。

        ts_ms: 可选的时间戳（毫秒，对应视频时间轴）。同一帧只应调用一个方法，
        传了时间戳能让 VIDEO 模式的平滑参数与真实时间对齐；不传则按 fps 自动递增。
        """
        result = self._infer(frame, ts_ms)
        if result is None or not result.pose_landmarks:
            return False, [], []
        pts2d = [(lm.x, lm.y, getattr(lm, "visibility", 1.0))
                 for lm in result.pose_landmarks[0]]
        world = getattr(result, "pose_world_landmarks", None) or []
        pts3d = [(lm.x, lm.y, lm.z) for lm in world[0]] if world else []
        return True, pts2d, pts3d

    def landmarks_from_frame(self, frame, ts_ms=None):
        """返回 (是否检测到, 关键点归一化坐标列表[(x,y,visibility)])"""
        result = self._infer(frame, ts_ms)
        if result is None or not result.pose_landmarks:
            return False, []
        pts = [(lm.x, lm.y, getattr(lm, "visibility", 1.0))
               for lm in result.pose_landmarks[0]]
        return True, pts

    def world_landmarks_from_frame(self, frame, ts_ms=None):
        """返回 (是否检测到, 米制三维关键点列表[(x,y,z)])。

        使用 mediapipe 的 pose_world_landmarks：真实尺度的三维坐标（米），
        原点在髋中心。三维量对相机朝向不做投影，因此由它算出的关节角
        **不随机位变化**——这是「多机位共用一套模板」的基础。

        代价：单目三维估计的深度分量精度弱于二维，故特征设计上刻意
        回避了对深度方向敏感的量（详见 compute_features_3d）。
        """
        result = self._infer(frame, ts_ms)
        if result is None:
            return False, []
        world = getattr(result, "pose_world_landmarks", None)
        if not world:
            return False, []
        return True, [(lm.x, lm.y, lm.z) for lm in world[0]]

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
        # 参照轴取「图像向上」（图像坐标 y 向下，故向上是 (0,-1)）。
        # 语义约定（与 actions.py 的 JOINT_FIELDS 声明一致）：
        #   0°   = 完全直立
        #   θ    = 躯干偏离竖直轴 θ 度（前倾与后仰**数值相同，不区分方向**）
        #   90°  = 躯干水平
        #   >90° = 头低于髋（倒立/极度下折）
        # 若误取 (0,+1)（图像向下）：直立会得到 180°，于是「值越大越前倾」的规则
        # 在站立画面里持续命中（误报），而「值越小」的规则永不命中（漏报），
        # 且恰好在「弯腰」这类目标动作上回到 90° 附近 —— 看起来像能识别，实则反了。
        vertical = np.array([0.0, -1.0])
        trunk_norm = np.linalg.norm(trunk_vec)
        if trunk_norm > 1e-6:
            cos = np.dot(trunk_vec, vertical) / trunk_norm
            cos = max(-1.0, min(1.0, cos))
            f["trunk_inclination"] = math.degrees(math.acos(cos))
        else:
            # 肩髋中心重合（关键点退化）时无方向可算，按直立处理。
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

    @staticmethod
    def compute_features_3d(pts):
        """由米制三维关键点计算「机位无关」特征（模板匹配的 3D 特征空间）。

        与 compute_features（二维图像空间）的区别：
        - 所有关节角用三维计算 → 不受相机朝向影响（三维夹角是旋转不变量）
        - 躯干前倾改为「相对下肢的三维夹角」，不再参照图像竖直方向；
          原实现的 trunk_inclination 在镜头俯仰后严重失真（实测漂移 4~9 个标准差）
        - 手腕位置改用「躯干自洽坐标系」表达，同样与相机无关

        刻意回避的量：鼻子的三维夹角（单目深度估计中最不稳，实测漂移 3.55σ）、
        相对图像竖直方向的任何量。
        """
        if len(pts) < 33:
            return {}
        p = {k: np.asarray(pts[v], dtype=float) for k, v in LM.items()}

        mid_hip = (p["left_hip"] + p["right_hip"]) / 2.0
        mid_shoulder = (p["left_shoulder"] + p["right_shoulder"]) / 2.0
        mid_knee = (p["left_knee"] + p["right_knee"]) / 2.0
        mid_ankle = (p["left_ankle"] + p["right_ankle"]) / 2.0

        shoulder_w = float(np.linalg.norm(p["left_shoulder"] - p["right_shoulder"]))
        hip_w = float(np.linalg.norm(p["left_hip"] - p["right_hip"]))
        scale = max(shoulder_w, hip_w) or 1e-6

        # 躯干自洽坐标系：y 轴 = 髋→肩（躯干轴），x 轴 = 骨盆横轴正交化，z = x × y
        y_ax = mid_shoulder - mid_hip
        ny = float(np.linalg.norm(y_ax))
        y_ax = y_ax / ny if ny > 1e-9 else np.array([0.0, -1.0, 0.0])
        x_raw = p["right_hip"] - p["left_hip"]
        x_ax = x_raw - float(np.dot(x_raw, y_ax)) * y_ax
        nx = float(np.linalg.norm(x_ax))
        x_ax = x_ax / nx if nx > 1e-9 else np.array([1.0, 0.0, 0.0])
        z_ax = np.cross(x_ax, y_ax)

        def trunk_yz(pt):
            d = pt - mid_hip
            return float(np.dot(d, y_ax)) / scale, float(np.dot(d, z_ax)) / scale

        def rel(a, b):
            return float(np.linalg.norm(a - b)) / scale

        f = {
            "l_elbow_angle": _angle(p["left_shoulder"], p["left_elbow"], p["left_wrist"]),
            "r_elbow_angle": _angle(p["right_shoulder"], p["right_elbow"], p["right_wrist"]),
            "l_shoulder_angle": _angle(p["left_elbow"], p["left_shoulder"], p["left_hip"]),
            "r_shoulder_angle": _angle(p["right_elbow"], p["right_shoulder"], p["right_hip"]),
            "l_knee_angle": _angle(p["left_hip"], p["left_knee"], p["left_ankle"]),
            "r_knee_angle": _angle(p["right_hip"], p["right_knee"], p["right_ankle"]),
            "l_hip_angle": _angle(p["left_shoulder"], p["left_hip"], p["left_knee"]),
            "r_hip_angle": _angle(p["right_shoulder"], p["right_hip"], p["right_knee"]),
            "trunk_vs_leg_angle": _angle(mid_shoulder, mid_hip, mid_knee),
            "hands_distance": rel(p["left_wrist"], p["right_wrist"]),
            "l_wrist_to_hip": rel(p["left_wrist"], mid_hip),
            "r_wrist_to_hip": rel(p["right_wrist"], mid_hip),
            "leg_extend": float(np.linalg.norm(mid_hip - mid_ankle)) / scale,
            "ankle_distance": rel(p["left_ankle"], p["right_ankle"]),
        }
        ly, lz = trunk_yz(p["left_wrist"])
        ry, rz = trunk_yz(p["right_wrist"])
        f["l_wrist_height"] = ly
        f["r_wrist_height"] = ry
        f["l_wrist_forward"] = lz
        f["r_wrist_forward"] = rz
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
