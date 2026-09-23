"""手机当摄像头——**不用装任何 App**，手机浏览器打开一个网址即可。

为什么需要它：IP Webcam 这类应用只在 Google Play 上架，国内应用商店搜不到；
而"手机变虚拟摄像头"的软件（iVCam / DroidCam / EV虚拟摄像头）都要在电脑端
再装一个客户端。这条路线把这两层都省掉——手机只用自带浏览器。

原理：
    手机浏览器（HTTPS）--- 浏览器摄像头 API 抓帧 ---> POST /push  (PC)
    PC 把最新帧原样转成 MJPEG ---> http://127.0.0.1:<port+1>/video
    然后就是普通视频源：monitor.py --source http://127.0.0.1:8444/video

为什么必须是 HTTPS：浏览器只在"安全上下文"下允许网页访问摄像头，
`http://192.168.x.x` 会被直接拒绝。所以这里自签一张证书，
手机上会提示"连接不私密"，点继续访问即可（数据不出局域网）。

自签证书**不依赖任何 Python 第三方包**：优先用 openssl 命令行
（Git for Windows 自带），没有才退回 cryptography。这样任何 Python 3.8+
都能跑，不必纠结用哪个解释器。

用法：
    python phone_cam.py
然后按屏幕提示，用手机浏览器打开打印出来的地址。
"""
import argparse
import datetime
import http.server
import ipaddress
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time

CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".workbuddy")
CERT_PEM = os.path.join(CERT_DIR, "phone_cam_cert.pem")
KEY_PEM = os.path.join(CERT_DIR, "phone_cam_key.pem")

BOUNDARY = "phonecam"

# 输出画面的目标尺寸。固定 16:9 是刻意的：2d 特征对宽高比敏感
# （mediapipe 用 x/W、y/H 两个不同分母），固定住才不会让模板莫名其妙失配。
TARGET_W, TARGET_H = 1280, 720


# ------------------------------------------------------------------ 工具

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


def _gen_with_openssl(ips):
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
        print(f"  openssl 调用失败：{e}")
        return False
    if r.returncode == 0 and os.path.exists(CERT_PEM) and os.path.exists(KEY_PEM):
        print("  已用 openssl 生成（无需任何 Python 第三方包）")
        return True
    detail = (r.stderr or "").strip().splitlines()
    print("  openssl 生成失败：" + (detail[-1] if detail
                                   else "退出码 %d（版本可能不支持 -addext）"
                                        % r.returncode))
    return False


def _gen_with_cryptography(ips):
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
    print("  已用 cryptography 生成")
    return True


def ensure_cert(ips):
    """自签证书（带 SAN）。已存在就直接复用。

    顺序刻意如此：openssl 命令行**零依赖**，任何 Python 都能跑；
    cryptography 只是没装 openssl 时的退路。
    """
    if os.path.exists(CERT_PEM) and os.path.exists(KEY_PEM):
        return True
    print("正在生成自签证书（首次运行一次，之后复用）...")
    if _gen_with_openssl(ips) or _gen_with_cryptography(ips):
        print(f"  证书已写入 {CERT_DIR}")
        return True

    print()
    print("!! 无法生成证书：本机既没有 openssl，也没有 Python 的 cryptography 包。")
    print("   任选其一即可：")
    print("     a) 安装 Git for Windows（自带 openssl），然后重跑本脚本；")
    print("     b) 执行  pip install cryptography   再重跑本脚本。")
    return False


def port_busy(port):
    """端口是否已被占用（用于给出比 'Address already in use' 更清楚的提示）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()



# ------------------------------------------------------------------ 状态

class Latest:
    """保存手机推上来的最新一帧，供 MJPEG 端消费。"""

    def __init__(self):
        self.lock = threading.Condition()
        self.jpeg = None
        self.count = 0
        self.last_at = 0.0
        self.size = None

    def put(self, buf):
        with self.lock:
            self.jpeg = buf
            self.count += 1
            self.last_at = time.time()
            self.lock.notify_all()

    def get_newer_than(self, seen, timeout=2.0):
        with self.lock:
            if self.count == seen:
                self.lock.wait(timeout)
            return self.jpeg, self.count


FRAME = Latest()


# ------------------------------------------------------------------ 手机页面

PAGE = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>手机摄像头</title>
<style>
  html,body{margin:0;height:100%%;background:#111;color:#eee;
            font:16px/1.5 system-ui,-apple-system,"Microsoft YaHei",sans-serif}
  #wrap{display:flex;flex-direction:column;height:100%%}
  video{width:100%%;flex:1;object-fit:contain;background:#000}
  #bar{padding:10px 14px;background:#1c1c1c}
  #btn{width:100%%;padding:14px;font-size:18px;border:0;border-radius:8px;
       background:#2f7d32;color:#fff}
  #msg{margin-top:8px;font-size:15px;color:#9ccc65;word-break:break-all}
  .warn{color:#ffb300 !important}
  .err{color:#ef5350 !important}
</style></head>
<body><div id="wrap">
  <video id="v" autoplay playsinline muted></video>
  <div id="bar">
    <button id="btn">开始推送到电脑</button>
    <div id="msg">点上面的按钮，允许摄像头权限</div>
  </div>
</div>
<script>
const TARGET_W = %(tw)d, TARGET_H = %(th)d, FPS = %(fps)d;
const v = document.getElementById('v'), msg = document.getElementById('msg');
let timer = null, sent = 0, fail = 0, busy = false;

function say(t, cls){ msg.textContent = t; msg.className = cls || ''; }

async function start(){
  try {
    // facingMode: environment = 后置摄像头。前置普遍带美颜/瘦脸，
    // 会直接扭曲骨架关键点，属于算法层无法修正的误差。
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: {ideal:'environment'},
               width: {ideal: TARGET_W}, height: {ideal: TARGET_H},
               frameRate: {ideal: FPS} }
    });
    v.srcObject = stream;
    await v.play();
  } catch (e) {
    say('打不开摄像头：' + e.name + ' —— ' + e.message, 'err');
    return;
  }

  const c = document.createElement('canvas');
  c.width = TARGET_W; c.height = TARGET_H;
  const ctx = c.getContext('2d');

  const push = () => {
    // 上一帧还没发完就跳过——否则网络一慢，请求会越堆越多，
    // 画面延迟无限增长（表现为"越用越卡"）。
    if (busy) return;
    busy = true;
    c.toBlob(async b => {
      try {
        if (!b) return;
        try {
          const r = await fetch('/push', {method:'POST',
            headers:{'Content-Type':'image/jpeg'}, body:b});
          if (r.ok) { sent++; fail = 0; } else { fail++; }
        } catch (e) { fail++; }
        const vw = v.videoWidth, vh = v.videoHeight;
        const vAspect = vw / vh, tAspect = TARGET_W / TARGET_H;
        let warn = '';
        if (vh > vw) warn = ' ⚠ 请把手机转成横屏！';
        else if (Math.abs(vAspect - tAspect) > 0.05)
          warn = ' ⚠ 画面比例 ' + vAspect.toFixed(2) + ' 与目标 1.78 不符';
        say('推流中 已发 ' + sent + ' 帧' + (fail ? '（失败 ' + fail + '）' : '') + warn,
            warn ? 'warn' : '');
      } finally {
        busy = false;
      }
    }, 'image/jpeg', 0.75);
  };

  // 先按相机自身比例原样绘制，再由 canvas 输出固定 16:9。
  // 用 contain（等比缩放 + 留黑边）而不是拉伸——拉伸会扭曲骨架，
  // 那是算法层修不回来的误差。
  function draw(){
    const vw = v.videoWidth, vh = v.videoHeight;
    if (!vw || !vh) { push(); return; }
    const s = Math.min(TARGET_W / vw, TARGET_H / vh);
    const dw = vw * s, dh = vh * s;
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, TARGET_W, TARGET_H);
    ctx.drawImage(v, (TARGET_W - dw) / 2, (TARGET_H - dh) / 2, dw, dh);
    push();
  }

  if (timer) clearInterval(timer);
  timer = setInterval(draw, 1000 / FPS);
  say('已开始，等待第一帧…');
}

document.getElementById('btn').addEventListener('click', start);
</script></body></html>
"""


class PhoneHandler(http.server.BaseHTTPRequestHandler):
    """手机侧的 HTTPS 接口：页面 + 推帧。"""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self.send_error(404)
            return
        html = (PAGE % {"tw": TARGET_W, "th": TARGET_H, "fps": ARGS.fps}
                ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html)

    def do_POST(self):
        if self.path.split("?")[0] != "/push":
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            self.send_error(400)
            return
        body = self.rfile.read(n)
        FRAME.put(body)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class StreamHandler(http.server.BaseHTTPRequestHandler):
    """PC 侧的普通 HTTP MJPEG：cv2.VideoCapture 直接读它。"""

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
        idle_report = 0
        while True:
            buf, count = FRAME.get_newer_than(seen)
            if buf is None or count == seen:
                idle_report += 1
                if idle_report == 1:
                    print("  [提示] MJPEG 客户端已连上，但手机还没推帧")
                if FRAME.count == 0 and time.time() - STARTED > 30:
                    break
                continue
            idle_report = 0
            seen = count
            try:
                self.wfile.write(b"--" + BOUNDARY.encode() + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(buf)}\r\n\r\n".encode())
                self.wfile.write(buf)
                self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                break


STARTED = time.time()
ARGS = None


def serve(handler, port, use_ssl=False):
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    if use_ssl:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_PEM, KEY_PEM)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    global ARGS, TARGET_W, TARGET_H
    ap = argparse.ArgumentParser(
        description="手机当摄像头（手机只需浏览器，无需装 App）")
    ap.add_argument("--port", type=int, default=8443,
                    help="手机访问的 HTTPS 端口（默认 8443）")
    ap.add_argument("--fps", type=int, default=15,
                    help="手机推帧帧率上限（默认 15；WiFi 好可调到 20，"
                         "低于 12 会让「持续时长」类判定失准）")
    ap.add_argument("--target", default="1280x720",
                    help="输出画面尺寸，必须是 16:9（默认 1280x720）")
    ARGS = ap.parse_args()

    try:
        TARGET_W, TARGET_H = (int(x) for x in ARGS.target.lower().split("x"))
    except Exception:
        print(f"--target 格式错误：{ARGS.target}（应为 1280x720）")
        sys.exit(2)

    http_port = ARGS.port + 1
    ip = lan_ip()

    # 端口先查，否则报 "Address already in use" 很难看出是重复启动
    for p in (ARGS.port, http_port):
        if port_busy(p):
            print(f"!! 端口 {p} 已被占用。多半是已经启动过一个 phone_cam.py。")
            print("   先关掉之前的那个（或换个端口）：")
            print(f"     python phone_cam.py --port {ARGS.port + 100}")
            sys.exit(3)

    if not ensure_cert([ip]):
        sys.exit(1)

    try:
        serve(PhoneHandler, ARGS.port, use_ssl=True)
        serve(StreamHandler, http_port, use_ssl=False)
    except OSError as e:
        print(f"!! 启动失败：{e}")
        print("   如提示端口被占用，换一个端口重试：python phone_cam.py --port 9443")
        sys.exit(3)

    print("=" * 68)
    print("手机摄像头已就绪（手机端**不需要装任何 App**）")
    print("=" * 68)
    print(f"  运行解释器：{sys.executable}")
    print()
    print("① 手机浏览器打开（与电脑同一个 WiFi）：")
    print()
    print(f"      https://{ip}:{ARGS.port}/")
    print()
    print("   会提示「连接不私密 / 证书无效」——这是自签证书导致的，正常现象。")
    print("   点「高级」→「继续前往」即可。画面只在你自己的局域网内传输。")
    print("   打开后点绿色按钮，并允许摄像头权限。")
    print(f"   画面会被固定为 {TARGET_W}x{TARGET_H}（16:9），请把手机**横过来**。")
    print()
    print("② 电脑上用这个地址（就是普通视频源）：")
    print()
    print(f"      python check_source.py --source http://127.0.0.1:{http_port}/video")
    print(f"      python monitor.py --source http://127.0.0.1:{http_port}/video")
    print()
    print("   先跑体检确认帧率与姿态检出率，再接进「监控设备」。")
    print()
    print("按 Ctrl+C 结束。")
    print("=" * 68)
    sys.stdout.flush()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
