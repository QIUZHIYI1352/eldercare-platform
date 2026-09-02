"""视频源封装：本机摄像头 / RTSP / HTTP 视频流 / 视频文件 的统一读取。

解决 RTSP 常见痛点：打开超时、缓冲延迟、断流重连。
"""
import time

import cv2

RTSP_OPEN_TIMEOUT_MS = 5000


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
        self.is_rtsp = self.source.lower().startswith(("rtsp://", "rtsps://"))
        self.is_http = self.source.lower().startswith(("http://", "https://"))
        self.is_camera = self.source.isdigit()
        self._reconnect_interval = 3.0
        self._last_attempt = 0.0

    def open(self):
        """打开视频源，返回是否成功。"""
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
        return self.cap.isOpened()

    def read(self):
        """读取一帧，支持断流自动重连。返回 (ok, frame)。"""
        if self.cap is None:
            return False, None
        ok, frame = self.cap.read()
        if ok:
            return True, frame
        # 断流/失败：尝试重连（限频）
        now = time.time()
        if (self.is_rtsp or self.is_http) and (now - self._last_attempt) > self._reconnect_interval:
            print(f"  视频流中断，尝试重连 {self.source} ...")
            self.release()
            if self.open():
                print("  重连成功")
                return self.cap.read()
        return False, None

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
