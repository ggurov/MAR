"""Tests for the static provider catalog (M2).

The catalog must mirror docs/providers.md exactly: 9 providers, fixed order,
verification flags confirmed against omp's provider roster on 2026-10-10.
Validation rules are exercised by tampering with copies of catalog entries
(the shipped CATALOG is frozen).
"""

import dataclasses

import pytest

from mar.providers import CATALOG, Provider, get, ids, unverified, validate

EXPECTED_IDS = (
    "cursor-grok",
    "codex",
    "claude",
    "cline",
    "opencode",
    "zai",
    "openrouter",
    "antigravity",
    "copilot",
    "jev",
    "gemini",
)

UNVERIFIED_IDS: set[str] = set()

VERIFIED_IDS = set(EXPECTED_IDS)

OMP_IDS = {
    "cursor-grok": "cursor",
    "codex": "openai-codex",
    "claude": "anthropic",
    "cline": "cline-pass",
    "opencode": "opencode-go",
    "zai": "zai",
    "openrouter": "openrouter",
    "antigravity": "google-antigravity",
    "copilot": "github-copilot",
}

ENV_VARS = {
    "cursor-grok": "CURSOR_ACCESS_TOKEN",
    "codex": "OPENAI_CODEX_OAUTH_TOKEN",
    "claude": "ANTHROPIC_API_KEY",
    "cline": "",
    "opencode": "",
    "zai": "",
    "openrouter": "",
    "antigravity": "",
    "copilot": "COPILOT_GITHUB_TOKEN",
    "jev": "",
    "gemini": "",
}

OAUTH_ENV_VARS = {
    "cursor-grok": "",
    "codex": "OPENAI_CODEX_OAUTH_TOKEN",
    "claude": "ANTHROPIC_OAUTH_TOKEN",
    "cline": "",
    "opencode": "",
    "zai": "",
    "openrouter": "",
    "antigravity": "",
    "copilot": "",
    "jev": "",
    "gemini": "",
}

EXPORT_KEY_ENV_VARS = {
    "cursor-grok": "CURSOR_ACCESS_TOKEN",
    "codex": "",
    "claude": "ANTHROPIC_API_KEY",
    "cline": "CLINE_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "zai": "ZAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "antigravity": "",
    "copilot": "COPILOT_GITHUB_TOKEN",
    "jev": "TYPESAFE_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

URL_FIELDS = ("key_url", "limits_url", "signup_url")


def test_ids_returns_exact_catalog_in_order():
    assert ids() == EXPECTED_IDS
    assert tuple(p.id for p in CATALOG) == EXPECTED_IDS
    assert len(CATALOG) == 11


def test_get_returns_matching_provider():
    zai = get("zai")
    assert isinstance(zai, Provider)
    assert zai.id == "zai"
    assert zai.verified is True
    assert zai.omp_provider_id == "zai"


def test_get_unknown_id_raises_key_error_listing_valid_ids():
    with pytest.raises(KeyError) as excinfo:
        get("does-not-exist")
    message = str(excinfo.value)
    assert "does-not-exist" in message
    for known in EXPECTED_IDS:
        assert known in message


@pytest.mark.parametrize("provider", CATALOG, ids=lambda p: p.id)
def test_every_url_field_is_https(provider):
    for field in URL_FIELDS:
        url = getattr(provider, field)
        assert url.startswith("https://"), f"{provider.id}.{field} = {url!r}"


def test_validate_shipped_catalog_is_healthy():
    assert validate() == []


def test_unverified_is_empty_every_entry_confirmed():
    assert unverified() == ()
    assert all(p.verified for p in CATALOG)


def test_verified_are_all_eleven_roster_confirmed_providers():
    assert {p.id for p in CATALOG if p.verified} == VERIFIED_IDS


@pytest.mark.parametrize(("provider_id", "omp_id"), sorted(OMP_IDS.items()))
def test_omp_ids_match_official_roster(provider_id, omp_id):
    assert get(provider_id).omp_provider_id == omp_id


@pytest.mark.parametrize(("provider_id", "env_var"), sorted(ENV_VARS.items()))
def test_omp_env_vars_match_official_roster(provider_id, env_var):
    assert get(provider_id).omp_env_var == env_var


@pytest.mark.parametrize(("provider_id", "env_var"), sorted(OAUTH_ENV_VARS.items()))
def test_export_oauth_env_vars_match_official_roster(provider_id, env_var):
    assert get(provider_id).export_oauth_env_var == env_var


@pytest.mark.parametrize(("provider_id", "env_var"), sorted(EXPORT_KEY_ENV_VARS.items()))
def test_export_key_env_vars_match_official_roster(provider_id, env_var):
    assert get(provider_id).export_env_var == env_var


def test_limits_kind_api_applies_only_to_openrouter():
    api_providers = [p for p in CATALOG if p.limits_kind == "api"]
    assert [p.id for p in api_providers] == ["openrouter"]
    assert all(p.limits_kind == "manual" for p in CATALOG if p.id != "openrouter")


@pytest.mark.parametrize("provider", CATALOG, ids=lambda p: p.id)
def test_v1_defaults(provider):
    assert provider.auth_methods == ("api_key",)
    assert provider.default_model is None


BASE = CATALOG[0]

TAMPER_CASES = [
    pytest.param(
        dataclasses.replace(BASE, id="codex"),
        "duplicate provider id 'codex'",
        id="duplicate-id",
    ),
    pytest.param(
        dataclasses.replace(BASE, id="Bad_Id"),
        "id 'Bad_Id' does not match",
        id="bad-id-shape",
    ),
    pytest.param(
        dataclasses.replace(BASE, key_url="http://insecure.example"),
        "key_url must start with https://",
        id="http-key-url",
    ),
    pytest.param(
        dataclasses.replace(BASE, limits_kind="psychic"),
        "limits_kind 'psychic' not in",
        id="bad-limits-kind",
    ),
    pytest.param(
        dataclasses.replace(BASE, verified=True, omp_provider_id=""),
        "verified=True but omp_provider_id is empty",
        id="verified-empty-omp-id",
    ),
    pytest.param(
        dataclasses.replace(BASE, verified=True, limits_url=""),
        "verified=True but limits_url is empty",
        id="verified-empty-limits-url",
    ),
    pytest.param(
        dataclasses.replace(BASE, omp_provider_id="Zai Main"),
        "omp_provider_id 'Zai Main' does not match",
        id="bad-omp-id-shape",
    ),
    pytest.param(
        dataclasses.replace(BASE, omp_env_var="not an env var"),
        "omp_env_var 'not an env var' does not match",
        id="bad-env-var-shape",
    ),
]


@pytest.mark.parametrize(("broken", "expected"), TAMPER_CASES)
def test_validate_catches_each_tampered_rule(broken, expected):
    problems = validate(CATALOG + (broken,))
    assert any(expected in problem for problem in problems), problems


def test_validate_empty_catalog_is_healthy():
    assert validate(()) == []


def test_catalog_entries_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        CATALOG[0].id = "mutated"
