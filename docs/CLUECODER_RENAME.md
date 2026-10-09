# ClueCoder naming migration

On 2026-10-09 this project was renamed from TraceCoder to ClueCoder to avoid confusion with the unrelated TraceCoder research project (arXiv:2602.06875). It remains derived from CoreCoder; upstream authorship and the MIT license are preserved.

The distribution, import package and CLI are now `cluecoder`. Configuration uses `CLUECODER_*`, and new state is stored under `.cluecoder`. The previous CLI and `TRACECODER_*` settings are not aliases. Back up existing `.tracecoder` data before manually migrating it; no automatic state migration is performed. Generic provider settings such as `OPENAI_API_KEY` retain their existing behavior.

The private repository is https://github.com/tongtreeopen/ClueCoder. The physical release checkout may retain its original directory name.

Historical tags, validation records, model traces and hashes retain their original TraceCoder names and identify the revisions that were actually tested. They are not evidence of new model runs after this migration. Benchmark suite IDs are renamed; task snapshots and Gate definitions are unchanged.
