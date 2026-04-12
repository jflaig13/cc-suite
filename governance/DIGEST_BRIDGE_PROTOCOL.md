# Digest Bridge Protocol

**Status:** Active (CC-Suite™ v2)
**Scope:** Cross-lifecycle communication from ephemeral batch agents to long-running interactive agents
**Related:** `CHANNEL_PROTOCOL.md`, `lifecycle/EPHEMERAL_LIFECYCLE.md`, `lifecycle/INTERACTIVE_LIFECYCLE.md`

---

## Purpose

In a mixed fleet with both long-running interactive agents and ephemeral batch-triggered agents, the batch agents produce output that the interactive agents need to see — but there's no shared persistent process to push events to (ephemeral agents die after each run). The **Digest Bridge Protocol** solves this: batch agents write structured digest files to a shared location, and a bridge helper reads those files and posts them to the message bus where the channel architecture fans them out to interactive agents.

One-way push. Batch workers talk to interactive agents via the same bus everything else uses.

---

## When to Use It

Use the Digest Bridge Protocol when ALL of these apply:

1. You have at least one ephemeral/batch agent producing output on a schedule
2. You have at least one long-running interactive agent that needs to receive that output
3. The two don't share a persistent process or memory
4. You already have a channel architecture in place (`CHANNEL_PROTOCOL.md`)
5. You want the output delivery to be the same mechanism as human-to-agent messaging

**Do NOT use the bridge when:**

- Both sides are long-running → use channels directly
- Both sides are ephemeral → use file-based handoffs between cron runs
- You need bidirectional push → ephemeral agents can't receive pushes (no consumer). Different design required.
- The output is large/binary → the bus (Slack, Discord, etc.) has size limits. Store artifacts separately and post a pointer.

---

## The Pattern

```
Ephemeral Agent (cron-triggered)
  │
  │  Spawns, executes its prompt, produces output
  │
  ▼
Output file in shared directory
  (e.g., ~/.shared/digest-latest.md)
  │
  ▼
Bridge helper script (runs as final step of the cron prompt)
  │
  │  Reads the output file
  │  Adds the [[BROADCAST]] marker (from CHANNEL_PROTOCOL.md)
  │  Posts to the message bus (Slack / Discord / custom)
  │
  ▼
Central poller (from CHANNEL_PROTOCOL.md)
  │
  │  Picks up the message on the next poll cycle
  │  Routes to all consumer queues (broadcast)
  │
  ▼
Per-role channel servers
  │
  │  Drain queues, push events to each interactive agent session
  │
  ▼
Interactive agents receive the digest as a channel event
```

---

## Required Elements

### 1. The shared output directory

A filesystem location both the ephemeral agent AND the bridge helper can read. Examples:

- `~/.shared/` on a single machine where both agents live
- A networked drive or SMB/NFS mount for multi-machine deployments
- A GCS/S3 bucket with sync (more complex, but platform-agnostic)

**Requirement:** the ephemeral agent writes atomically (write to `.tmp`, rename on success) so the bridge doesn't read partial content.

### 2. The digest format

Every digest MUST follow a structured format. See `bridges/DIGEST_FORMAT_TEMPLATE.md` for the full template. Minimum requirements:

- **Metadata header** — timestamp, source agent, scan window (if applicable)
- **Structured sections** — not free-form prose; use headings and tables
- **Verdict or summary line** — one-line TL;DR at the top for preview
- **Source-of-truth pointers** — if the digest is a summary, point at the full data

### 3. The bridge helper

A script that:

1. Takes a digest file path as input
2. Reads the digest
3. Adds the `[[BROADCAST]]` marker (so the poller fans it out to all consumers, not just name-matched roles)
4. Truncates to the message bus's size limit if needed, with a "(truncated — see full file at {path})" note
5. Posts to the bus via whatever API the bus uses (Slack Web API, Discord webhook, HTTP POST, etc.)
6. Returns non-zero exit code on failure

The bridge helper is **platform-specific** — CC-Suite users implement it for whatever bus they're using. See `bridges/BRIDGE_SCRIPT_TEMPLATE.md` for a generic skeleton (to be written).

### 4. The invocation

The ephemeral agent's cron prompt (or equivalent) invokes the bridge helper as its FINAL step:

```
# at the end of the agent's work
bash $REPO/scripts/bridge_helper.sh $OUTPUT_FILE "Human-Readable Label"
```

If the bridge fails, the agent logs the error. The digest file is still preserved (it's the source of truth); the bridge is just the delivery mechanism.

---

## The Broadcast Marker

Digests use the `[[BROADCAST]]` marker from `CHANNEL_PROTOCOL.md` to force fleet-wide fan-out. Why:

- Digest content often mentions specific agents by name (e.g., "CCTO-001 at 3/3 strikes")
- Without the marker, the poller's role-matching regex would route the digest ONLY to those mentioned agents
- But a digest is a fleet-wide briefing — every interactive agent should see it
- The `[[BROADCAST]]` marker overrides role matching and sends to all consumers

The bridge helper prepends the marker automatically. The digest writer does not need to include it.

---

## Dual-ACK Canon Compliance

Digest events delivered via the bridge are **Slack-sourced** (or bus-sourced equivalently) from the perspective of the receiving interactive agents. They arrive at each agent with `slack_ts` metadata populated (the bus's message timestamp).

**Therefore:** Receiving agents MUST acknowledge digest events via the dual-ACK canon (see `CHANNEL_PROTOCOL.md` section "Dual-ACK requirement" and the dedicated canon document).

- Internal ACK via the agent's reply tool
- External ACK via a reply post back to the bus channel

This means 10 agents receiving one daily digest will generate 10 dual-ACK replies. That's acceptable and expected — it proves every agent received the digest, and the founder can see at a glance if any agent missed it (silent = broken).

---

## Failure Modes

1. **Bridge helper can't reach the bus** — network error, expired token, bus outage. Recovery: log error, keep the digest file (it's preserved for next run), alert via secondary channel if possible.

2. **Ephemeral agent writes partial digest, bridge reads it mid-write** — race condition. Prevention: atomic write pattern (write to `.tmp`, rename on success).

3. **Digest exceeds bus size limit** — e.g., Slack's 3000-char block limit. Prevention: bridge helper truncates with a "see full file" pointer. Interactive agents can fetch the full file if the shared directory is accessible to them; otherwise they work from the truncated preview.

4. **Bus fan-out fails (poller down)** — digest lands in bus but never routes to consumers. Recovery: poller's heartbeat monitoring (see `CHANNEL_PROTOCOL.md`) catches this. Bridge can't recover directly; the channel architecture's rollback path is the recovery.

5. **Interactive agent never dual-ACKs the digest** — Scribe audit flags missing ACKs per the dual-ACK canon. Recovery: the agent that missed it gets a notification or strike per canon enforcement.

---

## Reference Implementation

Mise uses the digest bridge pattern for its Mac Mini fleet. The Mini's two daily cron jobs (`harness-health-digest` at 06:30 ET, `mini-scribe-daily-digest` at 06:00 ET) write digest files to `~/.openclaw-shared/` and invoke `scripts/push_digest_to_slack.sh` as their final step. The script posts to `#common-room` on the Slack workspace; the MacBook's Slack poller (`channels/shared/slack_poller.ts`) routes to all 10 MacBook agent channels; each agent receives the digest as a `<channel source="{role}-channel" ...>` system reminder. Verified end-to-end on 2026-04-10 evening.

Full Mise spec: `docs/brain/041026__channel-architecture-rollout.md` + `docs/brain/041126__cc-suite-v2-scope.md` Capability 5.

---

## CHANGELOG

- **v1.0 (2026-04-11):** Protocol established as part of CC-Suite™ v2 extraction from Mise's live implementation.
