# TraceCoder

**A Python coding agent with traceable execution, recoverable state, and evidence-based task verification.**

English | [中文](README_CN.md)

TraceCoder is for developers who want to inspect how an agent changes code, understand why it stops, and resume unfinished work. The model reads, reasons, and selects tools. The runtime records actions and enforces workspace boundaries, budgets, and completion evidence.

The project tracks a task beyond patch generation: preserve the current request, record actual operations, recover progress, and distinguish a proposed final answer from a result supported by checks.

## Capabilities

| Capability | Behavior |
| --- | --- |
| Code operations | Read, search, write, and edit workspace files; run checks through controlled Bash execution |
| Goal verification | Opt-in `/goal` checks work-card evidence before a separate tool-free evaluator reviews the candidate answer |
| Session recovery | Persistent event journal and work card; restore progress, blockers, and active Goal budgets |
| Long-term memory | Project and user scopes, on-demand reads, candidate review, conflict handling, and controlled undo |
| Context management | Budget-aware history assembly, current-request retention, paired tool messages, and archived-output retrieval |
| Background execution | Docker tasks with status, cancellation, time/output/concurrency limits, and cleanup tracking |
| Structured review | Validated sub-agent reports and a journaled, resumable, read-only `review-changes` workflow |
| Independent evaluation | Fixed tasks, snapshots, and budgets; an external Docker Test Gate checks generated patches |

## Quick start

Requires Python 3.10+ and access to a model endpoint. Install from source; no published PyPI installation is advertised yet.

From the project root, using PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .

$env:TRACECODER_API_KEY = "YOUR_API_KEY"
$env:TRACECODER_BASE_URL = "https://YOUR_PROVIDER/v1"
$env:TRACECODER_MODEL = "YOUR_MODEL"
```

Start in the repository you want to work on:

```powershell
cd E:\path\to\your-project
tracecoder
```

Example request:

```text
Fix empty-input handling in parse_config, add regression tests, run the relevant checks, and explain the result.
```

For one-shot execution, explicitly approve mutating tools:

```powershell
tracecoder --yes -p "Fix empty-input handling in parse_config, add regression tests, and run the checks"
```

CLI file tools are restricted to the startup workspace. Bash requires a local Docker Linux Engine and the configured digest-pinned image; otherwise execution is refused. See [execution.py](tracecoder/execution.py) for the image definition. `--yes` does not bypass workspace or execution boundaries. Without Docker, tasks that require Bash checks cannot complete those checks.

## Completion supported by evidence

Ordinary chat can return a model answer directly. To continue toward an explicit goal and check completion conditions, use:

```text
/goal Fix empty-input handling in parse_config, add regression tests, run the checks, and record acceptance evidence.
```

The agent records acceptance criteria, actual check commands, and matching result events in its work card. Failed, missing, or stale evidence cannot pass. Relevant changes or execution after a check may require a fresh check. A separate evaluator reviews the candidate answer only after deterministic evidence checks pass.

For work without executable checks, the user can explicitly accept it with `/work confirm`. A successful command proves that check passed, not that every requirement is covered.

Goal defaults are three automatic continuations, 15 minutes of active time, and 100,000 reported tokens. Session recovery preserves the budget and does not replay previous tool calls. Ordinary chat does not implicitly enable Goal.

## Common commands

| Command | Purpose |
| --- | --- |
| `/help` | Show interactive commands |
| `/plan` | Toggle read-only planning |
| `/goal <task>`, `/goal`, `/goal off` | Set, inspect, or disable a Goal |
| `/work status`, `/work confirm`, `/work reopen` | Inspect, accept, or reopen the work card |
| `/save`, `/sessions` | Show the current session ID and available sessions |
| `tracecoder --resume <ID>` | Resume a session |
| `/memory status`, `/memory review` | Inspect extraction jobs and review candidates |
| `/memory accept <ID>`, `/memory reject <ID>` | Handle memory candidates |
| `/memory undo` | Undo the latest controlled memory-management operation |
| `/model <name>`, `/reset` | Switch models or start a new session |

Background tasks, project Tasks, and review Workflows are model-invoked tools, not slash commands with the same names. Background execution is managed within the current CLI process, not a persistent scheduler. Workflows currently perform serial read-only review and do not automatically modify code.

## Configuration and local data

| Variable | Purpose |
| --- | --- |
| `TRACECODER_API_KEY` | Credentials; also accepts `OPENAI_API_KEY` and `DEEPSEEK_API_KEY` |
| `TRACECODER_BASE_URL` | OpenAI-compatible endpoint; `OPENAI_BASE_URL` takes precedence |
| `TRACECODER_MODEL` | Model name; configure it explicitly |
| `TRACECODER_PROVIDER` | `openai` by default, optionally `litellm` |
| `TRACECODER_MAX_CONTEXT` | Context token budget, default 128000 |
| `TRACECODER_MAX_TOKENS` | Per-response output limit, default 4096 |

Ordinary CLI runs load `.env`; existing process variables take precedence. Install the optional LiteLLM backend with `python -m pip install -e ".[litellm]"`.

Project Skills, memory, and Tasks live under `.tracecoder/`. User Skills, memory, and sessions live under `~/.tracecoder/`. Previous CoreCoder data is not migrated automatically. Journals and memory may contain project content and should follow your project's data-handling requirements.

## Evaluation and development

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests/ -q
python -m ruff check tracecoder tests examples
```

The fixed suites are `benchmarks/tracecoder-baseline-v1/` and `benchmarks/tracecoder-holdout-v1/`. Gate tests and reference patches are excluded from the Agent workspace. Reports preserve calls, usage, patches, and Gate results. See the [baseline protocol](docs/evaluation-baseline.md) and [holdout protocol](docs/evaluation-holdout.md) for setup, commands, and scoring limitations.

This is an experimental project. TraceCoder 0.1.0 uses its own release version sequence and is maintained by Tong ShuKai. See the [first-release verification](docs/validation/2026-10-09-first-release.md) for current checks and the [release record](docs/RELEASE.md) for provenance and publication status. `tracecoder --demo` uses a bundled scripted model and generated Python assertions; it needs no API key or pytest. Ordinary CLI Bash still requires Docker. Historical real-model and Docker results apply only to their recorded source snapshots. Candidate programs can read `/gate` in the validation container; the evaluator does not claim adversarial hidden-test isolation.

## Code and documentation

```text
tracecoder/
  agent.py                 Model and tool loop
  cli.py                   CLI assembly and interaction
  context_assembler.py     Model input assembly
  session_journal.py       Session event journal
  work_state.py / goal.py  Work state and Goal verification
  memory*.py / tasks.py    Memory and project Tasks
  execution.py            Docker execution
  background.py           Background task lifecycle
  workflow*.py            Read-only review runtime
  tools/                  Model-callable tools
  eval/                   Independent evaluation runner
```

- [Skill, Memory, and Task guide (Chinese)](docs/skill-memory-task-usage.md)
- [Mechanism attribution and tradeoffs (Chinese)](docs/learn-claude-code-mechanism-comparison.md)
- [Design documents](docs/superpowers/specs/)
- [Historical validation reports](docs/validation/)

## Origin and license

TraceCoder extends [CoreCoder by Yufeng He](https://github.com/he-yufeng/CoreCoder) and draws on mechanism examples from [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code). Our additions focus on evidence-based completion, recoverable state, reviewable memory provenance, budget-aware context, controlled background execution, structured review, and independent evaluation. The mechanism comparison documents their sources and limitations.

The original copyright and [MIT License](LICENSE) are preserved.
