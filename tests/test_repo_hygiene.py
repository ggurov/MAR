"""Repo hygiene: real-looking credentials must never appear in tracked files.

Keys belong in the user's config space (~/.mar/registry.json, omp's agent
dir) — never in this repository or its history. These tests scan every file
in the working tree for high-confidence secret formats (the user's real key
shapes). Patterns are deliberately specific to avoid false positives on the
obviously-fake fixtures used by the test suite (``test-key-…``, ``sk-test-…``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_SECRET_PATTERNS = {
    "openrouter key": re.compile(r"sk-or-v1-[0-9a-f]{20,}"),
    "anthropic oauth token": re.compile(r"sk-ant-oat01-[A-Za-z0-9_-]{20,}"),
    "jwt pair (codex token)": re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}"),
    "cursor token": re.compile(r"crsr_[0-9a-f]{20,}"),
    "opencode key": re.compile(r"oc_sk_[0-9a-f]{10,}"),
    "typesafe key": re.compile(r"apikey_[0-9a-f]{20,}"),
}

_EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"}


def _repo_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


@pytest.mark.parametrize("path", _repo_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_real_credentials_in_repo(path: Path):
    content = path.read_bytes().decode("utf-8", errors="replace")
    for name, pattern in _SECRET_PATTERNS.items():
        assert pattern.search(content) is None, (
            f"{path.name} looks like it contains a real {name}; "
            "keys belong in ~/.mar, never in the repo"
        )


def test_registry_is_not_inside_the_repo():
    assert not (REPO_ROOT / ".mar" / "registry.json").exists(), (
        "registry.json must live in ~/.mar (user config space), not the repo"
    )
