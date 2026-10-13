"""LAN HTTP server that serves MAR's omp config payloads.

Endpoints (all GET, all text):
    /           — human-readable instructions
    /config.sh  — bash export payload (BEGIN/END markers stripped)
    /config.ps1 — powershell export payload (BEGIN/END stripped)

Payloads are generated fresh per request from the registry, so what a
machine fetches always matches the current ``mar export`` output — updated
configs on demand for boxes that already have omp installed. The server
never mutates anything and holds no state.
"""

from __future__ import annotations

import contextlib
import io
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mar import cli


def render_config(registry: Path, shell: str) -> str:
    """Render the ``mar export`` payload for one shell, minus the markers.

    Args:
        registry: Path to the MAR registry file.
        shell: ``"bash"`` or ``"powershell"``.

    Returns:
        The payload body exactly as ``mar export`` prints between the
        BEGIN/END markers, without the markers. Stderr notes (skips,
        warnings) are discarded — they are written for the human running
        MAR, not the machine piping this into a shell.

    Raises:
        SystemExit: Propagated from ``mar export`` on an empty registry
            (exit code 1); callers should treat it as an empty payload.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        cli.main(["export", "--shell", shell, "--registry", str(registry)])
    lines = [
        line
        for line in out.getvalue().splitlines()
        if not (line.startswith("# ===") and ("BEGIN" in line or "END" in line))
    ]
    return "\n".join(lines).strip("\n")


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


def make_handler(registry: Path) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to one registry path.

    Args:
        registry: Path to the MAR registry file, read per request.

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
                    render_config(registry, "bash"),
                    "text/x-shellscript; charset=utf-8",
                )
            elif self.path == "/config.ps1":
                self._send(
                    200,
                    render_config(registry, "powershell"),
                    "text/plain; charset=utf-8",
                )
            elif self.path == "/":
                ips = "\n".join(f"  http://{ip}" for ip in lan_ips())
                self._send(
                    200,
                    "\n".join(
                        [
                            "MAR config server (LAN only, plain HTTP)",
                            "",
                            "Apply the current config on a box with omp:",
                            f"  curl -fsSL http://{host}/config.sh | sh",
                            "",
                            "Windows box (PowerShell):",
                            "  irm http://<this-host>/config.ps1 | iex",
                            "",
                            "This server's addresses:",
                            ips,
                            "",
                        ]
                    ),
                    "text/plain; charset=utf-8",
                )
            else:
                self._send(404, "not found\n", "text/plain; charset=utf-8")

        def log_message(self, format: str, *args: object) -> None:
            """Quiet default per-request logging to keep the console clean."""

    return Handler


def serve(host: str, port: int, registry: Path) -> None:
    """Run the config server until interrupted.

    Args:
        host: Bind address (``0.0.0.0`` for LAN reachability).
        port: TCP port to listen on.
        registry: Path to the MAR registry file.

    Raises:
        OSError: If the port cannot be bound.
    """
    server = ThreadingHTTPServer((host, port), make_handler(registry))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
