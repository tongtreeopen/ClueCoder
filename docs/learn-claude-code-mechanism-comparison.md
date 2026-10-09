# TraceCoder 与 learn-claude-code 机制对照：Skill / Memory v2 / Task / P0 边界

> 对照来源：learn-claude-code 提交 [`0dcafa2`](https://github.com/shareAI-lab/learn-claude-code/tree/0dcafa2ae053a1ddd6a72f265431104b08a5aa13) 根目录独立章节；Skill / Memory / Task 的初始 CoreCoder 开发基准为本地提交 `2934e37c2c37464095e851c03252a58058161d56`（2026-09-24），P0 与 P1 Memory v2 增量按 2026-09-25 工作树核对。教学章节不是一个已集成产品；TraceCoder 的规划项不算已实现。

> 版本范围：本文累积记录改名前的 CoreCoder 开发与评测历史。历史 revision、哈希和外部产物路径保持原身份，不是 TraceCoder 改名副本的验证成绩；各节以日期及后续增量为准。

## 一眼看结论

| 机制 | learn-claude-code 教学章节 | TraceCoder 当前实现 | 核心区别 |
| --- | --- | --- | --- |
| Skill | `s07`：扫描一个 `skills/` 目录，目录进 system prompt，`load_skill` 按需取全文 | 用户级与项目级两层扫描，项目级同名覆盖；目录进 system prompt，`load_skill` 取全文 | 保留渐进式加载；新增作用域/覆盖规则、64 KiB 上限与路径检查 |
| Memory | `s09`：`.memory/` 索引、模型筛选召回、回合末自动提取并在阈值后合并 | 项目/用户两级索引、按需正文、会话来源引用、延迟提取、受控自动新建与集中审阅/撤销 | 不再每轮确认；安全且有原文支撑的新候选默认自动保存，冲突/推断待审；合并仍须单独确认 |
| Task / Work | `s10`：`.tasks/` JSON 任务，添加依赖、认领、完成的状态机；`s16` 记录工作流状态 | `.tracecoder/tasks/` 任务状态机；新增会话级可恢复工作卡、Todo 快照与验收门 | 项目 Task 与当前工作分层；Todo done/模型自报不再等于工作目标完成 |
| Permission / Workspace | `s03`：按工具分类与用户批准控制调用 | 所有 CLI 默认绑定 `WorkspacePolicy`；五个文件工具校验路径，Bash 使用本地固定 digest 的 Docker 镜像，否则拒绝 | 用户同意与文件/执行边界分离；默认 Python Agent 同样限界；显式传入宿主工具时由调用方负责隔离 |
| Hooks | `s04`：工具前后执行匹配 Hook | `hooks.py` 保留，但严格 CLI 默认不加载命令 Hook | 保留 API 扩展点，避免 Hook shell 绕过 CLI 工作区；不是删除 Hook |
| Context / Session | `s08`：对话压缩示例；`s16`：工作流运行时日志 | CLI 会话事件日志、工作卡、渐进式 ContextAssembler；仍保留原 ContextManager 压缩器 | 原始事件与模型视图分离，按需展开旧事件；不等同教学 s16 的工作流通用运行时 |
| Goal Loop | `s17`：无工具回答后由独立无工具模型判断是否完成，未完成则反馈续跑 | 仅显式 `/goal` 启用；同一 Agent 停止点加工作卡证据门、候选缓冲、预算与会话恢复 | 教学 s17 的语义判断前增加确定性证据/权限门；未通过不展示为已验收答案 |

## Skill：保留“目录先行、全文按需”，扩展两级作用域

**来源行为。** `s07` 的 `SkillLoader` 构造时扫描 `WORKDIR/skills/*/SKILL.md`，YAML frontmatter 提供名称和简介，system prompt 只展示目录，模型调用 `load_skill(name)` 才拿到完整文件。它只有这一层目录，不定义用户级与项目级优先级。见 [扫描与解析](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s07_skill_loading/code.py#L45-L107)、[目录和加载](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s07_skill_loading/code.py#L109-L137)。

**TraceCoder 入口与过程。** [`SkillCatalog`](../tracecoder/skills.py) 启动时扫描 `~/.tracecoder/skills/*/SKILL.md`，再扫描 `<workspace>/.tracecoder/skills/*/SKILL.md`；同名项目级条目后写入，因此覆盖用户级。目录只有名称、简介，全文暂存在 catalog 中；[`load_skill`](../tracecoder/tools/skill.py) 以名称取全文，而不是接受任意文件路径。[`_local_context`](../tracecoder/cli.py) 将目录接入 [`Agent._full_messages`](../tracecoder/agent.py) 每轮构造的 system prompt。当前扫描是启动快照，文件变更不会自动刷新；子 Agent 继承同一个目录回调和加载工具。见 [扫描与限制](../tracecoder/skills.py#L23-L67)、[CLI 接线](../tracecoder/cli.py#L36-L54)、[子 Agent 接线](../tracecoder/tools/agent.py#L45-L55)。

**保留 / 新增 / 代价。** 保留了 s07 的渐进式披露；新增两级作用域、目录/文件符号链接越界检查和单文件 64 KiB 上限，方便项目规则覆盖个人默认规则并限制载入面。代价是作用域规则比教学示例复杂，且没有热重载；当前只读取 `SKILL.md` 全文，不自动读取其相邻 `references/` 或脚本，也没有 Skill 安装/更新管理器。`load_skill` 在权限与 Plan Mode 下属于只读工具；这不等于 Skill 正文是高信任指令，仓库内内容仍应按来源审视。见 [权限分类](../tracecoder/permissions.py#L20-L38)、[用例](../tests/test_skills.py)。

## Memory 首批基准：从自动沉淀改为显式、可审计的项目记忆

**来源行为。** `s09` 在 `WORKDIR/.memory/` 下使用逐条 Markdown 和 `MEMORY.md` 索引，记录带 `name`、`description`、`type`。开始一轮时，它把近期用户输入交给一次模型调用，从索引中选最多五条，再将正文放入 system prompt；选择失败时退回关键词匹配，召回总量限制为 20,000 字符。回合末无工具调用、即将返回时，它另发模型调用抽取候选持久记忆，筛掉临时项与重复项，写入新记录；新增后记录数达到阈值才尝试模型合并。该章节没有向主模型暴露 `remember`、`read_memory` 之类的工具。见 [存储与索引](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s09_memory/code.py#L84-L219)、[选择与召回](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s09_memory/code.py#L253-L349)、[提取与停止点](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s09_memory/code.py#L720-L747)、[合并](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s09_memory/code.py#L450-L537)。

**当时的 TraceCoder 入口与过程。** [`MemoryStore`](../tracecoder/memory.py) 把每条记录存为 `<workspace>/.tracecoder/memory/<名称哈希>.md`，重建 `MEMORY.md` 索引；[`_local_context`](../tracecoder/cli.py) 只把名称和简介放入 system prompt，正文须经 [`read_memory`](../tracecoder/tools/memory.py) 读取。主模型也可调用 `list_memories` 和 `remember`；后者要求有效名称、非空简介/正文，正文上限 16 KiB，同名默认拒绝，只有 `replace=true` 才覆盖。首批实现**不会**从普通对话自动抽取；P1 增量见下文。

**当时的选择原因与代价。** 首批没有照搬 s09 的自动记忆流水线：通过 `remember` 的权限确认后才落盘，避免把临时信息或未经确认的内容沉淀为长期项目状态。代价是缺少类型分类、模型选取召回、关键词回退和合并去重。`MEMORY.md` 是本地索引，不是向量检索库；历史记忆在提示词中被标为参考，不能覆盖当前用户指令。[`Permission`](../tracecoder/permissions.py) 在交互模式请求写入许可，在无审批回调的一次性模式拒绝写入，但 `--yes` 可放行；Plan Mode 即使 `--yes` 也拒绝写入。若直接调用 `MemoryStore.remember()`，不经过 Agent 的权限层。这些显式工具语义在 P1 保留。

## Task：移植依赖状态机，但不冒充多 Agent 调度

**来源行为。** `s10` 将 `task_<8 位十六进制>.json` 存在 `WORKDIR/.tasks/`；模型先 `create_task` 取真实 ID，再 `update_task(addBlockedBy)` 添加依赖。更新拒绝缺失依赖、自依赖与环；仅在前置任务全部完成后允许 `claim_task`，再由相同 owner 执行 `complete_task`。完成时会报告被解锁的后继任务。工具包装层的 owner 固定为 `"agent"`，所以这个章节的 owner 字段并不证明有多 Agent 并发调度。见 [数据与存储](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s10_task_system/code.py#L62-L125)、[图校验](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s10_task_system/code.py#L127-L171)、[状态机](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s10_task_system/code.py#L219-L273)、[工具包装](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s10_task_system/code.py#L341-L386)。

**TraceCoder 入口与过程。** [`TaskStore`](../tracecoder/tasks.py) 使用 `<workspace>/.tracecoder/tasks/task_*.json`，字段为 `id`、`subject`、`description`、`status`、`owner`、`blocked_by`。`create` 返回 ID；`update` 仅为 pending 任务追加依赖，并验证存在性、去重与无环；`claim` 检查全部依赖 completed，转到 in_progress；`complete` 检查状态与 owner，再转到 completed。[`六个任务工具`](../tracecoder/tools/task.py) 暴露给 Agent，其中 `list_tasks`、`get_task` 可只读运行，其余四个经权限层批准，Plan Mode 下拒绝。`todo_write` 仍是会话内临时清单，不等同于持久 Task。见 [创建与状态机](../tracecoder/tasks.py#L54-L164)、[CLI 接线](../tracecoder/cli.py#L49-L53)、[权限分类](../tracecoder/permissions.py#L20-L21)。

**保留 / 简化 / 代价。** 保留了持久文件、依赖 DAG、pending → in_progress → completed 的核心约束；新增 TraceCoder 既有的交互权限与 Plan Mode 接线、项目状态目录检查和进程内 `RLock`。相比 s10，字段名改为 Python 风格的 `blocked_by`，完成结果只返回任务本身，**不**计算“刚解锁哪些任务”。工具调用中的 owner 固定为 `"tracecoder"`，不是会话或进程唯一身份；`RLock` 只保护同一 `TaskStore` 实例内的并发线程，**不**提供跨 CLI 进程锁、崩溃恢复或分布式认领。因此现阶段只能说“持久化任务与单进程内状态约束”，不能说“多 Agent 协作调度”。见 [存储与锁](../tracecoder/tasks.py#L27-L52)、[认领与完成](../tracecoder/tasks.py#L142-L164)、[并发测试](../tests/test_tasks.py#L105-L119)。

## 历史阶段 P1 Memory v2：补齐召回与提取，但把持久写入变成可审阅操作（2026-09-25）

本节保留迁移前的行为与验收记录；当前 CLI 的默认规则以“渐进式三层记忆”小节为准。

**来源与入口。** [s09 源码](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s09_memory/code.py) 在每轮开始选相关记忆，回合末提取、写入并达到阈值后尝试合并。TraceCoder 在 [`MemoryWorkflow`](../tracecoder/memory_workflow.py) 中实现相同阶段，但由 [`cli.py`](../tracecoder/cli.py) 在 `Agent.chat()` 外编排：本轮一次模型选择（异常时关键词回退），最多五条、正文合计 20,000 字符；主回答正常完成后用另一模型调用从本轮用户输入和最终回答提取最多三条候选。辅助请求不提供工具，失败不替换主回答。旧记录仍可读取，新记录增加 `type`；显式 `remember` 工具保留。直接通过 Python API 构造 `Agent` 不会自动运行 CLI 的 MemoryWorkflow。

**当时的写入政策与 s09 的差异。** s09 在抽取后筛选并自动写入；P1 阶段 TraceCoder 默认展示候选，交互用户逐条确认。`-p` 默认只展示，`--memory-auto` 才允许本次一次性运行自动新增；交互 CLI 须输入 `/memory auto on`，只在当前进程生效。`--yes` 仍只控制工具许可，不能开启自动记忆；Plan Mode 只读召回，不提取、不保存、不合并、不撤销。自动模式仅新增不存在的记录，同名不同内容需人工确认。常见密钥前缀、私钥头和带密码 URL 被确定性拒绝，但检测不完整；模型筛选本身也不能证明候选事实正确。当前规则已迁移，见下文及[使用说明](skill-memory-task-usage.md)。

**合并与撤销。** s09 达阈值后直接用模型结果覆盖多条记录；TraceCoder 在 10 条后生成增删改提案，任何模式都要另行确认。受控新增、替换和整批合并记录前后镜像、哈希，以及 `source`/`mode` 审计字段；`/memory undo` 可在重启后撤销最近一次受控操作，若文件已被后来修改则拒绝，不覆盖新内容。未完成日志在再次打开 Memory CLI 时尝试恢复，冲突时停止受控写入。这是比教学章节更保守的持久化边界，但不等于跨进程强事务或完整秘密防泄漏。显式 `remember` 不进入受控撤销栈，文件 `/undo` 也与记忆撤销独立。见 [`MemoryStore`](../tracecoder/memory.py)、[日志测试](../tests/test_memory_journal.py)、[CLI 测试](../tests/test_cli_memory.py)。

**验证证据与未验证项。** 脚本模型离线测试覆盖模型选择/关键词回退、候选格式与重复过滤、显式开关、`--yes`/`--resume`/Plan Mode、合并提案和重启撤销。2026-09-25 Windows + Docker Desktop Linux Engine 最终运行 `py -3.13 -m pytest tests -q` 为 **289 passed**，`py -3.13 -m ruff check .`、compileall 和 `git diff --check` 通过。未用真实模型测候选准确率、记忆污染率、跨进程竞态或用户敏感数据漏检；因此不能宣称“自动记忆质量已验证”或“记忆存储是安全沙箱”。

## 验证边界与后续更新规则

### 渐进式三层记忆：从可恢复会话到延迟提取（2026-09-25）

**来源与入口。** 教学仓库 [`s08_context_compact`](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s08_context_compact/code.py) 在会话内裁剪/摘要模型消息；[`s16_workflow_runtime`](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s16_workflow_runtime/code.py) 另用工作流日志记录状态。TraceCoder 在 [`SessionJournal`](../tracecoder/session_journal.py) 新增用户目录中的逐会话事件 JSONL，`Agent` 的消息接收器在下一次模型请求前持久化用户、助手与工具消息；[`session.py`](../tracecoder/session.py) 可恢复新日志并导入旧 `*.json` 快照，CLI 的 REPL、`-p`、`--resume`、`--demo` 已接线。工具调用缺结果的崩溃恢复只追加“结果未持久化、副作用未知”的配对消息，不重跑工具。

**区别与取舍。** 这不是把教学 s08 或 s16 原样复制：原有 `ContextManager` 仍压缩模型视图，日志另保留写入时的消息快照，恢复读取返回副本，避免后续压缩修改日志内存缓存。事件有顺序与校验；最后一条不完整记录保留旁档并截去坏尾，完整但校验失败的事件拒绝打开。常见凭据会确定性脱敏，大输出有界截断；这提高可恢复性，但秘密识别不完整、被截断的原文不可从日志重建，旧已压缩快照也不能还原早期原文。后续工作卡、按层组装和延迟提取见下文；它们仍不等于教学 s16 的通用工作流运行时。

**验证边界。** 新增离线日志测试覆盖重启、跨工作区拒绝、尾部损坏、未答工具调用、脱敏/截断、校验、Agent 写入失败门与模型视图副本。早期定向回归为 **37 passed**，严格 CLI demo 也通过；本阶段最终全量回归和中英文 README 行数更新均已完成，结果见下方 2026-09-26 收口记录。该早期数字不是当前总数。

**工作记忆增量。** [`WorkStateStore`](../tracecoder/work_state.py) 在同一 session 的事件日志中保存完整工作卡修订，并以原子快照加速恢复；[`work_update`](../tracecoder/tools/work.py) 可更新目标、步骤、阻碍、下一步及验收引用，`todo_write` 成功变更会先持久化再替换进程清单。默认模型请求现只注入工作卡摘要，完整 Todo 留在可按需读取的工作卡中。模型请求 `complete` 只会进入唯一完成门：无条件/主观条件/无证据时是 `awaiting_review`，验收项登记的 Bash `command` 与成功结果 `event_id` 对应时，或用户明确 `/work confirm` 时可归档；完成后仍保留事件与快照，`/work reopen` 可恢复活动状态。项目 `TaskStore` 状态机未改。相对教学 s10/s16，TraceCoder 增加了会话工作状态与验收来源，但不声称拥有独立评估器；成功命令不证明测试覆盖充分，结果识别仍依赖当前工具格式。

**渐进式披露增量。** [`ContextAssembler`](../tracecoder/context_assembler.py) 已接入严格 CLI 的 Agent 请求：L0 只显示活动卡摘要、近况、旧事件导航与有界 Skill/记忆索引，不再每轮注入整张 Todo 或 P1 召回的全文；`search_session`/`read_session_event`、`search_memory`/`read_memory`、`read_work_state` 提供短片段到原始记录的按需路径。项目和用户级记忆 L0 优先读取已生成的 `MEMORY.md` 索引，不扫描正文。与教学 s09 的“模型轮前选最多五条并直接注入正文”相比，TraceCoder 牺牲了自动全文召回，换取可控上下文预算和来源可追溯；与 s08 的阈值式压缩相比，默认近况裁剪不等待上下文逼近阈值。当前仍沿用原有压缩器；全量离线回归已通过，但语义摘要质量和全部长会话边界未验证。

**长期记忆与延迟提取增量。** [`MemoryStore`](../tracecoder/memory.py) 保持旧项目记录可读，并新增独立用户级目录、来源 session/event、作用域、版本、时间与写入模式；项目同名记录不覆写用户级文件。严格 CLI 的 [`ExtractionQueue`](../tracecoder/extraction_queue.py) 将已持久化回合登记为可合并、可重试的作业；交互模式空闲后由进程内线程处理，`-p` 退出前有界处理，未完成作业留待下次启动或手动 `/memory process`。模型返回 `[]` 不写入；自动路径只接受有直接用户原文支撑、无同名冲突或疑似敏感词的新候选，且写入时记录来源与撤销镜像。工具来源、模型推断或同名更新进入集中审阅；显式 `/memory accept` 才可改写。`--yes` 不扩大记忆授权，Plan Mode 不登记新提取作业且暂停已有作业写入，退出 Plan Mode 后可恢复处理。与 s09 每轮提取、默认自动写入/阈值合并相比，这里用延迟和人工冲突门降低污染风险，代价是记忆生效较晚、无常驻处理保证，也尚未有真实模型污染率统计。会话级锁/租约只降低跨进程重复处理机会，不是强事务；凭据识别同样不完整。

**本阶段验证与边界。** 2026-09-25 在当前 Windows 工作树执行 `py -3.13 -m pytest -q` 为 **329 passed**；定向用例覆盖新会话、`--resume -p`、`/reset`、日志坏尾和未答工具结果、工作卡证据命令匹配、Plan Mode 中途暂停提取、辅助模型失败后的留队重试、用户/项目作用域、安全候选自动保存与撤销、冲突审阅及脱敏。`py -3.13 -m ruff check .`、`py -3.13 -m compileall -q tracecoder` 与 `git diff --check` 通过。离线脚本模型和真实文件系统只证明这些机制路径，不证明真实模型会正确判断长期价值，也未做跨进程强并发、断电恢复、所有敏感信息形态或任意 Docker 主机的安全验证。

**2026-09-26 收口与真实模型冒烟。** 实现提交 `b9062f1` 在独立检出中执行 `py -3.13 -m pytest -q` 为 **320 passed**；当前 Windows 工作树另含未提交的价格估算及其 11 个测试，整体为 **331 passed**。真实 DeepSeek v4 Flash 通过严格 CLI 在隔离临时工作区完成两组有限会话：`-p` 后 `--resume -p` 正确找回临时标记，且没有写入长期记忆；另一组先调用 `read_file` 读取测试文件并正确回答项目名。该组首次辅助提取因 1,200 输出 token 上限耗尽而只返回空正文，队列记录失败；将上限提高到 4,096、保留重试后，直接用户偏好写入用户级记忆并带来源事件，两个工具来源项目事实仍在待审队列，没有自动保存。审阅还发现 Bash 工具异常/中断文本可能误过工作卡完成门，现已拒绝这些结果并补回归。此为少量真实调用，不是候选准确率、成本、长会话恢复、跨进程并发或隔离强度的系统评估；测试目录及其记忆均位于系统临时目录，未写入用户真实记忆库。

### P0：Permission、Hook、Context 的增量对照（2026-09-25）

**s03 Permission。** [教学源码](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s03_permission/code.py) 的核心是工具调用许可；许可本身不是文件系统隔离。TraceCoder 保留 [`Permission`](../tracecoder/permissions.py) 的用户同意与 Plan Mode，同时在 [`_cli_context`](../tracecoder/cli.py) 绑定 [`WorkspacePolicy`](../tracecoder/workspace.py)：`read_file`、`write_file`、`edit_file`、`glob`、`grep` 在读取/写入或输出结果前拒绝已解析的工作区外路径；`--yes` 不能放行。相比教学示例，这是为所有 CLI 模式新增的确定性文件边界。`Agent`/`ALL_TOOLS` 直接经 Python API 构造仍保留原行为，调用方要自行提供策略。路径解析不能抵御并发替换符号链接，不能称为 OS 沙箱。见 [`tests/test_workspace.py`](../tests/test_workspace.py) 和 [`tests/test_cli_workspace.py`](../tests/test_cli_workspace.py)。

**s04 Hooks。** [教学源码](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s04_hooks/code.py) 展示工具调用前后的 Hook 扩展。TraceCoder 的 [`hooks.py`](../tracecoder/hooks.py) 继续支持 API 调用方显式接入，失败时跳过的语义也未变；但所有严格 CLI 默认传空 Hooks，亦不自动加载 MCP，因为两者可执行工作区外副作用。不是删除这两个模块；是取消默认 CLI 暴露。`--demo` 也使用严格装配。代价是旧 CLI 的 Hook/MCP 配置暂不生效；未来若放行，须逐工具白名单及等价隔离。见 [`tests/test_hooks.py`](../tests/test_hooks.py)（API 语义）和 [`tests/test_cli_workspace.py`](../tests/test_cli_workspace.py)（严格 CLI）。

**s08 Context。** [教学源码](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s08_context_compact/code.py) 是独立章节的压缩机制；TraceCoder 的 [`ContextManager`](../tracecoder/context.py) 已按长工具结果裁剪、旧消息摘要、紧急压缩三层工作，因此 P0 不重写。新增 [`tests/test_context_regression.py`](../tests/test_context_regression.py) 验证近期消息、工具调用/结果配对与摘要失败回退。这只证明离线消息结构，未证明真实模型长会话的摘要质量。

**Bash 执行边界。** `s03` 的许可分类没有替 TraceCoder 提供 OS 沙箱；TraceCoder 在严格 CLI 的 [`DockerExecutor`](../tracecoder/execution.py) 中新增独立执行边界，而 [`DisabledBashTool`](../tracecoder/tools/bash.py) 保留缺失后端时的关闭式结果。用户随后明确授权启动 Docker Desktop 并拉取测试镜像：Docker Hub 官方 Python 镜像因 TLS/EOF 拉取失败，微软开发镜像下载停滞后中止，最终成功拉取 Astral [官方文档列出的 Python 3.13 slim 镜像](https://docs.astral.sh/uv/guides/integration/docker/)，并固定本地 digest `sha256:aba5f865793af9275ceaa06d30935dfc6db8b81969c9fb099b0ebca6396b189e`。CLI 不自动拉镜像；仅在本地镜像与 Docker Linux Engine 可用时装配 `DockerBashTool`，否则拒绝，`--yes` 不能回退宿主 shell。

2026-09-25 Windows + Docker Desktop Linux Engine 的 [`tests/test_docker_execution.py`](../tests/test_docker_execution.py) 使用真实容器验证：普通 Python 命令、启动仓库所在 E: 盘挂载、工作区外宿主文件读写失败、越界符号链接不可读写、禁网/非 root、超时、输出洪泛、Ctrl+C 后无残留容器、镜像缺失时不退回宿主执行，以及 CLI 与 demo 接线。容器使用只读根、工作区单一 bind mount、`--network=none`、去 capabilities、非 root 用户与资源限制。Windows 全量回归为 **259 passed**，Ruff、compileall 与差异格式检查通过。这是目标平台上的有限验证，**不**证明 Linux 主机、所有 Docker 漏洞场景、恶意并发替换链接或真实模型端到端任务的安全性；镜像内依赖也不保证覆盖任意编程项目。

- 2026-09-24 在当前 Windows 工作树执行 `py -3.13 -m pytest tests/test_skills.py tests/test_memory.py tests/test_tasks.py tests/test_local_state.py -q`：**50 passed**。覆盖目录优先级/按需加载、记忆持久化与覆盖保护、Task 依赖/环/认领、Plan Mode，以及一条无 API Key 的集成演示。见 [测试目录](../tests/) 与 [离线演示](../examples/local_state_demo.py)。
- 本次仅静态核对 learn-claude-code 源码和其测试文件，**未**在该仓库运行测试，也**未**做两个项目的真实模型会话或跨进程压力测试。教学章节里写有自动提取/合并代码，不等于其效果和成本已被这里验证；TraceCoder 的 50 个测试也不证明模型会在恰当时机自主选择这些工具。
- 后续每新增或改动一项机制，应在本文件继续记录：来源章节与源码、TraceCoder 的实际入口/行为、保留/简化/新增、原因和代价、测试证据、未实现边界。**规划项只有落到代码并通过相应验证后，才能移入“当前实现”。**

## 独立评测基线：在 Agent 循环之外核验修复结果（2026-09-26）

**对照与边界。** learn-claude-code 的 Agent 循环、任务状态和 Goal Stop Gate 是理解执行控制的参考；`/goal` Stop Gate 的职责是决定 Agent 是否继续当前目标。这里新增的外部 Test Gate 在一次普通 `--yes -p` 运行结束后，用维护者固定的隐藏测试判断候选补丁。它不会接受 Agent 的完成声明、Todo 状态或自选验收命令，也不会把 Gate 失败反馈给模型重试。本次实现没有接入或改动 `/goal`，不能将两种门的结果混算。

**实际入口与流程。** [`python -m tracecoder.eval run`](../tracecoder/eval/__main__.py) 载入版本化八题清单，核验快照、干净的 Agent Git 修订、固定镜像、工作容器挂载和全部初始 Gate，再由 [`run_case`](../tracecoder/eval/runner.py) 为每题建立独立工作区与 HOME，调用原有严格 CLI。隐藏测试及参考答案不进入 worker 工作区。[`BudgetedModel`](../tracecoder/eval/budget.py) 对主 Agent 和一级子 Agent 共享 50 次请求、100,000 个提供方已报告 token；评测模式关闭请求重试、参数回退、dotenv 和回合后记忆提取。子 Agent 工具表仍移除 `agent`，因此没有递归孙子 Agent；其工具调用和结果以独立标记写入评测轨迹。Agent 自然耗尽工具轮次也记为 `agent_limit`，不会因 Gate 通过而算成功。进程限时 900 秒，达到上限后终止进程树并按运行标签核验容器清理。

**新增与代价。** [`run_gate`](../tracecoder/eval/gate.py) 使用独立 Docker 容器，候选代码和测试均只读，禁网、非 root、固定本地 digest、资源上限与 120 秒期限；固定八题的可信测试进程在独立子进程中调用候选代码，核验测试完成记录，拒绝提前退出或伪造成功输出。缺失 Docker 时关闭失败，不回退宿主。Runner 保存脱敏轨迹、补丁、真实用量、退出码、耗时和产物哈希。[`summarize`](../tracecoder/eval/report.py) 保留全部八个预定任务，同时给出通过数/预定数、实际 Gate 覆盖和失败类别；未知用量、不可复现身份、环境失败或没有有效 Gate 时，正式成功率为 `null`。代价是每题隔离启动成本、需要本地 Docker 镜像，并且一次回答仍可能越过剩余 token 阈值；下一次请求会被拒绝。

**验证证据。** [`test_all_fixed_cases_discriminate`](../tests/test_eval_fixtures.py) 在真实 Docker 验证全部八题原始快照失败、应用对应参考补丁后通过；[`tests/test_eval_report.py`](../tests/test_eval_report.py) 另用本地脚本 HTTP 提供方和真实 Docker 完成八题 CLI→Runner→Gate→报告闭环，检查固定分母、身份、Gate 覆盖、缺失凭据和 URL 凭据不落盘。提前退出和伪造 Gate 控制结果的容器回归、自然轮次上限、子代理轨迹及并发日志回归也已通过。该闭环用错误脚本答案验证失败计分，不是外部真实模型成功率。2026-09-26 Windows + Docker Linux Engine 29.5.3 的最终全量回归为 **399 passed，0 skipped**（383.06 秒），Ruff、compileall 与差异格式检查通过。

**未实现与限制。** 八个本地 Python 修复题仅为起始样本；没有统计显著性结论、第二 Agent 后端、自动策略晋升或自进化。模型随机性仍会影响结果，真实提供方的正式成功率需要独立的授权运行及完整 usage。可信 suite 应在评测期间保持静止；路径校验不能证明可抵御恶意宿主并发替换文件，脱敏也不保证覆盖编码或变形后的秘密。使用命令和输出说明见[评测使用文档](evaluation-baseline.md)。

## 逐次用量与固定验证集：扩展外部评测，不改变 Agent 决策（2026-09-26）

**来源对照。** learn-claude-code 的 [s15 集成示例](https://github.com/shareAI-lab/learn-claude-code/tree/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s15_integrated_harness)展示如何装配机制，[s17 Goal Loop](https://github.com/shareAI-lab/learn-claude-code/tree/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s17_goal_loop)关注单次目标的继续与停止；两者都不能替代在 Agent 之外冻结任务、逐次记录提供方用量并用独立隐藏 Test Gate 检验候选补丁。TraceCoder 保持原 Agent 停止语义，评测扩展仍由 `tracecoder.eval` 驱动。

**入口与实际行为。** [`BudgetedModel`](../tracecoder/eval/budget.py) 在每次真正发出模型请求前持久写入 `in_flight` 记录，在提供方返回后写入其报告的 prompt/completion token、累计值和用量状态；一级子 Agent 通过 [`AgentTool`](../tracecoder/tools/agent.py) 的调用作用域标记身份。请求前的预算拒绝仅增加拒绝次数，不伪装为提供方调用。[`run_case`](../tracecoder/eval/runner.py) 把 `model_calls.json` 脱敏、哈希归档，另设轨迹完整性字段；`usage.json` 格式、50 次调用限额、Gate 和成功率判定保持原义，当前默认 token 限额为 100,000。[`tracecoder.eval run`](../tracecoder/eval/__main__.py) 现在按 manifest ID 选择两套各八题的固定顺序；新增 [`tracecoder-holdout-v1`](../benchmarks/tracecoder-holdout-v1/manifest.json) 的快照、隐藏 Gate 与参考补丁，原 v1 清单未改。

**选择与代价。** 逐次用量使预算耗尽的轨迹可按调用定位，也明确区分已发请求但用量未知与请求前拒绝；代价是每次调用增加一次本地原子文件写入及一份归档。第二套任务可供候选改动后的独立验证，但八题样本仍小，且 Gate 覆盖只限于已写断言。验证集只有在候选开发与评测过程中保持冻结才有比较价值；其结果应与开发集分别报告，不自动晋升 Agent 配置。

**证据与边界。** [`test_eval_budget.py`](../tests/test_eval_budget.py) 覆盖主/一级子 Agent 归属、预算前置拒绝、缺失/部分用量、提供方错误和中断；[`test_eval_runner.py`](../tests/test_eval_runner.py) 覆盖真实 Docker 运行时的产物脱敏与哈希。新增 [`test_eval_holdout.py`](../tests/test_eval_holdout.py) 以真实 Docker 核验八个原始快照全部失败、各自参考补丁全部通过，并检查固定调度与旧版兼容。此阶段没有在新验证集上调用真实模型，因而没有验证集成功率或统计结论。调用身份标记只覆盖评测模型的主 Agent 与一级 `AgentTool` 子 Agent；其他辅助模型调用若绕过此作用域会标为主 Agent。轨迹完整性是归档诊断，不参与协议有效性或成功率计分。

## s17 Goal Loop：显式目标下的独立停止门（2026-09-26）

**教学机制。** [`s17_goal_loop/code.py`](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s17_goal_loop/code.py) 将无工具响应视为候选停止，交给不持有工具的独立评估器判读；未满足目标时把理由送回循环。它是教学实现，评估仍是模型判断，不是形式化证明。

**TraceCoder 入口与过程。** 严格 CLI 的 [`/goal` 与 `-p "/goal ..."`](../tracecoder/cli.py) 显式创建会话级 [`GoalController`](../tracecoder/goal.py)；普通聊天及未配置控制器的 Python `Agent` 不启用。控制器把状态、续跑次数、累计活跃时间、已报告 token 和待评估候选写入 [`SessionJournal`](../tracecoder/session_journal.py) 的版本化事件。[`Agent.chat`](../tracecoder/agent.py) 只在原有无工具返回点询问控制器：Goal 模式先缓冲候选，核验后才对外流式显示已验收答案；驳回时在同一循环继续，不伪造用户消息。[`ContextAssembler`](../tracecoder/context_assembler.py) 每轮只注入有界 Goal 指令、最近 Bash 结果事件 ID 和驳回理由，供模型按需登记验收，而非把整份事件日志塞入上下文。

**相对 s17 的新增与取舍。** [`WorkStateStore.goal_evidence`](../tracecoder/work_state.py) 是只读前置门：验收项的 Bash 命令与真实工具结果事件须精确对应；阻碍、拒绝、失败、中断、缺证、成功检查后的写文件/编辑，以及非当前验收集内的后续 Bash 均不能自动通过。无可核验检查时需用户明确 `/work confirm`；确认后的写入/执行也会使确认过期。之后才由独立 [`GoalEvaluator`](../tracecoder/goal_evaluator.py) 发起 `tools=None` 的模型调用，严格核对 JSON、理由和真实证据 ID；评估器不能替用户授权或覆盖确定性失败。默认最多 3 次自动续跑、累计 15 分钟和 100,000 已报告 token；`--yes` 不放宽证据或 Docker 边界。`--resume` 恢复预算与待评估候选，重新判读候选但不自动重放旧工具；交还用户后的新消息可在不重置预算的前提下再试，时间/token 上限耗尽须新设目标。`/reset` 新会话不带旧目标，Plan Mode 只记录目标、待退出后执行。这比教学 s17 更保守，代价是多一次模型调用、事件写入以及用户/模型登记验收的操作成本。

**验证与未覆盖。** 2026-09-26 Windows / Python 3.13 的离线脚本模型和临时会话测试见 [`tests/test_goal_loop.py`](../tests/test_goal_loop.py)：无 Goal 回归、真实工具结果登记到工作卡再验收的端到端路径、候选缓冲、续跑次数、时间/token 上限、拒绝工具、命令证据新鲜度、用户确认、评估错误、重启恢复与待评估候选、Plan Mode 和 `/reset`。全量 `py -3.13 -m pytest -q` 为 **356 passed, 11 skipped**；Ruff、compileall 与差异格式检查通过。11 项跳过项不等于 Docker Goal 场景已验证：本轮 Docker 服务未及时响应，未进行真实容器或真实模型的 Goal 端到端运行。Bash 成功识别仍依赖现有工具文本格式及命令/结果事件匹配，不是结构化 OS 证明；未测真实评估模型的语义准确率或弱测试漏检率。异常断电只能恢复到最后落盘事件，当前不是跨进程事务或后台自动唤醒。没有实现 s11 后台任务、s16 通用工作流或自进化候选评测/晋升。

## s08 Context：预算感知的四阶段压缩（2026-09-29）

**实际请求路径。** 严格 CLI 现在由 [`ContextAssembler`](../tracecoder/context_assembler.py) 在每次请求前构造唯一模型视图，并将 system、会话消息和工具 schema 一并估算。可用输入预算按配置上下文上限减去本次输出预留和 10% 安全余量计算；默认 `M=128,000`、`O=4,096` 时为 111,104 tokens。估算按模型读取同一会话最近的提供方 `prompt_tokens` 校准；上下文上限来源和输出预留写入 session metadata。达到预算仍无法缩减时，请求在发给模型前报错，不会截掉当前用户请求。

**四阶段及归档。** 顺序与 learn-claude-code `s08_context_compact` 对齐：`tool_result_budget` 对超 200,000 字符的当前工具结果批次按最大结果优先归档；`snip_compact` 在消息视图超过 50 条时保留开头 3 条与末尾约 46 条并记旧事件范围；`micro_compact/fit_tool_results` 在估算超过 70% 目标后归档较旧已消费输出，最近三个优先保留原文；低成本步骤后仍超过 85% 才运行 `compact_history`。归档先脱敏，文件路径限定于当前 session，记录 SHA-256 并支持有界读取；事件搜索仍返回短预览，`read_session_event` 可读取归档片段。摘要是低信任导航信息。超过 95% 进入紧急视图，供应方明确拒绝 prompt 长度时另做一次紧急视图重试。显式 `/compact` 也走相同组装器。

**评测预算提示与边界。** 仅 `TRACECODER_EVAL_MODE=1`、提供方累计 usage 已知且 Git 状态可读时，60% 无生产代码 diff 和 80% 总预算会触发一次进度提示；普通会话不触发。该提示不限制工具调用，也不证明 patch 正确。当前估算仍是启发式，真实模型窗口须由 `TRACECODER_MAX_CONTEXT` 配置；服务端硬窗口不匹配时依靠一次 prompt-too-long 恢复。离线脚本测试覆盖了预算公式、usage 校准、四阶段、配对保留、归档脱敏/哈希/回读、摘要去重、紧急重试和 eval-only 提示；没有真实模型或 SWE-bench Verified Mini 对照结果，因此尚不能声称成功率提高或总 token 降低。实现计划见 `2026-09-29-budget-aware-context.md`（归档材料未收录于此副本）。

## s09 Memory / s10 Task / 会话并发：真实验证补充（2026-10-01）

本轮只补验证证据，未修改机制实现。固定 8 题历史轨迹中的三个失败已经独立 Docker 重放：两次 retry_backoff 是先计算指数再封顶导致的大数溢出，一次 config_merge 是留下任务不允许的测试文件。四个历史 run 均没有预算压缩事件，不能把整体 token A/B 当成长上下文压缩收益证明。

实际线程/进程探针确认：共享同一 TaskStore 的 10 轮领取都只有一个赢家；独立实例 10/10 轮、四进程 7/10 轮出现重复领取。同一 session 的四个写进程写完后无法重新打开；共享 Journal 实例的 64 条线程写入及四个独立 session 均可读回。因此实例锁不构成跨实例或跨进程协调。

真实 DeepSeek v4 Flash 的默认 ExtractionQueue 固定 10 类样例各两轮，通过事先约定的 13/20 个场景；稳定遗漏是长消息尾部事实、同义去重和偏好更正。按 CLI reading 开关保留相同工具 schema 的最终配对中，Memory on 找回 8/8 个正文历史事实，off 均返回未知；当前请求覆盖旧记忆和无关查询均通过。on 严格 JSON 通过 10/12，两次注入例正确拒绝恶意指令但多加了说明。不能把小型资料查询成绩当作编码效益、consolidation 质量或所有攻击形态的证明。

会话/Task 与 Memory 定向离线回归分别 41、62 项通过。原始轨迹、固定样例、作废尝试、来源哈希和完整边界见 [2026-10-01 验证报告](validation/2026-10-01-agent-session-task-memory.md)。

## s09 Memory / s10 Task / Agent：定位后的修复（2026-10-01）

**入口及实现。** [`StateLock`](../tracecoder/state_lock.py) 使用标准库操作系统文件锁和线程可重入锁；[`TaskStore`](../tracecoder/tasks.py) 在项目级锁中完成读、状态检查和写回。相对教学 s10 的简单文件状态，TraceCoder 现在防止独立实例和多进程重复领取。[`SessionJournal`](../tracecoder/session_journal.py) 在锁内读日志，追加时拒绝日志字节数已经变化的过期写者，metadata 重读和原子替换也受锁保护。

**Memory 取舍。** [`ExtractionQueue`](../tracecoder/extraction_queue.py) 完整分段读取会话来源，为模型提供有界现有记录，要求指向原名称的 duplicate/update 关系。重复跳过，更正携带旧哈希待审，接受替换和撤销沿用已有持久日志。自动生成标签允许规范化标点，显式命名规则不变。相对 s09 的简化提取流程，这增加了模型输入和长消息批次数；语义去重依赖模型，现有目录不完整时转审阅，不能称作完备的语义冲突检测。

**Agent 验证行为。** [`prompt`](../tracecoder/prompt.py) 要求实际执行压力和边界检查，特别检查封顶之前的数值运算。可信评测 Runner 把 allowed_paths 传至 [`CLI`](../tracecoder/cli.py) 和 [`Agent`](../tracecoder/agent.py)：文件工具提前拒绝越界，候选最终答复前再核对文件哈希，要求恢复遗留越界文件。独立 Docker Gate 与导出后的路径拒绝继续存在；普通聊天及未设置 `/goal` 的通用完成判断保持原有边界。

**已执行证据。** 真实 Windows 四进程 Task 领取 10 轮均单赢家；64 条线程事件和四进程日志写入后可连续读回，三个过期写者明确拒绝，重新打开可续写。真实模型冻结 Memory 提取由 13/20 到 20/20，读回 on/off 严格 JSON 各 12/12；额外留出严格规则为 4/6，保留双事实拆分与待审项的评分限制。最终干净 revision `14ec777` 的固定八题两轮为 16/16，修改前同协议为 14/16；总 tokens 从 465,478 到 512,026，约增加 10%，不能宣称节省成本或统计显著提升。最终 Windows 全量回归 **468 passed，无跳过**，Ruff 和差异格式检查通过。固定协议、离线回归及所有中间失败见 [修复验证报告](validation/2026-10-01-agent-state-memory-fixes.md)。

**未覆盖。** 项目 Task 锁串行化整个 Store；Session 是操作级互斥和过期拒绝，没有生命周期租约、自动多写者合并或跨文件事务。未增加并发 Memory 事务、通用数值代码静态检查、强制所有 Agent 都执行正确测试、任意 Bash 即时副作用拦截，也未验证 consolidation、长会话收益或真实 Goal 评估器质量。

## s11 Background Tasks：会话内可取消的 Docker 命令（2026-10-02）

**来源。** 根目录 [`s11_background_tasks/code.py`](../../learn-claude-code/s11_background_tasks/code.py) 的 BackgroundManager 用线程执行 Bash，立即返回 ID，之后收集结果并通知模型。该教学机制使用宿主执行，不包含 TraceCoder 的 Docker 生命周期和工作卡证据门。

**入口与实际行为。** 严格 CLI 的 [`DockerBashTool`](../tracecoder/tools/bash.py) 增加 `run_in_background=true`；默认仍同步执行。会话级 [`BackgroundManager`](../tracecoder/background.py) 维护任务，模型通过 `background_status(task_id)` 查询状态、退出码和有界输出，通过 `background_cancel(task_id)` 取消。状态查询只读，启动和取消沿用工具权限；无 Docker 不注册后台工具，也不回退宿主 shell。最多同时运行四个任务，超时默认 120 秒、最大 300 秒，输出上限 1 MB；父子 Agent 共享会话任务，子 Agent 返回不会结束任务。

**保留与新增。** 保留立即返回 ID 和下一轮收集结果的思路。前后台共用 [`DockerExecutor`](../tracecoder/execution.py) 的隔离参数与清理逻辑；先 create 容器再 start --attach，使取消前已有明确容器身份。后台容器内 Python watchdog 为命令设置最大时间并终止子进程组，宿主监督器控制取消、输出溢出和清理。只有零退出且清理已验证才是 completed。CLI 退出、reset 和进入 Plan Mode 前清理活跃任务；清理不可信时拒绝 reset/进入只读模式。

**验收接线。** 后台线程只更新内存，主线程持久记录 note 类型的 background_start/background_result，完成记录关联真实命令、任务 ID 和启动工具调用。结果经低信任运行上下文提供给模型，不伪造用户指令或重复工具回复；查询结果携带已落盘完成事件 ID。工作卡和 Goal 只接受与真实启动回执对应、没有干扰的 completed 事件。活跃任务、未持久终态和清理失败阻止验收及用户确认。前台检查与后台执行重叠、检查后的新命令/文件操作/子 Agent 委派均使证据失效，要求所有任务结束后重跑检查。

**代价与边界。** 每次命令多一次容器 create 调用；后台进程需要镜像内 Python，缺失时不会执行用户命令。任务 ID 只在当前 CLI 进程有效，不提供常驻服务、重启续跑或自动唤醒。普通对话可返回“任务仍运行”，单次 `-p` 退出时清理任务。重启缺终态记录时不自动重放命令，要求新会话重新执行检查。强杀 CLI 后 watchdog 保证命令有时间上限，但可能留下已停止容器；不能承诺异常断电清理。子 Agent 任务可查询和取消；只有可与本会话真实启动消息配对的结果才可作验收证据，子 Agent 摘要不代替主 Agent 检查。

**验证。** [`tests/test_background_tasks.py`](../tests/test_background_tasks.py) 使用真实 Docker 覆盖立即返回、无换行部分输出、并行可用性、取消子进程、超时、输出溢出、并发上限、CLI 进程直接退出后的容器内期限、父子共享及 reset 清理；脚本模型/真实日志覆盖结果只持久一次、落盘失败重试、启动/查询结果不可验收、失败状态、恢复缺证、后台/前台检查重叠及子 Agent 修改后的新鲜度。最终回归数字及运行范围见 [本次验证报告](validation/2026-10-02-background-tasks.md)；本次没有调用真实模型，不能宣称解题成功率或成本改善。

**真实模型补测（2026-10-03）。** 使用当前 deepseek-v4-flash 配置、固定 Docker 镜像及默认 Goal token 预算，四个有指导场景各跑两轮。严格验收 **0/8**：连续状态查询耗尽预算、额外 Bash 使证据失效、新鲜重检未完成，取消场景还不满足单个 JSON 输出格式。取消实际行为 **2/2** 成功，八次清理与独立源码 Gate 均通过，失败/过期结果未被验收成功；定向回归 **96 passed**。这表明生命周期与证据门已有直接验证，但真实模型后台工作流尚未验收通过。协议、失效探索记录、轨迹与 71 次正式调用/227556 token 统计见 [真实模型报告](validation/2026-10-03-background-real-model.md)；本轮没有修改产品逻辑或提高 Goal 预算。

**Goal 默认预算调整（2026-10-03）。** 按用户要求将 GoalController.MAX_TOKENS 从 30,000 调为 100,000，仍累计工作模型与评估模型已报告的输入/输出 token；时间、续跑与证据门保持原行为。边界回归覆盖累计 99,999 token 时仍 active，再计 1 token 触发 limit；Goal 与工作卡测试 **35 passed**。本次未重跑真实模型，不将此前固定 30,000 Goal/48,000 外部预算的 0/8 结果改写；提高预算尚不能证明已修复重复调用。

**有界等待补充（2026-10-03）。** background_status 增加 wait_seconds=0…30，默认等待 10 秒、终态提前返回，复用 DockerRun 完成事件；等待期间不持 manager 锁，取消可并发进行。Agent 在主线程落盘并将真实 result_event_id 回填查询/取消回复，并行 Bash 仍使证据失效。与 s11 的异步执行/通知相比，增加进程内有界等待，减少模型轮询，不提供跨进程唤醒。定向回归 **100 passed**；两轮真实模型查询 **24→9**，成功场景有效新鲜证据 **2/2**，修改后新鲜重检 **1/2**，严格整场验收 **1/8**。内部预算同时已调整为 100000，非单变量实验；Goal 评估 JSON、续跑 BadRequest、格式和重检偏离仍未解决，不能宣称完整后台工作流已通过。协议与边界见 [等待方案报告](validation/2026-10-03-background-wait.md)。

**s11 / s17 / s08 请求链修复（2026-10-03）。** [`LLM`](../tracecoder/llm.py) 保留 DeepSeek 工具续跑必须回传的 reasoning_content，并计入上下文估算；[`GoalEvaluator`](../tracecoder/goal_evaluator.py) 仅在官方 DeepSeek 端点的无工具判读请求关闭思考、启用原生 JSON object，上限 2000，记录真实 finish_reason，截断直接拒绝。[`GoalController`](../tracecoder/goal.py) 向评估器提供有界真实活动上下文核对辅助要求，当前用户请求始终保留；辅助 ID 不可代替确定性成功检查证据。[`ContextAssembler`](../tracecoder/context_assembler.py) 修复最近窗口和 snip 丢弃未完成用户请求的问题，仍保留工具调用/回复配对及硬预算。这是相对教学工具循环、停止门和压缩的协议与上下文接线修复，不新增后台服务、通用工作流或自动 JSON 修补。

最终相关回归 **163 passed**，四个当前请求保留反例修复前全部失败；中间源码全量 **503 passed**，不作为最终源码全量成绩。固定八个真实模型/Docker 场景最终 **7/8**，成功、取消与修改后重检均 2/2；故意失败一例正确阻止验收，但 JSON 外加代码围栏，仍严格判失败。51 次调用、185850 token、9 次状态查询，用量全部已知；无接口/评估器错误，无残留容器或凭据原文泄漏。上一 1/8 使用 48000 外部预算，本轮为 100000，且最后候选评分更严格，非单变量比较，不推广为一般解题成功率。诊断、中间 3/8 失败、冻结来源和剩余格式问题见 [修复测评报告](validation/2026-10-03-background-repair.md)。

**s17 阻碍出口的格式序列化（2026-10-03）。** [`Agent._blocked_json_report`](../tracecoder/agent.py) 只在显式 Goal 已判 needs_user 且工作卡有 blocker 时，返回整个有效 `verified=false` JSON 对象原文；可去掉包住该对象的单层代码围栏，省略会破坏 JSON 的 Goal 状态前缀。原始候选和关联的 goal_report_format 事件保留，失败事实、证据 ID、Goal 状态和成功门均不改变。相对 s17 的候选停止判读，这是停止后的输出格式处理，不再让工作模型或评估器续跑，不增加工具、预算或通用格式修补器。畸形 JSON、额外解释、数组、verified=true、无 Goal 及其他停止状态保持原行为。

三例格式回归修复前失败，最终相关回归 **171 passed**；上次真实失败会话副本重放输出可直接解析，payload 和事件 19 完全不变，仍 needs_user，新增工具/API 请求为 0。新真实测评增加整个实际返回值的 JSON 校验，并单独报告原始模型格式与系统输出，避免把序列化称作模型原生格式能力。固定协议、完整结果及边界见 [格式修复报告](validation/2026-10-03-background-report-format.md)。

冻结真实模型/Docker 复测 **8/8**，实际返回值全可直接解析；原始模型候选也恰好全部为纯 JSON，因此本轮没有触发去围栏，不能称模型格式问题永久消失。47 次调用、171388 token 用量全部已知，无误验收、接口/评估器错误、残留容器或凭据原文泄漏；失败及取消均保持 needs_user。去围栏机制本身的证据是原始失败会话重放与回归，范围仍只限单个有效 verified=false 对象的外围包装。

## s08 / s11 / s17：五项验证与补强（2026-10-04）

低指导后台任务去掉查询、证据登记等操作步骤，固定任务、快照、预算和外部 Docker Gate；保留三个阶段 **4/8→7/8→7/8**。最终失败是额外 Bash 造成证据/任务约束未满足，随后工作回复用满 1200 输出 token 却没有正文/动作；现在明确 limit，停止无效请求，没有误验收或接口错误。这不是教学流程或泛化编码稳定性证明，不能把先前有指导 8/8 推广。

[`Agent._repeat_feedback`](../tracecoder/agent.py) 只对当前回合连续三个参数、结果都不变的指定只读调用提示无进展；不拦截实际执行，不限制必要 Bash 重检。真实模型三次读取后结束，四项确定性回归覆盖新用户回合、修改、结果变化与旧记录。相对教学循环增加了有界观察提示，没有全局工具去重或强制停机策略。官方 DeepSeek 带工具请求的 assistant 视图显式保留空 reasoning_content，解决同回合续跑 400；无正文/工具的工作回复记录 usage 并明确停止，不追加无效历史。评测 CLI 的独立评估器共用 BudgetedModel，避免绕过调用预算；普通无 Goal 不增加自动续跑。

[`ContextAssembler`](../tracecoder/context_assembler.py) 将当前请求保留在前缀并裁掉中间工具历史，真实记录的 120 对调用从超预算 30597 token 降至 **9311/12400**，保留最新要求、配对及硬上限；压缩不能使实际 Docker 的过期检查重新有效。摘要语义准确率仍未验证。

实际 CLI 子进程三种一次性模式通过，Windows 真实伪终端的 Goal、reset、活跃任务清理、Ctrl+C 和退出 **7/7 检查点**通过。两次强杀实际 CLI 后 watchdog 均以 124 退出，复用已有外部 Runner 的精确 run label 清理，本次清理后其他 run 哨兵仍运行；没有新增普通 CLI 异常断电自动恢复服务。测试容器断言由全局名称前缀改为 fixture 独立 label，避免把并行任务计入清理失败。最终完整回归 **520 passed，无跳过**。协议、失败阶段、真实 CLI 轨迹、范围与凭据审计见 [五项验证报告](validation/2026-10-04-five-improvements.md)。

## s16 结构化输出：给现有子 Agent 增加可校验结果（2026-10-04）

**来源。** 教学 [s16 源码](../../learn-claude-code/s16_workflow_runtime/code.py) 的 ExecutionState.agent() 校验子任务 JSON/schema，非法时重试一次，再失败则报错；它还提供 workflow registry、并行/流水线及 journal 缓存。本次只借鉴结果校验边界，没有引入整个工作流运行时。

**入口与取舍。** [AgentTool](../tracecoder/tools/agent.py) 新增可选 output_format="structured"，默认 text 保持兼容。固定 status/summary/findings/evidence_refs/blockers 五字段，宿主添加 child_id；拒绝额外/重复字段、类型错误、状态与阻碍矛盾。findings 为字符串列表，可含源码位置；没有引入任意 JSON Schema 解释器或新依赖。报告上限 16384 字符，不直接截断返回 JSON。

**保留与补强。** 保留一次有界纠正，但不给工具，沿用同一模型、共享预算包装器和 child_scope。再次非法、返回工具或截断则失败。Agent.last_finish_reason 与 last_turn_completed 区分停止和未完成；轮次、空回复、截断或预算耗尽不会输出结构化 completed，finish 事件也反映校验结果。evidence_refs 仅接受本次真实工具回复调用 ID，用 child_id 定位；普通 CLI 结构化模式保存子任务 note，已有回调仍可接管轨迹。大型来源只保存预览和哈希，直接 Python API 无回调/工作卡时不承诺持久化。来源指针和完成状态是低信任报告，不替代主 Agent 新鲜检查证据或独立 Gate。

**验证与边界。** 最终定向回归 **208 passed, 16 skipped**，新功能 26 项；Ruff、warnings-as-errors compileall 和差异检查通过。Docker 服务不可连接，16 项真实后台容器测试跳过；没有全量 pytest 或真实模型评测。未新增异步子 Agent、workflow 缓存/续跑、任务 worktree 或 Team Runtime，也未证明 findings 语义真实、纠正语义不变或编码成功率改善。调用方法、源码哈希、原始输出及预算/日志边界见 [验证报告](validation/2026-10-04-structured-subagent.md)。


## 2026-10-05：代码评审修复增量

- 入口：Agent 默认经 tools.default_tools 创建独立工具及工作区策略；CLI 保留 Docker/Disabled 后端。显式 BashTool 仍供可信宿主调用方使用。教学工具循环未增加这些边界。
- MemoryStore 复用 StateLock 跨进程串行事务；数据和索引替换前 fsync；undo 固定被选择的 operation，避免后台新保存改变撤销对象。未提交事务仍按 before-image 回滚，目录元数据掉电持久性未验证。
- ContextAssembler 截断索引时保留整行并提示按需检索，裁剪时保留消息对应的源下标；紧急恢复只接受结构化 context 错误码。库路径保留 context_backup，失败时恢复原历史；CLI 完整事件仍存于 journal。
- Plan Mode 的 Todo 保留临时规划用途而不写 work card，work_update 被拒。与教学会话清单相比，区分临时计划与持久工作状态。
- eval 内层尊重请求的 attempt 数，不覆盖成 5 次；可信 Gate supervisor 与 test child 以 python -I 启动。逻辑调用预算仍不等于物理 provider attempt 数，外层瞬态重试最多 5 次。
- --demo 默认执行固定离线脚本，使用临时目录和显式工具；严格演示的 journal/memory 也落在演示目录。完整验证与未验证边界见 修复记录（归档材料未收录于此副本）。


## 2026-10-05 第二轮 review 修复

- MemoryStore.remember 复用 save_candidate/apply_changes：统一类型、版本和显式写入元数据，并进入可撤销操作日志；保留旧工具接口。
- TaskStore 创建/替换在重命名前 flush/fsync；列表逐条跳过损坏记录并告警。依赖更新严格读取全图，损坏或缺失节点拒绝写入；未知任务错误不包含宿主路径。
- 保留 Skill 根目录不允许重定向的策略；根内 manifest 链接允许读取，并增加字段回退、根内链接测试。
- Docker 测试共享实际镜像/daemon 前置检查，缺少环境时跳过，静态与失败关闭测试继续执行。
- Gate 保留独立验证进程及只读挂载；候选程序在验证容器能读取 /gate，因此不声明对抗式隐藏测试隔离。未引入容器权限重构。
- README 移除剩余过期代码行数；评测文档统一为一次逻辑调用最多五次 provider 尝试，失败逻辑调用仍计一次预算。
- 入口：tracecoder/memory.py、tasks.py、eval/gate.py；回归：tests/test_review_round2.py。真实容器与模型 A/B 验证仍未完成。

第二轮验证：最终重点回归 102 passed、8 skipped；Ruff 默认规则和 E501 通过。全量执行 498 passed、56 skipped、13 failed，其中 3 个旧 fixture 在修正后重点回归通过，另 10 个为既有 Windows/评测临时路径限制。未重复全量，未运行真实 Docker 或模型评测。证据见 [修复摘要](validation/2026-10-05-review-repair-summary.md)。


## 2026-10-06 s16 Workflow Runtime

入口：`tracecoder/workflow.py`、`workflow_journal.py`、`workflow_budget.py`、`workflows/review.py`、`tools/workflow.py`；CLI 注册 workflow 与 workflow_status。

保留教学版宿主固定流程、步骤日志与断点恢复；简化为串行只读 review-changes，不引入动态脚本、并行、嵌套或副作用步骤。复用结构化子 Agent，三个审查维度后验证发现，精确字符串去重。

新增：运行锁、日志校验链与残片修复、快照重建；源码/模型/版本/参数身份约束和逐步漂移检查；跨恢复累计预算与未知 usage 失败关闭；跟踪文件白名单；外层显式 Goal/usage 计费。完成缓存恢复不调用模型。代价是源码或模型变化需新建运行，未跟踪输入阻止启动，忽略文件不能作为审查输入，含 workflow 的工具批次串行执行。

验证：真实 Git 仓库、进程锁和 CLI 子进程协议测试；独立审查问题已修复。功能回归 42 passed；最终全量 503 passed、47 skipped、33 failed。其中 32 个旧临时路径/工作区失败通过关闭本次接线的隔离副本复现；另一个 Windows demo 全量失败而单独通过，根因未确定；不声称全量通过。未实现后台/异步调度、并行、动态流程、修改代码和真实模型收益评测，模型审查不替代 Goal 或 Test Gate。详细命令、证据与边界见 [验证记录](validation/2026-10-06-workflow-runtime.md)。


## 2026-10-06 分支审查：上下文与记忆 CLI 修复

入口：ContextAssembler.build 与 MemoryCLI.handle_command。保留四阶段上下文缩减、当前用户请求和工具调用/结果配对；沿用当前工作树的用户请求保留与原始位置映射。新增最终 tool_result_fit，在工具结果仍超输入预算时归档当前结果并保留预览/引用，超大用户请求仍拒绝。将 /memory 领域命令分派集中到 MemoryCLI，保持既有 Plan Mode 与阅读开关回调，不引入新命令或自动写入权限。

测试覆盖 60 条消息 snip→summary 来源与游标、不重复发送已总结历史、当前工具输出归档与全文回读、真实记忆队列的拒绝/接受/撤销。未做真实模型收益对照和 Docker 评测；详见 [修复验证](validation/2026-10-06-branch-review-fixes.md)。


## 2026-10-09 TraceCoder 首发修复

- 来源：保留 CoreCoder 工具工作区策略与已有 ScriptedLLM 离线 demo；未增加新的 Agent 验收机制。
- 实际入口：`tracecoder --demo` 的固定脚本通过当前 Python 解释器执行生成的 Fibonacci 测试函数，普通 CLI 的 Bash 隔离要求不变。
- 保留：文件工具的 WorkspacePolicy、权限与 Plan Mode；旧工具测试使用明确的临时工作区和新工具实例，不放宽运行时路径限制。
- 简化：离线 demo 使用标准库及断言，去除普通安装对开发依赖 pytest 的隐式要求，并避免 pytest 从 Windows 路径向上探测导致的权限失败。
- 验证与边界：见 `docs/validation/2026-10-09-first-release.md`；固定脚本 demo 不证明真实模型自主性或 Docker 可用性。


## 2026-10-09 Linux Docker 首发验证增量

- 来源：现有 DockerExecutor 的非 root 容器隔离及独立 runner；远端 Linux 检查暴露私有项目目录无法被固定 nobody UID 写入。
- 实际入口：普通 DockerExecutor 在 Unix 非 root 调用时使用宿主 UID/GID；Windows 和 root 调用保留 65534 回退。带 run_id 的独立评测 worker 继续使用 65534，由 runner 的 _prepare_workspace 准备权限；/testbed 原有边界不变。
- 保留：禁用网络、只读容器根、能力裁剪、启动工作区挂载、deadline 与容器清理。
- 取舍：普通容器继承调用者对已挂载项目的文件权限，支持 Linux 0700 项目目录；不自动 chmod 用户项目，也不允许普通容器以 root 运行。
- 测试：新增宿主身份与 root/Windows 回退检查、Linux 私有目录实际 Docker 写入检查。中断测试改为替换 execution 模块的 time 引用，避免全局 time.sleep mock 干扰 POSIX subprocess 清理。
- 验证结果及未完成边界以首发后续验证报告为准。
