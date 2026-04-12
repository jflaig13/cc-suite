# Channel Definition Template — {Role Name}

**Status:** {Active / Proposed / Deprecated}
**Authority:** Layer 6 (Governance Spec) — subordinate to `governance/CHANNEL_PROTOCOL.md`
**Scope:** Defines the channel configuration for a single role.

Use this template to add a new channel for a specific role in your fleet. Copy this file to `governance/channels/{role-name}.md` (or equivalent), fill in the placeholders, and submit it with the role definition and port table update.

---

## Role

**Role name:** `{role-name}` (lowercase, alphanumeric, used in the reply tool name and queue filename)
**Agent ID prefix:** `{ROLE}-` (uppercase, used in the employee ID per `governance/ID_REGISTRY.md`)
**Role definition:** `governance/roles/{role-name}.md`

---

## Port Allocation

**Listen port:** `{NNNN}` (unique per role — see your deployment's port table)
**Binding:** `127.0.0.1:{NNNN}` (localhost only; never expose to the network)
**Health endpoint:** `http://127.0.0.1:{NNNN}/health`

Update your deployment's port table (e.g., `channels/PORTS.md`) with this allocation.

---

## Message Types Handled

List every event type this channel accepts. For each type, state the origin (human via bus, code hook, or direct POST) and whether a typed verdict is required.

| Event type | Origin | Verdict required? | Notes |
|---|---|---|---|
| `slack_message` | Human (bus) | No — informational | Default for all bus-sourced messages routed to this role |
| `message` | Any | No — informational | Generic direct POST |
| `{typed_event_1}` | {origin} | Yes — see below | {short description} |
| `{typed_event_2}` | {origin} | Yes — see below | {short description} |

For typed events, every channel in the fleet SHOULD reference the same canonical event-type registry to avoid drift. See `governance/scribe/EVENT_TYPES.md`.

---

## Required Verdict Phrases

For each typed event, list the verdict patterns the reply tool will enforce. Replies that do not match one of these patterns will be REJECTED by the channel server.

| Event type | Required verdict phrase (any one) |
|---|---|
| `{typed_event_1}` | `{PHRASE_A}` OR `{PHRASE_B}` OR `{PHRASE_C}` |
| `{typed_event_2}` | `{PHRASE_D}` OR `{PHRASE_E}` |

Untyped events (`slack_message`, `message`, informational hook events) accept any reply text.

Register these patterns in your implementation's equivalent of the Mise `VERDICT_PATTERNS` map (see `channels/scribe/webhook.ts`). Keep this table in sync with the implementation — drift is a failure mode.

---

## Audit Requirements

- **Audit log destination:** `{path-to-audit-log}` — append-only JSONL (or equivalent), one entry per accepted verdict.
- **Audit retention:** Permanent. Never truncated, never rewritten.
- **Audit fields:** `event_id`, `event_type`, `content`, `verdict`, `timestamp_event`, `timestamp_ack`.
- **Dual-ACK audit:** For bus-sourced events, the Scribe cross-references audit log entries against bus-side channel history to verify the external ACK landed. See `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md`.

---

## Launch

**Wrapper alias:** `{alias}` — invokes the deployment's launch wrapper with the role parameter.

**Example launch command (platform-specific):**

```
{launch-command-line}
```

The wrapper MUST:

1. Validate that the per-role config file exists
2. Pre-flight the port and clean up any orphaned subprocesses
3. Pass any required dev-mode flag to the agent runtime
4. Export environment variables the channel server needs (e.g., `AGENT_ROLE`, `AGENT_PORT`)
5. Spawn the agent runtime

See `governance/CHANNEL_PROTOCOL.md` — Agent Launch Requirements.

---

## Rollback

**Rollback procedure:**

1. Close the affected agent session(s).
2. Stop the channel subprocess: `{kill-command}`
3. Remove the role from the poller's routing table (so new messages do not accumulate in the dead queue).
4. Delete or archive the queue file if it is no longer needed.
5. If rolling back to polling, restore the polling loop in the role's init file.

**Full fleet rollback:** See `scripts/rollback_push_architecture.sh` in the reference implementation or your deployment's equivalent.

---

## Example — Filled In for a Hypothetical "Builder" Channel

As a concrete example, here is a filled-in version of this template for a hypothetical `builder` role in a deployment that uses Slack as the bus and Claude Code as the agent runtime.

**Role name:** `builder`
**Agent ID prefix:** `BUILDER-`
**Role definition:** `governance/roles/builder.md`

**Listen port:** `8801`
**Binding:** `127.0.0.1:8801`
**Health endpoint:** `http://127.0.0.1:8801/health`

**Message types handled:**

| Event type | Origin | Verdict required? | Notes |
|---|---|---|---|
| `slack_message` | Human (Slack) | No | Routed by regex `\bbuilder\b` or broadcast fall-through |
| `message` | Direct POST | No | From scripts and hooks |
| `ticket_assigned` | Human (Slack) or hook | Yes | A new work ticket from the human authority or upstream agent |
| `review_blocked` | Reviewer agent | Yes | The reviewer returned a BLOCK verdict on this builder's work |

**Required verdict phrases:**

| Event type | Required verdict phrase (any one) |
|---|---|
| `ticket_assigned` | `TICKET ACCEPTED` OR `TICKET REJECTED` OR `TICKET QUEUED` |
| `review_blocked` | `BLOCK ACKNOWLEDGED` OR `BLOCK DISPUTED` |

**Audit log destination:** `channels/builder/audit_log.jsonl`

**Launch wrapper alias:** `builder`

**Example launch command:**

```bash
./scripts/launch_agent.sh builder
# Which internally invokes:
# AGENT_ROLE=builder AGENT_PORT=8801 \
#   claude --mcp-config channels/configs/role-builder.mcp.json \
#          --dangerously-load-development-channels server:builder-channel
```

**Rollback procedure:**

1. Close the builder agent session.
2. `pkill -f "AGENT_ROLE=builder"`
3. Remove `builder` from `ROLE_PATTERNS` in `channels/shared/slack_poller.ts`.
4. Archive `channels/shared/queue_builder.jsonl` if no longer needed.
5. If rolling back builder work entirely, restore a polling loop in the builder's init skill.

---

## Related Files

- `governance/CHANNEL_PROTOCOL.md` — The abstract protocol this template instantiates.
- `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md` — Scribe-specific channel with typed-event audit enforcement.
- `governance/scribe/EVENT_TYPES.md` — Canonical event type registry.
- `governance/roles/{role-name}.md` — Role definition for the agent consuming this channel.
- Your deployment's port table (e.g., `channels/PORTS.md`).
