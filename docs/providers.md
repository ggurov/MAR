# Provider catalog data

The 10 lines below are the user's subscriptions, verbatim from
`problem_statement.txt`. This file is the editorial source for
`src/mar/providers.py`; keep both in sync.

Verification: `yes` = fact confirmed on this machine 2026-10-10 (live
`~/.omp/agent/agent.db` rows or documented public API). `no` = best-known
value, must be confirmed before MAR writes it anywhere (M9 `mar doctor`
promotes/demotes these).

| # | id | display | auth | omp_provider_id | key_url | limits_url | default_model | limits_kind | verified |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | cursor-grok | Cursor / Grok | api_key | (verify) | https://cursor.com/settings | https://cursor.com/usage | (user picks) | manual | no |
| 2 | codex | OpenAI Codex | api_key | (verify) | https://platform.openai.com/api-keys | https://platform.openai.com/usage | (user picks) | manual | no |
| 3 | claude | Anthropic Claude | api_key | (verify) | https://console.anthropic.com/settings/keys | https://console.anthropic.com/settings/usage | (user picks) | manual | no |
| 4 | cline | Cline | api_key | cline-pass | https://cline.bot | (verify) | (user picks) | manual | id yes |
| 5 | opencode | OpenCode | api_key | opencode-go | https://opencode.ai | (verify) | (user picks) | manual | id yes |
| 6 | zai | Z.ai | api_key | zai | (verify) | (verify) | (user picks) | manual | id yes |
| 7 | codebuddy | CodeBuddy | api_key | (verify) | https://www.codebuddy.ai | (verify) | (user picks) | manual | no |
| 8 | openrouter | OpenRouter | api_key | (verify) | https://openrouter.ai/settings/keys | https://openrouter.ai/credits | (user picks) | api | url yes |
| 9 | antigravity | Google Antigravity | api_key | (verify) | https://antigravity.google | (verify) | (user picks) | manual | no |
| 10 | copilot | GitHub Copilot | api_key | (verify) | https://github.com/settings/tokens | https://github.com/settings/copilot | (user picks) | manual | no |

Notes:

- `cursor-grok` is one subscription line in the problem statement; kept as one
  catalog entry. Split it only if the user says they are separate.
- OpenRouter is the only `limits_kind: api` entry at M4
  (`GET /api/v1/credits`, public documented endpoint).
- `(verify)` cells are intentionally empty-ish: the catalog ships the best
  known value in code, `validate()` flags unverified entries, and M9's
  `mar doctor` is the promotion path to `verified`.
- Default models are the user's choice at `mar add` time; the catalog never
  invents one.
