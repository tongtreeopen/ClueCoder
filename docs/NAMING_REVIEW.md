# TraceCoder 命名与来源审查（2026-10-09）

范围：发布副本的源码、配置、测试、示例、评测 fixtures、README、设计与验证说明。最初检查时本目录无 Git 历史；现已建立独立 Git 历史与私有远端。以改名要求和原 CoreCoder 工作区为对照，未开展全仓行为正确性审计。

## 已确认与修复

- 运行时包名、导入、CLI、环境变量、状态目录和 Docker 标识已统一，无遗留 CoreCoder 运行入口。
- 清理 Session、Context、Sub-agent、Edit、Todo、Permission、Hooks 中继承的教学式比较和未经核实的上游行数。
- 修正 Session/Todo/Hooks 说明，使其描述当前日志、持久化回调和 API 装配边界。
- 恢复历史验证产物的 corecoder 路径，避免指向并不存在的 tracecoder 历史目录。
- 明确历史源码哈希、revision 与命令对应原 CoreCoder 验证快照。
- 更新改名后的留出 suite manifest SHA-256；历史成绩没有重算或重新冠名。
- 移除示例中对继承版本号的宣传绑定。

## 应保留的上游信息

- LICENSE 中原作者版权及 MIT 条款。
- README 中 CoreCoder 来源链接和旧数据迁移说明。
- 历史验证路径、原始 revision 和源码哈希。
- pyproject.toml 中原作者信息；TraceCoder 维护者现已按本地 Git 身份配置为 Tong ShuKai。
- 模型列表中的 Claude/nano 型号是真实配置标识，不属于旧项目命名。

## 尚未确定

首发版本为 0.1.0；维护者使用本地 Git 身份 Tong ShuKai。私有远端 tongtreeopen/TraceCoder 已创建，pyproject.toml 已配置实际 Repository 和 Issues URL。PyPI 发布尚未进行。具体首发验证见 docs/validation/2026-10-09-first-release.md。

验证范围：语法、配置一致性、本地文档链接及命名扫描。不将这些检查当作全量功能、Docker 或真实模型验证。
