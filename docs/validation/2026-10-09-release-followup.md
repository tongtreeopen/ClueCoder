# Release follow-up: CI, Docker and model evidence

The user authorized a private repository for CI: [tongtreeopen/TraceCoder](https://github.com/tongtreeopen/TraceCoder). Public visibility and PyPI upload have not been performed.

## Revision and scope

CI checks commit `9ae2af7c42b42018c7b57e2ec8f7d7980c751823`. Its runtime source tree is `6cf6247b52424e24454647501063e06838561ad0`; the real-model-tested commit `b7dbd84f4a0e76373e0ef19a8fe0ec4dab082df6` has the identical runtime tree. Later documentation commits do not change runtime source, tests, examples, dependencies or CI.

## Validation

- [Cross-platform CI](https://github.com/tongtreeopen/TraceCoder/actions/runs/37899328094): Ubuntu/macOS/Windows, Python 3.10-3.13. All 15 jobs passed. Each Windows combination: 543 passed / 48 skipped; each Ubuntu/macOS combination: 540 passed / 51 skipped. Matrix jobs do not pull the Docker image; Docker-dependent and platform-specific skips are covered separately where supported. See [job summaries](release-smoke-20261009/ci-summary.json).
- Native Ubuntu Docker job: **78 passed**, no skips, in 90.94 seconds. Includes execution, background cancellation, fixture discrimination, and independent Gate/runner checks with the pinned image.
- CI package and lint jobs passed. Wheel/sdist were also rebuilt locally and passed Twine checks. A fresh Windows environment installed the wheel outside the source checkout, has neither CoreCoder nor pytest, passed `pip check`, and ran the scripted demo successfully.
- Local Windows/Python 3.13 complete Docker-enabled regression: **590 passed, 1 skipped**, no failures, in 567.29 seconds. The skipped case requires a native nonroot Linux host; the Ubuntu Docker job exercised it. Runtime, tests, examples, dependencies and CI match the CI-tested commit.

The CI failures led to concrete fixes: declare the test-only httpx dependency under current SDK versions; align the supported Ruff version; use the Linux caller UID/GID for ordinary workspace writes while retaining the prepared evaluation sandbox user; isolate the interrupt test clock from subprocess internals; use the current interpreter and an explicit quoted test path in the Plan/Hooks example; return failure when its command fails; compare the actual image ID rather than assuming it equals a repository digest; prepare the evaluation workspace in its timeout fixture.

## Real model smoke

Task: fixed `retry_backoff` snapshot from `tracecoder-baseline-v1`; initial Gate failed; only `solution.py` was allowed. Model: `deepseek-v4-flash`. All attempts use the same independent Docker Gate, 50-call and 900-second ceilings. Token budget is checked between calls, so an individual request can exceed the remaining budget.

| Attempt | Token budget | Calls / tokens | Agent / Gate |
| --- | --- | --- | --- |
| 1, earlier revision | 12,000 | 3 / 14,960 | agent_limit / test_fail |
| 2, earlier revision | 50,000 | 7 / 42,491 | pass / pass |
| 3, final runtime tree | 50,000 | 5 / 27,663 | pass / pass |

Final attempt took 24.71 seconds, changed only `solution.py`, and recorded a complete model-call trace. This is a single-task smoke check, not a baseline success rate or one-shot success claim. [Portable evidence](release-smoke-20261009/smoke-summary.json) retains every attempt and hashes of the published final patch, Gate output and model-call usage. Published text uses LF; full local trajectories and credentials are not included in the repository.

## Remaining publication actions

The final candidate is frozen as `release-ready-20261009`; the original local `v0.1.0` snapshot is preserved. The repository is private. No GitHub public release or PyPI upload has occurred. The PyPI JSON endpoint returned 404 at the name check; this does not reserve the name or guarantee a future upload. README package links now use actual repository URLs, which require repository access while private. Historical CoreCoder results remain separate in [historical validation](../HISTORICAL_VALIDATION.md).
