"""phone_cam.py（手机浏览器当摄像头）的回归测试。

起因：第一版把证书生成写成了**硬依赖 `cryptography` 包**，而用户 PATH 上的
`python` 恰好没装它（只有项目用的 Anaconda 装了），于是 `python phone_cam.py`
直接退出。这违背了本项目「零依赖 / 优雅降级」的一贯原则。

所以这里把两件事钉住：
1. 模块顶层**不许**引入任何第三方包 —— 任何 Python 3.8+ 都要能跑起来；
2. 证书生成走 openssl 命令行时，要能真的产出一份**带 SAN**的可用证书
   （SAN 缺了浏览器会拒绝，这不是可选项）。
"""
import ast
import os
import subprocess
import sys

import pytest

import phone_cam


# ------------------------------------------------ 零依赖（就是踩过的那个坑）

def _read():
    with open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "phone_cam.py"), encoding="utf-8") as f:
        return f.read()


def test_module_top_level_imports_are_all_stdlib():
    """顶层只许导入标准库。

    这是本次 bug 的直接回归防线：`cryptography` 只能作为**函数内的退路**
    （没装 openssl 时才用），不能出现在模块顶层的导入区 ——
    否则换一个没装它的解释器（例如用户 PATH 上那个）就整个跑不起来。

    用 AST 看真正的 import 语句，**不要用文本匹配**：
    文档里提到包名（例如解释为什么优先用 openssl）会造成误判。
    """
    tree = ast.parse(_read())
    names = []
    for node in tree.body:                     # 只看模块顶层
        if isinstance(node, ast.Import):
            names += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module.split(".")[0])

    stdlib = getattr(sys, "stdlib_module_names", None) or {
        "argparse", "datetime", "http", "ipaddress", "os", "shutil",
        "socket", "ssl", "subprocess", "sys", "threading", "time"}
    third = sorted(set(names) - set(stdlib))
    assert not third, (
        f"phone_cam.py 顶层引入了非标准库 {third}。"
        f"它必须能在任何 Python 3.8+ 上直接运行——第三方包只能是函数内的退路"
    )


def test_cryptography_is_only_a_function_local_fallback():
    """`cryptography` 必须只出现在函数内部（退路），不能出现在顶层。"""
    tree = ast.parse(_read())
    top_imports = [n for n in tree.body
                   if isinstance(n, (ast.Import, ast.ImportFrom))]
    for node in top_imports:
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""])
        assert not any("cryptography" in n for n in names), \
            "cryptography 不该出现在顶层导入区"


def test_openssl_is_preferred_over_cryptography():
    """证书生成必须**先试 openssl**（零依赖），cryptography 只作退路。"""
    src = _read()
    i_openssl = src.find("_gen_with_openssl(ips)")
    i_crypto = src.find("_gen_with_cryptography(ips)")
    assert i_openssl != -1 and i_crypto != -1, "两个生成函数都应存在"
    assert i_openssl < i_crypto, "调用顺序反了：应先试 openssl，再退回 cryptography"


# ------------------------------------------------ 证书真的能用（含 SAN）

@pytest.mark.skipif(phone_cam.find_openssl() is None,
                    reason="本机没有 openssl，跳过命令行证书生成测试")
def test_openssl_cert_has_san(tmp_path, monkeypatch):
    """openssl 生成的证书必须带 SAN。

    现代浏览器（尤其手机端）**强制校验 SAN**：只有 CN 是不够的，
    必须在 SAN 里列出实际访问用的 IP，否则连"继续访问"都点不出来。
    """
    monkeypatch.setattr(phone_cam, "CERT_DIR", str(tmp_path))
    monkeypatch.setattr(phone_cam, "CERT_PEM", str(tmp_path / "c.pem"))
    monkeypatch.setattr(phone_cam, "KEY_PEM", str(tmp_path / "k.pem"))

    assert phone_cam.ensure_cert(["127.0.0.1", "10.1.2.3"]) is True
    assert os.path.exists(phone_cam.CERT_PEM)
    assert os.path.exists(phone_cam.KEY_PEM)

    with open(phone_cam.CERT_PEM, encoding="utf-8") as f:
        pem = f.read()
    assert "BEGIN CERTIFICATE" in pem and "END CERTIFICATE" in pem

    exe = phone_cam.find_openssl()
    r = subprocess.run([exe, "x509", "-in", phone_cam.CERT_PEM, "-noout",
                        "-ext", "subjectAltName"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"证书无法解析：{r.stderr}"
    san = r.stdout
    assert "IP Address:10.1.2.3" in san, (
        f"局域网 IP 没进 SAN，手机浏览器会直接拒绝：{san}"
    )
    assert "IP Address:127.0.0.1" in san, san


def test_ensure_cert_reuses_existing_file(tmp_path, monkeypatch):
    """证书已存在时应直接复用，不要每次启动都重新生成。"""
    monkeypatch.setattr(phone_cam, "CERT_DIR", str(tmp_path))
    monkeypatch.setattr(phone_cam, "CERT_PEM", str(tmp_path / "c.pem"))
    monkeypatch.setattr(phone_cam, "KEY_PEM", str(tmp_path / "k.pem"))
    with open(phone_cam.CERT_PEM, "w", encoding="utf-8") as f:
        f.write("sentinel")
    with open(phone_cam.KEY_PEM, "w", encoding="utf-8") as f:
        f.write("sentinel")
    assert phone_cam.ensure_cert(["127.0.0.1"]) is True
    with open(phone_cam.CERT_PEM, encoding="utf-8") as f:
        assert f.read() == "sentinel", "已有证书被覆盖了"


# ------------------------------------------------ 页面与输出尺寸的硬约定

def test_target_is_16_9():
    """输出必须固定 16:9。

    2d 特征的角度由归一化坐标算出（mediapipe 用 x/W、y/H 两个不同分母），
    宽高比一变角度就系统性偏移（实测 16:9 -> 竖屏：躯干角 6.8° 变 20.7°）。
    默认值一旦改掉，所有模板都会静默失配。
    """
    assert (phone_cam.TARGET_W, phone_cam.TARGET_H) == (1280, 720)
    assert abs(phone_cam.TARGET_W / phone_cam.TARGET_H - 16 / 9) < 1e-6


def test_page_requests_rear_camera_and_uses_secure_context():
    """页面必须请求后置摄像头，且只在 HTTPS 下提供。

    - 前置摄像头普遍带美颜/瘦脸，那是对人体做形变，会直接扭曲骨架关键点，
      属于算法层无法修正的误差。
    - 浏览器只在安全上下文下允许网页访问摄像头，所以服务端必须是 HTTPS。
    """
    page = phone_cam.PAGE
    assert "getUserMedia" in page
    assert "environment" in page, "必须请求后置摄像头（facingMode: environment）"

    src = _read()
    assert "ssl.SSLContext" in src and "wrap_socket" in src, (
        "手机侧服务必须是 HTTPS：http 下浏览器会拒绝授予摄像头权限"
    )


def test_page_guards_against_request_pileup():
    """页面要有「上一帧没发完就跳过」的保护。

    否则网络一慢，fetch 请求会越堆越多，画面延迟无限增长（越用越卡）。
    """
    assert "busy" in phone_cam.PAGE


def test_page_draws_with_letterbox_not_stretch():
    """缩放要用 contain（等比 + 黑边），不能拉伸。

    拉伸会改变画面上人体的比例，等于扭曲骨架 —— 和宽高比问题同源，
    而且是算法层修不回来的误差。
    """
    page = phone_cam.PAGE
    assert "Math.min" in page, "应按 contain 语义取较小的缩放比"
