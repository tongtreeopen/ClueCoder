# Agent 失败、会话/Task 并发与 Memory 真实模型验证

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 tracecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

日期：2026-10-01。代码 HEAD：`f03bd9b6f7dd0e5dc96609436c1de3b6b27106a8`。
当前分支：`codex/skill-memory-task`；工作区原有 50 项未提交变更。
本轮增加验证文档，未修改运行时代码、固定任务、参考答案或 Gate。
脚本和全部临时会话/记忆在 `E:/eval-runs/corecoder-boundaries-20261001`，未使用真实用户记忆库。

## 1. 固定基线失败定位

沿用前次两组各两轮的 8 题 A/B 轨迹；核验全部 32 个任务的归档产物哈希，并重放其中三次失败。
任务、快照、隐藏 Gate 和固定 Docker image digest 均保持原值。重放不调用模型，不改变原始成绩。

| 原始失败 | 重放证据 | 定位 |
| --- | --- | --- |
| 基线第 2 轮 `retry_backoff` | Docker Gate 再次出现 `OverflowError`；原参考补丁通过 | 补丁使用 `min(initial * (2 ** index), cap)`，先发生整数转浮点溢出，再考虑 cap。Agent 自测仅覆盖少量重试，遗漏 1025 次的边界 |
| 候选第 2 轮 `retry_backoff` | 同一表达式、同一异常；原参考补丁通过 | 同类边界遗漏在两个版本中都出现 |
| 候选第 1 轮 `config_merge` | 最终补丁含 `solution.py` 和 `test_solution.py`；诊断性功能 Gate 通过 | 用户只允许改 `solution.py`。事件 33 明说为保留可复现检查而留下测试文件，最终触发 runner 的 forbidden-path 检查 |

`config_merge` 的诊断性功能通过不覆盖原始 `agent_error`，也不修正成功率。
Runner 的路径拒绝发生在产物导出后，入口见 [runner.py](../../tracecoder/eval/runner.py)。

四个历史 run 中均没有 `model_view_compaction` 事件；候选最大单次提供方 prompt 为 5,935 tokens，远低于默认输入预算 111,104。
因此上次 token 增长不能用来判定预算触发压缩的节省效果。近期事件裁剪/导航视图仍在工作，不能由“没有压缩事件”推断两个版本上下文完全一致。
该小样本只支持：大数边界验证不足、最终补丁违反路径约束。没有证据将这三次失败直接归因于预算压缩。

原始成绩保持基线 15/16、候选 14/16；本轮没有重新跑新的正式基线成绩。

复现脚本：baseline_replay.py（历史本地产物：`E:/eval-runs/corecoder-boundaries-20261001/baseline_replay.py`，未随发布提供）。
原始诊断：baseline_replay.json（历史本地产物：`E:/eval-runs/corecoder-boundaries-20261001/baseline_replay.json`，未随发布提供）。
首次重放曾未得到预期 Gate 结果，随后同一补丁的独立重放复现异常；未把首次基础设施/断言失败算成 Agent 新失败。

## 2. 会话和 Task 并发边界

使用实际文件系统、线程和 Windows spawn 进程；没有替换或模拟 TaskStore / SessionJournal 的读写。

| 场景 | 本次结果 |
| --- | --- |
| 同一个 TaskStore，8 线程抢同一任务，10 轮 | 10/10 轮恰好一个领取者 |
| 同一工作区分别创建 TaskStore，8 线程，10 轮 | 10/10 轮出现多个成功领取者；每轮 3–6 个 |
| 同一工作区四进程领取，10 轮 | 7/10 轮出现多个成功领取者；其余 3 轮碰巧只有一个 |
| 同一个 SessionJournal，8 线程，各写 8 条 | 64 条事件完整，序号连续，重新打开成功 |
| 四进程同时写同一 session，各写 8 条 | 四个写者均正常结束；重新打开失败：`Invalid session event sequence or kind` |
| 四进程分别写独立 session，各写 8 条 | 4/4 会话重新打开成功，每会话 8 条 |

根因是锁属于 Python 对象，而不是持久化状态：[TaskStore](../../tracecoder/tasks.py) 的 `RLock` 只保护该实例。
临时文件替换保证单次文件替换，不保证 `get -> 检查 pending -> 写入 owner` 的完整领取操作互斥。
[SessionJournal](../../tracecoder/session_journal.py) 的事件序号取自各自内存 `events` 长度；独立写者会使用重复序号，实例级锁无法协调其他实例/进程。

已验证的支持范围是共享单实例中的线程写入、彼此独立的会话。
共享 TaskStore 状态的独立实例/进程，以及同一会话的多个写进程，尚不具备正确协调；正常返回不能视为安全。
本轮只验证，不修复产品行为。下一步应优先让同一 session 的第二个写进程被明确拒绝，并使 Task 领取成为真正跨实例/进程互斥的操作。

脚本：concurrency_probe.py（历史本地产物：`E:/eval-runs/corecoder-boundaries-20261001/concurrency_probe.py`，未随发布提供）。
每次领取结果、最终 owner、会话读回结果：concurrency_probe.json（历史本地产物：`E:/eval-runs/corecoder-boundaries-20261001/concurrency_probe.json`，未随发布提供）。

## 3. Memory 真实模型质量

模型：`deepseek-v4-flash`，OpenAI-compatible provider，temperature=0，单次输出上限 4,096。
沿用 env 凭据，只在进程内使用。测试资料均为合成样例，命名、事实和判定规则在调用模型前落盘。
每组做两轮；读回第二轮颠倒 on/off 顺序。测试是真实模型与真实 Store/Queue/Agent，不是脚本模型。

### 3.1 默认 ExtractionQueue 提取

10 类样例各两轮，按事先设定的小样本验收规则通过 **13/20**。
这不是通用准确率，也不是 13/20 条候选正确率；一个场景包含“应提取/应为空/应避免重复/应先审阅”等不同要求。

| 场景 | 两轮结果 | 解释 |
| --- | --- | --- |
| 持久用户偏好、项目版本 | 各 2/2 | 提取了正确事实和作用域，记录来源事件 |
| 明确临时请求、虚构凭据、工具注入、用户引用的非偏好指令 | 各 2/2 | 未保存也未产生待审候选 |
| 助手猜测数据库 | 严格“应为空”规则 1/2 | 一次保存了用户原文“数据库尚未确认”。没有保存助手的 PostgreSQL 猜测；此项是长期价值判断歧义，不能称为事实幻觉 |
| 长消息尾部的项目版本 | 0/2 | 版本在 1,200 字符后；提取输入按事件仅取前 1,200 字符，模型根本看不到该事实 |
| 已有中文偏好的同义重述 | 0/2 | 不同 name/不同原文的同义偏好被再次自动保存 |
| 默认语言从中文更正为英文 | 0/2 | 英文更正以新 name 自动保存，旧中文记忆仍在，未进入冲突审阅 |

源码入口：[ExtractionQueue._process_job](../../tracecoder/extraction_queue.py)。
输入只含会话证据，没有现有记忆的语义索引；去重依赖正文规范化后的精确相等，冲突依赖生成 name 的精确匹配。
因此同义重述和更正可以绕过 existing 判断，成为新的自动保存记录。
这些问题在两轮均复现，修复重点应是超长来源的分段提取，以及相关旧记忆的重复/更正审阅。

固定样例：memory_probe_manifest.json（历史本地产物：`E:/eval-runs/corecoder-boundaries-20261001/memory-valid-v2/memory_probe_manifest.json`，未随发布提供）。
提取记录、待审队列和来源：memory_probe_results.json（历史本地产物：`E:/eval-runs/corecoder-boundaries-20261001/memory-valid-v2/memory_probe_results.json`，未随发布提供）。
该文件中的首次读回 on/off 对照不用于最终比较，原因见下一节。

### 3.2 Agent 按需读回，匹配 CLI reading 开关

初始探针在 reading off 时移除了工具 schema，模型会返回原始 DSML 调用文本。这引入了工具协议差异，不能公平比较 Memory。
最终重跑保留双方相同的三种 memory tool schema；off 设置 `enabled=False` 并隐藏索引，符合 CLI `/memory reading off` 接线。
Agent 使用真实 SessionJournal、WorkStateStore、ContextAssembler 以及 list/search/read memory 工具。未启用旧版自动 begin_turn recall。

| 检查 | Reading on | Reading off |
| --- | --- | --- |
| 命令、端口、项目代号、含恶意附文的端口：共 8 次仅存在于正文的事实查询 | 人工核对 8/8 答对事实；其中 2 次额外说明导致严格 JSON 失败 | 8/8 返回 UNKNOWN，无凭空提供事实 |
| 无关查询：2 次 | 2/2 返回 UNKNOWN | 2/2 返回 UNKNOWN |
| 当前请求覆盖旧验证命令：2 次 | 2/2 使用当前命令 | 2/2 使用当前命令 |
| 完整回答符合指定 JSON 格式 | 10/12 | 12/12 |

两个 memory injection 样例均返回正确端口，并明确拒绝记忆中的恶意指令；多余说明违反“只返回 JSON”，不能算格式通过，也不能误报为执行恶意指令。
项目代号查询仅装配当前项目 Store；另一个项目有不同代号，未出现跨项目串值。这不是任意文件访问或完整 CLI 隔离的证明。

最终配对用量：on 为 25 次调用 / 24,164 input / 1,958 output；off 为 24 次调用 / 21,434 input / 1,860 output。
Memory 在这组资料查询中提供了信息，也有额外成本；不能据此宣称编码任务成功率提高或总体 token 降低。

最终配对脚本：memory_paired_probe.py（历史本地产物：`E:/eval-runs/corecoder-boundaries-20261001/memory_paired_probe.py`，未随发布提供）。
固定协议：manifest.json（历史本地产物：`E:/eval-runs/corecoder-boundaries-20261001/memory-paired-v3/manifest.json`，未随发布提供）。
原始回答和用量：results.json（历史本地产物：`E:/eval-runs/corecoder-boundaries-20261001/memory-paired-v3/results.json`，未随发布提供）。
所有有效模型调用完整记录 prompt、响应与提供方 usage；v2 共 58 次/50,579 tokens，v3 共 49 次/49,416 tokens，usage 全部已知。
v2 总用量包含弃用的首组工具 schema 对照，不能混入 v3 配对成本。

### 3.3 验证范围和作废尝试

首个辅助记录器错误地调用了 LLMResponse 的 message 属性，产生的队列失败全部作废，原因保存在外部 `memory_probe_invalid_attempt.json`；共 11 次已完成提供方请求，未混入质量评分，也未混入有效用量。
本轮没有验证 Memory consolidation 的事实保留率、长会话端到端效益、模型变化后的泛化、所有注入/敏感信息形态或并发 Memory 写入。
两轮、合成样例、单模型只能作为定位信号。相关运行时代码哈希复核一致。
对 515 个归档文本文件扫描 env 中的真实 key/token 值，匹配数为 0。

## 4. 离线回归和下一步

实际执行：

```powershell
py -3.13 -m pytest tests/test_tasks.py tests/test_session.py tests/test_session_journal.py -q
# 41 passed in 4.88s
py -3.13 -m pytest tests/test_memory.py tests/test_memory_scope.py tests/test_memory_journal.py tests/test_memory_workflow.py tests/test_cli_memory.py tests/test_extraction_queue.py -q
# 62 passed in 14.49s
```

103 项通过与本次并发失败并不矛盾：原并发用例主要共享同一个 Store/Journal 实例，没有覆盖这次发现的独立实例/跨进程边界。
未改运行时代码，因此本轮没有重复上次 460 项全量回归。

建议后续按证据逐项处理：

1. 先修同一 session 多写者拒绝和 Task 跨进程领取互斥，保留本次多写者/多领取者探针为回归入口。
2. 再修 Memory 长消息截断、同义重复与更正冲突，沿用本轮冻结样例复查，随后增加未用于修复的留出样例。
3. 最后改 Agent 验证行为：补大数/边界检查，临时验证使用内联命令或临时目录，保持固定基线和 Gate 原样再比较。
