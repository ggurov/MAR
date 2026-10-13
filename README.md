# MAR — My AI Router

> **TL;DR** — You have a pile of AI subscriptions (Claude, Codex, Cursor, Z.ai,
> OpenRouter, Cline, OpenCode, Gemini, …). Their keys live in different
> dashboards, their limits are scattered, and every fresh
> [omp](https://github.com/canopy/omp) install means an hour of copy-pasting.
> MAR fixes that: `mar add` your keys once, then `mar omp-sync` configures any
> omp install — or `mar export` emits a **paste-ready block** that configures a
> machine that doesn't even have MAR. It never proxies traffic; it only moves
> configuration.

```powershell
PS> mar add openrouter --key sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxx
added openrouter (key: sk-o…yxxx)

PS> mar list
claude         active    oauth    default=-                    key=sk-a…cAAA
cline          active    api_key  default=-                    key=sk_8…d994
codex          active    oauth    default=-                    key=eyJh…e0ew
cursor-grok    active    api_key  default=-                    key=crsr…c158
opencode       active    api_key  default=-                    key=oc_s…mN_L
openrouter     active    api_key  default=-                    key=sk-o…1d7e
zai            active    api_key  default=-                    key=069b…Su1I

PS> mar export        # → paste the block on any machine with omp; done
```

## TL;DR — agentic operation (the easy way)

You never need the commands below. Clone the repo, run omp inside it, and just
ask:

```bash
git clone https://github.com/ggurov/MAR && cd MAR
uv tool install -e .   # optional; agents can also use `uv run mar`
omp
```

Then talk to the agent in free form — it reads [AGENTS.md](AGENTS.md), so it
already knows the TDD rules, the provider catalog, and to never leak your keys:

> *"here's my zai key: 0123abcd44332211.efghijklmn — add it, default model glm-5.3-flash"*

> *"what's active, and how much openrouter credit do I have left?"*

> *"sync everything into my omp install, judge should be jev"*

> *"give me the export block for windows"* … *"and one for linux"*

> *"gemini stopped working — check if the key is still alive"*

The agent runs the right `mar` commands, masks your keys in every output,
tests anything it changes, and hands you the pastable. That's the whole
workflow: **clone → run omp → ask**.

## Examples (fake keys shown)

The commands the agent runs under the hood — for reference, or if you'd rather
drive it yourself:

### Register subscriptions

```powershell
mar add zai --key "0123abcd44332211.efghijklmn" --default glm-5.3-flash
mar add openrouter --key "sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxx"
mar add cursor-grok --key "crsr_xxxxxxxxxxxxxxxxxxxxxxxx"
# OAuth providers (Claude, Codex): log in once with omp, then adopt the tokens:
omp login anthropic        # browser flow, token lands in omp's agent.db
omp login openai-codex
mar pull                   # mar pulls those tokens into its registry
```

### Check what you have

```powershell
PS> mar list --json
[
  {
    "provider": "openrouter",
    "status": "active",
    "auth_method": "api_key",
    "default_model": null,
    "key": "sk-o…1d7e"
  }
]
```

### Usage and limits

```powershell
PS> mar limits openrouter          # real API where one exists
openrouter (api):
  credits: 310.048636187 / 325.0 USD (all-time)

PS> mar limits gemini              # key-liveness via Google's models list
gemini (api):
  key alive: 62 models visible (45 gemini)

PS> mar limits zai                 # no public API → exact pointer instead
https://z.ai
Open this page and check usage manually.
```

(`omp usage` additionally reports quota windows for cline/opencode/zai.)

### Configure an omp install (this machine)

```powershell
PS> mar omp-sync --set-role judge=typesafe/jev-latest
written: cline-pass
written: cursor
written: opencode-go
written: openrouter
written: zai
```

### Configure a machine WITHOUT MAR (the pastable)

```powershell
PS> mar export
# ================================================================ BEGIN ==========================
New-Item -ItemType Directory -Force "$env:USERPROFILE\.omp\agent" | Out-Null
@'
ANTHROPIC_OAUTH_TOKEN=sk-ant-oat01-xxxxxxxx.xxxxxxxxxxxxxxxx
CLINE_API_KEY=sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENAI_CODEX_OAUTH_TOKEN=<jwt-from-mar-pull>
CURSOR_ACCESS_TOKEN=crsr_xxxxxxxxxxxxxxxxxxxx
TYPESAFE_API_KEY=apikey_xxxxxxxxxxxxxxxxxxxx
OPENCODE_API_KEY=oc_sk_xxxxxxxxxxxxxxxxxxx
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxx
ZAI_API_KEY=0123abcd44332211.efghijklmn
'@ | Set-Content "$env:USERPROFILE\.omp\agent\.env" -Encoding ascii
$cfg = "$env:USERPROFILE\.omp\agent\config.yml"
$roles = @'
modelRoles:
  judge: typesafe/jev-latest
  default: zai/glm-5.3-flash:max
'@
if (-not (Test-Path $cfg)) { Set-Content $cfg $roles -Encoding ascii }
if ((Test-Path $cfg) -and -not (Select-String -Path $cfg -Pattern '(?m)^modelRoles:' -Quiet)) { if (-not (Get-Content $cfg -Raw).EndsWith("`n")) { Add-Content $cfg '' }; Add-Content $cfg $roles }
# ================================================================ END ============================
```

Paste that into PowerShell on any machine with omp: credentials + judge/default
roles, done. `--shell bash` emits the bash variant — it also adds `~/.local/bin`
to `PATH` (live + `.bashrc`), since omp's `curl | sh` installs there and minimal
Linux setups leave it off `PATH`. Both variants end with `omp config set` lines:
hashline edit mode with deepseek pinned to it, and your machine's `statusLine`
(preset + segments) so hostname stays in the prompt display. The block also sets
`PI_STRICT_EDIT_MODE=1` in the agent `.env` — omp's silent per-family fallback
to `replace` (GLM Flash, DeepSeek, Kimi, …) is disabled, so a model that can't
cope fails visibly instead of quietly downgrading. OAuth tokens in the
block expire (Anthropic ≈ 8h, Codex ≈ 10d) — re-run `mar pull && mar export` to
refresh; export warns when a token is already dead or dying. Skip notes
(providers without env routes, e.g. antigravity → `/login google-antigravity`)
print to stderr, never into the block.

### Start a session right

```powershell
PS> mar prompt    # the startup prompt for a fresh omp session
```

## Commands

| Command | What it does |
| --- | --- |
| `mar add <provider> --key KEY` | Register a subscription (`--key-stdin` avoids shell history; `--default` picks a model) |
| `mar list [--json]` | Active subscriptions, masked keys, default models |
| `mar limits [provider]` | Real API checks (OpenRouter credits, Gemini key-liveness); exact pointers elsewhere |
| `mar omp-sync [--set-role role=model]` | Write stored keys into omp (agent.db / `.env`), set `modelRoles` |
| `mar pull` | Adopt OAuth logins (Claude, Codex) from local omp into the registry |
| `mar export [--shell bash\|fish\|powershell] [--output F]` | The zero-install pastable — commands only, BEGIN/END framed, notes on stderr |
| `mar serve [--port N] [--host ADDR]` | Serve the export payloads over LAN HTTP; boxes apply with `curl -fsSL http://<this-box>:8930/config.sh \| sh` (or `/config.fish \| fish`, `/config.ps1` PowerShell) |
| `mar prompt` | Startup prompt for a fresh omp session |
| `mar rotate <provider> --key-stdin` | Replace one provider's key in place — models, default model, and notes are preserved |
| `mar config [show\|backend]` | Show or switch the storage backend (file by default; optional Vault) |

## Where keys live (and don't)

Keys live **only** in your user config space: `~/.mar/registry.json` (chmod 600
best-effort; `~/.mar` is created 0700, and a loose-mode file self-tightens on
read) and omp's own `~/.omp/agent/` store — or, if you opt in, your
HashiCorp Vault. The repo contains code and fake-key examples only — enforced
by a hygiene test that fails the suite if any real-looking credential pattern
appears in any tracked file.

## Optional: store the registry in Vault (off by default)

If you run [HashiCorp Vault](https://www.hashicorp.com/products/vault)
(KV-v2), MAR can keep the registry there instead of the JSON file — useful
when several machines share one truth or when rotation should leave an audit
and rollback trail:

```bash
mar config backend vault --addr http://127.0.0.1:8200 \
     --token-file ~/.vault/mar-token --import   # --import copies the file registry over
mar list                                        # now reads from Vault
mar rotate zai --key-stdin                      # one-secret PUT; old version stays for rollback
mar config backend file                         # switch back anytime
```

Layout: one KV-v2 secret per provider under `secret/mar/keys/<provider>`
(fields: the key plus the subscription's models/default/status and
`updated_at`/`rotated_by` metadata), plus a `_registry` schema marker.
Rotating one key rewrites exactly that secret — KV-v2 version history gives
per-provider rollback. Consumers can read a single credential with
``vault kv get -field=key secret/mar/keys/zai``.

Nothing about Vault is required: no config and no `MAR_BACKEND` means the
JSON file, byte-for-byte as always. The token is read from a token file or
`VAULT_TOKEN` at use time — never stored in the config, never printed.
`--registry PATH` always forces the file backend (tests, migration,
inspection). Env-driven setup without a config file:
`MAR_BACKEND=vault VAULT_ADDR=... VAULT_TOKEN=...`.

## Providers (96)

cursor-grok, codex, claude, cline, opencode, zai, openrouter, antigravity,
copilot, jev (Typesafe judge — role-only), gemini, commandcode — plus the six
mesh-lane providers (deepseek, groq, cerebras, mistral, orcarouter,
cloudflare) and every provider in omp's roster (94 auth policies,
2026-10-13). Every omp id/env var is confirmed against omp's official
provider roster; orcarouter, cloudflare (Workers AI), and llama-cpp ship
verified=False. Catalog: `src/mar/providers.py` · data + verification
status: [docs/providers.md](docs/providers.md).

## Quickstart (dev)

```bash
uv tool install -e .   # installs `mar` on PATH (editable — repo changes apply live)
uv sync                # venv + pytest + ruff
uv run pytest          # TDD harness
```

Humans and agents: read [AGENTS.md](AGENTS.md) before your first edit — TDD is
mandatory (failing test first, green-only commits), and changes ship via
feature branch → PR → squash merge.

## Verification (2026-10-10, live)

- omp contracts inspected on a real install: `agent.db:auth_credentials`
  (api_key + oauth shapes), `config.yml:modelRoles`, `PI_CODING_AGENT_DIR`,
  `.env` loading, provider roster.
- Isolated agent dir: `omp-sync` from empty → `omp usage` live quota windows;
  one-shot inference via zai, anthropic (OAuth env), openai-codex (OAuth env),
  openrouter, gemini — all green.
- Pastable-only proof on a bare machine: paste → live usage → inference;
  double-run idempotent.

Full contracts: [docs/architecture.md](docs/architecture.md) ·
milestones: [PLAN.md](PLAN.md) ·
provider data: [docs/providers.md](docs/providers.md) ·
the original problem statement: [problem_statement.txt](problem_statement.txt).

## Status

Done: registry, catalog (96 providers: 12 originals + 6 mesh lanes + omp's
full 94-id roster swept 2026-10-13; 3 unverified), omp adapter,
limits (OpenRouter credits + Gemini key-check), bootstrap prompt,
omp-sync, OAuth pull, export pastable with role-setting, global install.
Planned: per-provider OAuth add-flows (M7), deeper limit fetchers (M8),
`mar doctor` (M9).
