"""Tests for the mar CLI: exit codes, masking, error mapping, real wiring."""

from __future__ import annotations

import io
import json
import re
import sqlite3

import pytest

from mar import cli

FAKE_KEY = "sk-test-1234567890abcd"


@pytest.fixture()
def reg_path(tmp_path):
    return tmp_path / "registry.json"


def run(reg_path, *argv):
    """Invoke main with --registry pointing at the temp registry."""
    return cli.main([*argv, "--registry", str(reg_path)])


def add_zai(reg_path, key=FAKE_KEY):
    return run(reg_path, "add", "zai", "--key", key)


class TestAdd:
    def test_add_persists_subscription_and_masks_key(self, reg_path, capsys):
        assert add_zai(reg_path) == 0
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["subscriptions"][0]["provider"] == "zai"
        out = capsys.readouterr().out
        assert FAKE_KEY not in out
        assert "…" in out or "..." in out

    def test_add_unknown_provider_is_usage_error(self, reg_path, capsys):
        with pytest.raises(SystemExit) as exc:
            run(reg_path, "add", "not-a-provider", "--key", FAKE_KEY)
        assert exc.value.code == 2
        assert "openrouter" in capsys.readouterr().err  # valid ids listed

    @pytest.mark.parametrize("flag", ["--key-stdin"])
    def test_add_reads_key_from_stdin(self, reg_path, monkeypatch, capsys, flag):
        monkeypatch.setattr("sys.stdin", io.StringIO(FAKE_KEY + "\n"))
        assert run(reg_path, "add", "zai", flag) == 0
        assert reg_path.exists()
        out = capsys.readouterr().out
        assert FAKE_KEY not in out

    def test_add_api_key_requires_key(self, reg_path):
        assert run(reg_path, "add", "zai") == 1

    def test_add_duplicate_fails(self, reg_path):
        assert add_zai(reg_path) == 0
        assert add_zai(reg_path) == 1

    def test_add_default_model_auto_appended(self, reg_path):
        assert run(reg_path, "add", "zai", "--key", FAKE_KEY, "--default", "glm-5.3-flash") == 0
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        sub = data["subscriptions"][0]
        assert sub["default_model"] == "glm-5.3-flash"
        assert "glm-5.3-flash" in sub["models"]


class TestRemoveAndList:
    def test_remove_then_list_empty(self, reg_path, capsys):
        add_zai(reg_path)
        assert run(reg_path, "remove", "zai") == 0
        assert run(reg_path, "list") == 0
        assert "no subscriptions" in capsys.readouterr().out

    def test_remove_missing_is_error(self, reg_path):
        assert run(reg_path, "remove", "zai") == 1

    def test_list_json_masks_keys(self, reg_path, capsys):
        add_zai(reg_path)
        capsys.readouterr()  # flush add output
        assert run(reg_path, "list", "--json") == 0
        rows = json.loads(capsys.readouterr().out)
        assert rows[0]["provider"] == "zai"
        assert FAKE_KEY not in json.dumps(rows)
        assert rows[0]["key"] != FAKE_KEY


class TestLimits:
    def test_limits_manual_provider_prints_catalog_url(self, reg_path, capsys):
        assert run(reg_path, "limits", "codex") == 0
        out = capsys.readouterr().out
        assert "https://platform.openai.com/usage" in out

    def test_limits_unknown_registered_provider_api_kind_without_key(self, reg_path):
        # openrouter is api-kind; without a stored key it must fail cleanly
        assert run(reg_path, "limits", "openrouter") == 1

    def test_limits_api_provider_live_fetch(self, reg_path, monkeypatch, capsys):
        add_zai(reg_path)  # ensure registry non-empty
        run(reg_path, "add", "openrouter", "--key", "sk-or-fake-123456")

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(
                    {"data": {"total_credits": 25.0, "total_usage": 5.25}}
                ).encode()

        seen = {}

        def fake_open(req, timeout=10.0):
            seen["url"] = req.full_url
            seen["header"] = req.headers
            return FakeResponse()

        monkeypatch.setattr("mar.limits._open", fake_open)
        assert run(reg_path, "limits", "openrouter") == 0
        out = capsys.readouterr().out
        assert seen["url"] == "https://openrouter.ai/api/v1/credits"
        assert seen["header"]["Authorization"] == "Bearer sk-or-fake-123456"
        assert "5.25 / 25.0 USD" in out

    def test_limits_no_active_subscriptions(self, reg_path):
        assert run(reg_path, "limits") == 1

    def test_limits_gemini_prints_liveness_note(self, reg_path, monkeypatch, capsys):
        run(reg_path, "add", "gemini", "--key", "AQ.gemini-fake-key-1234567890")
        capsys.readouterr()

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(
                    {"models": [{"name": "models/gemini-3-flash"}]}
                ).encode()

        monkeypatch.setattr(
            "mar.limits._open", lambda request, timeout=10.0: FakeResponse()
        )
        assert run(reg_path, "limits", "gemini") == 0
        out = capsys.readouterr().out
        assert "gemini (api):" in out
        assert "key alive: 1 models visible (1 gemini)" in out


class TestOmpSync:
    def test_omp_sync_writes_db_rows_and_env_vars(self, reg_path, tmp_path, capsys):
        agent_dir = tmp_path / "agent"
        run(reg_path, "add", "zai", "--key", FAKE_KEY)
        run(reg_path, "add", "cursor-grok", "--key", "crsr_fake_1234567890")
        code = run(reg_path, "omp-sync", "--agent-dir", str(agent_dir))
        assert code == 0
        out = capsys.readouterr().out
        assert "written: zai" in out
        assert "written: cursor" in out
        with sqlite3.connect(agent_dir / "agent.db") as conn:
            rows = conn.execute(
                "SELECT provider, credential_type, data FROM auth_credentials"
            ).fetchall()
        assert rows == [("zai", "api_key", '{"key":"' + FAKE_KEY + '"}')]
        env = (agent_dir / ".env").read_text(encoding="utf-8")
        assert env == "CURSOR_ACCESS_TOKEN=crsr_fake_1234567890\n"

    def test_omp_sync_sets_model_role(self, reg_path, tmp_path, capsys):
        agent_dir = tmp_path / "agent"
        run(reg_path, "add", "zai", "--key", FAKE_KEY)
        code = run(
            reg_path,
            "omp-sync",
            "--agent-dir", str(agent_dir),
            "--set-role", "default=zai/glm-5.3-flash",
        )
        assert code == 0
        import yaml

        config = yaml.safe_load((agent_dir / "config.yml").read_text(encoding="utf-8"))
        assert config["modelRoles"]["default"] == "zai/glm-5.3-flash"

    def test_omp_sync_bad_role_spec_is_usage_error(self, reg_path):
        run(reg_path, "add", "zai", "--key", FAKE_KEY)
        assert run(reg_path, "omp-sync", "--set-role", "defaultzai") == 2


class TestPrompt:
    def test_prompt_lists_providers_never_keys(self, reg_path, capsys):
        run(reg_path, "add", "zai", "--key", FAKE_KEY, "--default", "glm-5.3-flash")
        assert run(reg_path, "prompt") == 0
        out = capsys.readouterr().out
        assert "zai" in out
        assert "glm-5.3-flash" in out
        assert "mar export" in out
        assert FAKE_KEY not in out

    def test_prompt_empty_registry_is_error(self, reg_path):
        assert run(reg_path, "prompt") == 1

    def test_prompt_custom_file_appended(self,reg_path, tmp_path, capsys):
        custom = tmp_path / "custom.md"
        custom.write_text("install vim bindings", encoding="utf-8")
        run(reg_path, "add", "zai", "--key", FAKE_KEY)
        assert run(reg_path, "prompt", "--custom", str(custom)) == 0
        assert "install vim bindings" in capsys.readouterr().out

    def test_prompt_output_writes_file(self, reg_path, tmp_path):
        run(reg_path, "add", "zai", "--key", FAKE_KEY)
        out_path = tmp_path / "prompt.md"
        assert run(reg_path, "prompt", "--output", str(out_path)) == 0
        assert "zai" in out_path.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_agent_dir(tmp_path, monkeypatch):
    """Keep export/pull from reading the real machine's omp config."""
    monkeypatch.setattr(cli.omp, "default_agent_dir", lambda: tmp_path / "iso-agent")


class TestExport:
    def test_export_powershell_contains_all_keys(self, reg_path, capsys):
        run(reg_path, "add", "zai", "--key", "zai-key-1234567890")
        run(reg_path, "add", "cursor-grok", "--key", "cursor-key-1234567890")
        capsys.readouterr()
        assert run(reg_path, "export") == 0
        captured = capsys.readouterr()
        out = captured.out
        assert out.startswith(cli._EXPORT_BEGIN + "\n")
        assert cli._EXPORT_END in out
        assert "#" not in out.replace(cli._EXPORT_BEGIN, "").replace(cli._EXPORT_END, "")
        assert "ZAI_API_KEY=zai-key-1234567890" in out
        assert "CURSOR_ACCESS_TOKEN=cursor-key-1234567890" in out
        assert "@'" in out and "Set-Content" in out
        assert "$env:USERPROFILE\\.omp\\agent\\.env" in out
        assert "omp usage" in captured.err

    def test_export_bash_heredoc(self, reg_path, capsys):
        run(reg_path, "add", "openrouter", "--key", "or-key-123456789012")
        capsys.readouterr()
        assert run(reg_path, "export", "--shell", "bash") == 0
        out = capsys.readouterr().out
        assert "<<'MAR_EOF'" in out
        assert "OPENROUTER_API_KEY=or-key-123456789012" in out

    def test_export_skips_oauth_and_unknown_and_envless(self, reg_path, capsys):
        run(reg_path, "add", "zai", "--key", "zai-key-1234567890")
        # hand-edit registry to hold an oauth sub and an unknown provider
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        data["subscriptions"].append(
            {
                "provider": "claude",
                "auth_method": "oauth",
                "key": None,
                "models": [],
                "default_model": None,
                "added_at": "2026-10-10T00:00:00+00:00",
                "status": "active",
                "notes": "",
            }
        )
        data["subscriptions"].append(
            {
                "provider": "ghost-labs",
                "auth_method": "api_key",
                "key": "ghost-key-1234567890",
                "models": [],
                "default_model": None,
                "added_at": "2026-10-10T00:00:00+00:00",
                "status": "active",
                "notes": "",
            }
        )
        reg_path.write_text(json.dumps(data), encoding="utf-8")
        capsys.readouterr()
        assert run(reg_path, "export") == 0
        captured = capsys.readouterr()
        out = captured.out
        err = captured.err
        assert "ANTHROPIC_API_KEY" not in out
        assert "ghost-key" not in out
        assert "oauth token missing; run mar pull" in err
        assert "unknown provider" in err
        assert "skipped" in err

    def test_export_antigravity_envless_note(self, reg_path, capsys):
        run(reg_path, "add", "zai", "--key", "zai-key-1234567890")
        run(reg_path, "add", "antigravity", "--key", "ag-key-123456789012")
        capsys.readouterr()
        assert run(reg_path, "export") == 0
        captured = capsys.readouterr()
        assert "no env route" in captured.err
        assert "/login google-antigravity" in captured.err
        assert "ag-key-123456789012" not in captured.out  # unexportable key never prints

    def test_export_empty_registry_is_error(self, reg_path):
        assert run(reg_path, "export") == 1

    def test_export_sets_model_roles_from_agent_config(self, reg_path, tmp_path, capsys):
        agent_dir = tmp_path / "iso-agent"
        agent_dir.mkdir()
        (agent_dir / "config.yml").write_text(
            "modelRoles:\n  judge: typesafe/jev-latest\n  default: zai/glm-5.3-flash:max\n",
            encoding="utf-8",
        )
        run(reg_path, "add", "zai", "--key", "zai-key-1234567890")
        capsys.readouterr()
        assert run(reg_path, "export") == 0
        captured = capsys.readouterr()
        out = captured.out
        assert "modelRoles:" in out
        assert "  judge: typesafe/jev-latest" in out
        assert "  default: zai/glm-5.3-flash:max" in out
        assert "Select-String" in out  # idempotent guard for existing configs
        assert "role-set for judge, default" in captured.err
        # paste-safety: interactive PowerShell runs line-by-line; no statement
        # may continue onto a following line (a bare `else` breaks paste).
        assert re.search(r"(?m)^\s*else\b", out) is None
        for line in out.splitlines():
            if line.startswith("if ("):
                assert line.rstrip().endswith("}"), f"dangling if-block line: {line!r}"

    def test_export_roles_bash_variant(self, reg_path, tmp_path, capsys):
        agent_dir = tmp_path / "iso-agent"
        agent_dir.mkdir()
        (agent_dir / "config.yml").write_text(
            "modelRoles:\n  judge: typesafe/jev-latest\n", encoding="utf-8"
        )
        run(reg_path, "add", "zai", "--key", "zai-key-1234567890")
        capsys.readouterr()
        assert run(reg_path, "export", "--shell", "bash") == 0
        out = capsys.readouterr().out
        assert "grep -q '^modelRoles:'" in out
        assert "judge: typesafe/jev-latest" in out

    def test_export_without_roles_has_no_role_block(self, reg_path, capsys):
        run(reg_path, "add", "zai", "--key", "zai-key-1234567890")
        capsys.readouterr()
        assert run(reg_path, "export") == 0
        out = capsys.readouterr().out
        assert "modelRoles" not in out
        assert "role-set" not in capsys.readouterr().err

    def test_export_output_writes_file(self, reg_path, tmp_path):
        run(reg_path, "add", "zai", "--key", "zai-key-1234567890")
        out_path = tmp_path / "pastable.ps1"
        assert run(reg_path, "export", "--output", str(out_path)) == 0
        assert "ZAI_API_KEY=zai-key-1234567890" in out_path.read_text(encoding="utf-8")


class TestPull:
    @pytest.fixture()
    def agent_db(self, tmp_path):
        """omp agent dir with the live schema, one oauth + one api_key row."""
        import sqlite3

        from mar.omp import upsert_credential

        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        conn = sqlite3.connect(agent_dir / "agent.db")
        conn.execute(
            "CREATE TABLE auth_credentials ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, "
            "credential_type TEXT NOT NULL, data TEXT NOT NULL, "
            "disabled_cause TEXT DEFAULT NULL, identity_key TEXT DEFAULT NULL, "
            "created_at INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO auth_credentials (provider, credential_type, data) VALUES (?, ?, ?)",
            (
                "anthropic",
                "oauth",
                json.dumps(
                    {
                        "access": "anthropic-access-token-123",
                        "refresh": "anthropic-refresh-token-123",
                        "expires": 1791660887205,
                        "authorizedAt": 1791632387205,
                        "email": "user@example.com",
                        "accountId": "acct-1",
                        "orgId": "org-1",
                        "orgName": "example org",
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO auth_credentials (provider, credential_type, data) VALUES (?, ?, ?)",
            ("mystery-provider", "oauth", json.dumps({"access": "mystery-token-12345"})),
        )
        conn.commit()
        conn.close()
        upsert_credential(agent_dir, "zai", "zai-db-key-123456789")
        return agent_dir

    def test_pull_adopts_oauth_rows_into_registry(self, reg_path, agent_db, capsys):
        assert run(reg_path, "pull", "--agent-dir", str(agent_db)) == 0
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        subs = {s["provider"]: s for s in data["subscriptions"]}
        assert subs["claude"]["auth_method"] == "oauth"
        assert subs["claude"]["key"] == "anthropic-access-token-123"
        assert "expires 2026-10-10T19:34Z" in subs["claude"]["notes"]
        assert "user@example.com" in subs["claude"]["notes"]
        out = capsys.readouterr().out
        assert "pulled: claude (anthropic)" in out

    def test_pull_skips_unknown_omp_provider_and_api_rows(self, reg_path, agent_db, capsys):
        assert run(reg_path, "pull", "--agent-dir", str(agent_db)) == 0
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        subs = {s["provider"]: s for s in data["subscriptions"]}
        assert "zai" not in subs  # api_key rows are not adopted
        err = capsys.readouterr().err
        assert "mystery-provider" in err

    def test_pull_replaces_instead_of_duplicating(self, reg_path, agent_db):
        run(reg_path, "pull", "--agent-dir", str(agent_db))
        assert run(reg_path, "pull", "--agent-dir", str(agent_db)) == 0
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        assert [s["provider"] for s in data["subscriptions"]].count("claude") == 1

    def test_pull_preserves_models_on_existing_sub(self, reg_path, agent_db):
        run(reg_path, "add", "claude", "--key", "claude-key-12345678901", "--default", "claude-haiku")
        assert run(reg_path, "pull", "--agent-dir", str(agent_db)) == 0
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        sub = next(s for s in data["subscriptions"] if s["provider"] == "claude")
        assert sub["default_model"] == "claude-haiku"
        assert sub["auth_method"] == "oauth"

    def test_pull_with_no_oauth_rows_is_error(self, reg_path, tmp_path):
        empty = tmp_path / "empty-agent"
        empty.mkdir()
        assert run(reg_path, "pull", "--agent-dir", str(empty)) == 1


class TestExportOauth:
    def test_export_includes_oauth_tokens_with_warning(self, reg_path, capsys):
        run(reg_path, "add", "zai", "--key", "zai-key-1234567890")
        # hand-edit registry to hold an oauth claude sub (as mar pull writes it)
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        data["subscriptions"].append(
            {
                "provider": "claude",
                "auth_method": "oauth",
                "key": "anthropic-access-token-123",
                "models": [],
                "default_model": None,
                "added_at": "2026-10-10T00:00:00+00:00",
                "status": "active",
                "notes": "oauth via mar pull",
            }
        )
        reg_path.write_text(json.dumps(data), encoding="utf-8")
        capsys.readouterr()
        assert run(reg_path, "export") == 0
        captured = capsys.readouterr()
        out = captured.out
        assert "ANTHROPIC_OAUTH_TOKEN=anthropic-access-token-123" in out
        assert "ZAI_API_KEY=zai-key-1234567890" in out
        assert "they EXPIRE" in captured.err

    def test_export_oauth_without_route_notes_login(self, reg_path, capsys):
        run(reg_path, "add", "zai", "--key", "zai-key-1234567890")
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        data["subscriptions"].append(
            {
                "provider": "cursor-grok",
                "auth_method": "oauth",
                "key": "cursor-access-token-123456",
                "models": [],
                "default_model": None,
                "added_at": "2026-10-10T00:00:00+00:00",
                "status": "active",
                "notes": "",
            }
        )
        reg_path.write_text(json.dumps(data), encoding="utf-8")
        capsys.readouterr()
        assert run(reg_path, "export") == 0
        captured = capsys.readouterr()
        assert "no oauth env route" in captured.err
        assert "/login cursor" in captured.err
        assert "cursor-access-token-123456" not in captured.out  # unexportable key never prints


class TestTopLevel:
    def test_version_flag(self, capsys):
        assert cli.main(["--version"]) == 0
        assert "mar " in capsys.readouterr().out

    def test_no_subcommand_is_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            cli.main([])
        assert exc.value.code == 2

    def test_unknown_subcommand_is_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            cli.main(["frobnicate"])
        assert exc.value.code == 2
