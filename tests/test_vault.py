"""Tests for mar.vault: HashiCorp KV-v2 registry backend over a fake HTTP layer.

The fake server below reimplements just enough of Vault's KV-v2 HTTP API
(read/write/list/delete + the double-nested ``data.data`` envelope) in memory,
monkeypatched over ``mar.vault._open`` — the same seam limits.py uses. No test
touches a real Vault; the suite stays green without one.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from mar import vault
from mar.registry import Registry, Subscription
from mar.vault import VaultError, VaultStore

FAKE_KEY = "test-key-1234567890"
FAKE_KEY2 = "test-key-9876543210abcd"
ADDED_AT = "2026-10-10T12:00:00+00:00"
TOKEN = "test-token-12345678"


def sub(provider: str = "zai", key: str = FAKE_KEY, **overrides) -> Subscription:
    fields: dict = {
        "provider": provider,
        "auth_method": "api_key",
        "key": key,
        "models": ["glm-4.7"],
        "default_model": "glm-4.7",
        "added_at": ADDED_AT,
    }
    fields.update(overrides)
    return Subscription(**fields)


class FakeVault:
    """In-memory KV-v2 engine: secrets keyed by 'mount/data/path'."""

    def __init__(self):
        self.secrets: dict[str, dict] = {}
        self.requests: list[tuple[str, str, dict | None, dict | None]] = []
        self.fail_with: Exception | None = None
        self.token = TOKEN

    # -- plumbing ------------------------------------------------------------
    def open(self, request, timeout=None):
        if self.fail_with is not None:
            raise self.fail_with
        auth = request.headers.get("X-vault-token") or request.headers.get("X-Vault-Token")
        url = request.full_url
        path = url.split("/v1/", 1)[1].split("?")[0]
        body = json.loads(request.data.decode()) if request.data else None
        self.requests.append((request.get_method(), path, body, auth))
        if self.token and auth != self.token:
            return _resp(403, {"errors": ["permission denied"]})
        return self.route(request.get_method(), path, url, body)

    def route(self, method, path, url, body):
        list_mode = "list=true" in url
        if path.startswith("secret/data/"):
            key = path.removeprefix("secret/data/")
            if method == "GET":
                if key not in self.secrets:
                    return _resp(404, {"errors": []})
                return _resp(200, {"data": {"data": self.secrets[key]}})
            if method == "PUT":
                self.secrets[key] = body["data"]
                return _resp(200, {"data": {"version": 1}})
            if method == "DELETE":
                self.secrets.pop(key, None)
                return _resp(204, {})
        if path.startswith("secret/metadata/") and method == "GET" and list_mode:
            parent = path.removeprefix("secret/metadata/")
            prefix = f"{parent}/" if parent else ""
            keys = sorted(
                {k[len(prefix) :].split("/")[0] for k in self.secrets if k.startswith(prefix)}
            )
            return _resp(200, {"data": {"keys": keys}})
        return _resp(404, {"errors": []})


class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _resp(status, payload):
    if status >= 400:
        raise urllib.error.HTTPError(
            "http://vault/v1/x",
            status,
            "err",
            {},
            __import__("io").BytesIO(json.dumps(payload).encode()),
        )
    return _Resp(status, payload)


@pytest.fixture()
def fake(monkeypatch):
    fv = FakeVault()
    monkeypatch.setattr(vault, "_open", fv.open)
    return fv


def make_store() -> VaultStore:
    return VaultStore(addr="http://127.0.0.1:8200", token=TOKEN, mount="secret", path="mar/keys")


def payload(*subs: Subscription) -> dict:
    from dataclasses import asdict

    return {"version": 1, "subscriptions": [asdict(s) for s in subs]}


# --- write/read round trip ----------------------------------------------------


def test_write_stores_one_secret_per_provider_plus_registry_meta(fake):
    make_store().write(payload(sub("zai"), sub("openrouter")))
    assert set(fake.secrets) == {"mar/keys/_registry", "mar/keys/zai", "mar/keys/openrouter"}


def test_write_puts_key_and_metadata_fields(fake):
    make_store().write(payload(sub("zai")))
    fields = fake.secrets["mar/keys/zai"]
    assert fields["key"] == FAKE_KEY
    assert fields["auth_method"] == "api_key"
    assert fields["models"] == ["glm-4.7"]
    assert "updated_at" in fields and "rotated_by" in fields
    meta = fake.secrets["mar/keys/_registry"]
    assert meta["version"] == 1


def test_read_missing_vault_is_none(fake):
    assert make_store().read() is None


def test_round_trip_preserves_every_subscription_field(fake):
    s = make_store()
    s.write(
        payload(
            sub("zai"),
            sub(
                "claude",
                auth_method="oauth",
                key="oauth-tok-1234567890",
                status="active",
                notes="from pull",
                expires_at_ms=1790000000000,
                default_model=None,
                models=[],
            ),
        )
    )
    raw = VaultStore(
        addr="http://127.0.0.1:8200", token=TOKEN, mount="secret", path="mar/keys"
    ).read()
    assert raw is not None
    assert raw["version"] == 1
    by_provider = {e["provider"]: e for e in raw["subscriptions"]}
    assert by_provider["zai"]["key"] == FAKE_KEY
    assert by_provider["claude"]["auth_method"] == "oauth"
    assert by_provider["claude"]["expires_at_ms"] == 1790000000000
    assert by_provider["claude"]["notes"] == "from pull"


def test_registry_round_trip_through_vault_store(fake):
    reg = Registry(store=make_store())
    reg.add(sub("zai"))
    reg.add(sub("openrouter", key="sk-test-0987654321"))
    reg.save()
    reloaded = Registry.load(store=make_store())
    assert reloaded.get("zai").key == FAKE_KEY
    assert reloaded.get("openrouter").key == "sk-test-0987654321"


# --- rotation-friendliness -----------------------------------------------------


def test_changing_one_provider_touches_only_its_secret(fake):
    s = make_store()
    s.write(payload(sub("zai"), sub("openrouter", key="sk-test-1111111111")))
    before = dict(fake.secrets)
    s.write(payload(sub("zai"), sub("openrouter", key="sk-test-2222222222")))
    assert fake.secrets["mar/keys/zai"] == before["mar/keys/zai"]
    assert fake.secrets["mar/keys/openrouter"]["key"] == "sk-test-2222222222"
    assert fake.secrets["mar/keys/openrouter"]["rotated_by"]


def test_rotation_updates_key_and_rotated_by_only(fake):
    s = make_store()
    s.write(payload(sub("zai")))
    s.write(payload(sub("zai", key=FAKE_KEY2)))
    fields = fake.secrets["mar/keys/zai"]
    assert fields["key"] == FAKE_KEY2
    assert fields["models"] == ["glm-4.7"]
    assert fields["default_model"] == "glm-4.7"
    assert fields["rotated_by"]


def test_write_removes_secrets_for_removed_providers(fake):
    s = make_store()
    s.write(payload(sub("zai"), sub("openrouter", key="sk-test-3333333333")))
    s.write(payload(sub("zai")))
    assert "mar/keys/openrouter" not in fake.secrets
    assert "mar/keys/zai" in fake.secrets


# --- error handling -------------------------------------------------------------


def test_permission_denied_raises_vault_error_without_token(fake):
    s = VaultStore(addr="http://v", token="wrong-token-9999", mount="secret", path="p")
    with pytest.raises(VaultError, match="permission denied") as exc_info:
        s.write(payload(sub()))
    assert "wrong-token-9999" not in str(exc_info.value)


def test_unreachable_vault_raises_vault_error_with_addr(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(vault, "_open", boom)
    s = make_store()
    with pytest.raises(VaultError, match="127.0.0.1:8200") as exc_info:
        s.read()
    assert TOKEN not in str(exc_info.value)


def test_unknown_version_in_registry_meta_raises(fake):
    fake.secrets["mar/keys/_registry"] = {"version": 99}
    with pytest.raises(VaultError, match="version"):
        make_store().read()


def test_vault_error_is_caught_by_cli_main(fake, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("MAR_BACKEND", "vault")
    monkeypatch.setenv("VAULT_ADDR", "http://127.0.0.1:8200")
    monkeypatch.setenv("VAULT_TOKEN", TOKEN)
    monkeypatch.setattr("mar.store._config_path", lambda: tmp_path / "absent.json")
    from mar import cli

    fake.secrets["mar/keys/_registry"] = {"version": 99}
    assert cli.main(["list"]) == 1
    assert "version" in capsys.readouterr().err


# --- token handling ---------------------------------------------------------------


def test_ping_reports_vault_reachable(fake):
    assert make_store().ping() is True


def test_ping_reports_vault_unreachable(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(vault, "_open", boom)
    assert make_store().ping() is False
