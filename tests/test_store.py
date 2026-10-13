"""Tests for mar.store: persistence abstraction, file backend, backend config."""

from __future__ import annotations

import json
import os

import pytest

from mar import store
from mar.registry import SCHEMA_VERSION, Registry, RegistryError, Subscription
from mar.store import FileStore, StoreError, resolve_store

FAKE_KEY = "test-key-1234567890"
ADDED_AT = "2026-10-10T12:00:00+00:00"


def sub(provider: str = "zai", **overrides) -> Subscription:
    fields: dict = {
        "provider": provider,
        "auth_method": "api_key",
        "key": FAKE_KEY,
        "models": ["glm-4.7"],
        "default_model": "glm-4.7",
        "added_at": ADDED_AT,
    }
    fields.update(overrides)
    return Subscription(**fields)


# --- FileStore: parity with the pre-abstraction registry file contract -------


def test_filestore_write_then_read_round_trip(tmp_path):
    path = tmp_path / "registry.json"
    s = FileStore(path)
    s.write({"version": SCHEMA_VERSION, "subscriptions": []})
    assert s.read() == {"version": SCHEMA_VERSION, "subscriptions": []}


def test_filestore_write_is_json_indent2_with_trailing_newline(tmp_path):
    path = tmp_path / "registry.json"
    FileStore(path).write({"version": 1, "subscriptions": []})
    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert json.dumps(json.loads(text), indent=2) + "\n" == text


def test_filestore_missing_file_reads_none(tmp_path):
    assert FileStore(tmp_path / "registry.json").read() is None


def test_filestore_malformed_json_raises_store_error(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError, match="cannot read registry"):
        FileStore(path).read()


def test_filestore_write_is_atomic_via_replace(tmp_path):
    # Target exists with old content; a failed write must not truncate it.
    path = tmp_path / "registry.json"
    path.write_text('{"version": 1, "subscriptions": []}', encoding="utf-8")
    s = FileStore(path)

    class BadSub:
        def __init__(self):
            self.touched = False

        def __str__(self):  # json.dump raises before replace
            raise TypeError("boom")

    with pytest.raises(TypeError):
        s.write({"version": 1, "subscriptions": [BadSub()]})
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
    assert not list(tmp_path.glob("*.tmp")), "temp file must be cleaned up"


def test_filestore_write_chmods_0600_best_effort(tmp_path):
    path = tmp_path / "registry.json"
    FileStore(path).write({"version": 1, "subscriptions": []})
    if os.name == "posix":
        assert (path.stat().st_mode & 0o777) == 0o600


def test_filestore_lazy_default_path_honors_mar_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MAR_HOME", str(tmp_path))
    s = FileStore()
    s.write({"version": 1, "subscriptions": []})
    assert (tmp_path / "registry.json").exists()


# --- backend resolution ------------------------------------------------------


def test_resolve_default_backend_is_file(monkeypatch, tmp_path):
    monkeypatch.delenv("MAR_HOME", raising=False)
    monkeypatch.delenv("MAR_BACKEND", raising=False)
    monkeypatch.setattr(store, "_config_path", lambda: tmp_path / "absent.json")
    assert isinstance(resolve_store(None, None), FileStore)


def test_resolve_explicit_path_forces_file_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("MAR_BACKEND", "vault")
    monkeypatch.setenv("VAULT_ADDR", "http://127.0.0.1:8200")
    monkeypatch.setenv("VAULT_TOKEN", "test-token-1234")
    assert isinstance(resolve_store(tmp_path / "r.json", "vault"), FileStore)


def test_resolve_flag_backend_overrides_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MAR_BACKEND", "vault")
    monkeypatch.setattr(store, "_config_path", lambda: tmp_path / "absent.json")
    assert isinstance(resolve_store(None, "file"), FileStore)


def test_resolve_env_backend_vault_uses_vault_env_vars(monkeypatch, tmp_path):
    from mar import vault

    monkeypatch.setenv("MAR_BACKEND", "vault")
    monkeypatch.setenv("VAULT_ADDR", "http://127.0.0.1:8200")
    monkeypatch.setenv("VAULT_TOKEN", "test-token-1234")
    monkeypatch.setattr(store, "_config_path", lambda: tmp_path / "absent.json")
    s = resolve_store(None, None)
    assert isinstance(s, vault.VaultStore)
    assert s.addr == "http://127.0.0.1:8200"
    assert s.mount == "secret"
    assert s.path == "mar/keys"


def test_resolve_env_backend_vault_requires_addr_and_token(monkeypatch, tmp_path):
    monkeypatch.setenv("MAR_BACKEND", "vault")
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    monkeypatch.setattr(store, "_config_path", lambda: tmp_path / "absent.json")
    with pytest.raises(StoreError, match="VAULT_ADDR"):
        resolve_store(None, None)


def test_resolve_config_file_backend_vault(monkeypatch, tmp_path):
    from mar import vault

    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "backend": "vault",
                "vault": {
                    "addr": "http://127.0.0.1:8200",
                    "mount": "kv",
                    "path": "team/mar",
                    "token_file": str(tmp_path / "tok"),
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "tok").write_text("file-token-123456\n", encoding="utf-8")
    monkeypatch.delenv("MAR_BACKEND", raising=False)
    monkeypatch.setattr(store, "_config_path", lambda: cfg)
    s = resolve_store(None, None)
    assert isinstance(s, vault.VaultStore)
    assert (s.addr, s.mount, s.path) == ("http://127.0.0.1:8200", "kv", "team/mar")
    assert s._token == "file-token-123456"  # token wiring: read from token_file


def test_resolve_unknown_backend_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_config_path", lambda: tmp_path / "absent.json")
    with pytest.raises(StoreError, match="backend"):
        resolve_store(None, "s3")


# --- config file persistence -------------------------------------------------


def test_save_and_load_config_round_trip(tmp_path):
    cfg = tmp_path / "config.json"
    store.save_config({"backend": "vault", "vault": {"addr": "http://x:8200"}}, cfg)
    assert store.load_config(cfg) == {"backend": "vault", "vault": {"addr": "http://x:8200"}}
    if os.name == "posix":
        assert (cfg.stat().st_mode & 0o777) == 0o600


def test_load_config_missing_file_is_empty(tmp_path):
    assert store.load_config(tmp_path / "absent.json") == {}


def test_load_config_rejects_non_object(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("[]", encoding="utf-8")
    with pytest.raises(StoreError, match="config"):
        store.load_config(cfg)


# --- Registry integration: file behavior is unchanged -------------------------


def test_registry_load_save_via_store_file_default(tmp_path, monkeypatch):
    monkeypatch.setenv("MAR_HOME", str(tmp_path))
    reg = Registry()
    reg.add(sub())
    reg.save()
    loaded = Registry.load()
    assert loaded.get("zai").key == FAKE_KEY


def test_registry_load_maps_store_error_to_registry_error(tmp_path):
    bad = tmp_path / "registry.json"
    bad.write_text("{oops", encoding="utf-8")
    with pytest.raises(RegistryError, match="cannot read registry"):
        Registry.load(bad)
