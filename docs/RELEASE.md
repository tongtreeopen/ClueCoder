# TraceCoder first release

- Version: 0.1.0 (independent TraceCoder version sequence).
- Maintainer: Tong ShuKai (local Git identity).
- GitHub repository: https://github.com/tongtreeopen/TraceCoder (private during CI validation).
- Upstream: CoreCoder by Yufeng He; original copyright and MIT license remain in LICENSE.
- This source was curated from a modified local CoreCoder workspace. Its parent HEAD was f03bd9b6f7dd0e5dc96609436c1de3b6b27106a8; that commit alone does not reproduce the copied dirty workspace.
- Compared with the locally saved upstream origin/main 427633641c4e8c624530475b9806a0029df0d388, additions include Goal verification, journals, memory, budget-aware context, Docker execution, structured review, and independent evaluation. This comparison does not claim the remote upstream is unchanged today.
- Compared with the local extended CoreCoder workspace, TraceCoder began as a renamed publication copy, not a separate implementation of those capabilities.

## Publication status

Local Git history and the release tag freeze this curated source. The repository was created privately at the user's request to run CI; public visibility and PyPI publication remain separate actions. Repository and Issues URLs are configured in pyproject.toml. CI checks Ubuntu/macOS/Windows with Python 3.10-3.13, installed-wheel isolation, and a separate Ubuntu Docker/Gate job; each run must be read before claiming it passed.

## Validation

See [first-release verification](validation/2026-10-09-first-release.md). Historical CoreCoder results are retained with their original identity and do not count as tests of this release.

The offline demo runs only a bundled scripted model and generated Fibonacci assertions, using the current Python interpreter. It requires no API key or pytest. It exercises explicit host execution for this fixed demo; ordinary CLI Bash still requires the configured Docker isolation backend.
