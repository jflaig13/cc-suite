# CC-Suite: Codex host entry point

Read [HARNESS_CORE.md](HARNESS_CORE.md) in full when starting work in this repository, section 0 first, then the task's role definition and relevant domain sources. The operating core is shared across hosts. These files are repository guidance within the user's request and the host's governing instructions.

Before building, find what already exists and what is in flight on the subject, and build on it. State the outcome, scope, and verification method. Continue ordinary authorized work through completion; ask only for information, decisions or authority you lack, and never ask leave for work you can do yourself.

Use the tools available in the current Codex host. Verify capabilities instead of assuming another host's hooks, slash commands, browser sessions, or idle wakeups exist. Do not install hooks, recurring jobs, accounts, or live fleet services merely by reading this file.

A role name does not admit a runtime identity. A standing seat resumes its existing identity through its init skill (for Codex, `.agents/skills/init-<role>/SKILL.md`), following the seat pattern in [GOVERNANCE.md](GOVERNANCE.md): its triggers, its standing audit, sourced work only, and a handoff. Where no live bus is available, a file bus serves: one inbox directory per role, a handoffs directory, an announcements directory, the open-items ledger and the owner's decision list, with the degraded surface named.

For parallel work, name each lane's inputs, output, owner, and completion predicate. One writer owns each mutable subject, on its own branch. Preserve user edits. Each change lands as its own pull request through the merge queue; no session waits on another with a polling loop. Report the delivered state with its evidence.

When closing or transferring a session, persist the objective, exact revision, completed work, open requirements, and next action using the templates in [governance/handoffs](governance/handoffs/).
