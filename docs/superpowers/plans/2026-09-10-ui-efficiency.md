# 界面提效改造 实施记录

对应设计：docs/superpowers/specs/2026-09-10-ui-efficiency-design.md

## P1 导航与视觉统一（已完成）

- `frontend/js/app.js`：导航按教学/组织/内容/练习/管理分组；取消全部导航图标；教师登录直达教学看板。
- `frontend/css/style.css`：新增分组标题样式、大屏模式与行样式、表格行距放宽。

## P2 教学看板与大屏（已完成）

- 会话状态：`backend/vision/monitor_session.py` 增加停留时长、离屏时长、预警级别（阈值来自 privacy_settings）。
- 看板接口：`backend/routers/dashboard.py` 返回上述字段与阈值；新增 GET/PUT `/api/dashboard/thresholds`。
- 前端：看板状态条、异常筛选、操作列（看画面/发消息）、阈值设置弹窗、大屏入口；`?tv=1` 大屏页含总览/预警/班级进度与 Esc 退出。

## P3 消息与私密备注（已完成）

- 新表 `messages`；新路由 `backend/routers/messages.py`：会话列表、未读计数、会话详情（自动已读）、发送（教师发消息/备注，学员仅回复已有对话）。
- 前端：导航"消息"未读红点、会话列表+聊天窗口+回复框；看板/档案均可发起。

## P4 档案与步骤库（已完成）

- 档案：新增搜索（姓名/账号）、排序（姓名/平均分/最近练习/预警次数）与预警次数列。
- 步骤库：护理流程与动作模板合并为标签页（`App.renderLibrary`）。

## 验证

- `tests/test_database_compat.py`、`tests/test_assessment.py`、`tests/test_classroom.py` 全部通过；`node --check frontend/js/app.js` 通过。
- 接口冒烟：看板阈值读写、教师发消息/私密备注、学员会话与回复、未读计数、档案搜索排序均通过；冒烟数据已清理。
- 已知限制：本环境无浏览器/Word 渲染，界面效果需在浏览器中人工确认。
