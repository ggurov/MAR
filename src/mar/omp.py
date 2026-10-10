"""omp adapter: write provider credentials into agent.db and model roles into config.yml.

MAR pushes the user's subscription keys into an Oh My Pi (omp) install so omp
itself authenticates against providers. Two artifacts are touched:

- ``<agent_dir>/agent.db`` — SQLite store, table ``auth_credentials`` (DDL
  verified against a live omp install; kept verbatim in
  ``_AUTH_CREDENTIALS_DDL`` and mirrored in docs/architecture.md).
- ``<agent_dir>/config.yml`` — omp config; MAR only edits the ``modelRoles``
  mapping and leaves every other top-level key alone.

MAR is not a proxy: nothing here carries model traffic.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import yaml

__all__ = [
    "OauthCredential",
    "OmpError",
    "default_agent_dir",
    "list_credentials",
    "read_oauth_credentials",
    "set_model_role",
    "upsert_credential",
    "upsert_env_key",
]

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Verbatim live DDL from docs/architecture.md ("omp integration contract").
# Applied only when agent.db exists without the auth_credentials table, so the
# sqlite_master.sql text stays identical to omp's own schema.
_AUTH_CREDENTIALS_DDL = """CREATE TABLE auth_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            credential_type TEXT NOT NULL,
            data TEXT NOT NULL,
            disabled_cause TEXT DEFAULT NULL,
            identity_key TEXT DEFAULT NULL,
            created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
            updated_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
         )"""

# omp writes data as a compact JSON object with a "key" field (verified live).
_DATA_SEPARATORS = (",", ":")


class OmpError(Exception):
    """Raised for invalid adapter arguments or unusable omp files.

    Covers empty provider/key/role/model values, a config.yml that is not a
    YAML mapping (or whose modelRoles entry is not a mapping), and I/O failures
    against agent.db / config.yml.
    """


def default_agent_dir() -> Path:
    """Resolve the omp agent directory.

    Returns $PI_CODING_AGENT_DIR when set to a non-blank value (read at call
    time) — the override omp itself honors — else
    ``Path.home() / ".omp" / "agent"``. The directory is not created.

    Returns:
        Path to the omp agent directory.
    """
    override = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".omp" / "agent"


def upsert_credential(agent_dir: Path, provider: str, key: str) -> None:
    """Insert or replace the ``api_key`` credential row for one provider.

    Opens (creating if missing, together with ``agent_dir``) the SQLite store at
    ``<agent_dir>/agent.db``; bootstraps the ``auth_credentials`` table with the
    live omp DDL when absent. Deletes existing rows with
    ``(provider, credential_type='api_key')`` and inserts one new row whose
    ``data`` is the compact JSON ``{"key": key}``. Rows of other providers or
    other credential types are never touched.

    Args:
        agent_dir: omp agent directory holding agent.db.
        provider: omp provider id, e.g. ``"zai"``.
        key: the API key to store.

    Raises:
        OmpError: provider or key is empty/blank, or the db write failed.
    """
    if not provider or not provider.strip():
        raise OmpError("provider must be a non-empty string")
    if not key or not key.strip():
        raise OmpError("key must be a non-empty string")
    agent_dir = Path(agent_dir)
    db_path = agent_dir / "agent.db"
    agent_dir.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(db_path)
        try:
            with conn:
                _ensure_schema(conn)
                conn.execute(
                    "DELETE FROM auth_credentials"
                    " WHERE provider = ? AND credential_type = 'api_key'",
                    (provider,),
                )
                conn.execute(
                    "INSERT INTO auth_credentials (provider, credential_type, data)"
                    " VALUES (?, 'api_key', ?)",
                    (provider, json.dumps({"key": key}, separators=_DATA_SEPARATORS)),
                )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise OmpError(f"failed to write credential for {provider!r} to {db_path}: {exc}") from exc


def list_credentials(agent_dir: Path) -> list[tuple[str, str]]:
    """List distinct credential kinds currently stored in agent.db.

    Args:
        agent_dir: omp agent directory holding agent.db.

    Returns:
        Distinct ``(provider, credential_type)`` tuples ordered by provider;
        ``[]`` when the db (or the table) does not exist yet.

    Raises:
        OmpError: the db exists but could not be read.
    """
    db_path = Path(agent_dir) / "agent.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        try:
            if not _table_exists(conn):
                return []
            rows = conn.execute(
                "SELECT DISTINCT provider, credential_type"
                " FROM auth_credentials ORDER BY provider"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise OmpError(f"failed to read credentials from {db_path}: {exc}") from exc
    return [(row[0], row[1]) for row in rows]


def set_model_role(agent_dir: Path, role: str, model: str) -> None:
    """Set one entry of the ``modelRoles`` mapping in config.yml.

    Loads ``<agent_dir>/config.yml`` with ``yaml.safe_load`` (an empty or
    missing file yields a fresh mapping), sets ``modelRoles[role] = model``,
    and writes the file back with ``yaml.safe_dump(..., sort_keys=False,
    allow_unicode=True)``. All other top-level keys (and other model roles) are
    preserved; omp re-serializes this file anyway, so comments are not kept.

    Args:
        agent_dir: omp agent directory holding config.yml (created if missing).
        role: model role name, e.g. ``"default"`` or ``"judge"``.
        model: model id to assign, e.g. ``"zai/glm-5.3-flash:max"``.

    Raises:
        OmpError: role or model is empty/blank, config.yml is not a YAML
            mapping, its modelRoles entry is not a mapping, or the write failed.
    """
    if not role or not role.strip():
        raise OmpError("role must be a non-empty string")
    if not model or not model.strip():
        raise OmpError("model must be a non-empty string")
    agent_dir = Path(agent_dir)
    config_path = agent_dir / "config.yml"
    config: dict[str, object] = {}
    if config_path.exists():
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise OmpError(f"{config_path} is not valid YAML: {exc}") from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise OmpError(f"{config_path} does not contain a YAML mapping")
        config = loaded
    roles = config.get("modelRoles", {})
    if roles is None:
        roles = {}
    if not isinstance(roles, dict):
        raise OmpError(f"{config_path} has a non-mapping modelRoles entry")
    roles[role] = model
    config["modelRoles"] = roles
    agent_dir.mkdir(parents=True, exist_ok=True)
    try:
        config_path.write_text(
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except OSError as exc:
        raise OmpError(f"failed to write {config_path}: {exc}") from exc


def upsert_env_key(agent_dir: Path, var: str, key: str) -> None:
    """Set ``VAR=key`` in ``<agent_dir>/.env``, preserving all other content.

    omp loads the agent directory's ``.env`` as an environment fallback for
    providers whose catalog entry declares an env-var credential (e.g.
    ``CURSOR_ACCESS_TOKEN``). If a line for ``var`` already exists (with or
    without an ``export `` prefix), its first occurrence is replaced in
    place; otherwise the pair is appended. The file and agent directory are
    created when missing.

    Args:
        agent_dir: omp agent directory containing (or receiving) ``.env``.
        var: Environment variable name; must match ``^[A-Z][A-Z0-9_]*$``.
        key: Credential value written after the ``=``.

    Raises:
        OmpError: If ``var``/``key`` are blank or malformed, or the file
            cannot be written.
    """
    if not var or not _ENV_NAME_RE.match(var):
        raise OmpError(f"invalid environment variable name: {var!r}")
    if not key:
        raise OmpError(f"refusing to write an empty value for {var}")
    env_path = agent_dir / ".env"
    try:
        existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    except OSError as exc:
        raise OmpError(f"failed to read {env_path}: {exc}") from exc
    replacement = f"{var}={key}"
    lines: list[str] = existing.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        name = stripped.removeprefix("export ").split("=", 1)[0].strip()
        if name == var:
            lines[index] = replacement
            replaced = True
            break
    if not replaced:
        lines.append(replacement)
    agent_dir.mkdir(parents=True, exist_ok=True)
    try:
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise OmpError(f"failed to write {env_path}: {exc}") from exc


@dataclass(frozen=True)
class OauthCredential:
    """One OAuth credential row read from omp's agent.db.

    Attributes:
        provider: omp provider id (e.g. ``anthropic``, ``openai-codex``).
        access: Bearer access token as stored by omp.
        expires_ms: Token expiry as epoch milliseconds (0 when unknown).
        email: Account email omp recorded ("" when absent).
        org_name: Account/org label omp recorded ("" when absent).
    """

    provider: str
    access: str
    expires_ms: int
    email: str
    org_name: str


def read_oauth_credentials(agent_dir: Path) -> list[OauthCredential]:
    """Read every OAuth credential row from ``<agent_dir>/agent.db``.

    omp stores OAuth logins with ``credential_type = 'oauth'`` and a JSON
    ``data`` payload containing ``access``/``refresh``/``expires`` (epoch
    milliseconds) plus account metadata — shape verified live 2026-10-10.
    Only the access token is exposed; refresh tokens stay in omp's store.

    Args:
        agent_dir: omp agent directory containing ``agent.db``.

    Returns:
        Credentials ordered by provider id; empty when the db or table is
        missing.

    Raises:
        OmpError: If the database cannot be opened or rows are malformed.
    """
    db_path = agent_dir / "agent.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        try:
            if not _table_exists(conn):
                return []
            rows = conn.execute(
                "SELECT provider, data FROM auth_credentials "
                "WHERE credential_type = 'oauth' ORDER BY provider"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise OmpError(f"failed to read {db_path}: {exc}") from exc
    out: list[OauthCredential] = []
    for provider, data in rows:
        try:
            payload = json.loads(data)
            access = payload["access"]
            expires = int(payload.get("expires", 0))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OmpError(
                f"malformed oauth credential for {provider!r} in {db_path}: {exc}"
            ) from exc
        out.append(
            OauthCredential(
                provider=provider,
                access=access,
                expires_ms=expires,
                email=str(payload.get("email", "")),
                org_name=str(payload.get("orgName", "")),
            )
        )
    return out


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the auth_credentials table with the live omp DDL if it is absent.

    Args:
        conn: open SQLite connection to agent.db (inside a transaction).

    Raises:
        sqlite3.Error: the DDL could not be applied.
    """
    if not _table_exists(conn):
        conn.execute(_AUTH_CREDENTIALS_DDL)


def _table_exists(conn: sqlite3.Connection) -> bool:
    """Check whether the auth_credentials table exists in the connected db.

    Args:
        conn: open SQLite connection.

    Returns:
        True when a table named auth_credentials exists.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'auth_credentials'"
    ).fetchone()
    return row is not None
