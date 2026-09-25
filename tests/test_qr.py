"""二维码生成的回归测试。

这里最有价值的一条是**回环解码**：把生成的图重新用 OpenCV 的检测器读回来，
断言和原文一字不差。

为什么非要这么测：一张"扫不出来"的二维码在肉眼看来和正常的**完全一样**，
尺寸、内容、白边都好端端的。只看"函数没抛异常""SVG 里有 path"这类断言，
等于什么都没验。只有真的解一次，才能证明手机扫得出来。

为了连 SVG 一起验（而不是只验中间矩阵），测试会**解析自己生成的 path**
反推出矩阵 —— 好在路径语法是自造的固定形式（`M{x} {y}h{w}v1h-{w}z`），
解析起来只有一行正则。
"""
import re

import pytest

from backend import qr


def _cv2():
    return pytest.importorskip("cv2", reason="没有 OpenCV，二维码功能本就该降级")


def _decode(matrix, scale=10):
    """把 0/1 矩阵补上静默区放大后交给 OpenCV 解码。"""
    cv2 = _cv2()
    import numpy as np

    n = len(matrix)
    side = n + qr.QUIET_ZONE * 2
    img = np.full((side, side), 255, np.uint8)
    for y, row in enumerate(matrix):
        for x, v in enumerate(row):
            if v:
                img[y + qr.QUIET_ZONE, x + qr.QUIET_ZONE] = 0
    big = cv2.resize(img, None, fx=scale, fy=scale,
                     interpolation=cv2.INTER_NEAREST)
    text, _pts, _ = cv2.QRCodeDetector().detectAndDecode(big)
    return text


def _matrix_from_svg(svg):
    """从生成的 SVG 里把模块矩阵还原出来。"""
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert m, "SVG 缺少 viewBox"
    side = int(m.group(1))
    assert side == int(m.group(2)), "二维码必须是正方形"

    n = side - qr.QUIET_ZONE * 2
    grid = [[0] * n for _ in range(n)]

    g = re.search(r'<g transform="translate\((\d+) (\d+)\)">', svg)
    assert g, "缺少坐标平移组（静默区就是靠它实现的）"
    ox, oy = int(g.group(1)), int(g.group(2))
    assert (ox, oy) == (qr.QUIET_ZONE, qr.QUIET_ZONE), \
        "平移量必须等于静默区宽度，否则白边会被吃掉"

    d = re.search(r'<path d="([^"]*)"', svg)
    assert d, "SVG 里没有 path"
    for x, y, w in re.findall(r"M(\d+) (\d+)h(\d+)v1h-\d+z", d.group(1)):
        x, y, w = int(x), int(y), int(w)
        for c in range(x, x + w):
            grid[y][c] = 1
    return grid


# ------------------------------------------------------------ 核心：能扫出来

def test_common_urls_decode_back_to_themselves():
    """常见的局域网地址都要能扫回来。"""
    if not qr.available():
        pytest.skip("没有 OpenCV 编码器")

    for url in ("https://192.168.1.5:8443/",
                "https://10.220.63.201:8443/",
                "https://172.16.0.100:8443/?t=abc"):
        got = _decode(qr.qr_matrix(url))
        assert got == url, f"扫码结果对不上：{got!r} != {url!r}"


def test_svg_content_decodes_to_the_same_text():
    """SVG 里画出来的内容必须和原文一致（不是"差不多"）。

    这条把整条链路串起来验：矩阵 -> path 压缩 -> SVG 拼接。
    压缩游程时写错一个坐标，肉眼看图依然正常，但扫出来是乱的。
    """
    if not qr.available():
        pytest.skip("没有 OpenCV 编码器")

    url = "https://192.168.100.23:8443/"
    svg = qr.qr_svg(url)
    assert svg
    assert _decode(_matrix_from_svg(svg)) == url


def test_svg_has_white_background_and_quiet_zone():
    """白底与静默区都不是可选项。

    - 没有白底：深色主题页面下会变成"反色二维码"，多数扫码器不认
    - 静默区不足 4 模块：很多手机相机直接识别不出来
    两者都属于"看起来完全正常"的故障。
    """
    if not qr.available():
        pytest.skip("没有 OpenCV 编码器")

    svg = qr.qr_svg("https://192.168.1.5:8443/")
    assert "#ffffff" in svg, "缺少白色底"
    assert qr.QUIET_ZONE >= 4, "静默区不得小于 4 个模块"

    n = len(qr.qr_matrix("https://192.168.1.5:8443/"))
    assert f'viewBox="0 0 {n + 8} {n + 8}"' in svg


def test_longer_text_uses_a_bigger_version():
    """内容变长时矩阵要自动变大（版本自适应）。"""
    if not qr.available():
        pytest.skip("没有 OpenCV 编码器")

    short = len(qr.qr_matrix("https://a.b/"))
    long_ = len(qr.qr_matrix("https://192.168.100.200:8443/?token=" + "a" * 40))
    assert long_ > short, "长文本必须用更大的版本，否则会截断内容"


# ------------------------------------------------------------ 优雅降级

def test_missing_cv2_degrades_to_none(monkeypatch):
    """没装 cv2 时必须安静地返回 None，交给上层降级。

    cv2 是本项目的可选依赖（只跑管理后台可以不装），
    二维码生成不能因此抛异常把整个接口带崩。
    """
    monkeypatch.setattr(qr, "cv2", None)
    assert qr.available() is False
    assert qr.qr_matrix("https://a.b/") is None
    assert qr.qr_svg("https://a.b/") is None


def test_encode_failure_is_contained(monkeypatch):
    """编码器内部出错也要兜住，只返回 None。"""
    class Boom:
        @staticmethod
        def QRCodeEncoder_create():
            class E:
                @staticmethod
                def encode(_text):
                    raise RuntimeError("内部错误")
            return E()

    monkeypatch.setattr(qr, "cv2", Boom)
    assert qr.qr_svg("https://a.b/") is None


# ------------------------------------------------------------ 终端显示

def test_terminal_renderers_shape():
    """终端版要能出图（独立脚本在命令行里扫码用）。"""
    if not qr.available():
        pytest.skip("没有 OpenCV 编码器")

    url = "https://192.168.1.5:8443/"
    art = qr.qr_ascii(url)
    # 半块字符是两行并一行，所以行数约为边长的一半
    n = len(qr.qr_matrix(url)) + qr.QUIET_ZONE * 2
    assert len(art.split("\n")) == (n + 1) // 2
    assert set("".join(art.split("\n"))) <= set(" ▀▄█")

    ansi = qr.qr_ansi(url)
    assert ansi.count("\x1b[0m") == n, "每行都要复位颜色，否则终端会被染色"
