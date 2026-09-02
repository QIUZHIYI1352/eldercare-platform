"""初始化种子数据：默认账号、标准护理流程、可识别动作模板"""
from backend import database as db
from backend import auth


def seed():
    # ---- 默认用户 ----
    if not db.query_one("SELECT id FROM users WHERE username = 'admin'"):
        db.execute(
            "INSERT INTO users (id, username, password_hash, name, role, organization, phone, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (db.gen_id("user"), "admin", auth.hash_password("admin123"),
             "系统管理员", "admin", "智慧养老管理中心", "13800000000", db.now()),
        )
    sample_users = [
        ("nurse001", "护理大学生", "nursing_student", "某某护理学院", "13900000001"),
        ("caregiver001", "长期护理员", "long_term_caregiver", "社区居家养老中心", "13900000002"),
        ("elderly001", "养老护理员", "elderly_caregiver", "养老服务机构", "13900000003"),
        ("teacher001", "养老服务师", "elderly_service_teacher", "养老服务培训中心", "13900000004"),
    ]
    for uname, name, role, org, phone in sample_users:
        if not db.query_one("SELECT id FROM users WHERE username = ?", (uname,)):
            db.execute(
                "INSERT INTO users (id, username, password_hash, name, role, organization, phone, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (db.gen_id("user"), uname, auth.hash_password("123456"),
                 name, role, org, phone, db.now()),
            )

    # ---- 动作模板（仅当动作表为空时初始化，避免覆盖用户自定义）----
    if db.query_one("SELECT COUNT(*) AS c FROM actions")["c"] == 0:
        action_defs = [
            ("站立准备", "通用", "身体直立，躯干接近垂直",
             [{"joint": "trunk_inclination", "op": "<=", "value": 20}], 0.5),
            ("弯腰操作", "通用", "躯干前倾，靠近老人/床面",
             [{"joint": "trunk_inclination", "op": ">=", "value": 30}], 0.5),
            ("深弯腰低位操作", "通用", "大幅前倾，进行低位护理操作",
             [{"joint": "trunk_inclination", "op": ">=", "value": 55}], 0.5),
            ("屈膝下蹲", "通用", "双膝弯曲下蹲调整重心",
             [{"joint": "left_knee_angle", "op": "<=", "value": 110},
              {"joint": "right_knee_angle", "op": "<=", "value": 110}], 0.5),
            ("双臂抬起", "通用", "双臂抬高过肩，扶持或操作",
             [{"joint": "hand_height_left", "op": "<=", "value": -0.1},
              {"joint": "hand_height_right", "op": "<=", "value": -0.1}], 0.5),
            ("双手靠近", "通用", "双手靠近胸前，进行搓洗/扶持/固定",
             [{"joint": "hands_distance", "op": "<=", "value": 0.55}], 0.5),
        ]
        ids = {}
        for name, cat, desc, conds, dur in action_defs:
            aid = db.gen_id("act")
            ids[name] = aid
            db.execute(
                "INSERT INTO actions (id, name, category, description, conditions, duration, sample_ref, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (aid, name, cat, desc, db.json_dump(conds), dur, "", "system", db.now()),
            )

        # ---- 标准护理流程（步骤 action 引用上面动作）----
        process_defs = [
            ("协助卧床老人翻身", "生活照料",
             "协助长期卧床老人由仰卧翻转为侧卧，防止压疮",
             [("洗手准备（双手搓洗）", "双手靠近"),
              ("弯腰靠近老人", "弯腰操作"),
              ("屈膝调整重心", "屈膝下蹲"),
              ("双臂抬起扶持老人", "双臂抬起"),
              ("双手靠近固定体位", "双手靠近"),
              ("深弯腰整理背部垫枕", "深弯腰低位操作")]),
            ("协助老人进食", "生活照料",
             "协助行动不便老人安全进食",
             [("洗手准备（双手搓洗）", "双手靠近"),
              ("弯腰调整床头角度", "弯腰操作"),
              ("站立观察进食状态", "站立准备"),
              ("双手靠近协助持碗", "双手靠近")]),
            ("口腔护理", "基础护理",
             "为卧床老人进行口腔清洁",
             [("双手靠近准备器械", "双手靠近"),
              ("弯腰靠近老人", "弯腰操作"),
              ("屈膝下蹲操作", "屈膝下蹲"),
              ("双臂抬起进行擦拭", "双臂抬起")]),
            ("床-轮椅转移", "康复护理",
             "协助老人从床安全转移至轮椅",
             [("站立准备", "站立准备"),
              ("弯腰扶持老人", "弯腰操作"),
              ("屈膝下蹲发力", "屈膝下蹲"),
              ("双臂抬起环抱", "双臂抬起"),
              ("双手靠近固定", "双手靠近")]),
        ]
        for pname, cat, desc, steps in process_defs:
            pid = db.gen_id("proc")
            step_list = []
            for order, (sname, aname) in enumerate(steps, 1):
                step_list.append({"order": order, "name": sname, "action_id": ids[aname]})
            db.execute(
                "INSERT INTO processes (id, name, category, description, steps, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
                (pid, pname, cat, desc, db.json_dump(step_list), "system", db.now()),
            )

    # ---- 示例设备 ----
    if db.query_one("SELECT COUNT(*) AS c FROM devices")["c"] == 0:
        db.execute(
            "INSERT INTO devices (id, name, type, url, location, status, privacy_mask, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (db.gen_id("dev"), "1号护理床摄像头", "USB摄像头", "0", "A栋101护理间", "offline", 1, db.now()),
        )

    print("种子数据初始化完成")
    print("默认管理员: admin / admin123")
    print("演示账号: nurse001 / caregiver001 / elderly001 / teacher001  (密码 123456)")
