"""run_command's subprocess environment must let asyncio initialize on Windows.

Already independently filed upstream as PR#3 ("Give the sandbox enough
environment for the interpreter to start on Windows"). Not being submitted as
a new PR here -- applied locally, with its own failing-test-first proof, only
so Model Arena's races can actually run pytest on this machine.

Root cause: run_command's subprocess.run(..., env={...}) replaces the whole
environment with four keys (PATH, HOME, LANG, PYTHONDONTWRITEBYTECODE). On
Windows, asyncio.windows_events imports the _overlapped extension, which
needs SYSTEMROOT to locate the Winsock service provider -- without it, any
command that imports asyncio (pytest's own plugin machinery does) crashes
with OSError: [WinError 10106], indistinguishable from a genuine test
failure to anything reading only exit_code.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from s17code.coding.exec import run_command
from s17code.coding.workspace import Workspace


@pytest.fixture
def repo(tmp_path: Path) -> Workspace:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return Workspace.open(tmp_path)


def test_a_command_that_imports_asyncio_does_not_crash_on_windows(repo: Workspace) -> None:
    probe = repo.root / "probe.py"
    probe.write_text("import asyncio.windows_events\nprint('asyncio ok')\n")
    result = run_command(repo, ["python", "probe.py"])
    assert result.exit_code == 0, result.stderr
    assert "asyncio ok" in result.stdout
