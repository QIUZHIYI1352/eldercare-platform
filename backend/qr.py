"""二维码生成：把一段文本变成可以直接嵌进网页的 SVG。

用途很窄——「手机扫码接入」要在电脑屏幕上显示一个二维码，手机扫一下
就跳到摄像头页面，省掉在手机小键盘上手输 IP 和端口。

**为什么复用 OpenCV 而不自己实现 QR 规范**：手写一套完整的 QR 需要
GF(256) 上的 Reed-Solomon 纠错、8 种掩码的罚分选择、格式信息 BCH 编码
等等，约 400 行；任何一处写错都会产出「看着像二维码但扫不出来」的图，
而这种错几乎没有肉眼可辨的线索，排查成本极高。OpenCV（opencv-contrib，
≥4.5.1）的 objdetect 模块自带编码器，一行就能拿到模块矩阵，而 cv2
本来就是本项目的视觉依赖。

cv2 在本项目里是**可选**依赖（不装也能跑管理后台），因此：
  - cv2 缺失、或文本太长塞不进 -> qr_svg 返回 None，调用方降级为
    「把地址显示出来让用户手输」
  - 只用到 cv2 的编码接口，不做任何图像处理，不依赖 PIL
"""
import html

try:
    import cv2
except Exception:  # pragma: no cover - 管理后台可以不装视觉依赖
    cv2 = None

# 静默区（二维码四周留白）按规范留 4 个模块宽。
# 少于这个宽度，很多手机相机直接识别不出来——这是最容易被忽略、
# 又最难自查的一项：图看着完全正常。
QUIET_ZONE = 4

# 白色底必须显式画出来。页面可能是深色主题，若靠透明底透出深色，
# 二维码就成了"反色码"，绝大多数扫码器不认。
_BG = "#ffffff"
_FG = "#000000"


def available():
    """二维码编码器是否可用（cv2 有没有、版本够不够）。"""
    return cv2 is not None and hasattr(cv2, "QRCodeEncoder_create")


def qr_matrix(text):
    """文本 -> [[0/1]] 的方阵；不可用或编码失败时返回 None。

    只在 qr_svg 内部用，单独暴露是为了让测试能直接校验矩阵尺寸与内容。
    """
    if not available():
        return None
    try:
        img = cv2.QRCodeEncoder_create().encode(text)
    except Exception:
        return None
    if img is None or getattr(img, "ndim", 0) != 2:
        return None
    # 编码器输出 0/255 的灰度图
    return [[1 if px < 128 else 0 for px in row] for row in img.tolist()]


def _path_data(bits):
    """把方阵压成"横向游程"路径。

    逐模块画 <rect> 会产生上千个节点；同一行相邻的深色模块合并成一条
    横线后，节点数大约少一个数量级，SVG 体积从 ~40KB 降到 ~4KB。
    """
    parts = []
    for y, row in enumerate(bits):
        x, n = 0, len(row)
        while x < n:
            if not row[x]:
                x += 1
                continue
            end = x
            while end < n and row[end]:
                end += 1
            parts.append(f"M{x} {y}h{end - x}v1h-{end - x}z")
            x = end
    return "".join(parts)


def qr_svg(text, px=260, title="扫码打开手机摄像头"):
    """生成二维码 SVG；不可用或文本过长时返回 None。

    px 只是渲染尺寸，viewBox 用的是模块数，所以放大不会糊——
    这也是这里输出 SVG 而不是位图的原因（位图在高分屏上容易被插值糊掉，
    糊到一定程度就扫不出来了）。
    """
    bits = qr_matrix(text)
    if not bits:
        return None

    side = len(bits) + QUIET_ZONE * 2
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side}"'
        f' width="{px}" height="{px}" shape-rendering="crispEdges" role="img">'
    ]
    if title:
        out.append(f"<title>{html.escape(title)}</title>")
    out.append(f'<rect width="{side}" height="{side}" fill="{_BG}"/>')
    out.append(f'<g transform="translate({QUIET_ZONE} {QUIET_ZONE})">'
               f'<path d="{_path_data(bits)}" fill="{_FG}"/></g>')
    out.append("</svg>")
    return "".join(out)


def qr_ascii(text, invert=False):
    """终端里用的二维码（半块字符，两行并一行）。

    只在标准输出的终端下有意义，且**依赖终端背景是浅色**——若终端是深色
    主题，二维码会变成反色，多数手机扫不出来。所以调用方应优先用
    qr_ansi()（自带白底），这个函数留给不支持 ANSI 的场合。
    """
    bits = qr_matrix(text)
    if not bits:
        return None

    side = len(bits) + QUIET_ZONE * 2

    def at(r, c):
        r -= QUIET_ZONE
        c -= QUIET_ZONE
        if 0 <= r < len(bits) and 0 <= c < len(bits):
            v = bits[r][c]
            return (not v) if invert else bool(v)
        return bool(invert)  # 静默区：白底

    # ▀ 上半块 / ▄ 下半块 / █ 全块 / 空格
    glyph = {0b00: " ", 0b10: "▀", 0b01: "▄", 0b11: "█"}
    lines = []
    for row in range(0, side, 2):
        chars = []
        for col in range(side):
            top = 1 if at(row, col) else 0
            bot = 1 if (row + 1 < side and at(row + 1, col)) else 0
            chars.append(glyph[(top << 1) | bot])
        lines.append("".join(chars))
    return "\n".join(lines)


def qr_ansi(text, black="\x1b[40m", white="\x1b[47m", reset="\x1b[0m", quiet=True):
    """用 ANSI 背景色画二维码——深浅两种终端主题下都能扫。

    比 qr_ascii 稳的原因：块字符的颜色取决于终端前景色设置，而背景色
    是自己指定的，等于给二维码自带了一块白底。
    """
    bits = qr_matrix(text)
    if not bits:
        return None

    pad = QUIET_ZONE if quiet else 0
    side = len(bits) + pad * 2
    lines = []
    for row in range(side):
        buf = []
        for col in range(side):
            r, c = row - pad, col - pad
            dark = (0 <= r < len(bits) and 0 <= c < len(bits) and bits[r][c])
            buf.append((black if dark else white) + "  ")
        lines.append("".join(buf) + reset)
    return "\n".join(lines)
