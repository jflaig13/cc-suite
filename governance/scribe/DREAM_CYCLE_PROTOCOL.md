# Dream Cycle Protocol

**Status:** Active (CC-Suite™ v2)
**Authority:** Layer 6 (Governance Spec). Scribe governance pattern.
**Scope:** Framework-level. Generic — applicable to any CC-Suite deployment.
**Related:** `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md`, `governance/scribe/SUBATOMIC_AUDIT_PROTOCOL.md`

---

## The Problem

Scribe is session-triggered. In deployments where sessions don't fire every day, maintenance accumulates:

- Task-complete artifacts get committed without a paired announcement. Gaps pile up silently.
- New governance files land in the knowledge base without matching pointers in the memory index. Canon drift goes undetected.
- Events accumulate in the event queue with no session available to process them.

Each gap is small. Over a week, they compound into a state where the next Scribe session spends significant time on archaeology before doing governance work.

The Dream Cycle is the solution: scheduled nightly maintenance on an always-on host, no Scribe session required.

---

## Concept

A Scribe session is reactive: it fires when a human opens it. A Dream Cycle is proactive: it fires on a schedule while the human is not available.

The Dream Cycle does not replace Scribe governance. It is a checkup — four lightweight maintenance tasks that detect gaps and emit a digest. Governance decisions, canon-drift sweeps, and ratification remain MacBook (or primary-host) Scribe responsibilities.

The analogy: a Dream Cycle is like a nightly janitor pass. It sweeps and reports. It does not make decisions about what it found.

---

## The Four Maintenance Tasks

Every Dream Cycle run executes these four tasks in order:

**1. Gap-Fill Check**

Scan recent commits (last 24 hours) and cross-reference against the task-complete announcement log. Any commit that produced a persistent artifact without a paired announcement file is a gap.

List gaps in the digest. Do not remediate — report only.

This automates the session-init gap-fill scan defined in the Task Completion Announcement Canon. The responsible agent addresses gaps in their next session.

**2. Brain Pointer Check**

Scan the most recently added brain/knowledge files (typically the last 20). For each, check whether the memory index (MEMORY.md or equivalent) contains a pointer to it.

A knowledge file with no memory pointer is a canon drift signal: the rule exists in the file system but is invisible to agents loading the memory index at session start.

Flag files without pointers in the digest. Scribe addresses at next session.

**3. Event Queue Scan**

Check the Scribe event queue for events with timestamps older than 24 hours.

Implementation note: many event queue implementations are dynamic (the file is created on write and deleted on drain). Check for file existence before scanning. A missing file typically means no pending events — a healthy state. Report accordingly.

Events older than 24 hours indicate no session processed them overnight. Flag them. Do not process them — processing requires a full governance Scribe session.

**4. Digest Emit**

Emit a summary to the team communication channel (e.g., #tandem in Slack):

```
FROM: [dream-cycle-agent]
TO: Fleet
SUBJECT: nightly-dream-cycle-MMDDYY

[Date]

Gap-fill: [N gaps / none]
Brain pointers: [N missing / all clear]
Event queue: [N stale / clear]

[Detail]
```

Emit the digest regardless of findings. An "all clear" digest is a positive signal confirming the cycle ran.

---

## When to Use

Deploy a Dream Cycle when:

- Scribe sessions don't fire every day (typical for founder-operated deployments)
- Task-complete gaps regularly accumulate between sessions
- Brain files land faster than session-start gap-fill scans can keep up
- You want fleet-health visibility in the team channel without requiring a Scribe session

---

## Infrastructure Requirements

The Dream Cycle requires:

1. **An always-on host.** The primary host (laptop, workstation) is unsuitable — it sleeps. Use a dedicated machine (Mac Mini, cloud VM) or a cloud agent runtime (Claude Managed Agents, or equivalent).

2. **A scheduled agent runtime.** The always-on host must support cron-style job scheduling with an agent that can execute the four tasks and emit a Slack digest. OpenClaw (`openclaw cron`) and Claude Managed Agents both qualify.

3. **Repo access.** The Dream Cycle agent needs read access to the repository (git log, knowledge files, memory index, event queue).

4. **Slack write access.** The agent needs permission to post to the team channel.

---

## The mini-scribe Identity Principle

The agent executing the Dream Cycle should have a narrow identity: **maintenance, not governance.**

Write its SOUL.md (or equivalent identity file) to explicitly scope it to maintenance tasks:

- Run the four tasks
- Emit the digest
- Do not issue strikes
- Do not ratify canons
- Do not make decisions about the gaps found
- Do not do governance work

This prevents scope creep. A Dream Cycle agent with a broad "Scribe" identity will start making governance judgments it shouldn't be making from a nightly cron run. Narrow identity is load-bearing here.

---

## Scope Boundary (Explicit)

The Dream Cycle is maintenance automation. It is NOT:

- A replacement for Scribe governance sessions
- An authority for issuing strikes or terminations
- Authorized to run 7-wave subatomic audits
- Authorized to ratify or sweep canons
- Authorized to process pending events (it flags them, it does not process them)

The Dream Cycle runs, reports, and stops. Every finding it surfaces waits for a full Scribe session to act on.

---

## Deployment Checklist

Before deploying a Dream Cycle in any CC-Suite instance:

- [ ] Identify the always-on host and agent runtime
- [ ] Verify the cron schema from an existing deployed agent config (Mechanism Verification Attestation Canon — verify claims against implementation, not documentation)
- [ ] Create an isolated workspace for the Dream Cycle agent
- [ ] Write a narrow SOUL.md (maintenance identity only)
- [ ] Configure the cron job with a 2 AM schedule (or equivalent off-hours time)
- [ ] Manually trigger a test run before relying on the scheduled job
- [ ] Verify the digest appears in the team channel
- [ ] Update the mechanism attestation in the canon brain file
- [ ] Add the agent to the ID registry
- [ ] Add the job to the fleet operations document

---

## Reference Implementation

The Mise reference implementation:
- Always-on host: a dedicated nightly-cron host (Mac Mini in Mise's case; could be any always-on host with SSH access from the primary fleet host)
- Agent runtime: OpenClaw (one option; substitute your agent-runtime layer)
- Agent role: `mini-scribe` (a dedicated nightly scribe persona; substitute your role naming convention)
- Schedule: Daily 2 AM local time
- Cron config template available in the Mise reference implementation's brain-files directory
- Canon brain file: ratified in the Mise reference implementation 2026-05-18 (includes full deployment instructions for the reference implementation's specific host architecture)
