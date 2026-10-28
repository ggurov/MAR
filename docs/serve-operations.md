# Server operations (Windows management plane)

How `mar serve` stays alive on the Windows box, how to control it, and how to
re-register it on another machine. Everything here is **per-user** — no admin
rights required anywhere.

## What runs

- `uv run mar serve --port 8930` — HTML status page + `/config.sh` /`/config.ps1`
  payloads, regenerated per request from the registry.
- Windows Firewall rule `MAR config server`: TCP 8930 inbound, **LocalSubnet
  only**, Private+Domain profiles (created once with elevation; the rule itself
  never needs admin again).
- Task Scheduler task `MAR config server` — starts the supervisor at logon and
  keeps it running for the session.

## Files (per-user, machine-local — deliberately not in the repo)

| File | Role |
| --- | --- |
| `%USERPROFILE%\mar-serve.cmd` | Supervisor loop: port-guard → launch server → 5s sleep → respawn |
| `%USERPROFILE%\mar-serve.vbs` | Hidden-window wrapper: `Run("...mar-serve.cmd", 0, True)` — the wait keeps the task attached for the server's lifetime |
| `%USERPROFILE%\mar-task.xml` | Task Scheduler definition (must be **UTF-16 LE**) |
| `%USERPROFILE%\mar-serve.log` | Append-only stdout/stderr — crash evidence lives here |

## Managing it (no admin)

```
schtasks /Query /TN "MAR config server" /V /FO LIST   # status, last result, next run
schtasks /Run   /TN "MAR config server"                # start now
schtasks /End   /TN "MAR config server"                # stop now (kills the tree)
schtasks /Delete /F /TN "MAR config server"            # remove entirely
```

Status meanings: **Running** = server alive under the loop; **Ready** = not
running. `/End` terminates the process tree — the supervisor loop does *not*
fight it; `/Run` brings it back.

## Supervisor behavior (verified live)

- **Crash-respawn**: server process dies → loop sleeps ~5s → relaunch;
  measured ≤20s from kill to serving again (`curl` 200 + payload content check).
- **Port guard**: if :8930 is already LISTENING, the loop waits in 30s ticks
  instead of stacking failing instances.
- **Sleeps use `ping -n N 127.0.0.1`** — `timeout.exe` fails without an
  interactive console, which is exactly the context a hidden task provides.
- **`RestartOnFailure` in the task XML is inert for on-demand `/Run` starts**
  (Task Scheduler records the non-zero exit as `Last Result: 1` but schedules
  no retry). The in-script loop is the real supervisor; the XML block remains
  only as belt-and-braces for trigger-started runs.
- Alternative pattern for the same job: a `TimeTrigger` with
  `<Repetition><Interval>PT1M</Interval>` plus `IgnoreNew` — the tick relaunches
  a dead server within a minute. Not used here; the loop respawns faster.

## Re-register on another machine

1. Create `mar-serve.cmd` with that machine's repo path and `uv.exe` full path,
   and `mar-serve.vbs` pointing at the .cmd (both templates below).
2. Convert the task XML to UTF-16 — schtasks rejects UTF-8 XML with
   `unable to switch the encoding`:
   ```
   powershell -Command "(Get-Content -Raw mar-task.xml) -replace 'UTF-8','UTF-16' | Set-Content -Encoding Unicode mar-task.xml"
   ```
3. Register (per-user XML registers **without elevation**; the `/SC ONLOGON`
   CLI shorthand does not — that path needs admin):
   ```
   schtasks /Create /F /TN "MAR config server" /XML "%USERPROFILE%\mar-task.xml"
   ```
4. Firewall (once, elevated): `netsh advfirewall firewall add rule name="MAR
   config server" dir=in action=allow protocol=TCP localport=8930
   remoteip=localsubnet profile=private,domain`

### mar-serve.cmd template

```bat
@echo off
:loop
netstat -ano | findstr ":8930" | findstr "LISTENING" >nul && ping -n 31 127.0.0.1 >nul && goto loop
cd /d <REPO_PATH>
"<UV_EXE>" run mar serve --port 8930 >> "%USERPROFILE%\mar-serve.log" 2>&1
ping -n 6 127.0.0.1 >nul
goto loop
```

### mar-serve.vbs template

```vbs
WScript.Quit CreateObject("Wscript.Shell").Run("%USERPROFILE%\mar-serve.cmd", 0, True)
```

### mar-task.xml essentials

- `LogonTrigger` (delay `PT20S`), user `"<COMPUTER>\<USER>"` — **qualified name**;
  a bare username fails registration context.
- Principal: `InteractiveToken`, `LeastPrivilege`.
- `ExecutionTimeLimit` = `PT0S` (never kill the task), `MultipleInstancesPolicy`
  = `IgnoreNew`.

## Health check

```
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8930/            # 200
curl -s http://localhost:8930/config.sh | grep -c 'hideThinkingBlock\|composer.tokenRate\|memory.backend hindsight'  # 3
curl -s 'http://localhost:8930/config.sh?memory=off' | grep -c 'memory.backend off'  # 1
```

Check content, not just status — a stale process from an older code revision
still answers 200. The grep proves the payloads reflect current code.

## Log growth

`mar-serve.log` is append-only; truncate when it annoys you:

```
: > "%USERPROFILE%\mar-serve.log"
```

A running server keeps its old file handle; the truncation takes effect for
writes after the next respawn — harmless either way.
