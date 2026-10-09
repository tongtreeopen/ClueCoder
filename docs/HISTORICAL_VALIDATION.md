> 本文保留改名前的来源与验证记录。当前项目名为 ClueCoder，包入口为 `cluecoder/`；参见 [命名迁移](CLUECODER_RENAME.md)。

# Historical validation and provenance

The reports linked below are portable summaries of work performed before the TraceCoder name change. They remain available in the source distribution. Their revisions, source hashes, original CoreCoder identity and known failure boundaries are preserved; results are not relabeled as TraceCoder release results.

| Topic | Public summary |
| --- | --- |
| Structured sub-agent reports | [2026-10-04 report](validation/2026-10-04-structured-subagent.md) |
| Recoverable review workflow | [2026-10-06 report](validation/2026-10-06-workflow-runtime.md) |
| State, memory and Agent boundaries | [2026-10-01 report](validation/2026-10-01-agent-state-memory-fixes.md) |
| Sessions, Tasks and paired memory checks | [2026-10-01 report](validation/2026-10-01-agent-session-task-memory.md) |
| Background Docker execution | [2026-10-02 report](validation/2026-10-02-background-tasks.md) |
| Initial TraceCoder release | [2026-10-09 report](validation/2026-10-09-first-release.md) |

Historical raw artifacts under local drive paths are not included in this release. They are shown as literal provenance references, not clickable public evidence. They cannot be independently reproduced from a parent commit alone when a report refers to a dirty workspace. Publicly usable evidence consists of the bundled summaries and source, followed by checks of a frozen TraceCoder commit.

A current release's unit-test counts, infrastructure checks, model task checks and historical rates are separate measurements. Passing a single model task does not establish an eight-task suite success rate or performance on a public benchmark.
