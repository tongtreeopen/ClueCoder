# skill-memory-task 分支审查修复

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 tracecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

范围为上一轮 code-review 的三项 Spec 缺陷和一项 Standards 判断项。当前分支 codex/skill-memory-task，HEAD f03bd9b；在现有未提交修改上局部修复，未提交或推送。

## 修复与现有修改

1. 当前工作树已有按最近用户请求回退近期窗口的修复。保留它，现有 active_request 回归覆盖长工具轮次和 snip 裁剪。
2. 当前工作树已有 positions 映射原始消息下标的修复。新增 60 条消息强制裁剪/摘要回归，确认 summary_count 为 59，来源事件为 [1,59]，后续模型视图只保留当前请求，不重新发送已总结的尾部历史。
3. 新增 tool_result_fit：在最终视图仍超过输入预算时，从最大当前工具结果开始归档，保留 500 字符预览、引用和调用 ID，直到满足预算或已无法继续缩减。超大用户请求仍明确拒绝。真实 journal 回读验证归档全文，原始消息保持完整。
4. MemoryCLI.handle_command 接管 /memory 的模式、状态、审阅、接受/拒绝、处理、撤销和整合分派。REPL 只识别命令并传入现有输出函数；保留现有通知、阅读开关回调及 Plan Mode 边界。真实队列验证冲突候选、Plan Mode 拒绝、接受和撤销。

## 验证

反例先失败：超长当前结果 estimated 5776 input tokens / budget 2600；修复后通过。首轮上下文和记忆 CLI 回归 35 passed。最终相关回归 86 passed（40.57 秒）。

最终命令：`py -3.13 -m pytest tests/test_context_review_fixes.py tests/test_context_assembler.py tests/test_context_regression.py tests/test_cli_memory.py tests/test_extraction_queue.py tests/test_session_journal.py tests/test_memory_workflow.py tests/test_cli_workspace.py tests/test_cli_eval_mode.py -q --tb=short`。未重复全量 pytest；全量已有失败记录不作为本轮通过证明。

Ruff 检查变动产品/测试文件通过；warnings-as-errors compileall 通过；限定变动文件的 git diff --check 通过。

本轮验证使用脚本模型、真实临时目录与 session journal，不是实际模型质量或 Docker Gate 评测。不扩大范围修复此前全量中已记录的工作区 fixture 和 Windows demo 问题。
