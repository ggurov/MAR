"""Tests for the mar CLI: exit codes, masking, error mapping, real wiring."""

from __future__ import annotations

import io
import json
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


class TestOmpSync:
    def test_omp_sync_writes_verified_and_skips_unverified(self, reg_path, tmp_path, capsys):
        agent_dir = tmp_path / "agent"
        run(reg_path, "add", "zai", "--key", FAKE_KEY)
        run(reg_path, "add", "cursor-grok", "--key", "crsr_fake_1234567890")
        code = run(reg_path, "omp-sync", "--agent-dir", str(agent_dir))
        assert code == 0
        out = capsys.readouterr().out
        assert "written: zai" in out
        assert "skipped: cursor-grok" in out and "unverified" in out
        with sqlite3.connect(agent_dir / "agent.db") as conn:
            rows = conn.execute(
                "SELECT provider, credential_type, data FROM auth_credentials"
            ).fetchall()
        assert rows == [("zai", "api_key", '{"key":"' + FAKE_KEY + '"}')]

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
