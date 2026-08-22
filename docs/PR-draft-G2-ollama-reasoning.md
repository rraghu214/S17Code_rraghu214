# Title

Ollama never read the reasoning dial, and discarded the model's own reasoning field

# Body

## What breaks

`OllamaProvider` accepts a `reasoning` parameter and never reads it. Every request it
builds is identical regardless of what a caller asks for — `reasoning: "off"` and no
`reasoning` field at all produce byte-for-byte the same request.

Measured live against `qwen3:8b`, a model that thinks by default:

| | `reasoning` unset | `reasoning: "off"` |
|---|---|---|
| `reasoning_applied` | `false` | **`false`** — asked explicitly, still ignored |
| `text` | `""` | `""` |
| `output_tokens` | 300 / 300 | 300 / 300 |
| `latency_ms` | 142,445 | 95,032 |

Explicitly asking for `"off"` had zero effect. **95 to 142 seconds per call for zero
usable output** — worse than a cost problem, this makes the Ollama tier practically
unusable through this gateway, not merely inefficient.

Separately: Ollama's real response already separates a model's reasoning from its
answer, into `message.thinking` — never merged into `content`. That field was already
generated, already billed for, and silently discarded on every call.

## Reproduction and result

Ollama's own `/api/chat` was confirmed live to accept `think` as `false`/`true` or a
graded `"low"`/`"medium"`/`"high"`/`"max"` string — an invalid value is rejected with
the exact set: `must be "high", "medium", "low", "max", true, or false`. But at a
realistic token budget, only `false` actually resolves anything: `unset`, `true`,
`"low"`, `"medium"`, `"high"` and `"max"` all hit the identical wall on `qwen3:8b` —
`eval_count: 300` (budget fully consumed), `done_reason: "length"`, `content: ""`. Only
`think: false` produced a correct, complete answer (`"391"`, `done_reason: "stop"`,
118 of 300 tokens used).

```
$ uv run pytest tests/test_ollama_reasoning.py -q     # before the fix
7 failed, 1 passed
  KeyError: 'reasoning_text'
  AttributeError: 'ChatResponse' object has no attribute 'reasoning_text'
  assert client.seen[0]["think"] == expected_think   # 'think' key never present

$ uv run pytest tests/test_ollama_reasoning.py -q     # after the fix
8 passed
```

## The fix

Three files, all additive — nothing existing changes shape:

- `glc/providers.py` — `OllamaProvider.chat()` translates `reasoning` into Ollama's real
  `think` field (`"off"` → `false`, `"low"/"medium"/"high"` → the same string, unset →
  nothing sent, unchanged from today); fixes `reasoning_applied` to reflect what was
  actually put on the wire instead of a hardcoded `False`; captures `message.thinking`
  into a new `reasoning_text` key rather than discarding it.
- `glc/llm_schemas.py` — `ChatResponse` gains `reasoning_text: str | None = None`.
- `glc/routes/chat.py` — one line, `reasoning_text=result.get("reasoning_text")`, using
  `.get()` so no other provider is affected before it adds the same field.

Deliberately **not** changed: `reasoning` left unset still sends nothing to Ollama. That
default-suppression question is real but distinct (tracked separately) and out of scope
for this fix — the caller this gateway actually serves always sends `"off"` explicitly.

## Tests

`tests/test_ollama_reasoning.py` — 8 new tests: the `off`/`low`/`medium`/`high`
translation, unset staying unchanged, `message.thinking` capture, capture returning
`None` when absent, and the schema accepting the new field.

## Boundary checklist

- [x] Credentials stay inside the gateway — no change to how any key is held.
- [x] No agent-graph/memory/A2A code added.
- [x] Existing `/v1/*` callers stay compatible — `reasoning_text` is additive; a caller
      that doesn't read it sees no difference.
- [x] Nothing sensitive committed.
- [x] `uv run ruff check .` passes on the touched files.
- [x] `uv run pytest -q` — 473 passed, 2 pre-existing failures unrelated to this change
      (confirmed via `git stash` against a clean `main`: `test_channel_setup.py::
      test_secret_save_never_round_trips_and_requires_restart` and
      `voice/tts/test_system_fallback.py::test_synthesize_handles_empty_text`), 1 skipped.
      Zero new failures.
