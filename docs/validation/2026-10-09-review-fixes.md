# Release review repairs, 2026-10-09

Repairs the four reproduced findings from the review of `0f2b26bf896fc2396725c7ed9d852862d9a9917c`: concurrent work-card revision corruption; uncharged Goal child usage; shared workflow parent binding; missing child evaluation scope.

## Implementation

- WorkStateStore mutations hold a journal transaction and reload current persisted state before deriving the next card. Snapshot writes remain inside that transaction.
- Child model requests, including report corrections, use the outer Goal usage lock, check its budget before requests, charge known usage, and stop before executing tool calls on exhausted or unknown usage. Child completion does not run main Goal acceptance.
- Workflow tool instances are copied per child; parent bindings and usage/work-card context remain intact.
- Children inherit the evaluation file scope and Plan Mode.

## Validation

- Tested implementation commit: `5a9df49754d80248425692bb4c83e427a0d30b9f`.
- Runtime source tree: `f4e9db575b8255f87f2bba7cc14f2e50eed084ed`; test tree: `dc297c099bfee77d2a9cbea3af711f99217b1d1a`.
- [Private GitHub CI](https://github.com/tongtreeopen/TraceCoder/actions/runs/37902158362): all 15 jobs passed. Each Windows/Python 3.10-3.13 combination: 552 passed / 48 skipped. Each Ubuntu/macOS combination: 549 passed / 51 skipped. Separate native Ubuntu Docker job: 78 passed, no skips, in 100.60 seconds. Skips and separate job results are not added together as one test total. [Job summaries](2026-10-09-review-fixes-ci.json).
- Nine new regression cases passed. Existing Goal/workflow/subagent/work-card tests: 77 passed. New regressions plus session/state concurrency checks: 26 passed. These scopes overlap and are not cumulative totals.
- Complete local Windows/Python 3.13 Docker-enabled regression: **599 passed, 1 skipped**, no failures, in 563.32 seconds. The skip requires a native nonroot Linux host and passed in the Ubuntu Docker job.
- Ruff, compileall, diff checks, wheel/sdist build and Twine checks passed. The installed wheel outside the checkout ran its demo without CoreCoder or pytest and passed pip check.

Previous release real-model checks are historical evidence for the prior runtime tree; no real-model test is claimed for these repairs. A response can cross the remaining budget; further model calls and tool execution then stop. In-flight request cancellation is unchanged. Existing corrupted duplicate-revision histories are rejected rather than silently rewritten; the repair prevents new corruption.

The final repaired candidate is tagged `release-fixed-20261009`, preserving earlier freeze tags. The repository remains private; no PyPI upload or public release has been performed.
