# 第二轮 review 修复与裁定

> 历史记录：本文对应当时的设计或验证源码快照，当前代码位于 tracecoder/；历史 revision、哈希及产物路径保留原始 CoreCoder 身份。当前用法以 README 和 CLI 帮助为准；历史成绩不代表当前版本。

日期：2026-10-05。分支 `codex/skill-memory-task`，HEAD `f03bd9b`。修复均在原有脏工作区，未提交或推送；保留既有删除项和未跟踪特性。原报告针对已提交 HEAD，本文件针对当前工作区。

## 本轮修复

- `MemoryStore.remember` 统一经 `save_candidate/apply_changes` 写入，含类型、版本和显式写入元数据；新增和替换均可撤销。保留 replace 布尔校验及旧工具接口。新增回归修复前实际失败。
- TaskStore 创建与替换文件均 flush/fsync；列表逐记录跳过损坏 JSON/字段/不可读文件，并以任务 ID 和错误类型告警。依赖更新严格读取图，缺失或损坏节点拒绝写入；未知 ID 保留 FileNotFoundError 类型但不输出宿主路径。新增回归修复前实际失败。
- `replace_goal` 使用 GOAL_LIMIT。Skill 元数据缺失回退和根内 manifest 链接增加覆盖；load_skill 测试按工具名取工具。
- 十二个未设守卫的真实 Docker 测试接入共享 docker_ready fixture；仅环境不满足时跳过，不影响静态测试或失效关闭校验。未安装依赖或拉取镜像。
- README 中英表格移除过期行数，区分八个基础工具和 CLI 扩展工具；评测文档统一最多五次 provider 尝试、一个逻辑调用计一次预算（最终失败也计数），修正 fixture 测试入口。
- Gate 文档和规格明确：worker 看不到测试目录，但候选代码在 Gate 容器能读取 /gate。只读挂载不是测试保密边界；未宣称实现对抗式隐藏测试隔离。

## 已有修复或不适用于当前工作区

- usage 为 None、DockerBash 危险命令检查：上一轮已修复，保留现有回归。
- memory reading off：当前 CLI 单独提供纯 Skill catalog，并关闭记忆读取工具和记忆上下文；上一轮已有真实绑定回归。
- Skill 根不允许重定向的严格策略已在当前规格补充中记录；根内 manifest 链接当前允许。报告所谓 within_workspace fixture 指向工作区外的判断不成立：tmp_path/elsewhere 仍在工作区内。
- Verified 无界子进程、十题 manifest、百万 token、单机路径、测试路径过滤、重复分类等均涉及当前已删除模块，保留删除，不重新引入。public-v2 文档和测试亦已删除；当前机制对照不存在过期引用。
- 预算上下文规格已标明真实容器和模型评测未验证；不把实现或离线测试等同于 A/B 结果。

## 保留边界

- 脏工作树独立冒烟模式尚未实现，正式评测继续拒绝脏工作树，文档明确未交付。
- Docker 读取循环、上下文 section 结构、工具集合、私有成员依赖等代码组织建议未做广泛重构；本轮优先实际行为缺陷，避免同时改变运行协议。
- Gate 对抗式保密隔离需要独立设计；本轮按报告允许的另一处理方案纠正文档声明。

## 验证

新增回归首次执行：2 failed、3 passed；失败覆盖记忆旧 schema 和损坏记录拖垮列表。
针对本轮、任务、记忆范围、Work 状态：33 passed、2 skipped（符号链接创建权限）。Ruff 默认规则和 E501 通过。
全量执行日志：`dsh-review/repair-round2-tests.txt`，使用隔离 USERPROFILE 和仓库内 basetemp；最终结果见下方补记。

最终重点回归：102 passed、8 skipped，日志 `repair-round2-final-focused.txt`。包括 Skill、Memory（journal/workflow/scope）、Task、Work、抽取队列与上一轮修复回归；跳过为符号链接权限限制。

全量本次实际结果：498 passed、56 skipped、13 failed。10 项是此前环境/测试布局失败（3 项 Windows 符号链接权限，7 项仓库内 basetemp 导致评测输出目录被正确拒绝）；3 项旧记忆测试把 remember 当作 legacy/不记日志外部写入。已改为真实 legacy 文件和直接外部改写，最终重点回归全部通过。未重复全量，因此不把修正后推算数量写成实际全量结果。真实 Docker 验证没有执行，环境跳过不代表通过。

机制对照已同步：`docs/learn-claude-code-mechanism-comparison.md`。
