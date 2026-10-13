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

from mar import __version__, bootstrap, limits, omp, providers, registry

PROVIDER_IDS = providers.ids()

_EXPORT_BEGIN = "# ================================================================ BEGIN =========================="
_EXPORT_END = "# ================================================================ END ============================"


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
    p_add.add_argument("--key", default=None, help="API key (prefer --key-stdin to keep it out of shell history)")
    p_add.add_argument("--key-stdin", action="store_true", help="read the API key from one stdin line")
    p_add.add_argument("--method", choices=("api_key", "oauth"), default="api_key")
    p_add.add_argument("--model", action="append", default=[], metavar="MODEL", help="available model (repeatable)")
    p_add.add_argument("--default", default=None, metavar="MODEL", help="default model (auto-added to --model list)")
    p_add.add_argument("--status", choices=("active", "expired", "disabled"), default="active")
    p_add.add_argument("--notes", default="")
    p_add.add_argument("--registry", default=None, metavar="PATH")
    p_add.set_defaults(func=_cmd_add)

    p_rm = sub.add_parser("remove", help="forget a subscription")
    p_rm.add_argument("provider")
    p_rm.add_argument("--registry", default=None, metavar="PATH")
    p_rm.set_defaults(func=_cmd_remove)

    p_ls = sub.add_parser("list", help="show subscriptions with masked keys")
    p_ls.add_argument("--json", action="store_true", dest="as_json")
    p_ls.add_argument("--registry", default=None, metavar="PATH")
    p_ls.set_defaults(func=_cmd_list)

    p_lim = sub.add_parser("limits", help="usage snapshot (API) or where to check manually")
    p_lim.add_argument("provider", nargs="?", choices=PROVIDER_IDS, default=None)
    p_lim.add_argument("--registry", default=None, metavar="PATH")
    p_lim.set_defaults(func=_cmd_limits)

    p_sync = sub.add_parser("omp-sync", help="write stored keys into an omp install")
    p_sync.add_argument("--agent-dir", default=None, metavar="PATH")
    p_sync.add_argument(
        "--set-role", action="append", default=[], metavar="ROLE=MODEL",
        help="set config.yml modelRoles entry, e.g. --set-role default=zai/glm-5.3-flash (repeatable)",
    )
    p_sync.add_argument("--registry", default=None, metavar="PATH")
    p_sync.set_defaults(func=_cmd_omp_sync)

    p_prompt = sub.add_parser("prompt", help="print the startup prompt for a fresh omp install")
    p_prompt.add_argument("--custom", default=None, metavar="PATH", help="file whose content is appended as custom setup")
    p_prompt.add_argument("--output", default=None, metavar="PATH", help="write prompt to file instead of stdout")
    p_prompt.add_argument("--registry", default=None, metavar="PATH")
    p_prompt.set_defaults(func=_cmd_prompt)

    p_exp = sub.add_parser(
        "export",
        help="emit a zero-install pastable that configures omp on another machine",
    )
    p_exp.add_argument(
        "--shell", choices=("powershell", "bash"), default="powershell",
        help="target shell for the pastable (default: powershell)",
    )
    p_exp.add_argument(
        "--agent-dir", default=None, metavar="PATH",
        help="omp agent dir to read modelRoles from (default: this machine's)",
    )
    p_exp.add_argument("--output", default=None, metavar="PATH", help="write pastable to file instead of stdout")
    p_exp.add_argument("--registry", default=None, metavar="PATH")
    p_exp.set_defaults(func=_cmd_export)

    p_pull = sub.add_parser(
        "pull",
        help="adopt OAuth logins from the local omp install into the registry",
    )
    p_pull.add_argument("--agent-dir", default=None, metavar="PATH")
    p_pull.add_argument("--registry", default=None, metavar="PATH")
    p_pull.set_defaults(func=_cmd_pull)

    return parser


def _load(path: str | None) -> registry.Registry:
    """Load registry from explicit path or default location."""
    return registry.Registry.load(Path(path) if path else None)


def _cmd_add(args: argparse.Namespace) -> int:
    key = sys.stdin.readline().strip() if args.key_stdin else args.key
    if args.method == "api_key" and not key:
        return _err("api_key auth requires --key or --key-stdin")
    models = list(args.model)
    if args.default and args.default not in models:
        models.append(args.default)
    reg = _load(args.registry)
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
    reg = _load(args.registry)
    reg.remove(args.provider)
    reg.save()
    print(f"removed {args.provider}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    reg = _load(args.registry)
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
        print(f"{s.provider:<14} {s.status:<9} {s.auth_method:<8} default={default:<20} key={registry.mask(s.key)}")
    return 0


def _cmd_limits(args: argparse.Namespace) -> int:
    reg = _load(args.registry)
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
                print(f"mar: {pid}: no api key stored; run: mar add {pid} --key-stdin", file=sys.stderr)
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
    reg = _load(args.registry)
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
    reg = _load(args.registry)
    text = bootstrap.render_prompt(reg, custom_path=Path(args.custom) if args.custom else None)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    reg = _load(args.registry)
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
    lines = [f"{var}={key}" for var, key in pairs]
    notes = [f"exported {len(pairs)} credential(s) ({args.shell}); then run: omp usage / omp --yolo"]
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
    notes.append("edit policy: hashline strict (PI_STRICT_EDIT_MODE=1; model-family downgrades disabled)")

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
            settings_lines.append(
                f"omp config set statusLine.preset {status_line['preset']}"
            )
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
                "printf '\\nexport PATH=\"$HOME/.local/bin:$PATH\"\\n' >> \"$HOME/.bashrc\""
            ),
        ]
        notes.append("PATH: ~/.local/bin ensured for this session and .bashrc")

    if args.shell == "powershell":
        body = "\n".join(
            [
                'New-Item -ItemType Directory -Force "$env:USERPROFILE\\.omp\\agent" | Out-Null',
                "@'",
                *lines,
                "PI_STRICT_EDIT_MODE=1",
                "'@ | Set-Content \"$env:USERPROFILE\\.omp\\agent\\.env\" -Encoding ascii",
            ]
        )
        if role_lines:
            body = "\n".join(
                [
                    body,
                    "$cfg = \"$env:USERPROFILE\\.omp\\agent\\config.yml\"",
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
                    "if [ ! -f \"$cfg\" ]; then",
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
    reg = _load(args.registry)
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
    except (registry.RegistryError, limits.LimitError, bootstrap.BootstrapError, omp.OmpError) as exc:
        return _err(str(exc))
    except KeyError as exc:
        return _err(str(exc).strip("'\""))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
