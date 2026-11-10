# Provider catalog data

The 14 lines below are the user's subscriptions (problem statement minus
codebuddy, dropped by the user; plus `jev`, the Typesafe judge model that
backs omp's `modelRoles.judge`; plus `gemini` and `commandcode`; plus the
user's two self-hosted llama.cpp boxes, `strata` and `qwen`). Editorial
source for `src/mar/providers.py`; keep both in sync.

Verification: every omp id/env var below was confirmed against omp's official
provider roster (omp://providers.md, 2026-10-10); `cline`, `opencode`, `zai`
ids were additionally observed live in `~/.omp/agent/agent.db`; `strata` and
`qwen` were probed live on their LAN endpoints (2026-10-23 through
2026-11-02). All fourteen are `verified=True`; `unverified()` returns empty.

| # | id | display | omp_provider_id | credential route | key_url | limits_url | limits_kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | cursor-grok | Cursor / Grok | cursor | env `CURSOR_ACCESS_TOKEN` | https://cursor.com/settings | https://cursor.com/dashboard/spending | manual |
| 2 | codex | OpenAI Codex | openai-codex | env `OPENAI_CODEX_OAUTH_TOKEN` | https://platform.openai.com/api-keys | https://platform.openai.com/usage | manual |
| 3 | claude | Anthropic Claude | anthropic | env `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys | https://claude.ai/new#settings/usage | manual |
| 4 | cline | Cline | cline-pass | agent.db login row | https://cline.bot | https://app.cline.bot/dashboard/subscription | api (`GET /api/v1/users/{id}/balance`, millionths USD) |
| 5 | opencode | OpenCode | opencode-go | agent.db login row | https://opencode.ai | https://opencode.ai/console/ | api (`GET /zen/go/v1/usage`: Go 5h/weekly/monthly windows) |
| 6 | zai | Z.ai | zai | agent.db login row | https://z.ai/manage-apikey/apikey-list | https://z.ai/manage-apikey/coding-plan/personal/usage | manual |
| 7 | openrouter | OpenRouter | openrouter | agent.db login row | https://openrouter.ai/settings/keys | https://openrouter.ai/credits | api |
| 8 | antigravity | Google Antigravity | google-antigravity | agent.db (OAuth-first) | https://antigravity.google | https://antigravity.google | manual |
| 9 | copilot | GitHub Copilot | github-copilot | env `COPILOT_GITHUB_TOKEN` | https://github.com/settings/tokens | https://github.com/settings/copilot | manual |
| 10 | jev | Jev (Typesafe) | typesafe | env `TYPESAFE_API_KEY` | https://typesafe.ai | https://console.typesafe.ai/usage | manual |
| 11 | gemini | Google Gemini | google | env `GEMINI_API_KEY` | https://aistudio.google.com/api/keys | https://aistudio.google.com/usage | manual |
| 12 | commandcode | Command Code | commandcode | env `COMMAND_CODE_API_KEY` | https://commandcode.ai | https://commandcode.ai/settings/usage | api (`GET /alpha/billing/credits`: per-key balance + 5h/weekly windows; monthly allowance derived from the rolling caps) |
| 13 | strata | Strata (local llama.cpp) | llama.cpp | env `LLAMA_CPP_BASE_URL` (endpoint; no key) | — | `{endpoint}/models` probe | api |
| 14 | qwen | Qwen (local llama.cpp) | lm-studio | env `LM_STUDIO_BASE_URL` (endpoint; no key) | — | `{endpoint}/models` probe | api |

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
- Added 2026-11-10: cline credit balance via
  `GET /api/v1/users/{id}/balance` (integer millionths of USD; $13.57 live),
  and OpenCode Go windows via `GET /zen/go/v1/usage` (0/32/100% live). OpenCode
  API keys cannot read the Zen wallet balance; only a browser console session
  can, so MAR shows the Go windows instead.
- `omp usage` reports quota windows for cline, opencode, and zai out of the
  box; MAR also fetches the live cline balance and OpenCode Go windows.
- Default models are the user's choice at `mar add` time; the catalog never
  invents one.

## strata (self-hosted llama.cpp)

Probed live 2026-11-07 at `http://deepseek.nj02.gurov.us/v1` (llama.cpp
server; moved off the retired `http://192.168.1.203:8080/v1`):

- `GET /v1/models` → one model, `deepseek-v4.1-flash-1017-xq-v4`,
  `status.value: loaded`, `meta.n_ctx: 1048576`, input modality `text`.
- `POST /v1/chat/completions` answers; `POST /v1/responses` answers too (the
  API shape omp's `llama.cpp` provider uses — reasoning arrives as
  `reasoning_text`). `GET /health` → 200.
- omp, isolated agent dir, `.env` holding only
  `LLAMA_CPP_BASE_URL=http://deepseek.nj02.gurov.us/v1`, completed both a
  plain reply and a `bash` tool call as `--model
  llama.cpp/deepseek-v4.1-flash-1017-xq-v4`.
- Keyless: the registry row uses `auth_method: "none"`, MAR writes no
  credential, and `mar omp-sync` / `mar export` only add the endpoint env
  line. `mar limits strata` probes `{endpoint}/models`.

## qwen (second self-hosted llama.cpp)

Probed live 2026-11-07 at `http://qwen2.nj02.gurov.us/v1` (the old
`http://qwen.nj02.gurov.us/v1` name now resolves to a Strata server and is
rejected by its DNS-rebinding guard):

- `GET /v1/models` → one model, `qwen3.8-flash-next-abliterated-iq3_s`,
  `status.value: loaded`, `meta.n_ctx: 1048576`, input modality `text`.
- `POST /v1/chat/completions` answers (`ok`); `POST /v1/responses`
  answers 200; `GET /health` → 200.
- omp has no second `llama.cpp` slot, so MAR maps this box to omp's built-in
  OpenAI-compatible `lm-studio` provider: `LM_STUDIO_BASE_URL` is the env
  route and the model ref is
  `lm-studio/qwen3.8-flash-next-abliterated-iq3_s`. Verified live in an
  isolated agent dir (plain reply) and via `omp models lm-studio`.
- Keyless and probed by `mar limits qwen`; `omp-sync`/`export` add the
  endpoint env line.
- Context window: omp's built-in `lm-studio` discovery expects LM Studio's
  native `/api/v0/models` and otherwise falls back to 128k, so MAR also
  writes `models.yml` with `auth: none`, the endpoint, and the model's
  verified `contextWindow: 1048576` (`omp_models_api:
  openai-completions`). Verified live 2026-11-08: `omp models lm-studio`
  reports `contextWindow: 1048576`, and a plain reply still completes.
