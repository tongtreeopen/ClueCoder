# Agent、持久状态和 Memory 修复验证（2026-10-01）

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 tracecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

本次对应 [前一轮验证](2026-10-01-agent-session-task-memory.md) 定位的三组问题。修改运行时代码及回归用例，保留此前移除 Verified Mini 等工作区修改；没有恢复这些评测入口，也没有推送或提交主工作区。

## 1. 修复入口与边界

### Session / Task

- 新增 [StateLock](../../tracecoder/state_lock.py)：线程可重入锁加操作系统文件锁，Windows 使用 `msvcrt`，POSIX 使用 `flock`。同一进程的独立实例与不同进程共享锁文件；锁随句柄关闭或进程退出释放，忙等上限 10 秒，超时明确报错。
- [TaskStore](../../tracecoder/tasks.py) 把读取、检查、领取和写回放在同一个项目锁内，防止两个实例同时看到 pending。沿用原有原子替换、任务 ID 和工作区路径检查；没有新增数据库。
- [SessionJournal](../../tracecoder/session_journal.py) 在锁内读取/修复日志，追加前核对已经读取的日志字节数。过期写者明确报错，必须重新打开；追加序号不会来自过期实例。metadata 更新也在锁内重读，使用独立临时文件原子替换。

这是操作级互斥和过期写者拒绝，不是整个 CLI 会话的终身租约、多 Agent 协调或跨文件事务。重新打开后可以成为新写者；不能保护绕开 API 的直接文件修改。Task 使用项目级串行锁，吞吐受整个 Store 限制。

### Memory

- [ExtractionQueue](../../tracecoder/extraction_queue.py) 把每条消息完整分成 1,200 字符片段，再组成最多约 12,000 字符的来源批次。保留事件 ID 和偏移，避免原先只读取开头造成尾部事实不可见。
- 提取时提供最多 6,000 字符的已有记录目录，要求模型标注 `new / duplicate / update` 和原有名称。同义重复跳过；更正沿用原名称，携带旧哈希进入审阅，接受后替换、撤销后恢复。
- 目录不完整时关闭该批次自动保存，转为审阅。模型自动生成的标签只在此处规范化标点，正文、来源和用户显式命名校验不变；生成名称先检查敏感内容。
- 仍保留来源支持、作用域、敏感信息过滤、旧哈希检查及审阅流程。语义判别依赖模型，不保证识别所有矛盾。完整分段增加请求数，批次最多三个候选；跨批次分割的事实、Journal 原有超长事件截断和并发 Memory 写入仍有独立边界。

### Agent

- [system_prompt](../../tracecoder/prompt.py) 明确要求实际执行空输入、边界和压力检查；循环次数输入应检查数千次，封顶前的中间运算也要检查。只报告实际执行过的检查；请求仅 JSON 时不追加说明。
- 独立评测 Runner 把可信 `allowed_paths` 传给 CLI。[Agent](../../tracecoder/agent.py) 提前拒绝越界文件工具写入；接受无工具最终答复前比较初始/最终文件哈希，发现 Bash 等工具留下越界文件时要求恢复后继续。
- 外部 Runner 仍在导出后独立拒绝非法路径，Test Gate、初始快照、题目和预算不变。普通会话不启用这个评测范围检查；`/goal` 仍须显式设置。范围检查不是正确性证明，也不拦截任意 Bash 命令的每个即时副作用。

## 2. 实际验证

### 跨进程回归

[test_state_concurrency.py](../../tests/test_state_concurrency.py) 使用真实 Windows spawn 四进程，无 mock：10 轮 Task 领取每轮只有一个赢家；其他实例能正常完成已领取 Task。同一 Journal 64 次线程追加可读；四个同步打开的进程只有一个完成 8 次追加，三个过期写者明确拒绝。重新打开读取到连续 72 条，再追加得到 73 条；原始过期句柄仍被拒绝。

[test_fix_memory_and_eval_scope.py](../../tests/test_fix_memory_and_eval_scope.py) 覆盖完整长来源、更正审阅/接受/撤销、同义重复、标签标点、文件工具范围拒绝以及最终补丁范围恢复。[test_eval_runner.py](../../tests/test_eval_runner.py) 保留真实 Docker Shell 绕过文件工具后的外部拒绝检查，并补充文件工具提前拒绝的集成检查。

### Memory 真实模型

DeepSeek v4 Flash、temperature=0，沿用前一轮冻结的十类提取案例，每类两次：

| 检查 | 修改前 | 修改后 |
| --- | --- | --- |
| 冻结提取案例 | 13/20 | 20/20 |
| 长消息尾部 / 同义重复 / 语言更正 | 各 0/2 | 各 2/2 |
| Reading on 严格 JSON | 10/12 | 12/12 |
| Reading off 严格 JSON | 12/12 | 12/12 |

中间版本提取为 18/20：两次模型已看到正确事实，但生成标签含标点被名称校验丢弃。该失败记录保留，随后修复自动标签规范化，最终原冻结案例 20/20。更正样例还实际执行了接受和撤销，旧记录数量及内容可恢复。

另外三类事先冻结的留出案例各两次，严格规则 **4/6**，没有事后修改评分。双语时区重复与英文项目命令更正各 2/2。端口/版本双事实例两次未满足规则：规则要求所有保存记录都包含端口锚点且没有待审项；第一次正确拆成版本与端口两条，第二次端口自动保存、版本待审。人工核对没有错误事实，但这种拆分和保守审阅不能算原规则通过。

提取最终运行 26 次请求、23,809 tokens，usage 全部已知。配对读回和中间提取合计 69 次请求、70,038 tokens；这些用量不是编码任务收益。读回使用双方相同工具 schema，off 关闭 reading 并隐藏目录。读回在最后的提取标签与压力检查提示调整前执行；标签调整不改变读回路径，但未声称所有结果来自同一完整代码 revision。

原始固定协议、回答、来源和用量：

- [最终提取 manifest](E:/eval-runs/corecoder-fixes-20261001/memory-final/manifest.json)、[结果](E:/eval-runs/corecoder-fixes-20261001/memory-final/results.json)
- [中间提取与配对读回](E:/eval-runs/corecoder-fixes-20261001/memory-after/results.json)
- [真实模型验证脚本](E:/eval-runs/corecoder-fixes-20261001/memory_final.py)

### 固定编码基线与全量回归

在干净的独立验证 worktree 中提交本次运行时代码，最终 revision 为 `14ec777a9e25f92986a8aadd2a7f007b82026262`；主工作区对应八个运行时文件逐字节一致，原有未提交修改未纳入验证提交。

| 版本 | 两轮成绩 | 总调用 | 总 tokens |
| --- | --- | --- | --- |
| 修改前 `f03bd9b`，此前冻结运行 | 7/8 + 7/8 = 14/16 | 见原轨迹 | 465,478 |
| 中间 `a688b08` | 7/8 + 7/8 = 14/16 | 88 | 451,004 |
| 最终 `14ec777` | 8/8 + 8/8 = 16/16 | 96 | 512,026 |

中间版本仍两次计算无界指数，不能计作问题全部解决。最终版本的 retry_backoff 两次均通过；第一轮实际执行了十万次和微小初始值的压力检查，并改为有界迭代，没有读取隐藏 Gate。config_merge 两次只修改 solution.py，独立 Gate 通过。最终总 tokens 比修改前约增加 10%，不能宣称节省成本。样本只有八题两轮，并且本次同时调整提示和修改范围约束，不能单独归因某个机制或外推其他模型/真实仓库成绩。

核对四个新 run 与此前协议：模型/温度/输出上限、每题 100,000 tokens / 50 calls / 900 秒、固定镜像摘要、suite SHA-256、所有快照及 Gate 哈希和命令全部一致。所有最终 run 可复现标记为真、Docker 有效、usage 已知、模型调用轨迹完整；所有登记的原始产物哈希复核通过。

实际执行：

```powershell
py -3.13 -m pytest -q
# 468 passed in 453.90s (0:07:33)，无跳过
py -3.13 -m ruff check .
# All checks passed!
git diff --check
# 通过（仅 Git 行尾转换提示）
```

此前第一次全量运行有一个失败：旧测试期待禁止文件被创建后再由 Runner 拒绝，新文件工具已经提前拒绝，因此原断言失效。改为真实 Docker Bash 创建越界文件，仍验证外部路径拒绝，另外新增提前拒绝的集成检查。修复后的最终全量没有测试失败；未删除这一边界检查。

验证日志和固定产物：

- [最终第一轮](E:/eval-runs/corecoder-fixes-20261001/baseline-final-r1/summary.json)、[第二轮](E:/eval-runs/corecoder-fixes-20261001/baseline-final-r2/summary.json)
- [中间第一轮](E:/eval-runs/corecoder-fixes-20261001/baseline-after-r1/summary.json)、[第二轮](E:/eval-runs/corecoder-fixes-20261001/baseline-after-r2/summary.json)
- [全量回归日志](E:/eval-runs/corecoder-fixes-20261001/pytest-final.txt)、[协议与产物审计](E:/eval-runs/corecoder-fixes-20261001/audit_results.json)

对 692 个归档文本文件扫描运行环境中的真实 key/token/password/secret 值，匹配数为 0。最终 Docker 容器列表为空；验证 worktree 保留本地提交，没有推送。

## 3. 未扩大的结论

这些结果只证明列出的并发行为及单模型小样本质量。未验证长会话编码效益、Memory consolidation 质量、任意提示注入抵抗、所有秘密格式、跨进程 Memory 事务、模型变更后的泛化或真实 Goal 评估器准确率。
