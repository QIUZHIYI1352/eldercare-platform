"""接口层端到端测试：登录鉴权、权限边界、核心 CRUD。

依赖 conftest.py 的 SQLite 垫片，无需 MySQL / 摄像头 / opencv。
"""
import time

from tests.conftest import as_admin, as_student, as_teacher, login


# ---------- 基础与鉴权 ----------

def test_health_ok_without_vision(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # 未安装 cv2/mediapipe 时也必须能正常返回（这正是原先会崩溃的场景）
    assert "vision_available" in body


def test_frontend_static_files_served(client):
    """前端由同一个端口提供（StaticFiles 挂载在 /）。"""
    r = client.get("/")
    assert r.status_code == 200
    assert "智慧实训规范平台" in r.text
    assert client.get("/js/app.js").status_code == 200
    assert client.get("/css/style.css").status_code == 200


def test_openapi_schema_builds(client):
    """所有路由都能被 FastAPI 正确解析（可捕获参数/依赖声明错误）。"""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert len(r.json()["paths"]) >= 30


def test_protected_endpoints_require_token(client):
    for path in ["/api/processes", "/api/actions", "/api/devices",
                 "/api/training/records", "/api/auth/me"]:
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers={"Authorization": "Bearer bad-token"}).status_code == 401


def test_login_rejects_wrong_password(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_login_and_me(client):
    data = login(client, "admin", "admin123")
    assert data["token"] and data["user"]["role"] == "admin"
    assert "password_hash" not in data["user"]
    me = client.get("/api/auth/me",
                    headers={"Authorization": "Bearer " + data["token"]}).json()
    assert me["user"]["username"] == "admin"


def test_logout_invalidates_token(client):
    token = login(client, "admin", "admin123")["token"]
    h = {"Authorization": "Bearer " + token}
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_login_rate_limited(client):
    """连续错误尝试应触发限流（抵御暴力破解）。"""
    codes = [client.post("/api/auth/login",
                         json={"username": "admin", "password": "bad"}).status_code
             for _ in range(12)]
    assert 429 in codes, f"应出现 429 限流，实际 {codes}"


def test_register_creates_student_and_hashes_password(client, shim):
    r = client.post("/api/auth/register", json={
        "username": "newbie01", "password": "abc123", "name": "新学员",
        "role": "nursing_student"})
    assert r.status_code == 200, r.text
    row = shim.query_one("SELECT password_hash FROM users WHERE username = ?", ("newbie01",))
    assert row["password_hash"].startswith("pbkdf2_sha256$")
    assert "abc123" not in row["password_hash"]


def test_register_rejects_admin_role_and_bad_input(client):
    base = {"username": "hacker01", "password": "abc123"}
    assert client.post("/api/auth/register",
                       json={**base, "role": "admin"}).status_code == 400
    assert client.post("/api/auth/register",
                       json={"username": "ab", "password": "abc123"}).status_code == 400
    assert client.post("/api/auth/register",
                       json={"username": "okname", "password": "123"}).status_code == 400
    # 超长用户名不能把数据库打崩，应被 400 拦下
    assert client.post("/api/auth/register",
                       json={"username": "u" * 200, "password": "abc123"}).status_code == 400
    assert client.post("/api/auth/register",
                       json={"username": "with space", "password": "abc123"}).status_code == 400


def test_duplicate_username_conflict(client):
    payload = {"username": "dupuser", "password": "abc123"}
    assert client.post("/api/auth/register", json=payload).status_code == 200
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_session_ttl_expiry(client, shim):
    """过期会话必须失效。"""
    data = login(client, "admin", "admin123")
    token = data["token"]
    shim.execute("UPDATE sessions SET created_at=? WHERE token=?",
                 (int(time.time()) - 30 * 24 * 3600, token))
    assert client.get("/api/auth/me",
                      headers={"Authorization": "Bearer " + token}).status_code == 401


# ---------- 权限边界 ----------

def test_user_management_is_admin_only(client):
    assert client.get("/api/users", headers=as_student(client)).status_code == 403
    assert client.get("/api/users", headers=as_teacher(client)).status_code == 403
    assert client.get("/api/users", headers=as_admin(client)).status_code == 200


def test_user_list_masks_phone(client):
    items = client.get("/api/users", headers=as_admin(client)).json()["items"]
    assert items
    for u in items:
        masked = u.get("phone_masked") or ""
        assert "****" in masked or len(masked) <= 2


def test_admin_cannot_delete_self(client):
    me = client.get("/api/auth/me", headers=as_admin(client)).json()["user"]
    r = client.delete(f"/api/users/{me['id']}", headers=as_admin(client))
    assert r.status_code == 400


def test_privacy_settings_admin_only(client):
    assert client.get("/api/privacy/settings", headers=as_teacher(client)).status_code == 200
    assert client.put("/api/privacy/settings", json={"settings": {"face_blur": True}},
                      headers=as_teacher(client)).status_code == 403
    r = client.put("/api/privacy/settings", json={"settings": {"face_blur": True}},
                   headers=as_admin(client))
    assert r.status_code == 200
    assert client.get("/api/privacy/settings",
                      headers=as_admin(client)).json()["settings"]["face_blur"] is True


def test_audit_log_written(client):
    as_admin(client)
    logs = client.get("/api/privacy/audit-logs", headers=as_admin(client)).json()["items"]
    assert any(l["action"] == "login" for l in logs)


def test_student_cannot_manage_content(client):
    """护理流程 / 动作模板 / 设备属于培训标准，学员只能读不能改。"""
    h = as_student(client)
    assert client.get("/api/processes", headers=h).status_code == 200
    assert client.get("/api/actions", headers=h).status_code == 200
    assert client.get("/api/devices", headers=h).status_code == 200

    assert client.post("/api/processes", json={"name": "x", "steps": []},
                       headers=h).status_code == 403
    assert client.post("/api/actions", json={
        "name": "x", "conditions": [{"joint": "trunk_inclination", "op": ">", "value": 1}]},
        headers=h).status_code == 403
    assert client.post("/api/devices", json={"name": "x"}, headers=h).status_code == 403

    pid = client.get("/api/processes", headers=h).json()["items"][0]["id"]
    assert client.delete(f"/api/processes/{pid}", headers=h).status_code == 403
    aid = client.get("/api/actions", headers=h).json()["items"][0]["id"]
    assert client.delete(f"/api/actions/{aid}", headers=h).status_code == 403

    # 教师可以维护
    assert client.post("/api/processes", json={"name": "教师建的流程", "steps": []},
                       headers=as_teacher(client)).status_code == 200

    assert client.get("/api/classes", headers=h).status_code == 403
    assert client.get("/api/tasks", headers=h).status_code == 403
    assert client.get("/api/tasks/mine", headers=as_teacher(client)).status_code == 403


# ---------- 护理流程 CRUD ----------

def test_process_crud(client):
    h = as_admin(client)
    created = client.post("/api/processes", json={
        "name": "测试流程", "category": "基础护理", "description": "d",
        "steps": [{"order": 1, "name": "第一步", "action_id": "act_x"}]}, headers=h)
    assert created.status_code == 200
    pid = created.json()["id"]

    detail = client.get(f"/api/processes/{pid}", headers=h).json()
    assert detail["steps"][0]["name"] == "第一步"

    assert client.put(f"/api/processes/{pid}",
                      json={"name": "改名流程", "steps": detail["steps"]},
                      headers=h).status_code == 200
    assert client.get(f"/api/processes/{pid}", headers=h).json()["name"] == "改名流程"

    assert client.delete(f"/api/processes/{pid}", headers=h).status_code == 200
    assert client.get(f"/api/processes/{pid}", headers=h).status_code == 404


def test_process_requires_name(client):
    assert client.post("/api/processes", json={"name": "  "},
                       headers=as_admin(client)).status_code == 400


# ---------- 动作模板 ----------

def test_action_rule_validation(client):
    h = as_admin(client)
    assert client.post("/api/actions", json={"name": "无规则", "conditions": []},
                       headers=h).status_code == 400
    assert client.post("/api/actions", json={
        "name": "非法关节", "conditions": [{"joint": "nope", "op": ">", "value": 1}]},
        headers=h).status_code == 400
    assert client.post("/api/actions", json={
        "name": "非法类型", "template_type": "weird", "conditions": []},
        headers=h).status_code == 400
    assert client.post("/api/actions", json={
        "name": "序列缺数据", "template_type": "sequence", "template_data": {}},
        headers=h).status_code == 400


def test_action_list_does_not_leak_template_vectors(client):
    """回归：列表接口不应返回完整骨骼序列（否则响应体积随模板帧数暴涨）。"""
    h = as_admin(client)
    vectors = [[float(i)] * 16 for i in range(40)]
    aid = client.post("/api/actions", json={
        "name": "序列动作", "template_type": "sequence",
        "template_data": {"vectors": vectors, "threshold": 5.0}}, headers=h).json()["id"]

    items = client.get("/api/actions", headers=h).json()["items"]
    row = next(a for a in items if a["id"] == aid)
    assert "template_data" not in row and "_template_data" not in row
    assert "vectors" not in str(row)
    assert row["template_frames"] == 40
    assert row["template_threshold"] == 5.0

    # 详情接口仍需返回完整模板（编辑表单依赖它）
    detail = client.get(f"/api/actions/{aid}", headers=h).json()
    assert len(detail["template_data"]["vectors"]) == 40


def test_action_seed_and_crud(client):
    h = as_admin(client)
    seeded = client.get("/api/actions", headers=h).json()["items"]
    assert len(seeded) >= 6, "种子动作应已写入"

    aid = client.post("/api/actions", json={
        "name": "自定义弯腰", "conditions": [{"joint": "trunk_inclination", "op": ">=", "value": 40}],
        "duration": 1.5}, headers=h).json()["id"]
    assert client.get(f"/api/actions/{aid}", headers=h).json()["duration"] == 1.5
    assert client.put(f"/api/actions/{aid}", json={
        "name": "自定义弯腰2",
        "conditions": [{"joint": "trunk_inclination", "op": ">=", "value": 40}]},
        headers=h).status_code == 200
    assert client.delete(f"/api/actions/{aid}", headers=h).status_code == 200


def test_action_fields_endpoint(client):
    items = client.get("/api/actions/fields", headers=as_admin(client)).json()["items"]
    keys = {f["key"] for f in items}
    assert {"trunk_inclination", "hands_distance"} <= keys


# ---------- 设备 ----------

def test_device_crud(client):
    h = as_admin(client)
    assert client.post("/api/devices", json={"name": "  "}, headers=h).status_code == 400
    did = client.post("/api/devices", json={
        "name": "2号摄像头", "type": "RTSP网络摄像头",
        "url": "rtsp://x/y", "location": "B栋"}, headers=h).json()["id"]
    items = client.get("/api/devices", headers=h).json()["items"]
    assert any(d["id"] == did for d in items)
    assert client.put(f"/api/devices/{did}", json={"name": "改名设备"},
                      headers=h).status_code == 200
    assert client.delete(f"/api/devices/{did}", headers=h).status_code == 200


# ---------- 班级 / 任务 / 消息 ----------

def _make_class_with_student(client):
    th = as_teacher(client)
    cid = client.post("/api/classes", json={"name": "护理2401", "description": "d"},
                      headers=th).json()["id"]
    r = client.post(f"/api/classes/{cid}/members", json={"usernames": ["nurse001"]},
                    headers=th).json()
    assert r["added"] == ["nurse001"] and r["errors"] == []
    return cid, th


def test_class_member_management(client):
    cid, th = _make_class_with_student(client)
    members = client.get(f"/api/classes/{cid}/members", headers=th).json()["items"]
    assert [m["username"] for m in members] == ["nurse001"]

    # 非学员账号不能加入班级
    r = client.post(f"/api/classes/{cid}/members", json={"usernames": ["teacher001"]},
                    headers=th).json()
    assert r["added"] == [] and r["errors"]

    # 其他教师无权查看该班级
    other = login(client, "caregiver001", "123456")  # 学员身份
    assert client.get(f"/api/classes/{cid}/members",
                      headers={"Authorization": "Bearer " + other["token"]}).status_code == 403


def test_task_flow_teacher_to_student(client):
    cid, th = _make_class_with_student(client)
    pid = client.get("/api/processes", headers=th).json()["items"][0]["id"]
    assert client.post("/api/tasks", json={
        "class_id": cid, "process_id": pid, "title": "练习翻身体位"}, headers=th).status_code == 400

    tid = client.post("/api/tasks", json={
        "class_id": cid, "process_id": pid, "title": "练习翻身体位",
        "deadline": int(time.time()) + 86400}, headers=th).json()["id"]

    mine = client.get("/api/tasks/mine", headers=as_student(client)).json()["items"]
    task = next(t for t in mine if t["id"] == tid)
    assert task["state"] == "pending" and task["latest_score"] is None
    assert task["process_name"]

    # 过期任务
    client.put(f"/api/tasks/{tid}", json={"deadline": int(time.time()) - 60}, headers=th)
    mine = client.get("/api/tasks/mine", headers=as_student(client)).json()["items"]
    assert next(t for t in mine if t["id"] == tid)["state"] == "overdue"


def test_task_visibility_is_scoped_to_owner(client):
    cid, th = _make_class_with_student(client)
    pid = client.get("/api/processes", headers=th).json()["items"][0]["id"]
    tid = client.post("/api/tasks", json={
        "class_id": cid, "process_id": pid, "title": "T",
        "deadline": int(time.time()) + 3600}, headers=th).json()["id"]
    # 学员不能删除任务
    assert client.delete(f"/api/tasks/{tid}", headers=as_student(client)).status_code == 403


def test_message_flow(client):
    cid, th = _make_class_with_student(client)
    stu = as_student(client)
    students = client.get(f"/api/classes/{cid}/members", headers=th).json()["items"]
    stu_id = students[0]["id"]

    # 学员不能主动发起对话
    assert client.post("/api/messages", json={"to_user_id": "user_x", "body": "hi"},
                       headers=stu).status_code == 404
    # 教师发消息
    assert client.post("/api/messages", json={
        "to_user_id": stu_id, "body": "请完成翻身练习"}, headers=th).status_code == 200
    unread = client.get("/api/messages/unread-count", headers=stu).json()
    assert unread["count"] == 1
    # 学员查看会话（读取后已读）
    convs = client.get("/api/messages/conversations", headers=stu).json()["items"]
    assert len(convs) == 1
    thread = client.get(f"/api/messages/thread/{convs[0]['id']}", headers=stu).json()
    assert thread["items"][0]["body"] == "请完成翻身练习"
    assert client.get("/api/messages/unread-count", headers=stu).json()["count"] == 0
    # 学员回复教师
    te = client.get("/api/auth/me", headers=th).json()["user"]
    assert client.post("/api/messages", json={"to_user_id": te["id"], "body": "好的"},
                       headers=stu).status_code == 200


def test_teacher_private_note_hidden_from_student(client):
    cid, th = _make_class_with_student(client)
    stu = as_student(client)
    stu_id = client.get(f"/api/classes/{cid}/members", headers=th).json()["items"][0]["id"]
    client.post("/api/messages", json={"to_user_id": stu_id, "body": "公开消息"}, headers=th)
    client.post("/api/messages", json={"to_user_id": stu_id, "body": "内部备注",
                                       "kind": "note"}, headers=th)
    tid = client.get("/api/messages/conversations", headers=stu).json()["items"][0]["id"]
    bodies = [m["body"] for m in
              client.get(f"/api/messages/thread/{tid}", headers=stu).json()["items"]]
    assert "公开消息" in bodies
    assert "内部备注" not in bodies, "私密备注不应被学员看到"


def test_message_length_limit(client):
    cid, th = _make_class_with_student(client)
    stu_id = client.get(f"/api/classes/{cid}/members", headers=th).json()["items"][0]["id"]
    assert client.post("/api/messages", json={"to_user_id": stu_id, "body": "x" * 1001},
                       headers=th).status_code == 400


# ---------- 监控画面访问控制 ----------

def test_monitor_session_frame_requires_authorization(client):
    """回归：/frame 原先完全无鉴权，任何人拿到 session id 就能看摄像头画面。"""
    stu = as_student(client, "nurse001")
    other = as_student(client, "caregiver001")
    pid = client.get("/api/processes", headers=stu).json()["items"][0]["id"]
    r = client.post("/api/monitoring/sessions",
                    json={"source": "0", "process_id": pid}, headers=stu)
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    try:
        # 无令牌 → 401
        assert client.get(f"/api/monitoring/sessions/{sid}/frame").status_code == 401
        # 其他学员 → 403
        tok = other["Authorization"].split(" ")[1]
        assert client.get(
            f"/api/monitoring/sessions/{sid}/frame?token={tok}").status_code == 403
        # 本人 → 鉴权通过（没有 opencv 时无画面帧，返回 404 而不是 401/403）
        tok = stu["Authorization"].split(" ")[1]
        assert client.get(
            f"/api/monitoring/sessions/{sid}/frame?token={tok}").status_code == 404
        # 管理员可查看任意会话
        tok = as_admin(client)["Authorization"].split(" ")[1]
        assert client.get(
            f"/api/monitoring/sessions/{sid}/frame?token={tok}").status_code == 404
        # 不存在的会话
        assert client.get(
            f"/api/monitoring/sessions/sess_nope/frame?token={tok}").status_code == 404
    finally:
        client.delete(f"/api/monitoring/sessions/{sid}", headers=stu)


def test_monitor_session_requires_valid_process(client):
    h = as_student(client)
    assert client.post("/api/monitoring/sessions",
                       json={"process_id": "proc_nope"}, headers=h).status_code == 400
