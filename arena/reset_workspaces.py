"""Restore every lane worktree to the seeded-bug state on arena-target's main.

Run directly (``uv run python arena/reset_workspaces.py``) or imported and
called from the Arena backend's ``POST /api/reset``.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ARENA_DIR = Path(__file__).resolve().parent
LANES = json.loads((ARENA_DIR / "lanes.json").read_text(encoding="utf-8"))["lanes"]


def reset_lane(workspace: Path) -> dict:
    if not workspace.is_dir():
        return {"workspace": str(workspace), "ok": False, "error": "workspace directory not found"}
    try:
        subprocess.run(["git", "checkout", "--", "."], cwd=workspace, check=True,
                        capture_output=True, text=True)
        subprocess.run(["git", "clean", "-fd"], cwd=workspace, check=True,
                        capture_output=True, text=True)
        subprocess.run(["git", "reset", "--hard", "main"], cwd=workspace, check=True,
                        capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        return {"workspace": str(workspace), "ok": False, "error": error.stderr.strip()}
    return {"workspace": str(workspace), "ok": True}


def reset_all() -> list[dict]:
    results = []
    for lane in LANES:
        workspace = (ARENA_DIR / lane["workspace"]).resolve()
        results.append({"lane": lane["name"], **reset_lane(workspace)})
    return results


if __name__ == "__main__":
    for result in reset_all():
        status = "ok" if result["ok"] else f"FAILED: {result.get('error')}"
        print(f"{result['lane']}: {status}")
