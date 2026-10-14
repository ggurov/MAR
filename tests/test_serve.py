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
        monkeypatch.setattr(serve, "_run_omp_usage", dict)  # no windows
        rows = {r["provider"]: r for r in serve.services_snapshot(registry_path)}
        assert set(rows) == {"zai", "openrouter", "claude"}
        assert rows["claude"]["renew"].startswith("2026-")
        assert "in 3.0h" in rows["claude"]["renew"]  # token expiry fallback
        assert rows["claude"]["renew_url"] == "https://console.anthropic.com/settings/usage"
        assert rows["zai"]["renew_url"] == ""  # api keys: nothing to renew
        assert rows["zai"]["limits_url"].startswith("https://")
        assert rows["zai"]["usage"] is None  # only openrouter fetches live

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
                                "window": {"resetsAt": 1792238400422},
                            },
                        ],
                    }
                ]
            },
        )
        rows = {r["provider"]: r for r in serve.services_snapshot(registry_path)}
        assert rows["claude"]["usage"] == "5h 100% \u00b7 7d 40%"
        assert "2026-10-17" in rows["claude"]["renew"]  # earliest resetsAt wins

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
        assert row["usage"] == "$315.00 of $325.00 left"
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
        monkeypatch.setattr(serve, "_run_omp_usage", dict)
        html = serve.render_index_html("localhost:8930", registry_path)
        assert "<!doctype html>" in html.lower()
        assert "http://localhost:8930/config.sh | sh" in html
        assert "config.ps1" in html
        assert "navigator.clipboard" in body_guard(html)  # secure-context copy path
        assert "execCommand" in html  # non-secure-context fallback
        assert "github.com/ggurov/MAR" in html
        assert "Services" in html
        assert "zai" in html and "openrouter" in html
        assert "usage" in html  # usage-page links
        assert "plan ↗" in html  # oauth rows link the plan/usage page, not API keys
        assert "omp /login" in html  # renewal is re-login, then pull
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
