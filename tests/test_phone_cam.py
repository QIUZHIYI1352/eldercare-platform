"""手机当摄像头（手机浏览器直接推流）的回归测试。

起因一：第一版把证书生成写成了**硬依赖 `cryptography` 包**，而用户 PATH 上的
`python` 恰好没装它（只有项目用的 Anaconda 装了），于是 `python phone_cam.py`
直接退出。这违背了本项目「零依赖 / 优雅降级」的一贯原则。

起因二：手机接入后来从「独立脚本」升级成了「平台内嵌能力」（平台页面出二维码，
扫码即连）。实现搬进 `backend/vision/phone_cam.py` 后，两条路共用同一份代码——
这里额外钉一条，防止有人哪天又在顶层脚本里复制一份出来。

所以本文件守住三件事：
1. 模块顶层**不许**引入任何第三方包 —— 任何 Python 3.8+ 都要能跑起来；
2. 证书走 openssl 命令行时要产出**带 SAN** 的可用证书（SAN 缺了浏览器直接拒绝）；
3. 服务本身能用：页面可取、推帧能收、端口绑定范围正确。
"""
import ast
import http.client
import importlib.util
import os
import re
import ssl
import subprocess
import sys

import pytest

from backend.vision import phone_cam
from tests.conftest import as_student, as_teacher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(ROOT, "backend", "vision", "phone_cam.py")
WRAPPER_PATH = os.path.join(ROOT, "phone_cam.py")

HAVE_CERT_TOOL = (phone_cam.find_openssl() is not None
                  or importlib.util.find_spec("cryptography") is not None)


def _read(path=MODULE_PATH):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------ 零依赖（就是踩过的那个坑）

def test_module_top_level_imports_are_all_stdlib():
    """顶层只许导入标准库。

    这是那次 bug 的直接回归防线：`cryptography` 只能作为**函数内的退路**
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
        f"phone_cam 顶层引入了非标准库 {third}。"
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
    i_openssl = src.find("_gen_with_openssl(ips")
    i_crypto = src.find("_gen_with_cryptography(ips")
    assert i_openssl != -1 and i_crypto != -1, "两个生成函数都应存在"
    assert i_openssl < i_crypto, "调用顺序反了：应先试 openssl，再退回 cryptography"


def test_top_level_script_is_a_thin_wrapper():
    """顶层 phone_cam.py 只能是对 backend 实现的薄封装。

    两条路（平台内嵌 / 独立脚本）共用一份实现是有意的：如果哪天有人在顶层
    再写一份出来，行为必然在某次改动后分叉，最后表现成
    「平台上扫码能连、命令行扫码连不上」这种极难排查的问题。
    """
    src = _read(WRAPPER_PATH)
    assert "from backend.vision.phone_cam import main" in src, \
        "顶层脚本必须复用 backend.vision.phone_cam 的实现"
    assert "def main(" not in src, "顶层脚本不该自己再实现一份 main"
    assert len(src.splitlines()) < 40, "顶层脚本变胖了，可能又把逻辑搬回来了"


def test_no_third_party_import_in_wrapper():
    """独立脚本本身也要零第三方依赖（从项目根目录直接 `python phone_cam.py`）。"""
    tree = ast.parse(_read(WRAPPER_PATH))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = (node.names[0].name if isinstance(node, ast.Import)
                   else (node.module or ""))
            assert mod.startswith("backend") or mod in sys.stdlib_module_names, \
                f"顶层脚本引入了不该有的依赖：{mod}"


# ------------------------------------------------ 证书真的能用（含 SAN）

@pytest.fixture(scope="session")
def _cert_template(tmp_path_factory):
    """整轮测试只签一次证书。

    RSA 2048 的生成在 Windows 上要一两秒，每个用例都签一次会把套件拖慢十几秒，
    而绝大多数用例只关心"证书能用"，不关心签名过程本身。
    """
    if not HAVE_CERT_TOOL:
        pytest.skip("本机既没有 openssl 也没有 cryptography，无法签发证书")

    d = tmp_path_factory.mktemp("phonecam-cert")
    saved = (phone_cam.CERT_DIR, phone_cam.CERT_PEM,
             phone_cam.KEY_PEM, phone_cam.CERT_IPS)
    phone_cam.CERT_DIR = str(d)
    phone_cam.CERT_PEM = str(d / "c.pem")
    phone_cam.KEY_PEM = str(d / "k.pem")
    phone_cam.CERT_IPS = str(d / "c.ips")
    try:
        ok = phone_cam.ensure_cert([phone_cam.lan_ip(), "127.0.0.1"], quiet=True)
    finally:
        (phone_cam.CERT_DIR, phone_cam.CERT_PEM,
         phone_cam.KEY_PEM, phone_cam.CERT_IPS) = saved
    assert ok, "无法签发测试用证书"
    return d


@pytest.fixture()
def cert_workspace(_cert_template, monkeypatch):
    """把模块的证书路径指向那份已签好的证书——用例只复用，不重签。

    注意：这份证书是**全轮共享**的，所以凡是要把证书写坏/重签的用例
    必须改用下面那个 fresh_cert，否则会污染后续用例。
    """
    monkeypatch.setattr(phone_cam, "CERT_DIR", str(_cert_template))
    monkeypatch.setattr(phone_cam, "CERT_PEM", str(_cert_template / "c.pem"))
    monkeypatch.setattr(phone_cam, "KEY_PEM", str(_cert_template / "k.pem"))
    monkeypatch.setattr(phone_cam, "CERT_IPS", str(_cert_template / "c.ips"))
    return _cert_template


@pytest.fixture()
def fresh_cert(tmp_path, monkeypatch):
    """全新目录，专门用来测"签证书"这件事本身（会真的重新生成）。"""
    monkeypatch.setattr(phone_cam, "CERT_DIR", str(tmp_path))
    monkeypatch.setattr(phone_cam, "CERT_PEM", str(tmp_path / "c.pem"))
    monkeypatch.setattr(phone_cam, "KEY_PEM", str(tmp_path / "k.pem"))
    monkeypatch.setattr(phone_cam, "CERT_IPS", str(tmp_path / "c.ips"))
    return tmp_path


@pytest.mark.skipif(phone_cam.find_openssl() is None,
                    reason="本机没有 openssl，跳过命令行证书生成测试")
def test_openssl_cert_has_san(fresh_cert):
    """openssl 生成的证书必须带 SAN。

    现代浏览器（尤其手机端）**强制校验 SAN**：只有 CN 是不够的，
    必须在 SAN 里列出实际访问用的 IP，否则连"继续访问"都点不出来。
    """
    assert phone_cam.ensure_cert(["127.0.0.1", "10.1.2.3"], quiet=True) is True
    assert os.path.exists(phone_cam.CERT_PEM)
    assert os.path.exists(phone_cam.KEY_PEM)

    with open(phone_cam.CERT_PEM, encoding="utf-8") as f:
        pem = f.read()
    assert "BEGIN CERTIFICATE" in pem and "END CERTIFICATE" in pem

    r = subprocess.run([phone_cam.find_openssl(), "x509", "-in",
                        phone_cam.CERT_PEM, "-noout", "-ext", "subjectAltName"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"证书无法解析：{r.stderr}"
    assert "IP Address:10.1.2.3" in r.stdout, (
        f"局域网 IP 没进 SAN，手机浏览器会直接拒绝：{r.stdout}")
    assert "IP Address:127.0.0.1" in r.stdout


def test_ensure_cert_reuses_existing_file(fresh_cert):
    """证书仍覆盖当前地址时应直接复用，不要每次启动都重新生成。"""
    with open(phone_cam.CERT_PEM, "w", encoding="utf-8") as f:
        f.write("sentinel")
    with open(phone_cam.KEY_PEM, "w", encoding="utf-8") as f:
        f.write("sentinel")
    with open(phone_cam.CERT_IPS, "w", encoding="utf-8") as f:
        f.write("127.0.0.1")

    assert phone_cam.ensure_cert(["127.0.0.1"], quiet=True) is True
    with open(phone_cam.CERT_PEM, encoding="utf-8") as f:
        assert f.read() == "sentinel", "已有证书被覆盖了"


@pytest.mark.skipif(not HAVE_CERT_TOOL, reason="本机没有任何可用的证书生成手段")
def test_cert_is_reissued_when_lan_ip_changes(fresh_cert):
    """换 WiFi 导致局域网地址变化时，必须重签证书。

    否则新地址不在 SAN 里，浏览器报的是「证书名称不匹配」——
    那比自签告警更难绕过，有的浏览器连「继续前往」都不给。
    """
    assert phone_cam.ensure_cert(["10.0.0.5"], quiet=True) is True
    before = open(phone_cam.CERT_PEM, encoding="utf-8").read()

    assert phone_cam.ensure_cert(["192.168.1.77"], quiet=True) is True
    after = open(phone_cam.CERT_PEM, encoding="utf-8").read()
    assert after != before, "地址变了却没重签证书"
    with open(phone_cam.CERT_IPS, encoding="utf-8") as f:
        assert "192.168.1.77" in f.read()


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
    assert "Math.min" in phone_cam.PAGE, "应按 contain 语义取较小的缩放比"


def test_page_autostarts_and_keeps_screen_awake():
    """扫码后应当自动开始，不用再找按钮；同时尽量让手机别息屏。

    - 自动开始：扫码 -> 「继续前往」->「允许」三步就完事
    - 息屏后浏览器会停掉摄像头，画面直接断，所以尽量申请屏幕常亮
    """
    page = phone_cam.PAGE
    assert "addEventListener('load', start)" in page, "页面应自动开始推流"
    assert "getElementById('btn')" in page, "自动开始失败时仍要留一个可点的按钮"
    assert "wakeLock" in page, "应尝试申请屏幕常亮，否则手机息屏就断流"


def test_page_does_not_get_stuck_when_permission_hangs():
    """权限请求悬着不返回时，按钮必须能重新点。

    浏览器弹了权限框却一直不返回（标签页失焦、系统把摄像头挂起）时，
    按钮会永久停在禁用态 —— 那等于"卡住且没有任何出路"，
    比直接报错更糟：使用者连重试都做不到。
    """
    page = phone_cam.PAGE
    assert "setTimeout" in page and "重试" in page, \
        "缺少超时兜底：权限悬着时按钮会一直是禁用的"
    assert "gen" in page, \
        "需要尝试编号：否则超时后重试会让上一次请求也回来，开出两条流"


# ------------------------------------------------ 服务本身

@pytest.fixture()
def service(cert_workspace):
    """起一个用不常用端口的服务实例，结束一定停掉。"""
    if not HAVE_CERT_TOOL:
        pytest.skip("本机既没有 openssl 也没有 cryptography，无法签发证书")
    svc = phone_cam.PhoneCamService(https_port=18143, feed_port=18144)
    try:
        svc.start()
    except RuntimeError as e:            # 端口恰好被占
        pytest.skip(f"测试端口不可用：{e}")
    yield svc
    svc.stop()


def _https_get(svc, path):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    c = http.client.HTTPSConnection(svc.host_ip, svc.https_port,
                                    context=ctx, timeout=8)
    try:
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def _https_push(svc, data):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    c = http.client.HTTPSConnection(svc.host_ip, svc.https_port,
                                    context=ctx, timeout=8)
    try:
        c.request("POST", "/push", body=data,
                  headers={"Content-Type": "image/jpeg"})
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def test_service_serves_page_and_reports_running(service):
    code, body = _https_get(service, "/")
    assert code == 200
    page = body.decode("utf-8")
    assert "getUserMedia" in page
    assert str(phone_cam.TARGET_W) in page, "页面没拿到目标宽度"

    st = service.status()
    assert st["running"] is True
    assert st["https_url"].startswith("https://")
    assert st["frames"] == 0 and st["connected"] is False


def test_push_then_status_reports_connected(service):
    """推一帧后状态要变成"已连接"，并回传累计帧数。"""
    code, body = _https_push(service, b"\xff\xd8jpeg-bytes\xff\xd9")
    assert code == 200
    assert re.search(rb'"n":\s*1', body), f"没回传帧数：{body!r}"

    st = service.status()
    assert st["frames"] == 1
    assert st["connected"] is True
    assert st["last_frame_age"] is not None


def test_status_fps_stays_zero_until_enough_samples(service):
    """样本太少时不给帧率读数。

    两三帧的时间跨度可能是毫秒级，会算出几百 fps 的假读数，
    显示在界面上只会让人以为出了问题。
    """
    for _ in range(3):
        _https_push(service, b"x")
    assert service.status()["fps"] == 0.0


def test_push_rejects_empty_and_unknown_path(service):
    assert _https_push(service, b"")[0] == 400
    assert _https_get(service, "/nope")[0] == 404


def test_only_the_phone_port_is_exposed_to_lan(service):
    """画面转发口必须只绑回环，HTTPS 页才绑 0.0.0.0。

    MJPEG 是给本机识别进程读的，没有任何理由对局域网开放——
    少暴露一份实时画面，也少弹一个防火墙授权框。
    """
    assert service._https_srv.server_address[0] == "0.0.0.0"
    assert service._feed_srv.server_address[0] == "127.0.0.1"


def test_start_is_idempotent_and_stop_releases(service):
    """重复 start 不应起第二个监听；stop 后状态要归位。"""
    same = service.start()
    assert same["running"] is True
    assert service._feed_srv is not None

    service.stop()
    st = service.status()
    assert st["running"] is False
    assert st["https_url"] is None
    assert st["frames"] == 0


def test_port_conflict_raises_actionable_error(fresh_cert):
    """端口被占时要抛出「下一步怎么办」，而不是裸 OSError。

    这类报错只有用户能看见，抛个 Address already in use 等于让人干瞪眼。
    """
    if not HAVE_CERT_TOOL:
        pytest.skip("本机没有可用的证书生成手段")
    import socket

    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 18243))
    blocker.listen(1)
    try:
        svc = phone_cam.PhoneCamService(https_port=18243, feed_port=18244)
        with pytest.raises(RuntimeError) as e:
            svc.start()
        assert "端口" in str(e.value) and "占用" in str(e.value)
    finally:
        blocker.close()


# ------------------------------------------------ 平台接口

def test_phone_cam_endpoints_require_teacher(client, monkeypatch, service):
    """接入手机摄像头是管理动作：学生只该被拒绝。"""
    monkeypatch.setattr(phone_cam, "SERVICE", service)

    assert client.post("/api/phone-cam/start").status_code == 401
    assert client.post("/api/phone-cam/start",
                       headers=as_student(client)).status_code == 403
    assert client.post("/api/phone-cam/stop",
                       headers=as_student(client)).status_code == 403


def test_start_returns_qr_and_registers_device(client, monkeypatch, service):
    """教师点一下按钮，拿到二维码 + 视频源，并自动登记一台设备。"""
    monkeypatch.setattr(phone_cam, "SERVICE", service)
    h = as_teacher(client)

    r = client.post("/api/phone-cam/start", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["https_url"].startswith("https://")
    assert d["feed_url"].startswith("http://127.0.0.1:")
    assert d["device_name"] == "手机摄像头"
    if d["qr_svg"] is not None:          # cv2 缺失时允许降级
        assert d["qr_svg"].startswith("<svg")
        assert "<path" in d["qr_svg"]

    # 设备表里出现一台「手机摄像头」，且名字不暴露 127.0.0.1 这种实现细节
    items = client.get("/api/devices", headers=h).json()["items"]
    dev = [x for x in items if x["id"] == d["device_id"]]
    assert len(dev) == 1
    assert dev[0]["name"] == "手机摄像头"
    assert "127.0.0.1" not in dev[0]["name"]

    # 重复启动不应再多出一条设备（地址稳定 => 复用同一条记录）
    again = client.post("/api/phone-cam/start", headers=h).json()
    assert again["device_id"] == d["device_id"]
    items2 = client.get("/api/devices", headers=h).json()["items"]
    assert len(items2) == len(items)

    assert client.get("/api/phone-cam/status", headers=h).json()["running"] is True
    assert client.post("/api/phone-cam/stop", headers=h).json()["ok"] is True


def test_device_status_reflects_live_connection(client, monkeypatch, service):
    """设备列表里的状态要跟着手机的真实连接状态走。

    设备表里 status 是静态列，手机关掉页面后数据库并不知道；
    不做覆盖的话列表会一直挂着一个早就不在线的「在线」。
    """
    monkeypatch.setattr(phone_cam, "SERVICE", service)
    h = as_teacher(client)
    did = client.post("/api/phone-cam/start", headers=h).json()["device_id"]

    def status_of():
        items = client.get("/api/devices", headers=h).json()["items"]
        return next(x["status"] for x in items if x["id"] == did)

    assert status_of() == "offline", "还没推帧就显示在线，等于误报"

    _https_push(service, b"frame")
    assert status_of() == "online"
