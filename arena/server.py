"""Model Arena backend.

Everything the browser needs goes through this one process. It never talks to
a lane's :S17_CONTROL_TOKEN directly to the browser, and it never exposes a
lane port to the browser -- every SSE stream, diff, and graph view is proxied
and derived server-side. This is what lets the browser sit on one origin
(this process) with zero CORS configuration anywhere.

Run: ``uv run python arena/server.py`` (port 8090 by default).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ARENA_DIR = Path(__file__).resolve().parent
STATIC_DIR = ARENA_DIR / "static"
LANES: list[dict[str, Any]] = json.loads((ARENA_DIR / "lanes.json").read_text(encoding="utf-8"))["lanes"]
LANE_BY_NAME = {lane["name"]: lane for lane in LANES}

CONTROL_TOKEN = os.environ.get("S17_CONTROL_TOKEN", "").strip()
ARENA_PORT = int(os.environ.get("ARENA_PORT", "8090"))

# The initial POST /v1/agent/runs blocks until the whole multi-turn run
# finishes -- give it a generous ceiling, well above anything a real coding
# fix should take, so a genuinely stuck lane still gets caught by the SSE
# watchdog below rather than by this timing out first.
START_TIMEOUT = httpx.Timeout(connect=10.0, read=900.0, write=30.0, pool=10.0)
EVENTS_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)

# No RUN_FINISHED and no new event for this long -> the lane is declared dead
# rather than left showing "running" forever. Generous on purpose: a real
# multi-step coding fix across 3 providers can legitimately take minutes.
WATCHDOG_SECONDS = 600
SSE_RECONNECT_ATTEMPTS = 3
HEARTBEAT_SECONDS = 15

app = FastAPI(title="Model Arena")


class LaneState:
    def __init__(self, name: str, run_id: str) -> None:
        self.name = name
        self.run_id = run_id
        self.status = "starting"  # starting|running|passed|refused|thrashing|errored
        self.error: str | None = None
        self.cost_usd = 0.0
        self.step_count = 0
        self.started_at = time.monotonic()
        self.reasoning_by_step: dict[str, str | None] = {}


class Race:
    def __init__(self, race_id: str, prompt: str) -> None:
        self.race_id = race_id
        self.prompt = prompt
        self.lanes: dict[str, LaneState] = {}


RACES: dict[str, Race] = {}


class StartBody(BaseModel):
    prompt: str


def _lane_url(lane: dict[str, Any], path: str) -> str:
    return f"http://127.0.0.1:{lane['port']}{path}"


async def _start_lane(race: Race, lane: dict[str, Any], lane_state: LaneState) -> None:
    """Fire the blocking POST /v1/agent/runs in the background.

    This is trigger (a) of the 'errored' state: a genuine provider/infra
    failure here (connection refused, timeout, a raw non-2xx that isn't the
    422/503 routes.py itself translates) must not vanish silently -- it is
    the one place a dead lane can go unnoticed if this is not caught.
    """
    body = {
        "tenant_id": "arena",
        "prompt": race.prompt,
        "run_id": lane_state.run_id,
        "respond_as": "text",
        "allowed_side_effects": [
            "read_code", "edit_code", "create_file", "run_command",
            "git_diff", "git_reset", "validate_work", "glob_files", "grep_code",
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=START_TIMEOUT) as client:
            response = await client.post(
                _lane_url(lane, "/v1/agent/runs"),
                json=body,
                headers={"Authorization": f"Bearer {CONTROL_TOKEN}"},
            )
        if response.status_code >= 400:
            lane_state.status = "errored"
            lane_state.error = f"lane returned {response.status_code}: {response.text[:300]}"
    except httpx.HTTPError as error:
        lane_state.status = "errored"
        lane_state.error = f"{type(error).__name__}: {error}"


def _classify(evt: dict[str, Any]) -> str | None:
    """Derive a status transition from one raw AG-UI event, or None if unchanged."""
    etype = evt.get("type")
    if etype == "STEP_FINISHED" and "error" in evt:
        error = str(evt.get("error") or "")
        if error.startswith("GuardError:"):
            return "refused"
        return None
    if etype == "STATE_DELTA":
        delta = evt.get("delta") or {}
        if delta.get("op") == "graph_patched":
            reason = str(delta.get("reason") or "")
            if reason.startswith("stopping:") or reason.startswith("planner node limit reached"):
                return "thrashing"
            if reason.startswith("terminal"):
                return "passed"
            if reason.startswith("planner failed") or reason.startswith("planner call failed"):
                return "errored"
    return None


def _extract_cost(evt: dict[str, Any]) -> float:
    if evt.get("type") != "STEP_FINISHED":
        return 0.0
    delta = evt.get("delta") or {}
    value = delta.get("value")
    if isinstance(value, dict):
        cost = value.get("cost_usd")
        if isinstance(cost, (int, float)):
            return float(cost)
    return 0.0


async def _lane_event_stream(race: Race, lane_state: LaneState):
    """Proxy a lane's raw AG-UI SSE stream, annotated with derived Arena state."""
    lane = LANE_BY_NAME[lane_state.name]
    deadline = time.monotonic() + WATCHDOG_SECONDS

    if lane_state.status == "errored":
        yield _sse(_wrap(race, lane_state, {"type": "ARENA_ERROR", "reason": lane_state.error}))
        return

    lane_state.status = "running"
    url = _lane_url(lane, f"/v1/runs/{lane_state.run_id}/events?reconnect=1")
    attempt = 0
    finished = False
    last_activity = time.monotonic()

    while not finished and attempt < SSE_RECONNECT_ATTEMPTS:
        attempt += 1
        try:
            async with httpx.AsyncClient(timeout=EVENTS_TIMEOUT) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        raise httpx.HTTPStatusError(
                            f"{response.status_code}", request=response.request, response=response
                        )
                    buffer = ""
                    async for line in response.aiter_lines():
                        if time.monotonic() > deadline:
                            lane_state.status = "errored"
                            lane_state.error = f"watchdog: no completion within {WATCHDOG_SECONDS}s"
                            yield _sse(_wrap(race, lane_state, {"type": "ARENA_ERROR", "reason": lane_state.error}))
                            return
                        if line.startswith(":"):
                            continue
                        if not line:
                            continue
                        if line.startswith("data:"):
                            buffer = line[len("data:"):].strip()
                            try:
                                evt = json.loads(buffer)
                            except json.JSONDecodeError:
                                continue
                            last_activity = time.monotonic()
                            new_status = _classify(evt)
                            if new_status:
                                lane_state.status = new_status
                            lane_state.cost_usd += _extract_cost(evt)
                            if evt.get("type") == "STEP_STARTED":
                                lane_state.step_count += 1
                            if evt.get("type") == "RUN_FINISHED":
                                finished = True
                                if lane_state.status == "starting" or lane_state.status == "running":
                                    lane_state.status = "passed"
                            yield _sse(_wrap(race, lane_state, evt))
                            if finished:
                                return
            # Stream ended without RUN_FINISHED and without raising -- retry.
        except (httpx.HTTPError, asyncio.TimeoutError):
            if attempt >= SSE_RECONNECT_ATTEMPTS:
                lane_state.status = "errored"
                lane_state.error = f"SSE connection failed after {attempt} attempts"
                yield _sse(_wrap(race, lane_state, {"type": "ARENA_ERROR", "reason": lane_state.error}))
                return
            await asyncio.sleep(min(1.0 * attempt, 5.0))
            continue

    if not finished:
        lane_state.status = "errored"
        lane_state.error = f"SSE stream ended without completion (elapsed > {WATCHDOG_SECONDS}s)"
        yield _sse(_wrap(race, lane_state, {"type": "ARENA_ERROR", "reason": lane_state.error}))


def _wrap(race: Race, lane_state: LaneState, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "raw": raw,
        "status": lane_state.status,
        "cost_usd": round(lane_state.cost_usd, 6),
        "step_count": lane_state.step_count,
        "elapsed_ms": int((time.monotonic() - lane_state.started_at) * 1000),
    }


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@app.get("/api/lanes")
async def get_lanes() -> dict[str, Any]:
    return {"lanes": [{"name": lane["name"], "label": lane["label"], "provider": lane["provider"],
                        "workspace": lane["workspace"]} for lane in LANES]}


@app.post("/api/race/start")
async def start_race(body: StartBody) -> dict[str, Any]:
    if not CONTROL_TOKEN:
        raise HTTPException(503, "S17_CONTROL_TOKEN is not set in the Arena backend's environment")
    race_id = f"race-{uuid.uuid4().hex[:8]}"
    race = Race(race_id, body.prompt)
    lanes_out = []
    for lane in LANES:
        run_id = f"arena-{lane['name']}-{uuid.uuid4().hex[:8]}"
        lane_state = LaneState(lane["name"], run_id)
        race.lanes[lane["name"]] = lane_state
        asyncio.create_task(_start_lane(race, lane, lane_state))
        lanes_out.append({"name": lane["name"], "label": lane["label"], "provider": lane["provider"],
                           "run_id": run_id})
    RACES[race_id] = race
    return {"race_id": race_id, "prompt": body.prompt, "lanes": lanes_out}


@app.get("/api/race/{race_id}/lanes/{lane_name}/events")
async def lane_events(race_id: str, lane_name: str):
    race = RACES.get(race_id)
    if race is None:
        raise HTTPException(404, "race not found")
    lane_state = race.lanes.get(lane_name)
    if lane_state is None:
        raise HTTPException(404, "lane not found")
    return StreamingResponse(_lane_event_stream(race, lane_state), media_type="text/event-stream")


@app.get("/api/race/{race_id}/lanes/{lane_name}/diff")
async def lane_diff(race_id: str, lane_name: str) -> dict[str, Any]:
    race = RACES.get(race_id)
    if race is None:
        raise HTTPException(404, "race not found")
    lane_state = race.lanes.get(lane_name)
    if lane_state is None:
        raise HTTPException(404, "lane not found")
    lane = LANE_BY_NAME[lane_name]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(_lane_url(lane, f"/v1/agent/runs/{lane_state.run_id}"))
        response.raise_for_status()
    except httpx.HTTPError as error:
        return {"edits": [], "error": f"could not fetch journal: {error}"}
    journal = response.json()
    edits = []
    for node_id, node in journal.get("nodes", {}).items():
        skill = node.get("skill")
        if skill not in {"edit_code", "create_file"} or node.get("state") != "succeeded":
            continue
        node_input = node.get("input") or {}
        result = node.get("result") or {}
        if skill == "edit_code":
            edits.append({
                "node_id": node_id, "skill": skill, "path": result.get("path"),
                "old_string": node_input.get("old_string"), "new_string": node_input.get("new_string"),
            })
        else:
            edits.append({
                "node_id": node_id, "skill": skill, "path": node_input.get("path") or result.get("path"),
                "old_string": None, "new_string": node_input.get("content"),
            })
    return {"edits": edits}


@app.get("/api/race/{race_id}/lanes/{lane_name}/graph")
async def lane_graph(race_id: str, lane_name: str) -> dict[str, Any]:
    race = RACES.get(race_id)
    if race is None:
        raise HTTPException(404, "race not found")
    lane_state = race.lanes.get(lane_name)
    if lane_state is None:
        raise HTTPException(404, "lane not found")
    lane = LANE_BY_NAME[lane_name]
    try:
        # The networkx-backed structural graph (nodes/edges) lives on the
        # journal endpoint, not /v1/runs/{id}/snapshot -- that one returns the
        # AG-UI data-model fold ({results, patches}), a different shape.
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(_lane_url(lane, f"/v1/agent/runs/{lane_state.run_id}"))
        response.raise_for_status()
    except httpx.HTTPError as error:
        return {"nodes": {}, "edges": [], "error": f"could not fetch journal: {error}"}
    body = response.json()
    return {"nodes": body.get("nodes", {}), "edges": body.get("edges", [])}


@app.post("/api/reset")
async def reset_workspaces() -> dict[str, Any]:
    from arena.reset_workspaces import reset_all
    return {"results": reset_all()}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=ARENA_PORT)
