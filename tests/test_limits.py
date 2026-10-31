"""Tests for usage-limit snapshots (M4): API fetch + manual hints.

Network is fully mocked: every test monkeypatches ``limits._open`` (the
module-level injectable ``urllib.request.urlopen``); no test touches the
network. Keys are obviously fake, and every error-path test asserts the fake
key never leaks into the message.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.error
from datetime import datetime

import pytest

from mar import limits
from mar.limits import (
    LimitError,
    LimitItem,
    LimitSnapshot,
    fetch,
    manual_hint,
    supported,
)
from mar.providers import CATALOG

KEY = "mar-fake-key-LEAK-7f3a9"  # distinctive; must never appear in any message
CREDITS_URL = "https://openrouter.ai/api/v1/credits"


class FakeResponse:
    """Minimal urlopen stand-in: context manager returning canned bytes."""

    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def ok_open(request, timeout):
    """Fake urlopen returning a well-formed openrouter credits payload."""
    return FakeResponse(json.dumps({"data": {"total_credits": 50.0, "total_usage": 12.5}}).encode())


def raises_open(exc: Exception):
    def fake(request, timeout):
        raise exc

    return fake


# ---------------------------------------------------------------- supported()


def test_supported_lists_api_kind_ids_in_catalog_order():
    from mar.providers import CATALOG

    assert supported() == ["openrouter", "gemini", "strata"]
    assert supported() == [p.id for p in CATALOG if p.limits_kind == "api"]

STRATA_URL = "http://192.168.1.203:8080/v1"
STRATA_MODEL = "deepseek-v4.1-flash"


def _strata_model(model_id: str = STRATA_MODEL, ctx: int = 1048576) -> dict:
    return {
        "id": model_id,
        "object": "model",
        "status": {"value": "loaded"},
        "meta": {"n_ctx": ctx},
        "architecture": {
            "input_modalities": ["text"],
            "output_modalities": ["text"],
        },
    }


# ------------------------------------------------------ strata (local engine)


def test_fetch_strata_probes_the_catalog_endpoint_without_a_key(monkeypatch):
    captured = {}

    def fake_open(request, timeout):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return FakeResponse(json.dumps({"data": [_strata_model()]}).encode())

    monkeypatch.setattr(limits, "_open", fake_open)
    snapshot = fetch("strata", "", timeout=4.0)

    assert captured["url"] == f"{STRATA_URL}/models"
    assert captured["auth"] is None
    assert captured["timeout"] == 4.0
    assert snapshot.provider == "strata"
    assert snapshot.source == "api"
    assert snapshot.items == ()
    assert snapshot.note == f"up: 1 model \u00b7 {STRATA_MODEL} \u00b7 1048576 ctx \u00b7 text"


def test_fetch_strata_counts_every_served_model(monkeypatch):
    payload = {"data": [_strata_model(), _strata_model("second-model", ctx=8192)]}
    monkeypatch.setattr(limits, "_open", _payload(payload))
    assert fetch("strata", "").note.startswith("up: 2 models \u00b7 ")


STRATA_ERROR_CASES = [
    pytest.param({"object": "list"}, "missing the 'data' list", id="missing-data"),
    pytest.param({"data": []}, "no loaded model", id="empty-data"),
    pytest.param({"data": [{"object": "model"}]}, "no loaded model", id="entry-without-id"),
]


@pytest.mark.parametrize(("payload", "expected"), STRATA_ERROR_CASES)
def test_fetch_strata_error_paths(monkeypatch, payload, expected):
    monkeypatch.setattr(limits, "_open", _payload(payload))
    with pytest.raises(LimitError) as excinfo:
        fetch("strata", "")
    assert expected in str(excinfo.value)


def test_fetch_strata_http_error_names_the_endpoint(monkeypatch):
    monkeypatch.setattr(
        limits, "_open", raises_open(urllib.error.URLError("connection refused"))
    )
    with pytest.raises(LimitError) as excinfo:
        fetch("strata", "")
    assert "strata models API" in str(excinfo.value)
    assert "connection refused" in str(excinfo.value)

# ---------------------------------------------------------------- gemini fetcher()


def test_fetch_gemini_reports_key_liveness(monkeypatch):
    captured = {}
    payload = json.dumps(
        {
            "models": [
                {"name": "models/gemini-3-flash"},
                {"name": "models/gemini-flash-lite-latest"},
                {"name": "models/text-embedding-005"},
            ]
        }
    ).encode()

    def fake_open(request, timeout=10.0):
        captured["url"] = request.full_url
        captured["header"] = request.headers
        return _body(payload)(request, timeout)

    monkeypatch.setattr("mar.limits._open", fake_open)
    snap = fetch("gemini", "gemini-key-1234567890")
    assert captured["url"].startswith("https://generativelanguage.googleapis.com/v1beta/models")
    headers = {k.lower(): v for k, v in captured["header"].items()}
    assert headers["x-goog-api-key"] == "gemini-key-1234567890"
    assert snap.source == "api"
    assert snap.items == ()
    assert snap.note == "key alive: 3 models visible (2 gemini)"
    assert "gemini-key-1234567890" not in snap.note


def test_fetch_gemini_missing_models_list(monkeypatch):
    monkeypatch.setattr("mar.limits._open", _payload({"models": None}))
    with pytest.raises(LimitError) as excinfo:
        fetch("gemini", "gemini-key-1234567890")
    assert "missing the 'models' list" in str(excinfo.value)


def test_fetch_gemini_http_error_names_gemini(monkeypatch):
    monkeypatch.setattr(
        "mar.limits._open",
        raises_open(urllib.error.HTTPError("https://x", 400, "Bad Request", None, None)),
    )
    with pytest.raises(LimitError) as excinfo:
        fetch("gemini", "gemini-key-1234567890")
    assert "gemini models API returned HTTP 400" in str(excinfo.value)
    assert "gemini-key-1234567890" not in str(excinfo.value)


# ------------------------------------------------------------- manual_hint()


@pytest.mark.parametrize(
    "provider",
    [p for p in CATALOG if p.limits_url],
    ids=lambda p: p.id,
)
def test_manual_hint_is_two_lines_with_url_then_instruction(provider):
    hint = manual_hint(provider.id)
    lines = hint.splitlines()
    assert len(lines) == 2
    assert lines[0] == provider.limits_url
    assert "open this page and check usage manually" in lines[1].lower()


def test_manual_hint_for_self_hosted_entry_points_at_its_endpoint():
    hint = manual_hint("strata")
    lines = hint.splitlines()
    assert len(lines) == 2
    assert lines[0] == STRATA_URL
    assert "mar limits strata" in lines[1]


def test_manual_hint_openrouter_carries_catalog_page_url():
    assert "https://openrouter.ai/credits" in manual_hint("openrouter")


def test_manual_hint_unknown_id_raises_limit_error_listing_valid_ids():
    with pytest.raises(LimitError) as excinfo:
        manual_hint("nope")
    message = str(excinfo.value)
    assert "nope" in message
    assert "zai" in message  # one of the listed valid ids
    assert KEY not in message


# ------------------------------------------------------------------- fetch()


def test_fetch_openrouter_success_parses_credits(monkeypatch):
    captured = {}

    def fake_open(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["auth"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return FakeResponse(
            json.dumps({"data": {"total_credits": 50.0, "total_usage": 12.5}}).encode()
        )

    monkeypatch.setattr(limits, "_open", fake_open)
    snapshot = fetch("openrouter", KEY, timeout=3.5)

    assert isinstance(snapshot, LimitSnapshot)
    assert snapshot.provider == "openrouter"
    assert snapshot.source == "api"
    assert snapshot.note == ""
    assert snapshot.items == (
        LimitItem(name="credits", used=12.5, limit=50.0, unit="USD", window="all-time"),
    )
    assert captured["url"] == CREDITS_URL
    assert captured["method"] == "GET"
    assert captured["auth"] == f"Bearer {KEY}"
    assert captured["timeout"] == 3.5


def test_fetch_forwards_default_timeout_of_10(monkeypatch):
    captured = {}

    def fake_open(request, timeout):
        captured["timeout"] = timeout
        return FakeResponse(json.dumps({"data": {"total_credits": 1, "total_usage": 0}}).encode())

    monkeypatch.setattr(limits, "_open", fake_open)
    fetch("openrouter", KEY)
    assert captured["timeout"] == 10.0


def test_fetch_fetched_at_is_iso8601_utc(monkeypatch):
    monkeypatch.setattr(limits, "_open", ok_open)
    snapshot = fetch("openrouter", KEY)
    parsed = datetime.fromisoformat(snapshot.fetched_at)  # raises if not ISO 8601
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


# Error paths: table-driven; every message must hide the key.


def _body(body: bytes, status: int = 200):
    def fake(request, timeout):
        return FakeResponse(body, status)

    return fake


def _payload(payload):
    return _body(json.dumps(payload).encode())


ERROR_CASES = [
    (
        "http_401",
        raises_open(urllib.error.HTTPError(CREDITS_URL, 401, "Unauthorized", None, None)),
        "401",
    ),
    (
        "http_403",
        raises_open(urllib.error.HTTPError(CREDITS_URL, 403, "Forbidden", None, None)),
        "403",
    ),
    ("non_200_status", _body(b"{}", 500), "500"),
    (
        "url_error",
        raises_open(urllib.error.URLError("name or service not known")),
        "name or service not known",
    ),
    ("timeout", raises_open(TimeoutError("timed out")), "timed out"),
    ("malformed_json", _body(b"<html>not json</html>"), "malformed"),
    ("top_level_not_object", _body(b"[1, 2]"), "JSON"),
    ("missing_data_key", _payload({"total_credits": 50.0, "total_usage": 12.5}), "data"),
    ("data_not_object", _payload({"data": [1, 2]}), "data"),
    (
        "non_numeric_usage",
        _payload({"data": {"total_credits": 50.0, "total_usage": "lots"}}),
        "total_usage",
    ),
    (
        "bool_is_not_numeric",
        _payload({"data": {"total_credits": True, "total_usage": 12.5}}),
        "total_credits",
    ),
    ("missing_usage_field", _payload({"data": {"total_credits": 50.0}}), "total_usage"),
]


@pytest.mark.parametrize(
    ("case_name", "fake", "expected_fragment"),
    ERROR_CASES,
    ids=[c[0] for c in ERROR_CASES],
)
def test_fetch_error_paths_raise_limit_error_without_leaking_key(
    monkeypatch, case_name, fake, expected_fragment
):
    monkeypatch.setattr(limits, "_open", fake)
    with pytest.raises(LimitError) as excinfo:
        fetch("openrouter", KEY)
    message = str(excinfo.value)
    assert expected_fragment in message
    assert KEY not in message


@pytest.mark.parametrize("provider_id", ["zai", "cursor-grok"])
def test_fetch_manual_kind_provider_points_to_manual_hint(monkeypatch, provider_id):
    def fail_if_called(request, timeout):
        raise AssertionError("network must not be touched for manual-kind providers")

    monkeypatch.setattr(limits, "_open", fail_if_called)
    with pytest.raises(LimitError) as excinfo:
        fetch(provider_id, KEY)
    message = str(excinfo.value)
    assert "manual_hint" in message
    assert provider_id in message
    assert KEY not in message


def test_fetch_unknown_provider_raises_limit_error_listing_valid_ids():
    with pytest.raises(LimitError) as excinfo:
        fetch("nope", KEY)
    message = str(excinfo.value)
    assert "nope" in message
    assert "zai" in message
    assert KEY not in message


# ------------------------------------------------- dataclass shape contracts


def test_limit_item_is_frozen_with_pinned_fields():
    item = LimitItem(name="credits", used=1.0, limit=2.0, unit="USD", window="all-time")
    assert (item.name, item.used, item.limit, item.unit, item.window) == (
        "credits",
        1.0,
        2.0,
        "USD",
        "all-time",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.used = 3.0


def test_limit_snapshot_note_defaults_to_empty():
    snapshot = LimitSnapshot(
        provider="openrouter",
        fetched_at="2026-10-10T00:00:00+00:00",
        items=(),
        source="api",
    )
    assert snapshot.note == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.provider = "zai"
