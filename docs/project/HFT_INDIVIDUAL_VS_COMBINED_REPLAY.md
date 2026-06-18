# Individual vs Combined Replay Policy

1. Stage 2: full individual replay for every replay-eligible candidate
2. Execution-realism gates reduce to finalists
3. Stage 4: combined portfolio replay for finalists only

Combined replay artifact class: `combined_replay`. It must never substitute Stage 2 individual evidence.

Manifest requires explicit candidate selection (`--candidate-ids` or `--select-all-replay-eligible`).
