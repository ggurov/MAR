"""Tests for the tokenpool metadata/MAR export integration (no network)."""

from __future__ import annotations

import json
import urllib.error

import pytest

from mar import tokenpool


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_normalize_base_url_adds_v1_and_drops_query():
    assert tokenpool.normalize_base_url("http://pool:8940") == "http://pool:8940/v1"
    assert tokenpool.normalize_base_url("http://pool:8940/v1/") == "http://pool:8940/v1"
    assert tokenpool.normalize_base_url("https://pool/path") == "https://pool/path/v1"
    with pytest.raises(tokenpool.TokenpoolError):
        tokenpool.normalize_base_url("ftp://pool")


def test_fetch_models_reads_metadata_and_defaults(monkeypatch):
    payload = {
        "object": "list",
        "data": [
            {
                "id": "deepseek-v4.1-flash",
                "name": "DeepSeek V4.1 Flash",
                "context_window": 1048576,
                "max_tokens": 65536,
                "reasoning": True,
                "input": ["text"],
            },
            {"id": "glm-5.3-flash"},
            {"id": ""},
            "junk",
        ],
    }
    seen = {}

    def fake_open(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        return FakeResponse(payload)

    monkeypatch.setattr(tokenpool.urllib.request, "urlopen", fake_open)
    models = tokenpool.fetch_models("http://pool:8940", token="secret-token")
    assert [m.id for m in models] == ["deepseek-v4.1-flash", "glm-5.3-flash"]
    assert models[0].context_window == 1048576
    assert models[0].max_tokens == 65536
    assert models[1].context_window == 128_000
    assert seen["url"] == "http://pool:8940/v1/models"
    assert seen["auth"] == "Bearer secret-token"


def test_fetch_models_error_never_leaks_token(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(tokenpool.urllib.request, "urlopen", boom)
    with pytest.raises(tokenpool.TokenpoolError) as excinfo:
        tokenpool.fetch_models("http://pool:8940", token="super-secret")
    assert "super-secret" not in str(excinfo.value)


def test_provider_entry_shape():
    entry = tokenpool.provider_entry(
        "http://pool:8940",
        [tokenpool.TokenpoolModel("m", "Model", 1000, 100, True, ("text",))],
    )
    assert entry["api"] == "openai-completions"
    assert entry["auth"] == "none"
    assert entry["baseUrl"] == "http://pool:8940/v1"
    model = entry["models"][0]
    assert model["id"] == "m"
    assert model["contextWindow"] == 1000
    assert model["maxTokens"] == 100
    assert model["cost"]["input"] == 0
