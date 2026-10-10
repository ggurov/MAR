"""Usage-limit snapshots: real API fetchers + manual-check hints.

Dispatch is driven by the provider catalog's ``limits_kind``:

- ``"api"`` -> :func:`fetch` calls the provider's documented usage endpoint
  over HTTPS and parses it into a :class:`LimitSnapshot` (``source="api"``).
  Shipped fetcher: OpenRouter ``GET https://openrouter.ai/api/v1/credits``.
- ``"manual"`` -> :func:`manual_hint` returns the provider's catalog
  ``limits_url`` plus a one-line instruction; MAR never pretends to fetch
  what it cannot.

Network access goes through the module-level ``_open`` (bound to
``urllib.request.urlopen`` at import) so tests can monkeypatch it; unit
tests never touch the network. Secrets: the API key is sent only as an
``Authorization`` header and is never included in any error message.
"""

from __future__ import annotations

import json
import urllib.error
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
    """

    name: str
    used: float | None
    limit: float | None
    unit: str
    window: str


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
        Ids in fixed catalog order; currently ``["openrouter"]``.
    """
    return [provider.id for provider in CATALOG if provider.limits_kind == "api"]


def manual_hint(provider_id: str) -> str:
    """Return a 2-line manual-check instruction for any cataloged provider.

    Works for ``manual`` providers (the primary path) and ``api`` providers
    alike, because every catalog entry carries a ``limits_url``.

    Args:
        provider_id: Catalog id such as ``"zai"`` or ``"openrouter"``.

    Returns:
        Two lines: the provider's ``limits_url`` from the catalog, then the
        instruction to open that page and check usage manually.

    Raises:
        LimitError: If ``provider_id`` is unknown; the message lists valid ids.
    """
    provider = _lookup(provider_id)
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
        LimitSnapshot with one item: ``credits`` (used = total_usage,
        limit = total_credits, USD, all-time window).

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
    payload = _read_json(request, timeout)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LimitError("openrouter credits response is missing the 'data' object")
    return LimitSnapshot(
        provider="openrouter",
        fetched_at=_now_iso(),
        items=(
            LimitItem(
                name="credits",
                used=_number(data, "total_usage"),
                limit=_number(data, "total_credits"),
                unit="USD",
                window="all-time",
            ),
        ),
        source="api",
    )


_FETCHERS: dict[str, Callable[[str, float], LimitSnapshot]] = {
    "openrouter": _fetch_openrouter,
}


def _read_json(request: urllib.request.Request, timeout: float) -> dict:
    """Perform the request and return the parsed JSON object.

    Args:
        request: Prepared urllib request.
        timeout: Request timeout in seconds.

    Returns:
        The decoded top-level JSON object.

    Raises:
        LimitError: On non-200 status, HTTPError, URLError, timeout, or
            malformed/non-object JSON bodies.
    """
    try:
        with _open(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is not None and status != 200:
                raise LimitError(f"openrouter credits API returned HTTP {status}")
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise LimitError(f"openrouter credits API returned HTTP {exc.code} ({exc.reason})") from exc
    except TimeoutError as exc:
        raise LimitError("openrouter credits API request timed out") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise LimitError("openrouter credits API request timed out") from exc
        raise LimitError(f"openrouter credits API request failed: {exc.reason}") from exc
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LimitError(f"openrouter credits API returned malformed JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LimitError("openrouter credits API response is not a JSON object")
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
