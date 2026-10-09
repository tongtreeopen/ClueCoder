# P0：验证基线与工作区边界设计

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 cluecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

日期：2026-09-24
修订：2026-09-25（所有 CLI 默认严格边界）
状态：用户已确认；Windows 目标平台实施并通过 P0 计划验收（2026-09-25）

## 目标与范围

这是 A 路线的第一阶段，不是自进化实现。目标是让后续 Memory、后台任务、Workflow 和评测运行建立在可信的回归结果与明确的执行边界上。用户已选择**所有 CLI 会话默认严格限制在启动工作区**，包括交互、一次性提示、恢复会话和 `--yes`；不是仅自动化 Harness 启用。成功标准是：支持的平台上全量测试通过；文件工具不会越出工作区；CLI 不会把普通 `shell=True`、Hook 或 MCP 工具误称为安全沙箱；上下文压缩仍保持消息结构有效。每项实际变更都要补充与 learn-claude-code 对应机制的差异记录。

对照来源为 learn-claude-code 根目录的 [s03 Permission](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s03_permission/code.py)、[s04 Hooks](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s04_hooks/code.py)、[s08 Context Compact](https://github.com/shareAI-lab/learn-claude-code/blob/0dcafa2ae053a1ddd6a72f265431104b08a5aa13/s08_context_compact/code.py)。采用机制而非逐行复制；TraceCoder 的执行内核仍是 `Agent.chat()`。

## 已核实的现状

- 当前 [`Permission`](https://github.com/tongtreeopen/ClueCoder/blob/f48ffef08762212d4f080b18442c963d35fdc339/tracecoder/permissions.py) 将工具分为只读与需同意；Plan Mode 在许可之前阻止修改。`--yes` 可放行需同意的工具，但“用户同意”不是工作区隔离。
- [`read_file`、`write_file`、`edit_file`、`glob`、`grep`](https://github.com/tongtreeopen/ClueCoder/blob/f48ffef08762212d4f080b18442c963d35fdc339/tracecoder/tools/) 各自解析路径，没有共享的工作区约束；[`bash`](https://github.com/tongtreeopen/ClueCoder/blob/f48ffef08762212d4f080b18442c963d35fdc339/tracecoder/tools/bash.py) 用 `shell=True` 和危险命令正则，指定 `cwd` 不能阻止命令访问工作区以外。MCP、用户 Hook 也可能运行外部命令；子 Agent 继承父 Agent 工具。
- [`ContextManager`](https://github.com/tongtreeopen/ClueCoder/blob/f48ffef08762212d4f080b18442c963d35fdc339/tracecoder/context.py) 已有工具输出裁剪、旧消息摘要、紧急压缩，不需换成 s08 的独立循环，但要回归工具调用/结果成对性与近期消息保留。
- 2026-09-24 Windows 当前工作树执行 `py -3.13 -m pytest tests -q`：225 passed、7 failed。两项为 README 行数和 `.env` 影响默认配置测试；五项 Hook 测试使用 Windows `cmd.exe` 不支持的 `sh`、`cat`、`sleep` 等 POSIX 命令。失败分类仅说明当前复现状态，不预断每项修法。
- 工作树已有与本设计无关的未提交改动：`tracecoder/llm.py`、`tests/test_core.py`、`requirements.txt`，以及尚未跟踪的机制对照文档。实施时不得覆盖或顺手提交这些内容。

## 方案取舍

1. **已选择：所有 CLI 会话默认严格限制。** CLI 启动时固定解析工作区根目录，不提供本阶段的 CLI 绕过开关。REPL、`-p`、`--resume`、`--yes` 以及从 CLI 进入的子 Agent 共用策略；`--demo` 若装配真实工具也必须遵守。优点是交互与自动化没有两套相反的默认边界。代价是会破坏现有跨工作区绝对路径用法，且没有可靠隔离后端时 CLI Bash 不可用；需要在发布说明中醒目标注。用户通过 Python API 直接自行构造 `Agent` / `Tool` 不属于“所有 CLI”，其策略必须由调用方显式传入，不能声称自动受控。
2. 仅自动化受控、交互兼容。兼容代价较小，但交互 CLI 仍可越界；用户已明确不选。
3. 只在提示词或 Hook 中声明“不要越界”。不能强制约束工具，不满足 P0；不采用。

## 设计

### 1. 验证基线

将 7 项失败逐项归因，保留原测试意图：README 数字与实际代码一致；默认配置测试不被本地 `.env` 污染，同时不改变生产配置优先级；Hook 测试使用可在 Windows 与 POSIX 上运行的命令/脚本，验证阻止、观察、超时、并发及子 Agent 继承。测试修复不能通过无条件跳过或删除断言获得“全绿”。验证命令为目标解释器的 `python -m pytest tests -q`、`python -m ruff check .`、`python -m compileall -q tracecoder`。Windows 是本机实测目标；Linux 只有在实际运行相应 CI 或环境后才标为已验证。

### 2. 文件工具的统一策略

策略对象以 CLI 启动时解析的工作区绝对路径为根，对 `read_file`、`write_file`、`edit_file`、`glob`、`grep` 提供统一的路径解析与越界判定。读写、搜索起点和搜索结果都要检查；绝对路径、`..`、符号链接及 Windows junction 不能绕过根目录。工作区内的绝对路径可以继续使用。拒绝时给模型普通错误结果，不访问目标内容，不创建目标目录或文件。保留原工具名和 JSON 参数；CLI 装配层为每次运行绑定同一策略，不能继续使用无策略的全局工具实例。Python API 的直接构造方式可以保留，但须明确其调用方负责传入策略。子 Agent 继承同一策略，不允许降级。

路径检查是应用层防误用与已知越界路径拦截，**不是抵御恶意并发进程切换符号链接的 OS 沙箱**。对这一点不能在 README 或测试结果里作更强承诺。

### 3. Bash 与旁路能力

`cwd` 检查和危险命令正则不足以约束任意 shell；Python 官方文档也把显式调用 shell 的输入处理责任交给应用。[参考](https://docs.python.org/3/library/subprocess.html#security-considerations) **所有 CLI 会话**在没有已验证隔离执行后端时，对 `bash` 拒绝执行并给出明确原因；不得因 `--yes`、Plan Mode 切换或模型建议而绕过。这意味着 P0 若尚未交付可用的隔离后端，CLI 的代码执行能力会明显下降，不能把此状态包装成“功能完整”。

P0 要作为“CLI 严格且仍可用于编程”的完整交付，至少需要在目标平台验证一个可用的隔离执行后端；否则只能报告为“文件边界已做、Bash 暂不可用”的部分交付，不能直接进入依赖 Bash 的下一阶段。接入容器/受限执行器时，须单独验证工作区以外的宿主文件系统不可写、挂载范围、网络、进程权限、超时与退出清理；后端缺失或失败时关闭而非退回宿主机 `shell=True`。Docker 官方说明，错误的 bind mount 仍能让容器修改宿主文件，容器本身也不是无条件的安全保证。[参考](https://docs.docker.com/engine/security/) 本机仅确认 Docker CLI 存在，未确认 Docker daemon 可用；本设计不授权自动安装或启动 Docker。

所有 CLI 模式默认不暴露可执行任意命令的用户 Hook 与未知副作用的 MCP 工具；需要时只能通过显式工具白名单与同等隔离后端加入。受限文件工具不依靠 Hook 强制执行；Hook 的现有“运行失败即跳过”语义不能担当安全策略。`Permission` 继续负责用户同意，`WorkspacePolicy` 负责确定性文件边界，两者不能互相替代。未来自进化候选的真实执行隔离仍须在外层 Harness 单独实现，不能只依赖本阶段路径检查。

### 4. 上下文压缩回归

不改 s08 对应模块的产品行为，先增加或确认离线回归：长工具结果裁剪后，工具调用与结果仍成对；旧消息摘要后近期轮次仍可读；紧急压缩不生成孤立工具结果；压缩失败时 Agent 可用明确结果继续或停止。只有测试发现实际缺陷时才提出局部修复，避免 P0 顺手重写压缩算法。

## 验收与证据边界

- 全量测试在当前 Windows 目标解释器下 0 failed；Ruff 与 compileall 通过。若计划声明 Linux 支持，需另有 Linux 实测结果。
- 所有 CLI 启动路径默认绑定工作区策略；正常工作区内路径、绝对越界、`..`、符号链接、junction、搜索结果越界和写入前无副作用均有测试。子 Agent、并行调用、`--resume` 与 `--yes` 不能绕过策略；Python API 的直接构造边界另行说明。
- 没有隔离执行后端时，CLI Bash、用户命令 Hook 和未列白的 MCP 工具不能在宿主机偷偷运行；`--yes` 不改变此约束。若因此尚无法执行普通编程任务，必须在交付报告中列为明确的功能限制，而非宣称完整可用。
- P0 完整交付需要至少一个目标平台上的 Bash 隔离执行示例通过正常命令、越界访问、超时、退出清理测试；如后端不可用或需要新安装/外部配置，先报告并请求用户决定，不降级宿主执行。
- 以上使用离线测试与无需 API Key 的示例验证。真实模型是否正确选择工具、容器是否可用、恶意本地进程竞争路径及跨平台安全性，未验证前不得声称通过。
- 完成后更新 [`learn-claude-code-mechanism-comparison.md`](../../learn-claude-code-mechanism-comparison.md)：分别记录 s03/s04/s08 的来源、TraceCoder 实际行为、取舍、测试证据和仍未覆盖的边界。

## 非目标与后续依赖

P0 不实现 Memory 自动提取、后台任务、Cron、团队协作、Workflow、Goal Loop 或自进化晋升；也不把 Docker/WSL 的存在当作已经具备安全容器。P1–P7 使用这里建立的基线；任何需要执行不可信候选代码的阶段，必须另验隔离执行后端，不能沿用宿主机 Bash。
