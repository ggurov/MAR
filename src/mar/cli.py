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
    p_exp.add_argument("--output", default=None, metavar="PATH", help="write pastable to file instead of stdout")
    p_exp.add_argument("--registry", default=None, metavar="PATH")
    p_exp.set_defaults(func=_cmd_export)

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
    for sub in reg.active():
        if sub.auth_method != "api_key" or not sub.key:
            skipped.append(f"#   {sub.provider}: no api key stored")
            continue
        try:
            provider = providers.get(sub.provider)
        except KeyError:
            skipped.append(f"#   {sub.provider}: unknown provider (not in catalog)")
            continue
        if not provider.export_env_var:
            skipped.append(
                f"#   {sub.provider}: no env route in omp; run /login {provider.omp_provider_id} there"
            )
            continue
        pairs.append((provider.export_env_var, sub.key))
    if not pairs:
        return _err("nothing to export: no active subscriptions with exportable api keys")
    lines = [f"{var}={key}" for var, key in pairs]
    if args.shell == "powershell":
        body = "\n".join(
            [
                "# MAR portable setup — paste this whole block into PowerShell on the target machine.",
                "# It writes omp's agent .env (contains SECRETS; omp deletes nothing, omp reads it at startup).",
                'New-Item -ItemType Directory -Force "$env:USERPROFILE\\.omp\\agent" | Out-Null',
                "@'",
                *lines,
                "'@ | Set-Content \"$env:USERPROFILE\\.omp\\agent\\.env\" -Encoding ascii",
            ]
        )
    else:
        body = "\n".join(
            [
                "# MAR portable setup — paste this whole block into bash on the target machine.",
                "# It writes omp's agent .env (contains SECRETS; omp reads it at startup).",
                "mkdir -p ~/.omp/agent",
                "cat > ~/.omp/agent/.env <<'MAR_EOF'",
                *lines,
                "MAR_EOF",
            ]
        )
    footer = [
        "# Then verify and start:  omp usage    omp --yolo",
        f"# Exported {len(pairs)} credential(s) from MAR ({args.shell}).",
    ]
    if skipped:
        footer.append("# Not exportable:")
        footer.extend(skipped)
    text = "\n".join([body, *footer]) + "\n"
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
