# 智慧养老 · 辅助监管与培训系统
# Web 管理后台 + 监控识别（RTSP 无头模式）统一镜像
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

# opencv / mediapipe 运行所需系统库
#   libgl1        -> libGL.so.1 (opencv)
#   libglib2.0-0  -> libglib-2.0.so.0 (opencv)
#   libgomp1      -> OpenMP 运行时 (numpy/mediapipe)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（利用 Docker 层缓存，源码变动不触发重装）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码与前端
COPY . .

RUN mkdir -p data models

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
