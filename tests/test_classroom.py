"""P2 班级/任务纯逻辑自检（无需 MySQL）"""
from backend.routers.tasks import can_manage, task_state_for


def test_can_manage():
    admin = {"role": "admin", "id": "u1"}
    teacher = {"role": "elderly_service_teacher", "id": "u2"}
    other_teacher = {"role": "elderly_service_teacher", "id": "u3"}
    student = {"role": "nursing_student", "id": "u4"}
    assert can_manage(admin, "anyone") is True
    assert can_manage(teacher, "u2") is True
    assert can_manage(teacher, "u3") is False
    assert can_manage(student, "u2") is False


def test_task_state():
    now = 1_700_000_000
    assert task_state_for(now + 100, False, now) == "pending"
    assert task_state_for(now - 100, False, now) == "overdue"
    assert task_state_for(now - 100, True, now) == "done"


if __name__ == "__main__":
    test_can_manage()
    test_task_state()
    print("OK: classroom logic checks passed")
