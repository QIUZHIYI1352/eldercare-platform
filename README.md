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
| 后端 | Python 3.9~3.12 · FastAPI · MySQL 8（PyMySQL，无 ORM） |
| 前端 | 原生 HTML/CSS/JS（SPA，无需 Node） |
| 视觉识别 | OpenCV (`cv2`) · MediaPipe Pose · NumPy（软依赖，未安装时后台仍可运行） |

> 视觉识别是**软依赖**：只跑 Web 管理后台时可以不装 `opencv` / `mediapipe`，
> 首页右上角会显示「视觉引擎不可用」，其余功能不受影响。

## 目录结构

```
eldercare-platform/
├── run.py                  # 启动 Web 服务 (python run.py)
├── monitor.py              # 实时监控识别脚本 (cv2 + mediapipe, 支持 RTSP)
├── record_template.py      # 骨骼序列动作模板录制脚本 (DTW, --space 2d|3d)
├── calibrate_template.py   # 在真实机位上标定「序列模板」的 DTW 阈值（切换 3d 后必做）
├── calibrate_rule.py       # 在真实机位上标定「规则模板」的关节阈值（角度/时长类动作）
├── config.py               # 全局配置 / 隐私默认策略 / 角色定义
├── backend/
│   ├── main.py             # FastAPI 应用（lifespan 启动建表+种子数据）
│   ├── database.py         # MySQL 数据访问层（? 占位符自动转 %s）
│   ├── auth.py             # 口令哈希(PBKDF2) / 令牌 / 会话有效期 / 权限 / 审计 / 脱敏
│   ├── ratelimit.py        # 登录 / 注册的滑动窗口限流
│   ├── seed.py             # 种子数据（账号、流程、动作、设备）
│   ├── routers/            # API 路由
│   │   ├── auth_router.py  #   登录 / 注册 / 登出
│   │   ├── users.py        #   用户管理（管理员）  privacy.py  隐私策略 + 审计日志
│   │   ├── processes.py    #   护理流程（培训标准）  actions.py  动作模板
│   │   ├── devices.py      #   监控设备            monitoring.py 实时监控会话
│   │   ├── assessment.py   #   动作对比 + 报告      training.py   培训记录
│   │   ├── classes.py      #   班级管理            tasks.py      任务发布
│   │   ├── dashboard.py    #   教师看板 + 学员档案  messages.py   站内消息
│   └── vision/
│       ├── pose_engine.py        # mediapipe Tasks API 姿态检测 + 关节特征计算
│       ├── action_recognizer.py  # 基于规则的动作识别（含持续时长抗抖）
│       ├── template_matcher.py   # 骨骼序列模板匹配（DTW 动态时间规整）
│       ├── sequence_matcher.py   # LCS 步骤序列匹配 / 漏步检测
│       ├── assessment.py         # 步骤分类 + 每步 DTW 质量分
│       ├── monitor_session.py    # 后台识别线程 + 状态/画面输出
│       └── video_source.py       # 视频源封装（摄像头/RTSP/HTTP/文件，断流重连）
├── tests/                  # 测试（纯逻辑 + SQLite 垫片下的接口端到端）
└── frontend/               # Web 管理后台前端
    ├── index.html
    ├── css/style.css
    └── js/app.js
```

## Docker 一键部署（推荐）

无需手动安装 Python / MySQL，一条命令拉起 Web 管理后台（含 MySQL 8 容器）：

```bash
# 1. 设置数据库 root 密码（务必改成自己的强密码）
echo 'MYSQL_ROOT_PASSWORD=你的强密码' > .env

# 2. 启动
docker compose up -d --build
# 访问 http://localhost:8000  （默认账号 admin / admin123）
```

数据持久化在 Docker 卷 `mysql_data`（MySQL 数据库）与宿主机 `./models`（姿态模型，首次启动自动下载）。
`docker compose down` 不会丢数据，只有 `down -v` 会删除数据卷。

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

> - 建议 **Python 3.9 ~ 3.12**（MediaPipe 目前未覆盖 3.13）。
> - 若安装缓慢，可先装核心依赖（fastapi / uvicorn / pymysql）再单独装 mediapipe。
> - 只想跑 Web 管理后台、不用摄像头时，可以**跳过** `opencv-contrib-python` 与
>   `mediapipe`；后台仍可正常启动，只是「实时监控 / 动作对比」不可用。

### 2. 准备 MySQL 8

本机安装 MySQL 8 并保证 3306 可连。数据库与表会在首次启动时**自动创建**，
无需手动建库（库名默认 `eldercare`）。

### 3. 启动 Web 管理后台

```bash
# Windows PowerShell
$env:MYSQL_PASSWORD = "你的MySQL密码"; python run.py

# Linux / macOS
MYSQL_PASSWORD=你的MySQL密码 python run.py
```

浏览器访问 <http://localhost:8000>

**可用的环境变量**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MYSQL_HOST` / `MYSQL_PORT` | `localhost` / `3306` | 数据库地址 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | `root` / 空 | 数据库账号（**密码请用环境变量，不要写进代码**） |
| `MYSQL_DB` | `eldercare` | 库名，不存在会自动创建 |
| `CORS_ORIGINS` | 本机 8000/3000 | 允许的跨域来源，逗号分隔；`*` 表示不限制 |
| `SESSION_TTL_SECONDS` | `604800`（7 天） | 登录会话有效期；`0` 表示永不过期 |
| `PASSWORD_PBKDF2_ITERATIONS` | `200000` | 口令哈希迭代次数（调高更安全、略慢） |
| `FEATURE_SPACE` | `2d` | 序列模板特征空间：`2d`=机位必须固定；`3d`=多机位共用一套模板（需重录模板 + 标定阈值） |

**默认账号**

| 账号 | 密码 | 角色 |
|---|---|---|
| admin | admin123 | 管理员 |
| nurse001 | 123456 | 护理大学生 |
| caregiver001 | 123456 | 长期护理员 |
| elderly001 | 123456 | 养老护理员 |
| teacher001 | 123456 | 养老服务师 |

### 4. 启动实时监控识别

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

> **本地视频文件播完会自动收尾**（不会空转等待），RTSP/HTTP 断流则按断流处理并限频重连
> ——两者语义不同，不会互相误判。
>
> 用视频文件回放做验证时，录制/标定窗口按**视频时间轴**计时，而不是挂钟：
> 同一段素材在任何机器上结果都一样，且与引擎按 fps 生成的时间戳一致。
> （实时摄像头/RTSP 仍按真实时间计时，因为它们本身就是实时的。）

**接入任何新视频源之前，先跑一遍体检：**

```bash
python check_source.py --source 0                              # 本机摄像头
python check_source.py --source http://192.168.1.23:8080/video # 手机串流
python check_source.py --source rtsp://user:pwd@ip:554/stream1 # RTSP
```

它报告分辨率/宽高比、实测帧率、帧间隔抖动、姿态检出率、平均可见度，
并与库中已有序列模板比对宽高比，最后给出结论。

> **没有摄像头也可以用手机顶替**，零成本，甚至不用装 App：

```bash
python phone_cam.py     # 手机浏览器打开它打印的 https 地址即可（含自签证书说明）
```

> 详见 [docs/手机当摄像头.md](docs/手机当摄像头.md)（另有 App / 虚拟摄像头两条路线，
> 含国内可下载的软件清单）。
> ⚠️ 手机必须**横屏**且与录模板时同一宽高比——2d 特征对比例敏感
> （实测竖屏会把躯干角从 6.8° 抬到 20.7°），或直接改用 `--space 3d` 规避。

### 5. 录制骨骼序列动作模板（精细动作匹配）

规则型动作适合静态姿态，而「翻身、拍背、环抱转移」等过程性动作更适合用骨骼序列模板：

```bash
python record_template.py --name "协助翻身动作" --duration 5      # 本机摄像头录制 5 秒
python record_template.py --name "拍背排痰" --source rtsp://... --duration 6
python record_template.py --name "环抱转移" --action-id act_xxx    # 覆盖已有动作
```

倒计时结束后在镜头前完整演示该动作，脚本自动采集骨骼序列并保存为「序列型」动作模板，
监控时通过 DTW 动态时间规整实时比对。

> 模板与实时画面必须处在同一归一化空间。系统在匹配时会用**模板自身的均值/标准差**
> 归一化实时滑窗（不能用滑窗自身的统计量，否则短窗口会把整段动作压平而永远匹配不上）。

**多机位共用一套模板（`--space 3d`）**

默认特征空间是 `2d`（图像空间），此时**摄像头位置和角度必须固定，换机位就要重录模板**。
若希望多个机位共用同一套模板，用三维机位无关特征：

```bash
python record_template.py --name "协助翻身" --space 3d --duration 6
FEATURE_SPACE=3d python run.py        # 运行时切到同一空间
```

三维特征把所有关节角改用米制三维坐标计算，不再参照图像竖直方向，因此不受镜头俯仰/
偏转影响；实测「换机位额外造成的匹配损失」从 +45.8 降到 +0.78（约 58 倍改善）。
详见 [docs/多机位模板方案.md](docs/多机位模板方案.md)。

> **阈值必须实拍标定。** 切换到 `3d` 后请先用 `calibrate_template.py` 在你自己的机位上测出
> 真实距离再固化阈值——仿真给的 40.0 只是起点：
>
> ```bash
> python calibrate_template.py --action-id act_xxx --duration 6 --label right   # 正确动作
> python calibrate_template.py --action-id act_xxx --duration 6 --label wrong   # 无关动作
> ```
>
> 版本不符的历史模板会被**自动跳过**（而不是拿旧模板硬匹配），避免误报。

### 6. 运行测试

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -q        # 请用 python -m，确保 backend 包可被导入
```

接口测试用 SQLite 垫片替换数据层，因此**不需要 MySQL、摄像头或 opencv** 即可运行。

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
| `trunk_inclination` | 躯干偏离直立角（度） |
| `left/right_elbow_angle` | 肘关节角度 |
| `left/right_knee_angle` | 膝关节角度 |
| `left/right_hip_angle` | 髋关节角度 |
| `hand_height_left/right` | 手相对肩的高度 |
| `hands_distance` | 双手距离（肩宽归一） |
| `body_height` / `hip_center_height` | 身体/髋部高度 |

动作 = 一组「特征 + 运算符 + 阈值」条件全部满足，并**持续达到指定时长**（抗抖动）。
识别出的动作序列再通过 LCS 与流程步骤序列比对，得出完成/漏步/得分。

> **`trunk_inclination` 是「无符号」角度**：它取躯干与竖直轴的夹角，因此前倾 40°
> 与后仰 40° 的取值相同，都是 40；90° 表示躯干水平，>90° 表示头低于髋。
> 需要区分前后方向时，请配合其他特征一起判定。
>
> 该值为 0 表示**完全直立**。曾经此处把参考轴取反，导致直立被测成 180°，
> 于是「越前倾值越大」的规则在站立画面里持续命中（误报）、
> 「值越小」的规则永不命中（漏报）。修正后 2D 特征版本号升到 `v2`，
> **v1 的历史模板会被自动跳过而不是拿旧模板硬匹配**（见下节「特征版本」）。

> **阈值别手调，用 `calibrate_rule.py` 标定。** 该工具按「目标动作」与「无关动作」
> 两段实拍给出建议阈值，并刻意取**偏保守**的一侧（宁可漏报别误报）：
>
> ```bash
> # 方式一：针对某个已有的规则动作
> python calibrate_rule.py --action-id act_xxx --duration 6 --label hit   # 做目标动作
> python calibrate_rule.py --action-id act_xxx --duration 6 --label miss  # 做无关动作
>
> # 方式二：直接指定要标定的关节（模板还没建时用）
> python calibrate_rule.py --joints trunk_inclination,left_knee_angle \
>     --duration 6 --label hit --source 0
>
> python calibrate_rule.py --action-id act_xxx --report   # 只看已采集到的结果
> ```
>
> 没有摄像头时用 `--source some_video.mp4 --headless` 跑已录制的视频。

> 上表是**二维图像空间**特征（`FEATURE_SPACE=2d`），以图像竖直方向为参照，
> 因此要求摄像头位置角度固定。序列模板还支持一套**三维机位无关**特征
> （`FEATURE_SPACE=3d`，见上文「多机位共用一套模板」），用米制三维坐标计算关节角，
> 不受镜头俯仰/偏转影响。

### 运行模式与性能

姿态推理使用 MediaPipe Tasks 的 `PoseLandmarker`，**默认 `RunningMode.VIDEO`**
（由 `PoseEngine(running_mode=...)` 控制，不可用时自动退回 `IMAGE` 并提示）。

这一点对结果质量的影响比速度更大。`IMAGE` 模式把每帧当作互不相关的独立图片做一次
全量检测，没有帧间跟踪，因此单帧关键点会突然失稳。同一段 720p 素材（526 帧）实测：

| 指标 | IMAGE | VIDEO | 改善 |
|---|---|---|---|
| 单帧推理 | 23.07 ms | 9.74 ms | 2.37× |
| 处理帧率 | 38.3 fps | 77.5 fps | 2.02× |
| 姿态检出率 | 98.7 % | 100.0 % | — |
| 躯干角 p99 | 171.82° | 4.88° | 35× |
| 失稳帧（>55°） | 19 帧（3.66 %） | **0 帧** | 完全消除 |

那 19 个失稳帧正是「正常站姿被判成深弯腰」的来源，所以切换模式**同时也是在减少误报**。

其余两处已做的优化：

- **DTW 距离**：代价矩阵一次算完（30×16 基准 2.00 ms → 0.12 ms），
  等价性由 `tests/test_dtw_equivalence.py` 对拍保证。
- **预览画面按需编码**：没人看时完全不编码，有人看时限到 12 fps。
  原先按源帧率逐帧编码 JPEG（720p 约 9.5 ms/帧，占单帧总耗时 25%），
  而前端约 120 ms 才取一次画面。可用环境变量调整：

  ```bash
  PREVIEW_FPS=12          # 预览画面最高帧率（编码上限，与源帧率无关）
  PREVIEW_IDLE_SEC=3.0    # 超过这么久没人取过画面就停止编码
  ```

> **录制窗口按时间轴计时，不是按帧数。** `record_template.py` / `calibrate_template.py` /
> `calibrate_rule.py` 对文件源使用视频时间轴、对实时源使用墙钟。
> 否则 `--duration 5` 实际覆盖的素材时长会随机器快慢和推理模式漂移
> （实测同一素材 VIDEO 模式 8.88 s、IMAGE 模式 4.20 s），录出的模板不可复现。

**模板会记录录制时的推理模式（`engine_mode`）。** 因为 IMAGE 模式约 3.7% 的帧会失稳
（躯干角 4° → 170°+），而录制会下采样到几十帧，失稳向量有相当概率被存进模板并
**永久拉偏 DTW**——症状是「时好时坏且不可解释」，最难排查。

所以运行时若发现某条序列模板是用 IMAGE 模式录的，会**跳过它并提示重录**而不是硬匹配。
未声明该字段的旧模板不受影响（该字段是本次才加入录制流程的）。

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
4. **分级权限**：仅管理员可管理用户、隐私策略、审计日志；教师/管理员才能维护培训标准（流程、动作、设备）
5. **审计留痕**：登录、增删改、培训完成等操作全量记录
6. **口令安全**：PBKDF2-HMAC-SHA256（默认 20 万次迭代 + 随机盐），兼容并自动升级历史哈希
7. **会话管理**：令牌默认 7 天有效（活跃自动续期），过期会话自动清理
8. **抗暴力破解**：登录 5 分钟 10 次、注册 1 小时 8 次（按 IP + 账号限流）
9. **监控画面鉴权**：实时画面帧接口需令牌校验，且仅本人可看自己的会话（教师/管理员可看全部）
