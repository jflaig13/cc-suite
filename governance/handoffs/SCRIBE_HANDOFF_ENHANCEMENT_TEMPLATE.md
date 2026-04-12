# Scribe Handoff Enhancement — {AGENT_ID} — {DATE}

**Status:** Enhanced handoff. The Scribe has read the agent's raw handoff and added session-wide context the agent might not have included.

**Enhanced by:** Scribe
**Original handoff:** `{path to agent's raw handoff file}`
**Enhanced at:** {TIMESTAMP}
**Posted to:** {coordination channel or destination}

---

## Agent's Handoff (Verbatim)

*Include the agent's raw handoff verbatim — do not edit or rewrite. The Scribe adds context in the sections below.*

{PASTE AGENT HANDOFF HERE}

---

## Session-Wide Context (Scribe Addition)

*Governance, rulings, and canon that were established during the session and that the successor needs to know.*

### New Canon / Governance
- {New rule, tolerance, or canonical decision from this session}
- {Another new canon item}

### New Brain Files / Memos
- {Path to brain file created or updated during session, with one-line purpose}
- {Path to memo, with one-line purpose}

### Canon Changes That Affect This Agent
- {Any CC-Suite v2 rule update, tolerance addition, or policy change that directly affects how this agent operates}

---

## Cross-Agent Dependencies

*How this agent's work connects to other active agents' work.*

- {Agent A's P1 depends on this agent's file X being committed}
- {Agent B is waiting on this agent's V-Loop Step 6 verdict}
- {Tandem pair {A, B} has an open ticket that blocks this agent's Open Item 3}

---

## Coordination Board State

*Snapshot of the coordination surface at the moment the handoff was enhanced.*

- **Open tickets:** {count and summary}
- **Active tandem pairs:** {list}
- **Pending verdicts:** {what is waiting on whom}
- **Blocked items:** {what is waiting on the human authority}

---

## Scribe Verdict

*Short Scribe assessment of the handoff quality. Does it pass the self-contained test? Are there any open items that were implicitly left out? Does anything need clarification before the successor can start?*

**Self-contained:** {YES / NO — if NO, describe what is missing}
**Information loss risk:** {LOW / MEDIUM / HIGH — if not LOW, describe the risk}
**Recommended first action for successor:** {Scribe's recommendation on where to start, which may differ from the agent's own pickup instruction #1 if the Scribe sees a higher-priority item}

---

## Rules for Scribe Enhancement

1. **Never rewrite the agent's raw handoff.** Include it verbatim. The Scribe's additions go in the sections below the raw handoff, not inside it.
2. **Add context, do not make decisions.** The Scribe is an auditor, not an executive. If the Scribe sees a problem, it notes it under "Scribe Verdict" but does not unilaterally change the handoff's priorities or pickup instructions.
3. **One enhanced handoff per agent.** If three agents wrote handoffs, the Scribe produces three enhanced handoffs — not one aggregate document.
4. **Post to the coordination channel.** The channel is where successors look. Do not rely on the raw handoff file alone.
5. **Write a Scribe-to-Scribe handoff as well.** The Scribe's own session is also closing — it must produce its own handoff documenting Scribe-specific work (audit-log entries, newly wired canon, pending investigations).

---

## Reference Implementation

Mise's Scribe reads all CC Exec handoffs from `cc_execs/memos/scribe/{ROLE}_handoff_{DATE}.md`, enhances each one with the current Operations Board state, any new tolerances or canon from the session, and cross-agent dependencies, then posts the enhanced version to `#tandem`. The Scribe also writes its own handoff to `cc_execs/memos/scribe/Scribe_to_Scribe__session-{DATE}-handoff.md` before its own session closes.
