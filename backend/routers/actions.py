"""动作模板接口：支持自主添加/编辑可识别动作内容

模板类型（template_type）：
  - rule     角度规则：基于关节特征 + 阈值 + 持续时间（静态姿态判定）
  - sequence 骨骼序列：录制一段动作的关节特征时间序列，用 DTW 匹配（过程性动作）
"""
from fastapi import APIRouter, Depends, HTTPException

from backend import auth
from backend import database as db
from .auth_router import current_user
from .tasks import TEACHER_ROLES

router = APIRouter(prefix="/api/actions", tags=["actions"])


def _require_manager(user):
    """动作模板属于培训标准，只有教师/管理员可维护；所有登录用户可读取。"""
    if user["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="仅教师或管理员可维护动作模板")

# 可供规则引用的关节特征说明
JOINT_FIELDS = [
    # trunk_inclination 是「躯干与竖直轴的无符号夹角」：前倾与后仰数值相同，
    # 不区分方向；>90 表示头低于髋。见 backend/vision/pose_engine.py 的同名注释。
    {"key": "trunk_inclination", "label": "躯干偏离直立角(度)",
     "note": "0=直立，90=躯干水平，越大偏离直立越多（不区分前倾/后仰）"},
    {"key": "trunk_lateral_lean", "label": "躯干侧倾角(度)", "note": "正值=右倾"},
    {"key": "left_elbow_angle", "label": "左肘角度(度)", "note": "180=伸直, 90=弯曲"},
    {"key": "right_elbow_angle", "label": "右肘角度(度)", "note": "180=伸直, 90=弯曲"},
    {"key": "left_knee_angle", "label": "左膝角度(度)", "note": "180=伸直, 90=弯曲"},
    {"key": "right_knee_angle", "label": "右膝角度(度)", "note": "180=伸直, 90=弯曲"},
    {"key": "left_hip_angle", "label": "左髋角度(度)", "note": "坐/蹲时变小"},
    {"key": "right_hip_angle", "label": "右髋角度(度)", "note": "坐/蹲时变小"},
    {"key": "left_shoulder_angle", "label": "左肩角度(度)", "note": "抬臂时变化"},
    {"key": "right_shoulder_angle", "label": "右肩角度(度)", "note": "抬臂时变化"},
    {"key": "hand_height_left", "label": "左手相对肩高", "note": "负=高于肩"},
    {"key": "hand_height_right", "label": "右手相对肩高", "note": "负=高于肩"},
    {"key": "hands_distance", "label": "双手距离(肩宽比)", "note": "<0.6=双手靠近"},
    {"key": "body_height", "label": "身体高度(肩宽比)", "note": "蹲下时变小"},
    {"key": "hip_center_height", "label": "髋部高度(肩宽比)", "note": "站高坐低"},
]

_TEMPLATE_TYPES = {"rule", "sequence"}


def _summarize(row):
    """把 template_data 摘要化，避免列表接口返回过大的向量数据。

    只保留帧数与阈值等标量摘要；序列模板的完整 vectors 仅在
    单条详情接口（GET /api/actions/{aid}）返回，防止列表响应体积失控。
    """
    td = db.json_load(row.get("template_data"), {})
    if isinstance(td, dict):
        row["template_frames"] = len(td.get("vectors") or [])
        row["template_threshold"] = td.get("threshold", 8.0)
    else:
        row["template_frames"] = 0
        row["template_threshold"] = 8.0
    # 列表不返回完整模板数据（可能含上千帧 × 16 维向量）
    row.pop("template_data", None)
    return row


def _validate(payload):
    """校验并返回 (template_type, conditions, template_data)。"""
    ttype = payload.get("template_type") or "rule"
    if ttype not in _TEMPLATE_TYPES:
        raise HTTPException(status_code=400, detail=f"非法模板类型: {ttype}")
    conditions = payload.get("conditions") or []
    template_data = payload.get("template_data") or {}

    if ttype == "rule":
        if not conditions:
            raise HTTPException(status_code=400, detail="规则类型至少需要一条判定条件")
        valid_keys = {f["key"] for f in JOINT_FIELDS}
        for c in conditions:
            if c.get("joint") not in valid_keys:
                raise HTTPException(status_code=400, detail=f"未知关节特征: {c.get('joint')}")
    else:  # sequence
        vectors = template_data.get("vectors") if isinstance(template_data, dict) else None
        if not vectors or not isinstance(vectors, list) or len(vectors) < 2:
            raise HTTPException(status_code=400, detail="序列类型需要有效的骨骼序列数据(vectors)")
        template_data.setdefault("threshold", 8.0)
        template_data.setdefault("frames", len(vectors))
    return ttype, conditions, template_data


@router.get("/fields")
def list_fields(user=Depends(current_user)):
    return {"items": JOINT_FIELDS}


@router.get("/{aid}")
def get_action(aid: str, user=Depends(current_user)):
    row = db.query_one("SELECT * FROM actions WHERE id = ?", (aid,))
    if not row:
        raise HTTPException(status_code=404, detail="动作不存在")
    row["conditions"] = db.json_load(row.get("conditions"))
    row["template_data"] = db.json_load(row.get("template_data"), {})
    return row


@router.get("")
def list_actions(user=Depends(current_user)):
    rows = db.query("SELECT id, name, category, description, conditions, duration, "
                    "sample_ref, template_type, template_data, created_by, created_at "
                    "FROM actions ORDER BY created_at DESC")
    for r in rows:
        r["conditions"] = db.json_load(r.get("conditions"))
        _summarize(r)
    return {"items": rows}


@router.post("")
def create_action(payload: dict, user=Depends(current_user)):
    _require_manager(user)
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="动作名称必填")
    ttype, conditions, template_data = _validate(payload)
    aid = db.gen_id("act")
    db.execute(
        "INSERT INTO actions (id, name, category, description, conditions, duration, sample_ref, template_type, template_data, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (aid, name, payload.get("category") or "通用", payload.get("description") or "",
         db.json_dump(conditions), float(payload.get("duration") or 1.0),
         payload.get("sample_ref") or "", ttype, db.json_dump(template_data),
         user["id"], db.now()),
    )
    auth.audit(user, "create_action", f"新增识别动作 {name}({ttype})")
    return {"id": aid}


@router.put("/{aid}")
def update_action(aid: str, payload: dict, user=Depends(current_user)):
    _require_manager(user)
    row = db.query_one("SELECT * FROM actions WHERE id = ?", (aid,))
    if not row:
        raise HTTPException(status_code=404, detail="动作不存在")
    ttype, conditions, template_data = _validate(payload)
    db.execute(
        "UPDATE actions SET name=?, category=?, description=?, conditions=?, duration=?, sample_ref=?, template_type=?, template_data=? WHERE id=?",
        (payload.get("name") or row["name"], payload.get("category") or row["category"],
         payload.get("description") or row["description"],
         db.json_dump(conditions),
         float(payload.get("duration", row["duration"])),
         payload.get("sample_ref") or row["sample_ref"],
         ttype, db.json_dump(template_data), aid),
    )
    auth.audit(user, "update_action", f"更新识别动作 {row['name']}")
    return {"ok": True}


@router.delete("/{aid}")
def delete_action(aid: str, user=Depends(current_user)):
    _require_manager(user)
    db.execute("DELETE FROM actions WHERE id = ?", (aid,))
    auth.audit(user, "delete_action", f"删除识别动作 {aid}")
    return {"ok": True}
