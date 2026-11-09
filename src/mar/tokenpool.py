"""tokenpool integration: discover pooled models and declare the omp provider.

MAR never proxies model traffic. This module only reads tokenpool's
``/v1/models`` metadata so ``mar export`` can write a matching omp provider.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

__all__ = [
    "DEFAULT_TIMEOUT",
    "TokenpoolError",
    "TokenpoolModel",
    "fetch_models",
    "normalize_base_url",
    "provider_entry",
]

DEFAULT_TIMEOUT = 5.0


class TokenpoolError(Exception):
    """Raised when tokenpool metadata cannot be fetched or parsed."""


@dataclass(frozen=True)
class TokenpoolModel:
    """One model alias advertised by tokenpool.

    Attributes:
        id: Logical model id used by clients (``deepseek-v4.1-flash``).
        name: Display name.
        context_window: Prompt window in tokens.
        max_tokens: Output cap in tokens.
        reasoning: Whether the model exposes reasoning/thinking controls.
        input: Accepted input modalities.
    """

    id: str
    name: str
    context_window: int = 128_000
    max_tokens: int = 32_768
    reasoning: bool = True
    input: tuple[str, ...] = ("text",)


def normalize_base_url(url: str) -> str:
    """Normalize a tokenpool URL to its OpenAI-compatible ``/v1`` root.

    Args:
        url: Base URL, with or without a trailing ``/v1``.

    Returns:
        ``scheme://host[:port]/v1`` (query/fragment dropped).

    Raises:
        TokenpoolError: If the URL is not http(s) with a host.
    """
    raw = url.strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise TokenpoolError(f"invalid tokenpool URL {url!r}")
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _optional_int(data: dict, key: str, default: int) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return default
    return value


def fetch_models(
    url: str,
    *,
    token: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> list[TokenpoolModel]:
    """GET tokenpool's ``/v1/models`` and return its logical model aliases.

    Args:
        url: tokenpool base URL (``/v1`` added when missing).
        token: Optional client bearer token for the proxy.
        timeout: Request timeout in seconds.

    Returns:
        One entry per valid model alias; malformed rows are skipped.

    Raises:
        TokenpoolError: On HTTP/connection/timeout failures, malformed JSON,
            or a response with no valid models. Messages never contain the
            token.
    """
    base = normalize_base_url(url)
    headers = {"Accept": "application/json", "User-Agent": "MAR/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{base}/models", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise TokenpoolError(f"tokenpool {base} unreachable: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TokenpoolError(f"tokenpool {base} returned invalid JSON") from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise TokenpoolError(f"tokenpool {base} response has no data list")
    models: list[TokenpoolModel] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = row.get("id")
        if not isinstance(model_id, str) or not model_id or model_id in seen:
            continue
        seen.add(model_id)
        inputs = row.get("input")
        modalities = tuple(
            item for item in inputs if isinstance(item, str) and item
        ) if isinstance(inputs, list) else ("text",)
        name = row.get("name")
        models.append(
            TokenpoolModel(
                id=model_id,
                name=name if isinstance(name, str) and name else model_id,
                context_window=_optional_int(row, "context_window", 128_000),
                max_tokens=_optional_int(row, "max_tokens", 32_768),
                reasoning=bool(row.get("reasoning", True)),
                input=modalities or ("text",),
            )
        )
    if not models:
        raise TokenpoolError(f"tokenpool {base} advertises no usable models")
    return models


def provider_entry(url: str, models: list[TokenpoolModel]) -> dict[str, object]:
    """Build the omp ``models.yml`` provider entry for tokenpool.

    Args:
        url: tokenpool base URL (normalized internally).
        models: Models returned by :func:`fetch_models`.

    Returns:
        A provider mapping with ``api``, ``auth: none``, ``baseUrl`` and one
        model entry per alias (zero local cost, real context/output caps).

    Raises:
        TokenpoolError: If no models are supplied.
    """
    base = normalize_base_url(url)
    if not models:
        raise TokenpoolError("tokenpool provider needs at least one model")
    return {
        "api": "openai-completions",
        "auth": "none",
        "baseUrl": base,
        "models": [
            {
                "id": model.id,
                "name": model.name,
                "reasoning": model.reasoning,
                "input": list(model.input),
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": model.context_window,
                "maxTokens": model.max_tokens,
            }
            for model in models
        ],
    }
