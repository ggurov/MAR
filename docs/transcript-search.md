# Transcript full-text search — options

Context (2026-11-10): omp's hindsight auto-retain was dumping whole session
transcripts into the memory bank (`hindsight.retainMode = "full-session"`,
default). We disabled it (`hindsight.autoRetain: false`, distributed via
`mar.omp.write_hindsight_settings()`), because transcripts are search
material, not memories. This doc records the ways to give transcripts a
real full-text-search home later. Nothing is implemented yet.

## Where transcripts already live

- Main sessions: `~/.omp/agent/sessions/<encoded-cwd>/<timestamp>_<sessionId>.jsonl`
  — JSONL, one entry per line (header, user/assistant turns, tool calls,
  timestamps; title slot in the first 256 bytes). Layout and format:
  omp docs "Session Storage and Entry Model" (`omp://session.md`).
- Subagents: their own JSONL files; advisor transcripts land next to the
  owning session as `__advisor*.jsonl`.
- Light index already exists: `~/.omp/agent/history.db` (titles, idle
  recap journal) — not full text.
- Local outbox for memory retains: `~/.omp/agent/hindsight-outbox/`
  (not a transcript store; empty since autoRetain is off).

## Option 1 — index the sessions directory (recommended first step)

No omp changes. A small indexer watches `~/.omp/agent/sessions/` and feeds
any FTS engine:

- SQLite FTS5 (single file, ships with Python) or Meilisearch (LAN service
  on stor2) both work; ripgrep alone covers casual grep.
- One JSONL entry = one document (id, sessionId, cwd bucket, timestamp,
  role, text). Subagent and advisor files included.
- Runs as a user systemd timer on each box that runs omp; or a one-shot
  `mar transcript-index` command via the MAR CLI.

## Option 2 — push via omp extension/hook

Incremental shipping to a remote search endpoint (e.g. on stor2):

- Extension events (omp://extensions.md): `agent_end`, `session_stop`,
  `turn_end`, plus `onSession(event)` lifecycle (`start|switch|branch|tree|
  shutdown`). On `session_stop`/`agent_end`, read the session JSONL delta
  and POST it to the search service.
- Hook files are lighter: `--hook <path>`, native discovery under
  `<agentDir>/hooks/pre|post/` and `<cwd>/.omp/hooks/pre|post/`
  (omp://hooks.md, omp://extension-loading.md).
- Distribution: MAR already installs an omp extension (status-line JS) via
  `omp.write_status_line_extension()`; a transcript-shipper hook would ride
  the same mechanism (`omp.py`, `bootstrap.sync`). Pair with a
  `hindsight.autoRetain`-style config write if it ever needs a toggle.

## Option 3 — one-off pulls

- `/export` (session export/share), `omp render` (whole thread through the
  production transcript pipeline), `/handoff` markdown artifacts
  (`compaction.handoffSaveToDisk`). Fine for occasional grep-after, not a
  standing search box.

## What does not exist

- No native "transcript sink" config: nothing in `hindsight.*` /
  `mnemopi.*` / env vars routes raw transcripts anywhere except the two
  memory backends. Hindsight itself has no FTS surface (memory_units +
  vector/BM25 only, and we no longer feed it transcripts).
- Mnemopi (the other memory backend) stores transcripts locally as
  "episodes" in SQLite, but that is memory-shaped, not search-shaped.

## Reference omp internals

- Transcript extraction for auto-retain (what we turned off):
  `packages/coding-agent/src/hindsight/transcript.ts`
- Session persistence: `packages/coding-agent/src/session/*` (see
  omp://session.md implementation-file list).
