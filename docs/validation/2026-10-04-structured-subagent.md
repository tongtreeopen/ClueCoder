# 结构化子 Agent 结果（2026-10-04）

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 cluecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

## 实现与使用

`agent` 工具新增可选 `output_format="structured"`，默认 `text` 保留原调用方式、文本前缀和截断行为。

```json
{"task": "读取相关源码并报告问题及阻碍", "output_format": "structured"}
```

结构化返回值是可直接解析的 JSON 字符串：

```json
{
  "status": "completed",
  "summary": "已完成源码检查",
  "findings": ["发现描述，可包含文件路径及行号"],
  "evidence_refs": ["read-1"],
  "blockers": [],
  "child_id": "宿主生成的子任务 ID"
}
```

模型提供前五个字段，宿主添加 child_id。summary 为字符串；findings、evidence_refs、blockers 为非空字符串列表（列表可为空）。模型只可报告 completed 或 blocked；blocked 要求非空 blockers，completed 要求空 blockers。宿主失败结果为 error 或 limit，带非空 blockers、空 findings 和 evidence_refs。completed 只表示执行结束，不表示修复正确或测试通过。

校验拒绝缺失/额外/重复字段、类型错误、状态与阻碍矛盾及伪造引用。报告上限 16384 字符，不直接截断返回 JSON。引用是当前子任务实际工具回复的 tool_call_id，必须结合 child_id 查找轨迹；不是主会话成功检查 event_id，不会转换为 Goal 验收证据。失败/拒绝的工具回复也只是来源，不证明执行成功。

首次格式非法只允许一次纠正：不给工具、输出上限 4096 token、要求保留事实与阻碍，使用同一模型和 child_scope。BudgetedModel 计入共享调用/token 限额；无预算包装器的直接 Python API 不会自动新增全局预算控制。纠正再次非法、返回工具或截断时返回 error；预算耗尽返回 limit。子 Agent 达到轮次上限、返回空内容或终止于 length/max_tokens/content_filter 时，不发纠正请求，也不返回 completed。结构化异常摘要只记录异常类型。

usage 带 child_id。结构化 finish 在校验失败时 completed=false。拒绝的纠正工具请求只记录为 format_correction，不执行、不污染正常助手/工具配对历史。已有 on_subagent_event 回调继续负责轨迹；普通 CLI 无回调时，结构化模式通过 work_state.journal 保存子任务 note，重开会话仍可查来源。大 detail 保存有界预览、SHA-256 和 tool_call_id，全文不保证重建；所有记录仍经现有日志脱敏。直接 Python API 无工作卡或回调时，持久化由调用方负责。子消息不混入主 Agent messages，也不成为新的用户指令。

## 已执行验证

最终源码定向回归：**208 passed, 16 skipped in 53.19s**，见 原始输出（归档材料未收录于此副本）。其中新功能 **26 项**，覆盖完整长 JSON、文本兼容、真实文件读取与来源回查、跨子任务引用、非法类型/字段/重复键、单次纠正、工具拒绝、共享预算/actor、轮次/截断/空回复/异常、完成事件和普通日志重开。没有新增 mock/patch；使用现有 ScriptedLLM、真实 Agent/工具、文件/日志及 BudgetedModel。

兼容回归包括 core、预算、评测 CLI、权限、Hooks、Skill、Goal、工作卡、会话日志与后台任务。16 项后台真实 Docker 测试跳过；docker info 确认 Docker Desktop Linux Engine named pipe 不存在、服务不可连接。未启动 Docker或安装依赖，不将跳过项视为通过。

`py -3.13 -m ruff check .`、`py -3.13 -W error -m compileall -q corecoder` 与 `git diff --check` 通过。未执行全量 pytest 或真实模型评测；不声称改善解题成功率/成本、保证 findings 真实或纠正不改变语义。当前工作树含既有修改，本次未提交或覆盖其他改动。

原始 CoreCoder 验证快照的源码 SHA-256（不是改名副本当前文件的哈希）：

| 文件 | SHA-256 |
| --- | --- |
| corecoder/tools/agent.py | B0DB8476673AEBBAAF5949142B135D269BE41FDC8F87ECD1FA02E8E48267B9E0 |
| corecoder/agent.py | 860DEEFF5715E9DC4EED298B710083342131081369DC31F3A162DCD587BE4B72 |
| tests/test_structured_subagent.py | AD6F7558F1908EC36CD2E872B5C23E3B6C68C96E78090A8AA989E262F77AE4D6 |
