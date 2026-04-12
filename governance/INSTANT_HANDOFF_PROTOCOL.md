# Instant Handoff Protocol

**Status:** Canonical. Part of CC-Suite™ v2.

**Scope:** Every governed agent in every session. No exceptions.

---

## The Rule

**When the human authority signals session close — "shutting down," "updating," "new windows," "closing," "restarting," or any equivalent — every active agent writes a structured handoff IMMEDIATELY. No delays. No questions. No negotiation.**

The handoff must be self-contained: a new instance reading only the handoff (no chat history, no prior context) should know exactly what to do next.

---

## Why It Exists

Session transitions waste time. Without a protocol, each transition looks like:

1. Agent closes mid-work
2. Human opens a new window
3. Agent runs through init (reads mandatory files)
4. Agent takes any required quizzes
5. Agent tries to reconstruct what the previous instance was doing — usually from stale chat history or guesswork
6. Agent finally starts work, often duplicating effort or skipping steps

Instant Handoff collapses steps 5 and 6 into a single read of a structured file. Every agent knows exactly what to hand off, in what format, to what destination. The next instance picks up deterministically.

---

## When It Triggers

Any of these phrases (or equivalents) from the human authority:

- "shutting down"
- "updating" (Claude Code update, framework update, etc.)
- "new windows"
- "closing"
- "restarting"
- "need to update Claude Code"
- "rebooting"
- Any variant implying all agent sessions are about to close

Agents do NOT wait for a more explicit signal. If the intent is clearly "sessions closing soon," the protocol fires.

---

## Handoff Format

Every agent writes a handoff to its designated handoff location (template: `governance/handoffs/SESSION_HANDOFF_TEMPLATE.md`). The handoff MUST contain these sections:

1. **Active Work** — exact state when the session closed. Include file paths, line numbers, V-Loop step number if mid-ticket, test status, deploy status.
2. **Completed This Session** — numbered list of what was finished this session.
3. **Code Changes (Uncommitted)** — files modified, what changed, commit status, deploy status.
4. **Open Items (Priority Order)** — P0 (drop-everything), P1 (next action), P2+ (queue).
5. **Strike Count** — current strike count for this agent's ID.
6. **Pickup Instructions** — exact first action, second action, and what to check in the coordination channel. Numbered list. No ambiguity.

---

## Target Timing

**Under 2 minutes total** for the handoff write + Scribe enhancement + channel post. The protocol fails if it's slow enough that the human authority has to wait.

- Agent writes handoff: ~30 seconds (sections are short; the agent already knows the state)
- Scribe reads + enhances + posts to coordination channel: ~60 seconds
- New instance reads handoff and starts work: ~30 seconds

A handoff that takes 10 minutes to write is an anti-pattern. The handoff is a state snapshot, not a retrospective.

---

## The Scribe Enhancement Step (Optional But Recommended)

If your deployment has a Scribe (independent auditor role), the Scribe adds session-wide context that individual agents might not include:

- New governance rules, tolerances, or canon established during the session
- The current coordination-board state (open tickets, active tandem pairs, pending decisions)
- Cross-agent dependencies (e.g., "Agent A's work blocks Agent B's open item 3")
- Links to any brain files, memos, or decisions made during the session

The Scribe reads all agent handoffs, produces one enhanced handoff per agent, and posts them to the coordination channel (or whatever your equivalent surface is — shared file, channel, queue).

Template for the Scribe's enhancement layer: `governance/handoffs/SCRIBE_HANDOFF_ENHANCEMENT_TEMPLATE.md`.

**If your deployment has no Scribe:** skip this step. The agent's own handoff must be sufficient — which is why the `SESSION_HANDOFF_TEMPLATE.md` is designed to be complete on its own.

---

## How the Next Instance Picks Up

The successor instance, when it initializes:

1. Runs its normal init protocol (reads mandatory files per `.claude/CLAUDE.md` or equivalent)
2. **Reads the handoff FIRST** — before any other work. The handoff takes precedence over stale context from previous sessions.
3. Verifies the stated state is accurate (e.g., if the handoff says "file X modified at line 47," the successor opens file X and checks line 47)
4. Starts execution from the Pickup Instructions in the handoff

**The handoff is truth. If the handoff contradicts chat history or old memos, the handoff wins.**

---

## Self-Contained Handoff Rule

A handoff is self-contained only if a fresh instance with zero prior context can pick up work from it alone. Test: if the handoff says "continue the payment calculation fix," is that enough? No — the successor doesn't know which file, which bug, which test, which deploy. A self-contained handoff says:

> "Mid-ticket BUILD-042: payment calculation bug in `billing/calculate.py` line 128. V-Loop Step 3 complete (implementation + 14 passing tests). Next action: notify Reviewer (currently idle on TANDEM_BOARD) and hand off for V-Loop Step 4 code review. Deploy pending Reviewer PASS verdict."

If the handoff doesn't pass the self-contained test, the agent rewrites it before posting.

---

## No Information Loss

If the agent was mid-V-Loop, the handoff includes which step, what was verified, what remains. If code was changed, the handoff includes files + line numbers. If a test was failing, the handoff includes the exact test name and failure message. If a deploy was pending, the handoff includes deploy status and next action.

Information loss during handoff is a strike-eligible protocol violation. The handoff is the mechanical substitute for perfect memory — it must capture what the successor needs.

---

## Related Files

- `governance/handoffs/SESSION_HANDOFF_TEMPLATE.md` — the standard template every agent writes to
- `governance/handoffs/SCRIBE_HANDOFF_ENHANCEMENT_TEMPLATE.md` — how the Scribe enhances handoffs before posting
- `governance/handoffs/TANDEM_BOARD_TEMPLATE.md` — for mid-tandem sessions, the tandem board state also belongs in the handoff
- `GOVERNANCE.md` — master governance spec
- `governance/SESSION_MANAGEMENT.md` — CIP (Compaction Initialization Protocol) for context-boundary handoffs during long sessions (Phase 3 of CC-Suite v2)

---

## Reference Implementation

Mise Inc. established the Instant Handoff Protocol on 2026-03-28 via founder directive. See `docs/brain/032826__instant-handoff-protocol.md`. Implementation details:

- Handoff location: `cc_execs/memos/scribe/{ROLE}_handoff_{DATE}.md`
- Scribe enhancement: Scribe reads all role handoffs, enhances with session context and current Operations Board state, posts one enhanced handoff per role to `#tandem` Slack channel
- Successor pickup: New Claude Code instances running `/init-{role}` auto-detect tandem mode (TANDEM_BOARD.md exists), check `#tandem` during init, read the enhanced handoff, and start from the Pickup Instructions
- Typical handoff cycle time: ~90 seconds from "shutting down" signal to next-instance starting work
