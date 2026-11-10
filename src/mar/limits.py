"""Usage-limit snapshots: real API fetchers + manual-check hints.

Dispatch is driven by the provider catalog's ``limits_kind``:

- ``"api"`` -> :func:`fetch` calls the provider's documented usage endpoint
  and parses it into a :class:`LimitSnapshot` (``source="api"``). Shipped
  fetchers: OpenRouter ``GET https://openrouter.ai/api/v1/credits``, Gemini's
  models list (key liveness), and every keyless self-hosted llama.cpp
  engine's own ``{base_url}/models`` list (``strata``, ``qwen``) — LAN
  endpoints, not web pages.
- ``"manual"`` -> :func:`manual_hint` returns the provider's catalog
  ``limits_url`` plus a one-line instruction; MAR never pretends to fetch
  what it cannot. Self-hosted entries name their probe command instead.

Network access goes through the module-level ``_open`` (bound to
``urllib.request.urlopen`` at import) so tests can monkeypatch it; unit
tests never touch the network. Secrets: the API key is sent only as an
``Authorization`` header and is never included in any error message.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from mar.providers import CATALOG, Provider, ids

__all__ = [
    "LimitError",
    "LimitItem",
    "LimitSnapshot",
    "fetch",
    "manual_hint",
    "supported",
]

_open = urllib.request.urlopen  # module-level injectable; tests monkeypatch limits._open

_OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
_COMMANDCODE_CREDITS_URL = "https://api.commandcode.ai/alpha/billing/credits"
_COMMANDCODE_SUBSCRIPTIONS_URL = "https://api.commandcode.ai/alpha/billing/subscriptions"
_DEFAULT_TIMEOUT = 10.0


class LimitError(Exception):
    """Raised for unknown providers, non-fetchable kinds, or failed usage fetches."""


@dataclass(frozen=True)
class LimitItem:
    """One usage measurement.

    Attributes:
        name: Stable label, e.g. ``"credits"``.
        used: Consumed amount, or None when the provider does not report it.
        limit: Granted amount, or None when usage is not limit-bounded.
        unit: Measurement unit, e.g. ``"USD"``.
        window: Period the numbers cover, e.g. ``"all-time"``.
        reset_at_ms: Epoch milliseconds when the window resets; 0 when the
            provider does not report a reset.
    """

    name: str
    used: float | None
    limit: float | None
    unit: str
    window: str
    reset_at_ms: int = 0


@dataclass(frozen=True)
class LimitSnapshot:
    """Point-in-time usage view for one provider.

    Attributes:
        provider: Catalog id the snapshot belongs to.
        fetched_at: ISO-8601 UTC timestamp of the fetch.
        items: The individual measurements; may be empty if nothing is tracked.
        source: ``"api"`` (fetched live) or ``"manual"`` (user-checked web page).
        note: Optional human context, e.g. why a fetch fell back to manual.
    """

    provider: str
    fetched_at: str
    items: tuple[LimitItem, ...]
    source: str
    note: str = ""


def supported() -> list[str]:
    """Return catalog ids that have a real fetcher (``limits_kind == "api"``).

    Returns:
        Ids in fixed catalog order: hosted fetchers plus every self-hosted
        endpoint.
    """
    return [provider.id for provider in CATALOG if provider.limits_kind == "api"]


def manual_hint(provider_id: str) -> str:
    """Return a 2-line manual-check instruction for any cataloged provider.

    Works for ``manual`` providers (the primary path) and ``api`` providers
    alike. Entries with a ``limits_url`` get that page; a self-hosted entry
    with only a ``base_url`` gets its endpoint plus the probe command.

    Args:
        provider_id: Catalog id such as ``"zai"`` or ``"openrouter"``.

    Returns:
        Two lines: the provider's ``limits_url`` (or ``base_url`` for a
        self-hosted entry) from the catalog, then the instruction to open
        that page or run the probe.

    Raises:
        LimitError: If ``provider_id`` is unknown; the message lists valid ids.
    """
    provider = _lookup(provider_id)
    if provider.base_url:
        return (
            f"{provider.base_url}\n"
            f"No web usage page: run `mar limits {provider.id}` to probe this"
            " endpoint."
        )
    return f"{provider.limits_url}\nOpen this page and check usage manually."


def fetch(provider_id: str, key: str, *, timeout: float = _DEFAULT_TIMEOUT) -> LimitSnapshot:
    """Fetch a live usage snapshot for a provider with a documented API.

    Args:
        provider_id: Catalog id with ``limits_kind == "api"``.
        key: Secret API key; sent only as a bearer token, never logged.
        timeout: Seconds before the request gives up.

    Returns:
        LimitSnapshot with ``source="api"`` and ``fetched_at`` in ISO-8601 UTC.

    Raises:
        LimitError: If the provider is unknown, has no API fetcher (the
            message points at :func:`manual_hint`), the request fails
            (HTTP status, connection, or timeout), or the response cannot
            be parsed into the expected shape. Error messages never
            contain ``key``.
    """
    provider = _lookup(provider_id)
    if provider.limits_kind != "api":
        raise LimitError(
            f"provider {provider_id!r} publishes usage only via web page "
            f"({provider.limits_url}); no API fetch exists — "
            f"use limits.manual_hint({provider_id!r})"
        )
    if provider.base_url:
        return _fetch_local_endpoint(provider, key, timeout)
    fetcher = _FETCHERS.get(provider_id)
    if fetcher is None:
        raise LimitError(
            f"provider {provider_id!r} is marked limits_kind 'api' but ships "
            "no fetcher; this is a catalog/code sync bug"
        )
    return fetcher(key, timeout)


def _lookup(provider_id: str) -> Provider:
    """Resolve a catalog id, translating unknown ids into LimitError.

    Args:
        provider_id: Id to resolve.

    Returns:
        The matching Provider.

    Raises:
        LimitError: If unknown; the message lists every valid id.
    """
    for provider in CATALOG:
        if provider.id == provider_id:
            return provider
    raise LimitError(f"unknown provider {provider_id!r}; valid ids: {', '.join(ids())}")


def _fetch_openrouter(key: str, timeout: float) -> LimitSnapshot:
    """GET OpenRouter's documented credits endpoint and parse the totals.

    Args:
        key: OpenRouter API key (bearer token).
        timeout: Request timeout in seconds.

    Returns:
        LimitSnapshot with one ``balance`` item in USD: the account's
        remaining credits (``total_credits - total_usage``). It is unbounded
        (``used`` is None), so the UI shows the dollar amount without a
        progress bar.

    Raises:
        LimitError: On non-200/HTTPError/URLError/timeout, malformed JSON,
            missing ``data`` object, or non-numeric totals. Never includes
            ``key`` in the message.
    """
    request = urllib.request.Request(
        _OPENROUTER_CREDITS_URL,
        headers={"Authorization": f"Bearer {key}"},
        method="GET",
    )
    payload = _read_json(request, timeout, api_name="openrouter credits API")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LimitError("openrouter credits response is missing the 'data' object")
    total = _number(data, "total_credits")
    used = _number(data, "total_usage")
    return LimitSnapshot(
        provider="openrouter",
        fetched_at=_now_iso(),
        items=(
            LimitItem(
                name="Balance",
                used=None,
                limit=max(total - used, 0.0),
                unit="USD",
                window="all-time",
            ),
        ),
        source="api",
    )


_GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200"


def _fetch_gemini(key: str, timeout: float) -> LimitSnapshot:
    """GET Google's models list as a key-liveness and access snapshot.

    Google publishes no usage API for AI Studio keys (quotas are per-model
    RPM/RPD tiers, visible only in the AI Studio UI), so the strongest
    machine-checkable signal is the documented models list: a 200 response
    proves the key is alive and shows which models it may access.

    Args:
        key: Gemini API key (``AQ.``-style AI Studio key); sent only as the
            ``x-goog-api-key`` header, never logged.
        timeout: Request timeout in seconds.

    Returns:
        LimitSnapshot with no items and a ``note`` of the form
        ``key alive: N models visible (G gemini)``; ``source="api"``.

    Raises:
        LimitError: On HTTP/connection/timeout failures, malformed JSON, or
            a response without a ``models`` list. Never includes ``key`` in
            the message.
    """
    request = urllib.request.Request(
        _GEMINI_MODELS_URL,
        headers={"x-goog-api-key": key},
        method="GET",
    )
    payload = _read_json(request, timeout, api_name="gemini models API")
    models = payload.get("models")
    if not isinstance(models, list):
        raise LimitError("gemini models response is missing the 'models' list")
    ids = [
        entry.get("name", "").split("/")[-1]
        for entry in models
        if isinstance(entry, dict)
    ]
    gemini = [model_id for model_id in ids if "gemini" in model_id]
    return LimitSnapshot(
        provider="gemini",
        fetched_at=_now_iso(),
        items=(),
        source="api",
        note=f"key alive: {len(ids)} models visible ({len(gemini)} gemini)",
    )


def _fetch_local_endpoint(
    provider: Provider, key: str, timeout: float
) -> LimitSnapshot:
    """Probe a keyless self-hosted engine's own model list for liveness.

    The catalog's ``base_url`` is a LAN llama.cpp server, so these fetchers
    talk plain ``http://`` by design: ``GET {base_url}/models`` is
    llama.cpp's OpenAI-compatible list, and it carries the runtime fields MAR
    reports (loaded model id, ``meta.n_ctx``, input modalities).

    Args:
        provider: Self-hosted catalog entry carrying ``base_url``.
        key: Unused — the endpoint is keyless; accepted to match the
            fetcher signature and never sent anywhere.
        timeout: Request timeout in seconds.

    Returns:
        LimitSnapshot for ``provider.id`` with no items and a ``note`` of
        the form ``up: 1 model · <id> · 1048576 ctx · text``; ``source="api"``.

    Raises:
        LimitError: On HTTP/connection/timeout failures, malformed JSON, a
            missing/non-list ``data``, or a list with no model id. The
            message names the provider and endpoint family, never a key.
    """
    models_url = f"{provider.base_url.rstrip('/')}/models"
    request = urllib.request.Request(models_url, method="GET")
    api_name = f"{provider.id} models API"
    payload = _read_json(request, timeout, api_name=api_name)
    data = payload.get("data")
    if not isinstance(data, list):
        raise LimitError(f"{api_name} response is missing the 'data' list")
    models = [entry for entry in data if isinstance(entry, dict) and entry.get("id")]
    if not models:
        raise LimitError(
            f"{api_name} lists no loaded model at {provider.base_url}"
        )
    first = models[0]
    parts = [f"{len(models)} model" + ("" if len(models) == 1 else "s"), str(first["id"])]
    meta = first.get("meta")
    context_window = meta.get("n_ctx") if isinstance(meta, dict) else None
    if isinstance(context_window, int) and not isinstance(context_window, bool):
        parts.append(f"{context_window} ctx")
    architecture = first.get("architecture")
    modalities = architecture.get("input_modalities") if isinstance(architecture, dict) else None
    if isinstance(modalities, list):
        labels = [m for m in modalities if isinstance(m, str)]
        if labels:
            parts.append("+".join(labels))
    return LimitSnapshot(
        provider=provider.id,
        fetched_at=_now_iso(),
        items=(),
        source="api",
        note="up: " + " \u00b7 ".join(parts),
    )



def _optional_number(data: dict, field: str) -> float | None:
    """Extract an optional numeric (non-bool) field.

    Args:
        data: Mapping that may hold the field.
        field: Field name to read.

    Returns:
        The value as float, or None when absent/null.

    Raises:
        LimitError: When present but not a number.
    """
    value = data.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LimitError(f"billing response field {field!r} must be numeric")
    return float(value)


def _fetch_commandcode(key: str, timeout: float) -> LimitSnapshot:
    """GET commandcode's billing endpoint for the key's own usage.

    The response is account-scoped: two valid keys on the same plan can
    report different balances and window usage. Windows render in order
    5-hour, Weekly, then Monthly.

    commandcode does not return the monthly allowance directly, but the
    rolling caps are fixed slices of it: weekly is half and the 5-hour cap is
    a fifth, so ``monthly = weekly_cap * 2`` (fallback ``5h_cap * 5``).
    ``monthlyCredits`` is the remaining plan balance; purchased/free credits
    are subtracted because they are not part of the monthly allowance.

    The monthly reset is not in the credits response. It comes from the
    account's Stripe subscription (``currentPeriodEnd``) fetched best-effort;
    when that call fails the Monthly item simply has no reset.

    Args:
        key: commandcode API key (bearer token).
        timeout: Request timeout in seconds.

    Returns:
        LimitSnapshot with ``5-hour``/``Weekly`` items in credits (plus their
        reset epoch) and a ``Monthly`` item in USD with the billing-period
        reset. Without window caps the fallback is a plain ``Balance`` item
        (no percentage), still carrying the billing-period reset.

    Raises:
        LimitError: On HTTP/connection/timeout failures, malformed JSON, a
            non-numeric field, or a response without any balance/window.
            Never includes ``key`` in the message.
    """
    request = urllib.request.Request(
        _COMMANDCODE_CREDITS_URL,
        headers=_commandcode_headers(key),
        method="GET",
    )
    payload = _read_json(request, timeout, api_name="commandcode billing API")
    period_end_ms = _commandcode_period_end(key, timeout)
    items: list[LimitItem] = []
    caps: dict[str, float | None] = {}
    windows = payload.get("windowLimits")
    if isinstance(windows, dict):
        for field, label in (("fiveHour", "5-hour"), ("weekly", "Weekly")):
            entry = windows.get(field)
            if not isinstance(entry, dict):
                continue
            used = _optional_number(entry, "used")
            cap = _optional_number(entry, "cap")
            caps[field] = cap
            if used is None and cap is None:
                continue
            raw_reset = entry.get("resetAt") or 0
            reset = (
                int(raw_reset)
                if isinstance(raw_reset, (int, float)) and not isinstance(raw_reset, bool)
                else 0
            )
            items.append(
                LimitItem(
                    name=label,
                    used=used,
                    limit=cap,
                    unit="credits",
                    window=label,
                    reset_at_ms=max(reset, 0),
                )
            )
    credits = payload.get("credits")
    balance = _optional_number(credits, "monthlyCredits") if isinstance(credits, dict) else None
    if balance is not None:
        purchased = _optional_number(credits, "purchasedCredits") or 0.0
        free = _optional_number(credits, "freeCredits") or 0.0
        plan_remaining = max(balance - purchased - free, 0.0)
        monthly_cap = None
        if caps.get("weekly"):
            monthly_cap = float(caps["weekly"]) * 2
        elif caps.get("fiveHour"):
            monthly_cap = float(caps["fiveHour"]) * 5
        if monthly_cap:
            used_monthly = min(max(monthly_cap - plan_remaining, 0.0), monthly_cap)
            items.append(
                LimitItem(
                    name="Monthly",
                    used=used_monthly,
                    limit=monthly_cap,
                    unit="USD",
                    window="monthly",
                    reset_at_ms=period_end_ms,
                )
            )
        else:
            items.append(
                LimitItem(
                    name="Balance",
                    used=None,
                    limit=balance,
                    unit="USD",
                    window="monthly",
                    reset_at_ms=period_end_ms,
                )
            )
    if not items:
        raise LimitError("commandcode billing response has no credit balance or window limits")
    return LimitSnapshot(
        provider="commandcode",
        fetched_at=_now_iso(),
        items=tuple(items),
        source="api",
    )


def _commandcode_headers(key: str) -> dict[str, str]:
    """Return request headers for commandcode's alpha billing routes."""
    return {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        # commandcode's edge rejects the default Python-urllib UA (403).
        "User-Agent": "MAR/1.0 (+https://github.com/ggurov/MAR)",
    }


def _commandcode_period_end(key: str, timeout: float) -> int:
    """Return the account's billing-period end in epoch ms (0 if unknown).

    The credits endpoint never exposes the monthly reset. commandcode's
    subscription object carries ``currentPeriodEnd`` (ISO-8601), which is the
    monthly credit rollover. This is best-effort: a failed/absent subscription
    call leaves the Monthly meter without a reset instead of failing usage.
    """
    request = urllib.request.Request(
        _COMMANDCODE_SUBSCRIPTIONS_URL,
        headers=_commandcode_headers(key),
        method="GET",
    )
    try:
        payload = _read_json(request, timeout, api_name="commandcode subscriptions API")
    except LimitError:
        return 0
    data = payload.get("data")
    if not isinstance(data, dict):
        return 0
    return _parse_iso_ms(data.get("currentPeriodEnd"))


_CLINE_API_BASE = "https://api.cline.bot"
_OPENCODE_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"


def _parse_iso_ms(value: object) -> int:
    """Parse an ISO-8601 timestamp into epoch milliseconds (0 if invalid)."""
    if not isinstance(value, str):
        return 0
    try:
        return max(int(datetime.fromisoformat(value).timestamp() * 1000), 0)
    except ValueError:
        return 0


def _fetch_cline(key: str, timeout: float) -> LimitSnapshot:
    """Read Cline's pay-as-you-go credit balance.

    Cline reports integer money in millionths of a USD (``500000`` = $0.50).
    The profile call supplies the user id required by the balance route.

    Args:
        key: Cline API key (bearer token).
        timeout: Request timeout in seconds.

    Returns:
        LimitSnapshot with one unbounded ``Balance`` item in USD.

    Raises:
        LimitError: On HTTP/connection/timeout failures, malformed JSON, a
            missing user id/balance, or a non-numeric balance. Messages never
            include the key.
    """
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "MAR/1.0 (+https://github.com/ggurov/MAR)",
    }
    profile_request = urllib.request.Request(
        f"{_CLINE_API_BASE}/api/v1/users/me", headers=headers, method="GET"
    )
    profile = _read_json(profile_request, timeout, api_name="cline profile API")
    profile_data = profile.get("data")
    user_id = profile_data.get("id") if isinstance(profile_data, dict) else None
    if not isinstance(user_id, str) or not user_id:
        raise LimitError("cline profile response has no user id")
    balance_request = urllib.request.Request(
        f"{_CLINE_API_BASE}/api/v1/users/{urllib.parse.quote(user_id, safe='')}/balance",
        headers=headers,
        method="GET",
    )
    payload = _read_json(balance_request, timeout, api_name="cline balance API")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LimitError("cline balance response is missing the 'data' object")
    usd = max(_number(data, "balance"), 0.0) / 1_000_000.0
    return LimitSnapshot(
        provider="cline",
        fetched_at=_now_iso(),
        items=(LimitItem(name="Balance", used=None, limit=usd, unit="USD", window="credits"),),
        source="api",
    )


def _fetch_opencode(key: str, timeout: float) -> LimitSnapshot:
    """Read OpenCode Go's rolling quota windows.

    OpenCode's Zen API does not expose the wallet balance to API keys (only a
    browser console session can read it). The Go usage route does expose the
    5-hour/weekly/monthly percentages and reset timestamps; those are used.

    Args:
        key: OpenCode API key (bearer token).
        timeout: Request timeout in seconds.

    Returns:
        LimitSnapshot with percent items for available Go windows.

    Raises:
        LimitError: On HTTP/connection/timeout failures, malformed JSON, or a
            response with no usable window. Messages never include the key.
    """
    request = urllib.request.Request(
        _OPENCODE_USAGE_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "MAR/1.0 (+https://github.com/ggurov/MAR)",
        },
        method="GET",
    )
    payload = _read_json(request, timeout, api_name="opencode usage API")
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise LimitError("opencode usage response has no 'usage' object")
    items: list[LimitItem] = []
    for field, label in (("rolling", "5-hour"), ("weekly", "Weekly"), ("monthly", "Monthly")):
        entry = usage.get(field)
        if not isinstance(entry, dict):
            continue
        percent = _optional_number(entry, "percent")
        reset = _parse_iso_ms(entry.get("resetsAt"))
        if percent is None and not reset:
            continue
        items.append(
            LimitItem(
                name=label,
                used=percent,
                limit=100.0 if percent is not None else None,
                unit="percent",
                window=label,
                reset_at_ms=reset,
            )
        )
    if not items:
        raise LimitError("opencode usage response has no usable quota windows")
    return LimitSnapshot(
        provider="opencode",
        fetched_at=_now_iso(),
        items=tuple(items),
        source="api",
    )


_FETCHERS: dict[str, Callable[[str, float], LimitSnapshot]] = {
    "openrouter": _fetch_openrouter,
    "gemini": _fetch_gemini,
    "commandcode": _fetch_commandcode,
    "cline": _fetch_cline,
    "opencode": _fetch_opencode,
}


def _read_json(request: urllib.request.Request, timeout: float, *, api_name: str) -> dict:
    """Perform the request and return the parsed JSON object.

    Args:
        request: Prepared urllib request.
        timeout: Request timeout in seconds.
        api_name: Human label used in error messages (e.g.
            ``"openrouter credits API"``).

    Returns:
        The decoded top-level JSON object.

    Raises:
        LimitError: On non-200 status, HTTPError, URLError, timeout, or
            malformed/non-object JSON bodies. Messages carry ``api_name``.
    """
    try:
        with _open(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is not None and status != 200:
                raise LimitError(f"{api_name} returned HTTP {status}")
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise LimitError(f"{api_name} returned HTTP {exc.code} ({exc.reason})") from exc
    except TimeoutError as exc:
        raise LimitError(f"{api_name} request timed out") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise LimitError(f"{api_name} request timed out") from exc
        raise LimitError(f"{api_name} request failed: {exc.reason}") from exc
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LimitError(f"{api_name} returned malformed JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LimitError(f"{api_name} response is not a JSON object")
    return payload


def _number(data: dict, field: str) -> float:
    """Extract a strictly numeric (non-bool) field as float.

    Args:
        data: The ``data`` object from the API response.
        field: Field name such as ``"total_credits"``.

    Returns:
        The field value as float.

    Raises:
        LimitError: If the field is missing or not a JSON number.
    """
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LimitError(f"openrouter credits field 'data.{field}' is not numeric")
    return float(value)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with offset."""
    return datetime.now(UTC).isoformat()
