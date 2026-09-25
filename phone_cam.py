"""手机当摄像头（独立版）——手机不用装任何 App，浏览器打开一个网址即可。

这条路线适合**不想开平台**、只想快速验证摄像头通不通的场合。
平台内嵌的那条路（「实时监控」页 → 用手机当摄像头 → 扫码）走的是
**同一份实现**，见 `backend/vision/phone_cam.py`。

共用在这是有意的：拆成两份必然会在某次改动后悄悄分叉，最后表现成
「平台上扫码能连、命令行扫码连不上」这种极难排查的问题。

用法：
    python phone_cam.py
    然后按屏幕提示扫码（或手输地址），电脑侧接：
      python check_source.py --source http://127.0.0.1:8444/video
      python monitor.py      --source http://127.0.0.1:8444/video

注意：本脚本自身不依赖任何第三方包（证书走 openssl 命令行），
但需要从**项目根目录**运行，以便 import 到 backend 包。
"""
import sys

from backend.vision.phone_cam import main

if __name__ == "__main__":
    sys.exit(main())
