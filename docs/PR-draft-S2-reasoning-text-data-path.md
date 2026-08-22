# Title

GatewayClient.chat() discards a provider's reasoning content at the very first hop

# Body

## What breaks

glc_v5 PR #29 (Ollama) and PR #30 (Groq/Cerebras/NVIDIA/OpenRouter/GitHub) put a provider's
own separated reasoning content on the wire, as `ChatResponse.reasoning_text` in `POST
/v1/chat`'s JSON response.

`GatewayClient.chat()` (`gateway.py:51-97`) is the one chokepoint every call to glc_v5 passes
through — budgeted or not, triage or main run — and its return is hardcoded to 9 named fields.
`reasoning_text` is not one of them, so it is discarded on arrival, before any other code in
S17Code runs. From there it stays discarded through both branches of the architecture:
`GatewayClient.complete()` (the unbudgeted path) narrows further, and `BudgetedGateway.complete()`
(the budgeted path) never sees it either, so it can't reach the per-call audit record that
already flows automatically into the run's durable journal and OTel spans.

Net effect: a value the gateway now provides — and, once a provider bills for it, has already
been paid for — is unobservable to every S17Code caller, at every layer, forever. Nothing
crashes; `text` stays clean and usable regardless. But no caller can ever audit or debug why a
model reached a given answer, or notice that a call spent tokens thinking.

## Investigation note — the architecture is a per-call decision, not two fixed paths

Worth stating plainly for review, since it shapes exactly which files this PR touches and which
it doesn't. Whether a given call is "budgeted" (metered through `BudgetedGateway`) or
"unbudgeted" (raw `GatewayClient.complete()`) is decided fresh, every time, inside
`AgentRuntime.run()` — by whether `budget is not None` — not by which route or module initiated
it. The relevance-gate triage call in `events/engine.py` never touches `BudgetedGateway` at all;
the gate's own follow-up run, if it decides an event is relevant, always does, via that same
`AgentRuntime.run()`. That's why this fix touches `events/engine.py` on top of the transport
layer: it's a genuinely separate call, and the only one where reasoning is still never captured
even after the transport layer is fixed.

## Reproduction — before the fix

```
$ uv run pytest tests/test_gateway_boundary.py tests/test_economics.py \
                 tests/test_autonomous_events.py tests/test_telemetry.py -q

8 failed, 42 passed
  KeyError: 'reasoning_text'   (x8, across gateway/controller/engine assertions)
ERROR tests/test_telemetry.py
  ImportError: cannot import name 'REASONING_TEXT' from 's17code.telemetry.spans'
```

## The fix

Five lines of new data plus one new constant, across four files — all additive, all `dict.get()`
passthrough, no new control flow.

### Tier A — restore the data (required; nothing downstream can work without this)

- `gateway.py` — `GatewayClient.chat()` (the root chokepoint) and `GatewayClient.complete()`
  (the unbudgeted path) both add `"reasoning_text"` to their return dicts.
- `economics/controller.py` — `BudgetedGateway.complete()`'s own return dict gains it, and so
  does the `record` appended to `site.calls` — which is what makes it flow automatically into
  the run's durable journal and OTel spans, via the mechanism that already exists for
  `metered_calls`.

### Tier B — make it observable

- `events/engine.py` — the relevance-gate's triage decision record gains
  `reply.get("reasoning_text")`, unconditionally, on both a `relevant: true` and a
  `relevant: false` verdict. This is the one call in the whole architecture that Tier A alone
  can't cover, because triage never goes through `BudgetedGateway`.
- `telemetry/spans.py` — one line added to an existing, previously dead, opt-in PII-gated block
  (`if capture:`) that already handles `prompt`/`completion` the same way. `reasoning_text` is
  exactly as sensitive as those two, so it gets the same gate (`S17_OTEL_CAPTURE_CONTENT`), not
  a new one. New constant `REASONING_TEXT = "s15.reasoning_text"`, vendor-prefixed per this
  file's own stated policy for attributes with no blessed OTel GenAI convention yet — the same
  reasoning the file already gives for `s15.cost`.

### Tier C — a documented future opportunity, not implemented here

Five hand-rolled JSON extractors in this codebase (`events/engine.py`, `planner.py`,
`workers/parsing.py`, `reasoning/verifier.py`, `reasoning/jitrl.py`) have no defense against a
model that embeds a `<think>...</think>` block inline in its reply text, rather than returning
reasoning as a separate structured field. That is a real gap in principle, but it is distinct
from this bug and currently untriggered: every provider on this data path returns reasoning as
its own field (`message.thinking` for Ollama, `message.reasoning` for the OpenAI-compat family),
and glc_v5's own `providers.py` never inspects `<think>` tags anywhere today (confirmed — zero
matches for `<think` or `</think` in that file). Recorded here as a future opportunity to revisit
if a provider or model is ever added that thinks inline instead, rather than implemented
speculatively against a case nothing currently triggers.

## Why this matters — what a five-line, four-file fix actually unlocks

This section separates two categories on purpose: what is **mechanically, verifiably true the
moment this PR merges**, with no further work — and what is **newly possible but intentionally
not built here**, staying inside this PR's scope. Both matter to the case for taking this fix,
but they should not be blurred together.

### Already true today, automatically — traced through the actual code, not asserted

**1. The browser UI already receives it, live, with zero new code.**
`runtime.py:103` merges `site.result_fields()` — which now carries `reasoning_text` inside every
entry of `metered_calls`, via the Tier A change to `controller.py` — straight into the node's
`task_succeeded` journal payload. `ui/agui.py:73` then forwards that **entire payload,
unfiltered**, as an AG-UI `STATE_DELTA`:
```python
return {**base, "stepName": node, "delta": {"op": "add", "path": f"/results/{node}", "value": payload}}
```
There is no field allowlist here, unlike `gateway.py`'s root chokepoint. Any client already
speaking the AG-UI protocol — the same live event stream Session 14 built for the browser —
receives a model's reasoning content per call, today, the instant this PR lands. This was not a
design goal of this PR and required no additional wiring; it is a direct, mechanical consequence
of restoring the field at the one place (`controller.py`'s `record`) that both the journal and
the UI stream already read from. That is the strongest evidence this fix belongs at the root of
the data path rather than patched in separately per consumer.

**2. Every provider-call span becomes reasoning-visible in Jaeger, Honeycomb, Tempo — any OTLP
backend — by setting two environment variables, not by writing code.**
`build_tracer_provider` (`telemetry/spans.py:514`) wires a real `OTLPSpanExporter` (HTTP or gRPC,
chosen by the endpoint shape) the moment `S17_OTEL_EXPORTER_ENDPOINT` is set — this is standard
OTLP, which Jaeger, Honeycomb, Grafana Tempo and every other mainstream collector ingest natively.
Combined with `S17_OTEL_CAPTURE_CONTENT=1`, every `provider_call` span this codebase already
exports now carries `s15.reasoning_text` in the same attribute set as cost, token counts, model,
and the `reasoning_effort` dial. In Jaeger's own UI that means: open any trace, click any span,
and read *why* the model answered the way it did, in the same waterfall view that already shows
what it cost and how long it took — filterable and searchable the same way any other span
attribute is, no export pipeline to build, no new dashboard.

**3. The autonomous relevance gate's decisions are now explainable after the fact, permanently.**
`events/engine.py`'s own docstring states the premise of the whole module: *"an agent nobody is
watching is bounded by the controls around it and by nothing else."* Every triage verdict —
including every **refusal to act** — is written durably via `self.store.add_decision(...)`. Before
this fix, the reasoning behind that verdict existed for the lifetime of one function call and was
then gone; only the one-line `reason` string survived. After this fix, the full reasoning trace
is attached to the exact decision record an operator or auditor would pull up months later to
answer "why did the autonomous system act on this event — or why didn't it." For a system whose
entire safety case rests on the actions it takes without a human approving each one, that is not
a convenience feature; it is the difference between an audit trail that says *what* happened and
one that says *why*.

### Newly possible, not built here — genuine future work, scoped out on purpose

These require new code on top of the data this PR restores, and are deliberately **not**
implemented in this PR, to keep it scoped to restoring and surfacing the field rather than
building consumers for it:

- **Cross-run reasoning search.** Once spans reach a real backend, tag/attribute search (Jaeger's
  tag search, Honeycomb's BubbleUp) can answer "show me every run this month where the model's
  own reasoning expressed uncertainty" — a question that is currently unanswerable at any price,
  because the data to search does not exist anywhere today.
- **Dial-to-value analytics.** `record` already carries both the requested `reasoning` dial
  (`"low"`/`"high"`/etc.) and, after this PR, the actual `reasoning_text` produced. Correlating
  the two against `cost` and `latency_ms` — both already in the same record — would let a team
  answer "is `reasoning: high` actually buying proportionally better answers for this role, or
  just more billed tokens?" with real data instead of a guess.
- **A feedback loop on the relevance gate itself.** The triage decision log, now carrying
  `(event, subscription, reasoning_text, verdict)` per call, is exactly the dataset needed to spot
  a systematically miscalibrated gate — e.g., reasoning that keeps citing "looks promotional" for
  events that turn out to be real incidents. Invisible before this PR, because only the verdict
  was ever recorded, never the reasoning behind it.
- **Compliance-grade explainability on demand.** Several jurisdictions and internal governance
  policies increasingly require that an autonomous action be explainable after the fact, not just
  logged as having happened. This PR is the structural prerequisite for that capability across
  this entire codebase — every layer that would need to carry the explanation now does — but
  producing an actual on-demand explanation report is separate, unbuilt work.

## After the fix

```
$ uv run pytest tests/test_gateway_boundary.py tests/test_economics.py \
                 tests/test_autonomous_events.py tests/test_telemetry.py -q
70 passed
```

Targeted regression, unaffected:

```
$ uv run pytest tests/test_budget_runtime.py tests/test_runtime.py \
                 tests/test_runtime_regressions.py tests/test_a2a_route.py -q
28 passed
```

## Full suite

```
$ uv run pytest -q
1 failed, 493 passed, 1 skipped in 109.85s
FAILED tests/test_general_tools.py::test_current_datetime_uses_iana_timezone   (tzdata — PR #1, pre-existing)
```

A second, independent run of the same full suite additionally showed
`s17code/core/a2a/tests/test_hardening.py::test_official_subscription_resumes_waiting_graph_and_maps_cancel`
failing (`2 failed, 492 passed`) — a known, pre-existing race, not touched by this PR (same test
cited as pre-existing in PR #6), that reproduces on `main` roughly 3 times out of 4 rather than
deterministically. Neither failure touches any file this PR changes. 493/492 passed is the
pre-existing baseline of 482 (measured on a clean `main`) plus exactly the 10 new tests added
here.

## Depends on glc_v5 PR #29 and PR #30 — both currently open, neither merged

This PR is correct but **inert** until both merge. `reasoning_text` only carries a real value
once glc_v5's `POST /v1/chat` response includes it:

- PR #29 — Ollama never read the reasoning dial, and discarded the model's own reasoning field.
- PR #30 — Groq and Cerebras generate reasoning, bill for it, and the gateway throws it away.

Until then, `body.get("reasoning_text")` in `gateway.py` is always `None`, so every field this PR
adds is `None` everywhere in practice today — which the tests cover explicitly (an "absent"
case alongside a "present" case in each of the four touched files), because `None` is today's
real, live behavior, not just an edge case.

## Tests

10 new tests across the four touched files: `reasoning_text` present and absent through both
`GatewayClient.chat()` and `.complete()`; present and absent through `BudgetedGateway.complete()`'s
result and its journal record; the triage decision capturing it on both a relevant and an
irrelevant verdict, and defaulting to `None` when the gate sends none; and the span projection
respecting the existing PII opt-in gate — off by default, captured once opted in — matching the
two tests already established for `prompt`/`completion`.
