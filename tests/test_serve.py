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
        assert "omp config set composer.tokenRate true" in body
        assert "omp config set memory.backend hindsight" in body
        assert f"omp config set hindsight.apiUrl {cli.DEFAULT_GLOBAL_MEMORY_URL}" in body
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

    def test_memory_off_emits_no_memory_config(self, registry_path):
        body = serve.render_config(registry_path, "bash", memory="off")
        assert "omp config set memory.backend off" in body
        assert "hindsight" not in body

    def test_declares_tokenpool_provider(self, registry_path, monkeypatch):
        from mar import tokenpool

        monkeypatch.setattr(
            cli.tokenpool,
            "fetch_models",
            lambda url, **kwargs: [
                tokenpool.TokenpoolModel("deepseek-v4.1-flash", "DeepSeek", 1048576, 65536)
            ],
        )
        body = serve.render_config(
            registry_path, "bash", tokenpool="http://pool.example:8940"
        )
        assert "tokenpool:" in body
        assert "baseUrl: http://pool.example:8940/v1" in body
        assert "id: deepseek-v4.1-flash" in body


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
        assert rows["zai"]["renew"] == ""  # api keys: nothing expires
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
        assert rows["claude"]["renew"] == ""  # oauth row without expiry

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
        assert rows["opencode"]["meters"][0]["reset"] == "resets in 2.0h"
        assert rows["opencode"]["meters"][0]["percent"] is None
        assert rows["opencode"]["renew"] == ""  # api keys never renew

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
        assert rows["opencode"]["renew"] == ""

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
            assert rows[pid]["renew"] == ""  # api keys carry no renewal
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
        assert rows["zai"]["renew"] == ""

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
        assert "5h 14 cr" in cell
        assert "chat 200/200 req" in cell
        assert "resets" in cell
        assert earliest == 1792283530102

    def test_commandcode_windows_report_percent_used(self, registry_path, monkeypatch):
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
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        # amount shapes captured from a live `omp usage --json` report
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
                                "amount": {
                                    "used": 0.9582798204,
                                    "limit": 14,
                                    "usedFraction": 0.0684485586,
                                    "unit": "credits",
                                },
                                "window": {},
                            },
                            {
                                "id": "commandcode:7d",
                                "amount": {
                                    "used": 6.3729490048,
                                    "limit": 35,
                                    "usedFraction": 0.18208425728,
                                    "unit": "credits",
                                },
                                "window": {},
                            },
                            {
                                "id": "commandcode:balance",
                                "amount": {"remaining": 63.6270509952, "unit": "credits"},
                            },
                        ],
                    }
                ]
            },
        )
        rows = {r["provider"]: r for r in serve.services_snapshot(registry_path)}
        assert rows["commandcode"]["usage"] == (
            "5h 7% used \u00b7 7d 18% used \u00b7 balance 63.63 cr"
        )
        assert rows["commandcode"]["renew"] == ""

    def test_openrouter_row_carries_live_credits(self, registry_path, monkeypatch):
        calls = []

        def fake_fetch(provider_id, key, **kwargs):
            calls.append(provider_id)
            return limits.LimitSnapshot(
                provider="openrouter",
                fetched_at="2026-10-13T00:00:00+00:00",
                items=(limits.LimitItem("balance", None, 315.0, "USD", "all-time"),),
                source="api",
            )

        monkeypatch.setattr(serve.limits, "fetch", fake_fetch)
        monkeypatch.setattr(serve, "_usage_cache", {})
        rows = serve.services_snapshot(registry_path)
        row = next(r for r in rows if r["provider"] == "openrouter")
        assert row["usage"] == "$315.00 left"
        assert row["meters"][0]["percent"] is None  # balance: no progress bar
        assert "class=\"bar\"" not in serve._render_meters(row["meters"], row["usage"])
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

    def test_keyless_local_endpoint_row_shows_endpoint_state(
        self, registry_path, monkeypatch
    ):
        _add_sub(
            registry_path,
            {
                "provider": "strata",
                "auth_method": "none",
                "key": None,
                "models": ["deepseek-v4.1-flash-1017-xq-v4"],
                "default_model": "deepseek-v4.1-flash-1017-xq-v4",
                "added_at": "2026-10-23T00:00:00+00:00",
                "status": "active",
                "notes": "",
            },
        )
        calls = []

        def fake_fetch(provider_id, key, **kwargs):
            calls.append((provider_id, key))
            return limits.LimitSnapshot(
                provider="strata",
                fetched_at="2026-10-23T00:00:00+00:00",
                items=(),
                source="api",
                note="up: 1 model \u00b7 deepseek-v4.1-flash-1017-xq-v4",
            )

        monkeypatch.setattr(serve.limits, "fetch", fake_fetch)
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(serve, "_run_omp_usage", dict)
        row = next(
            r for r in serve.services_snapshot(registry_path) if r["provider"] == "strata"
        )
        assert ("strata", "") in calls
        assert row["usage"] == "up: 1 model \u00b7 deepseek-v4.1-flash-1017-xq-v4"
        assert row["endpoint"] == "http://deepseek.nj02.gurov.us/v1"
        assert row["limits_url"] == ""


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
        assert "curl -fsSL 'http://localhost:8930/config.sh?memory=on' | sh" in html
        assert 'id="global-memory"' in html
        assert "configure global memory" in html
        assert (
            '<a href="http://hub.nj02.gurov.us:9999/dashboard" target="_blank"'
            ' rel="noopener">Open Hindsight dashboard</a>' in html
        )
        memory_card = html.split("<h2>Global memory</h2>", 1)[1].split("</div>", 1)[0]
        assert "Open Hindsight dashboard" in memory_card
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
        assert "<td></td>" in html  # empty cells render blank, not a dash
        assert "in 3.0h" in html  # oauth token expiry stays in the renew cell
        assert "plan \u2197" not in html  # plan link removed from renew cell
        assert "re-login:" not in html  # re-login hint removed from renew cell
        assert "omp /login" not in html  # old generic hint is gone
        assert "mar pull" in html  # renewal-flow note under the table
        assert 'class="bar"' in html  # omp windows render as bars
        assert "10% used" in html  # 90% remaining -> 10% used bar

    def test_page_has_routing_card_and_tokenpool_link(self, registry_path, monkeypatch):
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
        monkeypatch.setattr(serve, "_run_omp_usage", dict)
        monkeypatch.delenv("MAR_TOKENPOOL_URL", raising=False)
        html = serve.render_index_html("mar.nj02.gurov.us:8930", registry_path)
        assert "Routing" in html
        assert "http://mar.nj02.gurov.us:8940/admin" in html
        monkeypatch.setenv("MAR_TOKENPOOL_URL", "http://pool.local:9000")
        html = serve.render_index_html("mar.nj02.gurov.us:8930", registry_path)
        assert "http://pool.local:9000/admin" in html

    def test_page_links_self_hosted_endpoint_when_no_usage_page_exists(
        self, registry_path, monkeypatch
    ):
        _add_sub(
            registry_path,
            {
                "provider": "strata",
                "auth_method": "none",
                "key": None,
                "models": ["deepseek-v4.1-flash-1017-xq-v4"],
                "default_model": "deepseek-v4.1-flash-1017-xq-v4",
                "added_at": "2026-10-23T00:00:00+00:00",
                "status": "active",
                "notes": "",
            },
        )

        def boom(provider_id, key, **kwargs):
            raise limits.LimitError("down")

        monkeypatch.setattr(serve.limits, "fetch", boom)
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(serve, "_run_omp_usage", dict)
        html = serve.render_index_html("localhost:8930", registry_path)
        assert (
            '<a href="http://deepseek.nj02.gurov.us/v1" target="_blank"'
            ' rel="noopener"><b>strata</b></a>' in html
        )


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

    def test_config_sh_declares_tokenpool_provider(self, server, monkeypatch):
        from mar import tokenpool

        monkeypatch.setenv("MAR_TOKENPOOL_URL", "http://127.0.0.1:8940")
        monkeypatch.setattr(
            cli.tokenpool,
            "fetch_models",
            lambda url, **kwargs: [
                tokenpool.TokenpoolModel("deepseek-v4.1-flash", "DeepSeek", 1048576, 65536)
            ],
        )
        status, body = self._get(f"{server}/config.sh")
        assert status == 200
        assert "tokenpool:" in body
        assert "baseUrl: http://127.0.0.1:8940/v1" in body

    def test_config_ps1_served(self, server):
        status, body = self._get(f"{server}/config.ps1")
        assert status == 200
        assert "Set-Content" in body

    def test_config_ps1_memory_off(self, server):
        status, body = self._get(f"{server}/config.ps1?memory=off")
        assert status == 200
        assert "omp config set memory.backend off" in body
        assert "hindsight" not in body

    def test_config_sh_memory_on_by_default(self, server):
        _, body = self._get(f"{server}/config.sh")
        assert "omp config set memory.backend hindsight" in body
        assert "omp config set hindsight.apiUrl http://hub.nj02.gurov.us:8888" in body

    def test_config_sh_memory_off_unconfigures(self, server):
        status, body = self._get(f"{server}/config.sh?memory=off")
        assert status == 200
        assert "omp config set memory.backend off" in body
        assert "hindsight" not in body

    def test_index_is_html_with_copy_and_repo_links(self, server):
        status, body = self._get(f"{server}/")
        assert status == 200
        assert "<!doctype html>" in body.lower()
        assert "text/html" in self._content_type(f"{server}/")
        host = server.split("//")[1]
        assert f"curl -fsSL 'http://{host}/config.sh?memory=on' | sh" in body
        assert 'id="global-memory"' in body
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


class TestServeCaching:
    def test_stale_omp_usage_serves_stale_and_refreshes(self, monkeypatch):
        monkeypatch.setattr(serve, "_omp_refresh_lock", threading.Lock())
        monkeypatch.setattr(
            serve, "_omp_cache", {"ts": time.monotonic() - 1000, "data": {"old": []}}
        )
        refreshed = threading.Event()
        calls = []

        def fake():
            calls.append(1)
            refreshed.set()
            return {"reports": [{"provider": "zai", "limits": [{"id": "zai:5h"}]}]}

        monkeypatch.setattr(serve, "_run_omp_usage", fake)
        assert serve._omp_usage() == {"old": []}  # stale data, no wait
        assert refreshed.wait(2)
        deadline = time.monotonic() + 2
        while serve._omp_cache.get("data") == {"old": []} and time.monotonic() < deadline:
            time.sleep(0.01)
        assert serve._omp_cache["data"] == {"zai": [{"id": "zai:5h"}]}
        assert calls == [1]

    def test_cold_omp_usage_returns_empty_while_refresh_runs(self, monkeypatch):
        monkeypatch.setattr(serve, "_omp_refresh_lock", threading.Lock())
        monkeypatch.setattr(serve, "_omp_cache", {})
        started = threading.Event()
        release = threading.Event()
        calls = []

        def fake():
            calls.append(1)
            started.set()
            release.wait(2)
            return {"reports": []}

        monkeypatch.setattr(serve, "_run_omp_usage", fake)
        thread = serve._refresh_omp_usage_async()
        assert thread is not None
        assert started.wait(2)
        assert serve._omp_usage() == {}  # refresh in flight: never block the page
        release.set()
        thread.join(2)
        assert calls == [1]

    def test_stale_usage_cell_serves_stale_and_refreshes(self, monkeypatch):
        monkeypatch.setattr(serve, "_usage_locks", {})
        monkeypatch.setattr(
            serve, "_usage_cache", {"openrouter": (time.monotonic() - 1000, "$1.00 left")}
        )
        refreshed = threading.Event()
        calls = []

        def fake_fetch(provider_id, key, **kwargs):
            calls.append(provider_id)
            refreshed.set()
            return limits.LimitSnapshot(
                provider="openrouter",
                fetched_at="2026-10-29T00:00:00+00:00",
                items=(limits.LimitItem("balance", None, 315.0, "USD", "all-time"),),
                source="api",
            )

        monkeypatch.setattr(serve.limits, "fetch", fake_fetch)
        assert serve._usage_cell("openrouter", "key") == "$1.00 left"  # stale, no wait
        assert refreshed.wait(2)
        deadline = time.monotonic() + 2
        while serve._usage_cache["openrouter"][1] != "$315.00 left" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert serve._usage_cache["openrouter"][1] == "$315.00 left"
        assert calls == ["openrouter"]

    def test_stale_usage_cell_refresh_is_single_flight(self, monkeypatch):
        monkeypatch.setattr(serve, "_usage_locks", {})
        monkeypatch.setattr(
            serve, "_usage_cache", {"openrouter": (time.monotonic() - 1000, "$1.00 left")}
        )
        started = threading.Event()
        release = threading.Event()
        calls = []

        def fake_fetch(provider_id, key, **kwargs):
            calls.append(provider_id)
            started.set()
            release.wait(2)
            return limits.LimitSnapshot(
                provider="openrouter",
                fetched_at="2026-10-29T00:00:00+00:00",
                items=(limits.LimitItem("balance", None, 315.0, "USD", "all-time"),),
                source="api",
            )

        monkeypatch.setattr(serve.limits, "fetch", fake_fetch)
        assert serve._usage_cell("openrouter", "key") == "$1.00 left"
        assert started.wait(2)
        assert serve._usage_cell("openrouter", "key") == "$1.00 left"
        release.set()
        deadline = time.monotonic() + 2
        while serve._usage_cache["openrouter"][1] != "$315.00 left" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert serve._usage_cache["openrouter"][1] == "$315.00 left"
        assert calls == ["openrouter"]


PRIMARY_CC = "user_primary-key-1234567890"
ALT_CC = "user_joesacct1-key-9876543210"


def _add_account(registry_path: Path, provider: str, name: str, key: str) -> None:
    assert (
        cli.main(
            [
                "account", "add", provider, name, "--key", key,
                "--registry", str(registry_path),
            ]
        )
        == 0
    )


@pytest.fixture()
def account_server(tmp_path, monkeypatch):
    """Serve a registry holding one provider with two alternate keys."""
    path = tmp_path / "registry.json"
    assert (
        cli.main(["add", "commandcode", "--key", PRIMARY_CC, "--registry", str(path)])
        == 0
    )
    _add_account(path, "commandcode", "joesacct1", ALT_CC)
    monkeypatch.setattr(serve, "_usage_cache", {})
    monkeypatch.setattr(serve, "_usage_locks", {})
    monkeypatch.setattr(
        serve.limits,
        "fetch",
        lambda provider_id, key, **kwargs: limits.LimitSnapshot(
            provider=provider_id,
            fetched_at="2026-11-08T00:00:00+00:00",
            items=(),
            source="api",
            note="mocked",
        ),
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(path))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", path
    httpd.shutdown()


def _post_json(url: str, payload: dict) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")


class TestAlternateAccounts:
    def test_snapshot_lists_one_row_per_key(self, account_server, monkeypatch):
        _, path = account_server
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(serve, "_run_omp_usage", dict)
        rows = [r for r in serve.services_snapshot(path) if r["provider"] == "commandcode"]
        assert [r["account"] for r in rows] == ["original", "joesacct1"]
        assert [r["selected"] for r in rows] == [True, False]

    def test_page_renders_radio_set_with_labels(self, account_server, monkeypatch):
        _, path = account_server
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(serve, "_run_omp_usage", dict)
        html = serve.render_index_html("localhost:8930", path)
        assert 'name="acct-commandcode"' in html
        assert "original</label>" in html
        assert "joesacct1</label>" in html
        assert 'value="original" checked' in html
        assert "/select" in html

    def test_select_switches_served_payload_and_persists(self, account_server):
        url, path = account_server
        status, _ = _post_json(f"{url}/select", {"provider": "commandcode", "account": "joesacct1"})
        assert status == 200
        body = urllib.request.urlopen(f"{url}/config.sh", timeout=5).read().decode("utf-8")
        assert f"COMMAND_CODE_API_KEY={ALT_CC}" in body
        assert PRIMARY_CC not in body
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["subscriptions"][0]["selected_account"] == "joesacct1"
        # 'original' is the reserved label for the subscription's own key
        status, _ = _post_json(f"{url}/select", {"provider": "commandcode", "account": "original"})
        assert status == 200
        body = urllib.request.urlopen(f"{url}/config.sh", timeout=5).read().decode("utf-8")
        assert f"COMMAND_CODE_API_KEY={PRIMARY_CC}" in body

    @pytest.mark.parametrize(
        "payload",
        [
            {"provider": "commandcode", "account": "ghost"},
            {"provider": "ghost", "account": "original"},
            {"provider": "commandcode"},
            {"account": "original"},
        ],
    )
    def test_select_rejects_bad_input(self, account_server, payload):
        url, _ = account_server
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _post_json(f"{url}/select", payload)
        assert excinfo.value.code == 400


class TestPerAccountUsage:
    def test_each_key_shows_its_own_usage_not_the_omp_snapshot(
        self, account_server, monkeypatch
    ):
        _, path = account_server
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_usage_locks", {})
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
                                "amount": {"used": 0, "limit": 14, "unit": "credits"},
                                "window": {"resetsAt": 0},
                            }
                        ],
                    }
                ]
            },
        )
        calls: list[tuple[str, str | None]] = []

        def fake_fetch(provider_id, key, **kwargs):
            calls.append((provider_id, key))
            used = 0.0 if key == PRIMARY_CC else 7.31
            return limits.LimitSnapshot(
                provider=provider_id,
                fetched_at="2026-11-08T00:00:00+00:00",
                items=(limits.LimitItem("5-hour", used, 14.0, "credits", "5-hour"),),
                source="api",
            )

        monkeypatch.setattr(serve.limits, "fetch", fake_fetch)
        rows = [r for r in serve.services_snapshot(path) if r["provider"] == "commandcode"]
        assert [r["account"] for r in rows] == ["original", "joesacct1"]
        assert rows[0]["usage"] == "5-hour 0/14 cr"
        assert rows[1]["usage"] == "5-hour 7.31/14 cr"
        assert [(m["label"], m["percent"]) for m in rows[0]["meters"]] == [("5-hour", 0)]
        assert [(m["label"], m["percent"]) for m in rows[1]["meters"]] == [("5-hour", 52)]
        assert rows[1]["meters"][0]["detail"] == "7.31 / 14 cr"
        assert calls == [("commandcode", PRIMARY_CC), ("commandcode", ALT_CC)]

    def test_switching_selection_does_not_move_usage(self, account_server, monkeypatch):
        _, path = account_server
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_usage_locks", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(serve, "_run_omp_usage", dict)

        def fake_fetch(provider_id, key, **kwargs):
            used = 0.0 if key == PRIMARY_CC else 7.31
            return limits.LimitSnapshot(
                provider=provider_id,
                fetched_at="2026-11-08T00:00:00+00:00",
                items=(limits.LimitItem("5-hour", used, 14.0, "credits", "5-hour"),),
                source="api",
            )

        monkeypatch.setattr(serve.limits, "fetch", fake_fetch)
        before = [r["usage"] for r in serve.services_snapshot(path) if r["provider"] == "commandcode"]
        assert (
            cli.main(
                [
                    "account", "select", "commandcode", "joesacct1",
                    "--registry", str(path),
                ]
            )
            == 0
        )
        after = [r for r in serve.services_snapshot(path) if r["provider"] == "commandcode"]
        assert [r["usage"] for r in after] == before
        assert [r["selected"] for r in after] == [False, True]


class TestQuotedCommands:
    def test_apply_commands_quote_the_url(self, account_server, monkeypatch):
        """`&` is a shell operator in bash and PowerShell; the URL must be quoted."""
        _, path = account_server
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(serve, "_run_omp_usage", dict)
        html = serve.render_index_html("localhost:8930", path)
        assert (
            "curl -fsSL 'http://localhost:8930/config.sh?memory=on"
            "&amp;accounts=commandcode=original' | sh" in html
        )
        assert (
            "irm 'http://localhost:8930/config.ps1?memory=on"
            "&amp;accounts=commandcode=original' | iex" in html
        )
        # The JS rebuild path quotes too (textContent shows raw `&`).
        assert "\"irm '\" + psUrl + \"' | iex\"" in html
        assert "\"curl -fsSL '\" + shUrl + \"' | sh\"" in html


class TestUsageBars:
    def test_page_renders_a_bar_per_key_with_percent(self, account_server, monkeypatch):
        _, path = account_server
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_usage_locks", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(serve, "_run_omp_usage", dict)

        def fake_fetch(provider_id, key, **kwargs):
            used = 0.0 if key == PRIMARY_CC else 7.0
            return limits.LimitSnapshot(
                provider=provider_id,
                fetched_at="2026-11-08T00:00:00+00:00",
                items=(limits.LimitItem("5-hour", used, 14.0, "credits", "5-hour"),),
                source="api",
            )

        monkeypatch.setattr(serve.limits, "fetch", fake_fetch)
        html = serve.render_index_html("localhost:8930", path)
        assert 'class="bar"' in html
        assert "50% used" in html
        assert "width:50%" in html
        assert "7 / 14 cr" in html
        assert 'class="fill ok"' in html


class TestAccountPinnedCommands:
    def test_render_config_honors_account_override(self, account_server):
        _, path = account_server
        body = serve.render_config(path, "bash", accounts="commandcode=joesacct1")
        assert f"COMMAND_CODE_API_KEY={ALT_CC}" in body
        assert PRIMARY_CC not in body
        assert json.loads(path.read_text(encoding="utf-8"))["subscriptions"][0][
            "selected_account"
        ] == ""

    def test_config_sh_query_pins_account_without_persisting(self, account_server):
        url, path = account_server
        body = urllib.request.urlopen(
            f"{url}/config.sh?accounts=commandcode=joesacct1", timeout=5
        ).read().decode("utf-8")
        assert f"COMMAND_CODE_API_KEY={ALT_CC}" in body
        assert json.loads(path.read_text(encoding="utf-8"))["subscriptions"][0][
            "selected_account"
        ] == ""

    @pytest.mark.parametrize(
        "query",
        ["accounts=commandcode", "accounts=commandcode%3Dghost", "accounts=%3Dx"],
    )
    def test_bad_account_query_is_400(self, account_server, query):
        url, _ = account_server
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"{url}/config.sh?{query}", timeout=5)
        assert excinfo.value.code == 400

    def test_page_command_names_the_selected_account(self, account_server, monkeypatch):
        _, path = account_server
        monkeypatch.setattr(serve, "_usage_cache", {})
        monkeypatch.setattr(serve, "_omp_cache", {})
        monkeypatch.setattr(serve, "_run_omp_usage", dict)
        html = serve.render_index_html("localhost:8930", path)
        assert "accountsParam" in html
        assert "accounts=commandcode=original" in html
        assert (
            cli.main(
                [
                    "account", "select", "commandcode", "joesacct1",
                    "--registry", str(path),
                ]
            )
            == 0
        )
        html = serve.render_index_html("localhost:8930", path)
        assert "accounts=commandcode=joesacct1" in html
