# TraceCoder 独立评测基线设计

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 cluecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

日期：2026-09-26。状态：设计规格已获用户确认；尚未实现或运行基线。

## 目标与完成定义

为 TraceCoder 的单 Agent 编码任务建立可复现的初始评测。每个任务固定题目、初始文件快照、Agent 配置、预算、Docker 镜像与独立 Test Gate。Runner 保存完整运行轨迹、最终补丁和逐题判定，再从真实运行计算成功率。第一版使用 8 个本地 Python 修复任务，每题运行一次；这是初始样本，不代表广泛任务的总体成功率。

成功条件：任一人用同一任务清单、Agent 版本、模型配置和镜像摘要可重跑同一协议（随机模型输出不保证逐字相同）；Agent 无法看到或修改验收测试；Docker 或模型不可用时明确标记未运行；模型自称完成、Todo 完成和 `/goal` 状态均不能代替 Test Gate 的结果。报告同时给出分子、分母、失败类别和环境信息，不补造缺失运行。

## 当前代码边界与方案

现有严格 CLI 在 [`_cli_context`](https://github.com/tongtreeopen/ClueCoder/blob/f48ffef08762212d4f080b18442c963d35fdc339/tracecoder/cli.py) 中绑定工作区文件工具和 `DockerExecutor`；Bash 仅在本地固定镜像可用时启用。[`DockerExecutor`](https://github.com/tongtreeopen/ClueCoder/blob/f48ffef08762212d4f080b18442c963d35fdc339/tracecoder/execution.py) 用 `--network=none`、有限内存/CPU、非 root 用户和单工作区 bind mount 执行命令。[`SessionJournal`](https://github.com/tongtreeopen/ClueCoder/blob/f48ffef08762212d4f080b18442c963d35fdc339/tracecoder/session_journal.py) 保存带序号的 `events.jsonl`。这些是可复用入口，但 CLI 目前没有评测任务清单、每运行独立状态目录、全局预算控制或外部 Test Gate。现有 `/goal` 完成门是 Agent 内部判断，不能充当独立评测结果。

选择**外部 Python runner 调用现有严格 CLI**。Runner 负责快照、预算、容器 Gate、归档和汇总；Agent 保持自己的工具循环。直接构造 Python `Agent` 虽易于采集指标，却会绕过严格 CLI 的默认装配，因此不作为第一版入口。只跑仓库自身 pytest 也不能衡量修复任务成功率。第二个 Agent 适配器留待此协议稳定后再接入，不合并两套 Agent 循环。

## 固定任务与运行协议

评测集是版本化 manifest 加 8 个自包含 Python fixture。每题含稳定 ID、任务文本、初始快照的 SHA-256、可见文件、允许改动路径、固定验收命令，以及只供 Gate 使用的测试目录。初始快照必须在 Gate 下失败，维护者的参考修复必须通过；这两项预检结果写入评测集版本。第一版不用会在运行时下载依赖或变动的外部仓库任务。

一次运行按以下顺序进行：

1. 校验 manifest、快照哈希、Agent 版本和镜像摘要。正式指标要求干净的 Agent Git 修订；未提交工作树只允许冒烟运行，并在结果中标为不可复现。
2. 把初始快照复制到新工作区。每次运行使用独立的 TraceCoder 会话、记忆和输出目录，避免上一个任务的上下文污染。Agent 可见的工作区不含 Gate 测试与答案。
3. 在宿主启动 TraceCoder 一次性严格 CLI；Agent 的 Bash 命令仍由现有 Docker 执行器运行。固定模型名、提供方、温度、提示词、镜像和预算；密钥只通过运行时环境传递，不进入轨迹或报告。第一版固定为普通 `-p` 模式、不启用 `/goal`；以后比较 `/goal` 时，两组须从相同快照分别运行，不混算成功率。
4. Agent 退出或预算到达后，Runner 冻结工作区和补丁，核对改动是否都在任务允许路径内。随后启动**独立 Docker 容器**，只读挂载候选工作区和独立 Gate 测试，执行 manifest 指定的命令。Gate 不接受 Agent 提供的命令、测试或成功声明，也不把测试目录挂载到 Agent 容器。越界改动即使测试通过也不计成功。
5. 保存逐题结果及汇总。测试输出、退出码、超时、镜像 ID、任务/快照哈希和实际执行命令必须可查；Gate 失败后不自动让 Agent 修补，以保持一次运行的预算与语义固定。

Runner 只允许预先登记的任务与命令，不运行模型生成的 Gate 命令。Agent 容器和 Gate 容器均禁网；Gate 使用固定镜像摘要、非 root 用户、资源上限和只读挂载，运行时临时文件写入 tmpfs。缺少镜像、Docker daemon 或挂载能力时整次运行标记为 `setup_error`，不回退到宿主 shell，也不把跳过的测试计为通过。

## 预算、结果与轨迹

每题固定 Agent 进程墙钟上限 15 分钟、最多 50 次模型调用，以及 100,000 个**提供方已报告** token 上限。Runner 对进程施加墙钟截止，超时后终止并核验相关 Docker 容器已清理；token 用量须从真实模型响应累计，在下一次模型调用前检查。若提供方不返回可靠用量，记录 `token_usage_unknown`；该次只能作冒烟结果，整组不发布正式成功率，因为统一预算无法核验。墙钟与调用次数仍是硬上限。估算价格只作观察指标。Gate 有单独的 120 秒超时和资源上限，不占 Agent 的 15 分钟。

每次运行目录至少包含：不可变 `run.json`（任务、配置、预算、代码/镜像/快照标识）、从 `SessionJournal` 导出的 `events.jsonl`、Agent 标准输出/错误、最终 patch、Gate 输出与 `result.json`。敏感字段沿用会话日志脱敏，并对额外日志做相同处理；报告不保存 API Key。`result.json` 区分 `pass`、`test_fail`、`agent_limit`、`agent_error`、`gate_error` 和 `setup_error`，记录耗时、模型调用数、已报告 token、退出码及产物哈希。所有预定任务都出现在报告中；成功率写作 `pass / scheduled`，并同时列出实际完成 Gate 的数量和各失败类别。若没有有效 Docker 运行，显示“未测得成功率”，不显示 `0%`。

## 验证与非目标

实现验证先用脚本模型和真实临时工作区覆盖快照隔离、预算、日志脱敏、任务缺失及 Gate 判定；再用真实 Docker 预检镜像、只读 Gate、隐藏测试不可见、失败退出码、超时和无宿主回退；最后才对固定 8 题运行真实模型并发布逐题结果。已有 Docker 测试会在镜像不可用时跳过，因此最终报告必须显示 Docker 预检与真实容器运行证据，不能只引用 pytest 总通过数。当前本机 Docker daemon 未运行，设计审阅阶段没有进行 Docker 实测。

第一版不做自进化、策略候选晋升、外部大型仓库基准、第二 Agent 后端、自动选择测试或统计显著性结论。若后续接入其他 Agent，新增适配器只负责运行与轨迹转换，任务、快照、预算、Gate 和计分仍由同一外部 runner 所有。

与 learn-claude-code 的机制差异需要在实施后更新 [`机制对照`](../../learn-claude-code-mechanism-comparison.md)：说明独立 Gate 与现有 Goal Stop Gate 的关系、真实入口、测试证据和未实现边界；本设计不能写成已经落地的能力。


## 2026-10-05 当前实现边界补充

原设计的“Agent 无法看到验收测试”仅在 worker 阶段成立。Gate 阶段候选程序与测试共享容器且可读取 /gate；当前实现不能证明对抗式隐藏测试隔离，也不能据此宣称达成该完整验收项。只读挂载保护测试文件写入，不能保护其保密性。

正式评测仍拒绝脏工作树；单独的不可复现冒烟模式未实现，不视为已交付。Docker 镜像或 daemon 缺失时，真实 Docker 回归用例跳过；跳过不计为容器验证通过。
