# Provider catalog data

The 12 lines below are the user's subscriptions (problem statement minus
codebuddy, dropped by the user; plus `jev`, the Typesafe judge model that
backs omp's `modelRoles.judge`; plus `gemini` and `commandcode`). Editorial
source for `src/mar/providers.py`; keep both in sync.

Verification: every omp id/env var below was confirmed against omp's official
provider roster (omp://providers.md, 2026-10-10); `cline`, `opencode`, `zai`
ids were additionally observed live in `~/.omp/agent/agent.db`. All nine are
`verified=True`; `unverified()` returns empty.

| # | id | display | omp_provider_id | credential route | key_url | limits_url | limits_kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | cursor-grok | Cursor / Grok | cursor | env `CURSOR_ACCESS_TOKEN` | https://cursor.com/settings | https://cursor.com/dashboard/spending | manual |
| 2 | codex | OpenAI Codex | openai-codex | env `OPENAI_CODEX_OAUTH_TOKEN` | https://platform.openai.com/api-keys | https://platform.openai.com/usage | manual |
| 3 | claude | Anthropic Claude | anthropic | env `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys | https://claude.ai/new#settings/usage | manual |
| 4 | cline | Cline | cline-pass | agent.db login row | https://cline.bot | https://app.cline.bot/dashboard/subscription | manual |
| 5 | opencode | OpenCode | opencode-go | agent.db login row | https://opencode.ai | https://opencode.ai/console/ | manual |
| 6 | zai | Z.ai | zai | agent.db login row | https://z.ai/manage-apikey/apikey-list | https://z.ai/manage-apikey/coding-plan/personal/usage | manual |
| 7 | openrouter | OpenRouter | openrouter | agent.db login row | https://openrouter.ai/settings/keys | https://openrouter.ai/credits | api |
| 8 | antigravity | Google Antigravity | google-antigravity | agent.db (OAuth-first) | https://antigravity.google | https://antigravity.google | manual |
| 9 | copilot | GitHub Copilot | github-copilot | env `COPILOT_GITHUB_TOKEN` | https://github.com/settings/tokens | https://github.com/settings/copilot | manual |
| 10 | jev | Jev (Typesafe) | typesafe | env `TYPESAFE_API_KEY` | https://typesafe.ai | https://console.typesafe.ai/usage | manual |
| 11 | gemini | Google Gemini | google | env `GEMINI_API_KEY` | https://aistudio.google.com/api/keys | https://aistudio.google.com/usage | manual |
| 12 | commandcode | Command Code | commandcode | env `COMMAND_CODE_API_KEY` | https://commandcode.ai | https://commandcode.ai/settings/usage | manual |

Notes:

- `jev` is judge-kind infrastructure, not a chat model: omp wires
  `typesafe/jev-latest` (and `jev-preview`) to `modelRoles.judge` only —
  `omp --model typesafe/jev-latest` is intentionally "not found". On fresh
  machines add the role with
  `mar omp-sync --set-role judge=typesafe/jev-latest`.
- `cursor-grok` is one subscription line in the problem statement; kept as one
  catalog entry. omp's `cursor` provider is OAuth-first; the `crsr_` token is
  written to the agent dir's `.env` (documented env fallback), which omp picks
  up when no OAuth session is stored.
- `codex` = ChatGPT/Codex OAuth subscription in omp (`openai-codex`). A plain
  platform.openai.com API key belongs to omp's `openai` provider instead —
  split the entry if the user holds both.
- Credential routes: `env VAR` → `mar omp-sync` writes `VAR=<key>` to
  `<agent_dir>/.env` (omp loads it as environment fallback); agent.db rows are
  login-style and rank above env in omp's credential precedence.
- Live checks 2026-10-10: openrouter key validated against
  `GET /api/v1/credits`; cline/opencode/zai validated by `omp usage` account
  endpoints in an isolated agent dir; cursor env token accepted by omp's
  registry (123 cursor models listed).
- `omp usage` reports quota windows for cline, opencode, and zai out of the
  box; MAR's `mar limits` covers openrouter via API and points at
  `limits_url` for the rest (M8 widens this).
- Default models are the user's choice at `mar add` time; the catalog never
  invents one.
