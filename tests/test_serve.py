"""Tests for the LAN config server (mar serve)."""

import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from mar import cli, serve


@pytest.fixture()
def registry_path(tmp_path):
    path = tmp_path / "registry.json"
    cli.main(["add", "zai", "--key", "zai-key-1234567890", "--registry", str(path)])
    return path


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

    def test_fish_payload_uses_set_gx(self, registry_path):
        body = serve.render_config(registry_path, "fish")
        assert "set -gx ZAI_API_KEY zai-key-1234567890" in body
        assert "set -gx PI_STRICT_EDIT_MODE 1" in body
        assert "fish_add_path ~/.local/bin" in body
        assert "omp config set edit.mode hashline" in body
        assert "BEGIN" not in body

    def test_empty_registry_renders_empty_payload(self, tmp_path):
        assert serve.render_config(tmp_path / "registry.json", "bash") == ""

    def test_empty_registry_fish_renders_empty_payload(self, tmp_path):
        assert serve.render_config(tmp_path / "registry.json", "fish") == ""


class TestLanIps:
    def test_returns_at_least_one_ip(self):
        ips = serve.lan_ips()
        assert len(ips) >= 1
        assert all("." in ip for ip in ips)


class TestHttpServer:
    @pytest.fixture()
    def server(self, registry_path):
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

    def test_config_fish_served(self, server):
        status, body = self._get(f"{server}/config.fish")
        assert status == 200
        assert "set -gx ZAI_API_KEY zai-key-1234567890" in body

    def test_empty_registry_config_sh_is_200_empty(self, tmp_path):
        httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), serve.make_handler(tmp_path / "registry.json")
        )
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            status, body = self._get(f"http://127.0.0.1:{httpd.server_address[1]}/config.sh")
        finally:
            httpd.shutdown()
        assert status == 200
        assert body == ""

    def test_index_shows_apply_command(self, server):
        status, body = self._get(f"{server}/")
        assert status == 200
        assert "config.sh | sh" in body

    def test_unknown_path_is_404(self, server):
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            self._get(f"{server}/nope")
        assert excinfo.value.code == 404
