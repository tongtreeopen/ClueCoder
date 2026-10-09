# ClueCoder first release

- Version: 0.1.0 (independent ClueCoder version sequence).
- Maintainer: Tong ShuKai (local Git identity).
- GitHub repository: https://github.com/tongtreeopen/ClueCoder (private during CI validation).
- Upstream: CoreCoder by Yufeng He; original copyright and MIT license remain in LICENSE.
- This source was curated from a modified local CoreCoder workspace. Its parent HEAD was f03bd9b6f7dd0e5dc96609436c1de3b6b27106a8; that commit alone does not reproduce the copied dirty workspace.
- Compared with the locally saved upstream origin/main 427633641c4e8c624530475b9806a0029df0d388, additions include Goal verification, journals, memory, budget-aware context, Docker execution, structured review, and independent evaluation. This comparison does not claim the remote upstream is unchanged today.
- ClueCoder is developed on top of CoreCoder, with the extensions described above maintained in this project.

## Publication status

Local Git history freezes this curated source. The original local `v0.1.0` tag preserves the first validation snapshot; `release-ready-20261009` identifies the earlier CI-validated candidate. The subsequent four-defect repair is frozen as `release-fixed-20261009`. The validated ClueCoder release is frozen as `cluecoder-0.1.0-ready-20261009`; see the release validation report for the tested implementation revision. The repository was created privately at the user's request to run CI; public visibility and PyPI publication remain separate actions. Repository and Issues URLs are configured in pyproject.toml. CI checks Ubuntu/macOS/Windows with Python 3.10-3.13, installed-wheel isolation, and a separate Ubuntu Docker/Gate job; each run must be read before claiming it passed.

## Validation

See [ClueCoder release validation](validation/2026-10-09-cluecoder-release.md), [real-model repair validation](validation/2026-10-09-real-model-repairs.md), [review repair validation](validation/2026-10-09-review-fixes.md), [release follow-up](validation/2026-10-09-release-followup.md) and [initial first-release verification](validation/2026-10-09-first-release.md). Historical CoreCoder results are retained with their original identity and do not count as tests of this release.

The offline demo runs only a bundled scripted model and generated Fibonacci assertions, using the current Python interpreter. It requires no API key or pytest. It exercises explicit host execution for this fixed demo; ordinary CLI Bash still requires the configured Docker isolation backend.
