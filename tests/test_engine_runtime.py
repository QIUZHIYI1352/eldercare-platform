"""运行期行为回归测试：视频源 EOF 语义 + 引擎时间戳单调性。

这两处都是「改错了不会立刻报错，但会让整条链路空转或被强杀」的地方：

1. `VideoSource.eof`：本地文件读到结尾必须能被识别为「结束」，
   否则调用方会把 EOF 当成「断流待重连」而无限空转
   （`monitor.py --source demo.mp4` 曾经永不退出）。
   而 RTSP/HTTP 断流**必须不置 eof**，否则会误判为「播完了」而提前收尾。

2. `PoseEngine._next_ts`：VIDEO 模式要求时间戳**严格递增**，
   重复或回退会让 mediapipe 抛错。这里必须保证单调，包括调用方传入
   非递增时间戳时也要自动补偿。
"""
import inspect
import os
import re

import numpy as np
import pytest

from backend.vision.pose_engine import PoseEngine
from backend.vision.video_source import VideoSource, classify_source, use_video_clock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("source,is_file", [
    ("demo.mp4", True),
    (".workbuddy/testdata/clip.webm", True),
    ("0", False),
    ("1", False),
    ("rtsp://192.168.1.10:554/stream1", False),
    ("http://192.168.1.10:8080/video", False),
    ("https://example.com/live.m3u8", False),
])
def test_use_video_clock_matches_source_kind(source, is_file):
    """只有**文件**才该用视频时间轴计时。

    摄像头/流本来就是实时的，帧到达节奏等于真实时间；文件是按 CPU 速度解码的，
    用墙钟量窗口会让 `--duration 5` 实际覆盖 4~9 秒不等的素材。
    """
    assert use_video_clock(source) is is_file


@pytest.mark.parametrize("source", [
    "demo.mp4", "0", "rtsp://192.168.1.10:554/stream1", "http://x/y.mp4",
])
def test_classification_has_single_source_of_truth(source):
    """EOF 语义与计时基准必须用同一套分类，否则两者会各自漂移。"""
    is_camera, is_rtsp, is_http, is_file = classify_source(source)
    vs = VideoSource(source)
    assert (vs.is_camera, vs.is_rtsp, vs.is_http, vs.is_file) == \
        (is_camera, is_rtsp, is_http, is_file)
    assert use_video_clock(source) is vs.is_file


# --------------------------------------------------------------- 视频源分类

@pytest.mark.parametrize("source,is_file", [
    ("demo.mp4", True),
    (".workbuddy/testdata/clip.webm", True),
    ("0", False),
    ("1", False),
    ("rtsp://192.168.1.10:554/stream1", False),
    ("http://192.168.1.10:8080/video", False),
    ("https://example.com/live.m3u8", False),
])
def test_source_classification(source, is_file):
    vs = VideoSource(source)
    assert vs.is_file is is_file, f"{source} 的 is_file 判定错误"


# --------------------------------------------------------------- EOF 语义

class _FakeCap:
    """最小 cv2.VideoCapture 替身：按剧本逐次返回读帧结果。"""

    def __init__(self, results):
        self._results = list(results)
        self.released = False

    def read(self):
        if self._results:
            return self._results.pop(0)
        return False, None

    def isOpened(self):
        return True

    def release(self):
        self.released = True

    def get(self, prop):
        return 25.0


def _frame():
    return np.zeros((4, 4, 3), dtype=np.uint8)


def test_file_source_sets_eof_after_last_frame():
    pytest.importorskip("cv2")
    vs = VideoSource("clip.mp4")
    vs.cap = _FakeCap([(True, _frame()), (True, _frame())])

    assert vs.read()[0] is True
    assert vs.read()[0] is True
    assert vs.eof is False, "还有帧时不应标记 eof"

    ok, frame = vs.read()
    assert ok is False and frame is None
    assert vs.eof is True, "文件读尽后必须置 eof，否则调用方会无限空转"


def test_stream_source_does_not_set_eof(monkeypatch):
    """RTSP 断流不是「播完」，不能被标成 eof 而提前收尾。

    注意：这里必须把 open() 换成替身。真实 open() 会去连 192.168.1.10，
    在无该设备的机器上会阻塞约 30 秒（opencv 的 ffmpeg 流超时），
    让整个测试套件莫名其妙地慢下来。
    """
    pytest.importorskip("cv2")
    vs = VideoSource("rtsp://192.168.1.10:554/stream1")
    vs.cap = _FakeCap([(True, _frame())])
    vs._last_attempt = 0.0                      # 允许触发重连分支
    tries = []
    monkeypatch.setattr(vs, "open", lambda: (tries.append(1), False)[1])

    assert vs.read()[0] is True
    assert vs.read()[0] is False
    assert vs.eof is False, "流断线必须保持 eof=False，由调用方走重连逻辑"
    assert tries, "断流且超过重连间隔时应尝试重连"


def test_stream_reconnect_is_rate_limited(monkeypatch):
    """重连必须限频，否则断流时会以每帧一次的频率猛敲网络。"""
    pytest.importorskip("cv2")
    vs = VideoSource("rtsp://192.168.1.10:554/stream1")
    vs.cap = _FakeCap([])
    vs._last_attempt = 0.0
    tries = []
    monkeypatch.setattr(vs, "open", lambda: (tries.append(1), False)[1])

    vs.read()          # 第一次失败 -> 触发一次重连
    vs.read()          # 紧接着失败 -> 因间隔未到，不应再重连
    vs.read()
    assert len(tries) == 1, f"重连被过于频繁地触发：{len(tries)} 次"


def test_reopen_clears_eof():
    """重新打开同一文件后应能再读一遍（标定对照需要跑两遍素材）。"""
    pytest.importorskip("cv2")
    vs = VideoSource("clip.mp4")
    vs.cap = _FakeCap([])
    vs.read()
    assert vs.eof is True
    vs.eof = False          # reopen() 内部会这么做（此处不真开设备）
    assert vs.eof is False


def test_camera_source_does_not_set_eof():
    pytest.importorskip("cv2")
    vs = VideoSource("0")
    vs.cap = _FakeCap([(True, _frame())])
    vs.read()
    assert vs.read()[0] is False
    assert vs.eof is False


# ------------------------------------------------------- 引擎时间戳单调性

def test_timestamps_strictly_increasing_by_default():
    eng = PoseEngine.__new__(PoseEngine)     # 不初始化 mediapipe，只测时间戳逻辑
    eng._fps = 25.0
    eng._tick = 0
    eng._last_ts = -1
    ts = [eng._next_ts() for _ in range(50)]
    assert all(b > a for a, b in zip(ts, ts[1:])), "时间戳必须严格递增"
    assert ts[0] == 40, "25fps 时首帧间隔应为 40ms"


def test_explicit_timestamp_is_used_when_increasing():
    eng = PoseEngine.__new__(PoseEngine)
    eng._fps = 30.0
    eng._tick = 0
    eng._last_ts = -1
    assert eng._next_ts(1000) == 1000
    assert eng._next_ts(1040) == 1040


def test_non_increasing_timestamp_is_compensated():
    """调用方传入重复/回退的时间戳时必须自动补偿，而不是把异常抛给上层。"""
    eng = PoseEngine.__new__(PoseEngine)
    eng._fps = 30.0
    eng._tick = 0
    eng._last_ts = -1
    first = eng._next_ts(5000)
    dup = eng._next_ts(5000)          # 重复
    back = eng._next_ts(100)          # 回退
    assert first == 5000
    assert dup > first and back > dup, "重复或回退的时间戳必须被顶到更大值"


def test_engine_requests_video_mode_by_default():
    """默认必须请求 VIDEO 模式。

    IMAGE 模式是本项目已实测过的质量陷阱（单帧躯干角会跳到 170°+，
    把正常站姿判成深弯腰），默认值一旦被改回 IMAGE，误报会重新出现。
    """
    sig = inspect.signature(PoseEngine.__init__)
    assert sig.parameters["running_mode"].default == "video"


def test_unavailable_engine_degrades_quietly():
    """未初始化 mediapipe 时（软依赖降级），推理接口必须安全返回而非抛异常。"""
    eng = PoseEngine.__new__(PoseEngine)
    eng.available = False
    eng._mode = "image"
    eng._fps = 30.0
    eng._tick = 0
    eng._last_ts = -1

    assert eng._infer(None) is None
    assert eng._infer(_frame()) is None
    assert eng.detect(_frame()) == (False, [], [])
    assert eng.landmarks_from_frame(_frame()) == (False, [])
    assert eng.world_landmarks_from_frame(_frame()) == (False, [])


# ------------------------------------------------ 静态一致性：读视频的地方

# 所有构造 VideoSource 的生产文件。
_VIDEO_SOURCE_CALLERS = [
    "monitor.py",
    "record_template.py",
    "calibrate_template.py",
    "calibrate_rule.py",
    "backend/vision/monitor_session.py",
]


def _read(rel):
    path = os.path.join(_ROOT, rel)
    assert os.path.exists(path), f"找不到 {rel}（测试清单需同步更新）"
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("rel", _VIDEO_SOURCE_CALLERS)
def test_video_source_callers_handle_eof(rel):
    """读 VideoSource 的地方必须判断 cap.eof。

    本地文件读尽是「正常结束」，流断线是「等待重连」——两者处理方式相反。
    漏判的后果不是抛错而是**静默空转**：`monitor.py --source x.mp4` 曾经永不退出。
    用一条静态规则把它钉住，新增调用方忘了写 eof 时立刻失败。
    """
    src = _read(rel)
    assert "VideoSource(" in src, f"{rel} 已不再使用 VideoSource，请更新本测试清单"
    assert ".eof" in src, (
        f"{rel} 用 VideoSource 读视频但没处理 cap.eof："
        f"本地文件读尽会被误判成断流而无限空转"
    )


# 每帧应调用的推理次数。detect() 一次就返回 2D+3D，不需要调两遍。
_ENGINE_CALLS_PER_FRAME = {
    "monitor.py": 1,
    "record_template.py": 1,
    "calibrate_template.py": 1,
    "calibrate_rule.py": 1,
    "backend/vision/monitor_session.py": 1,
}

_INFER_CALL = re.compile(r"\w+\.(?:detect|landmarks_from_frame|world_landmarks_from_frame)\(")


@pytest.mark.parametrize("rel,expected", sorted(_ENGINE_CALLS_PER_FRAME.items()))
def test_engine_inferred_once_per_frame(rel, expected):
    """同一帧只能推理一次。

    VIDEO 模式每调用一次就推进一个时间戳；同一帧调两次等于告诉 mediapipe
    「这中间又过了一帧」，帧间跟踪会因此错乱。
    IMAGE 模式下重复调用是幂等的，所以这个坑是切到 VIDEO 之后才出现的。
    （detect() 已同时返回 2D 与 3D，就是为了避免这种重复推理。）
    """
    src = _read(rel)
    found = _INFER_CALL.findall(src)
    assert len(found) == expected, (
        f"{rel} 中推理调用有 {len(found)} 处（期望 {expected}）：{found}。"
        f"同一帧推理多次会让 VIDEO 模式的时间戳与跟踪错乱"
    )


# -------------------------------------------- 故意冻结：评估路径的墙钟时间轴

def test_assessment_timeline_is_deliberately_wall_clock():
    """评估路径的事件时间轴目前**故意**用墙钟，不是漏改。

    对实时摄像头墙钟就是对的；对视频文件会按机器快慢失真约 1.8×
    （实测 5s 墙钟覆盖 8.88s 素材，见 video_source.use_video_clock）。

    已与需求方确认「先标注不改」——改它会动到评分口径。这条测试把当前行为钉住：
    真要改之前，请先更新本测试与 monitor_session.start() 里的 TODO 说明，
    别让评分口径在不知不觉中变了。
    """
    src = _read("backend/vision/monitor_session.py")
    assert "TODO(时间轴)" in src, (
        "评估时间轴的说明被删掉了。这是经过确认的「故意保留」状态，"
        "请先看懂 start() 的 TODO 再决定是否移除"
    )
    assert "_start_wall = time.time()" in src, (
        "评估/离屏时长的墙钟基准变了；若是有意改成视频时间轴，"
        "请一并更新本测试与相关阈值说明"
    )
