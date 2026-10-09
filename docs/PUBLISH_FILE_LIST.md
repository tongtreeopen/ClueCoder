# 发布文件清单

发布目录：`TraceCoder-publish-20261007`。本次不改产品代码，只精简发布材料与对应文档引用。

## 保留

| 路径 | 用途 |
| --- | --- |
| `tracecoder/` | 完整运行源码、工具与评测 Runner |
| `tests/` | 已有和新增机制的回归测试；保留失败边界的覆盖 |
| `benchmarks/tracecoder-baseline-v1/` | 固定开发评测任务、初始快照、Test Gate 与参考补丁 |
| `benchmarks/tracecoder-holdout-v1/` | 独立留出评测任务；与开发集分开报告 |
| `examples/` | Plan/Hooks 与本地状态 API 使用示例 |
| `.github/workflows/ci.yml` | CI 测试、语法与打包检查 |
| `pyproject.toml` | 包信息、CLI 入口和依赖的唯一维护入口 |
| `README.md`、`README_CN.md` | 安装、运行、功能边界和来源说明 |
| `LICENSE` | 原作者 MIT 许可与版权，原样保留 |
| `.gitignore`、`.gitattributes` | 排除本地生成文件并规范文本换行 |
| `docs/evaluation-baseline.md`、`docs/evaluation-holdout.md` | 固定评测协议、预算与评分边界 |
| `docs/skill-memory-task-usage.md` | Skill、Memory 与 Task 使用说明 |
| `docs/learn-claude-code-mechanism-comparison.md` | 机制来源、实际行为、改进取舍及验证边界 |
| `docs/superpowers/specs/` | 已实现机制的设计说明，保留架构决策 |
| `docs/validation/` 的其余 Markdown | 验证摘要，包括失败阶段；不将历史成绩当作当前成绩 |

## 已移出发布目录

材料存放在同级 `TraceCoder-excluded-20261007`，不会随发布目录提交。

| 路径 | 移出原因 |
| --- | --- |
| `article/`（18 文件） | 上游教学系列，未针对当前实现重新核对 |
| `assets/`（3 文件） | 上游截图和演示，不能代表当前 TraceCoder |
| `docs/research/` | 开发阶段调研草稿；机制对照和设计说明已有更直接信息 |
| `docs/validation/2026-10-06-workflow-ledger.md` | 实施流水账；设计和验证报告已保留 |
| `requirements.txt` | 与 pyproject.toml 重复，避免两处依赖漂移 |
| `RELEASE_PREPARATION.md` | 本地发布整理记录，归档保留，不作为产品文档 |
| `.pytest_cache/`、各级 `__pycache__/` | 测试缓存与编译缓存 |

## 验证与后续事项

- README 的导读入口、徽章和旧演示引用已清理；Goal 默认 token 预算同步为源码中的 100000。
- 本次检查文件清单、引用和许可完整性；未重跑测试或模型评测。
- 此前改名定向回归 44 passed，安装与 CLI 入口通过；离线 demo 有 Windows 权限和编码失败，尚未修复。
- 新仓库地址、PyPI 名称可用性及自动发布配置仍未确定。
- 删除缓存曾被执行策略拒绝；本次采用可恢复移动，发布目录已清理，归档仍存在。
