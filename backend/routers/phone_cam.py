"""手机当摄像头的接入接口。

平台「实时监控」页点一下「用手机当摄像头」，背后发生三件事：

  1. 在平台进程内起一个 HTTPS 小服务——浏览器只在「安全上下文」里放行
     摄像头，而手机访问 http://192.168.x.x 不算安全上下文，所以必须 HTTPS；
     同时另起一个只绑本机回环的 MJPEG 转发口给识别进程读
  2. 生成二维码，手机扫一下就能打开那个页面，不用在手机小键盘上手输地址
  3. 把手机这个源登记成一条普通设备记录，地址形如
     http://127.0.0.1:8444/video——对识别链路来说它就是一路普通视频源，
     没有任何特殊对待

于是使用者要做的只有两件事：扫码、点开始。
"""
from fastapi import APIRouter, Depends, HTTPException

from backend import auth
from backend import database as db
from backend import qr
from backend.vision import phone_cam as phone_cam_svc
from .auth_router import current_user
from .tasks import TEACHER_ROLES

router = APIRouter(prefix="/api/phone-cam", tags=["phone-cam"])

# 设备名刻意不用地址：127.0.0.1:8444 是纯实现细节，摆进设备列表只会让
# 使用者以为要自己去填。这一栏应该出现一台"设备"，而不是一条 URL。
DEVICE_NAME = "手机摄像头"
DEVICE_TYPE = "网络摄像头"
DEVICE_LOCATION = "局域网直连"


def _require_manager(user):
    """开监听端口、写设备表都属于管理动作；「实时监控」页本身也限教师/管理员。"""
    if user["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="仅教师或管理员可接入手机摄像头")


def _ensure_device(feed_url):
    """按接入地址复用设备记录。

    地址是稳定的（固定端口 + 回环），所以重启服务不会每次都多出一条设备，
    否则设备列表很快就会被"手机摄像头"刷屏。
    """
    row = db.query_one("SELECT * FROM devices WHERE url = ?", (feed_url,))
    if row:
        return row["id"]
    did = db.gen_id("dev")
    # 登记成 offline 而不是 online：这一刻服务刚起，手机还没连上来。
    # 写 online 等于开局就说谎，而真实状态由 live_device_status() 实时覆盖。
    db.execute(
        "INSERT INTO devices (id, name, type, url, location, status, privacy_mask,"
        " created_at) VALUES (?,?,?,?,?,?,?,?)",
        (did, DEVICE_NAME, DEVICE_TYPE, feed_url, DEVICE_LOCATION,
         "offline", 1, db.now()),
    )
    return did


@router.post("/start")
def start(user=Depends(current_user)):
    """启动手机接入服务，返回二维码与视频源地址（可重复调用）。"""
    _require_manager(user)
    try:
        info = phone_cam_svc.SERVICE.start(auto_port=True)
    except RuntimeError as e:
        # 409：请求本身没错，是环境状态不满足（端口被占 / 没有 openssl）
        raise HTTPException(status_code=409, detail=str(e))

    device_id = _ensure_device(info["feed_url"])
    auth.audit(user, "start_phone_cam", f"接入手机摄像头 {info['https_url']}")
    return {
        **info,
        "device_id": device_id,
        "device_name": DEVICE_NAME,
        # cv2 不可用时生成不了二维码，这里为 None，
        # 前端降级成"把地址显示出来让用户自己输"，功能不缺失只是麻烦一点
        "qr_svg": qr.qr_svg(info["https_url"]),
    }


@router.get("/status")
def get_status(user=Depends(current_user)):
    """实时状态：手机有没有连上、实测帧率、累计收到多少帧。"""
    return phone_cam_svc.SERVICE.status()


@router.post("/stop")
def stop(user=Depends(current_user)):
    _require_manager(user)
    phone_cam_svc.SERVICE.stop()
    auth.audit(user, "stop_phone_cam", "关闭手机摄像头")
    return {"ok": True}
