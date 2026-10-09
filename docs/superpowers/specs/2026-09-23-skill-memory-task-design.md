# TraceCoder 的 Skill、Memory 与 Task 功能设计

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 tracecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

## 后续规格与当前契约（2026-10-05）

下文保留 2026-09-23 第一批交付的范围，不作为整条后续分支的唯一规格。Memory v2、渐进式记忆、Goal Loop、独立评估和后台任务分别由同目录的 2026-09-25、2026-09-26、2026-09-29 和 2026-10-02 规格扩展。

当前非 eval CLI 默认启用延迟记忆提取：有原文支撑且通过筛查的新候选可自动保存，冲突与推断进入审阅；合并仍需确认。Plan Mode 暂停提取并拒绝持久化 work_update；todo_write 在该模式只改临时清单。eval 模式关闭自动提取，主模型、子 Agent 与 Goal evaluator 共用模型预算。此契约替代下文第一批“仅 remember 保存”和“不包含自动提取”的范围限定，并对应现有使用说明及 Memory v2 实现，不新增自动保存权限。

默认 Python Agent 与 ALL_TOOLS 也采用启动工作区边界和禁用的宿主 shell；显式传入 BashTool 属于调用方选择的宿主执行。Skill 配置根目录整体不得经符号链接重定向，根目录内的候选文件链接只要解析后仍在根内即可加载。Memory 事务用跨进程锁串行化并在替换前 fsync；未写入 committed 标记的事务在重启时回滚，这属于未提交事务恢复，不保证该事务已提交。

代码评审修复与验证记录见 2026-10-05 修复记录（归档材料未收录于此副本）。

## 目标与范围

第一批从 `learn-claude-code` 引入三项能力：按需加载 Skill（s07）、跨会话复用项目 Memory（s09），以及持久化、支持依赖关系的 Task（s10）。TraceCoder 继续保持小型、单进程 Coding Agent 的定位。现有 `Agent.chat()` 工具循环、`todo_write` 会话清单、权限判断和 Plan Mode 保持原有职责。

按 Skill → Memory → Task 的顺序交付；每一步完成后都能独立使用和测试。本批次不包含自动提取记忆、后台任务、常驻队友、工作流编排或自进化评估器。基础存储和工具验证后，再分别设计这些能力。

## 接入位置

`cli.main()` 负责组装工具列表和 `Agent`。每个工具通过 `Tool.name`、`Tool.parameters` 和 `execute()` 接入；`Agent._full_messages()` 组装系统提示词；`Agent._permit()` 执行 Plan Mode 和 `Permission` 判断。新增工具沿用这些接口。CLI 启动时确定工作区根目录并传给各存储模块，之后执行 `bash cd` 不会改变持久化数据的位置。

```text
CLI（工作区）
  ├─ SkillCatalog ── load_skill 工具 ── 工具结果
  ├─ MemoryStore ── 列出／读取／保存工具
  └─ TaskStore ── 创建／更新／列出／读取／认领／完成工具
         ↓
Agent 工具注册 → PreHook → Plan Mode → Permission → 执行 → PostHook
```

给 `Agent` 增加一个可选的 `context_catalog: Callable[[], str]`，在组装系统消息时调用。这样，新保存的 Memory 能在下一次模型请求中出现，而 `Agent` 无须直接读取文件。子 Agent 复用相同的工具实例、目录回调和权限策略。文件命名、序列化与校验留在各存储模块内部。

## Skill 加载

CLI 启动时扫描项目级 `WORKSPACE/.tracecoder/skills/*/SKILL.md` 和用户级 `~/.tracecoder/skills/*/SKILL.md`。同名时项目级优先。系统提示词只展示名称和单行简介；模型调用 `load_skill(name)` 后，工具结果才返回完整的 UTF-8 文件内容。未知名称只返回可用名称，不把参数解释为文件路径。

扫描时解析候选文件的真实路径，拒绝指向配置根目录之外的符号链接。无效或无法读取的文件会产生可见警告，但不妨碍 CLI 启动。Frontmatter 读取 `name` 和 `description`；缺少字段时分别使用目录名和正文首行。使用 PyYAML 解析标准 YAML 标量，并把它加入运行依赖。超过 64 KiB 的 Skill 文件会跳过，避免一次加载占用过多上下文。`load_skill` 属于只读工具，在 Plan Mode 下也可使用。

Skill 目录在启动时生成快照。新增或修改文件后，重新启动 TraceCoder 才会更新目录；本批次不做运行中热加载。

## 项目 Memory

`MemoryStore` 将记录存于 `WORKSPACE/.tracecoder/memory/`。每条记录包含经过校验的稳定名称、简短描述和 UTF-8 正文；写入后重建索引。系统提示词只加入简短索引，并说明历史记忆仅供参考，不能覆盖当前用户指令。`list_memories` 返回索引，`read_memory(name)` 返回指定记录全文。`remember(name, description, content, replace=False)` 创建记录；只有显式传入 `replace=True` 才覆盖已有记录。空字段、类似路径的名称、超过 16 KiB 的正文和越界符号链接均被拒绝。

保存 Memory 属于可变更操作，需经过 `Permission` 确认，并在 Plan Mode 下拒绝；列出和读取为只读操作。本批次只在用户或 Agent 明确调用 `remember` 时保存，不从普通对话自动推断长期记忆。这让持久化行为可见，也便于离线验证。自动提取、相关性排序、合并去重和删除功能留待基础存储及用户控制验证后设计。

Memory 文件属于本地运行状态，默认加入 Git 忽略规则。项目级 Skill 目录不忽略，团队可自行将 Skill 定义纳入版本管理。

## 持久化 Task

`TaskStore` 在 `WORKSPACE/.tracecoder/tasks/` 下为每个任务保存一个 JSON 文件。任务字段为 `id`、`subject`、`description`、`status`、`owner` 和 `blocked_by`。状态按 `pending → in_progress → completed` 变化。`create_task` 分配 ID；`update_task` 只能为待处理任务增加依赖；`claim_task` 要求所有前置任务已完成；`complete_task` 要求调用者拥有该任务。首版 CLI 使用稳定的 owner 名称 `tracecoder`，因此重启后同一用户仍能完成已认领任务。`list_tasks` 和 `get_task` 用于查看保存的状态。

存储模块在拼接路径前校验 ID，确认依赖任务存在，拒绝自依赖和依赖环，并在写入前校验整个拟议变更。模块内部使用锁，串行处理 TraceCoder 在同一进程内并行发出的工具调用。更新已有记录时先写临时文件再原子替换；创建任务使用排他创建。本批次不协调两个同时运行的 CLI 进程，也不实现常驻队友。

`list_tasks`、`get_task` 为只读操作；`create_task`、`update_task`、`claim_task`、`complete_task` 需要权限确认，并在 Plan Mode 下拒绝。现有 `todo_write` 继续管理当前会话的临时清单。Task 文件属于本地运行状态，默认加入 Git 忽略规则。

## 错误处理与验证

预期的参数错误以简短工具结果返回，供 Agent 修正调用。非预期 I/O 错误沿用现有工具执行边界呈现。测试不依赖真实模型服务。

按三个独立增量验证：

1. Skill：目录只展示元数据、加载返回全文；验证项目级优先、未知名称、损坏文件、符号链接越界，以及脚本化 Agent 在普通模式和 Plan Mode 下的调用。
2. Memory：新建存储实例后仍能读取此前保存的记录；验证索引与正文分离、重复和显式覆盖、路径校验、权限及 Plan Mode。
3. Task：新建存储实例后仍能看到任务；验证依赖链的阻塞与解锁、依赖环、非法状态转换、错误 owner、同进程并行调用不破坏记录，以及权限和 Plan Mode。

完成后运行针对性测试、TraceCoder 全量测试、Ruff 和 `git diff --check`。当前 Windows 工作区已有依赖 POSIX shell 的测试失败；报告时将这些既有失败与新增功能造成的失败分开。保留用户在 `tracecoder/llm.py`、`tests/test_core.py` 和 `requirements.txt` 中尚未提交的改动。

## 交付顺序

1. 实现 `SkillCatalog`、`load_skill`，接入提示词与 CLI。
2. 实现 `MemoryStore` 和显式 Memory 工具。
3. 实现 `TaskStore` 和持久化 Task 工具。
4. 完成集成回归、在现有 README 之外编写简短使用说明，并提供可复现的三项功能离线演示。
