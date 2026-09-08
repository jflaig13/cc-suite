# SPDX-License-Identifier: MPL-2.0
"""fleet_kernel.m3 — held autonomy-engine migration scaffolding (OIL-227).

Production M3 entry and pointer execution are fail-closed during the
``one-kernel-two-activations`` sequence.  The retained cognition prompt still
claims Company Scribe authority and its complete write surface is not governed
by the four-file legacy capture boundary.  Completing that migration belongs
to separately authorized Phase 4 Company Fleet work; none of this package's
internal migration evidence counts toward Mini Kernel or MacBook Scribe
activation.

Contract: cc_execs/memos/ccde/CCTO_design__20260710__m3-autonomy-engine-migration.md

§0 THE ONE LAW: REUSE, NEVER REWRITE. The production
scripts/run_autonomy_engine.sh path is now an immediate retirement fence.
Internal migration tests reuse the former implementation only through
scripts/internal/m3_autonomy_migration_scaffold.sh plus
scripts/autonomy_spend_envelope.py; they do not reimplement its arithmetic or
control flow in Python.

Scope of THIS commit (M3.1 only, per the design's own staging): the
production-candidate workflow SKELETON — config resolution, lease
fencing (M2's fleet_kernel.m2.lease), Objective/Run rows (M1's schemas),
the envelope cap-check invoked exactly as the legacy runner invokes it,
and CostRecord recording (Money-Effect Law: @DBOS.transaction(), effect
+ checkpoint in one commit). This skeleton does NOT yet invoke the real
engine's leg-1 cognition or leg-2 dispatch — that wiring is M3.2's job,
gated on resolving a structural-suppression question this build
surfaced (see the escalation memo) before any real engine invocation
happens from a kernel-driven path.
"""
