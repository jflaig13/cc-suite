# Monitoring-chain integrity

Observe a subject through the system that owns its state. A development checkout cannot prove the deployed revision; an old local log cannot prove another host is healthy. External services are authoritative for their own delivery and execution results.

For each monitor, record its subject, owner, observation method, actor permissions, source revision, capture time, configured freshness requirement, failure destination and material consumers. Verify the monitor's own liveness before relying on silence as a result.

A shared-state bridge is valid when the owning system is its single writer, updates are atomic, consumers can verify origin and revision, and freshness is reported honestly. The bridge remains a read-only projection. Its content does not authorize another host to mutate the source.

When a direct observation fails, report unknown or stale with the last successful evidence and the actual error. Do not silently substitute another tenant, cached success, fabricated zero or a healthy default. The failure must be observable without unnecessarily breaking an unrelated authorized customer action.

Scheduling, timeout and freshness parameters are explicit owner-approved configuration. This protocol supplies no universal intervals. A configured fallback records each use and preserves the primary operation's authority and idempotency.

Every consumer is part of the monitoring chain. Before acting on an alert, check that the sensor observed the intended subject and that the evidence still supports the proposed action. An acknowledgment proves receipt; a completion receipt must prove the requested outcome.
