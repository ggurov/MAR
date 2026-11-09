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
import os
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mar import cli, limits, providers
from mar.registry import PRIMARY_ACCOUNT, Registry

REPO_URL = "https://github.com/ggurov/MAR"
HINDSIGHT_DASHBOARD_URL = "http://hub.nj02.gurov.us:9999/dashboard"
_USAGE_TTL_SECONDS = 60.0
_usage_cache: dict[str, tuple[float, str]] = {}
_usage_locks: dict[str, threading.Lock] = {}
_usage_locks_guard = threading.Lock()


def _account_pairs(raw: str) -> list[tuple[str, str]]:
    """Parse a comma-separated ``PROVIDER=LABEL`` account override list.

    Args:
        raw: Query value, e.g. ``"commandcode=joesacct1"``; empty is valid.

    Returns:
        ``(provider, label)`` pairs in input order.

    Raises:
        ValueError: When an entry lacks ``=`` or has an empty side.
    """
    pairs: list[tuple[str, str]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(f"accounts entry {chunk!r} must be PROVIDER=LABEL")
        provider, label = chunk.split("=", 1)
        if not provider or not label:
            raise ValueError(f"accounts entry {chunk!r} must be PROVIDER=LABEL")
        pairs.append((provider, label))
    return pairs


def render_config(
    registry: Path,
    shell: str,
    backend: str | None = None,
    memory: str = "on",
    accounts: str = "",
) -> str:
    """Render the ``mar export`` payload for one shell, minus the markers.

    Args:
        registry: Path to the MAR registry file.
        shell: ``"bash"`` or ``"powershell"``.
        backend: Optional storage backend name; when given it is passed
            through, otherwise the file backend is forced via the path.
        memory: ``"on"`` (default) configures the global hindsight backend;
            ``"off"`` writes ``memory.backend off`` so the box stops using memory.
        accounts: Optional ``PROVIDER=LABEL`` overrides (comma-separated)
            that pin the served key for this payload only.

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
    argv += ["--memory", memory]
    for provider, label in _account_pairs(accounts):
        argv += ["--account", f"{provider}={label}"]
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        cli.main(argv)
    lines = [
        line
        for line in out.getvalue().splitlines()
        if not (line.startswith("# ===") and ("BEGIN" in line or "END" in line))
    ]
    return "\n".join(lines).strip("\n")


def _usage_cache_key(provider: str, account: str) -> str:
    """Return the cache/lock key for one provider key.

    The primary account keeps the bare provider id so existing cache
    entries and single-key providers behave exactly as before; alternate
    accounts get their own suffix.
    """
    if account in ("", PRIMARY_ACCOUNT):
        return provider
    return f"{provider}:{account}"


def _provider_usage_lock(cache_key: str) -> threading.Lock:
    """Return the per-(provider, account) lock guarding one live usage fetch."""
    with _usage_locks_guard:
        return _usage_locks.setdefault(cache_key, threading.Lock())

def _format_snapshot_items(items: tuple) -> str:
    """Format one live ``LimitSnapshot``'s items into a quota cell.

    USD items read like the OpenRouter cell (``"$315.00 left"``); numeric
    windows render ``"5h 0.45/14 cr"`` with a parenthesized reset when the
    provider reports one. Multiple items join with ``·``.

    Args:
        items: ``LimitSnapshot.items`` from ``limits.fetch``.

    Returns:
        The cell text, or ``""`` when no item carries a value.
    """
    parts: list[str] = []
    now = time.time()
    for item in items:
        unit = (item.unit or "").lower()
        if unit in ("usd", "$"):
            if item.used is not None and item.limit is not None:
                parts.append(f"${item.limit - item.used:.2f} left")
            elif item.limit is not None:
                parts.append(f"${item.limit:.2f} left")
            elif item.used is not None:
                parts.append(f"${item.used:.2f} used")
            continue
        suffix = "cr" if unit.startswith("cre") else item.unit
        if item.used is not None and item.limit is not None:
            text = f"{item.name} {item.used:g}/{item.limit:g} {suffix}".rstrip()
        elif item.limit is not None:
            text = f"{item.name} {item.limit:g} {suffix}".rstrip()
        elif item.used is not None:
            text = f"{item.name} {item.used:g} {suffix}".rstrip()
        else:
            continue
        if item.reset_at_ms:
            resets = (item.reset_at_ms / 1000 - now) / 3600
            text += f" (resets in {_format_duration(resets)})"
        parts.append(text)
    return " \u00b7 ".join(parts)


def _meter(
    label: str,
    *,
    fraction: float | None = None,
    detail: str = "",
    reset_at_ms: int | None = None,
) -> dict:
    """Build one display meter (a progress bar plus its human text).

    Args:
        label: Window/limit name, e.g. ``"5-hour"``.
        fraction: Used fraction (0..1); None means no bounded bar.
        detail: Human numbers under the bar, e.g. ``"0.48 / 14 cr"``.
        reset_at_ms: Epoch milliseconds of the window reset, or 0/None.

    Returns:
        A dict with ``label``, ``percent`` (None when unbounded),
        ``level`` (``ok``/``warn``/``crit``), ``detail`` and ``reset``.
    """
    percent: int | None = None
    level = "ok"
    if fraction is not None:
        fraction = min(max(float(fraction), 0.0), 1.0)
        percent = round(fraction * 100)
        level = "ok" if percent < 60 else ("warn" if percent < 85 else "crit")
    reset = ""
    if reset_at_ms:
        hours = (reset_at_ms / 1000 - time.time()) / 3600
        reset = f"resets in {_format_duration(hours)}"
    return {
        "label": label,
        "percent": percent,
        "level": level,
        "detail": detail,
        "reset": reset,
    }


def _meters_from_omp(items: list[dict]) -> tuple[dict, ...]:
    """Turn ``omp usage`` window limits into display meters.

    Uses ``usedFraction`` / absolute ``used`` + ``limit`` when present,
    percent-remaining windows are inverted (used = 100 - remaining), and
    a bare balance renders as text without a bar.

    Args:
        items: The ``limits`` list of one ``omp usage`` report.

    Returns:
        One meter per informative window, in report order.
    """
    meters: list[dict] = []
    base_labels = [
        (item.get("window") or {}).get("label")
        or item.get("label")
        or item.get("id", "?").rsplit(":", 1)[-1]
        for item in items
    ]
    counts: dict[str, int] = {}
    for base in base_labels:
        counts[base] = counts.get(base, 0) + 1
    for item, base in zip(items, base_labels, strict=True):
        amount = item.get("amount") or {}
        window = item.get("window") or {}
        label = (
            base
            if counts[base] == 1
            else f"{base} · {item.get('id', '?').rsplit(':', 1)[-1]}"
        )
        unit = amount.get("unit") or ""
        suffix = "cr" if unit.lower().startswith("cre") else unit
        used = amount.get("used")
        cap = amount.get("limit")
        remaining = amount.get("remaining")
        fraction = amount.get("usedFraction")
        detail = ""
        if unit == "percent" and remaining is not None:
            fraction = max(0.0, 1 - float(remaining) / 100)
            detail = f"{float(remaining):g}% left"
        elif cap is not None and used is not None:
            fraction = float(used) / float(cap) if cap else None
            detail = f"{float(used):g} / {float(cap):g} {suffix}".rstrip()
        elif cap is not None and remaining is not None:
            fraction = (float(cap) - float(remaining)) / float(cap) if cap else None
            detail = f"{float(remaining):g} / {float(cap):g} {suffix} left".rstrip()
        elif fraction is not None:
            fraction = float(fraction)
            detail = f"{fraction * 100:.0f}% used"
        elif cap is not None:
            detail = f"{float(cap):g} {suffix} limit".rstrip()
        elif remaining is not None:
            detail = f"{float(remaining):g} {suffix} left".rstrip()
        meter = _meter(label, fraction=fraction, detail=detail, reset_at_ms=window.get("resetsAt"))
        if meter["percent"] is not None or meter["detail"] or meter["reset"]:
            meters.append(meter)
    return tuple(meters)


def _meters_from_snapshot(items: tuple) -> tuple[dict, ...]:
    """Turn one live ``LimitSnapshot``'s items into display meters.

    Args:
        items: ``LimitSnapshot.items`` from ``limits.fetch``.

    Returns:
        One meter per informative item, in snapshot order. USD items keep
        a dollar detail (``"$315.00 left"``); numeric windows show
        ``"used / limit cr"``.
    """
    meters: list[dict] = []
    for item in items:
        unit = (item.unit or "").lower()
        if unit in ("usd", "$"):
            if item.used is not None and item.limit is not None:
                detail = f"${item.limit - item.used:.2f} left"
            elif item.limit is not None:
                detail = f"${item.limit:.2f} left"
            elif item.used is not None:
                detail = f"${item.used:.2f} used"
            else:
                continue
            fraction = (
                item.used / item.limit
                if item.used is not None and item.limit
                else None
            )
        else:
            suffix = "cr" if unit.startswith("cre") else item.unit
            if item.used is not None and item.limit is not None:
                fraction = item.used / item.limit if item.limit else None
                detail = f"{item.used:g} / {item.limit:g} {suffix}".rstrip()
            elif item.limit is not None:
                fraction = None
                detail = f"{item.limit:g} {suffix} left".rstrip()
            elif item.used is not None:
                fraction = None
                detail = f"{item.used:g} {suffix} used".rstrip()
            else:
                continue
        meter = _meter(
            item.name,
            fraction=fraction,
            detail=detail,
            reset_at_ms=item.reset_at_ms,
        )
        if meter["percent"] is not None or meter["detail"] or meter["reset"]:
            meters.append(meter)
    return tuple(meters)


def _fetch_usage(provider: str, key: str | None) -> tuple[str, tuple[dict, ...]]:
    """Fetch one provider's live quota text plus display meters.

    Raises:
        Exception: Any fetch/parse failure (callers decide the fallback).
    """
    snapshot = limits.fetch(provider, key or "", timeout=5.0)
    text = _format_snapshot_items(snapshot.items) or (snapshot.note or "ok")
    meters = _meters_from_snapshot(snapshot.items) if snapshot.items else ()
    return text, meters


def _refresh_usage_async(
    provider: str, key: str | None, account: str = PRIMARY_ACCOUNT
) -> threading.Thread | None:
    """Refresh one provider key's quota in the background, single-flight.

    Returns:
        The started thread, or ``None`` when a refresh is already running.
    """
    cache_key = _usage_cache_key(provider, account)
    lock = _provider_usage_lock(cache_key)
    if not lock.acquire(blocking=False):
        return None
    cache = _usage_cache
    previous = cache.get(cache_key)

    def worker() -> None:
        try:
            text, meters = _fetch_usage(provider, key)
        except Exception:  # noqa: BLE001 — keep the last good cell
            text = previous[1] if previous is not None else "unavailable"
            meters = previous[2] if previous is not None and len(previous) > 2 else ()
        finally:
            cache[cache_key] = (time.monotonic(), text, meters)
            lock.release()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _usage_entry(
    provider: str, key: str | None, account: str = PRIMARY_ACCOUNT
) -> tuple[str | None, tuple[dict, ...]]:
    """Return ``(quota text, meters)`` for one provider key, cached.

    The cache holds ``(monotonic, text, meters)`` per provider/account.
    Fresh entries return directly; stale entries return immediately while a
    single background refresh runs; a cold fetch blocks only when no other
    refresh already holds the lock (then ``(None, ())`` is returned).

    Args:
        provider: Catalog id.
        key: Provider credential used for the live fetch.
        account: Account label whose quota is shown.

    Returns:
        ``(text, meters)``; ``(None, ())`` when the provider has no live
        fetcher or a refresh is already in flight. Failures degrade to
        ``"unavailable"`` (or keep the last good value when refreshing).
    """
    try:
        entry = providers.get(provider)
    except KeyError:
        return None, ()
    if entry.limits_kind != "api":
        return None, ()
    cache_key = _usage_cache_key(provider, account)
    now = time.monotonic()
    cached = _usage_cache.get(cache_key)
    if cached is not None and now - cached[0] < _USAGE_TTL_SECONDS:
        meters = cached[2] if len(cached) > 2 else ()
        return cached[1], meters
    if cached is not None:
        _refresh_usage_async(provider, key, account)
        meters = cached[2] if len(cached) > 2 else ()
        return cached[1], meters
    lock = _provider_usage_lock(cache_key)
    if not lock.acquire(blocking=False):
        return None, ()
    try:
        text, meters = _fetch_usage(provider, key)
    except Exception:  # noqa: BLE001 — a dead usage API must never 500 the page
        text, meters = "unavailable", ()
    finally:
        _usage_cache[cache_key] = (time.monotonic(), text, meters)
        lock.release()
    return text, meters


def _usage_cell(
    provider: str, key: str | None, account: str = PRIMARY_ACCOUNT
) -> str | None:
    """Return the live quota text for one provider, cached and non-blocking."""
    return _usage_entry(provider, key, account)[0]


_OMP_TTL_SECONDS = 300.0
_omp_cache: dict = {}
_omp_refresh_lock = threading.Lock()


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


def _refresh_omp_usage_async() -> threading.Thread | None:
    """Refresh ``omp usage`` in the background, single-flight.

    Returns:
        The started thread, or ``None`` when a refresh is already running.
    """
    if not _omp_refresh_lock.acquire(blocking=False):
        return None
    cache = _omp_cache

    def worker() -> None:
        reports: dict[str, list] | None = None
        try:
            data = _run_omp_usage()
            reports = {r["provider"]: r.get("limits", []) for r in data.get("reports", [])}
        except Exception:  # noqa: BLE001 — keep the last good snapshot
            reports = None
        finally:
            cache["ts"] = time.monotonic()
            if reports is not None:
                cache["data"] = reports
            _omp_refresh_lock.release()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _omp_usage() -> dict[str, list]:
    """Return omp's usage reports, cached for :data:`_OMP_TTL_SECONDS`.

    Fresh data is returned directly. Stale data is returned immediately
    while a single background refresh runs, so a page render never waits
    on ``omp usage``. With no cache at all the call fetches synchronously
    unless another refresh is already in flight, in which case it returns
    an empty mapping so the page still renders fast.

    Returns:
        Mapping of omp provider id to its ``limits`` list (possibly empty).
    """
    now = time.monotonic()
    cache = _omp_cache
    data = cache.get("data")
    ts = cache.get("ts")
    if data is not None and ts is not None and now - ts < _OMP_TTL_SECONDS:
        return data
    if data is not None:
        _refresh_omp_usage_async()
        return data
    if not _omp_refresh_lock.acquire(blocking=False):
        return {}
    try:
        raw = _run_omp_usage()
        reports = {r["provider"]: r.get("limits", []) for r in raw.get("reports", [])}
    except Exception:  # noqa: BLE001 — degrade to the last good snapshot
        reports = {}
    finally:
        cache["ts"] = time.monotonic()
        cache["data"] = reports
        _omp_refresh_lock.release()
    return reports


def _omp_id_map() -> dict[str, str]:
    """Map catalog provider id → omp provider id (when one exists)."""
    return {
        p.id: p.omp_provider_id for p in providers.CATALOG if p.omp_provider_id
    }


def _format_duration(hours: float) -> str:
    """Format a window reset countdown for the quota cell.

    Args:
        hours: Hours until the window resets; negative once past.

    Returns:
        ``"now"`` when ``hours <= 0``; ``"2.0h"`` under 48 hours;
        ``"~8 days"`` beyond that.
    """
    if hours <= 0:
        return "now"
    if hours < 48:
        return f"{hours:.1f}h"
    days = round(hours / 24)
    return f"~{days} day" if days == 1 else f"~{days} days"


def _format_limits(items: list[dict]) -> tuple[str | None, int | None]:
    """Format omp usage limits into one quota cell plus earliest reset.

    Every window contributes a piece: quantified windows render their
    amount plus a parenthesized reset when the window reports one, and
    reset-only windows render as ``"<label> resets in X"``.

    Args:
        items: The ``limits`` list of one ``omp usage`` report.

    Returns:
        ``(cell, earliest_reset_ms)`` where ``cell`` is like
        ``"5h 100% · 7d 40% (resets in 4.0h)"`` (a window with a cap but
        no remaining renders its ``usedFraction`` as ``"5h 7% used"``,
        or its bare cap as ``"5h 14 cr"`` without one; ``None`` when
        every window lacks both an amount and a reset) and
        ``earliest_reset_ms`` is the soonest window reset (``None`` when
        no window reports one).
    """
    parts: list[str] = []
    earliest: int | None = None
    now = time.time()
    for item in items:
        amount = item.get("amount") or {}
        remaining, cap, unit = amount.get("remaining"), amount.get("limit"), (amount.get("unit") or "")
        window = item.get("window") or {}
        reset = window.get("resetsAt")
        if reset is not None and (earliest is None or reset < earliest):
            earliest = int(reset)
        resets = ""
        if reset is not None:
            resets = f"resets in {_format_duration((reset / 1000 - now) / 3600)}"
        label = item.get("id", "?").rsplit(":", 1)[-1]
        if remaining is None and cap is None:
            if resets:
                parts.append(f"{label} {resets}")
            continue
        used_fraction = amount.get("usedFraction")
        if remaining is None and used_fraction is not None:
            # commandcode windows: cap plus used fraction, no absolute remaining
            base = f"{label} {used_fraction * 100:.0f}% used"
        elif unit == "percent":
            base = f"{label} {(remaining or 0):.0f}%"
        elif unit == "usd":
            base = f"{label} ${(remaining or 0):.2f}"
        else:
            suffix = "cr" if unit.startswith("cre") else ("req" if unit.startswith("req") else unit)
            if remaining is None:
                base = f"{label} {cap:g} {suffix}".rstrip()
            elif cap:
                base = f"{label} {remaining:g}/{cap:g} {suffix}".rstrip()
            else:
                rem = f"{remaining:.2f}" if suffix == "cr" else f"{remaining:g}"
                base = f"{label} {rem} {suffix}".rstrip()
        parts.append(f"{base} ({resets})" if resets else base)
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

def services_snapshot(registry: Path) -> list[dict]:
    """Collect one row per active subscription account for the services panel.

    Args:
        registry: Path to the MAR registry file (file backend).

    Returns:
        One dict per active subscription KEY (primary first, then each
        alternate account) with keys ``provider``, ``account`` (label;
        ``"original"`` for the subscription's own key), ``selected`` (bool,
        the key ``mar export``/``omp-sync`` currently serve), ``auth``,
        ``usage`` (quota text from ``omp usage`` windows — each window
        carries its own ``resets in X`` countdown — else the live fetch for
        any ``limits_kind == "api"`` catalog entry with that account's key;
        ``None`` when neither reports), ``renew`` (oauth token expiry only:
        ``in 7.9h`` under 48 hours, ``in ~N days`` under a year, ``""``
        for longer-lived tokens, ``EXPIRED`` once past; empty string when
        nothing expires), ``limits_url``, and ``endpoint`` (the catalog
        ``base_url`` for a self-hosted entry, else ``""``).
    """
    rows: list[dict[str, str | None]] = []
    reg = Registry.load(registry)
    id_map = _omp_id_map()
    usage_map = _omp_usage()
    for sub in reg.active():
        try:
            entry = providers.get(sub.provider)
        except KeyError:
            entry = None
        limits_url = entry.limits_url if entry else ""
        omp_limits = usage_map.get(id_map.get(sub.provider, ""), [])
        omp_quota, _ = _format_limits(omp_limits)
        if sub.auth_method == "oauth" and sub.expires_at_ms:
            hours = (sub.expires_at_ms / 1000 - time.time()) / 3600
            renew = _format_renew(hours)
        else:
            renew = ""
        selected_label = sub.selected_account or PRIMARY_ACCOUNT
        keys = [(PRIMARY_ACCOUNT, sub.key)]
        keys.extend((account.name, account.key) for account in sub.accounts)
        shared_quota = None if sub.accounts else omp_quota
        shared_meters = (
            _meters_from_omp(omp_limits) if shared_quota is not None else ()
        )
        for label, key in keys:
            selected = label == selected_label
            quota = shared_quota
            meters = shared_meters
            if quota is None:
                quota, meters = _usage_entry(sub.provider, key, account=label)
            rows.append(
                {
                    "provider": sub.provider,
                    "account": label,
                    "selected": selected,
                    "auth": sub.auth_method,
                    "meters": meters,
                    "usage": quota,
                    "renew": renew,
                    "limits_url": limits_url,
                    "endpoint": entry.base_url if entry else "",
                }
            )
    return rows


def _render_meters(meters: tuple[dict, ...], usage: str | None) -> str:
    """Render quota meters as progress bars, falling back to plain text.

    Args:
        meters: Meter dicts from :func:`_meters_from_omp` /
            :func:`_meters_from_snapshot`.
        usage: Legacy text cell, used when no meter exists (e.g. a
            key-liveness note).

    Returns:
        HTML for one table cell.
    """
    if not meters:
        return html_mod.escape(usage) if usage else ""
    cards: list[str] = []
    for meter in meters:
        label = html_mod.escape(meter["label"])
        detail = html_mod.escape(meter["detail"])
        reset = html_mod.escape(meter["reset"])
        note = " \u00b7 ".join(part for part in (detail, reset) if part)
        title = f' title="{html_mod.escape(note)}"' if note else ""
        if meter["percent"] is None:
            cards.append(
                f'<div class="meter"{title}><div class="mhead"><span>{label}</span>'
                f"<b>{detail}</b></div>"
                + (f'<div class="mnote">{reset}</div>' if reset else "")
                + "</div>"
            )
            continue
        percent = meter["percent"]
        cards.append(
            f'<div class="meter"{title}><div class="mhead"><span>{label}</span>'
            f"<b>{percent}% used</b></div>"
            f'<div class="bar"><span class="fill {meter["level"]}" '
            f'style="width:{percent}%"></span></div>'
            + (f'<div class="mnote">{reset}</div>' if reset else "")
            + "</div>"
        )
    return '<div class="meters">' + "".join(cards) + "</div>"


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
    memory_url = os.environ.get("MAR_MEMORY_URL") or cli.DEFAULT_GLOBAL_MEMORY_URL
    dashboard_link = (
        f'<a href="{HINDSIGHT_DASHBOARD_URL}" target="_blank"'
        ' rel="noopener">Open Hindsight dashboard</a>'
    )
    rows_html: list[str] = []
    snapshot = services_snapshot(registry)
    key_counts: dict[str, int] = {}
    for row in snapshot:
        key_counts[row["provider"]] = key_counts.get(row["provider"], 0) + 1
    picked = [
        f"{row['provider']}={row['account']}"
        for row in snapshot
        if key_counts.get(row["provider"], 0) > 1 and row["selected"]
    ]
    accounts_query = "&accounts=" + ",".join(picked) if picked else ""
    sh_cmd = html_mod.escape(
        f"curl -fsSL http://{host}/config.sh?memory=on{accounts_query} | sh"
    )
    ps_cmd = html_mod.escape(
        f"irm http://{host}/config.ps1?memory=on{accounts_query} | iex"
    )
    for row in snapshot:
        usage = _render_meters(row.get("meters") or (), row["usage"])
        name = html_mod.escape(row["provider"])
        link = row["limits_url"] or row["endpoint"]
        if link:
            provider = (
                f'<a href="{html_mod.escape(link)}" target="_blank"'
                f' rel="noopener"><b>{name}</b></a>'
            )
        else:
            provider = f"<b>{name}</b>"
        renew = html_mod.escape(row["renew"])
        if "EXPIRED" in renew:
            renew = f'<span class="expired">{renew}</span>'
        account = html_mod.escape(row["account"])
        if key_counts[row["provider"]] > 1:
            checked = " checked" if row["selected"] else ""
            provider_arg = html_mod.escape(row["provider"], quote=True)
            account_arg = html_mod.escape(row["account"], quote=True)
            key_cell = (
                f'<label class="acct"><input type="radio" '
                f'name="acct-{provider_arg}" value="{account_arg}"{checked} '
                f"onchange=\"selectAccount('{provider_arg}','{account_arg}',this)\"> "
                f"{account}</label>"
            )
        else:
            key_cell = account
        rows_html.append(
            f"<tr><td>{provider}</td>"
            f"<td>{row['auth']}</td>"
            f'<td class="usage-cell">{usage}</td>'
            f"<td>{renew}</td>"
            f"<td class=\"keys\">{key_cell}</td></tr>"
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
  th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid #21262d;
            vertical-align:top; }}
  th {{ color:#8b949e; font-weight:600; font-size:12px; text-transform:uppercase;
        letter-spacing:.05em; }}
  td.mono {{ font-family:ui-monospace,Consolas,monospace; }}
  td.keys {{ white-space:nowrap; }}
  label.acct {{ display:flex; gap:6px; align-items:center; cursor:pointer; }}
  td.usage-cell {{ width:42%; min-width:220px; }}
  .meters {{ display:flex; flex-direction:row; flex-wrap:wrap; gap:10px 18px; }}
  .meter {{ flex:1 1 130px; min-width:120px; }}
  .meter .mhead {{ display:flex; justify-content:space-between; gap:8px;
                   flex-wrap:wrap; font-size:12.5px; margin-bottom:4px; }}
  .mhead b {{ font-variant-numeric:tabular-nums; }}
  .bar {{ height:9px; background:#21262d; border:1px solid #30363d;
          border-radius:5px; overflow:hidden; box-sizing:border-box; }}
  .bar .fill {{ display:block; height:100%; background:#3fb950; border-radius:4px; }}
  .fill.warn {{ background:#d29922; }}
  .fill.crit {{ background:#f85149; }}
  .mnote {{ color:#8b949e; font-size:11.5px; margin-top:3px; }}
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
      <button class="ghost" onclick="copyFetch('/config.sh?memory='+memParam()+accountsParam(), this)">Copy payload</button>
    </div>
  </div>

  <div class="card">
    <h2>Windows (PowerShell)</h2>
    <code class="cmd" id="ps-cmd">{ps_cmd}</code>
    <div class="row">
      <button onclick="copyEl('ps-cmd', this)">Copy command</button>
      <button class="ghost" onclick="copyFetch('/config.ps1?memory='+memParam()+accountsParam(), this)">Copy payload</button>
    </div>
  </div>

  <div class="card">
    <h2>Global memory</h2>
    <label style="display:flex; gap:8px; align-items:center; font-weight:600;">
      <input type="checkbox" id="global-memory" checked> configure global memory
    </label>
    <p class="note">Checked: the payload sets <code>memory.backend = hindsight</code> pointed at
       <code>{memory_url}</code>. Unchecked: it sets <code>memory.backend = off</code>, so the
       box is unconfigured from using memory.</p>
    <p class="note">{dashboard_link}</p>
  </div>

  <div class="card">
    <h2>Services</h2>
    <table>
      <tr><th>Provider</th><th>Auth</th><th>Quota</th><th>Renews</th><th>Key</th></tr>
      {''.join(rows_html)}
    </table>
    <p class="note">Quota bars fill as usage is consumed; hover a bar for exact numbers.
       <span class="warn">⚠ Plain HTTP</span> — payloads carry live credentials.
       LAN only; the firewall rule scopes this port to LocalSubnet.</p>
    <p class="note">Key radios switch which account <code>mar export</code>,
       <code>/config.sh</code>, <code>/config.ps1</code>, and <code>mar omp-sync</code>
       serve for that provider; the switch is written to the registry immediately.
       A running omp session caches <code>.env</code> at startup — restart it (or re-apply
       the payload) after switching.</p>
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
function memParam() {{
  return document.getElementById('global-memory').checked ? 'on' : 'off';
}}
function accountsParam() {{
  const out = [];
  document.querySelectorAll('input[type="radio"][name^="acct-"]:checked')
    .forEach(function (el) {{ out.push(el.name.slice(5) + '=' + el.value); }});
  return out.length ? '&accounts=' + out.join(',') : '';
}}
function refreshCommands() {{
  const HOST = "{host}";
  document.getElementById('sh-cmd').textContent =
    'curl -fsSL http://' + HOST + '/config.sh?memory=' + memParam() + accountsParam() + ' | sh';
  document.getElementById('ps-cmd').textContent =
    'irm http://' + HOST + '/config.ps1?memory=' + memParam() + accountsParam() + ' | iex';
}}
document.getElementById('global-memory').addEventListener('change', refreshCommands);
refreshCommands();

async function selectAccount(provider, account, el) {{
  try {{
    const r = await fetch('/select', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{provider: provider, account: account}})
    }});
    if (!r.ok) throw new Error((await r.text()).trim());
    location.reload();
  }} catch (e) {{
    el.checked = !el.checked;
    alert('MAR: ' + e.message);
  }}
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
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            memory = "off" if query.get("memory") == ["off"] else "on"
            accounts = query.get("accounts", [""])[0]
            try:
                overrides = _account_pairs(accounts)
                if overrides:
                    reg = (
                        Registry.load(None, backend=backend)
                        if backend
                        else Registry.load(registry)
                    )
                    for provider, label in overrides:
                        reg.select_account(provider, label)
            except (ValueError, cli.registry.RegistryError) as exc:
                self._send(400, f"{exc}\n", "text/plain; charset=utf-8")
                return
            if parsed.path == "/config.sh":
                self._send(
                    200,
                    render_config(registry, "bash", backend, memory, accounts),
                    "text/x-shellscript; charset=utf-8",
                )
            elif parsed.path == "/config.ps1":
                self._send(
                    200,
                    render_config(registry, "powershell", backend, memory, accounts),
                    "text/plain; charset=utf-8",
                )
            elif parsed.path == "/":
                self._send(
                    200, render_index_html(host, registry), "text/html; charset=utf-8"
                )
            else:
                self._send(404, "not found\n", "text/plain; charset=utf-8")

        def do_POST(self) -> None:  # stdlib naming convention
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != "/select":
                self._send(404, "not found\n", "text/plain; charset=utf-8")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 65_536:
                self._send(400, "invalid request body\n", "text/plain; charset=utf-8")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, "invalid JSON body\n", "text/plain; charset=utf-8")
                return
            provider = payload.get("provider") if isinstance(payload, dict) else None
            account = payload.get("account") if isinstance(payload, dict) else None
            if (
                not isinstance(provider, str)
                or not provider
                or not isinstance(account, str)
                or not account
            ):
                self._send(
                    400,
                    "provider and account are required strings\n",
                    "text/plain; charset=utf-8",
                )
                return
            try:
                reg = (
                    Registry.load(None, backend=backend)
                    if backend
                    else Registry.load(registry)
                )
                reg.select_account(provider, account)
                reg.save()
            except (cli.registry.RegistryError, OSError) as exc:
                self._send(400, f"{exc}\n", "text/plain; charset=utf-8")
                return
            self._send(
                200,
                json.dumps({"provider": provider, "account": account}) + "\n",
                "application/json; charset=utf-8",
            )

        def log_message(self, format: str, *args: object) -> None:
            """Quiet default per-request logging to keep the console clean."""

    return Handler


def _warm_caches(registry: Path) -> None:
    """Populate the usage caches once at startup, best-effort.

    Args:
        registry: Path to the MAR registry file.
    """
    try:
        services_snapshot(registry)
    except Exception:  # noqa: BLE001 — warming must never break startup
        return


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
    # Warm the usage caches in the background — the first page view
    # should not wait on a full provider sweep.
    threading.Thread(target=_warm_caches, args=(registry,), daemon=True).start()
    server = ThreadingHTTPServer((host, port), make_handler(registry, backend))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
