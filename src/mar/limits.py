"""Usage-limit snapshots: real API fetchers + manual-check hints.

Dispatch is driven by the provider catalog's ``limits_kind``:

- ``"api"`` -> :func:`fetch` calls the provider's documented usage endpoint
  and parses it into a :class:`LimitSnapshot` (``source="api"``). Shipped
  fetchers: OpenRouter ``GET https://openrouter.ai/api/v1/credits``, Gemini's
  models list (key liveness), and the keyless self-hosted ``strata`` engine's
  own ``{base_url}/models`` list — a LAN endpoint, not a web page.
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
        Ids in fixed catalog order; ``["openrouter", "gemini", "strata"]``.
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
    payload = _read_json(request, timeout, api_name="openrouter credits API")
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


def _fetch_strata(key: str, timeout: float) -> LimitSnapshot:
    """Probe the self-hosted engine's own model list as a liveness snapshot.

    The catalog's ``base_url`` is a LAN llama.cpp server, so this is the one
    fetcher that talks plain ``http://`` by design: ``GET {base_url}/models``
    is llama.cpp's OpenAI-compatible list, and it carries the runtime fields
    MAR reports (loaded model id, ``meta.n_ctx``, input modalities).

    Args:
        key: Unused — the endpoint is keyless; accepted to match the
            fetcher signature and never sent anywhere.
        timeout: Request timeout in seconds.

    Returns:
        LimitSnapshot with no items and a ``note`` of the form
        ``up: 1 model · <id> · 1048576 ctx · text``; ``source="api"``.

    Raises:
        LimitError: On HTTP/connection/timeout failures, malformed JSON, a
            missing/non-list ``data``, or a list with no model id. The
            message names the endpoint family, never a key.
    """
    provider = _lookup("strata")
    models_url = f"{provider.base_url.rstrip('/')}/models"
    request = urllib.request.Request(models_url, method="GET")
    payload = _read_json(request, timeout, api_name="strata models API")
    data = payload.get("data")
    if not isinstance(data, list):
        raise LimitError("strata models API response is missing the 'data' list")
    models = [entry for entry in data if isinstance(entry, dict) and entry.get("id")]
    if not models:
        raise LimitError(
            f"strata models API lists no loaded model at {provider.base_url}"
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
        provider="strata",
        fetched_at=_now_iso(),
        items=(),
        source="api",
        note="up: " + " \u00b7 ".join(parts),
    )

_FETCHERS: dict[str, Callable[[str, float], LimitSnapshot]] = {
    "openrouter": _fetch_openrouter,
    "gemini": _fetch_gemini,
    "strata": _fetch_strata,
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
