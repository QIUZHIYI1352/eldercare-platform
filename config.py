"""全局配置"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# MySQL 连接配置（可用环境变量覆盖，密码等敏感信息不要写死在代码里）
MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DB = os.environ.get("MYSQL_DB", "eldercare")

# 隐私安全默认策略
PRIVACY_DEFAULTS = {
    "face_blur": False,          # 人脸不打码（默认关闭）
    "store_raw_video": False,     # 默认不落盘原始视频
    "store_skeleton_only": True,  # 仅保存骨骼关键点（脱敏）
    "local_process_only": True,   # 数据本地处理，不出外网
    "mask_personal_info": True,   # 界面个人敏感信息脱敏
    "audit_log": True,            # 开启审计日志
}

# 角色定义
ROLES = {
    "admin": "管理员",
    "nursing_student": "护理大学生",
    "long_term_caregiver": "长期护理员",
    "elderly_caregiver": "养老护理员",
    "elderly_service_teacher": "养老服务师",
}

# 动作模板的特征空间（环境变量 FEATURE_SPACE 覆盖）
#   "2d" = 图像空间特征（旧版）：换机位后结构性失配，模板只能机位固定时用
#   "3d" = 三维机位无关特征（新版）：多机位可共用同一套模板
# 切换到 3d 后，需要重新录制序列动作模板；版本不符的旧模板会被自动跳过
# （跳过而不是拿旧模板硬匹配，避免产生误报）。
FEATURE_SPACE = os.environ.get("FEATURE_SPACE", "2d").strip().lower()

# ---- 实时监控画面（MJPEG/单帧轮询）的推流参数 -------------------------------
# 画面只用于「让人看见」，不参与识别，所以没必要按源帧率逐帧编码。
# JPEG 编码在 720p 下约 9ms/帧，是整条识别链路里第二大的开销（占 25%），
# 而前端轮询间隔约 120ms（≈8fps）——按源帧率编码等于白烧 3~4 倍 CPU。
#
# PREVIEW_FPS        预览画面最高帧率（编码上限，与源帧率无关）
# PREVIEW_IDLE_SEC   超过这么久没有任何人取过画面，就停止编码（省下全部开销）
PREVIEW_FPS = float(os.environ.get("PREVIEW_FPS", "12"))
PREVIEW_IDLE_SEC = float(os.environ.get("PREVIEW_IDLE_SEC", "3.0"))
