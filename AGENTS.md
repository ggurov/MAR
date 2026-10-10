# AGENTS.md — rules for every human or coding agent working in MAR

MAR = **My AI Router**. A local CLI that tracks AI-provider subscriptions (keys,
limits, defaults), pushes them into an Oh My Pi (omp) install, and emits the
single startup prompt for a fresh omp setup. **It is not a proxy** — no model
traffic ever flows through MAR. If a change makes MAR touch traffic, you are in
the wrong repo.

## Commands

| Task | Command |
| --- | --- |
| Install / sync env | `uv sync` |
| Full test suite | `uv run pytest` |
| One test file | `uv run pytest tests/test_registry.py` |
| One test | `uv run pytest -k name_fragment` |
| Lint | `uv run ruff check src tests` |
| Format | `uv run ruff format src tests` |
| Run the CLI | `uv run mar --help` |

Python >= 3.12, managed by uv. Runtime deps: stdlib + `pyyaml` only. Adding a
runtime dependency requires a PLAN.md entry saying why.

## The TDD loop (mandatory)

1. Read PLAN.md. Pick ONE slice. Re-read its acceptance criteria.
2. Write failing tests in `tests/test_<module>.py` first. Table-driven
   (`@pytest.mark.parametrize`) wherever inputs vary.
3. Run them. Confirm RED (fail for the expected reason, not import errors you
   could have avoided by scaffolding the module with type signatures only).
4. Implement the minimum in `src/mar/<module>.py` to go GREEN.
5. Refactor. Keep modules under 300 lines; split by concept, not by size.
6. Run the FULL suite: `uv run pytest`.
7. Commit — **green state only**.

Commit messages: `test:`, `feat:`, `fix:`, `docs:`, `chore:` + imperative
subject. Red states live in your working tree, never in history — history must
bisect.

## Non-negotiables

1. **Tests never touch real user state.** No `~/.mar`, no `~/.omp`, no network
   in unit tests. Every path comes from `tmp_path` or an explicit argument.
   `MAR_HOME` / `PI_CODING_AGENT_DIR` env vars are overrides for production use, and
   tests must set them to `tmp_path` when exercising default-path logic.
2. **Keys are secrets.** Never print, log, commit, or embed a raw key. Use the
   CLI's masking output (`sk_…abcd`). Fixtures use obviously fake keys
   (`test-key-1234`).
3. **No stubs.** No `NotImplementedError`, no `TODO`, no mock-returns in
   `src/`. If a real dependency is missing (e.g. an unverified provider
   endpoint), the catalog/notes say so explicitly and the CLI says what it
   can't do and why — that is a feature, not a stub.
4. **Docstring = contract.** Every public function/class has a docstring
   stating behavior, arguments, raises, and return. Tests enforce the
   docstring, not vice versa.
5. **One concept per module**, file-disjoint ownership. Two agents must never
   edit the same file in one change set.
6. **`main(argv) -> int`** in `cli.py`; subcommand handlers return exit codes
   (0 ok, 1 runtime error, 2 usage). No `sys.exit` outside the entry point.
7. Errors are typed: one exception class per module (`RegistryError`,
   `OmpError`, `LimitError`, …), raised with actionable messages.
8. Commit only what you verified. If you did not run it, do not claim it.

## Layout

```
src/mar/
  registry.py    # Subscription store: CRUD + persistence (~/.mar/registry.json)
  providers.py   # Static catalog of the user's 10 providers + validation
  limits.py      # Usage snapshots: real API fetchers + manual-check fallback
  omp.py         # omp adapter: agent.db credentials + config.yml model roles
  bootstrap.py   # startup-prompt renderer + one-shot omp sync orchestration
  cli.py         # argparse front end; wiring only, no business logic
tests/           # mirrors src/mar/, one file per module
docs/            # architecture.md (contracts), providers.md (catalog data)
PLAN.md          # milestones + acceptance criteria; update when scope changes
```

## Definition of done

- `uv run pytest` exits 0 with the new slice covered (happy path + boundary +
  error path).
- `uv run ruff check src tests` clean.
- If `cli.py` surface changed: `uv run mar <cmd> --help` and one real
  invocation against a `MAR_HOME` in `tmp` exercised it.
- PLAN.md updated if scope moved.
