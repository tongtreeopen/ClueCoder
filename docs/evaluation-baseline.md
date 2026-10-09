# Independent evaluation baseline

Run from a clean TraceCoder Git checkout with Python 3.10+, its existing dependencies,
Docker Linux Engine, and the local digest-pinned `DEFAULT_IMAGE` in `tracecoder/execution.py`.
No image or dependency is downloaded by the runner. Export `TRACECODER_API_KEY`,
`OPENAI_API_KEY`, or `DEEPSEEK_API_KEY` in the process environment; `.env` is not loaded.

```powershell
py -3.13 -m tracecoder.eval run --suite benchmarks/tracecoder-baseline-v1/manifest.json --output E:\eval-runs\baseline-v1-001 --model YOUR_MODEL --provider openai --base-url https://YOUR_ENDPOINT/v1
```

The output directory must be new and outside the Agent checkout and suite directory.
`--model` is required; `--provider` defaults to `openai` and also accepts `litellm`.
Omit `--base-url` for the provider default. URLs containing credentials or query strings
are rejected. `/goal` mode is not used. A worker issues one logical provider request per
model call; transient transport failures (connection reset, timeout, 429, 5xx) are retried
with up to five total provider attempts (four retries) per logical call. Each logical
call consumes one model call even when it ultimately fails; its transport retries do
not consume additional model calls. Exhausted requests are scored as Agent errors.

The baseline suite contains eight Python repair tasks. The complete manifest, snapshots,
hidden Gates, clean revision, writable worker mount and failing initial Gates are
checked before model calls. Each task then receives one fresh workspace and home,
900 seconds, 50 shared model calls and 100,000 provider-reported tokens by default.
Pass `--max-tokens` to set a different per-task token budget; the selected value is
recorded in each task's `run.json` and the summary. Token limits
are checked before the next call; an individual response can cross the token threshold.
The independent read-only Docker Gate has its own 120-second deadline. Both worker
and Gate containers have no network access; provider requests run on the host.

`summary.json` and `summary.md` retain all eight scheduled tasks, pass/scheduled,
Gate coverage, failure counts, Agent revision, image identity, suite manifest SHA-256
and Gate tree hashes. Each attempted task has a separate directory with configuration,
redacted trajectory/stdout/stderr, usage, patch, Gate output and artifact hashes.
`model_calls.json` is a separate hashed artifact with one record per counted model
call: call index, parent or first-level child ID, reported prompt and
completion tokens, cumulative input/output/combined tokens, usage status and outcome. A
record covers the logical call rather than each transport attempt, so a transient retry
inside one call does not add a record or charge the budget again; only a failed logical
call is recorded as `provider_error`. It records an
in-flight call before the request; a pre-call budget rejection updates `limit`
and `rejected_attempts` without adding a provider call. No prompts, responses,
credentials or exception messages are stored. `model_calls_trace_complete` reports
whether the archived call records match the existing `usage.json` counts; it does
not change scoring or protocol validity.
The trajectory includes tagged first-level sub-agent messages and tool results;
the Agent's natural tool-round stop is recorded as `agent_limit` even when the
independent Gate passes. Sub-agents cannot launch further agents.
Setup failures retain eight rows and return exit code 2. A valid protocol returns 0
even when individual tasks fail; inspect `success_rate` and the case statuses.

A formal rate requires all scheduled results, consistent clean revision/image,
verified Docker and reliable provider usage. Failed setup, broken Gate infrastructure
or unknown usage produces `success_rate: null` (not measured). Forbidden edits never
count as a pass. Model randomness can change outcomes; the evaluation protocol is
fixed. Eight local tasks do not establish broad coding performance or significance.

Validate the fixtures without a model:

```powershell
py -3.13 -m pytest tests/test_eval_fixtures.py tests/test_eval_cases.py -q
```

This requires real Docker: all eight initial snapshots must fail, and all eight exact
reference patches must pass. Fixture validation and scripted-provider tests are
infrastructure evidence, not a real-model success rate. Reference answers and Gates
stay outside the worker workspace. Trusted suite files must remain unchanged during
evaluation; concurrent host mutation and transformed/encoded secret exfiltration are
outside this local baseline's guarantees.

A separate frozen eight-task validation suite is available at
`benchmarks/tracecoder-holdout-v1/manifest.json`. Its use and validation boundary are
documented in [evaluation-holdout.md](evaluation-holdout.md). The original baseline
manifest at `benchmarks/tracecoder-baseline-v1/manifest.json` and its historical
results are unchanged.


Gate isolation boundary: the worker does not receive the Gate directory. During
validation, candidate code and read-only Gate tests share a container; candidate code
can read `/gate`. This is a local regression protocol, not an adversarial hidden-test
security boundary. A clean revision remains required; dirty-tree smoke runs are not implemented.
