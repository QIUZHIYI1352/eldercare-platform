"""中文文字渲染工具。

OpenCV 的 cv2.putText 使用 Hershey 字体，不支持中文，画面上的中文会显示为乱码。
本模块改用 Pillow + 系统中文字体渲染；纯 ASCII 文本仍走 cv2.putText（更快）。
找不到中文字体时自动回退到 cv2.putText，保证不崩溃。
"""
import os

import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False

# 常见中文字体路径（Windows / macOS / Linux）
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
    r"C:\Windows\Fonts\msyhbd.ttc",     # 微软雅黑粗体
    r"C:\Windows\Fonts\simhei.ttf",     # 黑体
    r"C:\Windows\Fonts\simsun.ttc",     # 宋体
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]

_font_path = None
_font_cache = {}


def _find_font_path():
    global _font_path
    if _font_path is not None:
        return _font_path or None
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            _font_path = p
            return p
    _font_path = ""
    return None


def has_chinese_font():
    """是否可用中文字体（用于日志/诊断）。"""
    return _HAS_PIL and _find_font_path() is not None


def _get_font(size):
    path = _find_font_path()
    if not path:
        return None
    key = (path, int(size))
    font = _font_cache.get(key)
    if font is None:
        try:
            font = ImageFont.truetype(path, int(size))
            _font_cache[key] = font
        except Exception:  # pragma: no cover
            font = None
    return font


def _is_ascii(text):
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def draw_texts(img_bgr, items):
    """在 BGR 图像上原地绘制多行文本。

    items: [(text, (x, y), (B, G, R), font_size), ...]
    """
    if not items or img_bgr is None:
        return img_bgr
    import cv2

    if (not _HAS_PIL or not _find_font_path()
            or all(_is_ascii(item[0]) for item in items)):
        # 纯 ASCII 或无可用的中文字体：走 OpenCV 快速路径
        for text, pos, color, size in items:
            cv2.putText(img_bgr, text, (int(pos[0]), int(pos[1])),
                        cv2.FONT_HERSHEY_SIMPLEX, max(0.4, size / 28.0),
                        (int(color[0]), int(color[1]), int(color[2])),
                        2, cv2.LINE_AA)
        return img_bgr

    # 含中文：整帧转 PIL 一次性绘制，避免多次转换开销
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil)
    for text, pos, color, size in items:
        font = _get_font(size)
        if font is None:
            continue
        b, g, r = (int(c) for c in color)
        draw.text((int(pos[0]), int(pos[1])), text, font=font, fill=(r, g, b))
    np.copyto(img_bgr, cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR))
    return img_bgr
