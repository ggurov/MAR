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

import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from mar import store as mar_store
from mar.store import FileStore, Store

SCHEMA_VERSION = 2
"""Registry payload version. 2 adds alternate per-provider accounts; 1 loads."""
LEGACY_SCHEMA_VERSIONS = frozenset({1})
PRIMARY_ACCOUNT = "original"
"""Reserved label for a subscription's own key (``Subscription.key``)."""
VALID_AUTH_METHODS = frozenset({"api_key", "oauth", "none"})
VALID_STATUSES = frozenset({"active", "expired", "disabled"})
_ACCOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

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
    "accounts",
    "selected_account",
)


class RegistryError(Exception):
    """Raised for any registry validation, duplicate, or persistence problem."""


@dataclass(frozen=True)
class Account:
    """One alternate key for a provider subscription.

    Attributes:
        name: Stable label shown in ``mar list`` and the serve radio set
            (letters, digits, ``.``, ``_``, ``-``; ``"original"`` is
            reserved for the subscription's primary key).
        key: Secret API key for this account.
        added_at: ISO-8601 timestamp of when the account was added.
        notes: Free-form user notes.
    """

    name: str
    key: str
    added_at: str = ""
    notes: str = ""

@dataclass(frozen=True)
class Subscription:
    """One provider subscription.

    Attributes:
        provider: Nonempty provider id (catalog cross-check happens upstream).
        auth_method: ``"api_key"``, ``"oauth"``, or ``"none"`` (keyless
            self-hosted endpoint).
        key: Secret key; required (nonempty) iff ``auth_method ==
            "api_key"``, and forbidden for ``"none"``.
        models: Model ids available under this subscription.
        default_model: Model used by default; when set it must be in ``models``.
        added_at: ISO-8601 timestamp of when the subscription was added.
        status: ``"active"``, ``"expired"``, or ``"disabled"``.
        notes: Free-form user notes.
        expires_at_ms: Token expiry as epoch milliseconds; 0 when unknown
            or not time-limited (api_key subscriptions).
        accounts: Alternate keys for the same provider (other billing
            accounts); empty for oauth/keyless subscriptions.
        selected_account: Name of the account whose key credential
            consumers must use; empty means the subscription's own ``key``.
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
    accounts: list[Account] = field(default_factory=list)
    selected_account: str = ""


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


def _validate_account(account: Account) -> None:
    """Validate one alternate account's label and key.

    Raises:
        RegistryError: When the name is blank, malformed, or the reserved
            primary label, or when the key is not a nonblank string.
    """
    if not isinstance(account.name, str) or not _ACCOUNT_NAME_RE.fullmatch(account.name):
        raise RegistryError(
            f"invalid account name {account.name!r}; expected letters, digits, "
            "'.', '_' or '-' (starting with a letter or digit)"
        )
    if account.name == PRIMARY_ACCOUNT:
        raise RegistryError(
            f"account name {PRIMARY_ACCOUNT!r} is reserved for the subscription's "
            "own key; choose another label"
        )
    if not isinstance(account.key, str) or not account.key.strip():
        raise RegistryError(
            f"account {account.name!r} must carry a non-empty key"
        )


def effective_key(sub: Subscription) -> str | None:
    """Return the key credential consumers must use for a subscription.

    Args:
        sub: A subscription, possibly carrying alternate accounts.

    Returns:
        The selected account's key when ``selected_account`` is set, else
        the subscription's own ``key``.

    Raises:
        RegistryError: If ``selected_account`` names no stored account
            (possible only in a hand-edited registry).
    """
    if not sub.selected_account:
        return sub.key
    for account in sub.accounts:
        if account.name == sub.selected_account:
            return account.key
    raise RegistryError(
        f"provider {sub.provider!r} selects unknown account "
        f"{sub.selected_account!r}; run: mar account select {sub.provider} original"
    )


def _validate(sub: Subscription) -> None:
    """Validate field rules for a subscription about to be stored.

    Raises:
        RegistryError: On an empty/blank/non-str provider, unknown
            auth_method or status, a missing key when auth_method is
            ``api_key``, a key on the keyless ``none`` method, a
            default_model outside ``models``, invalid/duplicate account
            labels, accounts on a non-api_key subscription, or a
            ``selected_account`` that names no stored account.
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
    if sub.auth_method == "none" and sub.key:
        raise RegistryError(
            f"provider {sub.provider!r} uses auth_method 'none' (keyless) "
            "but carries a key"
        )
    if sub.default_model is not None and sub.default_model not in sub.models:
        raise RegistryError(
            f"default_model {sub.default_model!r} for provider "
            f"{sub.provider!r} is not in models {sub.models!r}"
        )
    names: set[str] = set()
    for account in sub.accounts:
        if not isinstance(account, Account):
            raise RegistryError(
                f"provider {sub.provider!r} has a non-Account alternate entry: "
                f"{account!r}"
            )
        _validate_account(account)
        if account.name in names:
            raise RegistryError(
                f"provider {sub.provider!r} has duplicate account name "
                f"{account.name!r}"
            )
        names.add(account.name)
    if sub.accounts and sub.auth_method != "api_key":
        raise RegistryError(
            f"provider {sub.provider!r} uses auth_method {sub.auth_method!r}; "
            "alternate accounts apply to api_key subscriptions only"
        )
    if sub.selected_account and (
        not isinstance(sub.selected_account, str) or sub.selected_account not in names
    ):
        raise RegistryError(
            f"provider {sub.provider!r} selects unknown account "
            f"{sub.selected_account!r}; known accounts: {sorted(names) or 'none'}"
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
                ``version`` key, declares an unknown version, holds a
                non-object subscription entry, or holds a malformed
                account entry. Vault-level failures raise
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
        if version != SCHEMA_VERSION and version not in LEGACY_SCHEMA_VERSIONS:
            raise RegistryError(
                f"registry at {store!r} has unknown version {version!r}; "
                f"expected {SCHEMA_VERSION} (or legacy {sorted(LEGACY_SCHEMA_VERSIONS)})"
            )
        entries = raw.get("subscriptions", [])
        for entry in entries:
            if not isinstance(entry, dict):
                raise RegistryError(
                    f"registry at {store!r} has a non-object subscription entry: {entry!r}"
                )
            raw_accounts = entry.get("accounts", [])
            if not isinstance(raw_accounts, list):
                raise RegistryError(
                    f"registry at {store!r} has a non-list accounts field for "
                    f"{entry.get('provider')!r}: {raw_accounts!r}"
                )
            accounts: list[Account] = []
            for item in raw_accounts:
                if not isinstance(item, dict):
                    raise RegistryError(
                        f"registry at {store!r} has a non-object account entry: {item!r}"
                    )
                accounts.append(
                    Account(
                        name=item.get("name"),
                        key=item.get("key"),
                        added_at=item.get("added_at", ""),
                        notes=item.get("notes", ""),
                    )
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
                    accounts=accounts,
                    selected_account=entry.get("selected_account", ""),
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

    def _api_key_index(self, provider: str) -> int:
        """Return the index of an api_key subscription.

        Raises:
            RegistryError: If no subscription exists for ``provider`` or it
                is not an ``api_key`` one.
        """
        for index, existing in enumerate(self._subs):
            if existing.provider != provider:
                continue
            if existing.auth_method != "api_key":
                raise RegistryError(
                    f"provider {provider!r} uses auth_method "
                    f"{existing.auth_method!r}; alternate accounts apply to "
                    "api_key subscriptions only"
                )
            return index
        raise RegistryError(f"no subscription for provider {provider!r}")

    def add_account(
        self,
        provider: str,
        name: str,
        key: str,
        *,
        added_at: str = "",
        notes: str = "",
    ) -> None:
        """Attach an alternate key to an existing api_key subscription.

        The primary ``Subscription.key`` is untouched; the new account is
        stored unselected, so credential consumers keep serving the current
        key until :meth:`select_account` is called. Call :meth:`save`
        afterwards to persist.

        Args:
            provider: Provider id holding the subscription.
            name: Label for this account (``"original"`` and blank labels
                are rejected).
            key: The account's API key (must be nonblank).
            added_at: ISO-8601 timestamp; empty when the caller does not
                track it.
            notes: Free-form user notes.

        Raises:
            RegistryError: On an unknown/non-api_key provider, an invalid
                label or key, or a duplicate account label.
        """
        index = self._api_key_index(provider)
        account = Account(name=name, key=key, added_at=added_at, notes=notes)
        existing = self._subs[index]
        if any(a.name == name for a in existing.accounts):
            raise RegistryError(
                f"provider {provider!r} already has an account named {name!r}"
            )
        replacement = replace(existing, accounts=[*existing.accounts, account])
        _validate(replacement)
        self._subs[index] = replacement

    def select_account(self, provider: str, name: str) -> None:
        """Choose which key credential consumers serve for a provider.

        Args:
            provider: Provider id holding the subscription.
            name: Account label to serve, or ``""``/``"original"`` to serve
                the subscription's own primary key.

        Raises:
            RegistryError: On an unknown/non-api_key provider or a label
                that names no stored account.
        """
        index = self._api_key_index(provider)
        existing = self._subs[index]
        target = "" if name in ("", PRIMARY_ACCOUNT) else name
        if target and target not in {a.name for a in existing.accounts}:
            known = sorted(a.name for a in existing.accounts)
            raise RegistryError(
                f"provider {provider!r} has no account named {target!r}; "
                f"known accounts: {known or 'none'} (use 'original' for the "
                "subscription's own key)"
            )
        replacement = replace(existing, selected_account=target)
        _validate(replacement)
        self._subs[index] = replacement

    def remove_account(self, provider: str, name: str) -> None:
        """Delete one alternate account from a provider.

        When the removed account was selected, the subscription falls back
        to its primary key. Call :meth:`save` afterwards to persist.

        Args:
            provider: Provider id holding the subscription.
            name: Account label to remove.

        Raises:
            RegistryError: On an unknown/non-api_key provider or a label
                that names no stored account.
        """
        index = self._api_key_index(provider)
        existing = self._subs[index]
        if not any(a.name == name for a in existing.accounts):
            raise RegistryError(
                f"provider {provider!r} has no account named {name!r}"
            )
        accounts = [a for a in existing.accounts if a.name != name]
        selected = "" if existing.selected_account == name else existing.selected_account
        self._subs[index] = replace(
            existing, accounts=accounts, selected_account=selected
        )

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
