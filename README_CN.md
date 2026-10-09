# ClueCoder

Previous name: TraceCoder. See [naming migration](docs/CLUECODER_RENAME.md) for configuration and historical evidence.

**可追踪执行、可恢复状态、基于证据验收的 Python 编程 Agent。**

中文 | [English](https://github.com/tongtreeopen/ClueCoder/blob/main/README.md)

ClueCoder 面向需要理解 Agent 如何修改代码、为什么停止、如何恢复任务的开发者。模型负责阅读、推理和选择工具；运行层负责工作区约束、状态记录、预算与验收证据。

我们关注的不只是生成补丁，还包括一项任务从开始到交接的完整过程：保留当前需求，记录真实操作，恢复工作进度，并区分“模型说完成”和“已有检查证据”。

本项目是基于上游 CoreCoder 的维护扩展。这些新增机制也存在于本地扩展后的 CoreCoder 工作区；ClueCoder 标识的是整理后的独立发布版本，而非重新实现的全新引擎。

## 能做什么

| 能力 | 实际行为 |
| --- | --- |
| 代码操作 | 阅读、搜索、写入和编辑工作区文件，调用受控 Bash 执行检查 |
| 目标验收 | 显式 `/goal` 模式核对工作卡证据，再由独立无工具评估器判断候选回答 |
| 会话恢复 | 持久记录会话事件与工作卡，恢复目标、步骤、阻碍和活动 Goal 预算 |
| 长期记忆 | 项目与用户两级记忆，按需读取，支持候选审阅、冲突处理和受控撤销 |
| 上下文管理 | 按预算组织历史，保留当前请求与工具调用配对，支持归档输出回读 |
| 后台执行 | Docker 后台任务可查询、取消；有超时、输出与并发限制，以及清理状态 |
| 结构化审查 | 子 Agent 返回可校验报告；只读 `review-changes` 工作流支持日志与断点恢复 |
| 独立评测 | 外部 Runner 固定任务、快照和预算，由独立 Docker Test Gate 检查补丁 |

## 快速开始

需要 Python 3.10+ 和一个可用的模型接口。当前从源码安装；尚未提供已发布的 PyPI 安装说明。

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .

$env:CLUECODER_API_KEY = "YOUR_API_KEY"
$env:CLUECODER_BASE_URL = "https://YOUR_PROVIDER/v1"
$env:CLUECODER_MODEL = "YOUR_MODEL"
```

然后进入要处理的代码仓库启动：

```powershell
cd E:\path\to\your-project
cluecoder
```

示例请求：

```text
修复 parse_config 的空输入处理，补充回归测试，执行相关检查并说明结果。
```

一次性执行需要明确批准修改类工具：

```powershell
cluecoder --yes -p "修复 parse_config 的空输入处理，补充回归测试并执行检查"
```

镜像拉取和首次启动步骤见[首次运行检查表](https://github.com/tongtreeopen/ClueCoder/blob/main/docs/GETTING_STARTED.md)。

CLI 文件工具限定于启动工作区。Bash 要求本地 Docker Linux Engine 与配置的固定 digest 镜像可用，否则拒绝执行；镜像定义见 [execution.py](https://github.com/tongtreeopen/ClueCoder/blob/main/cluecoder/execution.py)。`--yes` 不绕过工作区或执行边界。因此，没有 Docker 时不能完成依赖 Bash 的测试检查。

## 让完成有据可查

普通聊天允许模型直接返回回答。需要持续推进并检查完成条件时，显式使用：

```text
/goal 修复 parse_config 的空输入处理，补充回归测试，执行检查并登记验收证据。
```

模型通过工作卡记录验收条件、实际检查命令和对应结果事件。失败、缺失或过期证据不能通过验收；检查后的相关修改或执行可能要求重新检查。确定性证据门通过后，独立评估器再判读候选回答。

没有可执行检查的任务可以由用户显式 `/work confirm` 验收。检查命令成功只能证明该检查通过，不能保证覆盖全部需求。

Goal 默认最多自动续跑 3 次，累计活动时间 15 分钟、100,000 个已报告 token。恢复会话保留预算，不自动重放旧工具调用。普通模式不自动启用 Goal。

## 常用操作

| 操作 | 用途 |
| --- | --- |
| `/help` | 查看交互命令 |
| `/plan` | 切换只读规划模式 |
| `/goal <目标>`、`/goal`、`/goal off` | 设置、查看或关闭 Goal |
| `/work status`、`/work confirm`、`/work reopen` | 查看、验收或重新打开工作卡 |
| `/save`、`/sessions` | 查看当前会话 ID 和可恢复会话 |
| `cluecoder --resume <ID>` | 恢复会话 |
| `/memory status`、`/memory review` | 查看提取队列与待审候选 |
| `/memory accept <ID>`、`/memory reject <ID>` | 处理记忆候选 |
| `/memory undo` | 撤销最近一次受控记忆管理操作 |
| `/model <名称>`、`/reset` | 切换模型或开始新会话 |

后台任务、项目 Task 和审查 Workflow 由模型通过工具调用，不是同名斜杠命令。后台执行只在当前 CLI 进程中管理，不是跨进程常驻调度服务。Workflow 当前限定为串行只读审查，不自动修复代码。

## 配置与本地数据

| 环境变量 | 用途 |
| --- | --- |
| `CLUECODER_API_KEY` | 模型凭据；也支持 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY` |
| `CLUECODER_BASE_URL` | OpenAI 兼容端点；`OPENAI_BASE_URL` 优先 |
| `CLUECODER_MODEL` | 模型名，建议显式配置 |
| `CLUECODER_PROVIDER` | 默认 `openai`；可选 `litellm` |
| `CLUECODER_MAX_CONTEXT` | 上下文 token 预算，默认 128000 |
| `CLUECODER_MAX_TOKENS` | 单次输出上限，默认 4096 |

普通 CLI 会读取 `.env`，已有进程环境变量优先。可选 LiteLLM 后端通过 `python -m pip install -e ".[litellm]"` 安装。

项目 Skill、记忆与任务位于 `.cluecoder/`；用户级 Skill、记忆和会话位于 `~/.cluecoder/`。旧 CoreCoder 数据不会自动迁移。日志与记忆可能包含项目内容，应按项目的数据要求管理。

## 评测与开发

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
python -m ruff check cluecoder tests examples
```

固定评测使用 `benchmarks/cluecoder-baseline-v1/` 与 `benchmarks/cluecoder-holdout-v1/` 两套任务。Agent 工作区不放入 Gate 测试或参考补丁；报告保留调用、用量、补丁与 Gate 结果。配置、命令和评分限制见[开发集协议](https://github.com/tongtreeopen/ClueCoder/blob/main/docs/evaluation-baseline.md)和[留出集协议](https://github.com/tongtreeopen/ClueCoder/blob/main/docs/evaluation-holdout.md)。

这是实验性项目。首发 Windows/Python 3.13 全量回归为 539 通过、47 跳过、0 失败；未进行 Docker 或真实模型端到端验证。ClueCoder 首发版本为 0.1.0，采用独立版本序列，维护者为 Tong ShuKai。当前检查见[首发验证](https://github.com/tongtreeopen/ClueCoder/blob/main/docs/validation/2026-10-09-first-release.md)，来源与发布状态见[发布记录](https://github.com/tongtreeopen/ClueCoder/blob/main/docs/RELEASE.md)。`cluecoder --demo` 使用内置脚本模型和生成的 Python 断言，无需 API Key 或 pytest；普通 CLI Bash 仍要求 Docker。历史真实模型和 Docker 成绩仅适用于原始源码快照。验证容器内的候选程序可以读取 `/gate`，评测不声称实现对抗性隐藏测试隔离。

## 代码与文档

```text
cluecoder/
  agent.py                 模型与工具循环
  cli.py                   CLI 装配与交互
  context_assembler.py      模型输入组织
  session_journal.py        会话事件日志
  work_state.py / goal.py   工作状态与目标验收
  memory*.py / tasks.py     长期记忆与项目任务
  execution.py              Docker 执行
  background.py             后台任务生命周期
  workflow*.py              只读审查运行时
  tools/                    模型可调用工具
  eval/                     独立评测 Runner
```

- [Skill、Memory 与 Task 使用说明](https://github.com/tongtreeopen/ClueCoder/blob/main/docs/skill-memory-task-usage.md)
- [机制来源、改进与取舍](https://github.com/tongtreeopen/ClueCoder/blob/main/docs/learn-claude-code-mechanism-comparison.md)
- [设计说明](https://github.com/tongtreeopen/ClueCoder/tree/main/docs/superpowers/specs/)
- [历史验证摘要与来源](https://github.com/tongtreeopen/ClueCoder/blob/main/docs/HISTORICAL_VALIDATION.md)

## 来源与许可

ClueCoder 基于 [Yufeng He 的 CoreCoder](https://github.com/he-yufeng/CoreCoder) 扩展，并参考 [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 的机制示例。我们的增量集中于证据验收、可恢复状态、来源可审阅的记忆、预算感知上下文、受控后台执行、结构化审查与独立评测；具体来源和边界在机制对照文档中记录。

项目保留原作者版权及 [MIT License](https://github.com/tongtreeopen/ClueCoder/blob/main/LICENSE)。
