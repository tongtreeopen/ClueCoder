from examples.local_state_demo import run_demo


def test_offline_local_state_demo_exercises_skill_memory_and_task(tmp_path):
    result = run_demo(tmp_path)
    assert "review: 审查代码" in result["skills"]
    assert "format: 格式" in result["memory"]
    assert result["task"] == "completed"
