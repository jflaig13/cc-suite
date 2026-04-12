# Ephemeral Agent Lifecycle

**Status:** Active (CC-Suite™ v2 — Phase 2 Channels)
**Authority:** Layer 6 (Governance Spec). Specialization of `governance/LIFECYCLE.md` for batch-triggered ephemeral agents.
**Scope:** Any agent that is cron-triggered or scheduled, spawns to execute a single task, and exits without a persistent process.
**Related:** `governance/LIFECYCLE.md` (base lifecycle), `governance/lifecycle/INTERACTIVE_LIFECYCLE.md`, `governance/DIGEST_BRIDGE_PROTOCOL.md` (Phase 3), `governance/CHANNEL_PROTOCOL.md`.

---

## Definition

An **ephemeral agent** is an agent session that is:

- **Batch-triggered** — launched by a scheduler (cron, systemd timer, Kubernetes CronJob, or similar) rather than by a human at a terminal
- **Single-turn** — spawns, executes one task, writes its output, and exits
- **Short-lived** — typical session duration is seconds to minutes, not hours or days
- **Consumer-less** — there is no persistent process listening for pushed events; the agent isn't running most of the time
- **One-way output** — communicates results via files, digest bridges, or direct posts to a shared bus, never via real-time event reception

Ephemeral agents are the right shape for autonomous batch work: nightly digests, scheduled scans, periodic audits, data pipelines. They are NOT the right shape for work that requires real-time coordination or human interaction.

Contrast with **interactive agents** (`governance/lifecycle/INTERACTIVE_LIFECYCLE.md`), which are long-running, channel-connected, and require persistent presence in the fleet.

---

## Why Channels Don't Apply

The channel protocol (`governance/CHANNEL_PROTOCOL.md`) is built on a load-bearing assumption: the agent is a long-running process that can receive a push at any moment. Ephemeral agents violate that assumption directly. They are dead 99%+ of the day. A channel server pushing events to a dead agent is a switchboard with all the phones disconnected — the infrastructure accepts the push and has nowhere to deliver it.

Attempting to port the channel architecture to ephemeral agents produces a category error. You end up with:

- Channel servers with no consumers
- Queue files that fill up but are never drained
- Port allocations for processes that don't exist
- Health checks that all report "unhealthy" because the consumer isn't there
- Maintenance burden for infrastructure that watches nothing

**Do not port the channel architecture to ephemeral agents.** Use the digest bridge pattern instead.

---

## The Digest Bridge Pattern (One-Way Output)

Ephemeral agents communicate their output via a one-way push: the agent writes a structured digest to a shared location, and a small bridge script reads the digest and posts it to the shared message bus. Interactive agents on the other side receive the digest through their normal channel pipeline.

```
┌────────────────────┐
│  Cron / scheduler  │
└─────────┬──────────┘
          │ fires at interval
          ▼
┌────────────────────┐
│  Ephemeral agent   │
│  spawns, runs,     │
│  writes digest     │
│  to shared dir,    │
│  exits             │
└─────────┬──────────┘
          │ digest file landed
          ▼
┌────────────────────┐
│  Digest bridge     │
│  (small script)    │
│  reads file,       │
│  posts to shared   │
│  bus as broadcast  │
└─────────┬──────────┘
          │ broadcast message
          ▼
┌────────────────────┐
│  Central poller    │
│  routes to all     │
│  interactive agent │
│  channel queues    │
└────────────────────┘
```

This pattern:

- Uses the existing interactive-agent channel infrastructure as the delivery layer
- Requires no new always-on processes on the ephemeral agent's side
- Naturally respects the broadcast marker pattern (see `governance/CHANNEL_PROTOCOL.md` — "Broadcast Marker Pattern")
- Keeps ephemeral output auditable via the interactive agents' normal audit log pipeline

See `governance/DIGEST_BRIDGE_PROTOCOL.md` (Phase 3) for the full bridge spec.

---

## Lifecycle Phases

The six phases from `governance/LIFECYCLE.md` apply with specific deltas for ephemeral agents.

### HIRE

Standard hire cycle from the base lifecycle. Issue the next sequential Employee ID from `governance/ID_REGISTRY.md`. Note the role is ephemeral in the ID registry and personnel record.

The agent identity is created "on paper" at hire time but the first real session doesn't spawn until the first scheduled job fires.

### ONBOARD

**Ephemeral-specific differences:**

1. **No channel setup required.** Skip all the channel-related onboarding steps from `governance/lifecycle/INTERACTIVE_LIFECYCLE.md` — no per-role config, no port reservation, no launch wrapper alias, no dual-ACK canon reading (there are no bus-sourced events to ACK).
2. **Schedule definition.** Document the cron/schedule trigger in the personnel record: when does the job fire, how long does it run, what is the timeout, what is the expected output location.
3. **Job prompt / instruction template.** The agent's "session initialization" is effectively the job prompt passed to it at spawn time. Document the canonical prompt in the role definition so successor instances see the same prompt.
4. **Output location.** Document where the agent writes its output (e.g., `{shared_dir}/{role}/output/` in the reference implementation). The bridge script reads from this location.
5. **Digest format (if applicable).** If the agent's output is consumed by a digest bridge, document the expected format in `governance/bridges/DIGEST_FORMAT_TEMPLATE.md` (Phase 3) or equivalent.

**Read requirements** for ephemeral agents are a subset of the full onboarding read list — they read the role definition, the values file, the governance spec, and any domain-specific docs needed for the task, but they skip channel-related docs.

### OPERATE

**Ephemeral-specific operational model:**

1. **Execute one job per invocation.** Each cron fire spawns a new session. The agent reads its instructions, does the work, writes the output, and exits. There is no "waiting for more work" state.
2. **Write output to the shared directory.** The agent's output must land in the documented location before the session exits. If the agent fails to write before exit, the output is lost.
3. **Optionally trigger the digest bridge.** If the output is intended for the interactive fleet, the agent (or the scheduler) invokes the digest bridge script as a post-step. See `governance/DIGEST_BRIDGE_PROTOCOL.md`.
4. **No acknowledgments.** Ephemeral agents do not acknowledge events because they do not receive events. The dual-ACK canon does not apply.
5. **Log to output, not to a channel.** All audit trail comes from the output files and the scheduler's run history. There is no live audit log to append to.

### REVIEW

Standard review cycle from the base lifecycle, adapted for ephemeral agents:

1. **Output file review.** Review the agent's output files for the period. Are they well-formed? Are they on schedule? Are there gaps (missed runs)?
2. **Scheduler run history.** Consult the scheduler's job history (e.g., `cron.log`, systemd journal, or the scheduler's built-in history tool) for run duration, exit codes, and failures.
3. **Digest reception (if applicable).** If the agent's output flows through the digest bridge, check that the interactive fleet is receiving the digests and acknowledging them.

### TERMINATE

**Ephemeral-specific termination:**

1. **Disable the cron job / schedule.** Removal from the scheduler is the equivalent of "closing the session" for an ephemeral agent.
2. **Deprecate the agent identity.** Update the ID registry to mark the agent as terminated. Archive the personnel record and output history.
3. **Remove the digest bridge hookup (if applicable).** If the agent's output flowed through a bridge, remove the bridge invocation from the scheduler or script.
4. **Write the termination packet.** Same five-section structure as any other termination; the "exit interview" section will be written on the agent's behalf based on output history and failure logs (since there's no live session to interview).

### REHIRE

**Ephemeral-specific rehire:**

1. Issue a new Employee ID.
2. Update the scheduler configuration with the new agent identity (the scheduler may key jobs by agent ID).
3. Re-document the job prompt, schedule, and output location in the personnel record.
4. The successor reads all predecessor termination packets — same as any other rehire.

---

## Required Infrastructure

Ephemeral agents need a much smaller infrastructure footprint than interactive agents:

| Component | Purpose | Must exist before first ephemeral hire |
|---|---|---|
| Scheduler | Cron / systemd timer / Kubernetes CronJob / equivalent | Yes |
| Shared output directory | Where agents write their output files | Yes |
| Agent runtime (e.g., OpenClaw, Claude Agent SDK, custom runner) | What the scheduler invokes | Yes |
| Job prompt template library | Canonical prompts for each ephemeral role | Yes |
| Digest bridge (if applicable) | One-way push from output to message bus | Only if ephemeral output must reach interactive agents |
| Scheduler run history / log retention | For the REVIEW phase | Yes |

Notably absent from this list: central poller, per-role queue files, channel servers, launch wrapper, port table, dual-ACK canon. None of those apply to ephemeral agents.

---

## When to Use Ephemeral vs Interactive

| Scenario | Shape |
|---|---|
| Nightly data digest | Ephemeral |
| Scheduled health check | Ephemeral |
| Hourly external API scan | Ephemeral |
| Long-running development work under human supervision | Interactive |
| Tandem code review with Builder/Reviewer coordination | Interactive |
| On-demand verification pipeline triggered by human request | Interactive |
| Autonomous overnight audit that posts results to the fleet | Ephemeral (with digest bridge to reach interactive agents) |
| Responding to a human message in a shared bus channel | Interactive |

**Heuristic:** if the agent must respond to unpredictable events in real time, it is interactive. If it runs on a schedule and produces output, it is ephemeral.

---

## Concurrency and Job Overlap

Ephemeral agents typically do not overlap because their schedules are spread out. If job overlap is possible in your deployment:

- Ensure each concurrent job writes to a distinct output path (e.g., timestamped subdirectories) so runs don't collide.
- Ensure the scheduler enforces "one-at-a-time" semantics if the job is not re-entrant (e.g., using a lockfile or the scheduler's own serialization support).
- If two ephemeral agents of the same role must run concurrently, define distinct roles in the ID registry — same rule as interactive agents.

Ephemeral-to-ephemeral coordination (one agent's output triggering another's execution) is better done via file-based handoffs that the next cron fire picks up, not via channels. If you find yourself needing real-time ephemeral-to-ephemeral coordination, reconsider whether the agents should be ephemeral at all — they may belong on the interactive side.

---

## Reference Implementation

The Mise reference implementation runs 8 ephemeral agents on a Mac Mini via OpenClaw cron-triggered sessions. Each agent fires on a schedule (hourly, daily, or weekly), spawns, executes a single prompt, writes output to `~/mise-core/.openclaw-mini-{role}/output/`, and exits.

Two of those agents' output flows through the digest bridge (`scripts/push_digest_to_slack.sh`) which posts a `[[BROADCAST]]`-marked summary to the shared `#common-room` Slack channel. The central poller on the interactive-agent side routes the broadcast to every interactive agent channel queue, and each interactive agent receives it as a normal channel event.

The decision to keep the Mini fleet ephemeral (rather than porting the channel architecture to it) is documented in `docs/brain/041126__mini-fleet-channel-architecture-eval.md`. The core finding: channel servers with no consumers are theatre; the digest bridge is the correct asymmetric architecture.

Full Mise spec: `docs/brain/041126__mini-fleet-channel-architecture-eval.md`. OpenClaw runtime: `docs/brain/031926__campclaw-openclaw-mastery.md`. Digest bridge: `scripts/push_digest_to_slack.sh`.

None of Mise's specific platform choices (OpenClaw, macOS cron, Slack) are load-bearing for the ephemeral lifecycle pattern. Any scheduler + shared output location + (optional) bridge to a message bus works.

---

## Related Files

- `governance/LIFECYCLE.md` — Base lifecycle this file specializes.
- `governance/lifecycle/INTERACTIVE_LIFECYCLE.md` — The alternate lifecycle for long-running interactive agents.
- `governance/CHANNEL_PROTOCOL.md` — Channel architecture spec. Does NOT apply to ephemeral agents but useful context for the "why channels don't apply" section.
- `governance/DIGEST_BRIDGE_PROTOCOL.md` (Phase 3, if present) — One-way push from ephemeral output to the shared message bus.
- `governance/bridges/DIGEST_FORMAT_TEMPLATE.md` (Phase 3, if present) — Canonical digest format.

---

## Changelog

- **v1.0 (2026-04-11):** Initial specialization. Extracted from the Mise reference implementation's Mini fleet operation model and the explicit design decision in `docs/brain/041126__mini-fleet-channel-architecture-eval.md` to keep channels off the ephemeral side.
