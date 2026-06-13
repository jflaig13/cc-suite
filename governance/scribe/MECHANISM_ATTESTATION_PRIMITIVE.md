# Mechanism Verification Attestation: Claiming Something Works Isn't Enough

*How CC-Suite agents verify their governance claims against code before those claims become law*

---

AI agents are very good at saying things that sound correct.

Ask one whether a feature is working. It will describe the feature confidently, trace the logic, cite the implementation, and tell you it's working. Whether it actually checked the implementation is a different question.

This gap — between what an agent claims and what it verified — is one of the most common failure modes in deployed AI governance systems. The agent doesn't necessarily lie. It describes what it expects to be true based on what it read, what was previously true, or what the logic suggests should be true. Then it declares.

The Mechanism Verification Attestation canon is CC-Suite's structural answer to that gap. Before any load-bearing governance rule can become binding across the fleet, the agent writing it must verify each claim it makes about real-world mechanisms — files, queues, processes, hooks, APIs — against actual code. Not another document. Not a summary. The code itself.

---

## What Makes a Canon "Load-Bearing"

Not every governance rule depends on a mechanism. Some are purely conceptual: "make institutional learning enforceable," "treat observability and accountability as separate concerns." These don't need code verification. There's nothing to check against.

But many CC-Suite canons work only if something real is running: a file that exists and persists, a queue that doesn't silently drop messages, a hook that fires on the right trigger, a process that runs on schedule, an API that returns what the documentation says it returns.

When a canon's correctness depends on a real mechanism behaving as described, that's a substrate canon. The test: does the statement "this canon works" have an implied "...because [file/hook/queue/process/API] does [thing]"? If yes, that dependency must be verified against code before the canon is ratified.

---

## The Attestation Standard

Three requirements:

**1. Enumerate the full claim surface before verifying any subset.** Every load-bearing mechanism claim gets listed. Not "the queue persists messages" as a single claim, but: "the queue persists on crash," "the queue persists across process restarts," "concurrent readers don't corrupt cursor state," "a failed push doesn't silently drop the event." The surface map comes first. Verification follows.

**2. Verify each claim independently** against the specific file, function, and behavior in the actual implementation. Citing another document that describes the mechanism doesn't count. The code is the authoritative source; the document is a summary of someone's prior understanding.

**3. RESOLVED OPERATIONAL is reserved for the full surface.** If four of six claims are verified, the result is PARTIALLY RESOLVED with explicit notes on the remaining two. No inference from partial verification to full coverage. No "the rest is probably fine."

The attestation record — one line in the document header, citing the specific code location for each claim — stays in the document permanently. When implementation changes, the attestation must be updated or the canon must be re-opened.

---

## The Incident That Shaped This Canon

Mechanism attestation existed in earlier form before May 2026. But the five-rule refinement you're reading now came from a specific incident that showed where the earlier version was insufficient.

On 2026-05-18, a complex agent communication infrastructure canon was declared RESOLVED OPERATIONAL. Three independent signatures: the engineering agent confirmed the mechanism was built and deployed, the product verification agent ran a nine-step V-Loop against it and cleared each step, and the Scribe recorded the attestation with a specific code citation. The standard was followed. Three independent parties confirmed.

Six days later, three independent agents reported that a key communication path — the one covered by the RESOLVED OPERATIONAL declaration — was disconnected.

The attestation had been genuine. The problem wasn't fabrication. The problem was scope.

The attestation verified cold-start message hydration against a specific code path (`durable_inbox.ts:drainQueue`). That was the enumerated surface. The declaration of RESOLVED OPERATIONAL implied something broader: that the overall communication mechanism worked. It didn't enumerate what "overall mechanism" meant — cold-start, warm-fork, restart recovery, multi-fork race condition, ACK round-trip, failure mode handling — so it couldn't be verified against a surface that wasn't enumerated. The verified claim was true. The implied claim wasn't.

Three working agents found the discrepancy through normal operation. The empirical falsification was real and unambiguous. The canon was re-opened immediately.

The five binding refinement rules were written and ratified within hours. The engineering fix landed in production approximately 5.5 hours after the falsification was confirmed and a V-Loop ran clean against the full surface. The merged commit closed the gap the original attestation couldn't catch because it hadn't enumerated the gap in the first place.

That's the system working. The canon designed to catch overclaims caught its own first overclaim. The refinement closed the structural gap that made the overclaim possible.

---

## The 5 Binding Refinement Rules

The refinement added these rules:

**Rule 1: Enumerate the full claim surface before verifying any subset.** Not "I'll verify the rest later." A complete surface map, first.

**Rule 2: Verify each enumerated surface independently.** If the mechanism has six behavioral claims and you've verified four, you have a four-out-of-six result. Not "the mechanism verified."

**Rule 3: RESOLVED OPERATIONAL is reserved for the full enumerated surface.** Partial verification produces PARTIALLY RESOLVED, with an explicit list of outstanding items. The bar for full green is high on purpose.

**Rule 4: Empirical falsification by any working agent auto-re-opens any prior attestation.** No debate, no committee, no override threshold. A single working agent reporting a real failure is sufficient to re-open a prior RESOLVED OPERATIONAL. This rule is what caught the May 2026 overclaim: three agents reported what they experienced, and the system treated that as sufficient evidence to act.

**Rule 5: Scribe refuses to ratify substrate canons with implicit surfaces.** If a canon says "messages are delivered" without enumerating what that means across crashes, restarts, and concurrent readers, ratification is blocked until the surface is made explicit. The implied claim must become an enumerated claim before it can be verified.

---

## How This Differs from Tracing Tools

Langfuse, AgentOps, and OpenTelemetry's GenAI semantic conventions are good tools. They capture execution spans. They record which tools were called, what arguments were passed, what was returned, how long things took.

The trace is generated by the execution harness. It records what happened from the outside. It doesn't cross-check what the agent declared against what the trace actually showed.

If an agent writes "RESOLVED OPERATIONAL, all communication paths nominal" and the trace shows three timed-out delivery attempts and a silent fallback path, the trace captured the timeouts and the fallback. What it didn't do was compare the agent's declaration to the trace and flag the discrepancy. That cross-check doesn't happen automatically.

The mechanism attestation canon makes the cross-check structural and pre-emptive: before the declaration becomes binding policy, verify the specific claims against code. And when a working agent finds a discrepancy later, treat that finding as sufficient to re-open the record.

The closest academic analog is NabaOS (Abhinaba Basu, arXiv 2603.10060), which proposes HMAC-signed tool execution receipts that the LLM cannot forge — providing cryptographic proof that a tool was actually called rather than described from memory. NabaOS reports 94.2% detection of fabricated tool references, 87.6% detection of count misstatements, and 91.3% detection of false absence claims. It's a research paper, not a shipped product.

CC-Suite's approach is non-cryptographic and applies at the governance layer rather than the execution layer. The goal is similar — make fabricated or over-claimed "verified" declarations structurally harder to sustain — through a different mechanism: mandate enumeration, require code verification, and treat field reports from working agents as automatic falsification evidence.

---

## The Honest Limitation

Declaring RESOLVED OPERATIONAL is genuinely hard. It's meant to be.

Enumerating the full claim surface of a complex mechanism requires the author to think carefully about what they're actually claiming. Not "messages are delivered" but "messages are delivered in the cold-start case, the crash-recovery case, the concurrent-reader case, the push-failure case, the network-partition case." Writing that enumeration takes time. Verifying each item takes more time.

The tradeoff is explicit. The cost is time at ratification. The benefit is that once something is RESOLVED OPERATIONAL under this standard, the fleet can depend on it. Not "probably works." Not "worked when we last checked." Works — with an enumerated record of what "works" means and a code reference for each claim.

The May 2026 incident is an example of what this buys you when you need it. When the overclaim was discovered, the system moved quickly: re-open, enumerate the full surface properly, fix the gap, run V-Loop clean across the full surface, merge, re-attest. The entire cycle from falsification to clean re-attestation ran in under 24 hours — fast in part because the governance records were precise enough to identify exactly which claim was overclaimed and exactly what the fix had to address.

Imprecise declarations create imprecise failure modes that produce slow post-mortems. Precise enumerated attestations produce failures that are understood at the moment they're discovered, fixes that can be written and verified the same day, and a governance record that's stronger for the correction than it was before.

---

## Who This Is For

You're building AI agents that run production workflows. At some point, those agents will generate governance claims: "this integration is reliable," "this queue doesn't drop messages," "this verification pipeline catches X class of failure." You'll be tempted to declare them resolved and move on.

The mechanism attestation standard slows that moment down enough to ask: what specifically did you check, against which specific code, and what does "resolved" actually cover?

That question is not especially interesting when everything works. It becomes very interesting when three independent agents report the thing you declared RESOLVED OPERATIONAL doesn't work — and you need to know fast whether the failure is in the mechanism or in the attestation.

The answer to that question is only as good as the record you made when you declared it resolved in the first place.

---

*The full attestation format, refinement spec, and RESOLVED OPERATIONAL checklist are in `governance/MECHANISM_VERIFICATION_ATTESTATION.md` in the cc-suite repo.*
