# Architecture & Contracts

Everything an implementer needs. Facts marked **verified** were inspected on
the live machine (Windows, `C:/Users/ggurov/.omp`) on 2026-10-10.

## Components

| Module | Owns | Depends on |
| --- | --- | --- |
| `registry.py` | Subscription CRUD + `~/.mar/registry.json` persistence | stdlib |
| `providers.py` | Static catalog of the user's 10 providers + validation | stdlib |
| `limits.py` | Usage snapshots; real fetcher(s) + manual hints | registry, providers |
| `omp.py` | omp credential store + model roles | stdlib + pyyaml |
| `bootstrap.py` | startup prompt renderer; one-shot sync orchestration | registry, providers, omp |
| `store.py` | Storage backends (file), backend selection + config | stdlib |
| `vault.py` | Optional KV-v2 registry backend (stdlib urllib client) | stdlib |
| `tokenpool.py` | Read tokenpool `/v1/models`; build the omp `tokenpool` provider entry for export | stdlib |

## Registry file contract (`~/.mar/registry.json`)

```json
{
  "version": 2,
  "subscriptions": [
    {
      "provider": "commandcode",
      "auth_method": "api_key",
      "key": "user_primary...",
      "models": ["deepseek/deepseek-v4.1-flash"],
      "default_model": "deepseek/deepseek-v4.1-flash",
      "added_at": "2026-10-10T12:00:00+00:00",
      "status": "active",
      "notes": "",
      "accounts": [
        {
          "name": "joesacct1",
          "key": "user_alt...",
          "added_at": "2026-11-08T12:00:00+00:00",
          "notes": ""
        }
      ],
      "selected_account": "joesacct1"
    }
  ]
}
```

- `provider` must exist in the catalog (providers.py) — enforced at `add()`.
- `status` ∈ {`active`, `expired`, `disabled`}; `auth_method` ∈ {`api_key`,
  `oauth`, `none`}; unknown values raise `RegistryError`.
- `default_model`, when set, must be in `models` (add-time validation).
- `accounts` holds alternate keys for the same provider (another billing
  account); labels are unique, nonblank, and must not be the reserved
  `original`. Accounts are api_key-only. `selected_account` names the account
  whose key `mar export`, `mar omp-sync`, `mar limits`, and `mar serve` use;
  empty/`original` means the subscription's own `key`. The primary key is
  never overwritten by account selection.
- `version: 1` registries (no accounts) still load; the next save writes
  version 2.
- `mar serve` payload URLs accept `?accounts=provider=label,...`; the handler
  validates each pair against the registry, applies it in memory only, and
  `mar export --account provider=label` does the same. This is how the page's
  apply commands visibly pin the selected key (the saved selection is
  untouched).
- Writes are atomic (temp file + `os.replace`); `0o600` best-effort; on Windows
  chmod is a no-op — documented, not faked.

### Storage backends (mar.store / mar.vault)

Persistence sits behind a two-method `Store` protocol (`read() -> dict |
None`, `write(payload)`). The default `FileStore` implements the historical
registry.json contract verbatim (atomic write, 0600 best-effort, `$MAR_HOME`
resolution); `VaultStore` implements the same surface over KV-v2 with one
secret per provider (`secret/mar/keys/<provider>`, fields = subscription
record + `updated_at`/`rotated_by`; `_registry` marker holds the version).
Vault saves diff against the server — unchanged providers are not rewritten,
so `mar rotate` is a single PUT and KV-v2 history gives per-provider
rollback. Selection precedence: explicit `--registry` path (always file) >
`--backend` flag > `$MAR_BACKEND` > `~/.mar/config.json` `backend` key >
file. The config file stores no secret values: the token resolves from
`vault.token_file` (read at use time) or `vault.token_env` (default
`VAULT_TOKEN`). The token travels only in the `X-Vault-Token` header — never
in a URL, body, or error message (test-enforced).

## tokenpool provider declaration

`mar export` declares the tokenpool proxy as an omp provider when
`--tokenpool URL` or `$MAR_TOKENPOOL_URL` is set. `tokenpool.py` reads
`{base}/v1/models` (optional `$MAR_TOKENPOOL_TOKEN` bearer), validates the
aliases' `context_window`/`max_tokens`, and emits a keyless
`openai-completions` provider (`auth: none`, `baseUrl` normalized to `/v1`)
alongside any self-hosted endpoint declarations. `mar serve` passes the same
env URL to each `/config.*` payload and shows a Routing card linking to
`<base>/admin` (derived from the page host at :8940 when the env is unset).
MAR never forwards model traffic.

## Usage fetchers

`limits_kind == "api"` catalog entries fetch live usage. `commandcode` uses
`GET https://api.commandcode.ai/alpha/billing/credits` with the key's bearer
token (a MAR user-agent is required; the default Python-urllib UA gets 403),
returning the key's own monthly balance plus 5h/weekly used/cap and reset
epochs — so alternate keys on one provider show different numbers. The
monthly allowance is not returned directly; the rolling caps are fixed slices
of it (weekly = half, 5-hour = a fifth), so MAR derives
`monthly = weekly_cap * 2` (fallback `5h_cap * 5`) and computes usage from
`monthlyCredits - purchased - free`. Both the web page and `mar limits` render
bounded values as inline progress bars (percent used plus a filled bar); exact
fractions stay in the hover title / detail text.

## omp integration contract

**verified** — the agent directory is resolved by omp via `PI_CODING_AGENT_DIR`
(default `~/.omp/agent`). MAR honors the same variable in
`omp.default_agent_dir()`, so `PI_CODING_AGENT_DIR=<dir> mar omp-sync`
configures an isolated omp install — the basis of the end-to-end test below.

Login-style credentials live in SQLite at `<agent_dir>/agent.db`, table
`auth_credentials`, live DDL:

```sql
CREATE TABLE auth_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            credential_type TEXT NOT NULL,
            data TEXT NOT NULL,
            disabled_cause TEXT DEFAULT NULL,
            identity_key TEXT DEFAULT NULL,
            created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
            updated_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
         )
```

`mar omp-sync` also writes `hindsight.autoRetain = false` into
`<agent_dir>/config.yml` (`omp.write_hindsight_settings`), so only explicit
`retain` tool calls feed the memory bank — full-session auto-retain would
mirror raw transcripts into it. Other `hindsight.*` keys (e.g. `apiUrl`)
are preserved. See `docs/transcript-search.md` for the transcript-search
follow-up this decision defers.

Live rows use `credential_type = 'api_key'` and `data = '{"key":"..."}'`
(JSON object with a `key` field). Provider ids observed live: `opencode-go`,
`cline-pass`, `zai`.

MAR write semantics (`upsert_credential`):

1. Open the db (create with the exact DDL above if missing — fresh installs).
2. `DELETE` existing rows for `(provider, credential_type='api_key')`.
3. `INSERT` new row with `data = json.dumps({"key": key})`.
4. Never touch rows of other credential types or other providers.

**verified** — model roles live in `~/.omp/agent/config.yml` under `modelRoles`
(e.g. `default: zai/glm-5.3-flash:max`, `judge: typesafe/jev-latest`). MAR edits
via `yaml.safe_load` → update `modelRoles` → `yaml.safe_dump(..., sort_keys=False)`.
Accepted reformatting tradeoff: omp owns this file too and rewrites it
(`setupVersion`); no comments were present in the inspected file.

**verified** — env-var credential route: omp loads `<agent_dir>/.env` and treats
`VAR=value` as an environment fallback for providers whose catalog entry
declares one (e.g. `CURSOR_ACCESS_TOKEN` for the OAuth-first `cursor`
provider). MAR's `omp.upsert_env_key()` replaces the first line for that var
(`export ` prefix tolerated) and appends otherwise, preserving all other
content. `bootstrap.sync` routes a provider to `.env` when its catalog entry
has `omp_env_var` set, else to an `agent.db` login-style row (which ranks
above env in omp's credential precedence).

**verified** — omp's status line accepts only its fixed segment ids
(`omp config set statusLine.leftSegments '["provider"]'` is rejected), and the
built-in `model` segment renders `model.name` — the catalog display name, not
the API id. The provider is not selectable at all. MAR therefore pins
`statusLine` to `leftSegments = [vim, hostname, mode, path, git, pr]` (no
`model`) and installs a dependency-free extension at
`<agent_dir>/extensions/mar-statusline.js`. It runs on `session_start` /
`session_switch`, polls `ctx.model` and `pi.getThinkingLevel()` for `/model`
and effort changes, and calls `ctx.ui.setWidget("mar-model", [`<glyph>
${provider}/${id}`], { placement: "aboveEditor" })`, where `<glyph>` is the
compact first character of the theme's thinking-level label for models that
expose levels (`off` uses the disabled symbol), else the theme's model icon.
omp renders it on its own line above the composer band.
The extension is rewritten on every sync/export, so it is MAR-owned.

## Provider catalog

Fourteen entries (see docs/providers.md for the data + verification status).
Fields:

`id, display, auth_methods, key_url, limits_url, signup_url, omp_provider_id,
omp_env_var, export_env_var, export_oauth_env_var, default_model,
limits_kind, verified, notes, omp_base_url_env_var, base_url,
context_window, omp_models_api`

- `limits_kind` ∈ {`api`, `manual`} — drives limits.py dispatch.
- `omp_provider_id` is what MAR writes into `agent.db.provider`. Only ids
  observed live are trustworthy; the rest carry `verified: no` in
  docs/providers.md and must be confirmed before M9 promotes them.
- `validate()` returns a list of problem strings; tests assert it is empty for
  the shipped catalog.

**verified 2026-10-31, endpoint moved 2026-11-07** — self-hosted engine
route. `strata` is a keyless llama.cpp server on the LAN
(`http://deepseek.nj02.gurov.us/v1`; the previous
`http://192.168.1.203:8080/v1` is gone). omp discovers that engine
implicitly as provider id `llama.cpp`, reading the endpoint from
`LLAMA_CPP_BASE_URL` (agent `.env` works; verified live) and speaking the
Responses API (`POST /v1/responses` answers; the server also serves `POST
/v1/chat/completions`, `GET /v1/models`, `GET /health`; tool calls complete
end to end). MAR therefore stores a subscription with `auth_method="none"`,
writes no credential, and puts `LLAMA_CPP_BASE_URL=<base_url>` in `.env` on
sync/export. The omp model reference is `llama.cpp/<model id>`
(`deepseek-v4.1-flash-1017-xq-v4`); omp's discovery fills context window
(`meta.n_ctx` = 1048576) and input modalities from the server, and `mar
limits strata` probes `{base_url}/models` (a LAN endpoint, not a web page —
the fetchers that talk plain `http://` by design).

A second keyless server (`qwen`, `http://qwen2.nj02.gurov.us/v1`, verified
2026-11-02, hostname updated 2026-11-07) uses omp's other built-in local
route: provider id `lm-studio` with `LM_STUDIO_BASE_URL`, model ref
`lm-studio/<model id>`. omp supports one endpoint per built-in local
provider, so each LAN engine needs its own route; both use the same generic
`{base_url}/models` limits probe.

**verified** — omp's `llama.cpp` discovery reads `meta.n_ctx` from
`GET /v1/models`, but its `lm-studio` discovery only understands LM Studio's
native `/api/v0/models`; a bare llama.cpp server leaves it on the fallback
128k window (observed live 2026-11-08). Since an explicit
`providers.<id>` entry in `<agent_dir>/models.yml` replaces built-in
discovery, MAR declares every model from the subscription there with the
catalog's verified `context_window` (1048576 for qwen) under
`omp_models_api` (`openai-completions`), plus `auth: none` and `baseUrl`.
`bootstrap.sync` merges that entry into an existing `models.yml`
(other providers untouched); `mar export` emits the whole file for a fresh
machine. `strata` keeps built-in discovery (`omp_models_api` empty) because
it already reports the right window.

## Limits

- `limits_kind == "api"` → real fetcher. Shipped: OpenRouter
  `GET https://openrouter.ai/api/v1/credits` with `Authorization: Bearer <key>`
  (documented public API), Gemini's models list (key liveness), and the
  self-hosted `strata` engine's `GET {base_url}/models` (loaded model, context
  window, input modalities). Tests mock HTTP (monkeypatched
  `urllib.request.urlopen`); no unit test hits the network.
- `limits_kind == "manual"` → `manual_hint()` returns provider's `limits_url`
  + one-line instruction; a self-hosted entry that has only a `base_url` names
  its endpoint and the `mar limits <id>` probe instead. Honest UX: MAR says
  what it cannot fetch and why.
- `LimitSnapshot.source` ∈ {`api`, `manual`} so the CLI can label output.

## Bootstrap prompt

`render_prompt(registry)` produces markdown for pasting into a fresh omp
session. It must:

- list every active subscription with provider id + default model (no keys);
  a self-hosted entry also carries `endpoint: <base_url>` and the omp model
  reference (`llama.cpp/<model id>`), so the line is copy-pasteable,
- instruct omp to run `mar omp-sync` (keys reach omp through MAR, not the
  prompt),
- append the contents of `~/.mar/prompt.md` (custom follow-up) when present,
- fail loudly (`BootstrapError`) if the registry is empty — an empty prompt is
  a bug, not a feature.

Tests assert key material never appears in output (a fixture key is injected
into the registry and must not leak).

## Security posture

- Keys at rest: plaintext in `~/.mar/registry.json`, 0600 best-effort
  (`~/.mar` created 0700; loose-mode files self-tighten on read; failed
  atomic writes never strand a plaintext temp file). Explicit v1 tradeoff;
  OS keyring is a future milestone (M10+, unplanned). Opt-in alternative:
  the Vault backend (see Storage backends above) moves keys at rest into
  KV-v2 with versioning, audit, and per-provider rollback.
- CLI output masks keys (`first4…last4`, or `<redacted>` if shorter than 12
  chars). `--json` output masks too. `mar export` prints plaintext by design
  (the pastable is the product); `--redact` previews the block masked, and
  `--output` writes 0600.
- `mar serve` exposes live credentials over plaintext HTTP by design (LAN
  apply); it warns on non-loopback binds — treat the port as trusted-LAN
  only.
- This repo must never contain real keys: tests use fake fixtures, `.mar/`
  is gitignored.

## OAuth tokens (pull + export)

**verified** — omp stores OAuth logins in the same `auth_credentials` table
with `credential_type = 'oauth'` and JSON payload
`{"access", "refresh", "expires" (epoch ms), "authorizedAt", "email",
"accountId", "orgId", "orgName"}` (anthropic + openai-codex, 2026-10-10).

- `mar pull` reads those rows (`omp.read_oauth_credentials`), maps
  `provider` → catalog id via `omp_provider_id`, and upserts registry subs
  with `auth_method="oauth"`, `key=access`, notes carrying account + expiry.
  Refresh tokens stay in omp's store — they are never copied into MAR.
- `mar pull` also reads `<agent_dir>/.env` (`omp.read_env_credentials`),
  omp's environment fallback. On env-configured installs `agent.db` holds
  no rows at all, so this is the only credential source. Catalog env vars
  map back to providers (`omp_env_var` / `export_env_var` → `api_key`,
  `export_oauth_env_var` → `oauth`); `agent.db` OAuth rows win because
  they carry refresh metadata.
- `mar pull` merges, it does not overwrite, when the registry already holds
  alternate accounts (`registry.merge_pulled_key`). omp's `.env`/`agent.db`
  only carry the credential omp *serves* — `mar export` writes
  `effective_key`, i.e. the selected account's key — so a pulled key that
  matches a stored account updates that account (or is a no-op) and never
  replaces the subscription's own key. Without this, one key would be
  stored twice; key-deduping consumers (tokenpool routing) collapse the
  duplicate and every account row reports the same billing usage.
- `mar export` emits OAuth subs as `export_oauth_env_var` lines:
  `ANTHROPIC_OAUTH_TOKEN`, `OPENAI_CODEX_OAUTH_TOKEN` (documented omp env
  routes). omp on the target machine reads them from `.env` but CANNOT
  refresh them — env credentials have no refresh flow. Access-token
  lifetimes observed: anthropic ≈ 8h, openai-codex ≈ 10d. The source
  machine's omp refreshes its stored copy automatically; re-run
  `mar pull && mar export` to refresh the pastable.

## Verification log

- 2026-10-10: inspected live `~/.omp/agent/agent.db` (`auth_credentials`
  schema + rows) and `~/.omp/agent/config.yml` (`modelRoles`) — contracts
  above derive from that inspection, not from docs.
- 2026-10-10: omp provider roster cross-checked (omp://providers.md,
  omp 18.8.7): `openrouter`, `cursor`, `anthropic`, `openai-codex`,
  `google-antigravity`, `github-copilot` are built-ins with the env vars
  listed in docs/providers.md; `codebuddy` is not in the roster (user dropped
  it from the catalog).
- 2026-10-10: end-to-end proof in an isolated agent dir
  (`PI_CODING_AGENT_DIR=<tmp>`): `mar omp-sync` from an empty dir →
  `omp usage` returned live quota windows for cline-pass, opencode-go, and
  zai; `omp models find` listed 568 openrouter and 123 cursor models;
  `omp --model zai/glm-5.3-flash` completed a one-shot inference. No omp
  code was modified — configuration only.
- 2026-10-10: pastable-only proof (bare machine: no MAR, no agent.db, only
  `mar export` output applied via PowerShell): `omp usage` live for
  cline/opencode/zai; one-shot inference via
  `anthropic/claude-haiku-4-5` (`ANTHROPIC_OAUTH_TOKEN` env) and
  `openai-codex/gpt-5.6-luna` (`OPENAI_CODEX_OAUTH_TOKEN` env) both
  succeeded.
