# Monitoring-Chain Integrity Protocol

**Status:** Active (CC-Suite™ v2)
**Scope:** Multi-host agent fleets with cross-host sensors
**Related:** `ATOMIC_VERIFICATION_PROTOCOL.md`, `CHANNEL_PROTOCOL.md`, `DIGEST_BRIDGE_PROTOCOL.md`

---

## Purpose

Prevent the class of bug where a sensor running on host A reports on a subject owned by host B using host A's stale local view instead of B's ground truth. In multi-host agent fleets this is a common, subtle, and destructive failure mode — sensors produce outputs that look real, are evaluated as real by downstream analysts, and trigger responses to problems that don't exist.

This protocol defines a single general rule, a corollary about the consumer chain, and a reference pattern (shared-state files) for bridging cross-host observation safely.

---

## The Core Rule

> **"The host that owns the observed subject is authoritative for that subject. Cross-host sensors: when host A reports on a subject owned by host B, the ground-truth check must query host B directly. Shared-state files — written by the owning host and read by the observing host — are an acceptable bridge."**

If host A cannot query host B and there is no shared-state bridge, host A must NOT sense B's state. Period.

## The Corollary

> **"The analyst who reads the monitor is part of the monitoring chain."**

Sensor reliability is not just about the sensor. Every layer that consumes sensor output is a potential failure mode. A downstream agent that reads a digest flag and escalates it without verifying the sensor that produced it is themselves part of the chain. "Trust but verify" is not optional at any layer.

---

## The Failure Class

Two observable properties define the class:

1. **The sensor runs on host A.**
2. **The subject it reports on is owned by host B, where B ≠ A.**

When both are true AND the sensor uses A's local view of B's state instead of (a) querying B directly or (b) reading a shared-state file written by B, the sensor reports a **hallucination**: a value that correlates with A's history but has no reliable relationship to B's current state.

Typical symptoms:

- Reports that look real but don't match direct observation of B
- Reports that stay the same for weeks even when B's state changes
- Reports that diverge across identical sensors on different hosts
- Reports that persist even when the sensor is supposed to track live state

---

## Subject Ownership

Every governed subject must have a declared authoritative host. The ownership table is a core deliverable of any multi-host CC-Suite™ deployment. Example categories:

| Category | Example owner | Example bridge |
|---|---|---|
| **Source control state** (branches, HEAD, remote refs) | Development host with network access to origin | Shared file with commit hash + push timestamp |
| **Deployment state** (live services, rollout status) | Host that queries the deployment API | Shared file with service revision + deploy time |
| **Process liveness** (daemons, pollers, schedulers) | Host the process runs on | Heartbeat file + health-check endpoint |
| **Filesystem state** (files, directories, sizes) | Host the filesystem belongs to | Direct query or shared manifest |
| **Scheduled-job state** (cron, launchd, systemd) | Host the scheduler runs on | Job-log file or direct query |
| **External service state** (third-party APIs, message buses) | No host owns it — the external service is the authority | Cached response with TTL; fallback to direct query |

**Every new sensor script must include a declared subject and an authoritative host.** The Scribe (or an equivalent audit role) reviews this declaration before the script ships.

---

## The Shared-State File Bridge Pattern

When direct cross-host query is impractical (latency, auth, network boundaries, rate limits), the acceptable pattern is a shared-state file: the owning host writes, the observing host reads.

### Properties of a valid bridge file

1. **Single writer.** Only the authoritative host writes. Enforced by filesystem permissions or process ownership.
2. **Many readers.** Any observing host may read.
3. **Timestamped.** Every write includes the wall-clock time the state was captured.
4. **Atomic writes.** Write to `.tmp` → `rename` to final path. Prevents partial reads.
5. **Shared filesystem access.** The file lives in a location both hosts can read (local filesystem on single host, NFS/SMB in a networked fleet, cloud storage with sync for distributed fleets).
6. **Graceful staleness reporting.** Readers check the timestamp and report "data stale, last update T ago" when it exceeds a threshold. Never silently trust a cached value.

### Canonical example

```
Host A (authoritative for "git state"):
  1. On every git commit, a post-commit hook writes ~/shared/git_state.json:
     {
       "head": "2c13478...",
       "origin_main": "2c13478...",
       "unpushed_count": 0,
       "last_push_utc": "2026-04-10T23:40:02Z",
       "captured_utc": "2026-04-11T14:30:00Z"
     }

Host B (observer):
  1. Bible audit reads ~/shared/git_state.json
  2. If captured_utc is > 1 hour old, report as STALE with the age
  3. Otherwise, report unpushed_count directly from the file
  4. Never runs `git log origin/main..HEAD` on B's local refs
```

Host B stops being wrong about git state because host B stops *sensing* git state. It reads host A's authoritative report.

---

## Corollaries

Two refinements emerged during the rule's ratification that are useful to formalize as corollaries.

### 1. Self-Echo Exemption

When an agent's own output comes back to its own input channel (via a message bus with broadcast/routing), the agent's internal acknowledgment is sufficient. No dual-ACK or deep audit required. The sensor (the message bus) is reporting on a subject (the message) authored by the receiving agent itself — there is nothing to verify.

This corollary is specifically important for the `CHANNEL_PROTOCOL.md` dual-ACK requirement: agents do not need to dual-ACK their own echoes.

### 2. Analyst-Is-Part-Of-The-Chain

Any agent or human that reads a sensor output and takes action based on it is themselves part of the monitoring chain. If the upstream sensor is unreliable, downstream actions based on its output are also unreliable. When reading a report:

1. Note the sensor that produced it
2. Note the authoritative host for the subject
3. If the sensor doesn't own the subject, verify through a direct query or shared-state file before escalating
4. If verification fails or is impossible, flag the uncertainty — don't silently trust the reading

---

## Audit Obligations

The audit role (Scribe or equivalent) is responsible for reviewing new sensors against this protocol.

### Review checklist

For every new sensor script, cron job, digest generator, or metric pipeline:

1. **What is the subject?** (one phrase: "git state," "disk usage," "agent strike count," "deployment revision," etc.)
2. **What host is authoritative for that subject?** (from the subject-owner table)
3. **What host is the sensor running on?**
4. **If sensor host ≠ authoritative host**, verify one of:
   - Sensor queries the authoritative host directly (HTTP call, SSH query, API call)
   - Sensor reads a shared-state file written by the authoritative host
   - The sensor has explicit "stale data is OK here because..." reasoning documented inline and the reasoning is sound
5. **If none of the above**, flag the sensor as violating this protocol. Scripts in production that violate this protocol become fix candidates. The fix is usually small: either add a direct query or introduce a shared-state bridge.

### Historical precedents

Document each confirmed instance of the failure class in the deployment's historical record. Future reviewers will reference these. Patterns matter more than specific bugs.

---

## Reference Implementation

Mise's concrete implementation of this protocol lives at `docs/brain/041126__monitoring-chain-integrity.md` in the `mise-core` repository. That brain file contains the Mise-specific subject-owner mapping (MacBook vs. Mini fleet), three historical failure examples (Mini git drift, `scan_strikes()` surfacing terminated agents, Mini self-ping in isolated OpenClaw sessions), and the concrete Scribe audit checklist adapted to Mise's toolchain.

The rule was ratified on 2026-04-11 after a multi-exec consensus thread triggered by `harness_digest.py` defect investigation. Credits: CCTO-004 (core rule formulation), CCPO-003 (analyst-is-part-of-chain corollary), CCRO-001 (self-correction incident that surfaced the failure class + self-echo exemption framing).

---

## Changelog

- **v1.0 (2026-04-11):** Protocol established as part of CC-Suite™ v2. Generalized from Mise's live reference implementation. Extracted core rule from exec consensus, documented failure class, codified shared-state bridge pattern, added corollaries (self-echo exemption, analyst-is-part-of-chain), defined audit obligations.
