# Skill、Memory、Task 使用说明

## 本地 Skill

将项目规则写到 `.cluecoder/skills/review/SKILL.md`：

```yaml
---
name: review
description: 审查项目代码
---
请检查错误处理和测试覆盖。
```

也可写到用户级 `~/.cluecoder/skills/review/SKILL.md`。同名时项目级优先。启动 ClueCoder 后，模型只能先看到 Skill 的名称和简介；调用 `load_skill(name="review")` 才能读取全文。Skill 文件变更后需重启 ClueCoder。单个文件不得超过 64 KiB。

无需模型服务的验证命令：

```powershell
py -3.13 -m pytest tests/test_skills.py -q
```

## 会话、工作卡与长期 Memory

CLI 会自动把会话事件写入 `~/.cluecoder/sessions/<ID>/events.jsonl`，`/save` 显示当前 ID，`/sessions` 列出可恢复会话，`cluecoder --resume <ID>` 继续。旧的单文件会话会导入新日志，但已被旧版压缩掉的原文无法还原。`/reset` 开新会话，旧会话仍可恢复；跨工作区恢复须在交互模式明确确认。日志中的常见密钥形态会脱敏，过大的事件会截断并注明，不能把日志当作完整的秘密保险箱。

每个有用户请求的 CLI 会话有一张工作卡：目标、步骤、Todo、下一步、阻碍、验收条件和证据引用。`/work status` 查看；`/work confirm` 表示你明确验收并归档；`/work reopen` 重新打开。模型可用 `work_update` 更新工作卡，但 Todo 全 done、模型说“完成”、退出 CLI 均不会自动关闭目标。机器检查条件须登记 Bash `command` 和成功结果的 `event_id`，两者与实际工具调用一致才可自动通过；这验证的是命令成功执行，不证明测试覆盖了全部需求。没有足够证据时进入 `awaiting_review`。项目级 `TaskStore` 仍是独立的任务清单，不等于工作卡。

默认提示采用渐进式披露：只展示活动卡摘要、最近消息、旧会话事件导航，以及 Skill/记忆索引。模型需要细节时可先 `search_session`、`search_memory`，再按 ID `read_session_event` 或用 `read_memory(name="...", scope="project|user")` 读取正文；`read_work_state` 可查看完整工作卡。旧会话和记忆均是低信任资料，不能覆盖当前用户指令或权限。

长期记忆分项目级 `.cluecoder/memory/` 和用户级 `~/.cluecoder/memory/`。回合结束后先登记待提取区间：交互 CLI 空闲约 15 秒时在本进程处理，`-p` 退出前有界处理一次；若进程退出或失败，作业留待下次启动或 `/memory process`，没有常驻后台服务。无长期价值可以返回零候选。只有有直接用户原文支撑、无冲突且未命中敏感规则的新候选自动保存；推断、冲突或疑似敏感候选进入集中审阅，不会每轮弹窗。常见凭据会直接拒绝，但识别不可能完整。

`/memory status` 看待处理数；`/memory review` 列出待审 ID，`/memory review <ID>` 查看详情，再用 `/memory accept <ID>` 或 `/memory reject <ID>` 处理。`/memory undo` 撤销最近一次受控项目/用户记忆写入，遇后续改动会拒绝覆盖。`/memory auto off` 使新候选也进入审阅队列，`/memory auto on` 恢复安全候选自动保存；`/memory extraction off|on`、`/memory reading off|on` 分别控制提取和读取，均只作用于当前 CLI 进程。`--memory-auto` 是兼容旧脚本的显式开启别名；安全候选现已默认自动保存，`--yes` 仍只影响工具权限。Plan Mode 不处理长期记忆写入。

显式 `remember` 工具仍要求原有权限，同名默认不覆盖；它不进入自动提取的撤销栈。`/memory consolidate` 仍需单独确认增删改提案。辅助提取会消耗 token；2026-09-26 仅在隔离目录做了少量真实模型冒烟，未评估候选准确率或污染率。辅助模型若输出为空会留作业供重试，不影响主回答；提取上限为 4,096 输出 token，成本可能高于早期版本。

离线验证：

```powershell
py -3.13 -m pytest tests/test_session_journal.py tests/test_work_state.py tests/test_context_assembler.py tests/test_memory_scope.py tests/test_extraction_queue.py -q
```

## 持久化 Task

用 `create_task` 创建项目任务，记录返回的 `task_...` ID；用 `update_task` 的 `blocked_by` 列表添加前置任务。只有所有前置任务完成后才能调用 `claim_task`，随后由同一稳定 owner `cluecoder` 调用 `complete_task`。`list_tasks` 和 `get_task` 在 Plan Mode 下也可读取；创建、更新、认领和完成均需权限确认，在 Plan Mode 下被拒绝。

Task 保存于启动工作区的 `.cluecoder/tasks/`，重启后仍可读取。CLI 的 `todo_write` 清单现在会随工作卡持久化并在恢复会话时读回，但仍不等于项目 Task 或目标验收。TaskStore 使用跨进程 StateLock 串行化状态写入；这不等于多 Agent 的自动调度或团队协作系统。

无需 API Key 的三项功能演示与测试：

```powershell
py -3.13 -m examples.local_state_demo
py -3.13 -m pytest tests/test_skills.py tests/test_memory.py tests/test_tasks.py tests/test_local_state.py -q
```
