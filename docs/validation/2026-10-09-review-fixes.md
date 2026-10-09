# Release review repairs, 2026-10-09

Repairs the four reproduced findings from the review of `0f2b26bf896fc2396725c7ed9d852862d9a9917c`: concurrent work-card revision corruption; uncharged Goal child usage; shared workflow parent binding; missing child evaluation scope.

## Implementation

- WorkStateStore mutations hold a journal transaction and reload current persisted state before deriving the next card. Snapshot writes remain inside that transaction.
- Child model requests, including report corrections, use the outer Goal usage lock, check its budget before requests, charge known usage, and stop before executing tool calls on exhausted or unknown usage. Child completion does not run main Goal acceptance.
- Workflow tool instances are copied per child; parent bindings and usage/work-card context remain intact.
- Children inherit the evaluation file scope and Plan Mode.

## Validation

Results are being collected. Previous release CI and real-model checks are historical evidence for the prior runtime tree; no real-model test is claimed for these repairs.
