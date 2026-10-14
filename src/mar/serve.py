"""LAN HTTP server that serves MAR's omp config payloads and service status.

Endpoints (all GET):
    /           — HTML page: apply commands with copy buttons, configured
                  services with quotas/renewals/usage links, repo link
    /config.sh  — bash export payload (BEGIN/END markers stripped)
    /config.ps1 — powershell export payload (BEGIN/END stripped)

Payloads are generated fresh per request from the registry, so what a
machine fetches always matches the current ``mar export`` output — updated
configs on demand for boxes that already have omp installed. The server
never mutates anything.
"""

from __future__ import annotations

import contextlib
import html as html_mod
import io
import json
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mar import cli, limits, providers

REPO_URL = "https://github.com/ggurov/MAR"
_USAGE_TTL_SECONDS = 60.0
_usage_cache: dict[str, tuple[float, str]] = {}


def render_config(registry: Path, shell: str, backend: str | None = None) -> str:
    """Render the ``mar export`` payload for one shell, minus the markers.

    Args:
        registry: Path to the MAR registry file.
        shell: ``"bash"`` or ``"powershell"``.
        backend: Optional storage backend name; when given it is passed
            through, otherwise the file backend is forced via the path.

    Returns:
        The payload body exactly as ``mar export`` prints between the
        BEGIN/END markers, without the markers. Stderr notes (skips,
        warnings) are discarded — they are written for the human running
        MAR, not the machine piping this into a shell.

    Raises:
        SystemExit: Propagated from ``mar export`` on an empty registry.
    """
    out, err = io.StringIO(), io.StringIO()
    argv = ["export", "--shell", shell]
    if backend:
        argv += ["--backend", backend]
    else:
        argv += ["--registry", str(registry)]
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        cli.main(argv)
    lines = [
        line
        for line in out.getvalue().splitlines()
        if not (line.startswith("# ===") and ("BEGIN" in line or "END" in line))
    ]
    return "\n".join(lines).strip("\n")


def _usage_cell(provider: str, key: str | None) -> str | None:
    """Return the quota text for one provider, caching live fetches.

    Only OpenRouter has a shipped fetcher; every other provider yields
    ``None`` here and relies on the usage-page link instead.

    Args:
        provider: Catalog id.
        key: The provider credential used for the live fetch.

    Returns:
        Human quota text, e.g. ``"$315.00 left"``; ``None``
        when the provider has no live fetcher. Fetch failures degrade to
        ``"unavailable"``.
    """
    if provider != "openrouter":
        return None
    cached = _usage_cache.get(provider)
    if cached is not None and time.monotonic() - cached[0] < _USAGE_TTL_SECONDS:
        return cached[1]
    try:
        snapshot = limits.fetch(provider, key or "", timeout=5.0)
        item = snapshot.items[0]
        if item.limit is not None:
            cell = f"${item.limit - (item.used or 0):.2f} left"
        else:
            cell = f"${item.used or 0:.2f} used"
    except Exception:  # noqa: BLE001 — a dead usage API must never 500 the page
        cell = "unavailable"
    _usage_cache[provider] = (time.monotonic(), cell)
    return cell


_OMP_TTL_SECONDS = 300.0
_omp_cache: dict = {}


def _run_omp_usage() -> dict:
    """Invoke ``omp usage --json`` and return the parsed document.

    Returns:
        The ``omp usage --json`` document (``reports`` keyed by provider).

    Raises:
        OSError: If ``omp`` cannot be executed.
        ValueError: If the output is not JSON.
    """

    done = subprocess.run(
        ["omp", "usage", "--json"],
        capture_output=True,
        text=True,
        timeout=150,
        check=True,
    )
    return json.loads(done.stdout)


def _omp_usage() -> dict[str, list]:
    """Return omp's usage reports, cached for :data:`_OMP_TTL_SECONDS`.

    Stale data is served when a refresh fails — a dead CLI must never
    break the page.

    Returns:
        Mapping of omp provider id to its ``limits`` list (possibly empty).
    """
    now = time.monotonic()
    if _omp_cache.get("ts") is not None and now - _omp_cache["ts"] < _OMP_TTL_SECONDS:
        return _omp_cache.get("data", {})
    try:
        data = _run_omp_usage()
        reports = {r["provider"]: r.get("limits", []) for r in data.get("reports", [])}
    except Exception:  # noqa: BLE001 — degrade to the last good snapshot
        reports = _omp_cache.get("data", {})
    _omp_cache["ts"] = now
    _omp_cache["data"] = reports
    return reports


def _omp_id_map() -> dict[str, str]:
    """Map catalog provider id → omp provider id (when one exists)."""
    return {
        p.id: p.omp_provider_id for p in providers.CATALOG if p.omp_provider_id
    }


def _format_limits(items: list[dict]) -> tuple[str | None, int | None]:
    """Format omp usage limits into one quota cell plus earliest reset.

    Args:
        items: The ``limits`` list of one ``omp usage`` report.

    Returns:
        ``(cell, earliest_reset_ms)`` where ``cell`` is like
        ``"5h 100% · 7d 40%"`` (``None`` when nothing is quantified) and
        ``earliest_reset_ms`` is the soonest window reset (``None`` when
        no window reports one).
    """
    parts: list[str] = []
    earliest: int | None = None
    for item in items:
        amount = item.get("amount") or {}
        remaining, cap, unit = amount.get("remaining"), amount.get("limit"), (amount.get("unit") or "")
        window = item.get("window") or {}
        reset = window.get("resetsAt")
        if reset is not None and (earliest is None or reset < earliest):
            earliest = int(reset)
        if remaining is None and cap is None:
            continue
        label = item.get("id", "?").rsplit(":", 1)[-1]
        if unit == "percent":
            parts.append(f"{label} {(remaining or 0):.0f}%")
        elif unit == "usd":
            parts.append(f"{label} ${(remaining or 0):.2f}")
        else:
            suffix = "cr" if unit.startswith("cre") else ("req" if unit.startswith("req") else unit)
            used = f"{remaining:g}" if remaining is not None else "\u2013"
            if cap:
                parts.append(f"{label} {used}/{cap:g} {suffix}".rstrip())
            else:
                parts.append(f"{label} {used} {suffix}".rstrip())
    return (" \u00b7 ".join(parts) if parts else None), earliest


def _format_renew(hours: float) -> str:
    """Format an oauth token's remaining lifetime for the Renews cell.

    Args:
        hours: Hours until the token expires; negative once past.

    Returns:
        ``EXPIRED`` when ``hours <= 0``; ``in 7.9h`` under 48 hours;
        ``in ~N days`` under a year; ``""`` for longer-lived tokens,
        whose expiry is far enough out to read as non-expiring.
    """
    if hours <= 0:
        return "EXPIRED"
    if hours < 48:
        return f"in {hours:.1f}h"
    if hours < 8760:
        days = round(hours / 24)
        return f"in ~{days} day" if days == 1 else f"in ~{days} days"
    return ""

def services_snapshot(registry: Path) -> list[dict[str, str | None]]:
    """Collect one row per active subscription for the services panel.

    Args:
        registry: Path to the MAR registry file (file backend).

    Returns:
        One dict per active subscription with keys ``provider``,
        ``auth``, ``usage`` (quota text from ``omp usage`` windows — with
        a trailing ``resets in X.Xh`` / ``resets EXPIRED`` for the
        soonest window reset — or the OpenRouter credits fetch; ``None``
        when a provider reports neither), ``renew`` (oauth token expiry
        only: ``in 7.9h`` under 48 hours, ``in ~N days`` under a year,
        ``""`` for longer-lived tokens, ``EXPIRED`` once past; ``—``
        for api keys and rows without an expiry), and ``limits_url``.
    """
    rows: list[dict[str, str | None]] = []
    reg = cli.registry.Registry.load(registry)
    id_map = _omp_id_map()
    usage_map = _omp_usage()
    for sub in reg.active():
        try:
            limits_url = providers.get(sub.provider).limits_url
        except KeyError:
            limits_url = ""
        omp_limits = usage_map.get(id_map.get(sub.provider, ""), [])
        quota, earliest_reset = _format_limits(omp_limits)
        if quota is None and sub.provider == "openrouter":
            quota = _usage_cell(sub.provider, sub.key)
        if earliest_reset is not None:
            hours = (earliest_reset / 1000 - time.time()) / 3600
            resets = f"resets in {hours:.1f}h" if hours > 0 else "resets EXPIRED"
            quota = f"{quota} \u00b7 {resets}" if quota else resets
        if sub.auth_method == "oauth" and sub.expires_at_ms:
            hours = (sub.expires_at_ms / 1000 - time.time()) / 3600
            renew = _format_renew(hours)
        else:
            renew = "\u2014"
        rows.append(
            {
                "provider": sub.provider,
                "auth": sub.auth_method,
                "usage": quota,
                "renew": renew,
                "limits_url": limits_url,
            }
        )
    return rows


def render_index_html(host: str, registry: Path) -> str:
    """Render the landing page for one reachable ``host:port``.

    Args:
        host: ``host:port`` as the client reached the server (Host header).
        registry: Path to the MAR registry file for the services panel.

    Returns:
        A complete HTML5 document: apply commands for sh and PowerShell
        with copy-to-clipboard buttons, the configured-services table
        (quota, renewal, provider usage link), a plaintext-HTTP warning, and the
        GitHub repo link.
    """
    sh_cmd = f"curl -fsSL http://{host}/config.sh | sh"
    ps_cmd = f"irm http://{host}/config.ps1 | iex"
    rows_html: list[str] = []
    for row in services_snapshot(registry):
        usage = html_mod.escape(row["usage"]) if row["usage"] is not None else "\u2014"
        name = html_mod.escape(row["provider"])
        if row["limits_url"]:
            provider = (
                f'<a href="{html_mod.escape(row["limits_url"])}" target="_blank"'
                f' rel="noopener"><b>{name}</b></a>'
            )
        else:
            provider = f"<b>{name}</b>"
        renew = html_mod.escape(row["renew"])
        if "EXPIRED" in renew:
            renew = f'<span class="expired">{renew}</span>'
        rows_html.append(
            f"<tr><td>{provider}</td>"
            f"<td>{row['auth']}</td>"
            f"<td class=\"mono\">{usage}</td>"
            f"<td>{renew}</td></tr>"
        )
    ips = "".join(f"<li><code>{ip}</code></li>" for ip in lan_ips())
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MAR config server</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; min-height:100vh; display:flex; justify-content:center;
         background:#0d1117; color:#e6edf3; font:15px/1.55 system-ui,-apple-system,sans-serif; }}
  main {{ width:100%; max-width:1300px; padding:36px 32px; }}
  h1 {{ font-size:19px; margin:0 0 2px; }}
  h1 span {{ color:#58a6ff; }}
  .sub {{ color:#8b949e; margin:0 0 22px; font-size:14px; }}
  .card {{ background:#161b22; border:1px solid #30363d; border-radius:10px;
           padding:16px 18px; margin-bottom:14px; }}
  .card h2 {{ font-size:12px; margin:0 0 10px; text-transform:uppercase;
              letter-spacing:.07em; color:#8b949e; }}
  code.cmd {{ display:block; background:#0d1117; border:1px solid #30363d; border-radius:6px;
              padding:10px 12px; font:13px/1.4 ui-monospace,Consolas,monospace;
              overflow-x:auto; white-space:nowrap; }}
  .row {{ display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }}
  button {{ background:#238636; color:#fff; border:0; border-radius:6px; padding:7px 14px;
            font:600 13px system-ui; cursor:pointer; }}
  button:hover {{ background:#2ea043; }}
  button.ghost {{ background:#21262d; border:1px solid #30363d; }}
  button.ghost:hover {{ background:#30363d; }}
  button.copied {{ background:#1f6feb; }}
  .note {{ font-size:13px; color:#8b949e; margin:8px 0 0; }}
  .warn {{ color:#d29922; }}
  .expired {{ color:#f85149; font-weight:600; }}
  a {{ color:#58a6ff; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
  th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid #21262d; }}
  th {{ color:#8b949e; font-weight:600; font-size:12px; text-transform:uppercase;
        letter-spacing:.05em; }}
  td.mono {{ font-family:ui-monospace,Consolas,monospace; }}
  ul {{ margin:6px 0 0; padding-left:18px; color:#8b949e; font-size:13px; }}
  footer {{ margin-top:20px; font-size:13px; color:#8b949e; }}
</style>
</head>
<body>
<main>
  <h1>MAR <span>·</span> config server</h1>
  <p class="sub">Pushes your provider keys, model roles, and omp policy to boxes on this LAN.
     Boxes must already have <strong>omp</strong> installed.</p>

  <div class="card">
    <h2>Linux / macOS</h2>
    <code class="cmd" id="sh-cmd">{sh_cmd}</code>
    <div class="row">
      <button onclick="copyEl('sh-cmd', this)">Copy command</button>
      <button class="ghost" onclick="copyFetch('/config.sh', this)">Copy payload</button>
    </div>
  </div>

  <div class="card">
    <h2>Windows (PowerShell)</h2>
    <code class="cmd" id="ps-cmd">{ps_cmd}</code>
    <div class="row">
      <button onclick="copyEl('ps-cmd', this)">Copy command</button>
      <button class="ghost" onclick="copyFetch('/config.ps1', this)">Copy payload</button>
    </div>
  </div>

  <div class="card">
    <h2>Services</h2>
    <table>
      <tr><th>Provider</th><th>Auth</th><th>Quota</th><th>Renews</th></tr>
      {''.join(rows_html)}
    </table>
    <p class="note"><span class="warn">⚠ Plain HTTP</span> — payloads carry live credentials.
       LAN only; the firewall rule scopes this port to LocalSubnet.</p>
    <p class="note">oauth renews by re-login — <code>omp login &lt;provider&gt;</code> on the
       box (omp stores the fresh token), then <code>mar pull</code> adopts it here. Don't
       switch to API keys: API billing is a separate tier from your subscription quota.</p>
  </div>

  <footer>
    <a href="{REPO_URL}">{REPO_URL}</a> · this server:
    <ul style="display:inline; padding:0; list-style:none">{ips}</ul>
  </footer>
</main>
<script>
async function copyText(t, btn) {{
  try {{ await navigator.clipboard.writeText(t); }}
  catch (e) {{
    const ta = document.createElement('textarea');
    ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  }}
  const old = btn.textContent;
  btn.textContent = 'Copied ✓'; btn.classList.add('copied');
  setTimeout(() => {{ btn.textContent = old; btn.classList.remove('copied'); }}, 1400);
}}
function copyEl(id, btn) {{ copyText(document.getElementById(id).textContent.trim(), btn); }}
async function copyFetch(path, btn) {{
  const r = await fetch(path);
  copyText(await r.text(), btn);
}}
</script>
</body>
</html>
"""


def lan_ips() -> list[str]:
    """Return this machine's LAN IPv4 addresses, best effort.

    Returns:
        At least one dotted-quad string. Uses the UDP-connect trick to
        find the primary outbound address; falls back to hostname
        resolution and finally loopback.
    """
    ips: list[str] = []
    with contextlib.suppress(OSError):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("10.255.255.255", 1))
            ips.append(sock.getsockname()[0])
        finally:
            sock.close()
    with contextlib.suppress(OSError):
        host_ip = socket.gethostbyname(socket.gethostname())
        if host_ip not in ips:
            ips.append(host_ip)
    if not ips:
        ips.append("127.0.0.1")
    return ips


def make_handler(registry: Path, backend: str | None = None) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to one registry source.

    Args:
        registry: Path to the MAR registry file, read per request (used
            unless a ``backend`` is given).
        backend: Optional storage backend name (see ``render_config``).

    Returns:
        A ``BaseHTTPRequestHandler`` subclass serving the endpoints.
    """

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str, content_type: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # stdlib naming convention
            host = self.headers.get("Host", "localhost")
            if self.path == "/config.sh":
                self._send(
                    200,
                    render_config(registry, "bash", backend),
                    "text/x-shellscript; charset=utf-8",
                )
            elif self.path == "/config.ps1":
                self._send(
                    200,
                    render_config(registry, "powershell", backend),
                    "text/plain; charset=utf-8",
                )
            elif self.path == "/":
                self._send(
                    200, render_index_html(host, registry), "text/html; charset=utf-8"
                )
            else:
                self._send(404, "not found\n", "text/plain; charset=utf-8")

        def log_message(self, format: str, *args: object) -> None:
            """Quiet default per-request logging to keep the console clean."""

    return Handler


def serve(host: str, port: int, registry: Path, backend: str | None = None) -> None:
    """Run the config server until interrupted.

    Args:
        host: Bind address (``0.0.0.0`` for LAN reachability).
        port: TCP port to listen on.
        registry: Path to the MAR registry file (file backend).
        backend: Optional storage backend name.

    Raises:
        OSError: If the port cannot be bound.
    """
    # Warm the omp usage cache in the background — the first page view
    # should not wait on a full provider sweep.
    threading.Thread(target=_omp_usage, daemon=True).start()
    server = ThreadingHTTPServer((host, port), make_handler(registry, backend))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
