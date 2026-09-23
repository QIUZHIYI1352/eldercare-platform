"""视频源封装：本机摄像头 / RTSP / HTTP 视频流 / 视频文件 的统一读取。

解决 RTSP 常见痛点：打开超时、缓冲延迟、断流重连。

注意：opencv 采用「软依赖」——未安装时本模块仍可导入，
Web 后台等不依赖摄像头的能力不受影响，调用 open() 时再报错，
避免把整个应用拖垮在 import 阶段。
"""
import time

try:
    import cv2
except Exception:  # pragma: no cover - 环境未装 opencv 时降级
    cv2 = None

RTSP_OPEN_TIMEOUT_MS = 5000

MISSING_CV2_MSG = "未安装 opencv（cv2），无法读取视频源。请执行 pip install opencv-contrib-python"


def classify_source(source):
    """把视频源字符串分类，返回 (is_camera, is_rtsp, is_http, is_file)。

    这个分类被两处独立依赖，所以必须只有一份实现：

    1. `VideoSource` —— 决定「读失败」是 EOF（该收尾）还是断流（该重连）。
    2. `use_video_clock` —— 决定录制的计时基准用视频时间轴还是墙钟。

    两处都答的是同一个问题：「这是可重放的素材，还是正在发生的实时信号？」
    分开实现迟早会答得不一致。
    """
    s = str(source).strip().lower()
    is_rtsp = s.startswith(("rtsp://", "rtsps://"))
    is_http = s.startswith(("http://", "https://"))
    is_camera = s.isdigit()
    return is_camera, is_rtsp, is_http, not (is_camera or is_rtsp or is_http)


def use_video_clock(source):
    """该视频源是否该用「视频时间轴」计时（而不是墙钟）。

    视频文件是按 CPU 速度解码的——处理多快，就在单位墙钟里吞掉多少素材。
    用墙钟去量录制窗口，`--duration 5` 实际覆盖的**视频时长**会随机器性能
    和推理模式漂移（实测同一段 720p 素材：VIDEO 模式 8.88s、IMAGE 模式 4.20s，
    相差 111%）。后果有两个：

    1. 录出的模板时间跨度不可复现——同一句命令在不同机器上得到不同模板；
    2. 引擎按 fps 生成时间戳（视频时间轴），而录制窗口/匹配器 cooldown 用墙钟，
       两条时间轴不一致，保持时长与冷却逻辑都会失准。

    所以：文件用视频时间轴；摄像头/RTSP/HTTP 用墙钟（它们本身就是实时的）。
    """
    return classify_source(source)[3]


class VideoSource:
    def __init__(self, source="0"):
        """
        source:
            "0" / "1" / 0            -> 本机摄像头索引
            "rtsp://..."             -> RTSP 流
            "http://..." / "https"   -> HTTP 视频流
            "xxx.mp4" 等文件路径      -> 本地视频文件
        """
        self.source = str(source).strip()
        self.cap = None
        self.is_camera, self.is_rtsp, self.is_http, self.is_file = \
            classify_source(self.source)
        self.eof = False
        self._reconnect_interval = 3.0
        self._last_attempt = 0.0

    def open(self):
        """打开视频源，返回是否成功。"""
        if cv2 is None:
            print(f"  {MISSING_CV2_MSG}")
            return False
        self._last_attempt = time.time()
        if self.is_camera:
            idx = int(self.source)
            # Windows 优先使用 DSHOW 后端降低延迟
            self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        elif self.is_rtsp:
            self.cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
            # 降低 RTSP 缓冲，减少延迟
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, RTSP_OPEN_TIMEOUT_MS)
            self.cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2000)
        elif self.is_http:
            self.cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        else:
            self.cap = cv2.VideoCapture(self.source)
        return bool(self.cap and self.cap.isOpened())

    def read(self):
        """读取一帧，支持断流自动重连。返回 (ok, frame)。

        读失败时：**本地文件**置 `eof=True`（调用方据此收尾退出）；
        RTSP/HTTP 视为断流，限频尝试重连。摄像头不做重连（拔插由调用方处理）。
        """
        if cv2 is None or self.cap is None:
            return False, None
        ok, frame = self.cap.read()
        if ok:
            return True, frame
        # 文件读尽：明确标记，避免调用方把 EOF 误当断流而无限空转
        if self.is_file:
            self.eof = True
            return False, None
        # 断流/失败：尝试重连（限频）
        now = time.time()
        if (self.is_rtsp or self.is_http) and (now - self._last_attempt) > self._reconnect_interval:
            print(f"  视频流中断，尝试重连 {self.source} ...")
            self.release()
            if self.open():
                print("  重连成功")
                return self.cap.read()
        return False, None

    def reopen(self):
        """重新打开同一视频源（用于需要再跑一遍文件内容的场景，如标定对照）。"""
        self.release()
        self.eof = False
        return self.open()

    def release(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = None

    @property
    def fps(self):
        if self.cap is not None and self.cap.isOpened():
            f = self.cap.get(cv2.CAP_PROP_FPS)
            return f if f > 0 else 30.0
        return 30.0


def open_source(source):
    """便捷函数：打开并返回 VideoSource，失败返回 None。"""
    vs = VideoSource(source)
    if vs.open():
        return vs
    vs.release()
    return None
