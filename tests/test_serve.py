"""Tests for the LAN config server (mar serve)."""

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from mar import cli, limits, serve


@pytest.fixture()
def registry_path(tmp_path):
    path = tmp_path / "registry.json"
    cli.main(["add", "zai", "--key", "zai-key-1234567890", "--registry", str(path)])
    cli.main(
        [
            "add", "openrouter", "--key", "or-key-1234567890",
            "--registry", str(path),
        ]
    )
    return path


def _add_sub(registry_path: "Path", entry: dict) -> None:
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    data["subscriptions"].append(entry)
    registry_path.write_text(json.dumps(data), encoding="utf-8")


class TestRenderConfig:
    def test_bash_payload_strips_markers_and_keeps_policy(self, registry_path):
        body = serve.render_config(registry_path, "bash")
        assert "ZAI_API_KEY=zai-key-1234567890" in body
        assert "omp config set edit.mode hashline" in body
        assert "PI_STRICT_EDIT_MODE=1" in body
        assert "BEGIN" not in body
        assert "END" not in body

    def test_powershell_payload_strips_markers(self, registry_path):
        body = serve.render_config(registry_path, "powershell")
        assert "ZAI_API_KEY=zai-key-1234567890" in body
        assert "BEGIN" not in body

    def test_payload_is_generated_fresh_per_call(self, tmp_path):
        path = tmp_path / "registry.json"
        body_before = serve.render_config(path, "bash")
        cli.main(["add", "zai", "--key", "zai-key-1234567890", "--registry", str(path)])
        body_after = serve.render_config(path, "bash")
        assert "ZAI_API_KEY" not in body_before
        assert "ZAI_API_KEY" in body_after


class TestServicesSnapshot:
    def test_lists_provider_links_and_expiry(self, registry_path, monkeypatch):
        _add_sub(
            registry_path,
            {
                "provider": "claude",
                "auth_method": "oauth",
                "key": "tok-123456",
                "models": [],
                "default_model": None,
                "added_at": "2026-10-10T00:00:00+00:00",
                "status": "active",
                "notes": "",
                "expires_at_ms": int(time.time() * 1000) + 3 * 3_600_000,
            },
        )
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(
            serve,
            "_run_omp_usage",
            lambda: {
                "reports": [
                    {
                        "provider": "anthropic",
                        "limits": [
                            {
                                "id": "anthropic:5h",
                                "amount": {
                                    "remaining": 90,
                                    "limit": 100,
                                    "unit": "percent",
                                },
                                "window": {
                                    "resetsAt": int(time.time() * 1000)
                                    + 2 * 3_600_000
                                },
                            }
                        ],
                    }
                ]
            },
        )
        rows = {r["provider"]: r for r in serve.services_snapshot(registry_path)}
        assert set(rows) == {"zai", "openrouter", "claude"}
        assert rows["claude"]["renew"] == "in 3.0h"  # oauth token expiry
        assert rows["claude"]["usage"] == "5h 90% (resets in 2.0h)"
        assert rows["zai"]["renew"] == "\u2014"  # api keys: nothing expires
        assert rows["zai"]["limits_url"].startswith("https://")
        assert rows["zai"]["usage"] is None  # only openrouter fetches live

    @pytest.mark.parametrize(
        ("offset_hours", "expected"),
        [
            (3, "in 3.0h"),
            (144, "in ~6 days"),
            (49, "in ~2 days"),
            (365 * 24 + 1, ""),
            (-1, "EXPIRED"),
        ],
    )
    def test_renew_formatting_is_tiered(
        self, registry_path, monkeypatch, offset_hours, expected
    ):
        _add_sub(
            registry_path,
            {
                "provider": "claude",
                "auth_method": "oauth",
                "key": "tok-123456",
                "models": [],
                "default_model": None,
                "added_at": "2026-10-10T00:00:00+00:00",
                "status": "active",
                "notes": "",
                "expires_at_ms": int(time.time() * 1000)
                + offset_hours * 3_600_000,
            },
        )
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(serve, "_run_omp_usage", dict)
        rows = {r["provider"]: r for r in serve.services_snapshot(registry_path)}
        assert rows["claude"]["renew"] == expected

    def test_omp_usage_fills_quota_and_renew_columns(
        self, registry_path, monkeypatch
    ):
        _add_sub(
            registry_path,
            {
                "provider": "claude",
                "auth_method": "oauth",
                "key": "tok-123456",
                "models": [],
                "default_model": None,
                "added_at": "2026-10-10T00:00:00+00:00",
                "status": "active",
                "notes": "",
                "expires_at_ms": 0,
            },
        )
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        reset_ms = int(time.time() * 1000) + 4 * 3_600_000
        monkeypatch.setattr(
            serve,
            "_run_omp_usage",
            lambda: {
                "reports": [
                    {
                        "provider": "anthropic",
                        "limits": [
                            {
                                "id": "anthropic:5h",
                                "amount": {
                                    "remaining": 100,
                                    "limit": 100,
                                    "unit": "percent",
                                },
                                "window": {},
                            },
                            {
                                "id": "anthropic:7d",
                                "amount": {
                                    "remaining": 40,
                                    "limit": 100,
                                    "unit": "percent",
                                },
                                "window": {"resetsAt": reset_ms},
                            },
                        ],
                    }
                ]
            },
        )
        rows = {r["provider"]: r for r in serve.services_snapshot(registry_path)}
        assert rows["claude"]["usage"] == "5h 100% \u00b7 7d 40% (resets in 4.0h)"
        assert rows["claude"]["renew"] == "\u2014"  # oauth row without expiry

    def test_reset_only_windows_quantify_quota(self, registry_path, monkeypatch):
        _add_sub(
            registry_path,
            {
                "provider": "opencode",
                "auth_method": "api_key",
                "key": "oc-key-1234567890",
                "models": [],
                "default_model": None,
                "added_at": "2026-10-10T00:00:00+00:00",
                "status": "active",
                "notes": "",
                "expires_at_ms": 0,
            },
        )
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(
            serve,
            "_run_omp_usage",
            lambda: {
                "reports": [
                    {
                        "provider": "opencode-go",
                        "limits": [
                            {
                                "id": "opencode-go:5h",
                                "amount": {},
                                "window": {
                                    "resetsAt": int(time.time() * 1000)
                                    + 2 * 3_600_000
                                },
                            }
                        ],
                    }
                ]
            },
        )
        rows = {r["provider"]: r for r in serve.services_snapshot(registry_path)}
        assert rows["opencode"]["usage"] == "5h resets in 2.0h"  # reset-only window
        assert rows["opencode"]["renew"] == "\u2014"  # api keys never renew

    def test_every_window_carries_its_own_reset_timeline(self, registry_path, monkeypatch):
        _add_sub(
            registry_path,
            {
                "provider": "opencode",
                "auth_method": "api_key",
                "key": "oc-key-1234567890",
                "models": [],
                "default_model": None,
                "added_at": "2026-10-10T00:00:00+00:00",
                "status": "active",
                "notes": "",
                "expires_at_ms": 0,
            },
        )
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        now_ms = int(time.time() * 1000)
        monkeypatch.setattr(
            serve,
            "_run_omp_usage",
            lambda: {
                "reports": [
                    {
                        "provider": "opencode-go",
                        "limits": [
                            {
                                "id": "opencode-go:rolling-5h",
                                "amount": {},
                                "window": {"resetsAt": now_ms + 2 * 3_600_000},
                            },
                            {
                                "id": "opencode-go:weekly",
                                "amount": {},
                                "window": {"resetsAt": now_ms + 27 * 3_600_000},
                            },
                            {
                                "id": "opencode-go:monthly",
                                "amount": {},
                                "window": {"resetsAt": now_ms + 8 * 24 * 3_600_000},
                            },
                        ],
                    }
                ]
            },
        )
        rows = {r["provider"]: r for r in serve.services_snapshot(registry_path)}
        assert (
            rows["opencode"]["usage"]
            == "rolling-5h resets in 2.0h \u00b7 weekly resets in 27.0h"
            " \u00b7 monthly resets in ~8 days"
        )
        assert rows["opencode"]["renew"] == "\u2014"

    def test_api_key_rows_never_show_window_resets(self, registry_path, monkeypatch):
        _add_sub(
            registry_path,
            {
                "provider": "commandcode",
                "auth_method": "api_key",
                "key": "cc-key-1234567890",
                "models": [],
                "default_model": None,
                "added_at": "2026-10-10T00:00:00+00:00",
                "status": "active",
                "notes": "",
                "expires_at_ms": 0,
            },
        )
        reset_ms = int(time.time() * 1000) + 2 * 3_600_000
        reports = [
            {
                "provider": omp_id,
                "limits": [
                    {
                        "id": f"{omp_id}:5h",
                        "amount": {"remaining": 50, "limit": 100, "unit": "percent"},
                        "window": {"resetsAt": reset_ms},
                    }
                ],
            }
            for omp_id in ("zai", "openrouter", "commandcode")
        ]
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(serve, "_run_omp_usage", lambda: {"reports": reports})
        rows = {r["provider"]: r for r in serve.services_snapshot(registry_path)}
        for pid in ("zai", "openrouter", "commandcode"):
            assert rows[pid]["renew"] == "\u2014"  # api keys carry no renewal
            assert rows[pid]["usage"] == "5h 50% (resets in 2.0h)"

    def test_omp_usage_failure_keeps_page_alive(
        self, registry_path, monkeypatch
    ):
        def boom():
            raise OSError("omp missing")

        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(serve, "_run_omp_usage", boom)
        rows = {r["provider"]: r for r in serve.services_snapshot(registry_path)}
        assert rows["zai"]["usage"] is None
        assert rows["zai"]["renew"] == "\u2014"

    def test_formatter_handles_missing_remaining(self, monkeypatch):
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(
            serve,
            "_run_omp_usage",
            lambda: {
                "reports": [
                    {
                        "provider": "commandcode",
                        "limits": [
                            {
                                "id": "commandcode:5h",
                                "amount": {"remaining": None, "limit": 14, "unit": "cre"},
                                "window": {"resetsAt": 1792283530102},
                            }
                        ],
                    }
                ]
            },
        )
        cell, earliest = serve._format_limits(
            serve._omp_usage().get("commandcode", [])
            + [
                {
                    "id": "copilot:chat",
                    "amount": {"remaining": 200, "limit": 200, "unit": "requests"},
                    "window": {},
                }
            ]
        )
        assert "5h \u2013/14 cr" in cell
        assert "chat 200/200 req" in cell
        assert "resets" in cell
        assert earliest == 1792283530102

    def test_openrouter_row_carries_live_credits(self, registry_path, monkeypatch):
        calls = []

        def fake_fetch(provider_id, key, **kwargs):
            calls.append(provider_id)
            return limits.LimitSnapshot(
                provider="openrouter",
                fetched_at="2026-10-13T00:00:00+00:00",
                items=(limits.LimitItem("credits", 10.0, 325.0, "USD", "all-time"),),
                source="api",
            )

        monkeypatch.setattr(serve.limits, "fetch", fake_fetch)
        monkeypatch.setattr(serve, "_usage_cache", {})
        rows = serve.services_snapshot(registry_path)
        row = next(r for r in rows if r["provider"] == "openrouter")
        assert row["usage"] == "$315.00 left"
        # second call serves from the TTL cache — no second fetch
        serve.services_snapshot(registry_path)
        assert calls == ["openrouter"]

    def test_openrouter_fetch_failure_degrades(self, registry_path, monkeypatch):
        def boom(provider_id, key, **kwargs):
            raise limits.LimitError("down")

        monkeypatch.setattr(serve.limits, "fetch", boom)
        monkeypatch.setattr(serve, "_usage_cache", {})
        row = next(
            r
            for r in serve.services_snapshot(registry_path)
            if r["provider"] == "openrouter"
        )
        assert row["usage"] == "unavailable"


class TestIndexHtml:
    def test_page_has_copy_buttons_repo_and_services(self, registry_path, monkeypatch):
        monkeypatch.setattr(serve, "_usage_cache", {})
        _add_sub(
            registry_path,
            {
                "provider": "claude",
                "auth_method": "oauth",
                "key": "tok-123456",
                "models": [],
                "default_model": None,
                "added_at": "2026-10-10T00:00:00+00:00",
                "status": "active",
                "notes": "",
                "expires_at_ms": int(time.time() * 1000) + 3 * 3_600_000,
            },
        )
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(
            serve,
            "_run_omp_usage",
            lambda: {
                "reports": [
                    {
                        "provider": "anthropic",
                        "limits": [
                            {
                                "id": "anthropic:5h",
                                "amount": {
                                    "remaining": 90,
                                    "limit": 100,
                                    "unit": "percent",
                                },
                                "window": {
                                    "resetsAt": int(time.time() * 1000)
                                    + 2 * 3_600_000
                                },
                            }
                        ],
                    }
                ]
            },
        )
        html = serve.render_index_html("localhost:8930", registry_path)
        assert "<!doctype html>" in html.lower()
        assert "http://localhost:8930/config.sh | sh" in html
        assert "config.ps1" in html
        assert "navigator.clipboard" in body_guard(html)  # secure-context copy path
        assert "execCommand" in html  # non-secure-context fallback
        assert "github.com/ggurov/MAR" in html
        assert "Services" in html
        assert "zai" in html and "openrouter" in html
        assert (
            '<a href="https://claude.ai/new#settings/usage" target="_blank"'
            ' rel="noopener"><b>claude</b></a>' in html
        )  # provider name doubles as the usage link
        assert ">Usage<" not in html  # usage column dropped
        assert "resets in 2.0h" in html  # window reset rides the quota cell
        assert "in 3.0h" in html  # oauth token expiry stays in the renew cell
        assert "plan \u2197" not in html  # plan link removed from renew cell
        assert "re-login:" not in html  # re-login hint removed from renew cell
        assert "omp /login" not in html  # old generic hint is gone
        assert "mar pull" in html  # renewal-flow note under the table


def body_guard(html: str) -> str:
    return html


class TestLanIps:
    def test_returns_at_least_one_ip(self):
        ips = serve.lan_ips()
        assert len(ips) >= 1
        assert all("." in ip for ip in ips)


class TestHttpServer:
    @pytest.fixture()
    def server(self, registry_path, monkeypatch):
        monkeypatch.setattr(serve, "_usage_cache", {})
        httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), serve.make_handler(registry_path)
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
        httpd.shutdown()

    def _get(self, url):
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")

    def test_config_sh_served(self, server):
        status, body = self._get(f"{server}/config.sh")
        assert status == 200
        assert "ZAI_API_KEY=zai-key-1234567890" in body
        assert "omp config set edit.mode hashline" in body

    def test_config_ps1_served(self, server):
        status, body = self._get(f"{server}/config.ps1")
        assert status == 200
        assert "Set-Content" in body

    def test_index_is_html_with_copy_and_repo_links(self, server):
        status, body = self._get(f"{server}/")
        assert status == 200
        assert "<!doctype html>" in body.lower()
        assert "text/html" in self._content_type(f"{server}/")
        host = server.split("//")[1]
        assert f"http://{host}/config.sh | sh" in body
        assert "github.com/ggurov/MAR" in body
        assert "navigator.clipboard" in body  # secure-context copy path
        assert "execCommand" in body  # non-secure-context fallback

    def _content_type(self, url):
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.headers.get("Content-Type", "")

    def test_unknown_path_is_404(self, server):
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            self._get(f"{server}/nope")
        assert excinfo.value.code == 404
