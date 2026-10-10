"""Tests for bootstrap: startup-prompt rendering and one-shot omp sync.

All state lives under tmp_path (explicit registry.json + agent_dir); the real
~/.mar, ~/.omp, and the network are never touched. Keys are fake fixtures.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from mar import omp
from mar.bootstrap import BootstrapError, SyncReport, render_prompt, sync
from mar.omp import OmpError
from mar.registry import Registry, Subscription

FAKE_KEY = "test-key-zai-0011223344"
FAKE_KEY_2 = "test-key-cline-9988776655"
ADDED_AT = "2026-10-10T00:00:00+00:00"


def _sub(
    provider: str,
    *,
    auth_method: str = "api_key",
    key: str | None = FAKE_KEY,
    model: str | None = None,
    models: list[str] | None = None,
    status: str = "active",
) -> Subscription:
    """Build a Subscription with registry-valid defaults for one test case."""
    if models is None:
        models = [model] if model else ["test-model"]
    return Subscription(
        provider=provider,
        auth_method=auth_method,
        key=key,
        models=models,
        default_model=model,
        added_at=ADDED_AT,
        status=status,
    )


def _registry(tmp_path: Path, subs: list[Subscription]) -> Registry:
    """Build a registry at an explicit tmp path and persist it."""
    reg = Registry(tmp_path / "registry.json")
    for sub in subs:
        reg.add(sub)
    reg.save()
    return reg


# ---------------------------------------------------------------- render_prompt

PROMPT_LINE_CASES = [
    pytest.param("zai", "Z.ai", "glm-5.3-flash", id="with-default-model"),
    pytest.param("opencode", "OpenCode", None, id="without-default-model"),
]


@pytest.mark.parametrize(("provider_id", "display", "model"), PROMPT_LINE_CASES)
def test_prompt_lists_active_subscription_line(
    tmp_path: Path, provider_id: str, display: str, model: str | None
) -> None:
    reg = _registry(tmp_path, [_sub(provider_id, model=model)])
    prompt = render_prompt(reg)
    expected = f"- {provider_id} ({display}) — default model: {model or 'none'}"
    assert expected in prompt


def test_prompt_header_is_one_line_naming_mar(tmp_path: Path) -> None:
    prompt = render_prompt(_registry(tmp_path, [_sub("zai", model="glm-5.3-flash")]))
    first_line = prompt.splitlines()[0]
    assert first_line.startswith("#")
    assert "MAR" in first_line


def test_prompt_lists_only_active_subscriptions(tmp_path: Path) -> None:
    reg = _registry(
        tmp_path,
        [
            _sub("zai", model="glm-5.3-flash"),
            _sub("claude", status="expired"),
            _sub("codex", status="disabled"),
        ],
    )
    prompt = render_prompt(reg)
    assert "- zai (Z.ai)" in prompt
    assert "- claude" not in prompt
    assert "- codex" not in prompt


def test_prompt_instructs_omp_sync_and_never_leaks_keys(tmp_path: Path) -> None:
    claude_key = "sk-ant-fake-1122334455"
    reg = _registry(
        tmp_path,
        [_sub("zai", model="glm-5.3-flash"), _sub("claude", key=claude_key)],
    )
    prompt = render_prompt(reg)
    assert "mar omp-sync" in prompt
    assert FAKE_KEY not in prompt
    assert claude_key not in prompt


@pytest.mark.parametrize(
    "subs",
    [
        pytest.param([], id="no-subscriptions"),
        pytest.param([_sub("zai", status="disabled")], id="all-inactive"),
    ],
)
def test_prompt_empty_registry_raises(tmp_path: Path, subs: list[Subscription]) -> None:
    with pytest.raises(BootstrapError, match="^registry is empty"):
        render_prompt(_registry(tmp_path, subs))


def test_prompt_custom_file_appended_under_its_own_heading(tmp_path: Path) -> None:
    custom = tmp_path / "prompt.md"
    custom.write_text("Use plan mode for big refactors.\n", encoding="utf-8")
    prompt = render_prompt(_registry(tmp_path, [_sub("zai")]), custom_path=custom)
    assert "## Custom setup" in prompt
    assert prompt.index("## Custom setup") < prompt.index("Use plan mode for big refactors.")


def test_prompt_missing_custom_file_raises(tmp_path: Path) -> None:
    with pytest.raises(BootstrapError, match="custom"):
        render_prompt(_registry(tmp_path, [_sub("zai")]), custom_path=tmp_path / "nope.md")


def test_prompt_unknown_provider_still_renders(tmp_path: Path) -> None:
    prompt = render_prompt(_registry(tmp_path, [_sub("ghost-labs")]))
    assert "- ghost-labs (unverified provider) — default model: none" in prompt


# ------------------------------------------------------------------------ sync


def test_sync_writes_zai_key_into_tmp_agent_db(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    reg = _registry(tmp_path, [_sub("zai", model="glm-5.3-flash")])

    report = sync(reg, agent_dir=agent_dir)

    assert report == SyncReport(written=("zai",), skipped=())
    conn = sqlite3.connect(agent_dir / "agent.db")
    try:
        rows = conn.execute(
            "SELECT provider, credential_type, data FROM auth_credentials"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("zai", "api_key", json.dumps({"key": FAKE_KEY}, separators=(",", ":")))]


def test_sync_report_is_frozen() -> None:
    report = SyncReport(written=(), skipped=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.written = ("zai",)  # type: ignore[misc]


SKIP_CASES = [
    pytest.param("ghost-labs", "api_key", "unknown provider", id="unknown-provider"),
    pytest.param("cursor-grok", "api_key", "omp provider id unverified", id="unverified-omp-id"),
    pytest.param("zai", "oauth", "no api key", id="oauth-has-no-key"),
]


@pytest.mark.parametrize(("provider_id", "auth_method", "reason"), SKIP_CASES)
def test_sync_skips_one_provider(
    tmp_path: Path, provider_id: str, auth_method: str, reason: str
) -> None:
    agent_dir = tmp_path / "agent"
    key = None if auth_method == "oauth" else FAKE_KEY
    reg = _registry(tmp_path, [_sub(provider_id, auth_method=auth_method, key=key)])

    report = sync(reg, agent_dir=agent_dir)

    assert report.skipped == ((provider_id, reason),)
    assert report.written == ()
    assert not (agent_dir / "agent.db").exists()


def test_sync_skips_api_key_sub_with_empty_key(tmp_path: Path) -> None:
    # Registry.add() forbids an empty api_key, but a hand-edited registry.json
    # can still carry one (load() does not revalidate) — sync must skip it.
    payload = {
        "version": 1,
        "subscriptions": [
            {
                "provider": "zai",
                "auth_method": "api_key",
                "key": "",
                "models": ["glm-5.3-flash"],
                "default_model": None,
                "added_at": ADDED_AT,
                "status": "active",
                "notes": "",
            }
        ],
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    agent_dir = tmp_path / "agent"

    report = sync(Registry.load(path), agent_dir=agent_dir)

    assert report.skipped == (("zai", "no api key"),)
    assert report.written == ()


def test_sync_applies_model_roles_to_config_yml(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    roles = {"default": "zai/glm-5.3-flash:max", "judge": "zai/glm-4.7"}
    reg = _registry(tmp_path, [_sub("zai", model="glm-5.3-flash")])

    report = sync(reg, agent_dir=agent_dir, roles=roles)

    assert report.written == ("zai",)
    config = yaml.safe_load((agent_dir / "config.yml").read_text(encoding="utf-8"))
    assert config["modelRoles"] == roles


def test_sync_omp_error_skips_provider_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = tmp_path / "agent"
    real_upsert = omp.upsert_credential

    def flaky_upsert(agent_dir: Path, provider: str, key: str) -> None:
        if provider == "zai":
            raise OmpError("agent.db is locked")
        real_upsert(agent_dir, provider, key)

    monkeypatch.setattr(omp, "upsert_credential", flaky_upsert)
    reg = _registry(tmp_path, [_sub("zai", model="glm-5.3-flash"), _sub("cline", key=FAKE_KEY_2)])

    report = sync(reg, agent_dir=agent_dir)

    assert report.skipped == (("zai", "agent.db is locked"),)
    assert report.written == ("cline-pass",)  # omp id, not the registry id
    conn = sqlite3.connect(agent_dir / "agent.db")
    try:
        providers_stored = [
            row[0]
            for row in conn.execute("SELECT provider FROM auth_credentials").fetchall()
        ]
    finally:
        conn.close()
    assert providers_stored == ["cline-pass"]


def test_sync_uses_default_agent_dir_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv("OMP_AGENT_DIR", str(agent_dir))
    reg = _registry(tmp_path, [_sub("zai", model="glm-5.3-flash")])

    report = sync(reg)

    assert report.written == ("zai",)
    assert (agent_dir / "agent.db").exists()
