# Real-model validation of the repaired release, 2026-10-09

Tested agent commit: `6da84a4ec2a66c5b793b51f460f2de35f456f219`. Runtime tree: `f4e9db575b8255f87f2bba7cc14f2e50eed084ed`. Model: `deepseek-v4-flash`, using the existing private local credential only inside the validation process. No source behavior was changed during this validation.

## Fixed task subset

Three selected tasks from `tracecoder-baseline-v1`, each on its untouched snapshot. Each initial independent Docker Gate failed before the agent ran. Each task used a 50,000-token budget, 50-call ceiling and 900-second worker deadline; only `solution.py` could change. The worker and independent Gate use the pinned image recorded in [summary.json](realmodel-repairs-20261009/summary.json).

| Task | Formal status | Independent Gate | Calls | Reported tokens |
| --- | --- | --- | --- | --- |
| retry_backoff | pass | pass | 4 | 20,228 |
| ttl_cache | pass | pass | 6 | 34,812 |
| config_merge | agent_limit | pass | 9 | 57,216 |

Formal result: **2 of 3 selected tasks passed**. Gate result: 3 of 3 candidate patches passed. Gate success does not override `agent_limit`; the budget is checked between requests, so one response can cross the remaining budget. All three modified only `solution.py`, had no forbidden changes, and retained complete model-call traces. This is a selected smoke subset, not an eight-task baseline result or a statistical success-rate estimate. Each fixed task ran once; there was no task retry or budget increase.

## Actual child and workflow checks

A tracked temporary project changed division from `/` to `//`. The real child read `solution.py`, then the main `WorkflowTool` ran the actual read-only `review-changes` workflow against the change. It is an API-driven mechanism probe, distinct from the isolated task runner and its independent Gate.

| Attempt | Child Goal tokens | Total Goal / provider tokens | Calls | Main binding / persisted workflow note | Workflow status |
| --- | --- | --- | --- | --- | --- |
| 1 | 3,941 | 50,011 / 50,011 | 21 | correct / recorded | limit |
| 2 | 4,482 | 51,626 / 51,626 | 24 | correct / recorded | limit |

Attempt 1 launched from the agent checkout while the file tools targeted the temporary project. The generated prompt cwd differed from that workspace; the model attempted reads in the wrong directory, which were refused. Its child report also omitted evidence references despite an actual read. This harness limitation and failed attempt are retained. Attempt 2 launched from the target project, matching normal CLI use, and explicitly requested the actual read evidence ID. The child completed with an actual tool-reply reference, but the workflow still exhausted the same 50,000-token budget. No attempt is reported as successful workflow completion.

The narrower implementation checks did pass on both attempts: the main workflow tool remained bound to the main Agent; its work-card journal received the workflow note; and every reported child/workflow token was included in the outer Goal (Goal total exactly matched provider total). Semantic completeness and review efficiency remain separate limitations. In particular, this tiny source change did not complete the full workflow within the chosen budget.

Two separate real requests used a 1-token Goal budget to force exhaustion. They consumed 1,095 and 1,150 tokens respectively, charged the same amount to Goal, returned `limit`, and refused the second request before another provider call. This verifies request blocking after a crossing response, not cancellation of an already in-flight request.

## Evidence and scope

All attempts together: **66 logical model calls, 216,138 provider-reported tokens**. Portable evidence includes every fixed-task patch, Gate output, model-call metadata, both mechanism session journals and budget-check traces. [Artifact hashes and results](realmodel-repairs-20261009/summary.json) bind the files to the tested source revision. The configured credential values were checked absent before these files were copied. Published text uses LF line endings. The private local runner scripts and credentials are not published.

The previous offline full regression and cross-platform CI remain valid for this identical source tree; they do not turn the incomplete real workflow into a success. Parallel work-card recovery and child write-scope branches were regression-tested offline; this real-model probe does not claim full coverage of those branches. No public repository release or PyPI upload occurred.
