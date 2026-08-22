# S17 Implementation Plan — Model Arena

**Deadline: Saturday, Aug 22, 2026, 1:00 PM — resubmission allowed.**

---

## 1. How this idea evolved (so the reasoning trail isn't lost)

1. **Started from the source material, not assumptions.** Read the full session notes PDF and the entire ~90-minute live transcript, including Q&A, and cloned both `glc_v5` and `S17Code` directly rather than relying on the course's description of them. This surfaced one thing the written notes never state: the instructor explicitly told the class *not* to build the four suggested clones (Lovable/v0, Perplexity, NotebookLM, Cursor's side panel) — "everyone is going to make them."

2. **First idea pass (4 options), all deliberately non-clone:** an agent-run flight recorder, a skill A/B workbench, a "Repo Doctor" diff viewer, and a cited-source codebase Q&A tool. All grounded in the real API surface (`/v1/agent/runs`, `/events`, `/snapshot`, `/composed`, `/s/{id}`), verified against `s17code/ui/routes.py` directly rather than the course's summary of it.

3. **The pivot.** You mentioned `glc_v5` is configured with 5 Gemini keys, Cerebras, NVIDIA, Groq (corrected from an initial "Grok" — different company, different model, verified against `providers.py`), and local Ollama. That's infrastructure almost nobody else in the cohort will have. It also connected directly to real code already in the repo: `S17Code/config/tiers.yaml` and `proofs/p7_cross_model_ladder.py` already prove a measured, real cost/latency ladder across providers. That's what turned into **Model Arena**: fire the same bug fix at multiple providers in parallel, watch them race, compare what each one actually produced.

4. **Stress-tested before committing**, not just liked on first pass:
   - *Real problem, would someone use it unprompted?* The underlying problem (which model to trust for a coding task, on your own task) is real and current. The specific build is a demo artifact, not a deployed product — said plainly rather than oversold.
   - *Does this already exist?* Checked live. **Copilot Arena** (CMU/Berkeley, 11k+ installs) does pairwise human-voted comparison of single-turn code completions. **SWE-bench** (Verified/Pro/Lite/Live) is the industry-standard way to compare agentic coding across models, but it's a static published leaderboard on someone else's fixed task set. Neither does live, test-verified, run-it-on-your-own-task comparison. That's the real, if narrow, gap.
   - *UI or IDE extension?* Web UI, deliberately, not a VS Code panel — a side panel both fights a 3-lane layout and risks reading as the exact "Cursor's side panel" clone the instructor warned against.
   - *Shareable?* Yes, conditional on the lanes actually diverging — worth picking a bug non-trivial enough that outcomes differ, not one all three one-shot identically.
   - *Confidence Claude Code builds it without blockers?* High on everything since verified against real code; the two live risks are provider tool-calling reliability varying model to model, and unverified cost-accounting completeness across all 5 providers — both explicitly designed around below, not ignored.

5. **Architecture worked out through direct verification, not guesswork**, across several rounds:
   - How 3 parallel lanes edit code without racing on the same files → **git worktree**, discovered to be the intended tool because `workspace.py` itself says *"the workspace is a git repository on purpose."*
   - How 3 differently-configured lanes actually run → 3 separate `S17Code` processes, one `glc_v5`, confirmed via `cli.py` (port is `S17_PORT`, configurable) and `main.py` (`load_dotenv` doesn't override already-exported shell vars, so per-terminal `export` before launch works cleanly).
   - Whether the graph is real and drawable → yes, and specifically **networkx** (`nx.DiGraph`, with a live `nx.is_directed_acyclic_graph()` check) — corrected on the record after an initial non-recursive grep missed it entirely.
   - Whether prompt/thinking content reaches the UI → confirmed via your own already-open PRs (`S17Code` #17, `glc_v5` #29/#30): once merged, `ui/agui.py` forwards it to the browser automatically, no new code needed. Also surfaced that `glc_v5` #28 (a classmate's PR) fixes Gemini's thinking *budget*, not response *content* — Gemini reasoning-text extraction is a confirmed, real, unaddressed gap.
   - Framework choice re-evaluated on request (FastUI, Prefab) rather than defended reflexively — verdict below.

6. **Session-fit self-check**, against sections 17 and 18 specifically, line by line, not just the highlights. Two real gaps surfaced (thrashing has no UI state, the validator never appears in any lane) — both are surfacing *existing* harness signals, not new judgment logic to build.

---

## 2. Why this is the right call, and why it's pressing now

- **The gap is real and current**, not manufactured for this assignment: even 2026's own SWE-bench commentary states "same model, different harness, swing of 15-20 points" — model-and-harness-specific comparison is an open, live question in the industry, not a solved one.
- **It's genuinely hard to clone** — it depends on infrastructure (5 configured providers) most of the cohort won't have, which satisfies "don't build the four obvious things" as a natural consequence of the idea, not as a forced constraint.
- **It reuses what the session already built** rather than reinventing: the AG-UI SSE protocol, the `networkx` DAG, `tiers.yaml`'s cost ladder, the existing `python-bugfix` skill. Every major piece of the Arena is a frontend for something the harness already does, which is exactly what "the engine is S17Code, the frontend is yours" asks for.
- **The deadline is ~48 hours out from today.** That changes this from a design conversation into an execution problem. Section 5 below sequences accordingly — MVP first, everything else layered on only if time allows, not built in parallel.

---

## 3. Technical architecture

### Repos and processes

| Component | Repo | Role | Port |
|---|---|---|---|
| Gateway | `glc_v5` | Holds all 5 provider keys. Single instance. Unmodified. | 8111 |
| Lane 1 | `S17Code` (process A) | Coding loop, pinned to Groq | 8113 |
| Lane 2 | `S17Code` (process B) | Coding loop, pinned to Gemini | 8114 |
| Lane 3 | `S17Code` (process C) | Coding loop, pinned to NVIDIA — **stretch, add after 2-lane MVP works** | 8115 |

Same `S17Code` codebase, same `.py` files, launched three times with different environment variables. No forking, no duplication of application code.

### Target/workspace repo (separate from S17Code and glc_v5)

A small, purpose-built demo repo, not a real open-source project — full control over difficulty and over whether lanes are likely to diverge. Bug #1: the `average()` empty-list case from the session itself (thematically apt, guaranteed clean pytest example). Bug #2 (stretch): something with more room for divergent approaches.

```bash
git worktree add /abs/path/workspace-groq   main
git worktree add /abs/path/workspace-gemini main
git worktree add /abs/path/workspace-nvidia main   # stretch
```

### Launch (per lane, separate terminals)

```bash
export S17_PORT=8113
export S17_GATEWAY_PROVIDER=groq
export S17_WORKSPACE=/abs/path/workspace-groq
export S17_SKILLS_DIR=/abs/path/S17Code/skills   # ships python-bugfix already — see §6
export S17_CONTROL_TOKEN=<same value across all lanes>
uv run s17code serve
```

Repeat per lane with the matching provider/workspace/port. `glc_v5` launches once, separately, holding all keys.

### API surface used (verified against `s17code/ui/routes.py`)

- `POST /v1/agent/runs` — starts a run. Requires `Authorization: Bearer <S17_CONTROL_TOKEN>`.
- `GET /v1/runs/{id}/events` — SSE stream, AG-UI protocol. No auth required (read-only).
- `GET /v1/runs/{id}/snapshot` — full state in one call, for reconnects. No auth required.
- Frontend never handles raw credentials for anything beyond the one control-token POST — read/stream side talks to each lane directly from the browser.

---

## 4. UI / UX spec

All screens already reviewed as mockups in chat — summarized here for the build, not re-embedded as raw markup (the mockup HTML was styled for the chat widget's own CSS-variable system, not meant to be copied verbatim into production).

1. **Start screen.** One task field (editable — this is the only per-run input). Below it, a read-only row per lane showing which workspace it's pinned to (lock icon — deliberately not editable from the browser; workspace is fixed at process launch, not per-request). "Start race" button fires the same prompt at all configured lanes' `/v1/agent/runs`.
2. **Live race (screen 1).** One column per lane. Each column: provider name, status badge (running / passed / refused / **thrashing — new, see §7 gap 1**), a compact live log fed straight from that lane's own `/events` stream, running stats (elapsed time, step count, cost).
3. **Scoreboard (screen 2).** Same layout, not a separate page — log rows freeze into final stats in place once each lane's `RUN_FINISHED` arrives. Badges for cheapest/fastest where applicable.
4. **Compare-edits panel.** One column per lane, populated from each lane's `edit_code` event payloads directly (anchor text + replacement — already exactly what `edit.py`'s mechanism produces, no new backend data needed). A lane with no successful edit (refused, or thrashed) shows an honest empty state, not a blank column.
5. **Lane graph detail (stretch).** Click into a lane, render its `nodes`/`edges` from `/snapshot` as an actual directed graph (networkx-backed data, confirmed). Different lanes will produce genuinely different-shaped graphs, not just different content.
6. **Step detail / thinking panel (stretch, depends on §6 PR work).** Click a log line, show prompt / response / thinking for that step. Needs a graceful "no thinking returned for this step" state — not every provider returns one, and this is also where the Qwen 3 `<think>`-tag gap you raised live in the session would resurface if Qwen is ever added.
7. **Validator step (stretch, addresses §7 gap 2).** After a lane goes green, show a distinct "validated" step, sourced from the same `validate_landing_page`-style node type the one-prompt walkthrough already demonstrated exists.

---

## 5. Build sequencing (deadline-aware — this order matters)

**MVP (must-have, target: day 1):**
- 2 lanes only (Groq, Gemini) — NVIDIA is the first thing cut if time runs short, not the first thing built
- Start screen → live race → scoreboard (items 1–3 above)
- `python-bugfix` skill enabled (§6 — zero authoring needed)
- One demo bug, tested end to end, recorded once dry-run before the real take

**Stretch (only after MVP runs cleanly end to end):**
- 3rd lane (NVIDIA)
- Compare-edits panel (item 4)
- Thrashing status + validator step (closes both §7 gaps — worth prioritizing over graph/thinking detail if only one stretch item fits, since these are the two items that came out of the section 17/18 compliance check, not just nice-to-haves)
- Lane graph detail, thinking panel (items 5–6)

**Stack:** vanilla HTML/CSS/JS for the race view (it's consuming `S17Code`'s own AG-UI SSE protocol directly — the native fit). FastUI, optional, for the start-screen chrome only, if Python-only authoring there is wanted. Not Prefab — too new, MCP-centered, unproven for this exact multi-process external-SSE pattern under a hard deadline.

---

## 6. Skills — what's actually needed

**Nothing new to author for the MVP.** `S17Code/skills/python-bugfix/SKILL.md` already ships in the repo and is exactly shaped for an `average()`-style fix:

> Change the code, never the test... Read before you edit... Stop after four.

Point `S17_SKILLS_DIR` at the existing `skills/` folder. Confirm this is actually set before recording — without it, the agent behaves like the pre-Session-17 "32 seconds, zero verification, ships broken" version, which undercuts the whole demo.

---

## 7. Gaps found against sections 17 & 18, and mitigations

| # | Gap | Mitigation | Priority |
|---|---|---|---|
| 1 | No "thrashing" status distinct from "refused" in the UI (commitment #6) | Add a fourth lane status; confirm how a thrashed run signals itself in the event stream (likely a distinct `RUN_FINISHED` reason or `S17_MAX_REPEAT_FAILURES` metadata) before building the label | Stretch, but prioritized |
| 2 | No validator step shown anywhere (commitment #7 — repeated 3x in the session as the single most important theme) | Surface the existing `validate_*`-style node type after a lane goes green | Stretch, but prioritized |
| 3 | ~~Scope vs. "no Opus behind you"~~ | Resolved — this is informal Q&A advice, not a graded criterion, and applies to per-call task complexity, not to product/orchestration complexity. No design change needed. | Closed |
| 4 | Unclear what SKILL.md work was needed | Resolved — `python-bugfix` already exists, zero authoring required for MVP | Closed |
| 5 | README attribution statement not concretely planned | Write it last, after it's known what was actually agent-generated vs. hand-specified — one paragraph, required by the assignment's own honesty rule | Must-have, cheap |
| 6 | Deadline unknown | Resolved — Aug 22, 2026, 1:00 PM, confirmed via the submission portal | Closed |
| 7 | Gemini reasoning-text extraction | Confirmed genuine gap (neither `glc_v5` #28 nor #29/#30 covers it). Optional 3rd/4th bug-fix PR candidate if time allows — same pattern as #29/#30 | Optional, time-permitting |
| 8 | Provider tool-calling reliability may vary (not a bug, a capability limit) | Design the UI to show this gracefully (a lane that stalls isn't "broken," it's informative) rather than treat it as an implementation failure | Design awareness, no separate task |

---

## 8. Part 2 — bug-fix PRs (already substantially done)

Reuse and extend, don't rebuild:

- **`S17Code` #6** — guard.py had two bypass routes (`copy_within_workspace` skipping the guard entirely, and a `..` path-normalization mismatch). Real, tested, TDD-shaped. Currently open, not merged.
- **`S17Code` #17 + `glc_v5` #29/#30** — the reasoning-content chain: gateway discarding `reasoning_text`, Ollama and Groq/Cerebras (and per #30's body, possibly NVIDIA/OpenRouter/GitHub) never having it wired at the source. All three currently open, not merged, and #17 explicitly depends on #29 and #30.
- **`glc_v5` #33** — unrelated (Windows/Gmail SMTP hostname bug). Real and good work, just not part of this narrative — don't present it as part of the reasoning-chain story.
- **`glc_v5` #28** (classmate's, not yours) — fixes Gemini's thinking *budget/on-off control*, not response content. Confirms gap #7 above is real.

**Action needed before "reusing" these:** none are merged into `main` yet. Either merge your own branches locally first, or work directly off your fork's branches — don't assume `main` already has this behavior.

**For the two required submission slots** (`glc_v5` bug PR, `S17Code` bug PR, 100 pts each): #6 and #17 already satisfy both slots on their own merits. The Gemini extension (gap #7) is a good optional third if time allows, not required to hit the 200 points.

---

## 9. Submission checklist (from the actual assignment portal)

- [ ] YouTube link — unlisted is fine, **must not be Private** (portal requires incognito-window accessibility)
- [ ] YouTube caption written
- [ ] GitHub repo link — **must be public**, not private (same incognito check)
- [ ] GitHub caption written
- [ ] README includes a plain attribution statement (agent-written vs. hand-written)
- [ ] Bug PR link — `glc_v5`
- [ ] Bug PR link — `S17Code`
- [ ] Demo shows the run succeeding *and* failing/refused (not just the happy path)
- [ ] All four "tested in incognito" checkboxes actually verified, not just assumed

---

## 10. Your manual checklist (not agent-automatable — review and adjust this section specifically)

- **Choosing and seeding the demo bug(s).** Claude Code can scaffold the repo, but which bug(s) to use, and how likely they are to make lanes diverge, is a judgment call worth a quick personal dry run before committing.
- **Provisioning credentials and env vars** for each lane — can't be done by an agent inside a repo; needs your actual keys.
- **Running the launch commands** and confirming all processes and worktrees are healthy before recording.
- **Deciding whether to merge your open PRs into `main` locally first, or build against your fork branches directly** — a git workflow call only you should make.
- **Recording and narrating the YouTube demo** — inherently manual.
- **Reviewing the README's attribution paragraph** before it's final — Claude Code can draft it, but the actual claim about what you wrote vs. what the agent wrote needs your sign-off, since that's the assignment's explicit honesty rule.
- **Final go/no-go on which stretch items make the cut**, given the ~48-hour window — Section 5's sequencing is a recommendation, not a mandate.
- **Checking the submission portal's incognito requirement yourself** right before submitting, since that's a live check against your actual account/repo visibility settings.
