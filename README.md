# Model Arena

Fire one coding-fix prompt at three separately-configured instances of the *same* coding
agent — one pinned to Groq, one to Gemini, one to NVIDIA — and watch them race, live, in a
browser: each one reads the code, edits it, runs the real test suite as the judge, and either
converges on a green test, gets refused for touching a protected file, thrashes without
converging, or genuinely errors out on a provider failure. No mocked failure, no scripted
happy path — the run is real, and so is the possibility that it doesn't work.

## The problem

Which model to trust for *your* coding task, on *your* codebase, is a live, unresolved
question — 2026's own SWE-bench commentary notes the same model can swing 15-20 points just
from a harness change. Copilot Arena does pairwise human-voted comparison of single-turn code
completions; SWE-bench is the industry-standard way to compare agentic coding across models,
but it's a static leaderboard on someone else's fixed task set. Neither does live,
test-verified, run-it-on-your-own-task comparison. Model Arena is a small, honest answer to
that specific gap — a demo artifact proving the idea, not a claim to have solved model
selection in general.

## Demo

**[Model Arena — live demo](https://youtu.be/Gpul2tY4A_Q)**

## Architecture

```
                    Browser (you)
                    http://127.0.0.1:8090
                         │
                         │  (the ONLY origin the browser ever talks to —
                         │   no CORS, no lane port ever exposed, no token
                         │   ever reaches client-side JS)
                         ▼
                 arena/server.py  (holds S17_CONTROL_TOKEN)
              ┌──────────┼──────────┐
      POST /v1/agent/runs, one bearer token per lane
              ▼          ▼          ▼
        S17Code     S17Code     S17Code       ← the exact same codebase,
        lane: groq  lane: gemini lane: nvidia   launched 3× with different
        :8113       :8114        :8115          env vars. No forking.
        worktree A  worktree B   worktree C    ← 3 git worktrees off one
              └──────────┼──────────┘            seeded-bug repo, so 3
                         ▼                        agents edit independently
              glc_v5 gateway :8111                without racing on files.
              holds every provider key;
              S17Code holds none
```

## Prerequisites

- Python 3.13, `uv`
- A running `glc_v5` gateway (`uv run glc serve`, port 8111) with at least one of
  Groq/Gemini/NVIDIA configured
- Windows or POSIX

## Setup

```bash
git clone <this repo> S17Code && cd S17Code
git checkout part1-model-arena
uv sync

# Seeds a small demo repo (arena-target, sibling to this one) with two
# deliberate bugs, then a git worktree per lane -- one per provider, so each
# agent edits its own isolated checkout without racing on the same files.
# Self-contained: no second repo to find or clone.
uv run python arena/seed_target.py

# Generate one shared token every lane + the backend must agree on:
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Copy each arena/env/*.env.example -> *.env (same basename, drop .example),
# fill in the token above and your own absolute paths.
```

## Configuration

Every lane needs its own `.env` file (see `arena/env/*.env.example`) — one plain `.env` at the
repo root can't hold three different configs, since `s17code/main.py` always loads exactly
`<repo root>/.env`. `uv run --env-file <path>` is what lets each lane point at its own file
instead.

| Var | Meaning |
|---|---|
| `S17_PORT` | this lane's HTTP port (8113/8114/8115) |
| `S17_GATEWAY_PROVIDER` | which provider this lane is pinned to |
| `S17_GATEWAY_MODEL` | optional — pin a specific model too, if the provider's own default has gone stale (ours had; see Design notes) |
| `S17_WORKSPACE` | this lane's own git worktree |
| `S17_DATA_DIR` / `S17_SANDBOX_ROOT` | must be unique per lane — 3 concurrent processes sharing one would stomp on each other |
| `S17_A2A_GRPC_ENABLED=0` | Model Arena doesn't use A2A; avoids a real port collision otherwise (gemini's HTTP port and A2A's default port are both 8114) |
| `S17_CONTROL_TOKEN` | the one shared value across all 4 processes |
| `GLC_BASE_URL` | `http://127.0.0.1:8111`, same for all three lanes |

## Run

Four terminals, from the `S17Code` repo root in each:

```bash
uv run --env-file arena/env/groq.env s17code serve
uv run --env-file arena/env/gemini.env s17code serve
uv run --env-file arena/env/nvidia.env s17code serve
uv run --env-file arena/env/backend.env python -m arena.server
```

Open `http://127.0.0.1:8090`. Between takes, click **Reset workspaces** (or
`uv run python arena/reset_workspaces.py`) to restore all three worktrees to the seeded-bug
state — no shell access needed mid-recording.

## Example

Prompt:
> Fix mathkit/stats.py so average([]) raises a ValueError instead of ZeroDivisionError. Make
> tests/test_stats.py pass without editing it.

`mathkit/stats.py` on a clean checkout:
```python
def average(values):
    return sum(values) / len(values)
```
`average([])` divides by zero — an accidental `ZeroDivisionError`, not the deliberate
`ValueError` the test suite expects. A lane that fixes it correctly adds an empty-input guard
before the division; a lane that can't converge shows up honestly as `thrashing` or `errored`,
not a silently-passing green badge.

A second seeded bug, `mathkit/dedupe.py`, gives more room for lanes to diverge on *how* they
fix it, not just whether: `list(set(items))` removes duplicates but Python sets carry no
ordering guarantee, silently breaking the function's own promise to preserve first-seen order.

To see a refusal instead: point a prompt at editing `tests/test_stats.py` directly (or any
path under `S17_PROTECTED_PATHS`'s default set — `tests/**`, `pyproject.toml`, `.github/**`,
etc.) — the guard refuses before the edit ever reaches disk, and the lane shows `refused`.

## Design notes

- **Why `S17_GATEWAY_PROVIDER` for pinning, not `config/tiers.yaml`'s budgeted path.**
  `tiers.yaml` lets a budgeted run's own per-tier `provider` field silently override
  `S17_GATEWAY_PROVIDER`, which would break provider pinning outright. Model Arena runs
  unbudgeted on purpose — confirmed by reading `s17code/gateway.py` and `runtime.py` directly
  — so every call in a lane goes through the one `llm` callable, and `S17_GATEWAY_PROVIDER`
  genuinely governs all of it.
- **Why the browser never talks to a lane directly.** `arena/server.py` proxies every SSE
  stream, diff, and graph view. This is what makes CORS a non-issue and keeps
  `S17_CONTROL_TOKEN` server-side only, matching the assignment's own explicit warning: read
  endpoints are open by design, but the route that starts work fails closed without a token.
- **Five lane states, not four.** `running/passed/refused/thrashing` are all derivable from
  the AG-UI event stream's own vocabulary — but none of them fit a genuine provider/infra
  failure (a rate limit, a decommissioned model, a network error), and with 5 real providers
  behind this, that happened repeatedly during development, not hypothetically. `errored` is a
  fifth state, set by the Arena backend itself (not the harness) on a failed start request, an
  SSE stream that can't reconnect, or a 10-minute watchdog with no completion.
- **Cost is a disclosed lower bound, not total run spend.** The gateway's own `/v1/chat`
  response already carries a computed `cost` object; `s17code/gateway.py` now captures it
  (same additive pattern as the reasoning-text field PR#17 restores). But only calls whose
  result flows through a graph node are covered — the planner's own per-turn decision calls
  aren't attributed to any node in the unbudgeted path, so the scoreboard's dollar figure is a
  real, gateway-computed number that understates the true total. Extending that into
  `planner.py`'s core loop was judged out of proportion for a demo feature; verified instead by
  hand-checking two providers' actual reported costs against their published $/Mtok rates —
  both reconciled exactly.
- **Two already-claimed harness bugs, applied locally, not filed.** Building this surfaced two
  real defects blocking a clean demo run: `run_command_worker` returned a raw `CommandResult`
  object instead of a dict, so no `run_command` call — including a passing test — could ever
  report cleanly (crashes inside the idempotency outbox's own durability write, caught and
  reported as an ordinary `task_failed`); and on Windows, `run_command`'s subprocess
  environment dropped `SYSTEMROOT`, breaking anything that imports `asyncio` (pytest's own
  plugin loader does) with `WinError 10106`. Both were independently found while building this
  product, then confirmed already filed upstream (PR#10/PR#2, and PR#3, respectively) before
  touching anything — applied locally with their own failing-test-first proof so the demo could
  run at all, not resubmitted as new PRs.

## Observations from a full day of live testing

Everything below was measured against real, running processes and real provider accounts —
none of it is projected or assumed. Kept here because it's more useful to a reader than
pretending the final config was the first thing that worked.

**Two more real harness bugs, found building this, already claimed by other students:**
- `run_command_worker` (`s17code/workers/coding.py`) returned the raw `CommandResult`
  dataclass instead of calling its own `.as_dict()`. Every `run_command` call — including a
  passing test run — crashed trying to persist that result, surfacing as an ordinary
  `task_failed` event indistinguishable from a genuine test failure. Root-caused all the way
  to `events/outbox.py`'s durability write. Already filed upstream as PR#10 and PR#2; applied
  locally only, with its own failing-test-first proof, so this demo's judge could actually
  report a result.
- `run_command`'s subprocess environment dropped `SYSTEMROOT` on Windows, so anything
  importing `asyncio` (pytest's own plugin loader does) crashed with `WinError 10106`. Already
  filed upstream as PR#3; applied locally only, same reasoning.

**Provider/model reliability, tested exhaustively, not assumed:**
- Groq's default model (`openai/gpt-oss-120b`) has an 8K tokens-per-minute free-tier cap,
  confirmed live via a real `429` — easily exhausted by a coding agent's large prompts. Every
  other plain model on the account shares that same cap. `groq/compound-mini` has its own
  much larger pool and isn't actually billed on the free tier, but it's an *agentic* system,
  and on the real planner prompt it internally delegated to sub-models the account couldn't
  reach — reverted in favor of the plain, rate-limited-but-predictable model.
- Gemini's newest model (`gemini-3.7-flash`) hung for 2+ minutes under real demand pressure;
  `gemini-3.1-flash-lite` was reliably fast and correct instead.
- NVIDIA's configured default model had been decommissioned server-side. Nine NVIDIA-hosted
  models were tested; most were 404, decommissioned, or never responded at all.
  `nvidia/nemotron-3-super-120b-a12b` was the most reliable found, though even it produced
  malformed planner JSON in one real race — free-tier model reliability is genuinely
  inconsistent, not a solved problem.
- Local Ollama models were tested as a rate-limit-free alternative: all four pulled models
  worked correctly but took 13–34 seconds for a two-word reply, which would make a single
  race take minutes rather than seconds — reliable but impractical for a live demo on this
  hardware.
- Cerebras returned `402 Payment Required` — free credits exhausted, not usable without
  billing.
- OpenRouter's free-tier models sit behind a shared capacity pool across *all* their users
  globally — hit a real `429` from that shared pool within two calls, ruling it out as a fix
  for Groq's rate limit.

**Two Arena-specific bugs found live, by actually using the running app:**
- Browsers auto-reconnect `EventSource` connections on any stream close, including ones the
  backend intended as final — a lane that errored or finished replayed its terminal event
  forever, flooding the log. Fixed on both sides: the frontend now closes its own connection
  on a terminal event; the backend short-circuits a stray reconnect instead of re-streaming a
  finished lane's full history.
- There was no way to stop watching a stuck lane. Added a per-lane abort control, with an
  honest disclosure that it reliably stops the *UI* from waiting, but can't guarantee killing
  an in-flight provider call already running on the lane's own server, since the harness has
  no cancellation endpoint.

**A genuine thrashing case, root-caused to the actual bytes, not guessed at:** one race showed
a lane thrash through 4 failed verifications and stop itself. Pulling the model's own raw
`edit_code` arguments from the run journal showed it submitted syntactically invalid Python —
wrong indentation on the first attempt, a literal two-character `\n` instead of a real newline
on the second. The harness's own repeat-failure guard caught it correctly, exactly as
designed.

## Transparency statement

- The idea came from brainstorming with ChatGPT, Gemini, and Claude — around 20 candidate
  product ideas were reviewed in depth, each weighed against several criteria (real problem,
  hard to clone, buildable reliably on the harness available), before Model Arena was chosen.
  That reasoning trail is preserved in `docs/S17-Part1-Plan.md`.
- The architecture, the Arena backend and frontend, the two harness fixes documented above,
  and every provider/model reliability test in this README were built and run by Claude Code
  across an extended session.
- I did not accept its output passively. I reviewed the implementation as it was built,
  questioned specific design decisions back to it, and required it to justify or change
  choices before I accepted them — including catching it giving me an incorrect explanation
  of exactly where one of the two harness bugs actually crashed, and making it re-verify with
  a live repro before I accepted the fix.
- I verified the actual running behavior myself, manually — starting every lane and the
  gateway in my own terminals, watching real races, reporting back what I actually saw
  (including bugs it hadn't caught, like the log-spam issue and the abort gap), and directing
  the troubleshooting priorities in real time rather than accepting the first proposed fix.
- Judgment calls that were mine, not the agent's, by explicit decision: which already-claimed
  harness bugs to fix locally versus leave alone, ruling out paid models even when a paid
  option would have been more reliable, which working documents to publish alongside the
  code, and the final scope cuts (e.g., not pursuing a bigger architecture change to make
  provider selection dynamic, once a simpler fix was found).
- The two required Part 2 bug-fix PRs (`S17Code` #6 and #17, `glc_v5` #29 and #30) were
  investigated and filed in an earlier session, separate from the one that built this product.

---

# S17Code — a general live-graph agent

S17Code takes S15's durable graph, memory, A2A, UI, budget controller and
telemetry as its foundation, then replaces the task-shaped planner with a
general capability-driven agent loop. `glc_v5` connects that loop to every
enabled gateway channel through one shared envelope.

The planner does **not** build the whole DAG up front. It proposes only the next
runnable frontier, the runtime launches independent nodes together, and every
outcome causes another planning round. The graph therefore grows from evidence:

```text
goal → plan next frontier → run independent work concurrently
     → observe real outcomes → critique evidence → expand or answer
```

There is no prompt classifier, benchmark router, `_work_intent`, or deterministic
task fallback. A model may propose work, but Python owns the boundary: only
registered capabilities with valid arguments and valid existing dependencies
can enter the graph.

## What makes it general

- `s17code/capabilities.py` is the complete manifest the planner sees. It
  describes what the agent can do and strictly validates every argument.
- `s17code/planner.py` asks for only the next useful frontier. A new task may
  depend only on evidence that already exists—not on an imagined future task.
- Independent tasks in one frontier run concurrently. Synthesis is held until
  active siblings finish, so the agent does not answer while useful evidence is
  still arriving.
- Before a terminal answer, a separate evidence-readiness pass checks the
  original request against accumulated outcomes. Missing facts cause more work,
  not cosmetic rewriting.
- Equivalent active work is deduplicated even when the planner invents a new
  node ID. Run and frontier limits keep an unproductive loop finite.
- Invalid planner output is repaired through the model and recorded. If repair
  fails, the run fails visibly; it never switches to a hidden, hardcoded agent.

## Capabilities

The shipped registry includes scoped memory recall and explicit remembering,
semantic document indexing, web search and URL reading, bounded research,
retrieval/distillation/validation, sandboxed file access, calendar artifact
creation, A2A delegation, UI composition and evidence-grounded answers.

Web research uses a multi-backend search client and then reads the returned
pages. Search snippets and pages are untrusted evidence. Crucially, if search
finds no usable URL—or no page can be read—the researcher returns
`insufficient: true` and does **not** ask a model to synthesize facts.

## Unattended operation

Everything above assumes somebody asked. The autonomy layer is what the harness
adds for the case where nobody did, and where nobody is watching either.

- `s17code/events/` normalises cron ticks, webhooks, Gmail Pub/Sub, channel
  messages and job callbacks into one `EventEnvelope`, deduplicates on
  `(source, id)`, and records a relevance decision for every matching
  subscription — including the decisions that were "no".
- **Events are facts; subscriptions are intent and authority.** An event can
  never write the instruction, the allowed side effects or the budget that
  govern it. That is why writing a subscription is a control-plane action.
- `s17code/auth.py` gates every write path and **fails closed**. With no
  `S17_CONTROL_TOKEN` configured, `PUT /v1/agent/subscriptions/{id}`,
  `POST /v1/agent/events`, `POST /v1/agent/runs` and the resume route all answer
  `503` rather than serving anonymously. Job callbacks hold a separate token.
- `s17code/events/governor.py` bounds operation over a **window**, not a
  request. A per-run ceiling does not bound an agent that starts its own runs;
  `daily_budget`, `max_runs_per_day` and `daily_triage_budget` do. It also
  rate-limits per source and refuses events this agent itself caused, so a reply
  into a watched mailbox cannot become a loop.
- Every refusal is recorded. A control that prevents work leaves no other trace,
  and without the record a well-defended night and an idle night look identical.
- `s17code/events/lease.py` stops a periodic trigger overlapping itself, and
  reports a skip rather than silently doing nothing.
- `s17code/events/report.py` publishes a heartbeat (`GET /v1/agent/liveness`,
  `503` once stale) and the human-readable account of a period nobody watched
  (`GET /v1/agent/report`), which costs **watching** separately from **doing**.

```bash
uv run python proofs/p_naive_vs_bounded.py    # naive vs gated vs bounded, same stream
uv run python proofs/p_autonomy_bounds.py     # seven properties of the ceilings
```

Both take their event stream and every ceiling as arguments, so they run against
work they have never seen, and both exit non-zero on failure.

## Inherited production boundaries

- `s17code/core/live_graph/`: event-sourced executor, patches and replay
- `s17code/core/memory/`: typed, scoped memory and semantic chunking
- `s17code/core/a2a/`: Agent Cards, JSON-RPC and optional gRPC
- `s17code/ui/`: catalog validation, A2UI surfaces, AG-UI and HITL
- `s17code/economics/`: model tiers, hard budget admission and ledger
- `s17code/telemetry/`: journal-to-OpenTelemetry span export
- `s17code/evals/`: generic resolution judging

The graph journal remains the source for replay, UI events and telemetry. All
gateway model calls—including planning and evidence review—pass through S15's
metered call seam. `glc_v5` remains a separate service and owns provider keys;
S17Code contains none.

## Run locally

Start `glc_v5` on port `8111`, then:

```bash
uv sync
cp .env.example .env
uv run pytest -q
uv run ruff check .
uv run s17code serve
```

S17Code defaults to `http://127.0.0.1:8113`. Useful environment variables are
documented in `.env.example`; most importantly:

```text
GLC_BASE_URL=http://127.0.0.1:8111
S17_GATEWAY_PROVIDER=gemini
S17_SANDBOX_ROOT=/absolute/path/the-agent-may-read
S17_CHANNEL_BRIDGE_TOKEN=the-same-private-value-used-by-glc-v5
S17_CONTROL_TOKEN=required-or-every-write-path-answers-503
S17_COMPLETION_TOKEN=a-different-token-for-job-callbacks
```

The control plane has no unauthenticated mode. `if expected and not
compare_digest(...)` reads like a check and behaves like an open door on a fresh
checkout, so these gates refuse to serve instead.

Do not put provider keys in S17Code. `glc_v5` can rotate among its configured
Gemini keys behind the one logical `gemini` provider.

## Channel operation and proof

GLC converts provider-specific payloads; S17 sees only the canonical envelope.
An inbound message creates a real live-graph run, and its terminal result is
returned on the originating channel and thread. A same-thread reply can satisfy
a waiting human-approval node. An external job callback can resume a sleeping
run and proactively send its completed answer through GLC.

The channel list is discovered from GLC at runtime:

```bash
curl -s http://127.0.0.1:8111/v1/channels | jq
```

The 20-prompt stress catalogue spans every shipped channel and checks observable
capability families, parallel frontiers, and wait/resume events—not prescribed
node IDs or a prompt-specific graph:

```bash
# Start this proof S17 with only local fixture mutations authorised:
S17_CHANNEL_ALLOWED_SIDE_EFFECTS=remember_explicit_fact,write_file,index_file,create_calendar_events,request_approval \
  uv run s17code serve

# In another shell, use the installation token printed from glc_v5:
GLC_INSTALL_TOKEN=<glc-v5-install-token> \
  uv run python proofs/channel_stress.py \
  --glc http://127.0.0.1:8111 --s17 http://127.0.0.1:8113
```

Run the proof with channel authority limited to the local fixture capabilities
shown in `proofs/channel_stress.py`. It injects canonical envelopes locally and
fails any scenario that invokes `send_channel_message` or `launch_job`, so it
cannot silently count an external delivery as proof.
Native provider payload conversion remains the responsibility of each GLC
adapter's tests. Its JSON report contains each original prompt, actual graph
capabilities, reply, event count, parallel/wait/resume evidence, and result.

GLC recomputes sender trust from pairing state before the message reaches S17.
Only a gateway-verified installation owner receives the side-effect authority
listed in `S17_CHANNEL_ALLOWED_SIDE_EFFECTS`; other allowed senders remain
read-only.

Example:

```bash
curl -s http://127.0.0.1:8113/v1/agent/runs \
  -H 'content-type: application/json' \
  -d '{
    "tenant_id":"demo",
    "project_id":"general-agent",
    "user_id":"student",
    "prompt":"Research Rust and Go independently, then compare their concurrency models. Explain one situation where each is the safer choice."
  }' | jq '{status, answer, graph: .graph.nodes, planner: .trace.planner}'
```

## Replaceable live proof

`proofs/tasks/general_agent.jsonl` is data, not routing code. Replace its prompts
with unseen tasks and run the same HTTP harness against a live S17 process:

```bash
S17_PORT=8116 uv run s17code serve
uv run python proofs/general_agent_live.py \
  --base-url http://127.0.0.1:8116 \
  --tasks proofs/tasks/general_agent.jsonl
```

The output at `proofs/out/general_agent_live.json` retains each prompt, final
answer, every node and edge, every accepted graph patch, planner decisions,
evidence review and timing. This is the inspectable proof of behavior—not a
claim that a prompt "worked."

The inherited S15 economics proofs remain available in `proofs/`. They test the
same runtime's budget ceiling, denial-of-wallet protection, trace export,
semantic-cache savings and cross-model tier ladder.

## Honest limits

A general agent is bounded by its registered capabilities, source availability
and models. The evidence critic is an additional model judgment, not a theorem.
The hard guarantees are narrower and enforced in code: authority validation,
existing-evidence dependencies, bounded graph/frontier size, deduplication,
metered provider calls, budget admission, durable outcomes, and no research
synthesis without readable sources.

Provider adapters vary in how much live external delivery they implement. The
connection proof establishes that every registered adapter reaches the S17 seam;
it is not a claim that unconfigured Gmail, Twilio, or Slack accounts can send.
