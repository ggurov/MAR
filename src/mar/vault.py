"""Optional HashiCorp Vault KV-v2 storage backend for the registry.

Activated ONLY by explicit configuration (``mar config backend vault``,
``$MAR_BACKEND=vault``); with nothing set, MAR uses its JSON-file store and
this module is never touched. Stdlib ``urllib`` only — no new runtime
dependency.

Secret layout under ``<mount>/<path>/`` (defaults ``secret/mar/keys/``):

- ``<provider>`` — one KV-v2 secret per subscription. Fields mirror the
  registry's subscription record (``key`` plus ``auth_method``, ``models``,
  …) with two added metadata fields: ``updated_at`` (ISO-8601 UTC) and
  ``rotated_by`` (``user@host`` of the last writer).
- ``_registry`` — schema marker (``{"version": 1, "updated_at": …}``).

Why one secret per provider instead of one blob: rotating a key rewrites
exactly one secret (other providers' data and version history stay
untouched), KV-v2 keeps per-secret version history so a bad rotation can be
rolled back per provider, and fleet consumers can read a single key with
``vault kv get -field=key <mount>/<path>/<provider>``.

Saves are diffed against the server: unchanged providers are not rewritten
(``mar rotate zai`` performs exactly one PUT). Removals delete the
provider's secret (latest version; history remains until pruned).

The token is sent only as the ``X-Vault-Token`` request header; it never
appears in a URL, request body, or error message.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import urllib.error
import urllib.request
from datetime import UTC, datetime

__all__ = ["VaultError", "VaultStore", "VaultUnreachable"]

_open = urllib.request.urlopen  # module-level injectable; tests monkeypatch vault._open

_REGISTRY_META = "_registry"
_REGISTRY_VERSION = 1  # mirror of registry.SCHEMA_VERSION; registry.load re-validates
_METADATA_FIELDS = ("updated_at", "rotated_by")
_DEFAULT_TIMEOUT = 10.0


class VaultError(Exception):
    """Raised when Vault denies a request or returns unusable data.

    Messages carry the HTTP method, API path, status, and Vault's own error
    list — never the token or any secret value.
    """


class VaultUnreachable(VaultError):
    """Raised when the Vault server cannot be contacted or times out."""


def _actor() -> str:
    """Return ``user@host`` identifying who last changed a secret."""
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    host = os.environ.get("HOSTNAME") or ""
    if not host:
        with contextlib.suppress(OSError):
            host = socket.gethostname()
    return f"{user}@{host or 'localhost'}"


def _without_metadata(fields: dict) -> dict:
    """Drop updated_at/rotated_by so two records compare by content only."""
    return {k: v for k, v in fields.items() if k not in _METADATA_FIELDS}


class VaultStore:
    """Registry store backed by Vault KV-v2: one secret per provider.

    Implements the same ``read()/write()`` surface as
    :class:`mar.store.FileStore`; constructed with resolved settings
    (``addr``, ``token``, ``mount``, ``path``) — normally via
    ``mar.store.resolve_vault_settings`` rather than directly.
    """

    def __init__(
        self,
        addr: str,
        token: str,
        mount: str = "secret",
        path: str = "mar/keys",
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        """Bind the store to a Vault server and KV-v2 location.

        Args:
            addr: Vault address, e.g. ``http://127.0.0.1:8200``.
            token: Vault token; sent only as a request header.
            mount: KV-v2 mount point (default ``secret``).
            path: Secret path prefix holding the per-provider secrets
                (default ``mar/keys``).
            timeout: Per-request timeout in seconds.
        """
        self.addr = addr.rstrip("/")
        self._token = token
        self.mount = mount.strip("/")
        self.path = path.strip("/")
        self._timeout = timeout

    # -- Store protocol ------------------------------------------------------

    def read(self) -> dict | None:
        """Load the registry payload from Vault.

        Returns:
            ``{"version": 1, "subscriptions": [...]}``, or None when this
            path holds no registry (no ``_registry`` secret) — callers
            treat that as an empty registry.

        Raises:
            VaultError: On denied/failed requests or an unknown schema
                version marker.
        """
        meta = self._read_secret(_REGISTRY_META)
        if meta is None:
            return None
        version = meta.get("version")
        if version != _REGISTRY_VERSION:
            raise VaultError(
                f"vault registry at {self.mount}/{self.path} has unknown "
                f"version {version!r}; expected {_REGISTRY_VERSION}"
            )
        subscriptions: list[dict] = []
        for key in self._list():
            if key == _REGISTRY_META:
                continue
            fields = self._read_secret(key)
            if fields is None:
                continue  # deleted between list and read
            entry = dict(fields)
            entry["provider"] = key.rstrip("/")
            subscriptions.append(entry)
        return {"version": _REGISTRY_VERSION, "subscriptions": subscriptions}

    def write(self, payload: dict) -> None:
        """Persist the registry payload, writing only what changed.

        Each subscription becomes one secret (plus ``updated_at`` /
        ``rotated_by``); secrets whose content is unchanged are skipped, so
        a single-key rotation is a single PUT. Provider secrets absent from
        the payload are deleted. Finishes by stamping ``_registry``.

        Raises:
            VaultError: On any denied/failed request.
        """
        now = datetime.now(UTC).isoformat()
        by = _actor()
        desired = {
            sub["provider"]: {k: v for k, v in sub.items() if k != "provider"}
            for sub in payload.get("subscriptions", [])
        }
        current: dict[str, dict | None] = {
            key: self._read_secret(key) for key in self._list() if key != _REGISTRY_META
        }
        for provider in sorted(desired):
            fields = dict(desired[provider])
            previous = current.get(provider)
            if previous is not None and _without_metadata(previous) == _without_metadata(fields):
                continue  # unchanged — rotation-friendly: no rewrite
            fields["updated_at"] = now
            fields["rotated_by"] = by
            self._write_secret(provider, fields)
        for provider in sorted(current):
            if provider not in desired:
                self._delete_secret(provider)
        self._write_secret(
            _REGISTRY_META,
            {"version": payload.get("version", _REGISTRY_VERSION), "updated_at": now},
        )

    def ping(self) -> bool:
        """Return True when the Vault server answers (any HTTP status).

        A denial (403/404) still proves reachability; only connection
        failures and timeouts return False.
        """
        try:
            self._list()
        except VaultUnreachable:
            return False
        except VaultError:
            return True  # server answered; it just declined
        return True

    # -- KV-v2 plumbing ------------------------------------------------------

    def _request(
        self, method: str, api_path: str, body: dict | None = None, *, list_mode: bool = False
    ) -> dict | None:
        """Perform one Vault HTTP request and return the parsed JSON body.

        Args:
            method: HTTP verb.
            api_path: Path under ``/v1/`` (e.g. ``secret/data/mar/keys/zai``).
            body: JSON request body, or None.
            list_mode: Append ``?list=true`` (Vault's LIST-over-GET form).

        Returns:
            The parsed response object; None for an empty body (204).

        Raises:
            VaultUnreachable: On connection failure or timeout.
            VaultError: On HTTP >= 400 (message includes Vault's error
                list) or a malformed JSON response.
        """
        url = f"{self.addr}/v1/{api_path}"
        if list_mode:
            url += "?list=true"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["X-Vault-Token"] = self._token
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _open(request, timeout=self._timeout) as response:
                status = getattr(response, "status", None)
                raw = response.read()
        except urllib.error.HTTPError as exc:
            errors: list[str] = []
            with contextlib.suppress(Exception):
                errors = json.loads(exc.read() or b"{}").get("errors") or []
            detail = "; ".join(str(e) for e in errors) or str(exc.reason)
            raise VaultError(
                f"vault {method} {api_path} failed: HTTP {exc.code} ({detail})"
            ) from exc
        except TimeoutError as exc:
            raise VaultUnreachable(f"vault {method} {api_path} timed out") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise VaultUnreachable(f"vault {method} {api_path} timed out") from exc
            raise VaultUnreachable(f"vault unreachable at {self.addr}: {exc.reason}") from exc
        if status is not None and status >= 400:
            raise VaultError(f"vault {method} {api_path} failed: HTTP {status}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise VaultError(f"vault {method} {api_path} returned malformed JSON") from exc

    def _read_secret(self, name: str) -> dict | None:
        """GET one KV-v2 secret; None when it does not exist (404)."""
        try:
            response = self._request("GET", f"{self.mount}/data/{self.path}/{name}")
        except VaultError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
        if response is None:
            return None
        try:
            data = response["data"]["data"]
        except (KeyError, TypeError) as exc:
            raise VaultError(f"vault returned a non-KV2 envelope for {self.path}/{name}") from exc
        if not isinstance(data, dict):
            raise VaultError(f"vault secret {self.path}/{name} is not a JSON object")
        return dict(data)

    def _write_secret(self, name: str, fields: dict) -> None:
        """PUT one KV-v2 secret (full-secret write of ``fields``)."""
        self._request("PUT", f"{self.mount}/data/{self.path}/{name}", {"data": fields})

    def _delete_secret(self, name: str) -> None:
        """DELETE the latest version of one KV-v2 secret."""
        self._request("DELETE", f"{self.mount}/data/{self.path}/{name}")

    def _list(self) -> list[str]:
        """LIST secret names under the store's path (``[]`` when empty/missing)."""
        try:
            response = self._request("GET", f"{self.mount}/metadata/{self.path}", list_mode=True)
        except VaultError as exc:
            if "HTTP 404" in str(exc):
                return []
            raise
        if not isinstance(response, dict):
            return []
        keys = (response.get("data") or {}).get("keys") or []
        return [str(key).rstrip("/") for key in keys]
