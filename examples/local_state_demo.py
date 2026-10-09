"""Offline demonstration of project Skill, Memory, and Task state."""

import json
import tempfile
from pathlib import Path

from tracecoder.memory import MemoryStore
from tracecoder.skills import SkillCatalog
from tracecoder.tasks import TaskStore


def run_demo(workspace: Path) -> dict[str, str]:
    manifest = workspace / ".tracecoder" / "skills" / "review" / "SKILL.md"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("---\nname: review\ndescription: 审查代码\n---\n检查错误处理", encoding="utf-8")
    skill = SkillCatalog(workspace, user_root=workspace / "user-skills")
    memory = MemoryStore(workspace)
    tasks = TaskStore(workspace)
    result = memory.remember("format", "格式", "输出使用中文")
    if not result.startswith("Saved"):
        raise RuntimeError(result)
    task = tasks.create("检查示例")
    tasks.claim(task.id)
    tasks.complete(task.id)
    return {"skills": skill.catalog(), "memory": memory.catalog(),
            "task": TaskStore(workspace).get(task.id).status}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        print(json.dumps(run_demo(Path(directory)), ensure_ascii=False))
