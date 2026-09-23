"""轻量内存滑动窗口限流：用于登录 / 注册等敏感接口的抗暴力破解。

说明：按进程内存计数，够单进程部署使用；若将来用多 worker 或多实例，
应替换为 Redis 等集中式存储（接口保持不变即可）。
"""
import threading
import time

_lock = threading.Lock()
_hits = {}  # key -> [命中时间戳]
_MAX_KEYS = 20000


def allow(key: str, limit: int, window: int) -> bool:
    """window 秒内最多 limit 次；超额返回 False（且不计入本次）。"""
    now = time.time()
    cutoff = now - window
    with _lock:
        arr = [t for t in _hits.get(key, ()) if t > cutoff]
        if len(arr) >= limit:
            _hits[key] = arr
            return False
        arr.append(now)
        _hits[key] = arr
        if len(_hits) > _MAX_KEYS:
            for k in [k for k, v in _hits.items() if not v or v[-1] <= cutoff]:
                _hits.pop(k, None)
        return True


def retry_after(key: str, window: int) -> int:
    """距离下一次可尝试还需多少秒（用于提示）。"""
    with _lock:
        arr = _hits.get(key) or []
    if not arr:
        return 0
    return max(0, int(window - (time.time() - arr[-1])) + 1)


def reset(key: str = None):
    """清空限流记录（测试用）。"""
    with _lock:
        if key is None:
            _hits.clear()
        else:
            _hits.pop(key, None)
