# S17 Part 2 — Bug Ledger

**Last re-checked against upstream: 2026-08-19.** glc_v5 sits at 28+ open PRs (2 of which,
#29/#30, are this session's own G2/G8; #33 is this session's own G1). S17Code at 16 (1 of
which, #17, is this session's own S2).
The repos are being actively hunted — S17Code went from 5 to 15 open PRs in about a day.
Re-run `gh pr list` immediately before opening anything, every time.

## Checklist — in-progress work

| Item | Repo | Branch | Status |
|---|---|---|---|
| S11 + S12 | S17Code | `part2-s17-guard-reachable` | ✅ **Filed** — [PR#6](https://github.com/theschoolofai/S17Code/pull/6) |
| **G2** (Ollama reasoning toggle) | glc_v5 | `part2-glc-ollama-reasoning-toggle` | ✅ **Filed** — [PR#29](https://github.com/theschoolofai/glc_v5/pull/29) |
| **G8** (OpenAI-compat reasoning discarded) | glc_v5 | `part2-glc-openai-compat-reasoning-text` | ✅ **Filed** — [PR#30](https://github.com/theschoolofai/glc_v5/pull/30) |
| S2 | S17Code | `part2-s17-reasoning-channel-json` | ✅ **Filed** — [PR#17](https://github.com/theschoolofai/S17Code/pull/17) |
| **G1** (SMTP EHLO hostname) | glc_v5 | `part2-glc-smtp-ehlo-hostname` | ✅ **Filed** — [PR#33](https://github.com/theschoolofai/glc_v5/pull/33) |
| G3 | glc_v5 | — | ⚪ **Deferred, not filed.** Confirmed real, but S17Code never triggers it (see G3 entry) |
| G5 | glc_v5 | — | ❌ **Fixed upstream (backport `9f2b647`), not filed** — see G5 entry |

**Filed: [theschoolofai/S17Code#6](https://github.com/theschoolofai/S17Code/pull/6) —
S11 + S12**, on branch `part2-s17-guard-reachable`. Both tests measured RED on `main`
before the fix, GREEN after; 64 passed / 1 pre-existing skip on the targeted regression.
No new conflicts on re-check after filing.

**Filed: [theschoolofai/S17Code#17](https://github.com/theschoolofai/S17Code/pull/17) —
S2**, on branch `part2-s17-reasoning-channel-json`. **This is not the defense-in-depth
JSON-parser fix described in the S2 entry below** — that scope was superseded by a full
architecture re-investigation (see `S2-Impact-Analysis.md`) that found the real, live gap:
`GatewayClient.chat()`, the one chokepoint every call to `glc_v5` passes through, hardcodes
its return to 9 fields and silently drops `reasoning_text` the moment glc_v5 PR#29/PR#30
start sending it. Filed scope is Tier A (restore the field through `gateway.py` and
`economics/controller.py`) + Tier B (surface it through `events/engine.py`'s triage
decision and `telemetry/spans.py`'s existing opt-in PII-gated span attributes). Tier C (the
JSON-parser defense-in-depth below) is documented in the PR body as a deferred future
opportunity, not implemented. 10 new tests, RED before (`KeyError`/`ImportError`) GREEN
after (70 passed); targeted regression 28 passed; full suite 493 passed with only the one
known pre-existing tzdata failure. **Depends on glc_v5 PR#29 and PR#30 — both still open —
to carry a non-`None` value**; correct but inert until either merges. No new conflicts on
re-check after filing.

**Filed: [theschoolofai/glc_v5#33](https://github.com/theschoolofai/glc_v5/pull/33) —
G1**, on branch `part2-glc-smtp-ehlo-hostname`. Re-authored as a single-file change off a
freshly-updated `main` (the original fix was proven live against Gmail on an 8-file bundled
branch, `part1-channel-bridges`). Mid-review, tracing exactly what smtplib's own default does
for a dotless `socket.getfqdn()` result surfaced one more real gap — the initial fix would
have sent a bare hostname where smtplib's own default promotes to a bracketed IP literal —
closed before filing so the fix is a strict superset of smtplib's existing behaviour rather
than a trade-off. 7 tests total (5 for the core bug, 2 added for the dotless-hostname case),
RED before each, GREEN after; full suite 541 passed, only the two pre-existing failures
already cited on PR#29/#30, 1 skipped. No new conflicts on re-check after filing. While
pulling `main` for this branch, found an official backport (`9f2b647`) had separately fixed
**G5** — logged below, not filed.

**Investigation closed, three PRs filed: G2, G8, S2 — each its own branch, each walked
through (issue / fix / files / before-after) before push.** All three trace back to the
user's own live-session observation about Qwen3's `<think>` tags breaking JSON parsing.
The investigation used a hand-built Postman collection hitting both `glc_v5` and each
provider's raw API directly (Ollama, Groq, Cerebras) — full findings below, ahead of the
per-finding entries.

Every entry below states its **evidence level** honestly:

- **MEASURED** — I executed it and observed the wrong behaviour myself.
- **REPORTED** — a subagent executed it; I have not independently re-run it. *Not to be
  filed until re-verified.*
- **READ** — code inspection only, no execution.

Nothing gets a branch until it is MEASURED by me. This matters: a subagent reported the a2a
terminal-state bug as "already fixed", and it turned out to be a live race failing 3 runs in
4 (S13). Another reported the dotted-path guard bug as live when it is genuinely fixed.

---

## The reasoning-toggle investigation — what the live evidence actually showed

Full raw request/response captures live in `htmlcov/ollama-direct/`, `htmlcov/groq-direct/`,
`htmlcov/cerebras-direct/`, and the through-`glc_v5` captures alongside them. The Postman
collection itself is `htmlcov/glc_reasoning_toggle.postman_collection.json` (two halves:
through `glc_v5`, and direct to each provider's native API, bypassing the gateway).

**Ollama's real `/api/chat` `think` field is confirmed to accept graduated string levels,
not just boolean.** Sending anything invalid returns
`must be "high", "medium", "low", "max", true, or false` — verified live. `"max"` isn't
reachable from `glc_v5`'s own schema (`Literal["off","low","medium","high"]`), so it's out
of scope unless the schema itself is extended.

**But at a realistic token budget, only `false` actually solves anything.** Every other
state — `unset`, `true`, `"low"`, `"medium"`, `"high"`, `"max"` — hit the exact same wall on
`qwen3:8b`: `eval_count: 300` (full budget consumed), `done_reason: "length"`, `content: ""`.
None of them finished thinking in time to produce an answer. Only `think: false` produced a
correct, complete answer (`"391"`, `done_reason: "stop"`, 118 of 300 tokens used).

**Ollama's native response already separates reasoning from the answer, into
`message.thinking` — `glc_v5`'s `OllamaProvider.chat()` reads only `message.content` and
silently discards `thinking` entirely.** Since `content` is empty whenever thinking isn't
suppressed, what reaches a caller today isn't a `<think>`-tagged string — it's an empty one.

**Groq and Cerebras behave differently, and better, by default.** Their raw OpenAI-compat
responses *always* separate reasoning into its own `message.reasoning` field — even when
`reasoning` is completely unset — and `content` stays clean and usable regardless:
```json
"message": {
  "content": "...391",
  "reasoning": "We need compute 17*24 = 408. sqrt(289) = 17..."
}
```
So on these providers, the failure isn't broken parsing — it's that `reasoning` tokens are
generated and billed (`usage.completion_tokens_details.reasoning_tokens`: 49 at unset, 21 at
`low`, 189 at `high`, all measured live) and then **permanently discarded by `glc_v5`**,
unconditionally, on every call where any reasoning happens. No caller, at any layer, can
ever observe why a model produced a given answer.

**There is no real "off" state on Groq for `gpt-oss-120b`.** `reasoning_effort: "none"` and
`reasoning_effort: "default"` are both rejected identically —
`must be one of 'low', 'medium', or 'high'` — despite a *different* validation error
elsewhere listing `['none','default','low','medium','high']` as allowed. Groq's own API is
internally inconsistent about this. The lowest real, working state is `"low"`, which is
exactly where `glc_v5`'s existing healing/retry loop already lands after two rejections —
**this path is already correct, nothing to fix for Groq's `off` handling itself.**

**Cerebras rejects the same request Groq recovers from, and `glc_v5`'s healing loop doesn't
recover.** Same literal payload, same model (`gpt-oss-120b`, since `zai-glm-4.7` retired
mid-investigation): Groq heals through two rejections to `"low"` and succeeds; Cerebras
returns one differently-worded 400 and `glc_v5` surfaces a raw 502 instead of retrying. The
healing code's error-string matching is very likely tuned to Groq's specific wording only —
logged as a fourth, distinct finding, not yet scoped for a fix.

**Bottom line — how G2, G3, S2 and the new G8 finally shape up:**
- **G2** (fix): `OllamaProvider` must read `reasoning` and translate it faithfully
  (`off`→`false`, `low/medium/high`→the same string), fix the always-`False`
  `reasoning_applied`, and additively capture `message.thinking` into a new `reasoning_text`
  key rather than discarding it.
- **G3** (deferred, not filed): confirmed real — unset sends nothing on Groq/Cerebras either
  — but `S17Code` always sends `reasoning: "off"` explicitly
  (`s17code/gateway.py:38`), so it never takes this path. Real for other callers, not a risk
  to this system.
- **G8** (new, fix): `OpenAICompatProvider.chat()` must also capture the provider's separate
  `message.reasoning` field into `reasoning_text`, instead of paying for it and throwing it
  away on every call.
- **S2** (fix, scope narrowed): once G2 ships, `content` reaching `S17Code` via Ollama is
  reliably clean, since `S17Code` always sends `"off"`. S2 stops being the fix for a live
  failure and becomes **defense-in-depth** — cheap, still worth doing, protects against any
  model/deployment that genuinely embeds `<think>` inline (some self-hosted vLLM/SGLang
  GLM/Qwen builds do, per `chat_template_kwargs.thinking` already in this codebase). Add an
  explicit empty-string test case alongside the original `<think>`-tag one, since empty
  string is what's actually been proven to reach `S17Code` via this path today.
- **The Cerebras healing gap** — logged, not yet scoped for a fix, lowest priority of the
  four.

---

## Status summary

**Recounted 2026-08-19 — the previous total (17) undercounted; corrected against the actual
itemised list below rather than carried forward.**

| | Count |
|---|---|
| Total findings | 23 (S1–S15, G1–G8; the Cerebras healing gap is logged but not yet numbered) |
| **Lost to an upstream PR** | 3 (S1, S5, S13) |
| **Fixed upstream via official backport, not filed** | 1 (G5) |
| Filed | 6 (S11, S12, G2, G8, S2, G1) |
| **Already claimed, applied locally only, not filed** | 2 (S14, S15 — found via a different investigation, see below) |
| Deferred, not filed | 1 (G3) |
| Remaining, unclaimed, not yet filed | **10** (S3, S4, S6, S7, S8, S9, S10, G4, G6, G7 — 5 of these now contested by file overlap with newer PRs, see the 2026-08-22 re-check above) |

**Filing order — done: S11+S12 → PR#6, G2 → PR#29, G8 → PR#30, S2 → PR#17, G1 → PR#33.** All
five confirmed-and-scoped candidates from the original plan are now filed. The 10 remaining
findings are REPORTED only (a subagent found them; not yet independently MEASURED by
execution) — per the standing rule, nothing gets a branch until that happens, exactly the
step that would have caught S13 being already fixed before it was lost to PR#15.

> **Note on urgency.** S17Code went from 5 to 16 open PRs in about two days
> (2026-08-17 → 2026-08-19); glc_v5 sits at 28+. S13 and S5 were lost to new PRs, and G5 was
> separately fixed by an upstream backport, all mid-investigation. Re-run `gh pr list`
> immediately before opening anything, every time — not just once per session — and re-check
> `main` for unrelated upstream movement in the same files before trusting an old "unclaimed"
> read.

**Re-checked 2026-08-22, via `gh pr list --state all` against both repos (this was a
file-overlap check only — confirms whether another PR touches the same file, not whether it
fixes the same bug; a real filing attempt still needs each candidate PR's diff read before
trusting "unclaimed").**

Still genuinely untouched by any PR (`S17Code`): **S7**, **S8**, **S9** — all in
`skills/generic.py`/`skills/manager.py`, zero overlap. Still genuinely untouched (`glc_v5`):
**G4** (`glc/cache/semantic.py`) and **G6** (`glc/channels/catalogue/twilio_sms/webhook.py`).

Now contested — another PR touches the same file, overlap unconfirmed, would need that PR's
diff read before filing: **S3** (`s17code/capabilities.py`, PR#40), **S4**/**S6**
(`s17code/reasoning/jitrl.py`, PR#38 and PR#14), **S10** (`s17code/runtime.py`, PR#40, #21,
#20, #11), **G3** (`glc/providers.py`, now PR#44 and PR#38 on top of the already-known #28 —
still deliberately deferred regardless), **G7** (`glc/policy/engine.py`, PR#26) — checked
PR#26's actual diff specifically for this one, since a merged backport fixed G5 as a
side-effect before: it does not touch encoding/locale reads anywhere, only the G5
path-normalization fix in the same file. **G7 is confirmed still real and unfixed**, just
carries file-overlap risk with #26 if filed.

Two more findings, from a separate investigation (building Model Arena, Part 1 — not the
original Part 2 hunt), added below as **S14** and **S15**. Both MEASURED, both confirmed
already claimed upstream before anything was touched, both applied **locally only** —
deliberately not filed as new PRs. See their entries under S17Code findings for the full
trace.

---

# S17Code findings

## S1 — `workers/special.py` never imports `os` ❌ **LOST**

**Claimed by upstream PR#5** (2026-08-17), `+1/-0` plus `tests/test_validate_work_runs.py`.
Independently found here first, but not filed in time. Do not submit.

*What it was:* `os.getenv`/`os.environ` used at lines 33, 42, 55 with no import, so
`validate_work` — the separate-agent validator the whole session is built around — raised
`NameError` on first call. A regression from `69c48c4` (the worker extraction), where the
code moved out of `runtime.py`, which does import `os`.

---

## S11 + S12 — the protected-path guard is reachable two ways ✅ **FILED — PR#6**

**Files:** `s17code/coding/edit.py`, `s17code/coding/guard.py`
**Unclaimed.** PR#3 touches `coding/exec.py`, a different file.

This is the strongest finding in the set, because it defeats commitment #1 of the session:
*"The judge is out of reach. Tests, CI and packaging are refused… **Every other guarantee
depends on that one.**"*

### S11 — `copy_code_file` never calls the guard at all

`guard_path` has exactly two call sites: `edit.py:75` (`apply_edit`) and `edit.py:116`
(`create_file`). `copy_within_workspace` (`edit.py:131-166`) does not call it.

`copy_code_file` is exposed straight to the model (`capabilities.py:394`,
`workers/coding.py:47`), so the judge is replaced in two ordinary capability calls:

```
create_file("scratch.py", content="<a trivially passing test>")
copy_code_file(source="scratch.py", destination="tests/test_calc.py", overwrite=True)
```

The function's own docstring claims *"it still refuses to clobber"* — but that only covers
`overwrite=False`; nothing consults the guard on either path. `copy_code_file` arrived in
`7bd63b0`, later than the guard, which is how it missed the invariant. It then does
`ledger.read.add(destination)` at `:165`, so the overwritten file becomes editable too.

### S12 — the guard runs before the path is normalised

`apply_edit` calls `guard_path(relative)` at `edit.py:75` **before**
`workspace.resolve(relative)` at `:78`. `is_protected` (`guard.py:48-51`) strips a leading
`./` and `/` but never collapses `..`; `Workspace.resolve` (`workspace.py:46`) calls
`.resolve()`, which does — and only refuses paths landing *outside* the workspace, which
these do not. So the guard and the file-opener disagree about which file is being touched.

Measured:

| path the agent names | guard verdict | file actually opened |
|---|---|---|
| `a/../.github/workflows/ci.yml` | **ALLOWED** | `.github/workflows/ci.yml` |
| `a/../pyproject.toml` | **ALLOWED** | `pyproject.toml` |
| `./a/../pytest.ini` | **ALLOWED** | `pytest.ini` |
| `src/../setup.cfg` | **ALLOWED** | `setup.cfg` |

Every anchored pattern fails this way. `tests/**` and `conftest.py` survive only by accident,
caught by their `**/`-prefixed twins. CI config and packaging — both named explicitly in the
session's commitment #1 — are editable.

**Fix:** guard the copy destination, and normalise before matching (or guard the *resolved*
relative path). **Submit as one PR** with two tests — the same invariant from two directions,
and a reviewer closing either would immediately ask about the other.
**Test home:** `tests/test_coding_surface.py`.

---

## S13 — `SubscribeToTask` drops the terminal task state ❌ **LOST**

**Claimed by upstream PR#15** ("A subscription that ends is not a task that finished",
2026-08-18), touching exactly `s17code/core/a2a/official.py`. Measured here first (3-of-4
live reproduction), not filed in time. Do not submit — kept below for the record.

**File:** `s17code/core/a2a/official.py:78-83`

**This ships as a red test on a clean checkout** — `test_hardening.py::test_official_
subscription_resumes_waiting_graph_and_maps_cancel`, failing **3 runs in 4**.

```python
yield p.StreamResponse(task=_task(task))
while task.state not in {TaskState.COMPLETED,TaskState.FAILED,TaskState.CANCELED}:
    await asyncio.sleep(.02); yield p.StreamResponse(task=_task(task))
```

The `yield` **suspends the generator**. If the core completes the task during that
suspension, the generator resumes, the `while` sees a terminal state, and the loop exits
**without ever yielding it**. There is no post-loop yield. `GraphA2ARemote.wait`
(`official.py:104-108`) takes the last streamed event as final, so the caller gets
`TASK_STATE_WORKING` and the graph node never observes completion:

```
assert final.status.state == p.TASK_STATE_COMPLETED
E  assert 2 == 3
E   where 2 = state: TASK_STATE_WORKING ... .state
```

**Fix:** one trailing `yield p.StreamResponse(task=_task(task))` after the loop, or
restructure to check-then-yield.

> **Judgement call before filing.** S16Code PR#6 fixes this same root cause in `s16code/`,
> and the standing decision is not to port other people's S16 fixes. This is arguably still
> ours and distinct, because it is not a port — **the test ships red in `S17Code`**, the same
> category as upstream PR#1 (tzdata). Lead the write-up with the failing test and its 3-in-4
> reproduction rate, and credit S16Code PR#6 as prior art on the shared cause.

---

## S2 — a reasoning channel breaks JSON parsing, one path silently
✅ **FILED — PR#17 — scope superseded, see closing note below**

**Files (original framing, see closing note for what was actually filed):**
`s17code/events/engine.py:18`, `workers/parsing.py:17`, `planner.py:43`,
`reasoning/verifier.py:89`, `reasoning/jitrl.py:137` · **Unclaimed**

Five hand-rolled JSON extractors, none reasoning-channel aware.

**Mode 1 — hard failure.** `events/engine.py:_json_object()` strips only ``` fences and calls
`json.loads` with **no fallback**. Any `<think>…</think>` reply raises, and the relevance gate
records `triage_failed` — indistinguishable from a broken provider.

**Mode 2 — silently wrong, and this is the serious one.** `planner.py` and
`workers/parsing.py` fall back to a `find("{")`…`rfind("}")` span. When the token budget is
exhausted **inside** the reasoning channel — exactly what `glc/economics/pricing.yaml:242-250`
documents for the qwen3 rung — the span lands on the draft the model was in the middle of
rejecting:

```
reply:  <think>
        Weighing it up. A first pass would be {"relevant": false, "why": "promotional"}

engine  -> JSONDecodeError                    (recorded as triage_failed)
planner -> {'relevant': False, ...}   <== the draft the model DISCARDED, as the decision
parsing -> {'relevant': False, ...}   <== same
```

No exception, no log line, no refusal. The harness acts on the model's rejected first
thought.

**Revised after the live Postman investigation (see the investigation summary above).**
Ollama's real response, once G2 ships, never actually reaches `S17Code` as a
`<think>`-tagged string — `S17Code` always sends `reasoning: "off"` explicitly
(`gateway.py:38`), so once `OllamaProvider` honors that, `content` is reliably clean. What
*is* proven to reach `S17Code` **today**, before G2 ships, is an **empty string** — Ollama's
`content` field is empty whenever thinking isn't suppressed, and `glc_v5` currently discards
the separate `thinking` field entirely. `json.loads("")` fails the same way
`json.loads("<think>...")` does (`JSONDecodeError: Expecting value: line 1 column 1`), so the
hard-failure mode (Mode 1) is already correctly triggered by today's actual traffic — nothing
new to prove there.

So S2 is no longer "the fix that makes Ollama work" (G2 is). It remains valuable as
**defense-in-depth**: the original `<think>`-tag scenario is still a real risk for *other*
callers/models — some self-hosted vLLM/SGLang GLM/Qwen deployments embed reasoning inline via
chat template rather than a separate field, per `chat_template_kwargs.thinking` already
existing in this codebase. Add both cases to the test suite: the original `<think>`-tag
repro, and a new explicit empty-string case matching what's actually been measured.

**Scoping note:** upstream PR#2 also touches `planner.py` (+10/−5), on verification counting
— a different function. To avoid any conflict, put the shared reasoning-strip helper in
`workers/parsing.py` and have `events/engine.py` adopt it; leave `planner.py` alone.
**Test home:** `tests/test_autonomous_events.py`.

**Closing note — everything above is the investigation as it stood before the architecture
re-check, kept for the record rather than deleted.** Tracing the actual data path (full
detail in `S2-Impact-Analysis.md`) found a different, more fundamental gap than JSON-parser
fragility: `reasoning_text` — the field glc_v5 PR#29/PR#30 add to `POST /v1/chat`'s
response — never survives `GatewayClient.chat()`, the one chokepoint every call passes
through, because its return is hardcoded to 9 named fields. That is a data-loss bug, not a
parsing-robustness one, and it is unconditional — it doesn't depend on a model ever sending
a `<think>` tag inline, which none of today's configured providers do. **Filed as
[PR#17](https://github.com/theschoolofai/S17Code/pull/17)**, scoped to restoring and
surfacing that field (`gateway.py`, `economics/controller.py`, `events/engine.py`,
`telemetry/spans.py`). The `<think>`-tag JSON-parser fragility documented above is real in
principle but untriggered by anything on this path today; it is carried forward in the PR
body as a documented future opportunity (Tier C), not implemented.

---

## S3 — `a2a_delegate` is missing `side_effect=True` **REPORTED**

**File:** `s17code/capabilities.py:447-450` · **Contested as of 2026-08-22** — PR#40 also
touches this file (unconfirmed whether it fixes this specific bug; read its diff before
filing)

A full registry audit found it is the **only** capability that causes work outside this
process while declaring `side_effect=False`. The sibling `launch_job` (`:451-454`) does the
same kind of thing and *does* declare it.

Consequences are mechanical:
- `planner.py:404-407` — the `allowed_side_effects` check never fires, so a run created with
  **zero** authority can delegate an arbitrary task to an arbitrary agent URL the model chose.
- `planner.py:452-453` — the manifest advertises it even to a zero-authority run.
- `runtime.py:465-476` — `idempotent()` only wraps side-effecting capabilities in the
  once-only outbox key, so a retried or resumed dispatch re-delegates.

No test anywhere references `a2a_delegate`. **Test home:** `tests/test_capability_contracts.py`.

---

## S4 — the `_JSON` regex is greedy **REPORTED**

**Files:** `reasoning/verifier.py:34`, `reasoning/jitrl.py:52` · **Contested as of 2026-08-22**
— PR#38 and PR#14 also touch `jitrl.py` (unconfirmed whether either fixes this specific
regex; read their diffs before filing)

Both define `_JSON = re.compile(r"\{.*\}", re.S)`, which spans from the **first** `{` to the
**last** `}` anywhere in the reply. A valid verdict of 92 followed by one closing sentence
containing braces becomes `verified=False, score=0`:

```
reply   : {"score": 92, "critique": "solid", "issues": []}
          I graded strictly against the task. Use {task} as the reference.
result  : VerifierError -> verified=False  score=0
```

In `jitrl._accept` the same regex silently downgrades to `decline("optimizer returned invalid
JSON")`, making the optimizer a no-op for any chatty provider. Distinct from S2: these have
no fallback **and** no fence stripping.
**Test home:** `tests/test_system2_reasoning.py`, `tests/test_jitrl_query_optimizer.py`.

---

## S5 — `jitrl.literals()` misses whole classes of literal ❌ **LOST**

**Claimed by upstream PR#14** ("The optimizer recognised a path by its extension, so most
paths were not paths", 2026-08-18), touching exactly `s17code/reasoning/jitrl.py`. Do not
submit — kept below for the record.

**File:** `reasoning/jitrl.py:54-63`, `:104-105`, `:149-154`

The one guard the module exists for does not hold. The extractor knows only quoted strings, a
fixed list of file extensions, 7–40 char lowercase hex, `vN.N.N`, and `#\d+`. Everything below
extracts to `[]` and the lossy rewrite is **accepted**:

| input | extracted |
|---|---|
| `update requirements.txt to pin urllib3` | `[]` |
| `the Dockerfile is broken` | `[]` |
| `fix Makefile target build` | `[]` |
| `rewrite main.go to use context` | `[]` |
| `fix scripts/deploy.sh` | `[]` |
| `port 8080 is hardcoded, use 9090` | `[]` |
| `issue 412 in the tracker` (no `#`) | `[]` |
| `open C:\Users\me\src\app\handler.py` | `['handler.py']` — drive and dirs lost |

Worst case, measured end-to-end: *"the service listens on port 8080, move it to 9090"* →
rewritten to *"Change the service listening port"* → **accepted**. The entire content of the
request is deleted and the planner receives a confident, contentless goal — the exact failure
`jitrl.py:17-19` says the module exists to prevent.
**Test home:** `tests/test_jitrl_query_optimizer.py`.

---

## S6 — the same guard fires backwards **REPORTED**

**File:** `reasoning/jitrl.py:105` · **Contested as of 2026-08-22** — same PR#38/PR#14
overlap as S4, same file (unconfirmed whether either fixes this specific bug)

`literals()` strips quotes (`.strip("\"'")`), so `the "retry_after" field` yields
`{'retry_after'}` — but a bare `retry_after` in the rewrite matches no alternative in
`_LITERALS`, so the rewrite's literal set is empty and the guard reports a dropped literal.
Any request where you quote an identifier and the model repeats it unquoted is permanently
un-rewritable.

---

## S7 — skill keyword matching is naked substring matching **REPORTED**

**File:** `skills/generic.py:155-156` · **Unclaimed**

```python
haystack = goal.lower()
return any(k in haystack for k in self.keywords)
```

The shipped `a2ui` skill declares keyword `ui` — a substring of **build**, **quick**,
**require**, **guide**, **fluid**, **circuit**. `charts` declares `graph` and `data` →
matches **paragraph**, **database**. Measured against the real `skills/` directory:

```
'build a summary of the quarterly numbers'  -> ['a2ui', 'house-style']
'explain the paragraph about database ...'  -> ['charts', 'house-style']

render('what is 2+2?')                       =  905 chars
render('build a summary of the quarterly …') = 6198 chars
```

A goal with nothing to do with UI silently spends 5.3 KB of the planner's system prompt on
A2UI instructions and steers it toward `compose_surface`. Fix is word-boundary matching or a
minimum keyword length. **Test home:** `tests/test_markdown_skills.py`.

---

## S8 — a UTF-8 BOM makes a SKILL.md unloadable **REPORTED**

**File:** `skills/generic.py:94` · **Unclaimed**

Reads `encoding="utf-8"`, not `utf-8-sig`, while `:22` anchors `_FRONTMATTER` with `\A---`. A
BOM — **what Notepad writes by default on Windows** — produces
`SkillFrontmatterError: SKILL.md must open with a --- frontmatter block`, blaming the wrong
thing entirely. A leading blank line gives the same misleading message, and a missing closing
`---` gives an actively wrong diagnosis.

*(Checked and clear: CRLF frontmatter parses correctly — `generic.py:22` uses `\r?\n`
throughout. The obvious Windows guess is not a bug here.)*

---

## S9 — `discover()` propagates `UnicodeDecodeError` **REPORTED**

**Files:** `skills/generic.py:93-96`, `skills/manager.py:64-70` · **Unclaimed**

`generic.py` catches only `OSError`; `manager.py` catches only `SkillError`.
`UnicodeDecodeError` is a `ValueError` — neither. So one cp1252-saved `SKILL.md` with a smart
quote takes down `AgentRuntime._skills()` and therefore `AgentRuntime.run()`.

This directly violates the contract stated at `manager.py:52-56`: *"A broken skill file is
recorded and skipped, never raised. One bad file in a skills directory should not stop an
agent from starting."*

---

## S10 — `_skills()` is a class-level cache **REPORTED**

**File:** `runtime.py:144`, `:153`, `:162` · **Contested as of 2026-08-22** — PR#40, #21, #20,
and #11 all also touch `runtime.py` (unconfirmed whether any fixes this specific caching bug
at these exact lines; read their diffs before filing)

`_skill_manager` is a class attribute, and `S17_SKILLS_DIR` is read at `:155` **after** the
cache check. Three consequences: two `AgentRuntime`s in one process with different skills
directories share the first tenant's skill bodies, which land in the second tenant's system
prompt; a `disable()` on one run persists into every later run forever; and with
`S17_SKILLS_DIR` later unset, `_skills()` still returns a manager, so the
`unavailable |= {"load_skill"}` mask at `:398` never fires.

---

## S14 — `run_command_worker` returns a raw dataclass, not a dict ✅ **MEASURED, ALREADY
CLAIMED — applied locally only, not filed**

**File:** `s17code/workers/coding.py:63-65` · **Claimed by upstream PR#10 and PR#2**

Found independently while building Model Arena (Part 1), not during the original Part 2 hunt.

```python
async def run_command_worker(ctx: RunContext, task: TaskSpec) -> dict[str, Any]:
    return run_command(ctx.workspace(), task.input["command"],
                       timeout=int(task.input.get("timeout", 120)))
```

Type-hinted to return a `dict`, but `run_command()` (`coding/exec.py:59-76`) actually returns
a `CommandResult` dataclass, and its own `.as_dict()` conversion method is simply never
called. Every sibling worker in the same file already returns a plain dict from its
underlying `coding/` call — this is the one outlier.

**Full trace, MEASURED end to end, not just read:**
1. `exec.py:143` — `run_command()` constructs and returns the raw `CommandResult`.
2. `workers/coding.py:64` — returned unchanged.
3. `run_command` has `side_effect=True` (`capabilities.py`), so it's wrapped by
   `runtime.py`'s `idempotent()` → `execute_once()` → `events/outbox.py`'s
   `ActionOutbox.execute()`.
4. `outbox.py:71` — `result = await operation()` succeeds, holding the raw `CommandResult`.
5. `outbox.py:77` — `self._write(key, {"status": "completed", "receipt":
   self._encode(result)})` — `_encode()` (`:44-48`) just wraps it, still holding the raw
   object.
6. `outbox.py:35` (inside `_write`) — `json.dump(value, stream, ...)` **is where the crash
   actually happens**, persisting the durability receipt.
7. That raise is outside `outbox.py`'s own try/except (only wraps step 4, not step 5), so it
   propagates uncaught up to `core/live_graph/core.py:194-195`'s `except Exception as exc:
   return task, False, {"error": f"{type(exc).__name__}: {exc}"}` — which is what actually
   catches it, converting a JSON-serialization crash into an ordinary-looking `task_failed`
   event indistinguishable from a genuine test failure.

Net effect: no `run_command` call — including a passing pytest run — can ever report cleanly
through this checkout. Isolated repro, no live server needed:
```python
from s17code.coding.exec import run_command
from s17code.coding.workspace import Workspace
result = run_command(Workspace.open("some/git/repo"), ["python", "--version"])
import json; json.dumps(result)  # TypeError: Object of type CommandResult is not JSON serializable
```
**Fix:** append `.as_dict()` to the `run_command(...)` call in `run_command_worker`. One
line. **Already filed upstream** as PR#10 ("run_command could not report its verdict") and
PR#2 ("Return run_command's result as a dict, and count a verification that could not run" —
broader, also touches `planner.py`). Confirmed via `gh pr list` before touching anything —
not resubmitted. Applied locally on the `part1-model-arena` branch only, with its own
failing-test-first proof (`tests/test_run_command_worker_serializable.py`), so Model Arena's
own demo could show a genuine judge verdict. **Test home if ever filed:**
`tests/test_run_command_worker_result.py` or similar, matching PR#10's naming.

---

## S15 — `run_command`'s subprocess environment drops `SYSTEMROOT` on Windows ✅ **MEASURED,
ALREADY CLAIMED — applied locally only, not filed**

**File:** `s17code/coding/exec.py:140-141` · **Claimed by upstream PR#3**

Found in the same session as S14, immediately after fixing it — the very next real
`run_command` call on this machine hit this one live.

```python
env={"PATH": os.environ.get("PATH", ""), "HOME": str(workspace.root),
     "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"},
```

`subprocess.run(..., env=...)` fully replaces the environment with exactly these 4 keys —
deliberate, for sandboxing, but on Windows it drops `SYSTEMROOT`, which `asyncio.
windows_events` needs to locate the Winsock service provider when it imports `_overlapped`.
Any command that imports `asyncio` — `pytest`'s own plugin autoload does — crashes with
`OSError: [WinError 10106] The requested service provider could not be loaded or
initialized`, indistinguishable from a real test failure to anything reading only
`exit_code`.

**MEASURED, isolated, deterministic:**
```python
from s17code.coding.exec import run_command
from s17code.coding.workspace import Workspace
ws = Workspace.open("some/git/repo")
(ws.root / "probe.py").write_text("import asyncio.windows_events\nprint('ok')\n")
run_command(ws, ["python", "probe.py"])  # exit_code=1, WinError 10106, on a clean checkout
```
Confirmed the fix in isolation too: adding only `"SYSTEMROOT": os.environ.get("SYSTEMROOT",
"")` to the same dict resolves it completely — a real `subprocess.run` with that one extra
key succeeds where the identical call without it doesn't.

**Fix:** add `"SYSTEMROOT": os.environ.get("SYSTEMROOT", "")` to the same `env` dict — a
no-op empty string on non-Windows. **Already filed upstream** as PR#3 ("Give the sandbox
enough environment for the interpreter to start on Windows"). Confirmed via `gh pr list`
before touching anything — not resubmitted. Applied locally on the `part1-model-arena`
branch only, with its own failing-test-first proof
(`tests/test_run_command_windows_systemroot.py`) — without it, Model Arena's demo could
never show a real `pytest` run completing on this machine. **Test home if ever filed:**
likely `tests/test_exec_environment.py`, matching PR#3's probable naming.

---

# glc_v5 findings

None of these files is touched by any of the 25 upstream PRs. `glc/providers.py`,
`imap/smtp_sender.py`, `policy/` and `cache/` are completely unclaimed territory.

## G1 — SMTP EHLO name from `socket.getfqdn()` ✅ **FILED — PR#33**

**File:** `glc/channels/catalogue/imap/smtp_sender.py:63` · **Unclaimed**

```python
smtp = smtplib.SMTP(self.host, self.port, timeout=30)
```

No `local_hostname`, so Python derives the EHLO name from `socket.getfqdn()`. On Windows that
can return embedded control characters (observed: `'LAPTOP-TGJ7B0SF.\x08\x08\x04\x04'`).
Gmail rejects the EHLO with **501**, STARTTLS is never advertised, and every send dies with
`SMTPNotSupportedError`.

Fix is `local_hostname=_ehlo_name()` with a regex-validated name falling back to
`"localhost"`. Already written and verified live against Gmail (`ehlo: 250`, `starttls: OK`)
on branch `part1-channel-bridges` (`0066ed4`) — **but that commit bundles 8 files, +506/−6**,
so re-author it off `main` as a focused change.

**No test file imports `SmtpSender` today** — the whole SMTP transport path is untested. The
adapter short-circuits before constructing one whenever a mock is injected
(`imap/adapter.py:228-244`).

**Filed as [PR#33](https://github.com/theschoolofai/glc_v5/pull/33)** on branch
`part2-glc-smtp-ehlo-hostname`, off a freshly-pulled `main`. One refinement made before
filing: `_ehlo_name()` initially returned a dotless-but-safe hostname bare, where smtplib's
own default (no `local_hostname` at all) promotes exactly that case to a bracketed IP
address-literal (RFC 5321 §4.1.3) instead. Closed so the fix is a strict superset of
smtplib's existing behaviour rather than trading one case away for another — 2 more tests
added for it (7 total), same RED-before/GREEN-after cycle repeated. Full suite: 541 passed,
same two pre-existing failures already cited on PR#29/#30, 1 skipped.

---

## G2 — `OllamaProvider` never sends `think: false` ✅ **FILED — PR#29**

**File:** `glc/providers.py:1030`, `:1103`, `:1116-1121`, `:1188` · **Unclaimed**

`_thinks_by_default()` (`:307-309`, with `"qwen3"` in `THINKING_BY_DEFAULT_HINTS` at
`:278-287`) has exactly one caller: `OpenAICompatProvider._apply_reasoning` (`:417-441`).
`OllamaProvider` is a separate class that accepts `reasoning=None` at `:1103` and **never
reads it**, hardcodes `"reasoning_applied": False` at `:1188`, and builds its `/api/chat`
payload without Ollama's `think` field at all.

The repo's own config demands it — `glc/routing/routing.yaml:66-67` and
`glc/economics/pricing.yaml:242-250` both state the qwen3 rung must be called with
`think:false` or it returns an empty answer, flagged `measured_needs_reasoning_off: true`.

**Proven twice, exactly as planned — deterministic reasoning plus a full live repro against
`qwen3:8b` on real hardware.** The live repro is the strongest evidence in this whole ledger:

| | unset | explicit `off` |
|---|---|---|
| `reasoning_applied` | `false` | **`false`** (asked explicitly, still ignored) |
| `text` | `""` | `""` |
| `output_tokens` | 300 / 300 | 300 / 300 |
| `latency_ms` | 142,445 | 95,032 |

Explicitly asking for `"off"` had **zero effect** — byte-for-byte identical to sending
nothing. **95 to 142 seconds per call for zero usable output.** Worse than the documented
Cerebras cost-only case — this is a time cost, not just a wasted-money one; at this latency
the Ollama tier is effectively unusable through this gateway today, not merely inefficient.

**Scope, finalized by the raw-API investigation (see summary above):**
1. Read `reasoning` and translate faithfully: `"off"` → `think: false`;
   `"low"/"medium"/"high"` → pass through as the identical string — confirmed live that
   Ollama's real API accepts exactly these (plus `"max"`, unreachable from `glc_v5`'s schema,
   out of scope).
2. Fix `reasoning_applied` to reflect what was actually sent.
3. **Additive:** capture Ollama's separate `message.thinking` field into a new
   `reasoning_text` key on the result dict, rather than silently discarding it — nothing
   existing changes shape, callers that don't read the new key see no difference.

**Test home:** new `tests/test_ollama_reasoning.py`, matching the `monkeypatch` +
hand-rolled-fake-`httpx.AsyncClient` convention already used in
`tests/test_provider_reasoning.py:83-119`.

---

## G3 — the default path never disables thinking for OpenAI-compat either ⚪ **CONFIRMED
REAL, DEFERRED — not filed**

**File:** `glc/providers.py:424` · **Deliberately deferred (see below), now also more
contested as of 2026-08-22** — PR#44 and PR#38 touch this file too, on top of the
already-known PR#28 (corroborating, different provider class)

```python
if not reasoning:
    return False
```

`_apply_reasoning` short-circuits before ever consulting `_thinks_by_default`, and
`ChatRequest.reasoning` defaults to `None`. So an ordinary `POST /v1/chat` with no
`reasoning` key sends **no** thinking control to `gpt-oss-120b` (the Groq default) or
`zai-glm-4.7` (the Cerebras default, retired mid-investigation — `gpt-oss-120b` also
reproduces it), both of which `_thinks_by_default` returns `True` for.

`pricing.yaml:170-173` documents the result: *"burned all 512 output tokens thinking and
returned `content: ""` for $0.0002735 — 4.5x the price of the answer, for no answer."*
`measured_needs_reasoning_off` has **zero readers** in any `.py` file.

**Deliberately not being fixed as part of this work, and here is the precise reason —**
confirmed by reading `s17code/gateway.py:38`: **every single call `S17Code` makes hardcodes
`"reasoning": "off"`.** `S17Code` never sends an unset `reasoning` field, so it never takes
this path regardless of whether it's fixed. This is real and broken for *any other* caller of
the raw gateway API that doesn't set `reasoning` — logged honestly as confirmed-but-deferred,
not closed as safe.

Broader than G2 in principle — hits Groq, Cerebras, NVIDIA and OpenRouter — but zero risk to
this specific system.
*Adjacency note:* upstream PR#25 touches `llm_schemas.py`; this fix lives in `providers.py`,
but cited line numbers in `llm_schemas.py` may shift.

---

## G8 — OpenAI-compat's `reasoning` field is generated, billed, and permanently discarded
✅ **FILED — PR#30**

**File:** `glc/providers.py` — `OpenAICompatProvider.chat()`, the same region as
`_apply_reasoning` (`:417-441`) · **Unclaimed**

Live, raw (no `glc_v5` in between) capture against Groq, `gpt-oss-120b`, `reasoning` unset:

```json
"message": {
  "content": "\\(17 \\times 24 = 408\\)...391",
  "reasoning": "We need compute 17*24 = 408. sqrt(289) = 17..."
}
```

Groq and Cerebras **always** return reasoning in its own `message.reasoning` field —
confirmed on every one of unset/low/medium/high, not only when suppression fails. The
tokens are real and billed: `usage.completion_tokens_details.reasoning_tokens` measured at
49 (unset), 21 (`low`), 189 (`high`) on the identical prompt. `OpenAICompatProvider.chat()`
reads only `message.content`; `message.reasoning` is never read anywhere in the file
(confirmed by grep — the same discard pattern as `OllamaProvider` and `message.thinking`).

**Why this is a bug, not merely an enhancement — reconsidered directly with the user and
correctly upgraded.** `content` staying usable means nothing crashes, so at first read this
looked softer than G2. But: the reasoning tokens are generated and billed on **every** call
where any reasoning happens — not conditionally, like G2's failure, but by construction, on
100% of such calls — and then made **permanently unobservable to every caller, at every
layer, forever.** No caller can audit or debug why a model reached a given answer. That is
squarely the kind of defect this whole project's own stated ethos is built against —
*"the run has to be visible," "don't let evidence disappear silently."* A gateway that pays
for a model's reasoning and then makes it permanently unrecoverable is a data-loss bug, not
a nice-to-have.

**Scope:** capture `message.reasoning` into the same additive `reasoning_text` key G2 adds
for Ollama — parallel structure across both provider families, one new field, nothing
existing changes shape. **Separate PR from G2** — different provider class, different root
cause, different severity profile; bundling would weaken G2's tight, evidence-backed story
with a claim that doesn't belong to it.

**Test home:** extend `tests/test_provider_reasoning.py`, matching its existing
`_Compat`/fake-`httpx` conventions.

**Extended after filing to also cover Gemini** (commit `99d72a5`, pushed to the same
branch/PR — bundled into #30 by explicit user decision, not a separate PR). `GeminiProvider`
had a two-part version of the same bug: the request never set `thinkingConfig.
includeThoughts` (so thought content never arrived at all), and the response parser had no
`part["thought"]` check (so if it ever did arrive, it would have contaminated the visible
answer). Both had to ship together — turning on `includeThoughts` alone would have
introduced a live contamination bug that doesn't exist today. Confirmed complete provider
coverage by grepping every `class .*Provider` in `glc/` and every registry construction
site: NVIDIA/OpenRouter/GitHub need nothing further (inherit `chat()` unchanged from
`OpenAICompatProvider`, already covered above), Ollama is covered by #29, no Anthropic/
Claude chat provider exists in this codebase. 3 more tests added to the same
`tests/test_provider_reasoning.py` file (not a new file), RED confirmed producing the
literal contamination (`'scratch work: 6*7=4242'`) before the fix, GREEN after — 18 passed
on the file, 471 on the full suite. Two other open PRs (#28, #38) touch overlapping lines
in `GeminiProvider` for unrelated reasons — disclosed in the PR body, not blocking on
either. Explicitly scoped out and named, not silently missed: Gemini's `output_tokens`
under-counting `thoughtsTokenCount` (a separate, real cost-accounting bug — logged as its
own future ledger item, not numbered yet), the `googleapis/python-genai#2121` "THOUGHT:"-
prefix edge case, and `maxOutputTokens` defaults (a deployment-time choice — live-confirmed
via the Postman collection that a tight budget lets thinking starve the visible answer,
same risk pattern already documented for Ollama/Groq/Cerebras elsewhere in this ledger).
Postman collection (`htmlcov/glc_reasoning_toggle.postman_collection.json`) extended with
Gemini folders in both halves for manual verification.

---

## G4 — the semantic cache namespace drops role, provider pin and `max_tokens` **REPORTED**

**Files:** `glc/cache/semantic.py:184-198`, `glc/routes/chat.py:440-451`,
`glc/cache/cache.yaml:40` · **Unclaimed**

`_cache_request_fields` computes 8 fields; `namespace()` folds in only the 5 listed in
`cache.yaml`, silently discarding `provider`, `max_tokens` and `auto_route`:

```
ns(auto_route=bulk,        max_tokens=64)   = 1cbf6224bc37324db253db1416d904a7
ns(auto_route=adjudicator, max_tokens=4096) = 1cbf6224bc37324db253db1416d904a7
ns(provider="gemini_1")                     = 1cbf6224bc37324db253db1416d904a7
```

So a `bulk` request (CHEAP tier, answered by local qwen3) and an `adjudicator` request
(`min_tier: FRONTIER`, *"the step whose mistakes are expensive"*) share a namespace. The
expensive-tier caller gets the free local model's answer at `$0.00` with
`price_source: "semantic-cache-hit"`. **The role floor — the entire thesis of
`routing/policy.py` — is bypassed by a cache that runs before routing.**

Same for an explicit provider pin, and for `max_tokens` (a 64-token truncated answer served
to a 4096-token request). *Not* the tenant-scoping concern; tenant scoping works correctly.
**Test home:** `tests/test_cache_semantic.py`.

---

## G5 — the shipped `file.delete` policy rule never fires ❌ **FIXED UPSTREAM, LOST**

**Fixed by upstream commit `9f2b647`** ("Policy engine: fail closed on every matcher —
backport glc_v2 #16/#69/#13/#66"), merged to `main` 2026-08-19, discovered when pulling
`main` before branching for G1. Not a competing student PR — an official backport — but the
effect is the same: `_matches_glob` now has a `_normalize_path_for_glob` helper doing
exactly the `expanduser` + `\\`→`/` normalisation this finding said was missing, applied to
both `value` and `pattern` before matching. Do not submit.

**Files (original framing):** `glc/policy/engine.py:38-45`, `:121`,
`glc/policy/policy.yaml:21-25` · **Unclaimed**

`_matches_glob` matches **literally** — no `expanduser`, no separator normalisation. The
shipped rule is `path_glob: "~/Documents/**"`, but every real tool passes an expanded path:

```
'~/Documents/taxes.pdf'                -> deny   | file deletes under ~/Documents not permitted
'/home/alice/Documents/taxes.pdf'      -> allow  | default-allow for owner_paired
'C:\Users\rragh\Documents\taxes.pdf'   -> allow  | default-allow for owner_paired
'C:\Users\rragh/Documents/taxes.pdf'   -> allow  | <- literal output of expanduser on the rule
```

The last line is the killer: the expanded form of the rule's own string does not match the
rule. On Windows it is doubly dead, because `\*` → `[^/]*` uses a POSIX-only separator class.
The one rule protecting user documents is inert against realistic input on every OS.
**Test home:** `tests/test_policy_engine.py`.

---

## G6 — Twilio signature computed over the internal URL **REPORTED**

**File:** `glc/channels/catalogue/twilio_sms/webhook.py:129` · **Unclaimed**

`url = str(request.url)` reconstructs the internal ASGI URL
(`http://localhost:8200/webhooks/twilio_sms`), but Twilio HMACs the **public** URL it posted
to — and the documented deployment is an ngrok tunnel (`server.py:10-18`). The signatures
differ; the scheme mismatch alone (`https` vs `http`) breaks it.

100% of genuine inbound SMS is rejected with 403, and the obvious operator response is the
escape hatch at `webhook.py:66-68`, `GLC_TWILIO_SKIP_SIG=1` — turning a fail-closed bug into
a fail-open one on a channel where `From` drives trust level.

**Test home:** a new **top-level** `tests/test_twilio_signature.py`. `compute_signature` and
`validate_signature` are pure and importable without FastAPI.

---

## G7 — YAML and SQL read with the locale codec, not UTF-8 **REPORTED**

**Files:** `glc/policy/engine.py:93`, `glc/routes/chat.py:71`, `glc/audit/store.py:48`
· **Contested as of 2026-08-22** — PR#26 (merged) and PR#37 (open) also touch
`glc/policy/engine.py`. Checked PR#26's actual diff specifically, since a merged backport
fixed G5 as a side-effect before: it adds `_normalize_path_for_glob` (the G5 fix) but touches
no encoding/locale read anywhere — **this finding is confirmed still real and unfixed**,
it just shares a file with PR#26 and PR#37, so a filing needs to diff cleanly against both.

None passes `encoding=`. Confirmed on this machine (`locale.getpreferredencoding(False)` =
`cp1252`): `glc/routing/routing.yaml` (1659 non-ASCII bytes) **parses to different data**
under the two codecs — mojibake in operator-facing `description` strings surfaced by
`/v1/routers`.

Worse on a CJK or Cyrillic ANSI codepage, where the same bytes raise `UnicodeDecodeError` —
and `PolicyEngine.from_yaml` catches that at `engine.py:95` and boots the **deny-everything**
`_SAFE_DEFAULT`, silently refusing every tool call.

*Honest split:* the cp1252 mangling is measured; the hard-failure variant is inferred, since
I could not change the locale read-only.

---

# Checked and cleared — do not file

Recorded so nobody re-spends on them.

**S17Code**
- **The dotted-path guard bug is genuinely fixed.** `guard.py:45-51` replaced the naive
  `lstrip("./")` with an explicit loop plus a comment naming the old failure. (S12 is a
  *different* defect in the same function.)
- **CRLF frontmatter parses correctly** — `generic.py:22` uses `\r?\n` throughout.
- **`SkillManager.reference()` path-escape checking is sound** — `../../SKILL.md`,
  `..\..\SKILL.md`, `/etc/passwd`, `C:/Windows/win.ini` all refused.
- **The three System-2 rules are all implemented**: fast path 85, cap a high score with
  listed issues, return best-not-last.
- **The `rerunnable` family is exactly the six** the session claims.
- **`window_reserve` is genuinely atomic** — check-and-increment inside one lock, no `await`.
- **Windows `file://` URIs, planner repair schema, refusal double-counting** — all fixed.

**glc_v5**
- `glc/security/allowlists.py` fails closed on an empty `allowed_senders`.
- `glc/security/trust_level.py` — no defect found.
- `glc/economics/pricing.py` zero prices match explicit free-tier entries; intentional.
- `glc/audit/store.py` — a write failure propagates rather than being swallowed.

---

# Inherited from S16, still present — checklist only, DO NOT PORT

These reproduce in `s17code/` but are fixed by existing **S16Code** PRs by other people.
Submitting a port is low-originality and a reviewer who knows both repos will spot it.
Recorded as analysis for the guide, not as submissions.

- **SSRF in `fetch_url`** — `tools.py:266-272`, no loopback/private check,
  `follow_redirects=True`, and the fetched body becomes cited evidence.
- **Heartbeat only beats inside `engine.process()`** — a quiet night is indistinguishable
  from a dead watcher, the exact thing `report.py:14-15` says must not happen.
- **`awaiting_a_human` is a hardcoded `[]`** at `report.py:128`, never populated, though the
  data exists at `engine.py:152`.
- **Run spend never recorded** — `engine.py:149-151` reads `spend_usd` from a dict that
  `runtime.py:498-506` never returns, so `daily_budget` can never bind.

*(S13 was in this "arguably distinct" position, and it turned out to matter: it was
independently claimed by upstream PR#15 before we filed. Left as a lesson on filing speed,
not just as a footnote.)*

---

# Related upstream work — not a conflict, corroborating evidence

**`glc_v5` PR#28, "Gemini thinking was never actually turned off"** (2026-08-18) fixes the
same bug *family* as G2/G3/G8, independently, for `GeminiProvider` — a different provider
class, no file/function overlap with our targets. Their root cause is identical in shape to
ours: `"off"` implemented as "skip configuring" rather than "actively suppress," and their
write-up cites the exact same `S17Code/config/tiers.yaml` gpt-oss-120b lesson and
`chat_template_kwargs.thinking` evidence this investigation independently arrived at. Worth
reading before writing G2/G8's PR bodies — not to copy, but their test file
(`tests/test_gemini_thinking_knob.py`) is a second, independent example of house style for
this exact shape of fix, on top of `tests/test_provider_reasoning.py`.

---

# Filing workflow

```bash
git switch -c part2-<slug> main          # always off main

# failing test FIRST
uv run pytest tests/test_<new>.py -q     # RED — paste into the PR body
# minimal fix
uv run pytest tests/test_<new>.py -q     # GREEN
uv run ruff check .
uv run pytest -q                         # once, before submitting
```

**Branches for the three queued PRs:**
- `part2-glc-ollama-reasoning-toggle` (G2) — ✅ filed as PR#29.
- `part2-glc-openai-compat-reasoning-text` (G8) — to be created after G2 is walked through
  and approved.
- `part2-s17-reasoning-channel-json` (S2) — created, off `main`.

**Two repo-specific traps:**

1. **S17Code's baseline is not green.** Measured: `2 failed, 482 passed, 1 skipped` in
   **78 minutes**. The two reds are the tzdata test (upstream PR#1) and S13 (now claimed by
   PR#15, but still red on this checkout until it merges). Disclose both in every PR body,
   and never prove a fix with a whole-suite exit code.
2. **glc_v5 CI ignores `tests/channels/`.** It runs
   `pytest tests/ --ignore=tests/channels --ignore=tests/voice/stt --ignore=tests/voice/tts`,
   so a test placed there will not run. Use top-level `tests/test_*.py`. House mocking style
   is `monkeypatch` plus inline fakes (`tests/test_provider_reasoning.py:83-119`), not
   `unittest.mock`. Match the shipped `.github/PULL_REQUEST_TEMPLATE.md`.
