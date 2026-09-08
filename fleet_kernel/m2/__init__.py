# SPDX-License-Identifier: MPL-2.0
"""fleet_kernel.m2 — the M2 proving spike (OIL-227).

Falsifies (or confirms) D4's control-plane decision: Postgres-backed
durable execution via DBOS Transact, with heartbeat leases + monotonic
DB-issued fencing tokens built as first-class KERNEL components (D4's own
framing — this is the kernel doing its job on its weakest plane, not
hand-rolling a queue).

Zero contact with the running system: every workflow here is either the
REPLICATED autonomy hourly (a toy re-implementation, not the real engine)
or a SYNTHETIC payroll-shaped workflow (fake money, fake accounts).
"""
