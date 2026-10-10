"""Bootstrap: startup-prompt renderer and one-shot omp sync orchestration.

Owns the bridge between MAR's registry and a fresh omp install. Two jobs:

- ``render_prompt`` emits the markdown a user pastes into a fresh omp session:
  every active subscription (ids and default models, never keys) plus the
  instruction to run ``mar omp-sync``, optionally followed by the user's
  custom ``~/.mar/prompt.md`` text.
- ``sync`` pushes one api_key credential per active, verified subscription
  into omp's ``agent.db`` and applies model roles to ``config.yml``. MAR never
  writes a guessed omp provider id: entries with an empty omp id or
  ``verified=False`` are skipped and reported instead.

A registry provider id that is absent from the catalog is never fatal: the
prompt labels it ``(unverified provider)`` and sync reports it as skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mar import omp, providers
from mar.registry import Registry, Subscription

__all__ = ["BootstrapError", "SyncReport", "render_prompt", "sync"]


class BootstrapError(Exception):
    """Raised for bootstrap failures: an empty registry or a missing custom file."""


@dataclass(frozen=True)
class SyncReport:
    """Outcome of one ``sync()`` run.

    Attributes:
        written: omp provider ids whose api_key credential was stored, in
            the order the subscriptions were processed.
        skipped: ``(registry provider id, reason)`` pairs, in processing
            order, for every active subscription that was not written.
    """

    written: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]


def _display_name(provider_id: str) -> str:
    """Return the catalog display name, or ``unverified provider`` if unknown."""
    try:
        return providers.get(provider_id).display
    except KeyError:
        return "unverified provider"


def _subscription_line(sub: Subscription) -> str:
    """Render one ``- id (display) — default model: m|none`` bullet."""
    model = sub.default_model if sub.default_model else "none"
    return f"- {sub.provider} ({_display_name(sub.provider)}) — default model: {model}"


def render_prompt(reg: Registry, *, custom_path: Path | None = None) -> str:
    """Render the markdown startup prompt for pasting into a fresh omp install.

    The prompt names MAR in a one-line header, lists every active
    subscription as ``- <provider id> (<display name>) — default model:
    <model|none>``, and instructs omp to run ``mar omp-sync`` so keys reach
    omp through MAR. Key material never appears anywhere in the output. When
    ``custom_path`` points at an existing file, its full text is appended
    under a ``## Custom setup`` heading.

    Args:
        reg: Registry whose active subscriptions are listed.
        custom_path: Optional file whose text is appended under
            ``## Custom setup``.

    Returns:
        The prompt as markdown text.

    Raises:
        BootstrapError: If the registry has zero active subscriptions, or
            ``custom_path`` is given but the file does not exist.
    """
    active = reg.active()
    if not active:
        raise BootstrapError(
            "registry is empty: add at least one active subscription with"
            " 'mar add' before rendering a bootstrap prompt"
        )
    if custom_path is not None and not Path(custom_path).exists():
        raise BootstrapError(f"custom prompt file not found: {custom_path}")

    lines = [
        "# MAR (My AI Router) — startup prompt",
        "",
        "## Active subscriptions",
        "",
        *(_subscription_line(sub) for sub in active),
        "",
        "## API keys",
        "",
        "This prompt never contains API keys.",
        "Run `mar omp-sync` to pull the credentials for these providers from",
        "MAR into this omp install (agent.db), then restart omp.",
    ]
    if custom_path is not None:
        custom_text = Path(custom_path).read_text(encoding="utf-8")
        lines += ["", "## Custom setup", "", custom_text.rstrip("\n")]
    return "\n".join(lines) + "\n"


def _sync_one(sub: Subscription, agent_dir: Path) -> tuple[str | None, str | None]:
    """Push one subscription's credential; return ``(omp id, None)`` or ``(None, reason)``.

    Never raises: every failure mode collapses into a skip reason so one bad
    provider cannot block the rest.
    """
    try:
        provider = providers.get(sub.provider)
    except KeyError:
        return None, "unknown provider"
    if not provider.omp_provider_id or not provider.verified:
        return None, "omp provider id unverified"
    if sub.auth_method != "api_key" or not sub.key:
        return None, "no api key"
    try:
        omp.upsert_credential(agent_dir, provider.omp_provider_id, sub.key)
    except omp.OmpError as exc:
        return None, str(exc)
    return provider.omp_provider_id, None


def sync(
    reg: Registry,
    *,
    agent_dir: Path | None = None,
    roles: dict[str, str] | None = None,
) -> SyncReport:
    """Write active api_key credentials into omp and apply model roles.

    For every active subscription the catalog entry is resolved; entries with
    an empty or unverified omp provider id, unknown providers, and
    subscriptions without an api key are skipped and reported. An
    ``OmpError`` from one provider is reported as a skip for that provider
    only; the remaining providers are still written. Afterwards, each
    ``roles`` entry is applied via ``set_model_role`` (errors propagate —
    roles are a single all-or-nothing config.yml).

    Args:
        reg: Registry to sync from; only active subscriptions are used.
        agent_dir: omp agent directory; None uses ``omp.default_agent_dir()``.
        roles: Optional ``{role: model}`` mapping applied after the
            credential loop.

    Returns:
        A :class:`SyncReport` listing written omp ids and skip reasons.

    Raises:
        omp.OmpError: If applying a model role fails.
    """
    target = Path(agent_dir) if agent_dir is not None else omp.default_agent_dir()
    written: list[str] = []
    skipped: list[tuple[str, str]] = []
    for sub in reg.active():
        omp_id, reason = _sync_one(sub, target)
        if omp_id is None:
            skipped.append((sub.provider, reason or "skipped"))
        else:
            written.append(omp_id)
    if roles:
        for role, model in roles.items():
            omp.set_model_role(target, role, model)
    return SyncReport(written=tuple(written), skipped=tuple(skipped))
