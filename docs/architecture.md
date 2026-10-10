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
| `cli.py` | argparse wiring, exit codes, key masking | all of the above |

## Registry file contract (`~/.mar/registry.json`)

```json
{
  "version": 1,
  "subscriptions": [
    {
      "provider": "zai",
      "auth_method": "api_key",
      "key": "069b...",
      "models": ["glm-4.7", "glm-5.3-flash"],
      "default_model": "glm-5.3-flash",
      "added_at": "2026-10-10T12:00:00+00:00",
      "status": "active",
      "notes": ""
    }
  ]
}
```

- `provider` must exist in the catalog (providers.py) — enforced at `add()`.
- `status` ∈ {`active`, `expired`, `disabled`}; `auth_method` ∈ {`api_key`,
  `oauth`}; unknown values raise `RegistryError`.
- `default_model`, when set, must be in `models` (add-time validation).
- Writes are atomic (temp file + `os.replace`); `0o600` best-effort; on Windows
  chmod is a no-op — documented, not faked.
- Path resolution: explicit arg > `$MAR_HOME/registry.json` > `~/.mar/registry.json`.

## omp integration contract

**verified** — credential store is SQLite at `~/.omp/agent/agent.db`,
table `auth_credentials`, live DDL:

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

## Provider catalog

Ten entries, exactly the lines of the problem statement (see
docs/providers.md for the data + verification status). Fields:

`id, display, auth_methods, key_url, limits_url, signup_url, omp_provider_id,
default_model, limits_kind, notes`

- `limits_kind` ∈ {`api`, `manual`} — drives limits.py dispatch.
- `omp_provider_id` is what MAR writes into `agent.db.provider`. Only ids
  observed live are trustworthy; the rest carry `verified: no` in
  docs/providers.md and must be confirmed before M9 promotes them.
- `validate()` returns a list of problem strings; tests assert it is empty for
  the shipped catalog.

## Limits

- `limits_kind == "api"` → real fetcher. Shipped: OpenRouter
  `GET https://openrouter.ai/api/v1/credits` with `Authorization: Bearer <key>`
  (documented public API). Tests mock HTTP (monkeypatched
  `urllib.request.urlopen`); no unit test hits the network.
- `limits_kind == "manual"` → `manual_hint()` returns provider's `limits_url`
  + one-line instruction. Honest UX: MAR says what it cannot fetch and why.
- `LimitSnapshot.source` ∈ {`api`, `manual`} so the CLI can label output.

## Bootstrap prompt

`render_prompt(registry)` produces markdown for pasting into a fresh omp
session. It must:

- list every active subscription with provider id + default model (no keys),
- instruct omp to run `mar omp-sync` (keys reach omp through MAR, not the
  prompt),
- append the contents of `~/.mar/prompt.md` (custom follow-up) when present,
- fail loudly (`BootstrapError`) if the registry is empty — an empty prompt is
  a bug, not a feature.

Tests assert key material never appears in output (a fixture key is injected
into the registry and must not leak).

## Security posture

- Keys at rest: plaintext in `~/.mar/registry.json`, 0600 best-effort. Explicit
  v1 tradeoff; OS keyring is a future milestone (M10+, unplanned).
- CLI output masks keys (`first4…last4`, or `<redacted>` if shorter than 12
  chars). `--json` output masks too.
- This repo must never contain real keys: tests use fake fixtures, `.mar/` is
  gitignored.

## Verification log

- 2026-10-10: inspected live `~/.omp/agent/agent.db` (`auth_credentials`
  schema + rows) and `~/.omp/agent/config.yml` (`modelRoles`) — contracts
  above derive from that inspection, not from docs.
