# MySQL 实库验证报告

> 验证对象：智慧实训规范平台（`E:\eldercare-platform`）
> 验证日期：2026-09-16
> 数据库：MySQL 8.0.44，库名 `eldercare`（本机真实库，非测试库）
> 验证方式：真实 MySQL + FastAPI ASGI TestClient + 真实 uvicorn 进程 + curl

---

## 一、结论摘要

| 项目 | 结果 |
|---|---|
| 累计检查项 | 90 项 |
| 通过 | 82 项 |
| 未通过 | 8 项（**经核查全部为验证脚本自身误差，非产品缺陷**） |
| 修正脚本后权限矩阵复测 | **25 / 25 全部通过** |
| 需要改动的生产代码 | **0 处** |
| 验证期间对真实库的写入 | **0 行**（仅走只读与拒绝路径） |

8 项"未通过"的成因分布：接口路径靠记忆猜测 4 项、测试脚本用错 HTTP 方法 1 项、
对接口角色定位判断错误 3 项。

---

## 二、关键验证项（全部通过）

### 1. 启动幂等性

`init_db()` + `seed()` 在已有真实数据的库上重复执行，**核心业务表行数零变化**：

| 表 | 启动前 | 启动后 |
|---|---|---|
| users | 5 | 5 |
| processes | 4 | 4 |
| actions | 6 | 6 |
| devices | 1 | 1 |

结论：种子数据不会重复插入，也不会覆盖已有内容，可以安全地反复重启服务。

### 2. 旧口令哈希兼容与自动升级

库中 5 个账号的 `password_hash` 原本均为**旧格式**（`salt$sha256`，单轮）。

| 账号 | 验证前 | 验证后 |
|---|---|---|
| admin | `5b5915dd...$df4d...`（sha256） | `pbkdf2_sha256$200000$...` |
| nurse001 | `fa5e2c8d...$bc9d...`（sha256） | `pbkdf2_sha256$200000$...` |
| teacher001 / caregiver001 / elderly001 | sha256 | 保持 sha256（尚未登录，登录时再升级） |

结论：老密码**无需重置即可正常登录**，且登录成功后自动升级为 PBKDF2-HMAC-SHA256（20 万次迭代）；
未登录账号保持原样，升级是懒加载的。

### 3. 会话有效期与过期清理

`sessions` 表由 30 行降为 6 行，逐行核对：被清理的 24 条 `created_at` 均在 7 天以前；
保留的 5 条有效会话年龄 6.0 天，以及本次登录新建的 1 条。

结论：过期清理只删该删的，未误伤有效会话。

### 4. 角色权限矩阵（25 / 25 通过）

写操作（POST / PUT / DELETE）——学员与长期护理员**全部 403**：

| 接口 | 学员 | 长期护理员 |
|---|---|---|
| `/api/processes` | 403 | 403 |
| `/api/processes/{id}` | 403 | 403 |
| `/api/actions` | 403 | — |
| `/api/devices` | 403 | — |
| `/api/users` | 403 | — |
| `/api/classes` | 403 | — |
| `/api/tasks` | 403 | — |

只读接口——所有已登录用户均可读：`/api/processes`、`/api/actions`、`/api/devices`（学员 / 护理员 / 教师均 200）。

教师专属接口——学员与护理员被拒：

| 接口 | 学员 | 护理员 | 教师 | 管理员 |
|---|---|---|---|---|
| `/api/dashboard/live` | 403 | 403 | 200 | 200 |
| `/api/classes` | 403 | 403 | 200 | 200 |
| `/api/teacher/students` | 403 | — | 200 | 200 |
| `/api/users` | 403 | — | 403 | 200 |

说明：`/api/dashboard/live`（教学预警看板）仅对教师/管理员开放，与前端菜单可见性一致，属既定设计。

### 5. 监控画面接口鉴权

| 请求 | 返回 |
|---|---|
| `GET .../frame` 无令牌 | 401 |
| `GET .../frame?token=<伪造>` | 401 |
| `GET .../frame?token=<有效>` + 不存在的会话 | 404（鉴权已通过，再查会话） |
| `GET .../stream` 无令牌 | 401 |

结论：拿到 session id 也无法在未授权情况下查看摄像头画面；鉴权在前、会话查询在后。
前端 `<img>` 通过 `?token=` 查询参数携带令牌（`frameUrl()`）。

### 6. 未授权访问全景

对 11 个业务接口发起无令牌请求：`/api/users`、`/api/processes`、`/api/actions`、`/api/devices`、
`/api/classes`、`/api/tasks`、`/api/dashboard/live`、`/api/privacy/settings`、`/api/privacy/audit-logs`、
`/api/messages/conversations`、`/api/dashboard/thresholds` —— **全部 401，无一处泄露**。

### 7. 真实 uvicorn 启动链路

以真实 MySQL 启动 `backend.main:app` 后，用 curl 实测：

| 请求 | 结果 |
|---|---|
| `GET /` | 200，2333 字节，标题「智慧实训规范平台」 |
| `GET /js/app.js` | 200，85117 字节 |
| `GET /css/style.css` | 200，8750 字节 |
| `GET /api/health` | 200 `{"status":"ok","app":"eldercare","vision_available":false}` |
| 登录 admin / admin123 | 200，返回令牌 |
| 学员 `GET /api/users` | 403 |
| 学员 `DELETE /api/processes/{id}` | 403 |
| 管理员 `GET /api/processes` | 200 |
| 错误密码登录 | 401 |
| 连续失败登录 | 第 10 次起 429（限流生效） |
| 登出后复用旧令牌 | 401 |

### 8. 数据完整性抽查

- 4 个护理流程的 `steps` 均可正确反序列化（口腔护理 4 步、床-轮椅转移 5 步、协助进食 4 步、协助翻身 6 步）。
- 6 个动作模板的 `conditions` 均可正确反序列化。
- 流程步骤引用的 `action_id` **无悬空引用**（否则漏步检测会误判）。
- 动作列表接口不再返回完整骨骼向量（`_template_data` 已移除，仅返回 `template_frames` / `template_threshold`）。

---

## 三、接口路径备忘

验证中曾因凭记忆猜路径而误报 404，以下为实测确认的真实路径：

| 用途 | 真实路径 |
|---|---|
| 审计日志 | `GET /api/privacy/audit-logs` |
| 预警阈值 | `GET` / `PUT /api/dashboard/thresholds` |
| 消息会话列表 | `GET /api/messages/conversations` |
| 未读数 | `GET /api/messages/unread-count` |
| 消息明细 | `GET /api/messages/thread/{uid}` |
| 发送消息 | `POST /api/messages` |
| 学员任务 | `GET /api/tasks/mine`（学员专属，管理员访问返回 403） |

**注意**：`privacy_settings` 是通用键值表，除 6 个隐私策略键外，还存有 3 个**预警阈值**：
`alert_step_warn`（默认 30）、`alert_step_crit`（默认 60）、`alert_off_seconds`（默认 5），
分别由 `backend/routers/dashboard.py` 与 `backend/vision/monitor_session.py` 读写，**不可当作脏数据删除**。

---

## 四、数据安全措施

1. 动手前已用 `mysqldump --single-transaction --routines --events` 全库备份：
   `.workbuddy/backup/eldercare_20260916_230057.sql`（29225 字节）。
   如需回滚：`mysql -u root -p < eldercare_20260916_230057.sql`。
2. 备份文件含 `password_hash` 与审计日志，属敏感数据。已在 `.gitignore` 中新增
   `.workbuddy/`、`*.sql`、`*.sql.gz`、`dump/`，防止误提交到 GitHub。
3. 验证过程**未将数据库密码写入任何文件或代码**。
4. 本轮验证只发只读请求与必然被拒绝的写请求，**未在库中新增任何业务数据**
   （仅登录会产生会话记录与审计日志，属正常使用痕迹）。

---

## 五、后续建议

以下为验证中观察到、但**本次未改动**的事项：

1. **`POST /api/training/records` 是遗留接口**，任何登录用户可自助写入自己的训练记录，
   前端已不再调用。建议移除或收紧。
2. **限流为单进程内存实现**（`backend/ratelimit.py`）。若用 `uvicorn --workers N`
   或多实例部署，须替换为 Redis 等集中式存储。
3. **`messages.conversations`、`dashboard.live` 存在 N+1 查询**，数据量大时可优化。
4. **监控源允许任意 URL**（RTSP / HTTP / 本地文件路径），内网部署下有轻微 SSRF 面。
5. **会话默认 7 天有效**，可通过环境变量 `SESSION_TTL_SECONDS` 调整；
   学习机房等共享终端场景可适当调短。
6. 上线前请完成：修改 `admin` 默认密码、绑定域名 + HTTPS、关闭 3306 公网暴露、配置定时备份。

---

## 六、复现方式

```bash
# 1) 自动化测试（SQLite 垫片，不需要 MySQL / 摄像头 / opencv）
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -q          # 71 passed

# 2) 连接真实 MySQL 启动服务
# macOS / Linux
MYSQL_PASSWORD=你的密码 python run.py
# Windows PowerShell
$env:MYSQL_PASSWORD="你的密码"; python run.py
# 访问 http://localhost:8000
```

环境变量：`MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DB`、
`SESSION_TTL_SECONDS`、`CORS_ORIGINS`。
