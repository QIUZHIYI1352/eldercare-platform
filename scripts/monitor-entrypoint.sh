#!/bin/sh
# 监控识别容器入口：根据环境变量拼接 monitor.py 启动参数
set -e

SOURCE="${MONITOR_SOURCE:-0}"
CMD="python monitor.py --source \"$SOURCE\" --headless"

if [ "${MONITOR_SAVE:-false}" = "true" ]; then
  CMD="$CMD --save"
fi

if [ -n "$MONITOR_PROCESS" ]; then
  CMD="$CMD --process \"$MONITOR_PROCESS\""
fi

echo "[monitor-entrypoint] 启动实时监控: $CMD"
exec /bin/sh -c "$CMD"
