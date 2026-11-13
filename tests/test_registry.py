"""Tests for mar.registry (slice M1): CRUD, validation, atomic JSON persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mar.registry import Registry, RegistryError, Subscription, default_path, mask

FAKE_KEY = "test-key-1234567890"  # 19 chars, obviously fake
ADDED_AT = "2026-10-10T12:00:00+00:00"


def sub(provider: str = "zai", **overrides) -> Subscription:
    """Build a valid Subscription with fake values, overridable per test."""
    fields: dict = {
        "provider": provider,
        "auth_method": "api_key",
        "key": FAKE_KEY,
        "models": ["glm-4.7", "glm-5.3-flash"],
        "default_model": "glm-5.3-flash",
        "added_at": ADDED_AT,
    }
    fields.update(overrides)
    return Subscription(**fields)


# --- default_path() ---------------------------------------------------------


def test_default_path_honors_mar_home(monkeypatch, tmp_path):
    monkeypatch.setenv("MAR_HOME", str(tmp_path))
    assert default_path() == tmp_path / "registry.json"


def test_default_path_falls_back_to_dot_mar(monkeypatch):
    monkeypatch.delenv("MAR_HOME", raising=False)
    assert default_path() == Path.home() / ".mar" / "registry.json"


# --- mask() -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("abcdefghijklmnop", "abcd...mnop"),  # len 16 >= 12
        (FAKE_KEY, "test...7890"),  # len 19 >= 12
        ("abcdefghijkl", "abcd...ijkl"),  # len 12 boundary
        ("12345678901", "<redacted>"),  # len 11 boundary, below 12
        ("s", "<redacted>"),  # len 1 boundary
        ("", ""),  # empty string
        (None, ""),  # None
    ],
)
def test_mask_table(key, expected):
    """mask() shows first4+last4 for long keys, redacts short ones, empty for None."""
    assert mask(key) == expected


# --- add() validation -------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": ""},
        {"provider": "   "},
        {"provider": 7},
        {"auth_method": "token"},
        {"auth_method": None},
        {"status": "gone"},
        {"auth_method": "api_key", "key": None},
        {"auth_method": "api_key", "key": ""},
        {"default_model": "glm-9"},  # not in models
    ],
)
def test_add_rejects_invalid_subscription(tmp_path, overrides):
    """add() raises RegistryError for any violated field rule and stores nothing."""
    reg = Registry(tmp_path / "registry.json")
    with pytest.raises(RegistryError):
        reg.add(sub(**overrides))
    assert reg.all() == []


def test_add_allows_oauth_without_key_and_default_model_none(tmp_path):
    """oauth needs no key; default_model may be None even with models present."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub(auth_method="oauth", key=None, default_model=None))
    assert reg.get("zai").auth_method == "oauth"


def test_add_allows_keyless_none_auth(tmp_path):
    """auth_method "none" stores a keyless subscription (self-hosted endpoints)."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub(provider="strata", auth_method="none", key=None, default_model=None))
    stored = reg.get("strata")
    assert stored.auth_method == "none"
    assert stored.key is None


def test_add_rejects_key_on_keyless_subscription(tmp_path):
    """A keyless provider must not carry key material into the registry."""
    reg = Registry(tmp_path / "registry.json")
    with pytest.raises(RegistryError, match="keyless"):
        reg.add(sub(provider="strata", auth_method="none", key=FAKE_KEY))
    assert reg.all() == []


def test_add_duplicate_provider(tmp_path):
    """add() refuses a second subscription for the same provider."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub())
    with pytest.raises(RegistryError, match="zai"):
        reg.add(sub())
    assert len(reg.all()) == 1


# --- load() -----------------------------------------------------------------


def test_load_missing_file_returns_empty_registry(tmp_path):
    """load() on a nonexistent file yields an empty, usable registry."""
    reg = Registry.load(tmp_path / "nope.json")
    assert reg.all() == []
    assert reg.active() == []


@pytest.mark.parametrize(
    "content",
    [
        "not json {",
        "",
        "{}",  # missing version
        "[]",  # wrong shape, no version
        '{"subscriptions": []}',  # missing version
        '{"version": 99, "subscriptions": []}',  # unknown version
        '{"version": 1, "subscriptions": ["oops"]}',  # non-object entry
    ],
)
def test_load_corrupt_file_raises_registry_error(tmp_path, content):
    """load() raises RegistryError for malformed JSON, missing/unknown version."""
    path = tmp_path / "registry.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RegistryError):
        Registry.load(path)


# --- roundtrip persistence --------------------------------------------------


def test_roundtrip_save_load(tmp_path):
    """save() then load() preserves every subscription field exactly."""
    path = tmp_path / "registry.json"
    reg = Registry(path)
    first = sub()
    second = sub(
        "openrouter",
        auth_method="oauth",
        key=None,
        models=["or/free"],
        default_model=None,
        status="disabled",
        notes="checked 2026-10-01",
        expires_at_ms=1791660887205,
    )
    reg.add(first)
    reg.add(second)
    reg.save()
    loaded = Registry.load(path)
    assert loaded.all() == [second, first]  # sorted by provider
    assert loaded.get("zai") == first
    assert loaded.get("openrouter").expires_at_ms == 1791660887205
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert len(data["subscriptions"]) == 2


def test_load_legacy_file_without_expires_at_ms(tmp_path):
    """Subscriptions from files written before expires_at_ms load with 0."""
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "subscriptions": [
                    {
                        "provider": "zai",
                        "auth_method": "api_key",
                        "key": "zai-key-1234567890",
                        "models": [],
                        "default_model": None,
                        "added_at": "2026-10-10T00:00:00+00:00",
                        "status": "active",
                        "notes": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    reg = Registry.load(path)
    assert reg.get("zai").expires_at_ms == 0


def test_file_schema_matches_contract(tmp_path):
    """On-disk JSON is {"version": 2, "subscriptions": [...]} with fields as-is."""
    path = tmp_path / "registry.json"
    reg = Registry(path)
    reg.add(sub())
    reg.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data["subscriptions"][0]
    assert set(entry) == {
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
    }
    assert data["version"] == 2
    assert entry["models"] == ["glm-4.7", "glm-5.3-flash"]


# --- remove / get / active / all --------------------------------------------


def test_remove_existing_and_missing(tmp_path):
    """remove() deletes a known provider; RegistryError for an unknown one."""
    path = tmp_path / "registry.json"
    reg = Registry(path)
    reg.add(sub())
    reg.remove("zai")
    assert reg.all() == []
    with pytest.raises(RegistryError, match="zai"):
        reg.remove("zai")


def test_get_missing_raises_keyerror(tmp_path):
    """get() raises KeyError naming the absent provider."""
    reg = Registry(tmp_path / "registry.json")
    with pytest.raises(KeyError, match="ghost"):
        reg.get("ghost")


def test_active_and_all_sorted_by_provider(tmp_path):
    """active() filters status=active; both accessors sort by provider."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub("zai", status="disabled"))
    reg.add(sub("openrouter"))
    reg.add(sub("anthropic"))
    assert [s.provider for s in reg.all()] == ["anthropic", "openrouter", "zai"]
    assert [s.provider for s in reg.active()] == ["anthropic", "openrouter"]


# --- atomicity smoke --------------------------------------------------------


def test_save_twice_file_stays_valid_json(tmp_path):
    """Two saves in a row leave exactly one valid file and no temp leftovers."""
    path = tmp_path / "registry.json"
    reg = Registry(path)
    reg.add(sub())
    reg.save()
    reg.add(sub("openrouter", models=["or/free"], default_model="or/free"))
    reg.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert len(data["subscriptions"]) == 2
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "registry.json"]
    assert leftovers == []


# --- path resolution --------------------------------------------------------


def test_mar_home_env_resolution(tmp_path, monkeypatch):
    """Without an explicit path, Registry uses $MAR_HOME/registry.json."""
    monkeypatch.setenv("MAR_HOME", str(tmp_path))
    reg = Registry()
    reg.add(sub())
    reg.save()
    assert (tmp_path / "registry.json").exists()


def test_explicit_path_beats_env(tmp_path, monkeypatch):
    """An explicit path argument wins over $MAR_HOME."""
    monkeypatch.setenv("MAR_HOME", str(tmp_path / "envhome"))
    explicit = tmp_path / "explicit.json"
    reg = Registry(explicit)
    reg.add(sub())
    reg.save()
    assert explicit.exists()
    assert not (tmp_path / "envhome").exists()


# --- alternate accounts (multi-key per provider) ----------------------------

from mar.registry import PRIMARY_ACCOUNT, Account, effective_key, merge_pulled_key

ALT_KEY = "test-alt-key-0123456789"


def test_effective_key_defaults_to_primary(tmp_path):
    """Without a selection, export/sync see the subscription's own key."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub())
    reg.add_account("zai", "joesacct1", ALT_KEY)
    assert reg.get("zai").selected_account == ""
    assert effective_key(reg.get("zai")) == FAKE_KEY


def test_select_account_switches_effective_key(tmp_path):
    """select_account makes another key the one credential consumers use."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub())
    reg.add_account("zai", "joesacct1", ALT_KEY)
    reg.select_account("zai", "joesacct1")
    stored = reg.get("zai")
    assert stored.selected_account == "joesacct1"
    assert effective_key(stored) == ALT_KEY
    assert stored.key == FAKE_KEY  # primary key is never destroyed


@pytest.mark.parametrize("selector", [PRIMARY_ACCOUNT, ""])
def test_select_original_restores_primary(tmp_path, selector):
    """Selecting the reserved 'original' label (or '') serves the primary key."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub())
    reg.add_account("zai", "joesacct1", ALT_KEY)
    reg.select_account("zai", "joesacct1")
    reg.select_account("zai", selector)
    assert effective_key(reg.get("zai")) == FAKE_KEY


@pytest.mark.parametrize(
    ("name", "key", "match"),
    [
        ("", ALT_KEY, "name"),
        ("   ", ALT_KEY, "name"),
        ("original", ALT_KEY, "reserved"),
        ("bad name", ALT_KEY, "name"),
        ("joesacct1", "", "key"),
        ("joesacct1", "   ", "key"),
    ],
)
def test_add_account_rejects_invalid_fields(tmp_path, name, key, match):
    """Account names are labels (no blanks/spaces); keys are non-empty strings."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub())
    with pytest.raises(RegistryError, match=match):
        reg.add_account("zai", name, key)
    assert reg.get("zai").accounts == []


def test_add_account_rejects_duplicate_name(tmp_path):
    """Two accounts on one provider cannot share a label."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub())
    reg.add_account("zai", "joesacct1", ALT_KEY)
    with pytest.raises(RegistryError, match="joesacct1"):
        reg.add_account("zai", "joesacct1", "another-key-1234567890")


def test_add_account_requires_an_api_key_subscription(tmp_path):
    """OAuth tokens are refreshed by mar pull; alternate keys are api_key only."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub(auth_method="oauth", key="oauth-token-123456"))
    with pytest.raises(RegistryError, match="api_key"):
        reg.add_account("zai", "joesacct1", ALT_KEY)


def test_add_account_unknown_provider(tmp_path):
    """Accounts attach to an existing subscription only."""
    reg = Registry(tmp_path / "registry.json")
    with pytest.raises(RegistryError, match="ghost"):
        reg.add_account("ghost", "joesacct1", ALT_KEY)


def test_select_unknown_account_is_error(tmp_path):
    """Selecting a label that was never added fails and changes nothing."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub())
    with pytest.raises(RegistryError, match="missing"):
        reg.select_account("zai", "missing")
    assert reg.get("zai").selected_account == ""


def test_remove_account_resets_selection(tmp_path):
    """Removing the selected account falls back to the primary key."""
    reg = Registry(tmp_path / "registry.json")
    reg.add(sub())
    reg.add_account("zai", "joesacct1", ALT_KEY)
    reg.add_account("zai", "joesacct2", "test-alt-key-2-0123456789")
    reg.select_account("zai", "joesacct1")
    reg.remove_account("zai", "joesacct1")
    assert effective_key(reg.get("zai")) == FAKE_KEY
    with pytest.raises(RegistryError, match="joesacct1"):
        reg.remove_account("zai", "joesacct1")


def test_accounts_round_trip_through_v2_registry(tmp_path):
    """Accounts and the selection survive save/load; the file is schema v2."""
    path = tmp_path / "registry.json"
    reg = Registry(path)
    reg.add(sub())
    reg.add_account("zai", "joesacct1", ALT_KEY)
    reg.select_account("zai", "joesacct1")
    reg.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert data["subscriptions"][0]["accounts"][0]["name"] == "joesacct1"
    assert data["subscriptions"][0]["selected_account"] == "joesacct1"

    reloaded = Registry.load(path)
    stored = reloaded.get("zai")
    assert stored.accounts == [Account(name="joesacct1", key=ALT_KEY)]
    assert effective_key(stored) == ALT_KEY


ALT2_KEY = "test-alt-key-2-0123456789"


@pytest.mark.parametrize(
    ("overrides", "pulled", "expected_key", "expected_alts"),
    [
        # No alternate accounts: the pulled credential is the primary key.
        ({}, "test-new-key-0123456789", "test-new-key-0123456789", []),
        # omp's .env carries the selected account's key (mar export writes
        # effective_key), so a pull must leave the primary key alone.
        (
            {
                "accounts": [Account(name="gurovgoat2", key=ALT_KEY)],
                "selected_account": "gurovgoat2",
            },
            ALT_KEY,
            FAKE_KEY,
            [ALT_KEY],
        ),
        # A rotated selected-account key updates that account, not the primary.
        (
            {
                "accounts": [Account(name="gurovgoat2", key=ALT_KEY)],
                "selected_account": "gurovgoat2",
            },
            "test-rotated-0123456789",
            FAKE_KEY,
            ["test-rotated-0123456789"],
        ),
        # Stale .env holding an alternate's key never overwrites the primary,
        # even when the primary is the selected one: duplicate keys collapse
        # in tokenpool routing and both accounts show the same usage.
        (
            {"accounts": [Account(name="gurovgoat2", key=ALT_KEY)]},
            ALT_KEY,
            FAKE_KEY,
            [ALT_KEY],
        ),
        # A different key with the primary selected is a genuine rotation.
        (
            {"accounts": [Account(name="gurovgoat2", key=ALT_KEY)]},
            "test-new-primary-0123456789",
            "test-new-primary-0123456789",
            [ALT_KEY],
        ),
        # A key matching another account must not be copied onto the selected
        # account either.
        (
            {
                "accounts": [
                    Account(name="gurovgoat2", key=ALT_KEY),
                    Account(name="other", key=ALT2_KEY),
                ],
                "selected_account": "gurovgoat2",
            },
            ALT2_KEY,
            FAKE_KEY,
            [ALT_KEY, ALT2_KEY],
        ),
    ],
)
def test_merge_pulled_key_never_duplicates_a_credential(
    overrides, pulled, expected_key, expected_alts
):
    """A pulled credential updates at most the slot omp actually serves."""
    stored = sub(**overrides)
    key, accounts = merge_pulled_key(stored, pulled)
    assert key == expected_key
    assert [a.key for a in accounts] == expected_alts
    assert key not in expected_alts
    assert stored.key == FAKE_KEY  # the input subscription is not mutated


@pytest.mark.parametrize("pulled", ["", "   "])
def test_merge_pulled_key_rejects_blank_key(pulled):
    """An empty pulled credential is an error, never a key wipe."""
    with pytest.raises(RegistryError, match="key"):
        merge_pulled_key(sub(), pulled)


def test_load_legacy_v1_registry_without_accounts(tmp_path):
    """A pre-accounts registry (version 1) loads as primary-key-only."""
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "subscriptions": [
                    {
                        "provider": "zai",
                        "auth_method": "api_key",
                        "key": FAKE_KEY,
                        "models": [],
                        "default_model": None,
                        "added_at": ADDED_AT,
                        "status": "active",
                        "notes": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    reg = Registry.load(path)
    stored = reg.get("zai")
    assert stored.accounts == []
    assert effective_key(stored) == FAKE_KEY


@pytest.mark.parametrize(
    "overrides",
    [
        {"accounts": [Account(name="a", key=ALT_KEY), Account(name="a", key=ALT_KEY)]},
        {"accounts": [Account(name="original", key=ALT_KEY)]},
        {"accounts": [Account(name="a", key="")]},
        {"selected_account": "ghost"},
        {"auth_method": "none", "key": None, "accounts": [Account(name="a", key=ALT_KEY)]},
    ],
)
def test_add_rejects_invalid_account_payload(tmp_path, overrides):
    """Subscription validation covers nested account invariants."""
    reg = Registry(tmp_path / "registry.json")
    with pytest.raises(RegistryError):
        reg.add(sub(**overrides))
