# 后台任务实现与验证（2026-10-02）

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 cluecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

已实现严格 CLI 会话内的后台 Docker 命令：`bash(command, timeout=120, run_in_background=true)` 返回任务 ID，`background_status(task_id)` 查询状态/退出码/部分输出，`background_cancel(task_id)` 终止并验证清理。同步 Bash 默认保持原接口。

## 实现范围

- 前后台共享 Docker 执行句柄；先 create 再 start --attach，取消时已有固定容器身份。
- 保持无网络、无新增权限、固定镜像及资源边界；后台容器内 watchdog 设置期限并清理子进程组。
- 同时最多 4 个任务，默认 120 秒、最大 300 秒、输出上限 1 MB；读输出不消费任务结果。
- 父子 Agent 共享会话管理器；子返回不结束任务。CLI 退出、reset、进入 Plan Mode 前清理活跃任务，清理不可信时拒绝模式转换。
- 主线程保存后台启动/完成 note 事件，查询结果提供真实完成事件 ID；日志落盘失败时保留待写结果以便重试。
- 工作卡与 Goal 检查真实启动回执和结构化终态。未结束、无终态、清理失败及受干扰的结果不能验收，用户确认也不覆盖运行中/未清理状态。

入口见 [Docker 执行器](../../cluecoder/execution.py)、[会话管理器](../../cluecoder/background.py)、[工具](../../cluecoder/tools/background.py)、[工作卡证据门](../../cluecoder/work_state.py)。与教学 s11 的保留、新增及边界见 [机制对照](../learn-claude-code-mechanism-comparison.md)。

## 已执行检查及快照边界

环境为 Windows / Python 3.13.5、真实 Linux Docker Engine、本地固定 digest 镜像；没有安装新依赖，没有调用真实模型。

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| 初始后台接口测试 | 9 failed | 实现前 start 接口缺失，确认 RED |
| Docker/后台中间回归 | 24 passed | 真实容器及原 Docker 测试 |
| 全量 `py -3.13 -m pytest -v -o faulthandler_timeout=30` | 494 passed，471.31 秒 | 最终退出码 124 兼容修复和重复 ID 修复之前的完整工作树 |
| 最终定向回归 | 96 passed，70.19 秒 | 两项最终修复之后，后台、Docker、Goal、工作卡、权限、严格 CLI 全部相关测试 |
| `py -3.13 -m ruff check .` | 通过 | 最终工作树 |
| `git diff --check` | 通过 | 最终工作树；Git 的 LF/CRLF 提示不是差异错误 |

最终定向命令：

```powershell
py -3.13 -m pytest tests/test_background_tasks.py tests/test_docker_execution.py tests/test_goal_loop.py tests/test_work_state.py tests/test_permissions.py tests/test_cli_workspace.py -q
```

原始输出保存在 全量回归（归档材料未收录于此副本） 和 最终定向回归（归档材料未收录于此副本）。完整回归中的 30 秒线程栈诊断对应原有较慢的 Docker Gate 检查，它们最终通过。第一次安静输出的全量尝试为定位耗时而停止，不计入通过结果。

## 真实容器验证

覆盖启动即返回、无换行的部分输出、任务运行时执行另一命令、真实退出码、取消包含子进程的命令后容器消失、重复取消、超时、1 MB 输出溢出、并发上限与替换任务、父子 Agent 共享所有权及 reset 清理。

另用独立 Python 进程启动后台任务后直接 `os._exit(0)`，查询容器最终退出码 124，验证容器内期限不依赖 CLI 线程存活；测试最后显式删除停止的容器。

脚本模型及真实 SessionJournal 另覆盖完成事件只写一次、工具回复配对不变、不插入假用户消息、落盘失败重试、失败/取消/查询结果不能成为成功证据、重启缺终态记录与用户确认的保守边界。

## 审查后的修复

三个具体问题均先观察回归失败，再修复并验证通过：

1. 前台检查与后台命令重叠时，后台稍后可能修改工作区。验收现在检查后台执行区间，拒绝这种前台证据。
2. 后台检查已落盘后子 Agent 可能继续改文件。子 Agent 委派现在使旧证据失效；运行中的检查也被标记为受干扰。
3. 后续 Bash 复用旧工具调用 ID 可绕过失效检查。豁免现在绑定具体 assistant 调用事件序号及 ID，不能仅靠重复字符串 ID。

此外前台命令主动退出 124 保留 `[exit code: 124]`，不再被后台 watchdog 状态识别误报成超时。独立只读审查复核了新鲜证据通过、三个失效路径拒绝；最终复核未发现新增阻塞问题。

## 限制

任务只在当前 CLI 进程有效，无重启续跑、定时调度或自动唤醒；一次性 `-p` 退出会清理活跃任务。强杀 CLI 后命令有容器内期限，但可能留下已停止的容器；异常断电清理未验证。输出和终态留在内存中直到 reset，长会话可能需要后续归档策略。

子任务共享查询和取消能力，但验收必须能对应本会话真实启动消息；子 Agent 摘要不能代替主 Agent 检查。没有验证真实模型的后台工具使用质量、解题成功率或成本改善。

当前分支原本已有其他未提交改动；本次未重置、打包或提交这些内容，功能改动保留在工作区。完整回归覆盖了该已有工作树，不代表干净提交的单独基准。
