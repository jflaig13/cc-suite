# CIP Handoff — [Task name]

**Primary:** {agent_id} (session {session_id})
**Context at handoff:** {pct}% ({tokens} tokens)
**Trigger:** {80% threshold | pre-emptive | platform signal}
**Written at:** {ISO timestamp}
**Target subagent:** {subagent_name_or_type}

---

## Safety Context (read this first)

Pre-digested from the compressed init file so you don't have to re-read it. These rules govern your work on this task.

- **Tier/EDG:** {e.g., Tier S, EDG-2}
- **Restricted files:** {files you MUST NOT touch; explain why}
- **Active workflow spec:** {pointer to the workflow doc, if applicable}
- **Hard rules:**
  - {rule 1 — e.g., "tests must pass before any commit"}
  - {rule 2 — e.g., "do not touch X file, it's a different session's work"}
  - {rule 3 — e.g., "all verification goes through /verify-shift, not manual scripts"}
- **Nuclear rule reminders:** {any critical don'ts for this task}

**Before you start work, echo this Safety Context back to the human authority in one message.** This proves you read it. Then wait for acknowledgment or start the task per your configured mode.

---

## Active Task

{one paragraph describing what needs to be done and why}

---

## Completed Steps (with evidence)

| # | Step | Evidence | Status |
|---|---|---|---|
| 1 | {step} | {file path / commit hash / screenshot / log line} | ✅ Done |
| 2 | {step} | {evidence} | ✅ Done |
| 3 | {step} | {evidence} | ⏸️ In progress — see Next Steps |

---

## Next Steps (in order)

1. **{First action}** — {specific file, command, or tool call to execute}
2. **{Second action}** — {what and why}
3. **{Third action}** — {what and why}
...

Do NOT skip steps. Do NOT reorder them. If you think the order is wrong, stop and write a handback handoff explaining the concern — don't act on your own judgment without escalation.

---

## Critical Context

Anything the subagent needs to know that isn't obvious from the code:

- {context item — e.g., "the foo() function in bar.py has a hidden side effect that breaks baz. Don't modify foo without reading the test file first."}
- {context item — e.g., "the founder said X in the last session — do not do Y."}
- {context item — e.g., "there's a known drift between docs/foo and docs/bar; bar wins."}

---

## Files to Read FIRST

In order:

1. `{file path}` — {why this file matters}
2. `{file path}` — {why}
3. `{file path}` — {why}

These are the files the subagent MUST read before doing anything. The compressed init file is NOT on this list (you're skipping it per CIP protocol — this handoff contains the Safety Context already).

---

## Git State

- **Branch:** {branch name}
- **Last commit:** {hash and message}
- **Uncommitted changes:** {list of files with status: M/A/D/?}
- **Push status:** {ahead of origin by N / up to date / deferred}

---

## Handback Instructions

When you finish the task (or hit your own 80% context limit), write a handback handoff:

1. Update this file's "Completed Steps" table with what you did
2. Update "Next Steps" with what remains (or mark all done)
3. Update "Git State" with the new state
4. Add a "Handback Notes" section at the bottom with anything the primary should know when it resumes post-compact
5. Do NOT delete this file — the primary will read it on resume
6. The file will be deleted by the primary after the task completes (ephemeral handoff)

If the task is complete when you finish, add a final note: **"TASK COMPLETE. Safe for primary to delete this handoff."**

---

## Handback Notes (filled by subagent before relay-back)

{This section is empty until the subagent fills it in}

---

*Handoff written 2026-04-11. CIP relay pattern per `governance/SESSION_MANAGEMENT.md`.*
