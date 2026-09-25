"""手机当摄像头——手机**不用装任何 App**，浏览器打开一个网址就能把画面送进平台。

为什么需要它：IP Webcam 这类应用只在 Google Play 上架，国内应用商店搜不到；
「手机变虚拟摄像头」的软件（iVCam / DroidCam / EV虚拟摄像头）都要在电脑端
再装一层客户端。这条路把这些都省掉——手机只用自带浏览器。

数据流：
    手机浏览器 --HTTPS--> 开启摄像头并抓帧 --POST /push--> 本进程的帧存储
    平台进程 --HTTP(仅本机)--> 读取 MJPEG --> 交给普通的 VideoSource 识别

**为什么必须 HTTPS**：浏览器只在「安全上下文」下允许网页访问摄像头，
`http://192.168.x.x` 会被直接拒绝。所以自签一张证书，手机上会提示
「连接不私密」，点继续前往即可（画面只在局域网内传输，不出公网）。

**为什么自签证书不用第三方包**：优先调用 openssl 命令行（Git for Windows
自带），没有才退回 cryptography。这样任何 Python 3.8+ 都能跑，
不必纠结用哪个解释器——踩过一次：裸 `python` 没装 cryptography，
脚本直接退出，用户只看到「跑不了」。

两个监听端口的分工（这是刻意的，别合并）：
  - HTTPS 服务绑 0.0.0.0：手机要能连进来，必然对局域网开放
  - MJPEG 服务绑 127.0.0.1：只给本机的识别进程读，不对局域网暴露画面，
    顺带少弹一个 Windows 防火墙授权框
"""
import argparse
import datetime
import http.server
import ipaddress
import os
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
from collections import deque

CERT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), ".workbuddy")
CERT_PEM = os.path.join(CERT_DIR, "phone_cam_cert.pem")
KEY_PEM = os.path.join(CERT_DIR, "phone_cam_key.pem")
# 记录证书里签了哪些 IP。换了 WiFi 导致局域网地址变化时，证书的 SAN 就不含
# 新地址了，浏览器会报「证书名称不匹配」——那比自签告警更难绕过，
# 有的浏览器连「继续前往」都不给。所以地址变了必须重签。
CERT_IPS = os.path.join(CERT_DIR, "phone_cam_cert.ips")

BOUNDARY = "phonecam"

DEFAULT_HTTPS_PORT = 8443
DEFAULT_FEED_PORT = 8444

# 输出画面的目标尺寸。固定 16:9 是刻意的：二维特征对宽高比敏感
# （mediapipe 归一化用 x/W、y/H 两个不同分母），测过 16:9 换成 9:16
# 能让躯干倾角差出 17°，远超阈值——录模板和实际推流必须同比例。
TARGET_W, TARGET_H = 1280, 720

# 超过这么久没有新帧，就认为手机已经断开（关页面 / 锁屏 / 走出 WiFi）
CONNECTED_TTL = 3.0


# ------------------------------------------------------------------ 环境探测

def lan_ip():
    """取本机在局域网里的地址（不会真的发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def find_openssl():
    """找 openssl 可执行文件（PATH 优先，其次 Git for Windows 的常见位置）。"""
    exe = shutil.which("openssl")
    if exe:
        return exe
    for p in (r"C:\Program Files\Git\usr\bin\openssl.exe",
              r"C:\Program Files (x86)\Git\usr\bin\openssl.exe",
              r"C:\Program Files\Git\mingw64\bin\openssl.exe"):
        if os.path.exists(p):
            return p
    return None


def _bind_fails(port, host):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:                     # Windows
            s.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        else:                                         # Unix
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def port_busy(port, hosts=("127.0.0.1", "0.0.0.0")):
    """端口是否已被占用（任一探测地址绑不上就算占用）。

    用**试着 bind**而不是 connect 判断，这一点很关键：
    connect 到一个没人监听的本地端口，在 Windows 上并不一定立刻返回
    「连接被拒」——防火墙会静默丢包，于是要一直等到超时。实测一次探测
    白等 500ms，启动时连查两个端口就是 1 秒的假卡顿；改 bind 后是 0.1ms。

    两个探测地址都必须试，因为 Windows 把「通配地址」和「具体地址」当成
    不同的绑定目标：别人占了 0.0.0.0:8443 时，往 127.0.0.1:8443 上绑仍然
    会成功（反之亦然），只探一个就会把「占用」误判成「空闲」。

    还必须显式用 SO_EXCLUSIVEADDRUSE：Windows 下默认的 SO_REUSEADDR
    允许把端口从正在监听的进程手里**抢过来**（实测确实能 bind 成功），
    等于把「占用」直接吞掉。Unix 上则相反——要用 SO_REUSEADDR 才能让
    TIME_WAIT 不造成误判，而它并不会掩盖正在监听的 socket。
    """
    if isinstance(hosts, str):
        hosts = (hosts,)
    return any(_bind_fails(port, h) for h in hosts)


# ------------------------------------------------------------------ 自签证书

def _save_cert_ips(ips):
    try:
        os.makedirs(CERT_DIR, exist_ok=True)
        with open(CERT_IPS, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(set(ips))))
    except Exception:
        pass  # 只是优化项，写不了不影响主流程


def _cert_covers(ips):
    """已有证书是否覆盖这些 IP（覆盖才能复用）。"""
    if not (os.path.exists(CERT_PEM) and os.path.exists(KEY_PEM)):
        return False
    try:
        with open(CERT_IPS, encoding="utf-8") as f:
            have = {x.strip() for x in f if x.strip()}
    except Exception:
        return False
    return set(ips).issubset(have)


def _gen_with_openssl(ips, quiet=False):
    """用 openssl 命令行生成自签证书——不需要任何 Python 包。"""
    exe = find_openssl()
    if not exe:
        return False
    os.makedirs(CERT_DIR, exist_ok=True)
    names = ["DNS:localhost", "IP:127.0.0.1"]
    for ip in sorted(set(ips)):
        if ip != "127.0.0.1":
            names.append(f"IP:{ip}")
    cmd = [exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
           "-keyout", KEY_PEM, "-out", CERT_PEM, "-days", "3650",
           "-subj", "/CN=phone-cam",
           "-addext", "subjectAltName=" + ",".join(names)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        if not quiet:
            print(f"  openssl 调用失败：{e}")
        return False
    if r.returncode == 0 and os.path.exists(CERT_PEM) and os.path.exists(KEY_PEM):
        _save_cert_ips(ips)
        if not quiet:
            print("  已用 openssl 生成（无需任何 Python 第三方包）")
        return True
    if not quiet:
        detail = (r.stderr or "").strip().splitlines()
        print("  openssl 生成失败：" + (detail[-1] if detail
                                       else "退出码 %d（版本可能不支持 -addext）"
                                            % r.returncode))
    return False


def _gen_with_cryptography(ips, quiet=False):
    """退路：用 cryptography 包生成。只有没装 openssl 时才会走到这里。"""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except Exception:
        return False

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "phone-cam")])
    san = [x509.DNSName("localhost")]
    for ip in set(list(ips) + ["127.0.0.1"]):
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .sign(key, hashes.SHA256()))

    os.makedirs(CERT_DIR, exist_ok=True)
    with open(KEY_PEM, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()))
    with open(CERT_PEM, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    _save_cert_ips(ips)
    if not quiet:
        print("  已用 cryptography 生成")
    return True


def ensure_cert(ips, quiet=False):
    """自签证书（带 SAN）。已存在且覆盖当前地址就直接复用。

    顺序刻意如此：openssl 命令行**零依赖**，任何 Python 都能跑；
    cryptography 只是没装 openssl 时的退路。
    """
    if _cert_covers(ips):
        return True
    if not quiet and (os.path.exists(CERT_PEM) or os.path.exists(KEY_PEM)):
        print("  局域网地址有变化，重新签发证书...")
    elif not quiet:
        print("正在生成自签证书（首次运行一次，之后复用）...")

    if _gen_with_openssl(ips, quiet) or _gen_with_cryptography(ips, quiet):
        if not quiet:
            print(f"  证书已写入 {CERT_DIR}")
        return True

    if not quiet:
        print()
        print("!! 无法生成证书：本机既没有 openssl，也没有 Python 的 cryptography 包。")
        print("   任选其一即可：")
        print("     a) 安装 Git for Windows（自带 openssl），然后重跑；")
        print("     b) 执行  pip install cryptography   再重跑。")
    return False


# ------------------------------------------------------------------ 帧存储

class FrameStore:
    """保存手机推上来的最新一帧，供 MJPEG 端和状态接口消费。

    只保留"最新一帧"是有意的：识别进程要的是实时画面，
    积压的旧帧只会让延迟越来越大（表现为"越用越卡"）。
    """

    # 用来估算实测帧率的窗口：15fps 时约等于最近 6 秒
    WINDOW = 90

    def __init__(self):
        self.lock = threading.Condition()
        self.jpeg = None
        self.count = 0
        self.last_at = 0.0
        self._times = deque(maxlen=self.WINDOW)

    def put(self, buf):
        with self.lock:
            self.jpeg = buf
            self.count += 1
            self.last_at = time.time()
            self._times.append(self.last_at)
            self.lock.notify_all()

    def reset(self):
        with self.lock:
            self.jpeg = None
            self.count = 0
            self.last_at = 0.0
            self._times.clear()

    def get_newer_than(self, seen, timeout=2.0):
        """等到有比 seen 更新的帧（或超时），返回 (jpeg, count)。

        注意 jpeg is None 也要等：服务重启后 count 归零，而调用方记着的
        seen 还是旧的大值，只比 count == seen 的话会立刻返回、
        外层循环随即空转烧 CPU。
        """
        with self.lock:
            if self.count == seen or self.jpeg is None:
                self.lock.wait(timeout)
            return self.jpeg, self.count

    def fps(self):
        """按最近若干帧的时间跨度算实测帧率（不是设定值）。

        样本太少时不给数：两三帧的时间跨度可能是毫秒级，
        算出来是几百 fps 的假读数，显示出来只会让人以为出了问题。
        """
        with self.lock:
            ts = list(self._times)
        if len(ts) < 8:
            return 0.0
        span = ts[-1] - ts[0]
        return (len(ts) - 1) / span if span > 0 else 0.0

    def age(self):
        """距离最后一帧过去了多少秒；从没收到过返回 None。"""
        with self.lock:
            if not self.last_at:
                return None
            return time.time() - self.last_at


FRAME = FrameStore()
# 服务停止时用它让 MJPEG 的长连接循环主动退出（否则 shutdown 只关掉 accept，
# 已建立的长连接会一直挂着，端口迟迟不释放）
_STOP = threading.Event()


class Settings:
    """手机页面需要知道的参数（由服务启动时写入）。"""

    fps = 15
    target_w = TARGET_W
    target_h = TARGET_H


SETTINGS = Settings()


# ------------------------------------------------------------------ 手机页面

PAGE = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<title>手机摄像头</title>
<style>
  html,body{margin:0;height:100%;background:#111;color:#eee;
            font:16px/1.5 system-ui,-apple-system,"Microsoft YaHei",sans-serif}
  #wrap{display:flex;flex-direction:column;height:100%}
  video{width:100%;flex:1;object-fit:contain;background:#000}
  #bar{padding:12px 14px;background:#1c1c1c;
       padding-bottom:calc(12px + env(safe-area-inset-bottom))}
  #btn{width:100%;padding:16px;font-size:18px;border:0;border-radius:10px;
       background:#2f7d32;color:#fff}
  #btn[disabled]{background:#3a3a3a;color:#8a8a8a}
  #msg{margin-top:8px;font-size:15px;color:#9ccc65;word-break:break-all}
  .warn{color:#ffb300 !important} .err{color:#ef5350 !important}
</style></head>
<body><div id="wrap">
  <video id="v" autoplay playsinline muted></video>
  <div id="bar">
    <button id="btn">点这里开始</button>
    <div id="msg">正在准备摄像头…</div>
  </div>
</div>
<script>
const TW = {{TW}}, TH = {{TH}}, FPS = {{FPS}};
const v = document.getElementById('v'), msg = document.getElementById('msg'),
      btn = document.getElementById('btn');
let timer = null, sent = 0, acked = 0, fail = 0, busy = false, running = false;
// 每次尝试的编号。超时兜底放开的按钮允许用户重试，而原来那次请求仍可能
// 迟一步返回——用编号把过期的那次作废，否则会同时开出两条摄像头流。
let gen = 0;

function say(t, cls){ msg.textContent = t; msg.className = cls || ''; }

async function start(){
  if (running) return;
  const my = ++gen;
  btn.disabled = true;
  btn.textContent = '正在请求权限…';
  say('正在请求摄像头权限，请点「允许」…');

  // 权限提示若迟迟不返回（个别浏览器在标签页失焦、或系统层把摄像头挂起时
  // 会一直悬着），按钮就会永久停在禁用态——那等于"没有任何办法往下走"。
  // 超时后把按钮放开，让人至少能重试。
  const guard = setTimeout(() => {
    if (running || my !== gen) return;
    gen++;                                  // 作废这次尝试
    btn.disabled = false;
    btn.textContent = '点这里重试';
    say('摄像头还没响应。请确认已点「允许」；若曾点过拒绝，'
        + '需要到浏览器设置里给本页放开摄像头权限，然后点按钮重试。', 'warn');
  }, 20000);

  let stream;
  try {
    // facingMode: environment = 后置摄像头。前置普遍带美颜/瘦脸，
    // 会直接扭曲骨架关键点，属于算法层无法修正的误差。
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: {ideal:'environment'},
               width: {ideal: TW}, height: {ideal: TH},
               frameRate: {ideal: FPS} }
    });
  } catch (e) {
    clearTimeout(guard);
    if (my === gen) {
      btn.disabled = false;
      btn.textContent = '点这里重试';
    }
    say('打不开摄像头：' + e.name + ' —— ' + e.message
        + '（若之前点过拒绝，需要到浏览器设置里放开本页摄像头权限）', 'err');
    return;
  }
  clearTimeout(guard);

  if (my !== gen) {                         // 已被更新的一次取代，丢弃这条流
    stream.getTracks().forEach(t => t.stop());
    return;
  }

  v.srcObject = stream;
  try { await v.play(); } catch (e) { /* 自动播放被拦也继续推流 */ }
  running = true;
  btn.disabled = true;
  btn.textContent = '正在推流';
  keepAwake();
  schedule();
  say('已开始，等待电脑接收…');
}

function keepAwake(){
  // 手机息屏后浏览器通常会停掉摄像头，画面就断了。
  // wakeLock 需要安全上下文（正好我们有 HTTPS），失败也不影响推流。
  if (!('wakeLock' in navigator)) return;
  const req = () => { try { navigator.wakeLock.request('screen').catch(()=>{}); } catch(e){} };
  req();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) req(); });
}

function schedule(){
  if (timer) clearInterval(timer);
  timer = setInterval(draw, 1000 / FPS);
}

const canvas = document.createElement('canvas');
canvas.width = TW; canvas.height = TH;
const ctx = canvas.getContext('2d');

// 先按相机自身比例原样绘制，再由 canvas 输出固定 16:9。
// 用 contain（等比缩放 + 留黑边）而不是拉伸——拉伸会扭曲骨架，
// 那是算法层修不回来的误差。
function draw(){
  const vw = v.videoWidth, vh = v.videoHeight;
  if (!vw || !vh) { return; }
  const s = Math.min(TW / vw, TH / vh);
  const dw = vw * s, dh = vh * s;
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, TW, TH);
  ctx.drawImage(v, (TW - dw) / 2, (TH - dh) / 2, dw, dh);
  push();
}

function push(){
  // 上一帧还没发完就跳过——否则网络一慢，请求越堆越多，
  // 画面延迟无限增长（表现为"越用越卡"）。
  if (busy) return;
  busy = true;
  canvas.toBlob(async b => {
    try {
      if (b) {
        try {
          const r = await fetch('/push', {method:'POST',
            headers:{'Content-Type':'image/jpeg'}, body:b});
          if (r.ok) {
            sent++; fail = 0;
            // 电脑回传它累计收到的帧数：手机上就能确认"真的收到了"，
            // 不用跑回电脑前面看。这是排查断流最直接的一个信号。
            try { const j = await r.json(); acked = j.n || 0; } catch (e) {}
          } else { fail++; }
        } catch (e) { fail++; }
      }
      report();
    } finally {
      busy = false;
    }
  }, 'image/jpeg', 0.75);
}

function report(){
  const vw = v.videoWidth, vh = v.videoHeight;
  let warn = '';
  if (vh > vw) warn = ' ⚠ 请把手机转成横屏！';
  else if (vw && Math.abs(vw / vh - TW / TH) > 0.05)
    warn = ' ⚠ 画面比例 ' + (vw / vh).toFixed(2) + ' 与目标 1.78 不符';
  const head = acked > 0
    ? '电脑已收到 ' + acked + ' 帧'
    : '已发出 ' + sent + ' 帧，等待电脑接收…';
  say(head + (fail ? '（失败 ' + fail + '）' : '') + warn, warn ? 'warn' : '');
}

// 打开页面就自动请求摄像头：扫码后只需「继续前往」+「允许」两步，
// 不用再低头找按钮。失败时按钮仍在，点一下就能重试。
btn.addEventListener('click', start);
window.addEventListener('load', start);
</script></body></html>
"""


def render_page():
    return (PAGE.replace("{{TW}}", str(SETTINGS.target_w))
                .replace("{{TH}}", str(SETTINGS.target_h))
                .replace("{{FPS}}", str(SETTINGS.fps)))


# ------------------------------------------------------------------ HTTP 处理

class PhoneHandler(http.server.BaseHTTPRequestHandler):
    """手机侧的 HTTPS 接口：页面 + 推帧。"""

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self._send(404, b"not found")
            return
        self._send(200, render_page().encode("utf-8"),
                   "text/html; charset=utf-8")

    def do_POST(self):
        if self.path.split("?")[0] != "/push":
            self._send(404, b"not found")
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            self._send(400, b"empty body")
            return
        body = self.rfile.read(n)
        FRAME.put(body)
        # 回传累计帧数：手机页据此显示"电脑已收到 N 帧"
        self._send(200, ('{"n":%d}' % FRAME.count).encode(),
                   "application/json")


class StreamHandler(http.server.BaseHTTPRequestHandler):
    """本机识别进程读取的普通 HTTP MJPEG（cv2.VideoCapture 直接读）。"""

    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.split("?")[0] not in ("/video", "/videofeed"):
            self.send_error(404, "use /video")
            return
        self.send_response(200)
        self.send_header("Content-Type",
                         f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        seen = 0
        while not _STOP.is_set():
            buf, count = FRAME.get_newer_than(seen, timeout=1.0)
            if buf is None or count == seen:
                continue
            seen = count
            try:
                self.wfile.write(b"--" + BOUNDARY.encode() + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(buf)}\r\n\r\n".encode())
                self.wfile.write(buf)
                self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                break


class _Server(http.server.ThreadingHTTPServer):
    """去掉了反向 DNS 查询的 HTTPServer。

    标准库的 HTTPServer.server_bind() 会调用 socket.getfqdn() 做一次
    **反向 DNS**，结果只用来填 server_name（生成 Host 头）。在 DNS 慢或者
    没网的环境里这一步会阻塞好几秒，把"点一下按钮"硬生生拖成卡顿。
    这里直接用绑定地址当 server_name —— 本服务不需要靠它认自己是谁。
    """

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port


def _serve(handler, port, host, use_ssl=False):
    """在后台线程起一个 HTTP(S) 服务，返回 server 对象（供 stop 关闭）。"""
    srv = _Server((host, port), handler)
    srv.daemon_threads = True
    if use_ssl:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_PEM, KEY_PEM)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    # poll_interval 默认 0.5s，而 shutdown() 要等循环走到下一次轮询才返回——
    # 两个监听就是 1 秒的"停止"卡顿。缩到 50ms，代价可以忽略。
    threading.Thread(target=srv.serve_forever, daemon=True, args=(0.05,),
                     name=f"phonecam-{port}").start()
    return srv


# ------------------------------------------------------------------ 服务

class PhoneCamService:
    """进程内单例服务：平台点一下「用手机当摄像头」时启停它。

    独立脚本（顶层 phone_cam.py）走的也是这个类，保证两条路的行为一致——
    两边各写一份必然会在某次改动后悄悄分叉。
    """

    def __init__(self, https_port=DEFAULT_HTTPS_PORT,
                 feed_port=DEFAULT_FEED_PORT, fps=15,
                 target=(TARGET_W, TARGET_H)):
        self.https_port = https_port
        self.feed_port = feed_port
        self.fps = fps
        self.target = target
        # 局域网地址懒取：模块导入时不该做网络动作，而且中途换 WiFi
        # 之后必须重新探测，否则二维码会指向一个已经失效的地址
        self._host_ip = None
        self._https_srv = None
        self._feed_srv = None
        self._lock = threading.Lock()

    # ---- 对外信息

    @property
    def host_ip(self):
        return self._host_ip or lan_ip()

    @property
    def running(self):
        return self._https_srv is not None

    @property
    def https_url(self):
        return f"https://{self.host_ip}:{self.https_port}/"

    @property
    def feed_url(self):
        """给识别进程用的地址。

        端口固定、绑定回环，所以这个地址跨次启动是稳定的——
        「设备管理」里登记的就是它，不稳定的话每重启一次就多一条设备。
        """
        return f"http://127.0.0.1:{self.feed_port}/video"

    def status(self):
        age = FRAME.age()
        return {
            "running": self.running,
            "https_url": self.https_url if self.running else None,
            "feed_url": self.feed_url if self.running else None,
            "frames": FRAME.count,
            "fps": round(FRAME.fps(), 1),
            "connected": age is not None and age < CONNECTED_TTL,
            "last_frame_age": round(age, 1) if age is not None else None,
            "https_port": self.https_port,
            "feed_port": self.feed_port,
        }

    # ---- 启停

    def start(self, auto_port=False):
        """启动两个监听；已在运行则直接返回（可重复调用）。

        auto_port=True 时，HTTPS 端口被占用会自动往后找一个可用的
        （跳过 MJPEG 口）。默认端口被独立版 phone_cam.py 占着是很常见的
        情况，为此让用户去查端口、杀进程，代价比自动避让大得多。

        失败抛 RuntimeError，消息里带**下一步该做什么**——
        这类报错只有用户能看见，抛个裸异常等于让人干瞪眼。
        """
        with self._lock:
            if self.running:
                return self.status()

            self._host_ip = lan_ip()

            if auto_port and port_busy(self.https_port):
                for p in range(self.https_port + 1, self.https_port + 20):
                    if p == self.feed_port or port_busy(p):
                        continue
                    self.https_port = p
                    break

            for p in (self.https_port, self.feed_port):
                if port_busy(p):
                    raise RuntimeError(
                        f"端口 {p} 已被占用，多半是已经开过一个手机摄像头服务。"
                        f"先关掉它，或换个端口（如 {self.https_port + 100}）再试。")

            if not ensure_cert([self.host_ip], quiet=True):
                raise RuntimeError(
                    "无法生成自签证书：本机既没有 openssl，也没有 cryptography 包。"
                    "装其中一个即可——安装 Git for Windows（自带 openssl），"
                    "或执行 pip install cryptography。")

            SETTINGS.fps = self.fps
            SETTINGS.target_w, SETTINGS.target_h = self.target
            FRAME.reset()
            _STOP.clear()

            try:
                # 手机要连进来，必须绑 0.0.0.0
                self._https_srv = _serve(PhoneHandler, self.https_port,
                                         "0.0.0.0", use_ssl=True)
                # 画面只给本机识别进程读，绑回环即可（不对局域网暴露画面）
                self._feed_srv = _serve(StreamHandler, self.feed_port,
                                        "127.0.0.1", use_ssl=False)
            except OSError as e:
                self.stop()
                raise RuntimeError(f"监听端口失败：{e}。换个端口再试。")

            return self.status()

    def stop(self):
        with self._lock:
            _STOP.set()
            for srv in (self._https_srv, self._feed_srv):
                if srv is None:
                    continue
                try:
                    srv.shutdown()
                    srv.server_close()
                except Exception:
                    pass
            self._https_srv = self._feed_srv = None
            FRAME.reset()
            return {"ok": True}


# 平台进程内的单例：Web 界面启停的就是它。独立脚本各自 new 一个，互不干扰。
SERVICE = PhoneCamService()


def live_device_status():
    """设备地址 -> 实时在线状态。

    手机这个源在设备表里也有一条记录，但它的 status 列是静态的：手机
    关掉页面后数据库并不知道。这里的实时值由「设备管理」接口读出来覆盖，
    避免列表上挂着一个早就不在线的「在线」。

    放在本模块而不是路由里，是为了让「设备管理」和「手机接入」两个路由
    都能用，又不必互相 import。
    """
    try:
        connected = SERVICE.status()["connected"]
    except Exception:
        connected = False
    return {SERVICE.feed_url: "online" if connected else "offline"}


# ------------------------------------------------------------------ 独立脚本

def _print_hint(svc, out=print):
    out("=" * 68)
    out("手机摄像头已就绪（手机端**不需要装任何 App**）")
    out("=" * 68)
    out(f"  运行解释器：{sys.executable}")
    out()
    out("① 手机浏览器打开下面这个地址（手机需与电脑同一个 WiFi）：")
    out()
    out(f"      {svc.https_url}")
    out()
    out("   会提示「连接不私密 / 证书无效」——自签证书的正常现象，")
    out("   点「高级」→「继续前往」即可。画面只在你自己的局域网内传输。")
    out()
    out("   页面打开后会自动请求摄像头权限，点「允许」就开始推流。")
    out(f"   画面会被固定为 {svc.target[0]}x{svc.target[1]}（16:9），请把手机**横过来**。")
    out()
    out("② 电脑上用这个地址（就是普通视频源）：")
    out()
    out(f"      python check_source.py --source {svc.feed_url}")
    out(f"      python monitor.py --source {svc.feed_url}")
    out()
    out("按 Ctrl+C 结束。")
    out("=" * 68)


def print_qr(url, out=print):
    """在终端里画一个二维码，省掉在手机上手输地址。"""
    try:
        from backend import qr
    except Exception:
        qr = None
    if qr is None:
        return
    art = qr.qr_ansi(url) or qr.qr_ascii(url)
    if art:
        out()
        out("   手机扫这个码可以直接打开（终端背景色会影响识别，扫不动就用上面的地址）：")
        out()
        for line in art.split("\n"):
            out("   " + line)
        out()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="手机当摄像头（手机只需浏览器，无需装 App）")
    ap.add_argument("--port", type=int, default=DEFAULT_HTTPS_PORT,
                    help=f"手机访问的 HTTPS 端口（默认 {DEFAULT_HTTPS_PORT}）")
    ap.add_argument("--fps", type=int, default=15,
                    help="手机推帧帧率上限（默认 15；WiFi 好可调到 20，"
                         "低于 12 会让「持续时长」类判定失准）")
    ap.add_argument("--target", default="1280x720",
                    help="输出画面尺寸，应当是 16:9（默认 1280x720）")
    ap.add_argument("--no-qr", action="store_true", help="终端不打印二维码")
    args = ap.parse_args(argv)

    try:
        tw, th = (int(x) for x in args.target.lower().split("x"))
    except Exception:
        print(f"--target 格式错误：{args.target}（应为 1280x720）")
        return 2

    if abs(tw / th - 16 / 9) > 0.02:
        print(f"警告：{tw}x{th} 不是 16:9。二维特征对宽高比敏感，"
              f"比例与录模板时不一致会系统性偏移角度。")

    svc = PhoneCamService(https_port=args.port, fps=args.fps, target=(tw, th))
    try:
        svc.start()
    except RuntimeError as e:
        print(f"!! {e}")
        return 3

    _print_hint(svc)
    if not args.no_qr:
        print_qr(svc.https_url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        svc.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
