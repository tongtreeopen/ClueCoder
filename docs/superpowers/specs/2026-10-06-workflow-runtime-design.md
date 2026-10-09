# 固定流程日志与断点续跑设计

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 tracecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

状态：待用户审阅；2026-10-06。本文不表示产品已经实现。

## 目标与首版范围

在现有模型驱动 Agent 循环之外，提供宿主注册的固定流程。用户或模型通过 workflow 工具启动并取得 run_id；执行中断后显式恢复，已完成且身份匹配的步骤不重复调用模型。首个交付流程为只读 review-changes：捕获 Git 变更 → 按正确性、接口兼容性、测试覆盖逐维审查 → 验证发现 → 合并重复项 → 报告。

沿用上一轮讨论的只读 MVP。首版串行运行，不引入线程并行、后台服务、自动唤醒、嵌套 workflow 或任意模型生成脚本。宿主维护名称与版本 registry，模型仅提交名称、允许的参数及 resume_from_run_id。

备选方案：仅外部 CLI 脚本更简单，但主 Agent 无法直接调用；移植 s16 全部 parallel/pipeline/nesting 增大并发和恢复范围。推荐先做工具背后的串行运行时，再根据实际成本增加并发。

## 入口与结构

- tracecoder/workflow.py：可信 registry、WorkflowRuntime、WorkflowContext、步骤调用、状态及恢复规则。
- tracecoder/workflow_journal.py：每次运行的目录、身份、步骤事件、互斥、校验和原子快照。
- tracecoder/workflows/review.py：首个宿主注册的审查流程。
- tracecoder/tools/workflow.py：workflow(name, args, resume_from_run_id) 和 workflow_status(run_id)。
- CLI 注册工具，运行时接收主 Agent 的模型、权限、工作区及日志来源；默认 AgentTool 文本方式不改。

WorkflowContext 提供 step(label, prompt, dependencies) 与进度记录，复用现有结构化子 Agent 契约及一次格式纠正。结果保留 child_id/evidence_refs，不把子任务 completed 或缓存命中当作 Goal 验收成功。状态查询不调用模型、不修改运行产物。

workflow 的启动/续跑仍需工具许可，不因内部只读而自动绕过审批；Plan Mode 首版仅允许查询，拒绝新建或续跑。子任务仅能使用实际只读文件工具 read_file/glob/grep，禁止 Bash、写文件、Memory/Task/工作卡写入、MCP、Hooks 和递归 Agent/Workflow。只读约束在工具注册边界实施，不依赖提示词。

## 源码快照与缓存身份

review-changes 在执行前捕获宿主限定工作区的 Git HEAD、tracked 文件树及 staged/unstaged diff；候选改动须属于 tracked 文件，存在非内部 untracked 文件时明确拒绝并要求用户先纳入版本控制。源码读取不能越过工作区或从日志内部目录读取。

每次 run 记录 workflow 名称/显式版本、runtime 契约版本、规范化参数、模型/provider/base_url/生成配置和源码快照摘要。API key 等凭据不进入身份或产物。源码身份同时反映文件内容和路径新增/删除/重命名，不能只看 Git HEAD。

步骤 key 使用完整 SHA-256，覆盖 run 身份、唯一 label、prompt、输出契约、依赖步骤 key 与输出摘要。缓存独立于执行时间和完成顺序。步骤 label 重复且内容不同则报错，不静默覆盖。

resume 首先重新捕获身份；工作区、源码、流程版本、模型配置或参数任一不同，拒绝复用旧 run，要求启动新 run。首版不做跨 run 缓存或部分源码变化后的自动失效传播。执行前后、每个步骤前后核对源码摘要，发现漂移立即停止、不缓存该步骤、不宣布完成。该检测不保证抵抗恶意宿主瞬时改写再还原。

## 持久化、锁与恢复

保存到项目 .tracecoder/workflows/<run_id>/，新 UUID 使用排他创建，路径与目录禁止越界链接。manifest 保存身份及脱敏配置；events.jsonl 记录 seq、事件类型、步骤 key、状态、结果摘要和校验；state.json/result.json 用临时文件原子替换。

复用 StateLock，在整个运行/续跑期间持有 run 级操作系统锁，禁止两个进程同时续跑同一 run。其他 run 可独立存在，但首版不通过工作流 API主动并行启动。

事件顺序：run_started/resumed → step_started → step_completed/blocked/failed → run_completed/blocked/failed/interrupted。step_completed 必须在校验通过、来源记录落盘且结果持久化之后写入并 flush/fsync；缓存仅从合法 completed 事件及其完整结果建立。

中断发生在 step_started 后、completed 落盘前，该步骤状态未知，下次只重跑这个只读步骤。可能重复产生模型费用，不承诺 exactly-once。blocked/error/limit 不进入成功缓存，先前合法 completed 步骤仍可用于后续显式续跑。

只恢复完整且有合法顺序/校验的事件；末尾不完整记录保留旁档后修复尾部，完整坏记录、重复完成、身份冲突或结果哈希不符拒绝恢复。state.json 为加速快照，以 journal 为事实来源，冲突时重建，不覆盖成功产物来掩盖错误。

## 结果、日志与预算

工具返回 run_id、status、完成/缓存步骤计数、结果或阻碍以及产物位置，保持可解析的结构。源码 diff、工具来源和报告是低信任内容，不伪造用户指令或主 Agent 检查成功事件。日志应用现有脱敏规则；若脱敏改变可复用结果，该结果不可缓存，明确记录原因，不用脱敏后的语义替换原始结果。

沿用主 Agent 模型和已有 BudgetedModel，所有子任务与格式纠正都受共享调用/token 预算；缓存命中不消耗新的模型调用。journal 保存各尝试实际 usage，resume 不把历史用量清零。首版运行默认累计最多 50 次调用、100000 提供方报告 token、900 秒活跃执行时间，恢复继续累计；缓存读取不需要剩余模型调用额度。外层预算更严时优先遵守外层预算。缺失 usage 明确 unknown，停止新增模型调用，不能估算后宣称可靠成本。单次调用可能越过 token 线，下一次前拒绝。

## 验收与证据

使用真实文件、Git 仓库、日志、锁和现有脚本模型，不新增 mock/patch。必须覆盖：

1. 实际工具/CLI 新建、状态查询、正常完成，以及未知流程/非法参数拒绝。
2. 中断于第 N 步后重开恢复：前 N-1 步不再调用模型；只有未完成步骤重跑；最终报告可解析。
3. 源码、HEAD、流程版本、模型/生成配置、参数变化均拒绝旧 run；步骤依赖变化不能命中旧结果。
4. 操作系统进程级互斥、坏尾恢复、完整坏记录/结果篡改拒绝；快照损坏从合法 journal 重建。
5. blocked/error/limit 不作为成功缓存；重复恢复已完成 run 零模型调用；已完成缓存仍可在预算耗尽时读取。
6. 子 Agent 不能执行写文件/Bash/MCP/状态写入；Plan Mode、许可、来源日志、秘密脱敏及原 Goal 证据边界保持有效。
7. 覆盖当前结构化子 Agent 回归及 workflow 集成回归，执行 Ruff、compileall、差异检查。真实模型和 Docker 结果分别记录，未执行不声称通过。

实现后更新 docs/learn-claude-code-mechanism-comparison.md，记录 s16 原机制、本次保留/简化/补强、入口、验证证据与边界。

## 执行次序

先实现日志/身份/恢复并验证真实进程锁，再接结构化只读步骤和预算，然后接 review-changes 与工具/CLI，最后运行回归并更新验证与机制对照。用户审阅本文后，进一步写出具体实施计划并确认执行方式。现有工作树包含大量其他未提交改动，只处理本功能范围，不混合提交。
