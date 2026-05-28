# FODL Trigger Protocol

**Status:** Canonical. Part of CC-Suite™ v2.

**Scope:** All governed fleets operating under CC-Suite. Defines how the FODL is rendered to the human authority and how decisions are captured. Pair with `FODL_SCOPE_DEFINITION.md` (which defines what belongs on FODL).

---

## Core Concept

**A bare-word `fodl` trigger from the human authority fires a full closed-loop FODL workflow: freshness sweep → plain-English render of status items → structured-question-primitive presentation of every open/decidable item → human selection → immediate application + state persistence + closure confirmation, all in the same response cycle.**

The structured question primitive (e.g., `AskUserQuestion` in Claude Code) replaces deprecated text-only action blocks (`"1=B, 21=A"`) because it renders as a native tappable chip UI on mobile clients — human rips through multi-decision FODL invocations at phone speed.

---

## Why It Exists

Two failure modes the previous text-only protocol allowed:

1. **Single-pass-stop on rendering** — agent renders the FODL list with recommendations, ends with "let me know," and waits indefinitely. Human attention is the scarcest resource; round-trips compound.

2. **Stale-rendering drift** — agent re-renders the same FODL file without sweeping for new blockers that emerged since last fire. Decisions get made on outdated decision-sets.

The Trigger Protocol fixes both: freshness sweep is mandatory, presentation is via structured tool calls that capture answers as tool results, application is in the same response cycle.

---

## Trigger Recognition (Case-Insensitive)

**Fires on:**
- Bare word `fodl` (whole message, no other content)
- `fodl?`
- `show fodl`
- `my fodl`

**Does NOT fire on:**
- `FODL-NNN` item-ID lookups (those are lookups, not triggers)
- `check fodl` / `review fodl` (those render same content but presentation is optional)
- Prefixed/suffixed usage (`fodl + ...`)
- Mentions of "FODL" within broader discussion

---

## The 4-Step Workflow

When the trigger fires, the receiving agent MUST execute these four steps in the same response cycle:

### Step 1 — Read FODL file
Read the canonical FODL file (e.g., `docs/fodl/current.md`) in full. The file preserves the full internal schema (Source / Classification / Status / Reversibility / Decay / Blast radius / Ask / Options / Reasoning / Ruled out / Default / Related) for audit trail. The file is the source of truth.

### Step 2 — Render plain-English context FIRST
Status-only items + already-decided items get plain-English bullets ABOVE the questions for situational awareness:

- 📦 STATUS-ONLY (awaiting data, no decision needed)
- 🟡 DECIDED LAST 7 DAYS (human can override)

The internal schema (Tier × EDG / decay / reversibility / blast-radius) STAYS in the file. NEVER appears in the human-facing surface.

### Step 3 — Present EVERY open/decidable item via structured-question-primitive calls
For example, in Claude Code, use `AskUserQuestion`. Each open item = one question. Agent's recommended option = FIRST option, label suffixed `(Recommended)`. Use multiSelect when options are non-exclusive. Use `preview` field when option comparison genuinely helps.

Up to 4 questions per call (tool limit); for >4 open items, issue multiple sequential calls.

#### Construction rules (binding)

- **Recommended option goes FIRST** with label suffix `(Recommended)`. Description ends with one-line `why` (the MAGI verdict).
- **Plain English everywhere** — question text, option labels, descriptions. No Tier×EDG, no decay-class, no reversibility, no blast-radius, no FODL-IDs in surface text.
- **Short chip header** (≤12 chars in Claude Code's `header` field) — short identifier for the chip UI.
- **`multiSelect: True`** only when options are genuinely non-exclusive (e.g., "Which workstreams should I run in parallel?"). Default single-select.
- **`preview` field** — use when comparison genuinely helps (mockups, code snippets, structural variants, copy diffs). Skip for simple preference questions.
- **"Other" path** — most clients auto-provide "Other" for freeform/defer/hold escape. Do NOT add an "Other" option manually.

### Step 4 — Closed-loop execution (binding — EVERY TIME)
When the human taps a selection, the answer returns as tool result. In the SAME response cycle, the agent MUST:

1. **Apply the decision** — execute per the item's Owner field (memo, channel send, file edit, canon ratification, lane dispatch). No deferred execution.
2. **Update the FODL file** — mark item status (`decided` / `scribe-decided` / `closed`) + log human selection + cite implementing artifact (commit hash / file path / memo path) in item Changelog.
3. **Emit triple-emission task-complete envelope** if the disposition produced a persistent artifact (per task-completion canon).
4. **Confirm closure** to human in next response — what was decided, what was applied, what closed. Surface any blocker that emerged during execution as a new FODL item or status update on existing.

**No prose fallback. No "let me know what you think." No hedging.** The tool IS the asking surface and closure IS mechanical.

---

## The "Other" / Freeform Path

If human selects "Other" + types a freeform response, agent applies MAGI to interpret + executes per direction. If the freeform response is ambiguous, agent issues a follow-up structured-question-primitive call with the disambiguating choices — do NOT prose-back. Closed loop applies recursively.

---

## Plain-English Render Discipline

Apply plain-English language EVERYWHERE in the human-facing surface:

### Banned in human-facing FODL renders
- Tier × EDG notation (internal classification, no human value)
- "Decay: weeks/months/days" (use plain English: "the longer it sits, the [specific consequence]")
- "Reversibility: permanent/reversible/etc" (use plain English: "once executed, can't be undone")
- "Default if no answer by X = stale-flag" (use plain English: "If you don't decide by X, [specific consequence]")
- "MAGI recommends" (bake the why into the option `description` directly)
- "Blast radius:" (name the affected area in English or skip if not relevant)
- "Source:" / "Status:" / "Related:" (these belong in the audit file, not the surface)
- Action-IDs like "FODL-2026-MM-DD-NNN" in surface text (use short header chip + simple numbering; map to canonical IDs in the audit-file update)
- Condensed "1=B, 21=A" text action block — DEPRECATED, replaced by structured tool calls

### Allowed in human-facing FODL renders
- Numbered items in prose context bullets for easy reference
- Bold for emphasis in prose
- Brief code blocks for paths/files when genuinely relevant
- Structured-question-primitive calls for every open/decidable item
- `(Recommended)` suffix on the first option label per question

---

## Why the Mobile Render Is Load-Bearing

The trigger protocol's choice of structured-question-primitive over text-only render is deliberate substrate optimization for mobile UX:

- On a mobile client with native chip-UI rendering (e.g., Claude Code iOS app), the human can tap through 4+ decisions in seconds
- On a CLI client, the same tool call renders as text prompts — functional but slower
- Tool behavior is identical; client render differs
- Mobile is where the human authority spends significant decision-time

The structured tool is the canonical surface; the client renders it according to its capabilities.

---

## Anti-Patterns Rejected by This Canon

- Presenting open items as prose with "let me know what you think" → use structured question primitive
- Ending with "1=B, 21=A" condensed action block → DEPRECATED, replaced by tool calls
- "I'll execute X when you have a moment" hedging → execute immediately on selection
- Skipping the FODL file update post-selection → mandatory, audit-trail load-bearing
- Mixing decided-item summary with open questions in one prose block → separate concerns: status-only as prose above, decisions via structured tool

---

## Recognition Triggers (Standing Obligation)

Scribe standing obligations:
- Monitor for trigger-word variants ("fodls", "fodl plz", etc.) — adjust recognition accordingly or flag to human for canon amendment
- If recommended-action format proves unreadable or slow in practice, surface as meta-FODL item for calibration
- If human consistently overrides agent recommendations in a category, tighten agent MAGI on that category per calibration rule

---

## Composition

- **FODL Scope Definition** (`FODL_SCOPE_DEFINITION.md`) — defines WHAT belongs on FODL. This canon defines HOW the FODL is rendered + decisions captured.
- **Equilibrium State** (`EQUILIBRIUM_STATE.md`) — the trigger protocol composes with equilibrium operations; daily roll-ups include FODL completeness check.
- **Plain-English Render Canon** — applies to question text + option labels + descriptions + surrounding prose.
- **Task Completion Announcement Canon** — triple emission on artifact-producing dispositions.
- **From/To/Subject Envelope Canon** — closure confirmations use envelope format.
- **Mechanism Verification Attestation** — substrate canon, mechanisms verified (structured question primitive schema confirmed, mobile chip render confirmed empirically).

---

## Reference Implementation

This canon was authored in Mise's `mise-core` repo as `docs/brain/041826__fodl-trigger.md` v2.0 (2026-05-27). Mise is CC-Suite's reference implementation; the canon was ratified there in active production use. v1.0 (2026-04-18) established the bare-word trigger; v1.1 (2026-04-19) added the plain-English render; v2.0 (2026-05-27) ratified the structured-question-primitive closed-loop. The framework version sanitizes Mise-specific identifiers while preserving the operational substance.
