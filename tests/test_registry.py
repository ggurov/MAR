"""Tests for mar.registry (slice M1): CRUD, validation, atomic JSON persistence."""

from __future__ import annotations

import json

import pytest

from mar.registry import Registry, RegistryError, Subscription, mask

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
        '{"version": 2, "subscriptions": []}',  # unknown version
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
    )
    reg.add(first)
    reg.add(second)
    reg.save()
    loaded = Registry.load(path)
    assert loaded.all() == [second, first]  # sorted by provider
    assert loaded.get("zai") == first
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["subscriptions"]) == 2


def test_file_schema_matches_contract(tmp_path):
    """On-disk JSON is {"version": 1, "subscriptions": [...]} with fields as-is."""
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
    }
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
    assert data["version"] == 1
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
