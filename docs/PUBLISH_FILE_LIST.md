# ClueCoder 发布文件清单

ClueCoder 在 CoreCoder 基础上开发，发布内容保留上游署名和 MIT 许可证。

| 路径 | 内容 |
| --- | --- |
| `cluecoder/` | 运行源码、工具与评测 Runner |
| `tests/` | 自动化回归测试 |
| `benchmarks/cluecoder-baseline-v1/` | 固定开发评测任务、快照、Test Gate 与参考补丁 |
| `benchmarks/cluecoder-holdout-v1/` | 独立留出评测任务 |
| `examples/` | 使用示例 |
| `docs/` | 使用说明、机制对照、评测协议与验证记录 |
| `.github/workflows/ci.yml` | 跨平台、Docker、打包与静态检查 |
| `pyproject.toml` | 包元数据、依赖和 CLI 入口 |
| `LICENSE` | 保留原始 MIT 许可证 |

运行状态、密钥、虚拟环境和临时构建产物不纳入源码发布。历史验证记录中的 revision、哈希及原始产物标识保留，以便核对证据。当前验证结论见 [发布说明](RELEASE.md)。
