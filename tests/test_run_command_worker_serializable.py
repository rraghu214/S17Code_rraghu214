"""run_command_worker must hand back something the journal can persist.

Already independently filed upstream as PR#10 ("run_command could not report
its verdict") and PR#2 ("Return run_command's result as a dict..."), found
here independently while building on top of this checkout. Not being
resubmitted as a new PR -- kept local, applied only so runs through this
checkout can actually see a command's result instead of a swallowed
serialization crash.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from s17code.capabilities import default_registry
from s17code.coding.edit import EditLedger
from s17code.workers.coding import run_command_worker
from s17code.workers.context import RunContext


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunContext:
    monkeypatch.setenv("S17_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("S17_ALLOWED_COMMANDS", "python")
    return RunContext(run_id="test-run", runtime=None, llm=None, scope=None,
                      registry=default_registry(), ledger=EditLedger())


async def test_run_command_worker_result_is_json_serializable(ctx: RunContext) -> None:
    task = _task({"command": ["python", "--version"]})
    result = await run_command_worker(ctx, task)
    json.dumps(result)  # this is what the graph checkpoint does; must not raise
    assert result["exit_code"] == 0
    assert result["ok"] is True


def _task(input_: dict) -> object:
    from s17code.core.live_graph import TaskSpec

    return TaskSpec(id="run_tests", skill="run_command", input=input_)
