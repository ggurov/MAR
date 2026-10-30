"""omp adapter: write provider credentials and display settings into an omp install.

MAR pushes the user's subscription keys into an Oh My Pi (omp) install so omp
itself authenticates against providers. Three artifacts are touched:

- ``<agent_dir>/agent.db`` — SQLite store, table ``auth_credentials`` (DDL
  verified against a live omp install; kept verbatim in
  ``_AUTH_CREDENTIALS_DDL`` and mirrored in docs/architecture.md).
- ``<agent_dir>/config.yml`` — omp config; MAR edits ``modelRoles`` and the
  ``statusLine`` segment list, leaving every other top-level key alone.
- ``<agent_dir>/extensions/mar-statusline.js`` — a tiny omp extension that
  prints its glyph (the compact thinking symbol when the model exposes
  levels; else the model icon) and ``provider/api-id`` on its own line above
  the status band, because omp's built-in ``model`` segment shows a catalog
  display name instead.

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
    "read_env_credentials",
    "read_model_roles",
    "read_oauth_credentials",
    "set_model_role",
    "standard_status_line",
    "status_line_extension",
    "upsert_credential",
    "upsert_env_key",
    "write_status_line",
    "write_status_line_extension",
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


def _load_config(agent_dir: Path) -> tuple[Path, dict[str, object]]:
    """Load ``<agent_dir>/config.yml`` as a mapping.

    Args:
        agent_dir: omp agent directory holding config.yml.

    Returns:
        ``(config_path, config)``. A missing or empty file yields ``{}``.

    Raises:
        OmpError: If the file cannot be read, is invalid YAML, or does not
            contain a YAML mapping.
    """
    config_path = Path(agent_dir) / "config.yml"
    config: dict[str, object] = {}
    if config_path.exists():
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise OmpError(f"failed to read {config_path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise OmpError(f"{config_path} is not valid YAML: {exc}") from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise OmpError(f"{config_path} does not contain a YAML mapping")
        config = loaded
    return config_path, config


def _write_config(
    agent_dir: Path, config_path: Path, config: dict[str, object]
) -> None:
    """Write a config mapping back to ``config_path``, creating the directory.

    Args:
        agent_dir: omp agent directory.
        config_path: target config.yml path.
        config: complete mapping to serialize.

    Raises:
        OmpError: If the directory or file cannot be written.
    """
    agent_dir = Path(agent_dir)
    agent_dir.mkdir(parents=True, exist_ok=True)
    try:
        config_path.write_text(
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except OSError as exc:
        raise OmpError(f"failed to write {config_path}: {exc}") from exc


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
    config_path, config = _load_config(agent_dir)
    roles = config.get("modelRoles", {})
    if roles is None:
        roles = {}
    if not isinstance(roles, dict):
        raise OmpError(f"{config_path} has a non-mapping modelRoles entry")
    roles[role] = model
    config["modelRoles"] = roles
    _write_config(agent_dir, config_path, config)


def read_env_credentials(agent_dir: Path) -> dict[str, str]:
    """Read credential variables from ``<agent_dir>/.env``.

    omp loads this file as its environment fallback, so on installs that
    authenticate through env vars instead of ``agent.db`` login rows it is
    the authoritative credential source. Parsing mirrors ``upsert_env_key``:
    blank lines and ``#`` comments are skipped, a leading ``export `` is
    ignored, and a matching pair of surrounding single or double quotes is
    stripped. Later lines override earlier ones (shell sourcing semantics),
    and names must match ``^[A-Z][A-Z0-9_]*$``.

    Args:
        agent_dir: omp agent directory that may contain ``.env``.

    Returns:
        Mapping of environment variable name to value; empty values are
        returned as ``""`` so callers can distinguish "unset" from "blank".
        An absent file yields ``{}``.

    Raises:
        OmpError: If the file exists but cannot be read.
    """
    env_path = Path(agent_dir) / ".env"
    if not env_path.exists():
        return {}
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OmpError(f"failed to read {env_path}: {exc}") from exc
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().removeprefix("export ").strip()
        if not _ENV_NAME_RE.match(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


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


def read_model_roles(agent_dir: Path) -> dict[str, str]:
    """Read the ``modelRoles`` mapping from ``<agent_dir>/config.yml``.

    Args:
        agent_dir: omp agent directory whose ``config.yml`` is read.

    Returns:
        Role → model mapping in file order; empty dict when the file is
        missing, empty, or has no ``modelRoles`` key.

    Raises:
        OmpError: If the file cannot be read/parsed or ``modelRoles`` is
            not a string→string mapping.
    """
    config_path = agent_dir / "config.yml"
    if not config_path.exists():
        return {}
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OmpError(f"failed to read {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise OmpError(f"malformed YAML in {config_path}: {exc}") from exc
    if not isinstance(config, dict) or "modelRoles" not in config:
        return {}
    roles = config["modelRoles"]
    if roles is None:
        return {}
    if not isinstance(roles, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in roles.items()
    ):
        raise OmpError(f"{config_path} has a non-string modelRoles mapping")
    return dict(roles)


def standard_status_line() -> dict[str, str | list[str]]:
    """Return MAR's standard omp ``statusLine`` segment configuration.

    The built-in ``model`` segment is deliberately absent: it renders the
    catalog display name (mixed case, provider-specific), not the API model
    id. :func:`write_status_line_extension` supplies ``provider/id`` on its
    own line above the band instead.

    Returns:
        A fresh mapping with ``preset``, ``leftSegments``, and
        ``rightSegments`` suitable for ``config.yml``.
    """
    return {
        "preset": "custom",
        "leftSegments": ["vim", "hostname", "mode", "path", "git", "pr"],
        "rightSegments": ["session_name", "token_total", "cost", "context_pct"],
    }


def write_status_line(agent_dir: Path) -> None:
    """Apply :func:`standard_status_line` to ``config.yml``.

    Loads the config, replaces only ``statusLine.preset``,
    ``statusLine.leftSegments``, and ``statusLine.rightSegments``, and writes
    it back. Other ``statusLine`` preferences (separator, context line, …)
    and every other top-level key are preserved. This also removes the
    built-in ``model`` segment from configs that still carry it.

    Args:
        agent_dir: omp agent directory holding config.yml (created if missing).

    Raises:
        OmpError: If config.yml is unreadable/invalid, ``statusLine`` is not a
            mapping, or the write failed.
    """
    agent_dir = Path(agent_dir)
    config_path, config = _load_config(agent_dir)
    status = config.get("statusLine", {})
    if status is None:
        status = {}
    if not isinstance(status, dict):
        raise OmpError(f"{config_path} has a non-mapping statusLine entry")
    status.update(standard_status_line())
    config["statusLine"] = status
    _write_config(agent_dir, config_path, config)


_STATUS_LINE_EXTENSION = """\
// omp's built-in `model` segment prints the catalog display name, which is
// not the API id, and carries its glyph. Render both on their own line above
// the composer band: `<glyph> provider/id`. The glyph is the compact
// thinking-level symbol when the model exposes levels, else the model icon.
export default function (pi) {
    let current;
    let last;
    let timer;

    const modelGlyph = model => {
        const theme = current.ui.theme;
        if (!theme) return "";
        if (!model.thinking) return theme.icon.model || "";
        const level = pi.getThinkingLevel();
        if (!level || level === "off") return theme.status.disabled || "";
        const display = theme.thinking && theme.thinking[level];
        return display ? display.split(" ")[0] : theme.icon.model || "";
    };

    const render = () => {
        if (!current || typeof current.ui.setWidget !== "function") return;
        const model = current.model;
        if (!model || !model.id) return;
        const glyph = modelGlyph(model);
        const line = (glyph ? glyph + " " : "") + model.provider + "/" + model.id;
        if (line === last) return;
        last = line;
        current.ui.setWidget("mar-model", [line], { placement: "aboveEditor" });
    };

    // The session model is a live getter (`ctx.model`), but omp has no
    // model/thinking-change event an extension can observe, so a change
    // mid-session would otherwise never repaint. Poll; `ctx.setInterval`
    // (unlike a raw timer) is contained and cleared on session shutdown.
    const bind = ctx => {
        // Subagent sessions share the extension instance; only the top-level
        // session owns the terminal widget.
        if (ctx.agent && ctx.agent.kind !== "main") return;
        const previous = current;
        current = ctx;
        last = undefined;
        if (timer && previous && typeof previous.clearTimer === "function") {
            previous.clearTimer(timer);
        }
        timer = typeof ctx.setInterval === "function" ? ctx.setInterval(render, 200) : undefined;
        render();
    };

    pi.on("session_start", async (_event, ctx) => bind(ctx));
    pi.on("session_switch", async (_event, ctx) => bind(ctx));
}
"""


def status_line_extension() -> str:
    """Return the JavaScript source for MAR's omp status-line extension.

    Returns:
        The complete extension module as text. It is dependency-free (no
        runtime import) so omp's loader can evaluate it from
        ``<agent_dir>/extensions`` without resolving a package path.
    """
    return _STATUS_LINE_EXTENSION


def write_status_line_extension(agent_dir: Path) -> Path:
    """Install the MAR status-line extension under ``<agent_dir>/extensions``.

    Overwrites ``mar-statusline.js`` with the current
    :func:`status_line_extension` source; other extensions are untouched.

    Args:
        agent_dir: omp agent directory (created if missing).

    Returns:
        Path to the written extension file.

    Raises:
        OmpError: If the directory or file cannot be written.
    """
    agent_dir = Path(agent_dir)
    extensions_dir = agent_dir / "extensions"
    target = extensions_dir / "mar-statusline.js"
    try:
        extensions_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(status_line_extension(), encoding="utf-8")
    except OSError as exc:
        raise OmpError(f"failed to write {target}: {exc}") from exc
    return target


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
