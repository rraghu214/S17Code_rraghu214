> **Status note (Gemini extension of PR #30 — implemented, committed `99d72a5`, pushed).**
> This doc originally drafted the PR body actually used to open #30 (Groq/Cerebras only,
> filed and confirmed clean). It has now been extended in place — not a new file — to also
> cover Gemini, per the user's decision to bundle the Gemini fix into this same PR rather
> than open a new one. Sections marked **(Gemini addendum)** are new. The Gemini tests were
> added to the *existing* `tests/test_provider_reasoning.py`, not a separate file (per
> explicit instruction) — the original plan's proposal of a new `test_gemini_reasoning.py`
> was not used. The original Groq/Cerebras sections below are unchanged from what was
> actually filed.

# Title

Groq, Cerebras, and Gemini generate reasoning, bill for it, and the gateway throws it away

# Body

## What breaks (Groq / Cerebras / NVIDIA / OpenRouter / GitHub)

`OpenAICompatProvider.chat()` — the shared code path behind Groq, Cerebras, NVIDIA,
OpenRouter, and GitHub — reads only `message.content` from the provider's response.
Confirmed live, direct against Groq's raw API, no gateway in between: the provider
**always** returns reasoning in its own separate field, even when `reasoning` is
completely unset:

```json
"message": {
  "content": "...391",
  "reasoning": "We need compute 17*24 = 408. sqrt(289) = 17..."
}
```

Those tokens are real and billed — `usage.completion_tokens_details.reasoning_tokens`
measured live on the identical prompt: 49 at unset, 21 at `"low"`, 189 at `"high"`. The
gateway pays for them and then makes them **permanently unobservable to every caller, at
every layer, forever.** Nothing crashes — `content` stays clean and usable regardless —
but no caller can ever audit or debug why a model reached a given answer.

**NVIDIA and OpenRouter need no separate work** — confirmed by reading both classes
directly: neither overrides `chat()`, both inherit it verbatim from `OpenAICompatProvider`
(only `__init__`, to set their own `base_url`; `OpenRouterProvider` additionally overrides
`_headers()` for two unrelated HTTP headers). The fix below covers all five providers in
this family identically.

## What breaks (Gemini) — (Gemini addendum) — two compounding gaps, not one

`GeminiProvider.chat()` has a different, two-part version of the same bug:

1. **The request never asks Gemini to return its thoughts at all.** Confirmed by direct
   search — `includeThoughts`/`include_thoughts` appears zero times anywhere in
   `providers.py`. The existing reasoning block only sets `thinkingConfig.thinkingLevel` or
   `thinkingConfig.thinkingBudget` — that controls *how much* the model thinks, not
   *whether Gemini returns it*. Per Google's own API, thinking tokens are generated and
   billed either way, but thought content only comes back in the response when
   `includeThoughts: true` is explicitly set. Today it isn't — so this is the same
   "generates it, bills for it, throws it away" bug as Groq/Cerebras, one layer earlier
   (nothing ever arrives at the gateway to discard).
2. **Even if it did come through, the response parser would contaminate the answer with
   it.** The exact line:
   ```python
   text = "".join(p.get("text", "") for p in parts if "text" in p)
   ```
   concatenates *every* part with a `text` key. Gemini marks reasoning parts with a
   sibling `part["thought"] = true`, which this line never checks. So the bug isn't just
   "reasoning is dropped" — it's "if reasoning ever did come through, it would leak
   straight into the visible answer."
3. **These two must ship together, not just be bundled for tidiness.** Turning on
   `includeThoughts` without also fixing the parser would introduce a *new* live
   contamination bug that does not exist today (today no thought parts return at all, so
   there is nothing yet to contaminate the answer with).

**Three things scoped out explicitly, named rather than silently missed** (same convention
as #17/S2's Tier C):

- `googleapis/python-genai#2121` — some Gemini configurations return thought content as a
  plain text part with a `"THOUGHT:"` string prefix instead of properly setting
  `part.thought = True`. Named in a code comment, not fixed.
- `output_tokens` under-counting `thoughtsTokenCount`. Checked directly: Gemini's
  `usageMetadata` fields are additive (`promptTokenCount + candidatesTokenCount +
  thoughtsTokenCount = totalTokenCount`, confirmed against a live response), unlike
  OpenAI-compat's `completion_tokens`, which already includes reasoning tokens as a
  documented subset — so Groq/Cerebras's `output_tokens` was already correct and Gemini's
  is not. A real, separate cost-accounting bug, not touched by this PR — logged as its own
  ledger item.
- `maxOutputTokens` defaults. Thinking and the visible answer share one token budget on
  Gemini's side; a tight `max_tokens` can starve the answer once thinking turns on (live
  reproduction below shows exactly this). A deployment-time config choice, not a code
  change.

**Live reproduction of the budget-starvation risk**, captured manually via the Postman
collection (`htmlcov/glc_reasoning_toggle.postman_collection.json`) against
`gemini-2.5-flash` with `thinkingBudget: 8192, includeThoughts: true` and
`maxOutputTokens: 300`: `thoughtsTokenCount: 287` alone consumed nearly the entire 300-token
ceiling, leaving the visible answer truncated mid-generation
(`finishReason: "MAX_TOKENS"`, answer cut off after `"First, calculate 17 times 2"`). This
independently confirms gap 1 is real (a `thought: true` part is genuinely returned once
`includeThoughts` is set) and confirms the split logic correctly separates it (verified by
hand-tracing the exact response through the algorithm before writing the fix). Not a risk
to S17Code's own production traffic — `s17code/gateway.py` always sends `reasoning: "off"`
explicitly, so this code path never fires for it, the same reasoning that made G3
"confirmed real, zero risk to this system."

## Provider coverage, confirmed complete — (Gemini addendum)

Grepped every `class .*Provider` definition across the entire `glc/` tree and every
provider-registry construction site, at explicit request, to make sure nothing else in
this bug family was missed. Chat providers, in full: `OpenAICompatProvider` and its five
subclasses (Groq/Cerebras/NVIDIA/OpenRouter/GitHub, all covered above), `GeminiProvider`
(covered here), `OllamaProvider` (already fixed in #29). No Anthropic/Claude chat provider
exists anywhere in this codebase. `EmbeddingProvider`, `STTProvider`, `TTSProvider`
hierarchies exist but are a different domain (embeddings, speech) — not chat reasoning,
out of scope for this bug.

## Known overlap with two other open PRs — (Gemini addendum)

- **#28** ("Gemini thinking was never actually turned off") rewrites the exact
  request-side `thinkingConfig` construction into a new `_gemini_thinking_config()`
  helper, for correct on/off/budget mapping. Confirmed via full diff read: does not touch
  `includeThoughts` or the response-parsing line at all. Rebase note either way: adding
  `includeThoughts` to whichever version of the request-building code is on `main` at
  merge time is a 2-key addition, small regardless of order.
- **#38** ("Strip thinking/CoT markup from normalised assistant text") replaces the exact
  response-parsing line with a `_gemini_visible_text()` helper that already skips
  `part.get("thought") is True` parts — but **discards** that content rather than
  capturing it (its own test documents "Reasoning is not the answer — do not return it as
  visible text"). Related to this fix, not redundant with it: #38 prevents leakage, this
  fix additionally makes the content observable. Rebase note: if #38 merges first, this
  fix's job narrows to also capturing what `_gemini_visible_text` already discards.

Not blocking on either — see reproduction/result below once implemented.

## Reproduction and result

```
$ uv run pytest tests/test_provider_reasoning.py -k "reasoning_text" -q   # before the fix
3 failed
  KeyError: 'reasoning_text'
  AttributeError: 'ChatResponse' object has no attribute 'reasoning_text'

$ uv run pytest tests/test_provider_reasoning.py -k "reasoning_text" -q   # after the fix
3 passed
```

**(Gemini addendum)**
```
$ uv run pytest tests/test_provider_reasoning.py -k gemini -q   # before the fix
FAILED test_geminis_own_reasoning_field_is_captured_not_discarded — KeyError: 'reasoning_text'
FAILED test_gemini_text_excludes_thought_content_when_both_are_present
  AssertionError: assert 'scratch work: 6*7=4242' == '42'
  (the actual contamination bug, reproduced by the test)
FAILED test_gemini_reasoning_text_is_none_when_no_thought_parts_return — KeyError: 'reasoning_text'
3 failed, 15 deselected in 2.14s

$ uv run pytest tests/test_provider_reasoning.py -v   # after the fix, full file
18 passed in 1.26s
```

## The fix

Three files, all additive — same shape as the sibling Ollama fix already open in #29,
applied here to the OpenAI-compat provider family:

- `glc/providers.py` — `OpenAICompatProvider.chat()` captures `message.reasoning` into a
  new `reasoning_text` key on the return dict.
- `glc/llm_schemas.py` — `ChatResponse` gains `reasoning_text: str | None = None`.
- `glc/routes/chat.py` — one line, `reasoning_text=result.get("reasoning_text")`.

This branch was cut from `main` before #29 merged, so it re-adds the same schema field
independently rather than depending on that PR landing first — each PR stays fully
self-contained and testable in isolation on a clean checkout. If both merge, the schema
addition is identical content in both diffs, at worst a trivial merge point.

**The fix (Gemini addendum) — same file, `GeminiProvider.chat()`:**

- Request side: add `"includeThoughts": True` inside both existing `thinkingConfig` dict
  literals (the `thinkingLevel` branch and the `thinkingBudget` branch).
- Response side: replace the single `text = "".join(...)` line with a split — parts
  flagged `thought: true` join into `reasoning_text`, the rest join into `text` — and add
  `"reasoning_text"` to the return dict alongside the existing `"reasoning_applied"` key.
- `llm_schemas.py`/`routes/chat.py`: no Gemini-specific change — the `reasoning_text`
  field and wiring added for Groq/Cerebras above are provider-agnostic and already cover
  Gemini's new key.

## Tests

3 new tests added to the existing `tests/test_provider_reasoning.py`: the reasoning
field being captured when present, `reasoning_text` being `None` when the provider sends
none, and the schema accepting the new field.

**Tests (Gemini addendum)** — 3 new tests added to this same, already-extended
`tests/test_provider_reasoning.py` file (not a separate file): `reasoning_text` captured
from a thought part; `text` excludes thought content when both are present (the actual
regression test for the contamination bug); `reasoning_text` is `None` when no thought
parts return.

## Boundary checklist

- [x] Credentials stay inside the gateway — no change to how any key is held.
- [x] No agent-graph/memory/A2A code added.
- [x] Existing `/v1/*` callers stay compatible — `reasoning_text` is additive; a caller
      that doesn't read it sees no difference.
- [x] Nothing sensitive committed.
- [x] `uv run ruff check .` passes on the touched files.
- [x] `uv run pytest -q` — **471 passed** (468 + 3 new Gemini tests), 2 pre-existing
      failures unrelated to this change
      (`test_channel_setup.py::test_secret_save_never_round_trips_and_requires_restart`
      and `voice/tts/test_system_fallback.py::test_synthesize_handles_empty_text`,
      identical to the pair already confirmed unrelated on #29), 1 skipped. Zero new
      failures.
