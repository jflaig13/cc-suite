# Session Management Protocol

**Status:** Active (CC-Suite™ v2)
**Scope:** Long-running interactive agents with context windows
**Supersedes:** N/A (new in v2)

---

## Purpose

Long-running agents hit context-window limits mid-work. Without a protocol, compaction events either (a) drop critical context or (b) interrupt work mid-task and fail to resume cleanly. This spec defines the **Compaction Initialization Protocol (CIP)** — a relay-baton pattern where an agent hits 80% context, writes a structured handoff, and a fresh subagent picks up and continues without loss.

CIP makes compaction a **relay pass**, not an interruption.

---

## When CIP Fires

CIP activates when any of the following is true:

1. **Context usage reaches 80%** of the session's configured limit (measured by token budget, not turns)
2. **The agent anticipates the next tool call will push over 80%** (pre-emptive handoff)
3. **The founder or another authority signals session-close** (use INSTANT_HANDOFF_PROTOCOL.md instead — CIP is for in-work relay, not end-of-session close)
4. **A platform-level signal** (e.g., Claude Code's PostCompact hook) indicates a compaction just occurred and no handoff was written in the last 10 minutes

---

## The Relay Cycle

```
Primary Agent (session A, 80% context)
  │
  │  Writes CIP relay handoff to handoff directory
  │  Invokes subagent with handoff as input
  │
  ▼
Subagent (fresh context)
  │
  │  Reads handoff FIRST
  │  Echoes back Safety Context to human authority
  │  Continues the work from where Primary stopped
  │  When done (or hitting its own limit), writes updated handoff
  │
  ▼
Primary Agent (post-compact, still session A)
  │
  │  Reads updated handoff
  │  Resumes work
```

**Result:** Context discontinuity is absorbed by the relay. The task state is preserved in the handoff file, not in any agent's memory.

---

## CIP Handoff Format

See `session/CIP_HANDOFF_TEMPLATE.md` for the full template. Required sections:

1. **Safety Context** — Tier/EDG, restricted files, active workflow spec, hard rules the subagent must know. Pre-digested so the subagent doesn't need to read the compressed init file.
2. **Active Task** — What the primary was doing when it hit 80%
3. **Completed Steps** — What's already done (with evidence)
4. **Next Steps** — What to do next, in order
5. **Critical Context** — Anything the subagent needs to know that isn't obvious from the code
6. **Files to Read First** — Specific files the subagent should load immediately
7. **Git State** — Branch, uncommitted changes, last commit hash
8. **Handback Instructions** — How the subagent should write its own handoff when it's done

---

## Subagent Rules

Subagents operating under CIP follow a **different init sequence** than fresh sessions:

- **Skip the compressed init file** (HARNESS_CORE equivalent) — the handoff contains the Safety Context already digested
- **Echo the Safety Context back to the human authority** before starting work — this is a protocol check. The subagent has to prove it read and understood the safety context.
- **Work the task** — execute next steps in order
- **Write the handback handoff** when done (or hitting its own 80%)
- **Do not invent scope** — only work what the primary's handoff specified

This is by design. Subagents are NOT full-fledged replacements for the primary; they are relay workers with a narrow task. Full init + domain loading for a CIP subagent would waste the relay purpose (saving context).

---

## Ephemeral Handoffs

CIP handoff files are **ephemeral**. After the task completes, the handoff is DELETED (not archived). Reasons:

- The handoff is in-work state, not a permanent record
- Keeping completed CIP handoffs clutters the handoff directory and makes it harder to find active ones
- The git history preserves a full record of what happened if needed
- Permanent records go in SESSION_HANDOFF (via `INSTANT_HANDOFF_PROTOCOL.md`) or memos, not CIP files

Rule: if a CIP handoff is >24 hours old, it's stale and should be investigated (probably a lost relay).

---

## PostCompact Safety Net

On platforms that support compaction hooks (e.g., Claude Code's PostCompact), implement a mechanical backstop:

1. Hook fires after every compaction event
2. Check the handoff directory for a CIP file modified in the last 10 minutes
3. If none exists, emit a warning to the session: "You just compacted without writing a CIP handoff. Write one now before continuing."
4. The warning does NOT block the session — it's a reminder

This is the mechanical enforcement layer. The canon says "write CIP at 80%"; the hook enforces "if you forgot, notice and recover now."

---

## Relationship to Instant Handoff Protocol

CIP and INSTANT_HANDOFF_PROTOCOL.md are related but distinct:

| Aspect | CIP | Instant Handoff |
|---|---|---|
| Trigger | 80% context in the middle of work | Human signals "shutting down" / "updating" |
| Scope | In-work relay between primary and subagent | End-of-session persistence for the next instance |
| Destination | Ephemeral handoff file, deleted after task | Permanent session handoff, archived |
| Subagent involved? | Yes — CIP is specifically about subagent relay | No — instant handoff is between human-initiated sessions |
| Content | Safety context + next steps + files to read | Active work + completed items + open items + pickup instructions |

Both are required. Neither replaces the other.

---

## Failure Modes

1. **Lost relay** — Primary writes handoff, subagent never picks up. Detection: CIP file >24h old and never superseded. Recovery: investigate why the subagent failed to spawn; manually re-invoke.

2. **Stale safety context** — Primary wrote handoff at time T, subagent picks up at T+hours after canonical files changed. Recovery: subagent's first action is always to re-read any canon files the handoff flags as critical.

3. **Task scope drift** — Subagent reads handoff, decides to do something else, writes updated handoff that doesn't match the original task. Recovery: primary reads updated handoff, notices scope drift, escalates to human authority.

4. **Handoff directory corruption** — Multiple agents writing to the same directory cause file conflicts. Recovery: use unique filenames per relay (timestamps + agent ID).

---

## Reference Implementation

Mise uses CIP in its MacBook fleet. Directory: `cc_execs/handoffs/cip/`. PostCompact hook at `scripts/cip_postcompact_check.sh`. Auto-compact threshold 90%, CIP trigger 80%. Full spec: `docs/brain/022826__compaction-initialization-protocol.md`. Canonical CC-Suite™ v2 extraction from this reference implementation.

---

## CHANGELOG

- **v1.0 (2026-04-11):** Protocol established as part of CC-Suite™ v2 extraction from Mise's live implementation. Adds `session/` subdirectory with CIP_HANDOFF_TEMPLATE.md.
