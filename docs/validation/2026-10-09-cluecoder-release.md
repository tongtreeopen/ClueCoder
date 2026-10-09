# ClueCoder release validation

Implementation revision: `a504ecf55565d00442abe0be6359f35640e3468f`.
Runtime tree: `f8a1349e012b5d1f59b2ae1a98745bb95f47d382`.
Test tree: `b2dfeb7096f4e3c6aed5bfa492c37e2a1b3791ca`.

The distribution, import package, CLI, configuration prefix, state directories, Docker labels, suite IDs, documentation and private GitHub repository use ClueCoder. CoreCoder authorship and MIT notices remain. See [project origin](../PROJECT_ORIGIN.md) for the CoreCoder foundation and current configuration.

[CI run 37934319139](https://github.com/tongtreeopen/ClueCoder/actions/runs/37934319139) passed all 15 jobs against the implementation revision:

- Python 3.10–3.13 on Windows: 552 passed, 48 skipped for each matrix entry.
- Python 3.10–3.13 on Ubuntu and macOS: 549 passed, 51 skipped for each entry.
- Separate native Ubuntu Docker execution and independent Gate job: 78 passed, no skips.
- Package build, installed-wheel isolation/demo, and Ruff passed.

Local checks: wheel and sdist build; Twine metadata check; Ruff; compileall; `CLUECODER_*` configuration and `.cluecoder` session namespace checks. A fresh runtime-only virtual environment outside the checkout imported the installed wheel with no `corecoder`, `tracecoder` or `pytest`, ran `cluecoder --version` and the offline scripted demo, and passed `pip check`.

Local full suite on Windows/Python 3.13 with Docker available: **599 passed, 1 skipped in 551.27 seconds**. The skip requires native Linux non-root execution; that case passed in the separate Ubuntu Docker CI job.

92 tracked historical evidence and benchmark task files were compared byte-for-byte in Git against reference revision `f48ffef08762212d4f080b18442c963d35fdc339`; all were unchanged. Benchmark task contents and historical hashes retain their recorded meaning. Previous release tags remain unchanged.

No fresh real-model call was made for this release check. Earlier real-model results retain their tested revision and original recorded identifiers. The repository remains private; neither public release nor PyPI upload was performed.

## Documentation corrections after the recorded release checks

The subsequent documentation-only correction repaired 16 relative source links and 12 stale current-source banners, updated configuration names and demo behavior, refreshed both README validation summaries and command tables, and marked the initial release report as superseded for current status. All relative Markdown file links under `docs/` resolved locally; all 79 historical `blob/f48ffef…/tracecoder/` URLs were unchanged. Runtime source, tests, examples, package metadata and CI were unchanged. The byte-for-byte statement above describes the recorded release snapshot; later report prose/link edits are not changes to raw traces or benchmark tasks. No new full suite or model run was required or claimed for these prose corrections.
