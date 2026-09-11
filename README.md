# 智慧实训规范平台

面向**护理大学生、长期护理员、养老护理员、养老服务师**的 Web 管理后台，核心能力是「**辅助监管 + 培训**」。

## 项目定位

> 睡眠监测只是智慧养老的一个基础场景。本项目的切入重点是：长期护理员、养老护理员、养老服务训练这三大群体的**护理操作能力不完善**问题。

系统以 Web 管理后台呈现，同时具备**监控识别能力**：
- 可**自主添加识别的动作内容**（关节角度规则，可扩展）
- 可**自主添加监控设备**（USB / RTSP / HTTP 视频流）
- 使用 **`cv2` + `mediapipe`** 做人姿态估计与动作识别

## 两大核心支柱

1. **操作防漏提醒（节省人力）**
   实时识别护理操作动作，与标准护理流程逐步骤比对，画面即时提示
   `✓已完成 / ▶当前 / ⚠漏步`，帮助学员/护理员发现「哪一步漏掉了」。

2. **隐私安全能力优化（监管能力优化）**
   - 数据本地处理，不出外网
   - 仅保存骨骼关键点（脱敏），默认不落盘原始视频
   - 界面敏感信息（姓名/手机号）脱敏显示
   - 角色分级权限 + 全量操作审计日志
   - （人脸打码默认关闭，专注动作判定，可在「隐私安全」页开启）

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python · FastAPI · SQLite |
| 前端 | 原生 HTML/CSS/JS（SPA，无需 Node） |
| 视觉识别 | OpenCV (`cv2`) · MediaPipe Pose · NumPy |

## 目录结构

```
eldercare-platform/
├── run.py                  # 启动 Web 服务 (python run.py)
├── monitor.py              # 实时监控识别脚本 (cv2 + mediapipe, 支持 RTSP)
├── record_template.py      # 骨骼序列动作模板录制脚本 (DTW)
├── config.py               # 全局配置 / 隐私默认策略 / 角色定义
├── backend/
│   ├── main.py             # FastAPI 应用
│   ├── database.py         # SQLite 数据访问层
│   ├── auth.py             # 口令哈希 / 令牌 / 权限 / 审计 / 脱敏
│   ├── seed.py             # 种子数据（账号、流程、动作、设备）
│   ├── routers/            # API 路由（auth/users/processes/actions/devices/monitoring/training/privacy）
│   └── vision/
│       ├── pose_engine.py        # mediapipe Tasks API 姿态检测 + 关节特征计算
│       ├── action_recognizer.py  # 基于规则的动作识别（含持续时长抗抖）
│       ├── template_matcher.py   # 骨骼序列模板匹配（DTW 动态时间规整）
│       ├── sequence_matcher.py   # LCS 步骤序列匹配 / 漏步检测
│       └── video_source.py       # 视频源封装（摄像头/RTSP/HTTP/文件，断流重连）
└── frontend/               # Web 管理后台前端
    ├── index.html
    ├── css/style.css
    └── js/app.js
```

## Docker 一键部署（推荐）

无需手动安装 Python 依赖，一条命令拉起 Web 管理后台：

```bash
docker compose up -d
# 访问 http://localhost:8000  （默认账号 admin / admin123）
```

数据持久化在宿主机 `./data`（SQLite 数据库）与 `./models`（姿态模型，首次启动自动下载）。

### 同时启动实时监控识别（RTSP 无头模式）

编辑 `docker-compose.yml` 中 monitor 服务的环境变量，然后：

```bash
docker compose --profile monitor up -d
```

| 环境变量 | 说明 | 示例 |
|---|---|---|
| `MONITOR_SOURCE` | 视频源 | `rtsp://user:pwd@ip:554/stream1`、`0`(本机摄像头) |
| `MONITOR_PROCESS` | 护理流程名 | `协助卧床老人翻身` |
| `MONITOR_SAVE` | 自动保存记录 | `true` / `false` |

> 监控容器以 `--headless` 运行（不弹窗），识别结果与漏步提醒写入与 Web 共享的数据库，
> 在后台「实时监管 / 监控记录」页查看。查看实时识别日志：`docker logs -f eldercare-monitor`。

### 常用命令

```bash
docker compose ps                 # 查看服务状态
docker compose logs -f web        # 查看 Web 日志
docker compose down               # 停止（保留数据）
docker compose down -v            # 停止并清除数据卷（慎用）
```

## 快速开始（本地开发）

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> MediaPipe 建议 Python 3.8 ~ 3.11。若安装缓慢，可先装核心依赖再单独装 mediapipe。

### 2. 启动 Web 管理后台

```bash
python run.py
```

浏览器访问 <http://localhost:8000>

**默认账号**

| 账号 | 密码 | 角色 |
|---|---|---|
| admin | admin123 | 管理员 |
| nurse001 | 123456 | 护理大学生 |
| caregiver001 | 123456 | 长期护理员 |
| elderly001 | 123456 | 养老护理员 |
| teacher001 | 123456 | 养老服务师 |

### 3. 启动实时监控识别

```bash
python monitor.py                                    # 本机摄像头，交互选择流程
python monitor.py --source 0                         # 指定摄像头
python monitor.py --source rtsp://user:pwd@ip:554/stream1   # RTSP 网络摄像头
python monitor.py --source http://ip:8080/video      # HTTP 视频流
python monitor.py --source demo.mp4                  # 本地视频文件
python monitor.py --device dev_xxx                   # 读取后台设备表中的接入地址
python monitor.py --process "协助卧床老人翻身"
python monitor.py --save                             # 结束后保存监控记录
```

画面左上角实时显示步骤完成状态与漏步提醒；按 `q` 退出、`s` 保存并结束。

### 4. 录制骨骼序列动作模板（精细动作匹配）

规则型动作适合静态姿态，而「翻身、拍背、环抱转移」等过程性动作更适合用骨骼序列模板：

```bash
python record_template.py --name "协助翻身动作" --duration 5      # 本机摄像头录制 5 秒
python record_template.py --name "拍背排痰" --source rtsp://... --duration 6
python record_template.py --name "环抱转移" --action-id act_xxx    # 覆盖已有动作
```

倒计时结束后在镜头前完整演示该动作，脚本自动采集骨骼序列并保存为「序列型」动作模板，
监控时通过 DTW 动态时间规整实时比对。

## 功能清单

- **仪表盘**：核心能力说明、统计、最近培训、快速开始
- **护理流程**：编排标准操作步骤（每步关联一个可识别动作）
- **动作模板**：自主添加识别动作（关节特征 + 阈值 + 持续时间）
- **监控设备**：自主添加 USB / RTSP / HTTP 监控设备
- **实时监管**：监控识别说明 + 监控记录
- **培训记录**：学员训练得分、完成/漏步统计
- **用户管理**（管理员）：角色分级账号管理
- **隐私安全**（管理员）：隐私策略开关 + 操作审计日志

## 动作识别原理

MediaPipe Pose 输出 33 个关键点，系统据此计算可解释特征：

| 特征 key | 含义 |
|---|---|
| `trunk_inclination` | 躯干前倾角（度） |
| `left/right_elbow_angle` | 肘关节角度 |
| `left/right_knee_angle` | 膝关节角度 |
| `left/right_hip_angle` | 髋关节角度 |
| `hand_height_left/right` | 手相对肩的高度 |
| `hands_distance` | 双手距离（肩宽归一） |
| `body_height` / `hip_center_height` | 身体/髋部高度 |

动作 = 一组「特征 + 运算符 + 阈值」条件全部满足，并**持续达到指定时长**（抗抖动）。
识别出的动作序列再通过 LCS 与流程步骤序列比对，得出完成/漏步/得分。

## 默认动作模板

站立准备 · 弯腰操作 · 深弯腰低位操作 · 屈膝下蹲 · 双臂抬起 · 双手靠近

> 以上为「规则型」模板。系统同时支持「序列型」骨骼模板（DTW），
> 通过 `record_template.py` 录制过程性动作。

## 默认护理流程

协助卧床老人翻身 · 协助老人进食 · 口腔护理 · 床-轮椅转移

## 隐私安全设计要点

1. **数据不出本地**：`local_process_only` 默认开启
2. **骨骼脱敏存储**：`store_skeleton_only` 默认开启，`store_raw_video` 默认关闭
3. **界面脱敏**：学员姓名、手机号默认打码展示
4. **分级权限**：仅管理员可管理用户、隐私策略、审计日志
5. **审计留痕**：登录、增删改、培训完成等操作全量记录
