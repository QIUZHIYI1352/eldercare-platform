"""P1 对比算法自检：分类 + 分段评分（无需摄像头/MySQL）"""
from backend.vision.assessment import classify_steps, segment_score


def ev(action_id, start_idx=0, end_idx=9):
    return {"action_id": action_id, "start_idx": start_idx, "end_idx": end_idx}


def results(step_actions, events):
    return [r["result"] for r in classify_steps(step_actions, events)]


def test_correct_order():
    assert results(["A", "B", "C"], [ev("A", 0, 9), ev("B", 10, 19), ev("C", 20, 29)]) \
        == ["matched", "matched", "matched"]


def test_missing_step():
    assert results(["A", "B", "C"], [ev("A"), ev("B")]) \
        == ["matched", "matched", "missed"]


def test_out_of_order():
    # 标准 A,B,C；实际 B,A,C：C 顺序正确，B 先于 A 执行 → B 判顺序错误
    assert results(["A", "B", "C"], [ev("B", 0, 9), ev("A", 10, 19), ev("C", 20, 29)]) \
        == ["matched", "order_error", "matched"]


def test_duplicate_step_missing_occurrence():
    # 标准 A,B,A；只做了一次 A + B：第二次 A 应判遗漏，不是顺序错误
    assert results(["A", "B", "A"], [ev("A", 0, 9), ev("B", 10, 19)]) \
        == ["matched", "matched", "missed"]


def test_duplicate_step_out_of_order():
    # 标准 A,B,A；实际 A,A,B：第二个 A 出现在 B 前 → 第三步 A 判顺序错误
    assert results(["A", "B", "A"], [ev("A", 0, 9), ev("A", 10, 19), ev("B", 20, 29)]) \
        == ["matched", "matched", "order_error"]


def test_segment_score_same_and_far():
    template = [[0.0] * 4, [1.0] * 4, [2.0] * 4, [3.0] * 4]
    same = segment_score(template, template, threshold=8.0)
    assert same is not None and same >= 99.0
    far = segment_score([[50.0] * 4] * 4, template, threshold=8.0)
    assert far == 0.0


if __name__ == "__main__":
    test_correct_order()
    test_missing_step()
    test_out_of_order()
    test_duplicate_step_missing_occurrence()
    test_duplicate_step_out_of_order()
    test_segment_score_same_and_far()
    print("OK: assessment logic checks passed")
