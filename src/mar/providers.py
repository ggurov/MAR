"""Static catalog of the user's 10 AI providers + integrity validation.

Editorial source: docs/providers.md (kept in sync with it). The catalog is the
single place MAR learns provider facts from; it never invents defaults or
keys. Entries flagged ``verified=True`` were confirmed on this machine
(2026-10-10): live ``~/.omp/agent/agent.db`` rows for cline/opencode/zai, and
OpenRouter's publicly documented credits API. Everything else ships
best-known values and must be confirmed before MAR writes it anywhere
(``mar doctor``, M9, is the promotion path).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["CATALOG", "Provider", "get", "ids", "unverified", "validate"]

_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
_ID_RE = re.compile(_ID_PATTERN)
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
        default_model=None,
        limits_kind="manual",
        verified=False,
        notes=(
            "Best-known endpoints; omp id unverified. One subscription line "
            "covering Cursor's Grok models; split only if the user says so."),
    ),
    Provider(
        id="codex",
        display="OpenAI Codex",
        auth_methods=("api_key",),
        key_url="https://platform.openai.com/api-keys",
        limits_url="https://platform.openai.com/usage",
        signup_url="https://platform.openai.com/signup",
        omp_provider_id="codex",
        default_model=None,
        limits_kind="manual",
        verified=False,
        notes="Best-known endpoints; omp id unverified.",
    ),
    Provider(
        id="claude",
        display="Anthropic Claude",
        auth_methods=("api_key",),
        key_url="https://console.anthropic.com/settings/keys",
        limits_url="https://console.anthropic.com/settings/usage",
        signup_url="https://console.anthropic.com/",
        omp_provider_id="anthropic",
        default_model=None,
        limits_kind="manual",
        verified=False,
        notes="Best-known endpoints; omp id unverified.",
    ),
    Provider(
        id="cline",
        display="Cline",
        auth_methods=("api_key",),
        key_url="https://cline.bot",
        limits_url="https://cline.bot",
        signup_url="https://cline.bot",
        omp_provider_id="cline-pass",
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
        default_model=None,
        limits_kind="manual",
        verified=True,
        notes=(
            "omp provider id observed live in ~/.omp/agent/agent.db "
            "(2026-10-10); key/limits pages not yet confirmed."),
    ),
    Provider(
        id="codebuddy",
        display="CodeBuddy",
        auth_methods=("api_key",),
        key_url="https://www.codebuddy.ai",
        limits_url="https://www.codebuddy.ai",
        signup_url="https://www.codebuddy.ai",
        omp_provider_id="codebuddy",
        default_model=None,
        limits_kind="manual",
        verified=False,
        notes="Best-known endpoints; omp id unverified.",
    ),
    Provider(
        id="openrouter",
        display="OpenRouter",
        auth_methods=("api_key",),
        key_url="https://openrouter.ai/settings/keys",
        limits_url="https://openrouter.ai/credits",
        signup_url="https://openrouter.ai/",
        omp_provider_id="openrouter",
        default_model=None,
        limits_kind="api",
        verified=True,
        notes=(
            "limits_kind 'api': GET /api/v1/credits is a publicly documented "
            "endpoint; omp id unverified."),
    ),
    Provider(
        id="antigravity",
        display="Google Antigravity",
        auth_methods=("api_key",),
        key_url="https://antigravity.google",
        limits_url="https://antigravity.google",
        signup_url="https://antigravity.google",
        omp_provider_id="antigravity",
        default_model=None,
        limits_kind="manual",
        verified=False,
        notes="Best-known endpoints; omp id unverified.",
    ),
    Provider(
        id="copilot",
        display="GitHub Copilot",
        auth_methods=("api_key",),
        key_url="https://github.com/settings/tokens",
        limits_url="https://github.com/settings/copilot",
        signup_url="https://github.com/features/copilot",
        omp_provider_id="copilot",
        default_model=None,
        limits_kind="manual",
        verified=False,
        notes="Best-known endpoints; omp id unverified.",
    ),
)


def ids() -> tuple[str, ...]:
    """Return the catalog ids in their fixed, documented order.

    Returns:
        Tuple of provider ids, exactly the 10 lines of docs/providers.md.
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
    ``^[a-z0-9][a-z0-9-]*$``; key_url/limits_url/signup_url start with
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
    return problems


def unverified() -> tuple[Provider, ...]:
    """Return every catalog entry whose facts are not yet confirmed.

    Returns:
        Providers with ``verified=False`` in catalog order; these carry
        best-known values and a warning note.
    """
    return tuple(provider for provider in CATALOG if not provider.verified)
