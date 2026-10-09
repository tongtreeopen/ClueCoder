> Historical snapshot; superseded for current release status by [ClueCoder release validation](2026-10-09-cluecoder-release.md) and [release record](../RELEASE.md). The checks and publication status below describe the initial snapshot only.

# TraceCoder 0.1.0 first-release verification

Date: 2026-10-09. This report describes the curated TraceCoder publication copy, not historical CoreCoder snapshots.

## Changes

Legacy file-tool tests explicitly bind their temporary workspace and use fresh tools. Runtime workspace restrictions remain enforced. The offline scripted demo runs generated Python assertions without the optional pytest dependency. The maintainer is taken from local Git identity; upstream author and MIT copyright are retained. Version 0.1.0 starts the independent TraceCoder version sequence.

## Verified snapshot

- Clean regression commit: `316d9a28cee36bdbc3e984a8b1ae7bd5f64323a9`.
- `tracecoder/` Git tree: `c8258cf3728c12730204abe2c31e440c7289d900`.
- `tests/` Git tree: `d381a282a8a540d4c646e39a5dad7c7a0ff35ccf`.
- The final release documentation commit preserves these exact source and test trees. It only records results and release documentation.

## Current checks

| Check | Result |
| --- | --- |
| Windows / Python 3.13 full regression: `py -3.13 -m pytest tests -q` | 539 passed, 47 skipped, 0 failed; 283.05 seconds |
| Focused repair regression | 111 passed |
| `py -3.13 -m ruff check tracecoder tests examples` | Passed |
| `py -3.13 -m compileall -q tracecoder tests` | Passed |
| Wheel build and `python -m twine check` | Passed for the tested source snapshot |
| Install Wheel with its declared dependencies in a fresh venv | Passed; no system site-packages |
| Installed import from outside the checkout / `--version` | Passed; version 0.1.0; CoreCoder absent |
| Installed `--demo` | Passed; generated Fibonacci assertions executed; pytest absent |
| Fresh-venv `pip check` | No broken requirements |
| Relative Markdown links | No missing files |
| Tracked-file credential-pattern scan | No matches for private-key headers, common GitHub tokens or OpenAI-style tokens; not a comprehensive secret audit |

The full regression log is preserved as [pytest output](2026-10-09-first-release-pytest.txt). Final distributions and their checksums are recorded in `release-manifest.json` beside the built artifacts, outside this checkout.

## Limits and publication status

Docker Engine preflight failed because dockerDesktopLinuxEngine is unavailable. The 47 skipped tests are not counted as passes. No real-model or Docker end-to-end validation is claimed. Linux/macOS and Python 3.10-3.12 remain for CI. Fresh-install smoke checks do not establish live model-provider compatibility.

GitHub lookup did not resolve `tongtreeopen/TraceCoder`. It remains the intended publication target; no external repository was created, no commit was pushed, and no PyPI package was published. A Repository URL will be added once an actual remote exists. Historical validation paths may refer to the original machine and are retained as provenance, not portable release artifacts.

The offline demo is a fixed scripted host-execution example. Ordinary CLI Bash still requires the configured Docker isolation backend; this release does not weaken that boundary.
