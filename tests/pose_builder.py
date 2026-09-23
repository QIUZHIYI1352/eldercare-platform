"""构造可复现的测试姿态：给定躯干前倾角与膝角，反解关键点位置（侧视）。

为什么需要它：真实视频的关键点带抖动，无法单独用视频验证「角度→语义」是否
1:1 正确。构造出来的姿态有已知真值，可以把「符号方向」「角度量纲」钉死。

放在 tests/ 下作为测试基础设施，请勿在别处复制第二份——曾经因为脚本里的
参数没被真正使用（knee_deg 传了却被忽略）而得出过错误结论。
"""
import math


def _rot(v, deg):
    """二维向量逆时针旋转 deg 度。"""
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


def _norm(v):
    n = math.hypot(v[0], v[1])
    return (v[0] / n, v[1] / n) if n > 1e-9 else (0.0, -1.0)


def build_pose(trunk_deg=0.0, knee_deg=180.0, arm_up=False,
               hip=(0.50, 0.55), trunk_len=0.30, thigh=0.15, shin=0.15):
    """返回 33 个 (x, y, vis) 关键点（侧视图，图像 y 轴向下）。

    参数语义与生产代码的约定一致：
      - trunk_deg: 躯干偏离竖直方向的角度，0 = 直立，90 = 躯干水平
      - knee_deg:  髋-膝-踝夹角，180 = 完全伸直，90 = 大腿与小腿成直角
      - arm_up:    手腕抬到肩上方（用于手相对肩高的判定）
    """
    pts = [(0.5, 0.5, 1.0)] * 33

    def put(i, xy):
        pts[i] = (xy[0], xy[1], 1.0)

    hx, hy = hip
    a = math.radians(trunk_deg)
    # trunk_deg=0 时肩在髋正上方（y 更小），保证 0° 真的对应直立
    shoulder = (hx + trunk_len * math.sin(a), hy - trunk_len * math.cos(a))
    put(11, (shoulder[0] - 0.05, shoulder[1]))
    put(12, (shoulder[0] + 0.05, shoulder[1]))
    put(23, (hx - 0.05, hy))
    put(24, (hx + 0.05, hy))

    # 膝在髋正下方，再反解踝的位置使 «髋-膝-踝» 夹角精确等于 knee_deg
    knee = (hx, hy + thigh)
    put(25, (knee[0] - 0.04, knee[1]))
    put(26, (knee[0] + 0.04, knee[1]))
    to_hip = _norm((hx - knee[0], hy - knee[1]))      # 膝→髋，向上
    # 旋转量必须等于目标膝角本身：180° 时踝落在正下方（伸直），
    # 90° 时水平。用 (180 - knee_deg) 会得到互补角。
    to_ankle = _rot(to_hip, knee_deg)
    ankle = (knee[0] + shin * to_ankle[0], knee[1] + shin * to_ankle[1])
    put(27, (ankle[0] - 0.04, ankle[1]))
    put(28, (ankle[0] + 0.04, ankle[1]))

    # 手腕：上举时高于肩，否则垂在髋部下方
    wy = shoulder[1] - 0.35 if arm_up else hy + 0.25
    put(15, (shoulder[0] - 0.18, wy))
    put(16, (shoulder[0] + 0.18, wy))
    return pts
