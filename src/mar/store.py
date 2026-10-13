"""Registry persistence: storage backends, backend config, and selection.

Owns WHERE registry state lives so the rest of MAR only says "load/save":

- :class:`FileStore` — the default JSON-file backend. Byte-identical with the
  historical ``registry.json`` contract (atomic write, 0600 best-effort,
  ``$MAR_HOME``-aware lazy path resolution).
- :class:`mar.vault.VaultStore` — optional HashiCorp Vault KV-v2 backend
  (one secret per provider; see ``mar.vault``).

Backend selection, in precedence order:

1. an explicit ``path`` (the ``--registry`` flag) always selects FileStore —
   the escape hatch for tests, inspection, and migration;
2. the ``--backend file|vault`` CLI flag;
3. ``$MAR_BACKEND``;
4. ``backend`` in ``~/.mar/config.json`` (written by ``mar config backend``);
5. ``file`` — the default; with no config and no env vars set, MAR behaves
   exactly as it did before backends existed.

The config file never holds secret VALUES: the vault token comes from a
token file (``vault.token_file``) or an environment variable
(``vault.token_env``, default ``VAULT_TOKEN``), read at use time.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from mar import vault

__all__ = [
    "CONFIG_FILENAME",
    "FileStore",
    "Store",
    "StoreError",
    "load_config",
    "resolve_store",
    "resolve_vault_settings",
    "save_config",
]

CONFIG_FILENAME = "config.json"
DEFAULT_REGISTRY_FILENAME = "registry.json"
DEFAULT_VAULT_MOUNT = "secret"
DEFAULT_VAULT_PATH = "mar/keys"
VALID_BACKENDS = ("file", "vault")


class StoreError(Exception):
    """Raised for backend-config problems or an unreadable store.

    Messages are actionable (name the missing setting) and never contain a
    token or key value.
    """


class Store(Protocol):
    """Minimal persistence surface a registry backend must implement."""

    def read(self) -> dict | None:
        """Return the saved registry payload, or None when nothing is stored."""
        ...

    def write(self, payload: dict) -> None:
        """Persist the full registry payload atomically."""
        ...


def _mar_home() -> Path:
    mar_home = os.environ.get("MAR_HOME")
    if mar_home:
        return Path(mar_home)
    return Path.home() / ".mar"


def default_registry_path() -> Path:
    """Return the registry file used when no explicit path is given.

    Returns:
        ``$MAR_HOME/registry.json`` when ``MAR_HOME`` is set, else
        ``~/.mar/registry.json``. Read at call time so env changes apply
        between calls.
    """
    return _mar_home() / DEFAULT_REGISTRY_FILENAME


def _tighten_perms(file: Path) -> None:
    """Best-effort hygiene: chmod secrets-bearing files to 0600.

    No-op on non-posix or odd filesystems; never raises.
    """
    try:
        mode = file.stat().st_mode & 0o777
        if mode != 0o600:
            os.chmod(file, 0o600)
    except OSError:
        pass


def _ensure_private_dir(directory: Path) -> None:
    """Create ``directory`` (with parents) and, when newly created, chmod 0700.

    Existing directories are left alone — only directories MAR itself
    creates (a fresh ``~/.mar``) are made private. Never raises beyond the
    underlying ``mkdir`` OSError.
    """
    existed = directory.exists()
    directory.mkdir(parents=True, exist_ok=True)
    if not existed:
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass


def _config_path() -> Path:
    """Return the backend-config file path (``$MAR_HOME/config.json``)."""
    return _mar_home() / CONFIG_FILENAME


class FileStore:
    """JSON-file registry store — MAR's original persistence, unchanged.

    Writes are atomic (temp file + ``os.replace``) and chmod 0600
    best-effort; reads return None for a missing file so callers treat it
    as an empty registry.
    """

    def __init__(self, path: Path | None = None):
        """Bind the store to a file path.

        Args:
            path: Explicit registry file; None resolves lazily per call from
                ``$MAR_HOME/registry.json`` or ``~/.mar/registry.json``.
        """
        self._explicit = Path(path) if path is not None else None

    def _resolve(self) -> Path:
        """Return the file for this call, honoring explicit over default."""
        if self._explicit is not None:
            return self._explicit
        return default_registry_path()

    def read(self) -> dict | None:
        """Load and return the raw registry payload.

        Hygiene: a file whose mode is more permissive than 0600 is
        tightened to 0600 best-effort (a registry copied around with 0644
        self-heals on first read).

        Returns:
            The parsed JSON object; None when the file does not exist.

        Raises:
            StoreError: If the file exists but cannot be read or parsed.
        """
        file = self._resolve()
        if not file.exists():
            return None
        _tighten_perms(file)
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"cannot read registry {file}: {exc}") from exc
        return raw

    def write(self, payload: dict) -> None:
        """Write the payload atomically.

        Serializes to a temp file in the target directory, chmods it 0o600
        (best-effort), then ``os.replace``s the target — readers never see
        a partial file, and a failure never leaves a temp file behind.

        Raises:
            OSError: If the target directory is not writable or the final
                replace fails.
        """
        file = self._resolve()
        _ensure_private_dir(file.parent)
        fd, tmp_name = tempfile.mkstemp(dir=file.parent, prefix=file.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
            try:
                os.chmod(tmp_name, 0o600)
            except OSError:
                pass  # best-effort; e.g. Windows FAT or odd ACLs
            os.replace(tmp_name, file)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


def load_config(path: Path | None = None) -> dict:
    """Load the backend config.

    Args:
        path: Explicit config file; None uses ``$MAR_HOME/config.json``.

    Returns:
        The parsed config object; ``{}`` when the file is missing.

    Raises:
        StoreError: If the file exists but is unreadable or not a JSON object.
    """
    file = Path(path) if path is not None else _config_path()
    if not file.exists():
        return {}
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreError(f"cannot read mar config {file}: {exc}") from exc
    if not isinstance(raw, dict):
        raise StoreError(f"mar config {file} must be a JSON object")
    return raw


def save_config(config: dict, path: Path | None = None) -> None:
    """Write the backend config atomically with 0600 best-effort perms.

    Args:
        config: The config object to persist.
        path: Explicit config file; None uses ``$MAR_HOME/config.json``.

    Raises:
        OSError: If the write fails.
    """
    file = Path(path) if path is not None else _config_path()
    _ensure_private_dir(file.parent)
    fd, tmp_name = tempfile.mkstemp(dir=file.parent, prefix=file.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, file)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def resolve_vault_settings(overrides: dict | None = None) -> dict:
    """Resolve Vault connection settings: explicit overrides > config > env.

    Args:
        overrides: Optional partial settings (``addr``, ``mount``, ``path``,
            ``token_file``, ``token_env``) that win over the config file;
            falsy values are ignored so callers can pass sparse CLI input.

    Returns:
        ``{"addr", "token", "mount", "path"}`` with the token VALUE resolved
        (token file read now; env var read now).

    Raises:
        StoreError: When no address or no token can be resolved — the
            message names the flags/env vars to set, never a secret.
    """
    cfg: dict = dict(load_config().get("vault") or {})
    cfg.update({k: v for k, v in (overrides or {}).items() if v})
    addr = cfg.get("addr") or os.environ.get("VAULT_ADDR", "").strip()
    mount = cfg.get("mount") or DEFAULT_VAULT_MOUNT
    path = cfg.get("path") or DEFAULT_VAULT_PATH
    token_file = cfg.get("token_file") or os.environ.get("VAULT_TOKEN_FILE", "").strip()
    token_env = cfg.get("token_env", "VAULT_TOKEN")
    token = ""
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise StoreError(f"cannot read vault token file {token_file}: {exc}") from exc
    if not token:
        token = os.environ.get(token_env, "").strip()
    if not addr:
        raise StoreError(
            "vault backend needs an address: pass --addr, set vault.addr in "
            f"{_config_path()}, or export VAULT_ADDR"
        )
    if not token:
        raise StoreError(
            "vault backend needs a token: pass --token-file, set "
            f"vault.token_file in {_config_path()}, or export "
            f"{token_env}"
        )
    return {"addr": addr, "token": token, "mount": mount, "path": path}


def resolve_store(explicit_path: Path | None = None, backend: str | None = None) -> Store:
    """Build the store MAR should use for this operation.

    Args:
        explicit_path: Explicit registry file path (``--registry``); when
            given, the file backend is forced regardless of other settings.
        backend: Explicit backend name (``--backend``); overrides env and
            config.

    Returns:
        A :class:`FileStore` or :class:`mar.vault.VaultStore`.

    Raises:
        StoreError: On an unknown backend name, or vault settings that
            cannot be resolved.
    """
    if explicit_path is not None:
        return FileStore(explicit_path)
    chosen = (
        backend
        or os.environ.get("MAR_BACKEND", "").strip()
        or load_config().get("backend")
        or "file"
    )
    if not isinstance(chosen, str) or chosen not in VALID_BACKENDS:
        raise StoreError(f"unknown backend {chosen!r}; expected one of {VALID_BACKENDS}")
    if chosen == "vault":
        return vault.VaultStore(**resolve_vault_settings())
    return FileStore()
