# 动作捕捉对比分析平台 · 设计文档

日期：2026-09-06
状态：待用户复核

## 1. 背景与目标

在现有「智慧实训规范平台」基础上，扩展为面向教师与学员的**动作对比训练平台**：

- 教师端：班级管理、任务发布、实时预警看板、学员档案。
- 学员端：登录、任务列表、步骤操作页、个人记录。
- 步骤库管理：创建模板、添加/编辑/删除步骤、预览模板库。
- 对比逻辑：标准步骤序列 vs 实际完成序列，检测**遗漏**与**顺序错误**，输出总分 + 每步得分 + 遗漏/错误列表。
- 动作捕捉数据源以摄像头 + MediaPipe 为主，预留专业动捕设备导入接口（本期不实现导入器）。

数据库从 SQLite 迁移到本机 MySQL（用户已安装），所有新增表直接建在 MySQL。

## 2. 现状与差距

| 需求 | 现状 | 动作 |
|---|---|---|
| 教师端 · 班级管理 | 无班级概念 | 新增 classes / class_members |
| 教师端 · 任务发布 | 只有护理流程静态模板 | 新增 tasks，绑定班级+流程 |
| 教师端 · 实时预警看板 | 单会话步骤状态，无汇总看板 | 新增看板接口与页面 |
| 教师端 · 学员档案 | training_records 有原始数据 | 新增按学员聚合视图 |
| 学员端 · 登录 | 已有（按角色） | 复用 |
| 学员端 · 任务列表 | 无「我的任务」 | 新增 |
| 学员端 · 步骤操作页 | 实时监管页可当雏形 | 改为接任务→做动作→出结果的闭环 |
| 学员端 · 个人记录 | 列表未按本人过滤 | 增加本人过滤 |
| 步骤库管理 | 流程 CRUD + 步骤内嵌编辑 + 预览 | 基本具备，沿用 |
| 对比逻辑 | LCS 只分「完成/漏步」，乱序被误判为漏步 | 升级为遗漏/顺序错误/完成三类 |
| 每步质量得分 | 无（只有流程总分） | 新增采集落库 + 分段 DTW 评分 |
| 数据库 | SQLite | 迁移 MySQL |

## 3. 技术基线

- 后端：Python + FastAPI + PyMySQL（SQL 直写，保持现有轻量风格）
- 前端：原生 HTML/CSS/JS SPA，无构建步骤
- 视觉：OpenCV + MediaPipe PoseLandmarker，沿用 `backend/vision/` 现有模块
- 数据库：本机 MySQL，库名 `eldercare`，utf8mb4
- 默认账号与角色沿用现有 `config.ROLES`

角色能力划分：

| 角色 | 能力 |
|---|---|
| admin / elderly_service_teacher（养老服务师） | 班级管理、任务发布、看板、学员档案、步骤库管理 |
| nursing_student / long_term_caregiver / elderly_caregiver | 学员端：任务列表、步骤操作、个人记录 |

## 4. 总体数据模型（MySQL 新增/变更表）

沿用现有表：users、sessions、processes、actions、devices、monitoring_records、privacy_settings、audit_logs。

变更：

- `training_records` 增加列：`task_id TEXT DEFAULT ''`、`capture_record_id TEXT DEFAULT ''`

新增：

```
classes          -- 班级
  id, name, owner_user_id(教师), description, created_at

class_members    -- 班级成员
  class_id, user_id, joined_at   (PK: class_id+user_id)

tasks            -- 教师发布的任务
  id, class_id, process_id, title, description,
  deadline(INTEGER 秒), status(published/closed), created_by, created_at

motion_capture_records  -- 一次对比评分运行的完整数据
  id, user_id, task_id, process_id, source, source_type(default 'mediapipe'),
  status(capturing/finished), started_at, ended_at,
  steps JSON(流程步骤快照), frames JSON(逐帧特征+相对时间),
  segments JSON(每步边界与结果), score REAL, created_at
```

说明：任务不单独存「某学员是否完成」，由该学员是否存在 finished 的 training_records（task_id 关联）推导。

## 5. 分阶段实施

### P0 · MySQL 切换

1. `requirements.txt` 增加 `PyMySQL`。
2. `config.py` 增加 MySQL 连接配置（环境变量可覆盖）：
   - MYSQL_HOST=localhost、MYSQL_PORT=3306、MYSQL_USER=root、MYSQL_PASSWORD=、MYSQL_DB=eldercare
3. `backend/database.py` 改造：
   - `get_conn()` 改为 PyMySQL 连接（DictCursor、utf8mb4、autocommit 沿用每次 execute 后 commit 的现有模式）。
   - 启动时先 `CREATE DATABASE IF NOT EXISTS eldercare CHARACTER SET utf8mb4`，再建表。
   - SQL 占位符统一处理：database.py 层把 `?` 转成 `%s`，路由层现有 SQL 基本不动。
   - 删除 SQLite 专用 PRAGMA。
4. `backend/routers/privacy.py` 的 `ON CONFLICT(key) DO UPDATE` 改为 MySQL 的 `ON DUPLICATE KEY UPDATE`。
5. seed 保持不变：MySQL 空库首次启动自动写入默认账号/流程/动作/设备。
6. 旧 SQLite 数据：开发环境不迁移，直接 seed；若需保留旧数据，实施时另写一次性导入脚本（默认不做）。

验收：全新 MySQL 空库启动服务 → 自动建库建表 + 种子数据 → 登录/CRUD 冒烟通过。

### P1 · 对比引擎升级（含采集、评分、遗漏/顺序错误）

#### 实时采集

- `MonitorSession` 增加 `assess` 模式；普通监控模式行为完全不变。
- 逐帧把 16 维特征向量 + 相对时间戳追加进内存列表（不落原始视频，不存 33 关键点）。
- 自动切步（近似边界，允许结果页手动修正）：
  - 规则型步骤：条件满足起始帧 → 离开帧。
  - 序列型步骤：DTW 命中帧向前推模板长度个帧。
- 全部步骤完成（顺序分=100）自动结束；教师/学员可随时手动结束；结束即落库并返回 record_id。

#### 对比算法规格

输入：标准步骤序列 `S`（按流程步骤顺序，每步含 action_id）、实际识别事件序列 `E`（按时间）。

匹配算法用**带占用标记的贪心顺序匹配**（不用现有 LCS，原因见下）：

1. 指针指向 `E` 开头；按 `S` 顺序为每步寻找其 action 的下一个未占用事件；找到则标记占用并推进指针，该步 = **完成且顺序正确**。
2. 遍历完 `S` 后，仍有**未占用事件**（执行过但没排进正确位置）的步骤 = **顺序错误**（优先按该步骤 action 对应的最早未占用事件归属，一步一事件，避免重复归属）。
3. 既没被匹配、其 action 也没有未占用事件的步骤 = **遗漏**。
4. 每步质量分：该步事件对应帧段 vs 该动作序列模板的归一化 DTW 距离，`score = 100 × (1 - min(dist/threshold, 1))`。
5. 每步结果与得分：
   - 完成且动作有序列模板 → DTW 质量分。
   - 完成但动作无序列模板（纯规则动作未升级）→ 显示「无标准」，不计入平均。
   - 顺序错误 / 遗漏 → 0 分（有模板时计入平均）。
6. 总分 = 有模板步骤的平均分。
7. 输出：总分、每步得分（含结果分类：完成/遗漏/顺序错误）、遗漏列表、顺序错误列表。

设计说明：不用现有 LCS 回溯，是因为种子流程里同一动作会重复出现在多步（如「双手靠近」出现两次），LCS 无法表达「一次执行只能占用一步、缺一次执行应判遗漏而不是顺序错误」。贪心匹配按步骤顺序找首个未占用事件，能正确处理重复步骤与次数不足；现有 `match_sequence`（LCS）仅保留给普通监控模式的实时步骤状态显示，评估分类用新模块。

#### 新增表

`motion_capture_records`（见第 4 节）。

#### 接口

- `POST /api/assessment/sessions` `{source, process_id, task_id?}` → `{session_id}`
- 复用 `GET /api/monitoring/sessions/{sid}/frame|state` 做实时画面与步骤状态
- `DELETE /api/assessment/sessions/{sid}` → 等待线程收尾，返回 `{record_id}`
- `GET /api/assessment/records` → 列表（本人可见自己的，教师可见所属学员）
- `GET /api/assessment/records/{rid}` → 报告详情（步骤、边界、结果分类、每步得分、总分）
- `PUT /api/assessment/records/{rid}/segments` `{segments:[{order,start_idx,end_idx}]}` → 重算并返回详情

#### 前端

- 新导航「动作对比」：选流程/设备 → 实时画面 + 步骤状态 → 结束后跳报告页。
- 报告页：总分、每步得分与分类、遗漏/顺序错误列表；每步起止时间可编辑后重算。
- 本期不做曲线图、不做文字偏差提示（用户已确认）。

#### 检查

- 纯逻辑（贪心分类 + 分段评分）留一个 `assert` 自检脚本，用合成序列验证：
  顺序正确 → 无遗漏/无顺序错误且高分；乱序 → 出现顺序错误；缺步 → 出现遗漏。

### P2 · 班级与任务

新增表：`classes`、`class_members`、`tasks`（见第 4 节）。

教师接口（仅 admin / elderly_service_teacher）：

- 班级：`GET/POST /api/classes`、`PUT/DELETE /api/classes/{cid}`
- 成员：`GET /api/classes/{cid}/members`、`POST`（按 username 批量加入）、`DELETE`
- 任务：`GET/POST /api/tasks`、`PUT/DELETE /api/tasks/{tid}`、发布时绑定班级与流程并设置 deadline

学员接口：

- `GET /api/tasks/mine` → 我所在班级的已发布任务 + 我的完成状态（是否有 finished 记录、最新得分）

前端：

- 教师「班级管理」页：班级 CRUD、学员按账号添加/移除。
- 教师「任务发布」页：选班级 → 选流程（预览步骤）→ 标题/说明/截止时间 → 发布/关闭。
- 学员「我的任务」页：任务卡片，显示截止时间与完成状态，已完成显示得分。

### P3 · 学员端闭环

- 步骤操作页：学员从「我的任务」进入 → 复用 P1 的 assess 会话页，绑定 `task_id`。
- 会话结束落库时：写入 `motion_capture_records.task_id`，并写入一条 `training_records`（含 `task_id`、`capture_record_id`、总分）。
- 任务完成判定：存在该学员该任务的 finished 记录；重复练习保留多次历史。
- 个人记录页：`GET /api/training/mine`（本人过滤），列表 + 点进报告详情。

### P4 · 教师端看板与档案

#### 实时预警看板

- 数据来源：内存中的进行中 assess 会话 + 最近 50 条 finished 记录。
- 两个面板：
  1. 进行中：学员、班级、流程、当前步骤、顺序分，3 秒自动刷新。
  2. 预警：总分 < 60 或含遗漏/顺序错误的最新记录，标注类型。
- 接口：`GET /api/dashboard/live`（教师）。

#### 学员档案

- `GET /api/teacher/students?class_id=` → 学员列表 + 汇总（任务数、完成数、平均分）。
- `GET /api/teacher/students/{uid}/records` → 该学员全部记录（时间、流程、得分、遗漏/错误数），可点进报告。

## 6. 隐私与数据量

- 沿用隐私默认：不存原始视频、界面脱敏、仅管理员可看审计。
- `motion_capture_records.frames` 只存脱敏关节特征（16 维），一条 60 秒记录约几百 KB，本地 MySQL 可接受。
- 记录列表沿用现有 LIMIT 200 风格；删除策略后续按需加，本期不做。

## 7. 测试与验收

- P1 自检：贪心分类 + 评分（合成数据 assert）。
- 每个阶段结束时做接口冒烟 + 页面手工验收：
  - P0：MySQL 上登录/CRUD 正常。
  - P1：录制标准（命令行）→ 学员做一遍 → 报告含每步得分与遗漏/顺序错误列表 → 调边界重算生效。
  - P2：建班、加学员、发任务、学员看到任务。
  - P3：接任务做动作 → 自动生成记录 → 个人记录可见。
  - P4：看板出现进行中/预警数据，档案聚合正确。

## 8. 明确不做（本期 YAGNI 边界）

- 专业动捕设备导入器（本期只预留 `source_type` 字段与特征向量接口约定）。
- 浏览器内录制标准模板（沿用命令行 `record_template.py`）。
- 曲线图、文字偏差提示、PDF 报告。
- 任务逾期/消息推送等通知。
- 视频文件上传离线分析。
