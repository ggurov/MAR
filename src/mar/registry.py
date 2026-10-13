"""Subscription registry: CRUD + versioned, atomic JSON persistence.

Owns the on-disk contract for ``registry.json``:
``{"version": 1, "subscriptions": [...]}`` where each subscription keeps its
fields as-is (``models`` is a list). Provider-catalog membership is NOT checked
here — that cross-check lives in cli/bootstrap.

Path resolution (computed at call time so env changes apply between calls):
explicit ``path`` argument > ``$MAR_HOME/registry.json`` > ``~/.mar/registry.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA_VERSION = 1
VALID_AUTH_METHODS = frozenset({"api_key", "oauth"})
VALID_STATUSES = frozenset({"active", "expired", "disabled"})

_SUBSCRIPTION_FIELDS = (
    "provider",
    "auth_method",
    "key",
    "models",
    "default_model",
    "added_at",
    "status",
    "notes",
    "expires_at_ms",
)


class RegistryError(Exception):
    """Raised for any registry validation, duplicate, or persistence problem."""


@dataclass(frozen=True)
class Subscription:
    """One provider subscription.

    Attributes:
        provider: Nonempty provider id (catalog cross-check happens upstream).
        auth_method: ``"api_key"`` or ``"oauth"``.
        key: Secret key; required (nonempty) iff ``auth_method == "api_key"``.
        models: Model ids available under this subscription.
        default_model: Model used by default; when set it must be in ``models``.
        added_at: ISO-8601 timestamp of when the subscription was added.
        status: ``"active"``, ``"expired"``, or ``"disabled"``.
        notes: Free-form user notes.
        expires_at_ms: Token expiry as epoch milliseconds; 0 when unknown
            or not time-limited (api_key subscriptions).
    """

    provider: str
    auth_method: str
    key: str | None
    models: list[str]
    default_model: str | None
    added_at: str
    status: str = "active"
    notes: str = ""
    expires_at_ms: int = 0


def mask(key: str | None) -> str:
    """Mask a secret key for display.

    Args:
        key: Raw key, or None.

    Returns:
        ``first4...last4`` when ``len(key) >= 12``; ``<redacted>`` for
        1..11 characters; ``""`` for None or the empty string.
    """
    if not key:
        return ""
    if len(key) < 12:
        return "<redacted>"
    return f"{key[:4]}...{key[-4:]}"


def _validate(sub: Subscription) -> None:
    """Validate field rules for a subscription about to be stored.

    Raises:
        RegistryError: On an empty/blank/non-str provider, unknown
            auth_method or status, a missing key when auth_method is
            ``api_key``, or a default_model outside ``models``.
    """
    if not isinstance(sub.provider, str) or not sub.provider.strip():
        raise RegistryError("provider must be a nonempty string")
    if sub.auth_method not in VALID_AUTH_METHODS:
        raise RegistryError(
            f"invalid auth_method {sub.auth_method!r} for provider "
            f"{sub.provider!r}; expected one of {sorted(VALID_AUTH_METHODS)}"
        )
    if sub.status not in VALID_STATUSES:
        raise RegistryError(
            f"invalid status {sub.status!r} for provider {sub.provider!r}; "
            f"expected one of {sorted(VALID_STATUSES)}"
        )
    if sub.auth_method == "api_key" and not sub.key:
        raise RegistryError(
            f"provider {sub.provider!r} uses auth_method 'api_key' "
            "but has no key"
        )
    if sub.default_model is not None and sub.default_model not in sub.models:
        raise RegistryError(
            f"default_model {sub.default_model!r} for provider "
            f"{sub.provider!r} is not in models {sub.models!r}"
        )


class Registry:
    """In-memory subscription store persisted to a single JSON file."""

    def __init__(self, path: Path | None = None):
        """Create an empty registry bound to a lazily resolved file path.

        Args:
            path: Explicit registry file; None means resolve on each call
                from ``$MAR_HOME/registry.json`` or ``~/.mar/registry.json``.
        """
        self._explicit = Path(path) if path is not None else None
        self._subs: list[Subscription] = []

    def _resolve_path(self) -> Path:
        """Return the registry file for this call, honoring path precedence."""
        if self._explicit is not None:
            return self._explicit
        mar_home = os.environ.get("MAR_HOME")
        if mar_home:
            return Path(mar_home) / "registry.json"
        return Path.home() / ".mar" / "registry.json"

    @classmethod
    def load(cls, path: Path | None = None) -> Registry:
        """Load a registry from disk.

        Args:
            path: Explicit registry file (see path resolution above).

        Returns:
            A Registry holding the file's subscriptions; a missing file
            yields an empty registry.

        Raises:
            RegistryError: If the file exists but is unreadable, contains
                malformed JSON, lacks a ``version`` key, declares an unknown
                version, or holds a non-object subscription entry.
        """
        reg = cls(path)
        file = reg._resolve_path()
        if not file.exists():
            return reg
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"cannot read registry {file}: {exc}") from exc
        if not isinstance(raw, dict) or "version" not in raw:
            raise RegistryError(f"registry {file} has no 'version' key")
        version = raw["version"]
        if version != SCHEMA_VERSION:
            raise RegistryError(
                f"registry {file} has unknown version {version!r}; "
                f"expected {SCHEMA_VERSION}"
            )
        entries = raw.get("subscriptions", [])
        for entry in entries:
            if not isinstance(entry, dict):
                raise RegistryError(
                    f"registry {file} has a non-object subscription entry: "
                    f"{entry!r}"
                )
            reg._subs.append(
                Subscription(
                    provider=entry.get("provider"),
                    auth_method=entry.get("auth_method"),
                    key=entry.get("key"),
                    models=entry.get("models", []),
                    default_model=entry.get("default_model"),
                    added_at=entry.get("added_at", ""),
                    status=entry.get("status", "active"),
                    notes=entry.get("notes", ""),
                    expires_at_ms=entry.get("expires_at_ms", 0),
                )
            )
        return reg

    def save(self) -> None:
        """Write the registry to disk atomically.

        Serializes to a temp file in the target directory, chmods it 0o600
        (best-effort; silently skipped on filesystems that refuse it), then
        ``os.replace``s the target — readers never see a partial file.

        Raises:
            OSError: If the target directory is not writable or the final
                replace fails.
        """
        file = self._resolve_path()
        file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "subscriptions": [asdict(s) for s in self._subs],
        }
        fd, tmp_name = tempfile.mkstemp(
            dir=file.parent, prefix=file.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
            try:
                os.chmod(tmp_name, 0o600)
            except OSError:
                pass  # best-effort; e.g. Windows FAT or odd ACLs
            os.replace(tmp_name, file)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def add(self, sub: Subscription) -> None:
        """Validate and store a new subscription.

        Args:
            sub: The subscription to append.

        Raises:
            RegistryError: On any field-rule violation (see ``_validate``)
                or when a subscription for ``sub.provider`` already exists.
        """
        _validate(sub)
        if any(s.provider == sub.provider for s in self._subs):
            raise RegistryError(
                f"subscription for provider {sub.provider!r} already exists"
            )
        self._subs.append(sub)

    def remove(self, provider: str) -> None:
        """Delete the subscription for a provider.

        Args:
            provider: Provider id whose subscription is removed.

        Raises:
            RegistryError: If no subscription for ``provider`` exists.
        """
        before = len(self._subs)
        self._subs = [s for s in self._subs if s.provider != provider]
        if len(self._subs) == before:
            raise RegistryError(f"no subscription for provider {provider!r}")

    def get(self, provider: str) -> Subscription:
        """Return the subscription for a provider.

        Args:
            provider: Provider id to look up.

        Returns:
            The stored Subscription.

        Raises:
            KeyError: If no subscription for ``provider`` exists (message
                names the provider).
        """
        for s in self._subs:
            if s.provider == provider:
                return s
        raise KeyError(f"no subscription for provider {provider!r}")

    def active(self) -> list[Subscription]:
        """Return subscriptions with ``status == "active"``, sorted by provider."""
        return [s for s in self._sorted() if s.status == "active"]

    def all(self) -> list[Subscription]:
        """Return every stored subscription, sorted by provider."""
        return self._sorted()

    def _sorted(self) -> list[Subscription]:
        """Return the internal subscriptions sorted by provider id."""
        return sorted(self._subs, key=lambda s: s.provider)
