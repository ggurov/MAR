"""Static catalog of the user's 96 AI providers + integrity validation.

Editorial source: docs/providers.md (kept in sync with it). The catalog is the
single place MAR learns provider facts from; it never invents defaults or
keys. Entries flagged ``verified=True`` were confirmed on this machine: the
12 originals on 2026-10-10 (live ``~/.omp/agent/agent.db`` rows for
cline/opencode/zai, OpenRouter's documented credits API), and the 2026-10-13
roster sweep - every omp provider id + env var cross-checked against omp's
provider roster (``omp://providers.md`` and the catalog's compiled KDL auth
policies in the oh-my-pi source on this box). Everything else ships
best-known values and must be confirmed before MAR writes it anywhere
(``mar doctor``, M9, is the promotion path).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["CATALOG", "Provider", "get", "ids", "unverified", "validate"]

_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
_ID_RE = re.compile(_ID_PATTERN)
_ENV_PATTERN = r"^[A-Z][A-Z0-9_]*$"
_ENV_RE = re.compile(_ENV_PATTERN)
_LIMITS_KINDS = frozenset({"api", "manual"})
_URL_FIELDS = ("key_url", "limits_url", "signup_url")


@dataclass(frozen=True)
class Provider:
    """One cataloged AI provider and the facts MAR knows about it.

    Attributes:
        id: Catalog identifier, lowercase kebab (``^[a-z0-9][a-z0-9-]*$``);
            the key used by registry entries to reference a provider.
        display: Human-readable name for CLI output.
        auth_methods: Supported auth kinds; v1 ships ``("api_key",)`` only.
        key_url: Where the user creates/manages an API key for this provider.
        limits_url: Where usage/limits are visible (web) or the documented
            API base for ``limits_kind == "api"`` providers.
        signup_url: Where a subscription is purchased/started.
        omp_provider_id: Value MAR writes into omp's ``agent.db``
            ``auth_credentials.provider``. Trustworthy only when
            ``verified`` is True.
        omp_env_var: When nonempty, omp accepts this provider's credential
            via this environment variable (written to the agent dir's
            ``.env`` by ``mar omp-sync``) instead of a login-sourced
            agent.db row. Empty means agent.db credential row.
        export_env_var: Environment variable omp recognizes for this
            provider on ANY machine, used by ``mar export`` to build the
            zero-install pastable. May differ from ``omp_env_var`` (the
            sync route) — e.g. providers MAR syncs as agent.db rows still
            have documented env aliases. Empty means the provider has no
            env route (OAuth-only); export tells the user to ``/login``.
        export_oauth_env_var: Environment variable for this provider's
            OAuth access token on any machine (``mar pull`` + ``mar
            export``). Empty means no documented OAuth env route.
        default_model: Always None — the user picks the default at
            ``mar add`` time; the catalog never invents one.
        limits_kind: ``"api"`` (real fetcher exists) or ``"manual"``
            (point the user at ``limits_url``); drives limits.py dispatch.
        verified: True only for facts confirmed live on this machine or via
            documented public API; False means best-known, unconfirmed.
        notes: Short human note; unconfirmed omp ids say "omp id unverified".
    """

    id: str
    display: str
    auth_methods: tuple[str, ...]
    key_url: str
    limits_url: str
    signup_url: str
    omp_provider_id: str
    omp_env_var: str
    export_env_var: str
    export_oauth_env_var: str
    default_model: str | None
    limits_kind: str
    verified: bool
    notes: str


CATALOG: tuple[Provider, ...] = (
    Provider(
        id="cursor-grok",
        display="Cursor / Grok",
        auth_methods=("api_key",),
        key_url="https://cursor.com/settings",
        limits_url="https://cursor.com/usage",
        signup_url="https://cursor.com/signup",
        omp_provider_id="cursor",
        omp_env_var="CURSOR_ACCESS_TOKEN",
        export_env_var="CURSOR_ACCESS_TOKEN",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env fallback confirmed in omp's provider roster "
            "(2026-10-10). Cursor is OAuth-first in omp; MAR writes the "
            "crsr_ token to agent .env. One subscription line covering "
            "Cursor's Grok models; split only if the user says so."),
    ),
    Provider(
        id="codex",
        display="OpenAI Codex",
        auth_methods=("api_key",),
        key_url="https://platform.openai.com/api-keys",
        limits_url="https://platform.openai.com/usage",
        signup_url="https://platform.openai.com/signup",
        omp_provider_id="openai-codex",
        omp_env_var="OPENAI_CODEX_OAUTH_TOKEN",
        export_env_var="",
        export_oauth_env_var="OPENAI_CODEX_OAUTH_TOKEN",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="omp id + env confirmed in omp's provider roster (2026-10-10).",
    ),
    Provider(
        id="claude",
        display="Anthropic Claude",
        auth_methods=("api_key",),
        key_url="https://console.anthropic.com/settings/keys",
        limits_url="https://console.anthropic.com/settings/usage",
        signup_url="https://console.anthropic.com/",
        omp_provider_id="anthropic",
        omp_env_var="ANTHROPIC_API_KEY",
        export_env_var="ANTHROPIC_API_KEY",
        export_oauth_env_var="ANTHROPIC_OAUTH_TOKEN",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="omp id + env confirmed in omp's provider roster (2026-10-10).",
    ),
    Provider(
        id="cline",
        display="Cline",
        auth_methods=("api_key",),
        key_url="https://cline.bot",
        limits_url="https://cline.bot",
        signup_url="https://cline.bot",
        omp_provider_id="cline-pass",
        omp_env_var="",
        export_env_var="CLINE_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp provider id observed live in ~/.omp/agent/agent.db "
            "(2026-10-10); key/limits pages not yet confirmed."),
    ),
    Provider(
        id="opencode",
        display="OpenCode",
        auth_methods=("api_key",),
        key_url="https://opencode.ai",
        limits_url="https://opencode.ai",
        signup_url="https://opencode.ai",
        omp_provider_id="opencode-go",
        omp_env_var="",
        export_env_var="OPENCODE_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp provider id observed live in ~/.omp/agent/agent.db "
            "(2026-10-10); key/limits pages not yet confirmed."),
    ),
    Provider(
        id="zai",
        display="Z.ai",
        auth_methods=("api_key",),
        key_url="https://z.ai/manage-apikey/apikey-list",
        limits_url="https://z.ai",
        signup_url="https://z.ai",
        omp_provider_id="zai",
        omp_env_var="",
        export_env_var="ZAI_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp provider id observed live in ~/.omp/agent/agent.db "
            "(2026-10-10); key/limits pages not yet confirmed."),
    ),
    Provider(
        id="openrouter",
        display="OpenRouter",
        auth_methods=("api_key",),
        key_url="https://openrouter.ai/settings/keys",
        limits_url="https://openrouter.ai/credits",
        signup_url="https://openrouter.ai/",
        omp_provider_id="openrouter",
        omp_env_var="",
        export_env_var="OPENROUTER_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="api",
        verified=True,
        notes=(
            "limits_kind 'api': GET /api/v1/credits is a publicly documented "
            "endpoint. omp id confirmed in omp's provider roster "
            "(2026-10-10); MAR writes a login-style agent.db row (same tier "
            "as /login, which omp validates against the provider)."),
    ),
    Provider(
        id="antigravity",
        display="Google Antigravity",
        auth_methods=("api_key",),
        key_url="https://antigravity.google",
        limits_url="https://antigravity.google",
        signup_url="https://antigravity.google",
        omp_provider_id="google-antigravity",
        omp_env_var="",
        export_env_var="",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="omp id confirmed in omp's provider roster (2026-10-10); OAuth-backed provider.",
    ),
    Provider(
        id="copilot",
        display="GitHub Copilot",
        auth_methods=("api_key",),
        key_url="https://github.com/settings/tokens",
        limits_url="https://github.com/settings/copilot",
        signup_url="https://github.com/features/copilot",
        omp_provider_id="github-copilot",
        omp_env_var="COPILOT_GITHUB_TOKEN",
        export_env_var="COPILOT_GITHUB_TOKEN",
        export_oauth_env_var="COPILOT_GITHUB_TOKEN",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="omp id + env confirmed in omp's provider roster (2026-10-10).",
    ),
    Provider(
        id="jev",
        display="Jev (Typesafe)",
        auth_methods=("api_key",),
        key_url="https://typesafe.ai",
        limits_url="https://typesafe.ai",
        signup_url="https://typesafe.ai",
        omp_provider_id="typesafe",
        omp_env_var="TYPESAFE_API_KEY",
        export_env_var="TYPESAFE_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "Judge-kind models (jev-latest, jev-preview) wired to omp's "
            "modelRoles.judge; NOT chat-selectable. Env confirmed in omp's "
            "provider roster and live in the user's environment."),
    ),
    Provider(
        id="gemini",
        display="Google Gemini",
        auth_methods=("api_key",),
        key_url="https://aistudio.google.com/api/keys",
        limits_url="https://aistudio.google.com/usage",
        signup_url="https://aistudio.google.com/",
        omp_provider_id="google",
        omp_env_var="GEMINI_API_KEY",
        export_env_var="GEMINI_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="api",
        verified=True,
        notes=(
            "limits_kind 'api': MAR checks key liveness via the documented "
            "models list (Google publishes no usage API for AI Studio keys; "
            "usage is visible only in the AI Studio UI). omp id + env "
            "confirmed in omp's provider roster (2026-10-10)."),
    ),
    Provider(
        id="commandcode",
        display="Command Code",
        auth_methods=("api_key",),
        key_url="https://commandcode.ai",
        limits_url="https://commandcode.ai",
        signup_url="https://commandcode.ai",
        omp_provider_id="commandcode",
        omp_env_var="COMMAND_CODE_API_KEY",
        export_env_var="COMMAND_CODE_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster and model cache "
            "(2026-10-10; 88 models: Claude/DeepSeek/GPT lines). omp usage "
            "reports credit balance + 5h/weekly windows; keys validated "
            "against /alpha/whoami."),
    ),
    Provider(
        id="deepseek",
        display="DeepSeek",
        auth_methods=("api_key",),
        key_url="https://platform.deepseek.com/api_keys",
        limits_url="https://platform.deepseek.com/usage",
        signup_url="https://platform.deepseek.com",
        omp_provider_id="deepseek",
        omp_env_var="DEEPSEEK_API_KEY",
        export_env_var="DEEPSEEK_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13); provider also "
            "staged live in ~/.omp/agent/models.yml. OpenAI-compatible base "
            "https://api.deepseek.com/v1 (1M ctx per verified specs noted there "
            "2026-10-10). Usage is dashboard-web only - limits manual."
        ),
    ),
    Provider(
        id="groq",
        display="Groq",
        auth_methods=("api_key",),
        key_url="https://console.groq.com/keys",
        limits_url="https://console.groq.com/usage",
        signup_url="https://console.groq.com",
        omp_provider_id="groq",
        omp_env_var="GROQ_API_KEY",
        export_env_var="GROQ_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13). "
            "OpenAI-compatible API; usage/limits are console-web only - no documented usage "
            "endpoint, so limits_kind is manual."
        ),
    ),
    Provider(
        id="cerebras",
        display="Cerebras",
        auth_methods=("api_key",),
        key_url="https://cloud.cerebras.ai",
        limits_url="https://cloud.cerebras.ai",
        signup_url="https://cloud.cerebras.ai",
        omp_provider_id="cerebras",
        omp_env_var="CEREBRAS_API_KEY",
        export_env_var="CEREBRAS_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13). Keys and usage "
            "both live in the Cerebras cloud console (omp's login auth-url is the "
            "/platform/ page); limits manual."
        ),
    ),
    Provider(
        id="mistral",
        display="Mistral",
        auth_methods=("api_key",),
        key_url="https://console.mistral.ai/api-keys",
        limits_url="https://console.mistral.ai",
        signup_url="https://console.mistral.ai",
        omp_provider_id="mistral",
        omp_env_var="MISTRAL_API_KEY",
        export_env_var="MISTRAL_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13). The console is "
            "the key + billing surface; limits manual."
        ),
    ),
    Provider(
        id="orcarouter",
        display="Orcarouter",
        auth_methods=("api_key",),
        key_url="https://orcarouter.ai/dashboard",
        limits_url="https://orcarouter.ai/dashboard",
        signup_url="https://orcarouter.ai",
        omp_provider_id="orcarouter",
        omp_env_var="ORCAROUTER_API_KEY",
        export_env_var="ORCAROUTER_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=False,
        notes=(
            "Mesh router in the user's stack (per ~/tools/key-drop docs): API base "
            "https://api.orcarouter.ai/v1, keys managed at the dashboard. Not in omp's "
            "roster - omp id unverified."
        ),
    ),
    Provider(
        id="cloudflare",
        display="Cloudflare Workers AI",
        auth_methods=("api_key",),
        key_url="https://dash.cloudflare.com/profile/api-tokens",
        limits_url="https://dash.cloudflare.com",
        signup_url="https://dash.cloudflare.com",
        omp_provider_id="cloudflare",
        omp_env_var="CLOUDFLARE_API_TOKEN",
        export_env_var="CLOUDFLARE_API_TOKEN",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=False,
        notes=(
            "Workers AI token (dashboard template 'Workers AI'; the key file carries "
            "account_id:token). omp roster has cloudflare-ai-gateway only - a different "
            "product; Workers AI omp id unverified."
        ),
    ),
    Provider(
        id="abliteration",
        display="Abliteration",
        auth_methods=("api_key",),
        key_url="https://abliteration.ai/console",
        limits_url="https://abliteration.ai",
        signup_url="https://abliteration.ai",
        omp_provider_id="abliteration",
        omp_env_var="ABLITERATION_API_KEY",
        export_env_var="ABLITERATION_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url; ABLIT_KEY alias.",
    ),
    Provider(
        id="aiand",
        display="ai&",
        auth_methods=("api_key",),
        key_url="https://console.aiand.com/api-keys",
        limits_url="https://console.aiand.com",
        signup_url="https://console.aiand.com",
        omp_provider_id="aiand",
        omp_env_var="AIAND_API_KEY",
        export_env_var="AIAND_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="aimlapi",
        display="AIML API",
        auth_methods=("api_key",),
        key_url="https://aimlapi.com",
        limits_url="https://aimlapi.com",
        signup_url="https://aimlapi.com",
        omp_provider_id="aimlapi",
        omp_env_var="AIMLAPI_API_KEY",
        export_env_var="AIMLAPI_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="no documented key page in the roster - URLs best-known.",
    ),
    Provider(
        id="alibaba-coding-plan",
        display="Alibaba Coding Plan",
        auth_methods=("api_key",),
        key_url="https://www.alibabacloud.com",
        limits_url="https://www.alibabacloud.com",
        signup_url="https://www.alibabacloud.com",
        omp_provider_id="alibaba-coding-plan",
        omp_env_var="ALIBABA_CODING_PLAN_API_KEY",
        export_env_var="ALIBABA_CODING_PLAN_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "no documented key page in the roster - URLs best-known; subscription login "
            "flow."
        ),
    ),
    Provider(
        id="alibaba-token-plan",
        display="QwenCloud Token Plan",
        auth_methods=("api_key",),
        key_url="https://www.alibabacloud.com",
        limits_url="https://www.alibabacloud.com",
        signup_url="https://www.alibabacloud.com",
        omp_provider_id="alibaba-token-plan",
        omp_env_var="ALIBABA_TOKEN_PLAN_API_KEY",
        export_env_var="ALIBABA_TOKEN_PLAN_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "no documented key page in the roster - URLs best-known; "
            "BAILIAN_TOKEN_PLAN_API_KEY alias."
        ),
    ),
    Provider(
        id="amazon-bedrock",
        display="Amazon Bedrock",
        auth_methods=("api_key",),
        key_url="https://console.aws.amazon.com/iam",
        limits_url="https://console.aws.amazon.com/bedrock",
        signup_url="https://aws.amazon.com",
        omp_provider_id="amazon-bedrock",
        omp_env_var="AWS_BEARER_TOKEN_BEDROCK",
        export_env_var="AWS_BEARER_TOKEN_BEDROCK",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "no documented key page in the roster - URLs best-known; bearer-token profile "
            "or the SigV4 AWS chain."
        ),
    ),
    Provider(
        id="apple",
        display="Apple Foundation Models",
        auth_methods=("api_key",),
        key_url="https://developer.apple.com",
        limits_url="https://developer.apple.com",
        signup_url="https://developer.apple.com",
        omp_provider_id="apple",
        omp_env_var="",
        export_env_var="",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id confirmed in omp's provider roster (2026-10-13). Keyless on-device "
            "engine on supported Apple Silicon; no key page exists."
        ),
    ),
    Provider(
        id="azure",
        display="Azure OpenAI",
        auth_methods=("api_key",),
        key_url="https://portal.azure.com",
        limits_url="https://portal.azure.com",
        signup_url="https://azure.microsoft.com",
        omp_provider_id="azure",
        omp_env_var="AZURE_OPENAI_API_KEY",
        export_env_var="AZURE_OPENAI_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="no documented key page in the roster - URLs best-known.",
    ),
    Provider(
        id="baseten",
        display="Baseten",
        auth_methods=("api_key",),
        key_url="https://app.baseten.co/settings/api_keys",
        limits_url="https://app.baseten.co",
        signup_url="https://app.baseten.co",
        omp_provider_id="baseten",
        omp_env_var="BASETEN_API_KEY",
        export_env_var="BASETEN_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="bedrock-mantle",
        display="Amazon Bedrock Mantle",
        auth_methods=("api_key",),
        key_url="https://aws.amazon.com/bedrock",
        limits_url="https://aws.amazon.com/bedrock",
        signup_url="https://aws.amazon.com",
        omp_provider_id="bedrock-mantle",
        omp_env_var="AWS_BEARER_TOKEN_BEDROCK",
        export_env_var="AWS_BEARER_TOKEN_BEDROCK",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13). omp's AWS "
            "bearer-token profile over Bedrock; URLs best-known."
        ),
    ),
    Provider(
        id="charm-hyper",
        display="Charm Hyper",
        auth_methods=("api_key",),
        key_url="https://hyper.charm.land/",
        limits_url="https://hyper.charm.land",
        signup_url="https://hyper.charm.land",
        omp_provider_id="charm-hyper",
        omp_env_var="CHARM_HYPER_API_KEY",
        export_env_var="CHARM_HYPER_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url; HYPER_API_KEY alias.",
    ),
    Provider(
        id="cloudflare-ai-gateway",
        display="Cloudflare AI Gateway",
        auth_methods=("api_key",),
        key_url="https://dash.cloudflare.com",
        limits_url="https://dash.cloudflare.com",
        signup_url="https://dash.cloudflare.com",
        omp_provider_id="cloudflare-ai-gateway",
        omp_env_var="CLOUDFLARE_AI_GATEWAY_API_KEY",
        export_env_var="CLOUDFLARE_AI_GATEWAY_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "no documented key page in the roster - URLs best-known; omp login also stores "
            "CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_GATEWAY_ID; omp routes "
            "Anthropic/OpenAI/Workers AI gateway models."
        ),
    ),
    Provider(
        id="coreweave",
        display="CoreWeave Serverless Inference",
        auth_methods=("api_key",),
        key_url="https://wandb.ai/settings",
        limits_url="https://wandb.ai",
        signup_url="https://www.coreweave.com",
        omp_provider_id="coreweave",
        omp_env_var="COREWEAVE_API_KEY",
        export_env_var="COREWEAVE_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "key_url is omp's documented login auth-url; WANDB_API_KEY alias; omp sends "
            "COREWEAVE_PROJECT as OpenAI-Project header."
        ),
    ),
    Provider(
        id="deepinfra",
        display="DeepInfra",
        auth_methods=("api_key",),
        key_url="https://deepinfra.com/dash/api_keys",
        limits_url="https://deepinfra.com/dash",
        signup_url="https://deepinfra.com",
        omp_provider_id="deepinfra",
        omp_env_var="DEEPINFRA_API_KEY",
        export_env_var="DEEPINFRA_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="devin",
        display="Devin",
        auth_methods=("api_key",),
        key_url="https://app.devin.ai",
        limits_url="https://app.devin.ai",
        signup_url="https://devin.ai",
        omp_provider_id="devin",
        omp_env_var="DEVIN_API_KEY",
        export_env_var="DEVIN_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13); OAuth-first "
            "(auth.app.devin.ai per roster); URLs best-known."
        ),
    ),
    Provider(
        id="exa",
        display="Exa",
        auth_methods=("api_key",),
        key_url="https://dashboard.exa.ai/api-keys",
        limits_url="https://dashboard.exa.ai",
        signup_url="https://exa.ai",
        omp_provider_id="exa",
        omp_env_var="EXA_API_KEY",
        export_env_var="EXA_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url; web-search API.",
    ),
    Provider(
        id="factory-droid",
        display="Factory Droid",
        auth_methods=("api_key",),
        key_url="https://factory.ai",
        limits_url="https://factory.ai",
        signup_url="https://factory.ai",
        omp_provider_id="factory-droid",
        omp_env_var="",
        export_env_var="",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id confirmed in omp's provider roster (2026-10-13). Device-code OAuth via "
            "auth.factory.ai; roster says no env key route (FACTORY_API_KEY is a different "
            "product)."
        ),
    ),
    Provider(
        id="firepass",
        display="Fire Pass (Fireworks subscription)",
        auth_methods=("api_key",),
        key_url="https://app.fireworks.ai/settings/users/api-keys",
        limits_url="https://app.fireworks.ai",
        signup_url="https://app.fireworks.ai",
        omp_provider_id="firepass",
        omp_env_var="FIREPASS_API_KEY",
        export_env_var="FIREPASS_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "key_url is omp's documented login auth-url; subscription lane on the Fireworks "
            "key console."
        ),
    ),
    Provider(
        id="fireworks",
        display="Fireworks",
        auth_methods=("api_key",),
        key_url="https://app.fireworks.ai/settings/users/api-keys",
        limits_url="https://app.fireworks.ai",
        signup_url="https://app.fireworks.ai",
        omp_provider_id="fireworks",
        omp_env_var="FIREWORKS_API_KEY",
        export_env_var="FIREWORKS_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="gitlab-duo",
        display="GitLab Duo Non-Agentic",
        auth_methods=("api_key",),
        key_url="https://gitlab.com/-/user_settings/personal_access_tokens",
        limits_url="https://gitlab.com",
        signup_url="https://gitlab.com",
        omp_provider_id="gitlab-duo",
        omp_env_var="GITLAB_TOKEN",
        export_env_var="GITLAB_TOKEN",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13); OAuth-first; PAT "
            "URL best-known."
        ),
    ),
    Provider(
        id="gitlab-duo-agent",
        display="GitLab Duo Agent",
        auth_methods=("api_key",),
        key_url="https://gitlab.com/-/user_settings/personal_access_tokens",
        limits_url="https://gitlab.com",
        signup_url="https://gitlab.com",
        omp_provider_id="gitlab-duo-agent",
        omp_env_var="GITLAB_TOKEN",
        export_env_var="GITLAB_TOKEN",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13); OAuth-first; PAT "
            "URL best-known."
        ),
    ),
    Provider(
        id="gmi-cloud",
        display="GMI Cloud",
        auth_methods=("api_key",),
        key_url="https://console.gmicloud.ai",
        limits_url="https://console.gmicloud.ai",
        signup_url="https://console.gmicloud.ai",
        omp_provider_id="gmi-cloud",
        omp_env_var="GMI_API_KEY",
        export_env_var="GMI_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="google-gemini-cli",
        display="Google Cloud Code Assist (Gemini CLI)",
        auth_methods=("api_key",),
        key_url="https://developers.google.com/gemini-code-assist",
        limits_url="https://developers.google.com/gemini-code-assist",
        signup_url="https://developers.google.com/gemini-code-assist",
        omp_provider_id="google-gemini-cli",
        omp_env_var="",
        export_env_var="",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id confirmed in omp's provider roster (2026-10-13). Google OAuth (free "
            "tier); no env key route."
        ),
    ),
    Provider(
        id="google-vertex",
        display="Google Vertex AI",
        auth_methods=("api_key",),
        key_url="https://console.cloud.google.com/apis/credentials",
        limits_url="https://console.cloud.google.com/vertex-ai",
        signup_url="https://cloud.google.com/vertex-ai",
        omp_provider_id="google-vertex",
        omp_env_var="GOOGLE_CLOUD_API_KEY",
        export_env_var="GOOGLE_CLOUD_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13); ADC "
            "service-account chain is the primary route; URLs best-known."
        ),
    ),
    Provider(
        id="helmcode",
        display="Helmcode",
        auth_methods=("api_key",),
        key_url="https://cloud.helmcode.com",
        limits_url="https://cloud.helmcode.com",
        signup_url="https://cloud.helmcode.com",
        omp_provider_id="helmcode",
        omp_env_var="HELMCODE_API_KEY",
        export_env_var="HELMCODE_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="huggingface",
        display="Hugging Face Inference",
        auth_methods=("api_key",),
        key_url="https://huggingface.co/settings/tokens",
        limits_url="https://huggingface.co/settings",
        signup_url="https://huggingface.co",
        omp_provider_id="huggingface",
        omp_env_var="HUGGINGFACE_HUB_TOKEN",
        export_env_var="HUGGINGFACE_HUB_TOKEN",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "key_url is omp's documented login auth-url (fine-grained token page); HF_TOKEN "
            "alias."
        ),
    ),
    Provider(
        id="kagi",
        display="Kagi",
        auth_methods=("api_key",),
        key_url="https://kagi.com/settings/api",
        limits_url="https://kagi.com/settings",
        signup_url="https://kagi.com",
        omp_provider_id="kagi",
        omp_env_var="KAGI_API_KEY",
        export_env_var="KAGI_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url; search API (credits shown there).",
    ),
    Provider(
        id="kilo",
        display="Kilo Gateway",
        auth_methods=("api_key",),
        key_url="https://kilo.dev",
        limits_url="https://kilo.dev",
        signup_url="https://kilo.dev",
        omp_provider_id="kilo",
        omp_env_var="KILO_API_KEY",
        export_env_var="KILO_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="no documented key page in the roster - URLs best-known; custom omp login flow.",
    ),
    Provider(
        id="kimi-code",
        display="Kimi Code",
        auth_methods=("api_key",),
        key_url="https://www.kimi.com",
        limits_url="https://www.kimi.com",
        signup_url="https://www.kimi.com",
        omp_provider_id="kimi-code",
        omp_env_var="KIMI_API_KEY",
        export_env_var="KIMI_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="device-code OAuth via auth.kimi.com per roster; URLs best-known.",
    ),
    Provider(
        id="litellm",
        display="LiteLLM",
        auth_methods=("api_key",),
        key_url="https://docs.litellm.ai/docs/proxy/deploy",
        limits_url="https://docs.litellm.ai",
        signup_url="https://docs.litellm.ai",
        omp_provider_id="litellm",
        omp_env_var="LITELLM_API_KEY",
        export_env_var="LITELLM_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "key_url is omp's documented login auth-url; self-hosted proxy - virtual "
            "keys/usage live in its admin UI; LITELLM_BASE_URL selects the endpoint."
        ),
    ),
    Provider(
        id="llama-cpp",
        display="llama.cpp (local)",
        auth_methods=("api_key",),
        key_url="https://github.com/ggmlorg/llama.cpp",
        limits_url="https://github.com/ggmlorg/llama.cpp",
        signup_url="https://github.com/ggmlorg/llama.cpp",
        omp_provider_id="",
        omp_env_var="LLAMA_CPP_API_KEY",
        export_env_var="LLAMA_CPP_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=False,
        notes=(
            "omp roster id is 'llama.cpp' (with a dot) - MAR's id grammar cannot express "
            "it, so no omp_provider_id; omp id unverified. Local engine, keyless unless the "
            "server requires auth."
        ),
    ),
    Provider(
        id="lm-studio",
        display="LM Studio (local)",
        auth_methods=("api_key",),
        key_url="https://lmstudio.ai",
        limits_url="https://lmstudio.ai",
        signup_url="https://lmstudio.ai",
        omp_provider_id="lm-studio",
        omp_env_var="LM_STUDIO_API_KEY",
        export_env_var="LM_STUDIO_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13). Local engine, "
            "keyless by default; env is optional server auth."
        ),
    ),
    Provider(
        id="local",
        display="Local models",
        auth_methods=("api_key",),
        key_url="https://ohmypi.xyz",
        limits_url="https://ohmypi.xyz",
        signup_url="https://ohmypi.xyz",
        omp_provider_id="local",
        omp_env_var="",
        export_env_var="",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id confirmed in omp's provider roster (2026-10-13). omp's keyless umbrella "
            "for implicit local engines; not a vendor."
        ),
    ),
    Provider(
        id="meta",
        display="Meta Model API",
        auth_methods=("api_key",),
        key_url="https://developer.meta.com/ai/",
        limits_url="https://developer.meta.com/ai/",
        signup_url="https://developer.meta.com/ai/",
        omp_provider_id="meta",
        omp_env_var="MODEL_API_KEY",
        export_env_var="MODEL_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url; META_API_KEY alias.",
    ),
    Provider(
        id="minimax",
        display="MiniMax",
        auth_methods=("api_key",),
        key_url="https://platform.minimax.io",
        limits_url="https://platform.minimax.io",
        signup_url="https://platform.minimax.io",
        omp_provider_id="minimax",
        omp_env_var="MINIMAX_API_KEY",
        export_env_var="MINIMAX_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="no documented key page in the roster - URLs best-known.",
    ),
    Provider(
        id="minimax-code",
        display="MiniMax Token Plan (International)",
        auth_methods=("api_key",),
        key_url="https://platform.minimax.io/subscribe/token-plan",
        limits_url="https://platform.minimax.io",
        signup_url="https://platform.minimax.io",
        omp_provider_id="minimax-code",
        omp_env_var="MINIMAX_CODE_API_KEY",
        export_env_var="MINIMAX_CODE_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="minimax-code-cn",
        display="MiniMax Token Plan (China)",
        auth_methods=("api_key",),
        key_url="https://platform.minimaxi.com/subscribe/token-plan",
        limits_url="https://platform.minimaxi.com",
        signup_url="https://platform.minimaxi.com",
        omp_provider_id="minimax-code-cn",
        omp_env_var="MINIMAX_CODE_CN_API_KEY",
        export_env_var="MINIMAX_CODE_CN_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="moonshot",
        display="Moonshot (Kimi API)",
        auth_methods=("api_key",),
        key_url="https://platform.moonshot.ai/console/api-keys",
        limits_url="https://platform.moonshot.ai/console",
        signup_url="https://platform.moonshot.ai",
        omp_provider_id="moonshot",
        omp_env_var="MOONSHOT_API_KEY",
        export_env_var="MOONSHOT_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url; KIMI_API_KEY alias.",
    ),
    Provider(
        id="muse-code",
        display="Muse Code (Subscription)",
        auth_methods=("api_key",),
        key_url="https://www.meta.com",
        limits_url="https://www.meta.com",
        signup_url="https://www.meta.com",
        omp_provider_id="muse-code",
        omp_env_var="",
        export_env_var="",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id confirmed in omp's provider roster (2026-10-13). Device-code login via "
            "auth.meta.com (Meta OIDC) per roster; no public key console known - URLs "
            "best-known."
        ),
    ),
    Provider(
        id="nanogpt",
        display="NanoGPT",
        auth_methods=("api_key",),
        key_url="https://nano-gpt.com/api",
        limits_url="https://nano-gpt.com",
        signup_url="https://nano-gpt.com",
        omp_provider_id="nanogpt",
        omp_env_var="NANO_GPT_API_KEY",
        export_env_var="NANO_GPT_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="novita",
        display="Novita",
        auth_methods=("api_key",),
        key_url="https://novita.ai/settings/key-management",
        limits_url="https://novita.ai",
        signup_url="https://novita.ai",
        omp_provider_id="novita",
        omp_env_var="NOVITA_API_KEY",
        export_env_var="NOVITA_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="nvidia",
        display="NVIDIA",
        auth_methods=("api_key",),
        key_url="https://org.ngc.nvidia.com/setup/personal-keys",
        limits_url="https://build.nvidia.com",
        signup_url="https://www.nvidia.com",
        omp_provider_id="nvidia",
        omp_env_var="NVIDIA_API_KEY",
        export_env_var="NVIDIA_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url; build.nvidia.com shows usage.",
    ),
    Provider(
        id="ollama",
        display="Ollama (local)",
        auth_methods=("api_key",),
        key_url="https://ollama.com",
        limits_url="https://ollama.com",
        signup_url="https://ollama.com",
        omp_provider_id="ollama",
        omp_env_var="OLLAMA_API_KEY",
        export_env_var="OLLAMA_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13). Local engine, "
            "keyless by default (env optional)."
        ),
    ),
    Provider(
        id="ollama-cloud",
        display="Ollama Cloud",
        auth_methods=("api_key",),
        key_url="https://ollama.com/settings/keys",
        limits_url="https://ollama.com/settings",
        signup_url="https://ollama.com",
        omp_provider_id="ollama-cloud",
        omp_env_var="OLLAMA_CLOUD_API_KEY",
        export_env_var="OLLAMA_CLOUD_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="openai",
        display="OpenAI",
        auth_methods=("api_key",),
        key_url="https://platform.openai.com/api-keys",
        limits_url="https://platform.openai.com/usage",
        signup_url="https://platform.openai.com",
        omp_provider_id="openai",
        omp_env_var="OPENAI_API_KEY",
        export_env_var="OPENAI_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13). Platform PAYG "
            "key; distinct from the codex entry (ChatGPT/Codex OAuth subscription)."
        ),
    ),
    Provider(
        id="openai-codex-device",
        display="ChatGPT Plus/Pro (Codex, headless)",
        auth_methods=("api_key",),
        key_url="https://chatgpt.com",
        limits_url="https://chatgpt.com",
        signup_url="https://chatgpt.com",
        omp_provider_id="openai-codex-device",
        omp_env_var="",
        export_env_var="",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id confirmed in omp's provider roster (2026-10-13). omp's headless "
            "device-flow variant of the codex subscription; no key console."
        ),
    ),
    Provider(
        id="opencode-zen",
        display="OpenCode Zen",
        auth_methods=("api_key",),
        key_url="https://opencode.ai/auth",
        limits_url="https://opencode.ai",
        signup_url="https://opencode.ai",
        omp_provider_id="opencode-zen",
        omp_env_var="OPENCODE_API_KEY",
        export_env_var="OPENCODE_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="parallel",
        display="Parallel",
        auth_methods=("api_key",),
        key_url="https://platform.parallel.ai/settings?tab=api-keys",
        limits_url="https://platform.parallel.ai",
        signup_url="https://platform.parallel.ai",
        omp_provider_id="parallel",
        omp_env_var="PARALLEL_API_KEY",
        export_env_var="PARALLEL_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="perplexity",
        display="Perplexity (Pro/Max)",
        auth_methods=("api_key",),
        key_url="https://www.perplexity.ai/settings/api",
        limits_url="https://www.perplexity.ai",
        signup_url="https://www.perplexity.ai",
        omp_provider_id="perplexity",
        omp_env_var="PERPLEXITY_API_KEY",
        export_env_var="PERPLEXITY_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13); Pro/Max "
            "OAuth-style login per roster; key page best-known."
        ),
    ),
    Provider(
        id="qianfan",
        display="Qianfan",
        auth_methods=("api_key",),
        key_url="https://console.bce.baidu.com/qianfan/ais/console/apiKey",
        limits_url="https://console.bce.baidu.com",
        signup_url="https://cloud.baidu.com",
        omp_provider_id="qianfan",
        omp_env_var="QIANFAN_API_KEY",
        export_env_var="QIANFAN_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="qwen-portal",
        display="Qwen Portal",
        auth_methods=("api_key",),
        key_url="https://chat.qwen.ai",
        limits_url="https://chat.qwen.ai",
        signup_url="https://chat.qwen.ai",
        omp_provider_id="qwen-portal",
        omp_env_var="QWEN_OAUTH_TOKEN",
        export_env_var="QWEN_OAUTH_TOKEN",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "key_url is omp's documented login auth-url; QWEN_PORTAL_API_KEY alias for the "
            "same route."
        ),
    ),
    Provider(
        id="sakana",
        display="Sakana AI",
        auth_methods=("api_key",),
        key_url="https://console.sakana.ai/api-keys",
        limits_url="https://console.sakana.ai",
        signup_url="https://console.sakana.ai",
        omp_provider_id="sakana",
        omp_env_var="SAKANA_API_KEY",
        export_env_var="SAKANA_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url; FUGU_API_KEY alias.",
    ),
    Provider(
        id="siliconflow",
        display="SiliconFlow",
        auth_methods=("api_key",),
        key_url="https://cloud.siliconflow.com/account/ak",
        limits_url="https://cloud.siliconflow.com",
        signup_url="https://cloud.siliconflow.com",
        omp_provider_id="siliconflow",
        omp_env_var="SILICONFLOW_API_KEY",
        export_env_var="SILICONFLOW_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="siliconflow-cn",
        display="SiliconFlow (China)",
        auth_methods=("api_key",),
        key_url="https://cloud.siliconflow.cn/account/ak",
        limits_url="https://cloud.siliconflow.cn",
        signup_url="https://cloud.siliconflow.cn",
        omp_provider_id="siliconflow-cn",
        omp_env_var="SILICONFLOW_CN_API_KEY",
        export_env_var="SILICONFLOW_CN_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="singularityapi-dev",
        display="SingularityAPI",
        auth_methods=("api_key",),
        key_url="https://app.singularityapi.dev",
        limits_url="https://app.singularityapi.dev",
        signup_url="https://app.singularityapi.dev",
        omp_provider_id="singularityapi-dev",
        omp_env_var="SINGULARITYAPI_DEV_API_KEY",
        export_env_var="SINGULARITYAPI_DEV_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "key_url is omp's documented login auth-url; pay-as-you-go gateway (300+ "
            "models)."
        ),
    ),
    Provider(
        id="singularityapi-tech",
        display="SingularityAPI Reserved Lanes",
        auth_methods=("api_key",),
        key_url="https://app.singularityapi.tech",
        limits_url="https://app.singularityapi.tech/compute/billing",
        signup_url="https://app.singularityapi.tech",
        omp_provider_id="singularityapi-tech",
        omp_env_var="SINGULARITYAPI_TECH_API_KEY",
        export_env_var="SINGULARITYAPI_TECH_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "key_url is omp's documented login auth-url; reserved DeepSeek lanes, billed "
            "per booked slot."
        ),
    ),
    Provider(
        id="snowflake",
        display="Snowflake Cortex",
        auth_methods=("api_key",),
        key_url="https://app.snowflake.com",
        limits_url="https://app.snowflake.com",
        signup_url="https://www.snowflake.com",
        omp_provider_id="snowflake",
        omp_env_var="SNOWFLAKE_PAT",
        export_env_var="SNOWFLAKE_PAT",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "no documented key page in the roster - URLs best-known; omp also stores "
            "SNOWFLAKE_ACCOUNT and offers a browser OAuth login."
        ),
    ),
    Provider(
        id="stencil",
        display="Stencil (invite only)",
        auth_methods=("api_key",),
        key_url="https://auth.stencil.so",
        limits_url="https://auth.stencil.so",
        signup_url="https://auth.stencil.so",
        omp_provider_id="stencil",
        omp_env_var="STENCIL_API_KEY",
        export_env_var="STENCIL_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "key_url is omp's documented login auth-url; invite-only; OAuth login with "
            "STENCIL_BASE_URL/STENCIL_AUTH_URL overrides."
        ),
    ),
    Provider(
        id="stepfun",
        display="StepFun",
        auth_methods=("api_key",),
        key_url="https://platform.stepfun.ai/interface-key",
        limits_url="https://platform.stepfun.ai",
        signup_url="https://platform.stepfun.ai",
        omp_provider_id="stepfun",
        omp_env_var="STEPFUN_API_KEY",
        export_env_var="STEPFUN_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="synthetic",
        display="Synthetic",
        auth_methods=("api_key",),
        key_url="https://dev.synthetic.new/docs/api/overview",
        limits_url="https://dev.synthetic.new",
        signup_url="https://synthetic.new",
        omp_provider_id="synthetic",
        omp_env_var="SYNTHETIC_API_KEY",
        export_env_var="SYNTHETIC_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="tavily",
        display="Tavily",
        auth_methods=("api_key",),
        key_url="https://app.tavily.com/home",
        limits_url="https://app.tavily.com",
        signup_url="https://app.tavily.com",
        omp_provider_id="tavily",
        omp_env_var="TAVILY_API_KEY",
        export_env_var="TAVILY_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url; search API.",
    ),
    Provider(
        id="together",
        display="Together",
        auth_methods=("api_key",),
        key_url="https://api.together.xyz/settings/api-keys",
        limits_url="https://api.together.xyz/settings",
        signup_url="https://www.together.ai",
        omp_provider_id="together",
        omp_env_var="TOGETHER_API_KEY",
        export_env_var="TOGETHER_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="umans",
        display="Umans AI Coding Plan",
        auth_methods=("api_key",),
        key_url="https://app.umans.ai/billing",
        limits_url="https://app.umans.ai",
        signup_url="https://app.umans.ai",
        omp_provider_id="umans",
        omp_env_var="UMANS_AI_CODING_PLAN_API_KEY",
        export_env_var="UMANS_AI_CODING_PLAN_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="venice",
        display="Venice",
        auth_methods=("api_key",),
        key_url="https://venice.ai/settings/api",
        limits_url="https://venice.ai/settings",
        signup_url="https://venice.ai",
        omp_provider_id="venice",
        omp_env_var="VENICE_API_KEY",
        export_env_var="VENICE_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="vercel-ai-gateway",
        display="Vercel AI Gateway",
        auth_methods=("api_key",),
        key_url="https://vercel.com/ai-gateway",
        limits_url="https://vercel.com/ai-gateway",
        signup_url="https://vercel.com",
        omp_provider_id="vercel-ai-gateway",
        omp_env_var="AI_GATEWAY_API_KEY",
        export_env_var="AI_GATEWAY_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13); "
            "VERCEL_AI_GATEWAY_API_KEY alias; URLs best-known."
        ),
    ),
    Provider(
        id="vllm",
        display="vLLM (local)",
        auth_methods=("api_key",),
        key_url="https://docs.vllm.ai",
        limits_url="https://docs.vllm.ai",
        signup_url="https://docs.vllm.ai",
        omp_provider_id="vllm",
        omp_env_var="VLLM_API_KEY",
        export_env_var="VLLM_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13). Local "
            "OpenAI-compatible server; env optional for an unauthenticated local endpoint."
        ),
    ),
    Provider(
        id="wafer-serverless",
        display="Wafer Serverless",
        auth_methods=("api_key",),
        key_url="https://app.wafer.ai",
        limits_url="https://app.wafer.ai/usage",
        signup_url="https://app.wafer.ai",
        omp_provider_id="wafer-serverless",
        omp_env_var="WAFER_SERVERLESS_API_KEY",
        export_env_var="WAFER_SERVERLESS_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url (usage page per roster).",
    ),
    Provider(
        id="web",
        display="Web search engines",
        auth_methods=("api_key",),
        key_url="https://ohmypi.xyz",
        limits_url="https://ohmypi.xyz",
        signup_url="https://ohmypi.xyz",
        omp_provider_id="web",
        omp_env_var="",
        export_env_var="",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id confirmed in omp's provider roster (2026-10-13). omp's umbrella for "
            "web-search tool backends (their keys live under their own providers); not a "
            "vendor."
        ),
    ),
    Provider(
        id="xai",
        display="xAI API",
        auth_methods=("api_key",),
        key_url="https://console.x.ai",
        limits_url="https://console.x.ai",
        signup_url="https://x.ai",
        omp_provider_id="xai",
        omp_env_var="XAI_API_KEY",
        export_env_var="XAI_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13); omp's login "
            "auth-url is console.x.ai/team/default/api-keys."
        ),
    ),
    Provider(
        id="xai-oauth",
        display="xAI Grok OAuth (SuperGrok/X Premium+)",
        auth_methods=("api_key",),
        key_url="https://x.ai",
        limits_url="https://x.ai",
        signup_url="https://x.ai",
        omp_provider_id="xai-oauth",
        omp_env_var="XAI_OAUTH_TOKEN",
        export_env_var="XAI_OAUTH_TOKEN",
        export_oauth_env_var="XAI_OAUTH_TOKEN",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id + env confirmed in omp's provider roster (2026-10-13). Device-code "
            "OAuth for SuperGrok/X Premium+; XAI_API_KEY is accepted for explicit requests "
            "but does not unlock subscription models."
        ),
    ),
    Provider(
        id="xiaomi",
        display="Xiaomi MiMo",
        auth_methods=("api_key",),
        key_url="https://platform.xiaomimimo.com",
        limits_url="https://platform.xiaomimimo.com",
        signup_url="https://platform.xiaomimimo.com",
        omp_provider_id="xiaomi",
        omp_env_var="XIAOMI_API_KEY",
        export_env_var="XIAOMI_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="no documented key page in the roster - URLs best-known.",
    ),
    Provider(
        id="xiaomi-token-plan-ams",
        display="Xiaomi Token Plan (Europe)",
        auth_methods=("api_key",),
        key_url="https://platform.xiaomimimo.com/console/plan-manage",
        limits_url="https://platform.xiaomimimo.com",
        signup_url="https://platform.xiaomimimo.com",
        omp_provider_id="xiaomi-token-plan-ams",
        omp_env_var="XIAOMI_TOKEN_PLAN_AMS_API_KEY",
        export_env_var="XIAOMI_TOKEN_PLAN_AMS_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="xiaomi-token-plan-cn",
        display="Xiaomi Token Plan (China)",
        auth_methods=("api_key",),
        key_url="https://platform.xiaomimimo.com/console/plan-manage",
        limits_url="https://platform.xiaomimimo.com",
        signup_url="https://platform.xiaomimimo.com",
        omp_provider_id="xiaomi-token-plan-cn",
        omp_env_var="XIAOMI_TOKEN_PLAN_CN_API_KEY",
        export_env_var="XIAOMI_TOKEN_PLAN_CN_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="xiaomi-token-plan-sgp",
        display="Xiaomi Token Plan (Singapore)",
        auth_methods=("api_key",),
        key_url="https://platform.xiaomimimo.com/console/plan-manage",
        limits_url="https://platform.xiaomimimo.com",
        signup_url="https://platform.xiaomimimo.com",
        omp_provider_id="xiaomi-token-plan-sgp",
        omp_env_var="XIAOMI_TOKEN_PLAN_SGP_API_KEY",
        export_env_var="XIAOMI_TOKEN_PLAN_SGP_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="yolo-auto",
        display="Yolo-Auto",
        auth_methods=("api_key",),
        key_url="https://yolo-auto.com/app",
        limits_url="https://yolo-auto.com",
        signup_url="https://yolo-auto.com",
        omp_provider_id="yolo-auto",
        omp_env_var="YOLO_AUTO_API_KEY",
        export_env_var="YOLO_AUTO_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="zai-coding-plan",
        display="Z.AI (GLM Coding Plan - Sign in)",
        auth_methods=("api_key",),
        key_url="https://z.ai",
        limits_url="https://z.ai",
        signup_url="https://z.ai",
        omp_provider_id="zai-coding-plan",
        omp_env_var="",
        export_env_var="",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp id confirmed in omp's provider roster (2026-10-13). Browser-OAuth sign-in "
            "variant of the zai coding plan; the api-key route is the zai entry."
        ),
    ),
    Provider(
        id="zenmux",
        display="ZenMux",
        auth_methods=("api_key",),
        key_url="https://zenmux.ai/settings/keys",
        limits_url="https://zenmux.ai",
        signup_url="https://zenmux.ai",
        omp_provider_id="zenmux",
        omp_env_var="ZENMUX_API_KEY",
        export_env_var="ZENMUX_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
    Provider(
        id="zhipu-coding-plan",
        display="Zhipu Coding Plan",
        auth_methods=("api_key",),
        key_url="https://bigmodel.cn/coding-plan/personal/overview",
        limits_url="https://bigmodel.cn",
        signup_url="https://bigmodel.cn",
        omp_provider_id="zhipu-coding-plan",
        omp_env_var="ZHIPU_API_KEY",
        export_env_var="ZHIPU_API_KEY",
        export_oauth_env_var="",
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes="key_url is omp's documented login auth-url.",
    ),
)


def ids() -> tuple[str, ...]:
    """Return the catalog ids in their fixed, documented order.

    Returns:
        Tuple of provider ids, exactly the 96 lines of docs/providers.md.
    """
    return tuple(provider.id for provider in CATALOG)


def get(provider_id: str) -> Provider:
    """Look up a provider by its catalog id.

    Args:
        provider_id: Catalog id such as ``"zai"`` or ``"cursor-grok"``.

    Returns:
        The matching Provider entry from CATALOG.

    Raises:
        KeyError: If ``provider_id`` is not in the catalog; the message
            lists every valid id so the caller can recover.
    """
    for provider in CATALOG:
        if provider.id == provider_id:
            return provider
    raise KeyError(
        f"unknown provider {provider_id!r}; valid ids: {', '.join(ids())}"
    )


def validate(catalog: Sequence[Provider] = CATALOG) -> list[str]:
    """Check catalog integrity; healthy input yields no problems.

    Rules: unique ids; ids and omp_provider_id match
    ``^[a-z0-9][a-z0-9-]*$``; omp_env_var matches ``^[A-Z][A-Z0-9_]*$``;
    key_url/limits_url/signup_url start with
    ``https://``; limits_kind in {"api", "manual"}; verified entries must
    have a non-empty omp_provider_id and limits_url.

    Args:
        catalog: Entries to check; defaults to the shipped CATALOG so the
            healthy-catalog check is a zero-argument call. Tamper tests pass
            a modified sequence.

    Returns:
        Problem strings, one per violation, each naming the provider id;
        empty list for a healthy catalog.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for provider in catalog:
        pid = provider.id
        if pid in seen:
            problems.append(f"duplicate provider id '{pid}'")
        seen.add(pid)
        if not _ID_RE.match(pid):
            problems.append(f"provider '{pid}': id '{pid}' does not match {_ID_PATTERN}")
        for field in _URL_FIELDS:
            url = getattr(provider, field)
            if url and not url.startswith("https://"):
                problems.append(
                    f"provider '{pid}': {field} must start with https:// (got '{url}')"
                )
        if provider.limits_kind not in _LIMITS_KINDS:
            kinds = ", ".join(sorted(_LIMITS_KINDS))
            problems.append(
                f"provider '{pid}': limits_kind '{provider.limits_kind}' "
                f"not in {{{kinds}}}"
            )
        if provider.verified and not provider.omp_provider_id:
            problems.append(
                f"provider '{pid}': verified=True but omp_provider_id is empty"
            )
        if provider.verified and not provider.limits_url:
            problems.append(
                f"provider '{pid}': verified=True but limits_url is empty"
            )
        omp_id = provider.omp_provider_id
        if omp_id and not _ID_RE.match(omp_id):
            problems.append(
                f"provider '{pid}': omp_provider_id '{omp_id}' "
                f"does not match {_ID_PATTERN}"
            )
        if provider.omp_env_var and not _ENV_RE.match(provider.omp_env_var):
            problems.append(
                f"provider '{pid}': omp_env_var '{provider.omp_env_var}' "
                f"does not match {_ENV_PATTERN}"
            )
        if provider.export_oauth_env_var and not _ENV_RE.match(
            provider.export_oauth_env_var
        ):
            problems.append(
                f"provider '{pid}': export_oauth_env_var "
                f"'{provider.export_oauth_env_var}' does not match {_ENV_PATTERN}"
            )
    return problems


def unverified() -> tuple[Provider, ...]:
    """Return every catalog entry whose facts are not yet confirmed.

    Returns:
        Providers with ``verified=False`` in catalog order; these carry
        best-known values and a warning note.
    """
    return tuple(provider for provider in CATALOG if not provider.verified)
