"""步骤序列匹配：将识别到的动作序列与标准护理流程比对，检测漏步。

核心算法：最长公共子序列(LCS)确定「已按顺序完成」的步骤，
其余应完成但未按序出现的步骤即为「漏步」。
"""


def match_sequence(step_actions, detected_actions):
    """step_actions: 流程步骤对应的 action_id 列表（顺序）
    detected_actions: 实际识别到的 action_id 序列（可含重复，按时间排序）

    返回 dict:
      completed_steps: 已按顺序完成的步骤索引列表
      missed_steps:    漏掉/未按序完成的步骤索引列表
      current_step:    当前应执行的下一步索引（-1 表示已完成）
      score:           0~100 完成度得分
    """
    n, m = len(step_actions), len(detected_actions)
    # LCS 动态规划
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if step_actions[i - 1] == detected_actions[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    # 回溯得到 step 索引（在 LCS 中被匹配的步骤）
    matched = []
    i, j = n, m
    while i > 0 and j > 0:
        if step_actions[i - 1] == detected_actions[j - 1]:
            matched.append(i - 1)
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    matched = matched[::-1]

    completed = set(matched)
    missed = [i for i in range(n) if i not in completed]

    # 当前步骤 = 第一个未完成的步骤
    current_step = missed[0] if missed else -1

    score = round(len(completed) / n * 100, 1) if n else 100.0
    return {
        "completed_steps": sorted(completed),
        "missed_steps": missed,
        "current_step": current_step,
        "score": score,
    }
