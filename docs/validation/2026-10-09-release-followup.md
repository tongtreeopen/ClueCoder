# Release follow-up: CI, Docker and model evidence

The user requested a private GitHub repository for cross-platform CI. The repository is https://github.com/tongtreeopen/TraceCoder; its visibility is private. Public publication and PyPI upload have not been requested or performed by this validation.

## Prepared checks

- Matrix: Ubuntu, macOS, Windows; Python 3.10, 3.11, 3.12, 3.13.
- Packaging: wheel and sdist validation; installed-wheel scripted demo without CoreCoder or pytest.
- Docker: separate pinned-image execution and independent Gate job.
- Real model: one fixed `retry_backoff` task, untouched snapshot, only `solution.py` permitted, existing independent Docker Gate, 12,000 reported-token budget, 50-call / 900-second ceilings. Model credentials are read from the existing private local configuration only inside the validation process.

Results are pending. A single successful task will be reported as a smoke check, not a baseline success rate. Historical results remain separated in [historical validation](../HISTORICAL_VALIDATION.md).
