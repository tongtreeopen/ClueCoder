# s16 Workflow Runtime 验证记录

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 tracecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

已批准设计和计划；在当前工作区串行实现。分支 `codex/skill-memory-task`，HEAD `f03bd9b`；本轮未提交或推送，保留原有未提交修改。

## 使用

CLI 已注册两个工具，可让 Agent 调用：

```json
{"name":"review-changes","args":{}}
```

上面是 `workflow` 的参数。返回 `run_id` 后，续跑调用 `workflow`，增加 `resume_from_run_id`；查询调用 `workflow_status`，参数为 `{"run_id":"返回的 UUID"}`。启动和续跑受权限控制，Plan Mode 只允许查询。

运行目录为 `.tracecoder/workflows/<run_id>/`，保存 manifest、顺序事件日志、状态快照和最终结果。宿主固定流程串行审查 correctness、compatibility、tests，验证发现，再做精确字符串去重。子步骤只允许 read_file、glob、grep；读权限限定为捕获的 Git 跟踪文件，排除内部状态目录。

## 保留、简化与新增

保留 s16 的宿主固定流程、步骤结果记录与完成步骤恢复；简化为一个只读串行流程和空参数，不接受动态脚本、DAG、并发或嵌套工作流。复用结构化 AgentTool，但使用独立只读 Agent，子历史不混入主会话。

新增完整运行锁、带校验链的日志、末尾残片修复、可重建快照和原子写入。恢复身份绑定仓库路径、HEAD、跟踪文件内容、暂存及未暂存 diff、流程版本、模型配置、参数和预算。步骤前后与最终返回检查源码漂移；只有完整有效的步骤可缓存。

预算跨恢复累计：默认 50 次模型调用、100000 已报告 tokens、900 秒活动时间，纠正调用也计费。完成缓存恢复零模型调用；请求中断导致 usage 未知时拒绝继续发请求。嵌套模型调用同时计入外层显式 Goal 和 usage 回调，包含 workflow 的工具批次在调用线程串行执行。

## 验证

真实临时 Git 仓库、真实跨进程锁、文件读写和本地 HTTP SSE 服务验证日志恢复、完成/部分缓存、身份失效、漂移、未知 usage、只读边界、权限、Plan Mode、外层 Goal 和 CLI 子进程新建/查询/续跑。脚本模型验证协议，不证明真实模型审查质量。

最终功能回归：`py -3.13 -m pytest tests/test_workflow_journal.py tests/test_workflow_runtime.py tests/test_workflow_cli.py tests/test_structured_subagent.py -q --tb=short`，42 passed（122.41 秒），原始输出 `workflow-final-focused.txt`。

最终全量：`py -3.13 -m pytest -q --tb=short`，503 passed、47 skipped、33 failed（315.52 秒），原始输出 `workflow-final-full.txt`。32 个失败是旧测试工具在仓库目录初始化，却访问临时工作区；已用隔离源码副本关闭本次 Agent/CLI/权限接线复现同名失败。另一个 demo 全量运行时 pytest 扫描到 C:\Documents and Settings 并遭遇 Windows 权限错误；补齐 examples 后单独运行 4 passed，未确认其全量顺序依赖的根因。证据为 `workflow-before-feature.txt` 和补齐 examples 后的 `workflow-before-feature-demo.txt`；这不是干净 Git HEAD 的回归基线。

独立审查发现两个 Important 问题：忽略文件可读但没有进入身份，以及外层 Goal 漏记 usage；已分别重现并修复，审查复查 10 passed，无剩余 Critical/Important。

Ruff：`py -3.13 -m ruff check tracecoder tests examples` 通过；warnings-as-errors compileall 和 `git diff --check` 通过。全目录 Ruff 会扫描既有生成测试目录中的故意无效样例，因此源码检查明确限定为产品、测试和 examples。

## 边界

要求仓库根目录且存在 HEAD；非内部目录的未跟踪文件会阻止启动，Git 忽略文件不可审查。过大 diff 拒绝执行，避免静默遗漏。日志校验防损坏，不提供针对恶意重写者的签名保护；只读策略不是 OS 沙箱。运行期间没有后台服务、异步步骤、自动修复或副作用步骤；模型报告和缓存不构成 Goal 验收或外部 Test Gate 证据。未运行真实模型 A/B 或 Docker 工作流评测，跳过项没有算作通过。
