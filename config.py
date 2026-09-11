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
