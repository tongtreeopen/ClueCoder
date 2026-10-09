> ClueCoder 在 CoreCoder 基础上开发。历史记录的 revision、哈希和原始产物标识按实际验证范围保留；参见 [项目来源](PROJECT_ORIGIN.md)。

# Historical validation and provenance

The reports linked below summarize development and validation of the CoreCoder-based agent mechanisms. They remain available in the source distribution. Their revisions, source hashes, original CoreCoder identity and known failure boundaries are preserved; results are not treated as validation of a different source revision.

| Topic | Public summary |
| --- | --- |
| Structured sub-agent reports | [2026-10-04 report](validation/2026-10-04-structured-subagent.md) |
| Recoverable review workflow | [2026-10-06 report](validation/2026-10-06-workflow-runtime.md) |
| State, memory and Agent boundaries | [2026-10-01 report](validation/2026-10-01-agent-state-memory-fixes.md) |
| Sessions, Tasks and paired memory checks | [2026-10-01 report](validation/2026-10-01-agent-session-task-memory.md) |
| Background Docker execution | [2026-10-02 report](validation/2026-10-02-background-tasks.md) |
| Initial release snapshot (superseded for current status) | [2026-10-09 report](validation/2026-10-09-first-release.md) |

Historical raw artifacts under local drive paths are not included in this release. They are shown as literal provenance references, not clickable public evidence. They cannot be independently reproduced from a parent commit alone when a report refers to a dirty workspace. Publicly usable evidence consists of the bundled summaries and source, followed by checks of a frozen ClueCoder commit.

A current release's unit-test counts, infrastructure checks, model task checks and historical rates are separate measurements. Passing a single model task does not establish an eight-task suite success rate or performance on a public benchmark.
