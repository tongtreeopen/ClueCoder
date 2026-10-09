# 后台有界等待方案测试（2026-10-03）

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 cluecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

## 结论

方案已实现并完成真实模型四场景、两轮共八次测试，以及 **100 passed in 81.63s** 定向回归。后台状态查询从上一协议的 **24 次降为 9 次**；成功场景两轮都通过一次等待拿到真实 completed/exit 0、无干扰的已落盘事件，并登记为有效验收证据。旧测试这两个场景均未建立有效成功证据。

修改场景第一轮完成两次后台检查：首个结果因修改失效，第二个新鲜结果成功登记；第二轮把重检改成前台执行，又运行额外 Bash 使证据失效，随后反复查询历史，达到 12 轮上限。故后台等待问题已有直接改善，不能宣称所有重复工具调用已解决。

完整严格验收 **1/8**（第二轮故意失败场景）。两次失败均得到真实 failed/exit 1，两次取消均得到 cancelled/exit 137；八次后台清理、固定文件哈希、独立源码语义 Gate 均通过，无错误 achieved。真实 Goal 仍受输出格式、评估 JSON 错误、续跑 BadRequestError 及流程偏离影响。

## 实现范围

- DockerRun.wait 可带时限，复用既有完成事件；不新增轮询线程。无时限的前台等待保留原实现。
- BackgroundManager.status 增加 wait_seconds，严格接受整数 0…30；等待不持有 manager 锁，其他线程可取消。0 为即时查询。
- background_status 工具默认等待 10 秒；任务结束提前返回。background_cancel 的 schema 仍只接收 task_id。
- Agent 在主线程收集已结束任务，再更新状态/取消工具回复中的实际快照及 result_event_id。并行批次先完成干扰标记，再发布结果，避免把并发 Bash 的证据错报为干净。
- 系统提示引导没有其他工作时等待 30 秒，不用 Bash sleep、重复读文件或翻查历史等待；终态后进入验收或重检。没有增加通用工具禁用规则，没有放宽新鲜度门。

## 测试条件与比较边界

与前次相同：真实 deepseek-v4-flash / api.deepseek.com，固定摘要 Docker 镜像，15 秒固定检查、临时工作区、四个场景各两轮、temperature=0、1200 输出 token、最多 12 次调用/48000 外部 token/360 秒墙钟上限，真实 Agent、日志、工作卡和 GoalEvaluator，主任务提示未修改。

内部 Goal 默认预算为用户刚要求的 **100000 token**；前次为 30000。新的工具 schema、等待实现、结果回复和系统提示也发生变化，因此这不是单变量 A/B 实验，不能把所有差异归因于 wait_seconds。后台等待本身的限时、提前返回、并发取消和主线程事件行为由独立回归直接验证。外部预算仍为 48000，不能将它误解为本次每场景可消耗完整 100000。

本次依旧是有指导的简单功能测试，不是软件问题解题基准或终端交互层完整 E2E。独立源码 Gate 不替代模型固定验收检查。

## 逐次证据

| 场景 | 状态查询旧 → 新 | 后台终态 | 新鲜证据/重检 | Goal |
|---|---:|---|---|---|
| round-1-success | 4 → 1 | completed/0 (event 27, interfered=False) | True | active |
| round-1-failure | 6 → 1 | failed/1 (event 22, interfered=False) | 不适用 | needs_user |
| round-1-cancel | 1 → 1 | cancelled/137 (event 22, interfered=True) | 不适用 | needs_user |
| round-1-stale | 3 → 2 | completed/0 (event 27, interfered=True); completed/0 (event 44, interfered=False) | True | error |
| round-2-success | 3 → 1 | completed/0 (event 18, interfered=False) | True | error |
| round-2-failure | 4 → 1 | failed/1 (event 19, interfered=True) | 不适用 | needs_user |
| round-2-cancel | 1 → 1 | cancelled/137 (event 13, interfered=True) | 不适用 | active |
| round-2-stale | 2 → 1 | completed/0 (event 27, interfered=True) | False | limit |

原始计数及终态来自 audit.json（归档材料未收录于此副本），严格评分来自 summary.json（归档材料未收录于此副本）。每个 round-* 目录保留初始/最终哈希、源码、观察记录、会话事件、工作卡、用量及独立 Gate。已记录的 12 个机制相关产品源文件在冻结运行前后哈希相同，正式运行过程中未改代码。

## 尚未通过的原因

1. 第一轮成功：一次等待拿到事件 27 并完成工作卡；评估器要求额外证明运行期读取与文件未变，但目前传给评估器的主要是固定检查证据，外部 runner 的哈希和观察并未传入。随后续跑出现 BadRequestError。不能将工作卡完成等同于整个 Goal 达成。
2. 第一轮修改、第二轮成功：均形成有效新鲜证据，但 Goal 返回 Invalid goal evaluator JSON。当前记录没有评估器原始输出，不能确定是格式、输出截断还是其他原因。
3. 第二轮取消：取消与负面 JSON 报告均正确，但工作卡没有有效成功检查，Goal 要求继续后遇到 BadRequestError。两个 BadRequestError 的请求正文/错误正文没有保留，具体原因尚未定位，不能直接归咎于提供方。
4. 第一轮失败/取消、第一轮修改最终返回说明文字或围栏 JSON，不符合约定单个 JSON 输出。
5. 第二轮修改：后台首次过期结果被正确拒绝；模型随后执行前台固定检查和额外源码检查，再多轮读取事件，未形成规定的后台新鲜重检，最后因 Agent maximum rounds 而停止。它没有耗尽内部 100000 token 预算。
6. 第二轮失败虽严格通过，Goal 原因却是 Goal and work card disagree；它只证明该场景保持未验收和报告正确，不代表负面终态收尾流程理想。

下一步应针对 Goal 续跑请求捕获脱敏错误详情、保存评估器原始输出和结束原因，定位 BadRequest/JSON 错误；同时检查目标要求与评估器实际收到的证据是否一致。针对“反复翻历史”的重检偏离应另做定向复现；本轮没有顺带改 Goal、LLM 或验收规则。

## 用量与辅助探针

正式运行登记 **59 次模型调用、194204 已报告 token**；第一轮成功和第二轮取消发生 provider_error，用量为 unknown。因此该 token 数不是完整账单，也不发布 token 节省比例或统一预算成功率。

额外做了一次“最后一条消息为 assistant”的最小请求探针，成功响应（41 输入/20 输出 token，正文为空），没有复现 BadRequest；不能凭该探针定位实际失败。探针独立保存在 provider-continuation-probe.json（归档材料未收录于此副本），不计入八次评分和上述用量。已扫描保存目录，没有发现当前凭据原值。

## 回归与初次失败

最终 回归原始输出（归档材料未收录于此副本） 为 **100 passed**，包含有限等待返回 running、完成提前返回、非法参数拒绝、等待不阻塞并发取消、即时/默认查询、结果事件只写一次、主线程写日志，以及单工具/并行只读/并行 Bash 三条结果发布路径。并行 Bash 的结果仍被标为 interfered，不能验收。

初次回归为 98 passed、1 failed：将前台等待也替换成完成事件等待后，原有通过 time.sleep 注入中断的清理测试不再触发。修复为保留原前台路径，新增时限只用于后台查询；最终上述回归全部通过。Ruff 检查通过。

```powershell
py -3.13 -m pytest tests/test_background_tasks.py tests/test_docker_execution.py tests/test_goal_loop.py tests/test_work_state.py tests/test_permissions.py tests/test_cli_workspace.py -q
```

测试器复用原实现，另存于 run.py（归档材料未收录于此副本），冻结参数和源码/测试器哈希见 protocol.json（归档材料未收录于此副本）。旧运行记录保持原样。
