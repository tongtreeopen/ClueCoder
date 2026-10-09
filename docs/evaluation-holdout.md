# Fixed holdout validation suite

`tracecoder-holdout-v1` is a second, versioned set of eight Python repair tasks for
checking a future Agent candidate after development on `tracecoder-baseline-v1`.
Its manifest SHA-256 is
`9faef87735a00b1867dff1ffdd2ba358838ab1cf4cee38cc28a2cde85e6c06fb`.
Keep its prompts, snapshots, Gates, reference patches, task order and budgets fixed
when comparing candidates. The runner records the manifest and Gate hashes for each
run. Do not place Gate tests or reference patches in the Agent workspace.

Validate the fixtures without a model from a clean TraceCoder checkout with the
configured local Docker image:

```powershell
py -3.13 -m pytest tests/test_eval_holdout.py -q
```

The Docker fixture check requires all eight original snapshots to fail their Gate
and each exact reference patch to pass. It verifies the task setup, not Agent
performance. The holdout has not been run with a real model at this freeze point.

To run a future candidate, use a new output directory outside the checkout:

```powershell
py -3.13 -m tracecoder.eval run --suite benchmarks/tracecoder-holdout-v1/manifest.json --output E:\eval-runs\holdout-001 --model YOUR_MODEL --provider openai --base-url https://YOUR_ENDPOINT/v1
```

The same Docker isolation, 900-second task limit, shared 50-call and 100,000-token
budget, independent 120-second Test Gate, trajectory archive, and eight-task
denominator apply to both suites. `summary.json` identifies the suite and its
hash. A candidate comparison should report baseline and holdout results separately,
including usage completeness and failed or missing runs. A fixture pass or an
untested holdout does not establish a success rate or justify promotion.


Isolation boundary: holdout tests are absent from the Agent worker, but candidate
code can read `/gate` during validation because it shares the Gate container.
These results measure local regression performance, not resistance to adversarial
hidden-test inspection.
