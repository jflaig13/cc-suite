// advisor_ask_detect.ts — B3 (CCTO amendment A3, OIL-221 north-star trial):
// detects an ADVISOR ask so the handoff-ledger auto-open gate (G-c) can fire
// for it, not just for event_type=task_handoff.
//
// The Plan-First advisor protocol (cc_execs/memos/ccde/CCTO_design__
// 20260708__plan-first-mechanism.md §3) sends an advisor consult as
// event_type=agent_direct with SUBJECT prefix "ADVISOR:" — deliberately NOT
// task_handoff, since it's a pull (a question) not a push (a directive).
// That meant it never got the stall-backstop's protection: CCDE's own
// branch-reachability advisor ask reached CCTO's queue durably (receipt-or-
// loud held at the transport layer) but never surfaced into a live-but-busy
// window — receipt-to-queue != receipt-to-attention. This predicate closes
// that gap by giving the handoff-row-open gate a second, precise trigger.

const ADVISOR_SUBJECT_RE = /^SUBJECT:\s*ADVISOR:/m;

export function isAdvisorAsk(eventType: string, content: string): boolean {
  return eventType === "agent_direct" && ADVISOR_SUBJECT_RE.test(content || "");
}
