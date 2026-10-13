"""MAR command line interface.

Wiring only: parses argv, calls domain modules, maps errors to exit codes
(0 ok, 1 runtime error, 2 usage). Business logic lives in mar.registry,
mar.providers, mar.limits, mar.omp, mar.bootstrap.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from mar import __version__, bootstrap, limits, omp, providers, registry, store, vault

PROVIDER_IDS = providers.ids()

_EXPORT_BEGIN = "# ================================================================ BEGIN =========================="
_EXPORT_END = "# ================================================================ END ============================"


def _backend_flag(parser: argparse.ArgumentParser) -> None:
    """Add the --backend selector to a registry-touching subcommand."""
    parser.add_argument(
        "--backend",
        choices=("file", "vault"),
        default=None,
        help="storage backend for this command (default: $MAR_BACKEND, then "
        "~/.mar/config.json, then file)",
    )


def _now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def _err(message: str) -> int:
    """Print an error line to stderr; runtime-error exit code."""
    print(f"mar: {message}", file=sys.stderr)
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mar",
        description="My AI Router — subscription/key tracker and omp setup assistant (not a proxy).",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p_add = sub.add_parser("add", help="register a subscription")
    p_add.add_argument("provider", choices=PROVIDER_IDS)
    p_add.add_argument(
        "--key", default=None, help="API key (prefer --key-stdin to keep it out of shell history)"
    )
    p_add.add_argument(
        "--key-stdin", action="store_true", help="read the API key from one stdin line"
    )
    p_add.add_argument("--method", choices=("api_key", "oauth"), default="api_key")
    p_add.add_argument(
        "--model", action="append", default=[], metavar="MODEL", help="available model (repeatable)"
    )
    p_add.add_argument(
        "--default",
        default=None,
        metavar="MODEL",
        help="default model (auto-added to --model list)",
    )
    p_add.add_argument("--status", choices=("active", "expired", "disabled"), default="active")
    p_add.add_argument("--notes", default="")
    p_add.add_argument("--registry", default=None, metavar="PATH")
    _backend_flag(p_add)
    p_add.set_defaults(func=_cmd_add)

    p_rm = sub.add_parser("remove", help="forget a subscription")
    p_rm.add_argument("provider")
    p_rm.add_argument("--registry", default=None, metavar="PATH")
    _backend_flag(p_rm)
    p_rm.set_defaults(func=_cmd_remove)

    p_ls = sub.add_parser("list", help="show subscriptions with masked keys")
    p_ls.add_argument("--json", action="store_true", dest="as_json")
    p_ls.add_argument("--registry", default=None, metavar="PATH")
    _backend_flag(p_ls)
    p_ls.set_defaults(func=_cmd_list)

    p_lim = sub.add_parser("limits", help="usage snapshot (API) or where to check manually")
    p_lim.add_argument("provider", nargs="?", choices=PROVIDER_IDS, default=None)
    p_lim.add_argument("--registry", default=None, metavar="PATH")
    _backend_flag(p_lim)
    p_lim.set_defaults(func=_cmd_limits)

    p_sync = sub.add_parser("omp-sync", help="write stored keys into an omp install")
    p_sync.add_argument("--agent-dir", default=None, metavar="PATH")
    p_sync.add_argument(
        "--set-role",
        action="append",
        default=[],
        metavar="ROLE=MODEL",
        help="set config.yml modelRoles entry, e.g. --set-role default=zai/glm-5.3-flash (repeatable)",
    )
    p_sync.add_argument("--registry", default=None, metavar="PATH")
    _backend_flag(p_sync)
    p_sync.set_defaults(func=_cmd_omp_sync)

    p_prompt = sub.add_parser("prompt", help="print the startup prompt for a fresh omp install")
    p_prompt.add_argument(
        "--custom",
        default=None,
        metavar="PATH",
        help="file whose content is appended as custom setup",
    )
    p_prompt.add_argument(
        "--output", default=None, metavar="PATH", help="write prompt to file instead of stdout"
    )
    p_prompt.add_argument("--registry", default=None, metavar="PATH")
    _backend_flag(p_prompt)
    p_prompt.set_defaults(func=_cmd_prompt)

    p_exp = sub.add_parser(
        "export",
        help="emit a zero-install pastable that configures omp on another machine",
    )
    p_exp.add_argument(
        "--shell",
        choices=("powershell", "bash", "fish"),
        default="powershell",
        help="target shell for the pastable (default: powershell)",
    )
    p_exp.add_argument(
        "--agent-dir",
        default=None,
        metavar="PATH",
        help="omp agent dir to read modelRoles from (default: this machine's)",
    )
    p_exp.add_argument(
        "--redact",
        action="store_true",
        help="mask key values in the pastable (preview only; the result is NOT appliable)",
    )
    p_exp.add_argument(
        "--output", default=None, metavar="PATH", help="write pastable to file instead of stdout"
    )
    p_exp.add_argument("--registry", default=None, metavar="PATH")
    _backend_flag(p_exp)
    p_exp.set_defaults(func=_cmd_export)

    p_pull = sub.add_parser(
        "pull",
        help="adopt OAuth logins from the local omp install into the registry",
    )
    p_pull.add_argument("--agent-dir", default=None, metavar="PATH")
    p_pull.add_argument("--registry", default=None, metavar="PATH")
    _backend_flag(p_pull)
    p_pull.set_defaults(func=_cmd_pull)

    p_serve = sub.add_parser(
        "serve",
        help="serve config payloads over LAN HTTP (curl http://<host>/config.sh | sh)",
    )
    p_serve.add_argument("--port", type=int, default=8930, metavar="N")
    p_serve.add_argument("--host", default="0.0.0.0", metavar="ADDR")
    p_serve.add_argument("--registry", default=None, metavar="PATH")
    _backend_flag(p_serve)
    p_serve.set_defaults(func=_cmd_serve)
    p_rot = sub.add_parser(
        "rotate",
        help="replace a provider's key in place, keeping models and settings",
    )
    p_rot.add_argument("provider", choices=PROVIDER_IDS)
    p_rot.add_argument("--key", default=None, help="new API key (prefer --key-stdin)")
    p_rot.add_argument(
        "--key-stdin", action="store_true", help="read the new key from one stdin line"
    )
    p_rot.add_argument("--registry", default=None, metavar="PATH")
    _backend_flag(p_rot)
    p_rot.set_defaults(func=_cmd_rotate)

    p_cfg = sub.add_parser("config", help="show or change storage settings")
    cfg_sub = p_cfg.add_subparsers(dest="config_command", required=True, metavar="action")

    p_show = cfg_sub.add_parser("show", help="print the effective storage config (never secrets)")
    p_show.add_argument("--json", action="store_true", dest="as_json")
    p_show.set_defaults(func=_cmd_config_show)

    p_be = cfg_sub.add_parser("backend", help="select the storage backend")
    p_be.add_argument("backend", choices=("file", "vault"), help="backend to select")
    p_be.add_argument(
        "--addr", default=None, metavar="URL", help="vault address (or export VAULT_ADDR)"
    )
    p_be.add_argument(
        "--mount", default=None, metavar="NAME", help="KV-v2 mount point (default secret)"
    )
    p_be.add_argument(
        "--path", default=None, metavar="PATH", help="secret path prefix (default mar/keys)"
    )
    p_be.add_argument(
        "--token-file",
        default=None,
        metavar="PATH",
        help="file holding the vault token, read at use time",
    )
    p_be.add_argument(
        "--token-env",
        default=None,
        metavar="VAR",
        help="env var holding the token (default VAULT_TOKEN)",
    )
    p_be.add_argument(
        "--import",
        action="store_true",
        dest="do_import",
        help="copy the current file registry into the new backend before switching",
    )
    p_be.set_defaults(func=_cmd_config_backend)

    return parser


def _cmd_serve(args: argparse.Namespace) -> int:
    """Run the LAN config server until interrupted. Returns 0 on clean stop."""
    from mar import serve

    reg_path = Path(args.registry) if args.registry else registry.default_path()
    backend = None if args.registry else getattr(args, "backend", None)
    print(f"MAR config server on http://{args.host}:{args.port}")
    for ip in serve.lan_ips():
        print(f"  apply on a box: curl -fsSL http://{ip}:{args.port}/config.sh | sh")
    print("PowerShell targets: irm http://<this-host>/config.ps1 | iex")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "WARNING: /config.* payloads carry live credentials over plaintext "
            "HTTP; anyone who can reach this port can read them — bind to a "
            "trusted network or 127.0.0.1 only.",
            file=sys.stderr,
        )
    print("Fish targets: curl -fsSL http://<this-host>/config.fish | fish")
    print("Ctrl+C to stop.")
    serve.serve(args.host, args.port, reg_path, backend=backend)
    return 0


def _load(args: argparse.Namespace) -> registry.Registry:
    """Load registry from explicit path or the configured backend."""
    return registry.Registry.load(
        Path(args.registry) if args.registry else None,
        backend=getattr(args, "backend", None),
    )


def _cmd_add(args: argparse.Namespace) -> int:
    key = sys.stdin.readline().strip() if args.key_stdin else args.key
    if args.method == "api_key" and not key:
        return _err("api_key auth requires --key or --key-stdin")
    models = list(args.model)
    if args.default and args.default not in models:
        models.append(args.default)
    reg = _load(args)
    reg.add(
        registry.Subscription(
            provider=args.provider,
            auth_method=args.method,
            key=key,
            models=models,
            default_model=args.default,
            added_at=_now(),
            status=args.status,
            notes=args.notes,
        )
    )
    reg.save()
    print(f"added {args.provider} (key: {registry.mask(key)})")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    reg = _load(args)
    reg.remove(args.provider)
    reg.save()
    print(f"removed {args.provider}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    reg = _load(args)
    subs = reg.all()
    if args.as_json:
        rows = [
            {
                "provider": s.provider,
                "status": s.status,
                "auth_method": s.auth_method,
                "default_model": s.default_model,
                "key": registry.mask(s.key),
            }
            for s in subs
        ]
        print(json.dumps(rows, indent=2))
        return 0
    if not subs:
        print("no subscriptions")
        return 0
    for s in subs:
        default = s.default_model or "-"
        print(
            f"{s.provider:<14} {s.status:<9} {s.auth_method:<8} default={default:<20} key={registry.mask(s.key)}"
        )
    return 0


def _cmd_limits(args: argparse.Namespace) -> int:
    reg = _load(args)
    targets = [args.provider] if args.provider else [s.provider for s in reg.active()]
    if not targets:
        return _err("no active subscriptions; run: mar add <provider> --key-stdin")
    exit_code = 0
    for pid in targets:
        try:
            sub = reg.get(pid)
        except KeyError:
            sub = None
        if pid in limits.supported():
            if sub is None or not sub.key:
                print(
                    f"mar: {pid}: no api key stored; run: mar add {pid} --key-stdin",
                    file=sys.stderr,
                )
                exit_code = 1
                continue
            try:
                snap = limits.fetch(pid, sub.key)
            except limits.LimitError as exc:
                print(f"mar: {exc}", file=sys.stderr)
                exit_code = 1
                continue
            print(f"{pid} ({snap.source}):")
            if snap.note:
                print(f"  {snap.note}")
            for item in snap.items:
                used = "?" if item.used is None else item.used
                cap = "?" if item.limit is None else item.limit
                print(f"  {item.name}: {used} / {cap} {item.unit} ({item.window})")
        else:
            try:
                print(limits.manual_hint(pid))
            except limits.LimitError as exc:
                print(f"mar: {exc}", file=sys.stderr)
                exit_code = 1
    return exit_code


def _cmd_omp_sync(args: argparse.Namespace) -> int:
    reg = _load(args)
    roles: dict[str, str] = {}
    for spec in args.set_role:
        if "=" not in spec:
            print(f"mar: --set-role expects ROLE=MODEL, got {spec!r}", file=sys.stderr)
            return 2
        role, model = spec.split("=", 1)
        roles[role] = model
    report = bootstrap.sync(
        reg,
        agent_dir=Path(args.agent_dir) if args.agent_dir else None,
        roles=roles or None,
    )
    for written in report.written:
        print(f"written: {written}")
    for provider, reason in report.skipped:
        print(f"skipped: {provider} ({reason})")
    return 0


def _cmd_prompt(args: argparse.Namespace) -> int:
    reg = _load(args)
    text = bootstrap.render_prompt(reg, custom_path=Path(args.custom) if args.custom else None)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    reg = _load(args)
    pairs: list[tuple[str, str]] = []
    skipped: list[str] = []
    oauth_used = False
    oauth_expiries: list[tuple[str, int]] = []
    for sub in reg.active():
        if sub.auth_method == "oauth":
            try:
                provider = providers.get(sub.provider)
            except KeyError:
                skipped.append(f"skipped {sub.provider}: unknown provider (not in catalog)")
                continue
            if not sub.key:
                skipped.append(f"skipped {sub.provider}: oauth token missing; run mar pull")
                continue
            if not provider.export_oauth_env_var:
                skipped.append(
                    f"skipped {sub.provider}: no oauth env route in omp; "
                    f"run /login {provider.omp_provider_id} there"
                )
                continue
            pairs.append((provider.export_oauth_env_var, sub.key))
            oauth_used = True
            oauth_expiries.append((sub.provider, sub.expires_at_ms))
            continue
        if sub.auth_method != "api_key" or not sub.key:
            skipped.append(f"skipped {sub.provider}: no api key stored")
            continue
        try:
            provider = providers.get(sub.provider)
        except KeyError:
            skipped.append(f"skipped {sub.provider}: unknown provider (not in catalog)")
            continue
        if not provider.export_env_var:
            skipped.append(
                f"skipped {sub.provider}: no env route in omp; "
                f"run /login {provider.omp_provider_id} there"
            )
            continue
        pairs.append((provider.export_env_var, sub.key))
    if not pairs:
        return _err("nothing to export: no active subscriptions with exportable api keys")
    redact = getattr(args, "redact", False)
    lines = [f"{var}={registry.mask(key) if redact else key}" for var, key in pairs]
    notes = [
        f"exported {len(pairs)} credential(s) ({args.shell}); then run: omp usage / omp --yolo"
    ]
    if redact:
        notes.append("REDACTED: key values are masked — preview only, NOT appliable")
    if oauth_used:
        notes.append(
            "includes OAuth tokens: they EXPIRE; re-run `mar pull && mar export` to refresh"
        )
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    for provider_id, exp_ms in oauth_expiries:
        if exp_ms and exp_ms <= now_ms:
            notes.append(
                f"WARNING {provider_id}: token ALREADY EXPIRED — "
                "re-run `mar pull && mar export` before pasting"
            )
        elif exp_ms and exp_ms < now_ms + 2 * 3_600_000:
            notes.append(
                f"{provider_id}: token expires in {(exp_ms - now_ms) // 60_000} min — "
                "re-pull before it dies"
            )
    notes.extend(skipped)

    # Pass through non-credential env lines from the local agent .env (e.g.
    # LM_STUDIO_BASE_URL for a LAN LM Studio). Provider credential vars are
    # excluded — the registry is authoritative for those and stale local
    # copies must not leak or shadow rotated keys. Policy env vars ship
    # statically below and are also skipped to avoid duplicates.
    provider_env_vars = {
        var
        for p in providers.CATALOG
        for var in (p.omp_env_var, p.export_env_var, p.export_oauth_env_var)
        if var
    } | {"PI_STRICT_EDIT_MODE"}
    source_agent_dir = Path(args.agent_dir) if args.agent_dir else omp.default_agent_dir()
    extra_lines: list[str] = []
    local_env = source_agent_dir / ".env"
    if local_env.exists():
        for raw in local_env.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name = line.split("=", 1)[0].strip().removeprefix("export ").strip()
            if name and name not in provider_env_vars:
                extra_lines.append(line)
        if extra_lines:
            lines.extend(extra_lines)
            notes.append(
                "carried local env override(s): "
                + ", ".join(sorted(line.split("=", 1)[0] for line in extra_lines))
            )

    role_lines: list[str] = []
    roles: dict[str, str] = {}
    try:
        roles = omp.read_model_roles(
            Path(args.agent_dir) if args.agent_dir else omp.default_agent_dir()
        )
    except omp.OmpError as exc:
        notes.append(f"model roles not exported: {exc}")
    if roles:
        role_lines = [f"  {role}: {model}" for role, model in roles.items()]
        notes.append(
            "role-set for "
            + ", ".join(roles)
            + " (config.yml; skipped there if modelRoles already exists)"
        )

    # Edit policy: hashline everywhere; deepseek explicitly pinned (other
    # agent stacks silently downgrade deepseek to a weaker edit format).
    settings_lines = [
        "omp config set edit.mode hashline",
        'omp config set edit.modelVariants \'{"deepseek":"hashline"}\'',
    ]
    notes.append(
        "edit policy: hashline strict (PI_STRICT_EDIT_MODE=1; model-family downgrades disabled)"
    )

    # statusLine cloned from this machine so fresh boxes keep the hostname
    # segment in the prompt display.
    try:
        status_line = omp.read_status_line(
            Path(args.agent_dir) if args.agent_dir else omp.default_agent_dir()
        )
    except omp.OmpError as exc:
        status_line = {}
        notes.append(f"statusLine not exported: {exc}")
    if status_line:
        if "preset" in status_line:
            settings_lines.append(f"omp config set statusLine.preset {status_line['preset']}")
        for key in ("leftSegments", "rightSegments"):
            if key in status_line:
                value = json.dumps(status_line[key], separators=(",", ":"))
                settings_lines.append(f"omp config set statusLine.{key} '{value}'")
        notes.append("statusLine cloned (hostname stays in the prompt)")
    else:
        notes.append("statusLine not exported: not set on this machine")

    # omp's curl|sh installer drops the binary in ~/.local/bin, which is not
    # on PATH on many minimal Linux setups. bash only — bashrc is a bash file.
    path_lines: list[str] = []
    if args.shell == "bash":
        path_lines = [
            (
                'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; '
                '*) export PATH="$HOME/.local/bin:$PATH" ;; esac'
            ),
            (
                "grep -qs '\\.local/bin' \"$HOME/.bashrc\" || "
                'printf \'\\nexport PATH="$HOME/.local/bin:$PATH"\\n\' >> "$HOME/.bashrc"'
            ),
        ]
        notes.append("PATH: ~/.local/bin ensured for this session and .bashrc")

    if args.shell == "fish":
        env_lines = [*lines, "PI_STRICT_EDIT_MODE=1"]
        body = "\n".join(
            [
                "mkdir -p ~/.omp/agent",
                "printf '%s\\n' "
                + " ".join(f"'{line}'" for line in env_lines)
                + " > ~/.omp/agent/.env",
                *(
                    f"set -gx {name} {value}"
                    for name, _, value in (l.partition("=") for l in env_lines)
                ),
                "fish_add_path ~/.local/bin",
            ]
        )
        notes.append("PATH: ~/.local/bin ensured via fish_add_path")
        if role_lines:
            roles_text = "\\n".join(["modelRoles:", *role_lines])
            body = "\n".join(
                [
                    body,
                    'set cfg "$HOME/.omp/agent/config.yml"',
                    'if not test -f "$cfg"',
                    f"    printf '{roles_text}\\n' > \"$cfg\"",
                    "else if not grep -q '^modelRoles:' \"$cfg\"",
                    f"    printf '\\n{roles_text}\\n' >> \"$cfg\"",
                    "end",
                ]
            )
        body = "\n".join([body, *settings_lines])
    elif args.shell == "powershell":
        body = "\n".join(
            [
                'New-Item -ItemType Directory -Force "$env:USERPROFILE\\.omp\\agent" | Out-Null',
                "@'",
                *lines,
                "PI_STRICT_EDIT_MODE=1",
                '\'@ | Set-Content "$env:USERPROFILE\\.omp\\agent\\.env" -Encoding ascii',
            ]
        )
        if role_lines:
            body = "\n".join(
                [
                    body,
                    '$cfg = "$env:USERPROFILE\\.omp\\agent\\config.yml"',
                    "$roles = @'",
                    "modelRoles:",
                    *role_lines,
                    "'@",
                    "if (-not (Test-Path $cfg)) { Set-Content $cfg $roles -Encoding ascii }",
                    "if ((Test-Path $cfg) -and -not (Select-String -Path $cfg -Pattern '(?m)^modelRoles:' -Quiet)) { if (-not (Get-Content $cfg -Raw).EndsWith(\"`n\")) { Add-Content $cfg '' }; Add-Content $cfg $roles }",
                ]
            )
        body = "\n".join([body, *settings_lines])
    else:
        body = "\n".join(
            [
                "mkdir -p ~/.omp/agent",
                "cat > ~/.omp/agent/.env <<'MAR_EOF'",
                *lines,
                "PI_STRICT_EDIT_MODE=1",
                "MAR_EOF",
            ]
        )
        if role_lines:
            roles_text = "\\n".join(["modelRoles:", *role_lines])
            body = "\n".join(
                [
                    body,
                    'cfg="$HOME/.omp/agent/config.yml"',
                    'if [ ! -f "$cfg" ]; then',
                    f"  printf '{roles_text}\\n' > \"$cfg\"",
                    "elif ! grep -q '^modelRoles:' \"$cfg\"; then",
                    f"  printf '\\n{roles_text}\\n' >> \"$cfg\"",
                    "fi",
                ]
            )
        body = "\n".join([body, *path_lines, *settings_lines])
    text = f"{_EXPORT_BEGIN}\n{body}\n{_EXPORT_END}\n"
    for note in notes:
        print(f"mar: {note}", file=sys.stderr)
    if args.output:
        out_path = Path(args.output)
        out_path.write_text(text, encoding="utf-8")
        try:
            os.chmod(out_path, 0o600)
        except OSError:
            pass
        print(f"wrote {args.output} (contains keys; keep it safe)")
    else:
        print(text, end="")
    return 0


def _cmd_pull(args: argparse.Namespace) -> int:
    agent_dir = Path(args.agent_dir) if args.agent_dir else omp.default_agent_dir()
    creds = omp.read_oauth_credentials(agent_dir)
    if not creds:
        return _err(f"no oauth credentials found in {agent_dir / 'agent.db'}")
    by_omp_id = {p.omp_provider_id: p for p in providers.CATALOG if p.omp_provider_id}
    reg = _load(args)
    pulled = 0
    seen: set[str] = set()
    for cred in creds:
        provider = by_omp_id.get(cred.provider)
        if provider is None:
            print(f"skipped: {cred.provider} (not in MAR catalog)", file=sys.stderr)
            continue
        if provider.id in seen:
            print(
                f"skipped: {cred.provider} (duplicate account; registry holds one per provider)",
                file=sys.stderr,
            )
            continue
        seen.add(provider.id)
        expires = ""
        if cred.expires_ms:
            expires = datetime.fromtimestamp(cred.expires_ms / 1000, UTC).strftime(
                "%Y-%m-%dT%H:%MZ"
            )
        notes = f"oauth via mar pull ({cred.email}"
        if cred.org_name:
            notes += f" @ {cred.org_name}"
        notes += ")"
        if expires:
            notes += f"; expires {expires}; re-pull to refresh"
        try:
            old = reg.get(provider.id)
        except KeyError:
            old = None
        else:
            reg.remove(provider.id)
        reg.add(
            registry.Subscription(
                provider=provider.id,
                auth_method="oauth",
                key=cred.access,
                models=old.models if old else [],
                default_model=old.default_model if old else None,
                added_at=_now(),
                status=old.status if old else "active",
                notes=notes,
                expires_at_ms=cred.expires_ms,
            )
        )
        pulled += 1
        print(f"pulled: {provider.id} ({cred.provider}) — expires {expires or 'unknown'}")
    reg.save()
    return 0 if pulled else 1


def _cmd_rotate(args: argparse.Namespace) -> int:
    key = sys.stdin.readline().strip() if args.key_stdin else args.key
    if not key:
        return _err("rotate requires --key or --key-stdin")
    reg = _load(args)
    reg.rotate(args.provider, key)
    reg.save()
    print(f"rotated {args.provider} (key: {registry.mask(key)})")
    return 0


def _cmd_config_show(args: argparse.Namespace) -> int:
    cfg = store.load_config()
    backend = os.environ.get("MAR_BACKEND", "").strip() or cfg.get("backend") or "file"
    env_note = " (from MAR_BACKEND)" if os.environ.get("MAR_BACKEND", "").strip() else ""
    if args.as_json:
        print(json.dumps({"backend": backend, "config": cfg}, indent=2))
        return 0
    print(f"backend: {backend}{env_note}")
    vault_cfg = cfg.get("vault") or {}
    if isinstance(vault_cfg, dict):
        for key in ("addr", "mount", "path", "token_file", "token_env"):
            if vault_cfg.get(key):
                print(f"vault.{key}: {vault_cfg[key]}")
    return 0


def _cmd_config_backend(args: argparse.Namespace) -> int:
    if args.backend == "file":
        store.save_config({})
        print("backend: file")
        return 0
    overrides = {
        "addr": args.addr,
        "mount": args.mount,
        "path": args.path,
        "token_file": args.token_file,
        "token_env": args.token_env,
    }
    if args.do_import:
        source = registry.Registry.load(Path(args.registry) if args.registry else None)
        target = vault.VaultStore(**store.resolve_vault_settings(overrides))
        destination = registry.Registry(store=target)
        for sub in source.all():
            destination.add(sub)
        destination.save()
        print(
            f"imported {len(source.all())} subscription(s) into vault at "
            f"{target.mount}/{target.path}"
        )
    settings = store.resolve_vault_settings(overrides)
    persisted = {key: value for key, value in overrides.items() if value}
    store.save_config({"backend": "vault", "vault": persisted})
    print(f"backend: vault ({settings['addr']}, {settings['mount']}/{settings['path']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns process exit code: 0 ok, 1 error, 2 usage."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--version", "-V"):
        print(f"mar {__version__}")
        return 0
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (
        registry.RegistryError,
        limits.LimitError,
        bootstrap.BootstrapError,
        omp.OmpError,
        store.StoreError,
        vault.VaultError,
    ) as exc:
        return _err(str(exc))
    except KeyError as exc:
        return _err(str(exc).strip("'\""))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
