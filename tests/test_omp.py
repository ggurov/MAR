"""Tests for the omp adapter: agent.db credentials and config.yml model roles.

All state lives under tmp_path; the real ~/.omp and environment are never touched.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from mar.omp import (
    OmpError,
    default_agent_dir,
    list_credentials,
    read_env_credentials,
    read_model_roles,
    read_oauth_credentials,
    set_model_role,
    standard_status_line,
    status_line_extension,
    upsert_credential,
    upsert_env_key,
    write_status_line,
    write_status_line_extension,
)

FAKE_KEY = "test-key-1234567890"
FAKE_KEY_2 = "test-key-9876543210"

# Exact DDL from docs/architecture.md ("omp integration contract", live DDL).
DOC_DDL = """CREATE TABLE auth_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            credential_type TEXT NOT NULL,
            data TEXT NOT NULL,
            disabled_cause TEXT DEFAULT NULL,
            identity_key TEXT DEFAULT NULL,
            created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
            updated_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
         )"""


def _norm_sql(sql: str) -> str:
    """Collapse all whitespace so sqlite_master.sql can be compared to the doc DDL."""
    return " ".join(sql.split())


def _doc_ddl() -> str:
    """Extract the auth_credentials DDL from docs/architecture.md (source of truth)."""
    doc = Path(__file__).resolve().parents[1] / "docs" / "architecture.md"
    text = doc.read_text(encoding="utf-8")
    for block in re.findall(r"```sql\n(.*?)```", text, re.DOTALL):
        if "auth_credentials" in block:
            return block.strip()
    raise AssertionError("auth_credentials DDL block not found in docs/architecture.md")


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    """Agent dir with agent.db pre-seeded with a foreign api_key row and an oauth row."""
    d = tmp_path / "agent"
    d.mkdir()
    conn = sqlite3.connect(d / "agent.db")
    try:
        conn.execute(DOC_DDL)
        conn.execute(
            "INSERT INTO auth_credentials (provider, credential_type, data) VALUES (?, 'api_key', ?)",
            ("keep-me", '{"key":"test-key-keep-alive"}'),
        )
        conn.execute(
            "INSERT INTO auth_credentials (provider, credential_type, data) VALUES (?, 'oauth', ?)",
            ("keep-me", '{"refresh_token":"test-token-keep"}'),
        )
        conn.commit()
    finally:
        conn.close()
    return d


def _rows(db_path: Path) -> list[tuple]:
    """Return all auth_credentials rows as (provider, credential_type, data)."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT provider, credential_type, data FROM auth_credentials ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()


# --- upsert_credential -------------------------------------------------------


def test_upsert_inserts_api_key_row(agent_dir: Path) -> None:
    upsert_credential(agent_dir, "zai", FAKE_KEY)
    rows = [r for r in _rows(agent_dir / "agent.db") if r[0] == "zai"]
    assert rows == [("zai", "api_key", json.dumps({"key": FAKE_KEY}, separators=(",", ":")))]


def test_upsert_data_matches_documented_live_format(agent_dir: Path) -> None:
    """data is the compact JSON object omp writes live: {"key":"..."}."""
    upsert_credential(agent_dir, "zai", FAKE_KEY)
    conn = sqlite3.connect(agent_dir / "agent.db")
    try:
        (data,) = conn.execute(
            "SELECT data FROM auth_credentials WHERE provider = 'zai'"
        ).fetchone()
    finally:
        conn.close()
    assert data == '{"key":"test-key-1234567890"}'


def test_upsert_same_provider_replaces_old_key(agent_dir: Path) -> None:
    upsert_credential(agent_dir, "zai", FAKE_KEY)
    upsert_credential(agent_dir, "zai", FAKE_KEY_2)
    rows = [r for r in _rows(agent_dir / "agent.db") if r[0] == "zai"]
    assert len(rows) == 1
    assert json.loads(rows[0][2]) == {"key": FAKE_KEY_2}


def test_upsert_keeps_foreign_provider_and_oauth_rows(agent_dir: Path) -> None:
    upsert_credential(agent_dir, "zai", FAKE_KEY)
    rows = _rows(agent_dir / "agent.db")
    assert ("keep-me", "api_key", '{"key":"test-key-keep-alive"}') in rows
    assert ("keep-me", "oauth", '{"refresh_token":"test-token-keep"}') in rows


def test_upsert_bootstraps_missing_db(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh" / "agent"  # parent does not exist yet either
    upsert_credential(fresh, "zai", FAKE_KEY)
    db_path = fresh / "agent.db"
    assert db_path.is_file()
    conn = sqlite3.connect(db_path)
    try:
        (sql,) = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'auth_credentials'"
        ).fetchone()
    finally:
        conn.close()
    assert _norm_sql(sql) == _norm_sql(DOC_DDL)
    assert _norm_sql(sql) == _norm_sql(_doc_ddl())
    rows = _rows(db_path)
    assert rows == [("zai", "api_key", '{"key":"test-key-1234567890"}')]


def test_bootstrap_ddl_matches_doc_even_when_db_preexists_empty(tmp_path: Path) -> None:
    fresh = tmp_path / "agent"
    fresh.mkdir()
    (fresh / "agent.db").touch()  # zero-byte file: omp "created" it but has no tables
    upsert_credential(fresh, "cline-pass", FAKE_KEY)
    conn = sqlite3.connect(fresh / "agent.db")
    try:
        (sql,) = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'auth_credentials'"
        ).fetchone()
    finally:
        conn.close()
    assert _norm_sql(sql) == _norm_sql(_doc_ddl())


# --- list_credentials --------------------------------------------------------


def test_list_credentials_missing_db_returns_empty(tmp_path: Path) -> None:
    assert list_credentials(tmp_path / "nope") == []


def test_list_credentials_reflects_state_and_is_ordered(agent_dir: Path) -> None:
    assert list_credentials(agent_dir) == [("keep-me", "api_key"), ("keep-me", "oauth")]
    upsert_credential(agent_dir, "aardvark", FAKE_KEY)
    upsert_credential(agent_dir, "zai", FAKE_KEY)
    assert list_credentials(agent_dir) == [
        ("aardvark", "api_key"),
        ("keep-me", "api_key"),
        ("keep-me", "oauth"),
        ("zai", "api_key"),
    ]


def test_list_credentials_after_replacement_has_no_duplicate_provider(
    agent_dir: Path,
) -> None:
    upsert_credential(agent_dir, "keep-me", FAKE_KEY)  # replaces keep-me api_key row
    creds = list_credentials(agent_dir)
    assert sorted(creds) == [("keep-me", "api_key"), ("keep-me", "oauth")]
    assert [provider for provider, _ in creds] == sorted(
        provider for provider, _ in creds
    )


# --- set_model_role ----------------------------------------------------------


def test_set_model_role_creates_config_when_missing(tmp_path: Path) -> None:
    set_model_role(tmp_path / "agent", "default", "zai/glm-5.3-flash:max")
    config = yaml.safe_load((tmp_path / "agent" / "config.yml").read_text(encoding="utf-8"))
    assert config == {"modelRoles": {"default": "zai/glm-5.3-flash:max"}}


def test_set_model_role_preserves_unrelated_top_level_keys(tmp_path: Path) -> None:
    cfg = tmp_path / "agent" / "config.yml"
    cfg.parent.mkdir()
    cfg.write_text(
        "shellPath: /bin/bash\ntheme: dark\nsetupVersion: 7\n"
        "modelRoles:\n  default: openrouter/anthropic/claude-3.5-sonnet\n",
        encoding="utf-8",
    )
    set_model_role(tmp_path / "agent", "judge", "typesafe/jev-latest")
    config = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert config["shellPath"] == "/bin/bash"
    assert config["theme"] == "dark"
    assert config["setupVersion"] == 7
    assert config["modelRoles"] == {
        "default": "openrouter/anthropic/claude-3.5-sonnet",
        "judge": "typesafe/jev-latest",
    }


def test_set_model_role_overwrites_existing_role(tmp_path: Path) -> None:
    cfg = tmp_path / "agent" / "config.yml"
    cfg.parent.mkdir()
    cfg.write_text(
        "modelRoles:\n  default: openrouter/old-model\n  judge: typesafe/jev-latest\n",
        encoding="utf-8",
    )
    set_model_role(tmp_path / "agent", "default", "zai/glm-5.3-flash:max")
    config = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert config["modelRoles"] == {
        "default": "zai/glm-5.3-flash:max",
        "judge": "typesafe/jev-latest",
    }


def test_set_model_role_accepts_empty_existing_config(tmp_path: Path) -> None:
    cfg = tmp_path / "agent" / "config.yml"
    cfg.parent.mkdir()
    cfg.write_text("", encoding="utf-8")  # yaml.safe_load -> None
    set_model_role(tmp_path / "agent", "default", "zai/glm-5.3-flash:max")
    assert yaml.safe_load(cfg.read_text(encoding="utf-8")) == {
        "modelRoles": {"default": "zai/glm-5.3-flash:max"}
    }


def test_set_model_role_rejects_non_mapping_config(tmp_path: Path) -> None:
    cfg = tmp_path / "agent" / "config.yml"
    cfg.parent.mkdir()
    cfg.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(OmpError, match="mapping"):
        set_model_role(tmp_path / "agent", "default", "zai/glm-5.3-flash:max")


def test_set_model_role_rejects_non_mapping_model_roles(tmp_path: Path) -> None:
    cfg = tmp_path / "agent" / "config.yml"
    cfg.parent.mkdir()
    cfg.write_text("modelRoles: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(OmpError, match="modelRoles"):
        set_model_role(tmp_path / "agent", "default", "zai/glm-5.3-flash:max")


# --- error paths (table-driven) ---------------------------------------------


@pytest.mark.parametrize(
    ("provider", "key"),
    [
        ("", FAKE_KEY),
        ("   ", FAKE_KEY),
        ("zai", ""),
        ("zai", "   "),
    ],
    ids=["empty-provider", "blank-provider", "empty-key", "blank-key"],
)
def test_upsert_credential_rejects_empty_args(
    agent_dir: Path, provider: str, key: str
) -> None:
    with pytest.raises(OmpError):
        upsert_credential(agent_dir, provider, key)
    # Nothing was written.
    assert list_credentials(agent_dir) == [("keep-me", "api_key"), ("keep-me", "oauth")]


@pytest.mark.parametrize(
    ("role", "model"),
    [
        ("", "zai/glm-5.3-flash:max"),
        ("   ", "zai/glm-5.3-flash:max"),
        ("default", ""),
        ("default", "   "),
    ],
    ids=["empty-role", "blank-role", "empty-model", "blank-model"],
)
def test_set_model_role_rejects_empty_args(
    tmp_path: Path, role: str, model: str
) -> None:
    with pytest.raises(OmpError):
        set_model_role(tmp_path, role, model)
    assert not (tmp_path / "config.yml").exists()


# --- default_agent_dir -------------------------------------------------------


def test_default_agent_dir_prefers_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "custom"))
    assert default_agent_dir() == tmp_path / "custom"


def test_default_agent_dir_falls_back_to_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    assert default_agent_dir() == Path.home() / ".omp" / "agent"


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "blank"])
def test_default_agent_dir_treats_blank_env_as_unset(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", value)
    assert default_agent_dir() == Path.home() / ".omp" / "agent"


# --- read_model_roles --------------------------------------------------------


def test_read_model_roles_missing_file(tmp_path: Path) -> None:
    assert read_model_roles(tmp_path) == {}


def test_read_model_roles_round_trips(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text(
        "shellPath: bash\nmodelRoles:\n  judge: typesafe/jev-latest\n  default: zai/glm\n",
        encoding="utf-8",
    )
    roles = read_model_roles(tmp_path)
    assert roles == {"judge": "typesafe/jev-latest", "default": "zai/glm"}


def test_read_model_roles_without_key(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("shellPath: bash\n", encoding="utf-8")
    assert read_model_roles(tmp_path) == {}


def test_read_model_roles_rejects_non_mapping(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("modelRoles: [a, b]\n", encoding="utf-8")
    with pytest.raises(OmpError):
        read_model_roles(tmp_path)


# --- status line -------------------------------------------------------------


def test_standard_status_line_omits_model_and_pins_segments() -> None:
    status = standard_status_line()
    assert status["preset"] == "custom"
    assert "model" not in status["leftSegments"]
    assert status["leftSegments"] == ["vim", "hostname", "mode", "path", "git", "pr"]
    assert status["rightSegments"] == [
        "session_name",
        "token_total",
        "cost",
        "context_pct",
    ]


def test_write_status_line_creates_config(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    write_status_line(agent)
    config = yaml.safe_load((agent / "config.yml").read_text(encoding="utf-8"))
    assert config["statusLine"] == standard_status_line()


def test_write_status_line_replaces_cloned_model_segment(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "config.yml").write_text(
        "modelRoles:\n  default: zai/glm-5.3-flash\n"
        "statusLine:\n  preset: custom\n"
        "  leftSegments: [vim, hostname, model]\n"
        "  rightSegments: [token_total]\n"
        "  separator: pipe\n",
        encoding="utf-8",
    )
    write_status_line(agent)
    config = yaml.safe_load((agent / "config.yml").read_text(encoding="utf-8"))
    assert config["modelRoles"] == {"default": "zai/glm-5.3-flash"}
    assert config["statusLine"]["leftSegments"] == [
        "vim",
        "hostname",
        "mode",
        "path",
        "git",
        "pr",
    ]
    assert config["statusLine"]["separator"] == "pipe"  # unrelated preference kept


def test_write_status_line_rejects_non_mapping_status_line(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("statusLine: [a, b]\n", encoding="utf-8")
    with pytest.raises(OmpError, match="statusLine"):
        write_status_line(tmp_path)


def test_status_line_extension_renders_provider_and_api_id() -> None:
    source = status_line_extension()
    assert 'current.model' in source
    assert 'model.provider + "/" + model.id' in source
    assert "pi.getThinkingLevel()" in source
    assert "modelGlyph" in source
    assert "theme.icon.model" in source
    assert 'placement: "aboveEditor"' in source


_JS_RUNTIME = shutil.which("node") or shutil.which("bun")

_EXTENSION_HARNESS = """\
import { pathToFileURL } from "node:url";

const [, , extensionPath] = process.argv;
const extension = await import(pathToFileURL(extensionPath).href);

const handlers = {};
let thinking = "off";
const pi = {
    on(event, handler) { handlers[event] = handler; },
    getThinkingLevel() { return thinking; },
};

const widgets = [];
const timers = new Set();
let model = { provider: "zai", id: "glm-5.3-flash", thinking: true };
const ctx = {
    agent: { kind: "main" },
    get model() { return model; },
    ui: {
        theme: {
            icon: { model: "M" },
            status: { disabled: "o" },
            thinking: { high: "H high" },
        },
        setWidget(_key, lines) { widgets.push(lines.join("\\n")); },
    },
    setInterval(callback, ms) {
        const timer = setInterval(callback, ms);
        timers.add(timer);
        return timer;
    },
    clearTimer(timer) { clearInterval(timer); timers.delete(timer); },
};

extension.default(pi);
await handlers.session_start({}, ctx);
if (widgets.at(-1) !== "o zai/glm-5.3-flash") {
    console.error(`initial widget wrong: ${widgets.at(-1)}`);
    process.exit(1);
}

// A mid-session model switch, like /model, must repaint without a new session.
model = { provider: "openai", id: "gpt-luna-6", thinking: true };
thinking = "high";
await new Promise(resolve => setTimeout(resolve, 400));
if (widgets.at(-1) !== "H openai/gpt-luna-6") {
    console.error(`widget did not follow the model switch: ${widgets.at(-1)}`);
    process.exit(1);
}
const subWidgets = [];
await handlers.session_switch({}, {
    ...ctx,
    agent: { kind: "sub" },
    ui: { ...ctx.ui, setWidget(_key, lines) { subWidgets.push(lines.join("\\n")); } },
});
if (subWidgets.length !== 0) {
    console.error(`subagent session painted the main widget: ${subWidgets}`);
    process.exit(1);
}

for (const timer of timers) clearInterval(timer);
"""


@pytest.mark.skipif(_JS_RUNTIME is None, reason="no JavaScript runtime available")
def test_status_line_extension_repaints_when_model_changes(tmp_path: Path) -> None:
    extension = tmp_path / "mar-statusline.mjs"
    extension.write_text(status_line_extension(), encoding="utf-8")
    harness = tmp_path / "harness.mjs"
    harness.write_text(_EXTENSION_HARNESS, encoding="utf-8")
    result = subprocess.run(
        [_JS_RUNTIME, str(harness), str(extension)],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_write_status_line_extension_creates_managed_file(tmp_path: Path) -> None:
    target = write_status_line_extension(tmp_path / "agent")
    assert target == tmp_path / "agent" / "extensions" / "mar-statusline.js"
    assert target.read_text(encoding="utf-8") == status_line_extension()


def test_write_status_line_extension_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "agent" / "extensions" / "mar-statusline.js"
    target.parent.mkdir(parents=True)
    target.write_text("// stale\n", encoding="utf-8")
    write_status_line_extension(tmp_path / "agent")
    assert target.read_text(encoding="utf-8") == status_line_extension()


def test_write_status_line_extension_unwritable_target(tmp_path: Path) -> None:
    extensions = tmp_path / "agent" / "extensions"
    extensions.mkdir(parents=True)
    (extensions / "mar-statusline.js").mkdir()
    with pytest.raises(OmpError, match="failed to write"):
        write_status_line_extension(tmp_path / "agent")


# --- read_oauth_credentials --------------------------------------------------


def test_read_oauth_credentials_missing_db(tmp_path: Path) -> None:
    assert read_oauth_credentials(tmp_path) == []


def test_read_oauth_credentials_parses_rows(tmp_path: Path) -> None:
    import sqlite3

    (tmp_path / "agent").mkdir()
    conn = sqlite3.connect(tmp_path / "agent" / "agent.db")
    conn.execute(
        "CREATE TABLE auth_credentials (provider TEXT, credential_type TEXT, data TEXT)"
    )
    conn.execute(
        "INSERT INTO auth_credentials VALUES ('anthropic', 'oauth', ?)",
        (
            json.dumps(
                {
                    "access": "tok-access",
                    "refresh": "tok-refresh",
                    "expires": 1791660887205,
                    "email": "user@example.com",
                    "orgName": "example org",
                }
            ),
        ),
    )
    conn.execute(
        "INSERT INTO auth_credentials VALUES ('zai', 'api_key', '{\"key\": \"k\"}')"
    )
    conn.commit()
    conn.close()

    creds = read_oauth_credentials(tmp_path / "agent")
    assert len(creds) == 1  # api_key rows ignored
    cred = creds[0]
    assert cred.provider == "anthropic"
    assert cred.access == "tok-access"
    assert cred.expires_ms == 1791660887205
    assert cred.email == "user@example.com"
    assert cred.org_name == "example org"


def test_read_oauth_credentials_malformed_row_raises(tmp_path: Path) -> None:
    import sqlite3

    (tmp_path / "agent").mkdir()
    conn = sqlite3.connect(tmp_path / "agent" / "agent.db")
    conn.execute(
        "CREATE TABLE auth_credentials (provider TEXT, credential_type TEXT, data TEXT)"
    )
    conn.execute(
        "INSERT INTO auth_credentials VALUES ('anthropic', 'oauth', '{nope')"
    )
    conn.commit()
    conn.close()
    with pytest.raises(OmpError):
        read_oauth_credentials(tmp_path / "agent")


# --- upsert_env_key ----------------------------------------------------------


def test_upsert_env_key_creates_file_and_dir(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    upsert_env_key(agent_dir, "CURSOR_ACCESS_TOKEN", "crsr_fake_1234567890")
    content = (agent_dir / ".env").read_text(encoding="utf-8")
    assert content == "CURSOR_ACCESS_TOKEN=crsr_fake_1234567890\n"


def test_upsert_env_key_preserves_other_lines(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "FOO=bar\nOPENROUTER_API_KEY=old\n# comment\n", encoding="utf-8"
    )
    upsert_env_key(tmp_path, "OPENROUTER_API_KEY", "new")
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines == ["FOO=bar", "OPENROUTER_API_KEY=new", "# comment"]


def test_upsert_env_key_replaces_export_prefixed_line(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("export XAI_API_KEY=old\n", encoding="utf-8")
    upsert_env_key(tmp_path, "XAI_API_KEY", "new")
    assert env.read_text(encoding="utf-8") == "XAI_API_KEY=new\n"


@pytest.mark.parametrize(
    ("var", "key"),
    [("lower_case", "v"), ("HAS SPACE", "v"), ("", "v"), ("GOOD", "")],
    ids=["lower", "space", "empty-var", "empty-key"],
)
def test_upsert_env_key_rejects_bad_args(
    tmp_path: Path, var: str, key: str
) -> None:
    with pytest.raises(OmpError):
        upsert_env_key(tmp_path, var, key)
    assert not (tmp_path / ".env").exists()


# --- read_env_credentials ----------------------------------------------------


def test_read_env_credentials_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_env_credentials(tmp_path / "agent") == {}


def test_read_env_credentials_parses_dotenv_lines(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "# comment\n"
        "export ANTHROPIC_OAUTH_TOKEN=sk-ant-oat-123\n"
        "ZAI_API_KEY='zai-key-1234'\n"
        'OPENROUTER_API_KEY="or-key-1234"\n'
        "EMPTY_VAR=\n"
        "\n"
        "not a line\n"
        "lower_case=ignored\n",
        encoding="utf-8",
    )
    assert read_env_credentials(tmp_path) == {
        "ANTHROPIC_OAUTH_TOKEN": "sk-ant-oat-123",
        "ZAI_API_KEY": "zai-key-1234",
        "OPENROUTER_API_KEY": "or-key-1234",
        "EMPTY_VAR": "",
    }


def test_read_env_credentials_later_line_wins(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("ZAI_API_KEY=old\nZAI_API_KEY=new\n", encoding="utf-8")
    assert read_env_credentials(tmp_path)["ZAI_API_KEY"] == "new"


def test_read_env_credentials_unreadable_file_raises(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.mkdir()
    with pytest.raises(OmpError):
        read_env_credentials(tmp_path)
