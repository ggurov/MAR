"""LAN HTTP server that serves MAR's omp config payloads.

Endpoints (all GET, all text):
    /config.sh  — bash export payload (BEGIN/END markers stripped)
    /config.ps1 — powershell export payload (BEGIN/END stripped)
    /config.fish — fish payload (set -gx env lines + .env persistence)

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


def render_config(registry: Path, shell: str, backend: str | None = None) -> str:
    """Render the ``mar export`` payload for one shell, minus the markers.

    Args:
        registry: Path to the MAR registry file (read fresh per call; the
            file backend is forced when this path is used).
        shell: ``"bash"``, ``"powershell"``, or ``"fish"``.
        backend: Optional storage backend name passed through to the export
            command (``vault`` reads the registry from Vault instead).

    Returns:
        The payload body exactly as ``mar export`` prints between the
        BEGIN/END markers, without the markers. Stderr notes (skips,
        warnings) are discarded — they are written for the human running
        MAR, not the machine piping this into a shell.

    Notes:
        An empty registry renders as an empty payload — ``mar export``
        exits 1 with no output for an empty registry and stdout is what
        becomes the payload.
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
            when ``backend`` is None).
        backend: Optional storage backend name forwarded to the export
            command per request (``vault`` reads from Vault instead of the
            file).

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
            elif self.path == "/config.fish":
                self._send(
                    200,
                    render_config(registry, "fish"),
                    "text/x-fish; charset=utf-8",
                )
            elif self.path == "/config.ps1":
                self._send(
                    200,
                    render_config(registry, "powershell", backend),
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
                            "Fish box:",
                            f"  curl -fsSL http://{host}/config.fish | fish",
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


def serve(host: str, port: int, registry: Path, backend: str | None = None) -> None:
    """Run the config server until interrupted.

    Args:
        host: Bind address (``0.0.0.0`` for LAN reachability).
        port: TCP port to listen on.
        registry: Path to the MAR registry file (file backend).
        backend: Optional storage backend name (see ``make_handler``).

    Raises:
        OSError: If the port cannot be bound.
    """
    server = ThreadingHTTPServer((host, port), make_handler(registry, backend=backend))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
