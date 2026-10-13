"""Subscription registry: CRUD + versioned persistence via storage backends.

Owns the in-memory subscription set and its validation. Persistence is
delegated to a :class:`mar.store.Store` — by default the historical JSON
file (``registry.json``); optionally a Vault KV-v2 backend (see
``mar.store`` / ``mar.vault``). The on-disk contract for the file backend is
``{"version": 1, "subscriptions": [...]}`` where each subscription keeps its
fields as-is (``models`` is a list). Provider-catalog membership is NOT
checked here — that cross-check lives in cli/bootstrap.

Path resolution (computed at call time so env changes apply between calls):
explicit ``path`` argument > ``$MAR_HOME/registry.json`` > ``~/.mar/registry.json``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path

from mar import store as mar_store
from mar.store import FileStore, Store

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
        raise RegistryError(f"provider {sub.provider!r} uses auth_method 'api_key' but has no key")
    if sub.default_model is not None and sub.default_model not in sub.models:
        raise RegistryError(
            f"default_model {sub.default_model!r} for provider "
            f"{sub.provider!r} is not in models {sub.models!r}"
        )


DEFAULT_REGISTRY_FILENAME = mar_store.DEFAULT_REGISTRY_FILENAME


def default_path() -> Path:
    """Return the registry file used when no explicit path is given.

    Returns:
        ``$MAR_HOME/registry.json`` when ``MAR_HOME`` is set, else
        ``~/.mar/registry.json``.
    """
    return mar_store.default_registry_path()


class Registry:
    """In-memory subscription store persisted through a storage backend."""

    def __init__(self, path: Path | None = None, store: Store | None = None):
        """Create an empty registry bound to a persistence backend.

        Args:
            path: Explicit registry FILE (file backend only); None resolves
                lazily per call from ``$MAR_HOME/registry.json`` or
                ``~/.mar/registry.json``.
            store: Persistence backend; None builds a :class:`FileStore`
                from ``path``. Mutually exclusive with ``path``.

        Raises:
            RegistryError: When both ``path`` and ``store`` are given.
        """
        if path is not None and store is not None:
            raise RegistryError("pass either path or store, not both")
        self._store: Store = (
            store if store is not None else FileStore(Path(path) if path is not None else None)
        )
        self._subs: list[Subscription] = []

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        *,
        store: Store | None = None,
        backend: str | None = None,
    ) -> Registry:
        """Load a registry from its store.

        Args:
            path: Explicit registry file (see path resolution above); when
                given, the file backend is forced.
            store: Explicit persistence backend (skips resolution).
            backend: Backend name (``--backend``); resolves the store via
                ``mar.store.resolve_store`` when ``store`` is not given.

        Returns:
            A Registry holding the stored subscriptions; a missing store
            (no file, no vault registry marker) yields an empty registry.

        Raises:
            RegistryError: If the stored payload is unreadable, lacks a
                ``version`` key, declares an unknown version, or holds a
                non-object subscription entry. Vault-level failures raise
                :class:`mar.vault.VaultError` unchanged.
        """
        if store is None:
            store = mar_store.resolve_store(Path(path) if path is not None else None, backend)
        reg = cls(store=store)
        try:
            raw = store.read()
        except mar_store.StoreError as exc:
            raise RegistryError(str(exc)) from exc
        if raw is None:
            return reg
        if not isinstance(raw, dict) or "version" not in raw:
            raise RegistryError(f"registry at {store!r} has no 'version' key")
        version = raw["version"]
        if version != SCHEMA_VERSION:
            raise RegistryError(
                f"registry at {store!r} has unknown version {version!r}; expected {SCHEMA_VERSION}"
            )
        entries = raw.get("subscriptions", [])
        for entry in entries:
            if not isinstance(entry, dict):
                raise RegistryError(
                    f"registry at {store!r} has a non-object subscription entry: {entry!r}"
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
        """Persist the registry through its store.

        The file backend writes atomically (temp file + ``os.replace``,
        0600 best-effort); the vault backend writes one KV-v2 secret per
        changed provider.

        Raises:
            OSError: If the file write fails (file backend).
            mar.vault.VaultError: If a vault request fails (vault backend).
        """
        self._store.write(
            {
                "version": SCHEMA_VERSION,
                "subscriptions": [asdict(s) for s in self._subs],
            }
        )

    def rotate(self, provider: str, key: str) -> None:
        """Replace the api key of an existing subscription in place.

        Every other field (models, default model, status, notes, added_at)
        is preserved — rotation must not lose configuration. Call
        :meth:`save` afterwards to persist.

        Args:
            provider: Provider id whose key is replaced.
            key: The new key value.

        Raises:
            RegistryError: If ``key`` is empty, no subscription exists for
                ``provider``, or the subscription is not an ``api_key``
                one (oauth tokens refresh via ``mar pull``, not rotation).
        """
        if not isinstance(key, str) or not key.strip():
            raise RegistryError("rotated key must be a non-empty string")
        for index, existing in enumerate(self._subs):
            if existing.provider != provider:
                continue
            if existing.auth_method != "api_key":
                raise RegistryError(
                    f"provider {provider!r} uses auth_method "
                    f"{existing.auth_method!r}; rotate applies to api_key "
                    "subscriptions (oauth: re-run mar pull)"
                )
            replacement = replace(existing, key=key)
            _validate(replacement)
            self._subs[index] = replacement
            return
        raise RegistryError(f"no subscription for provider {provider!r}")

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
            raise RegistryError(f"subscription for provider {sub.provider!r} already exists")
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
