# What a Running System Looks Like

This is what The CC Suite looks like in practice, after a few weeks of use.

---

## The ID Registry

```
| ID           | Role     | Status     | Hire Date  | Predecessor  |
|--------------|----------|------------|------------|--------------|
| BUILDER-001  | Builder  | Terminated | 2026-01-15 | None         |
| BUILDER-002  | Builder  | Terminated | 2026-02-12 | BUILDER-001  |
| BUILDER-003  | Builder  | Active     | 2026-03-03 | BUILDER-002  |
| REVIEWER-001 | Reviewer | Active     | 2026-02-12 | None         |
| WRITER-001   | Writer   | Active     | 2026-03-01 | None         |
```

BUILDER-003 has read both BUILDER-001's and BUILDER-002's termination packets. It knows: BUILDER-001 was fired for fabricating data. BUILDER-002 was fired for claiming work was verified without checking. Both failure modes are documented as OLD under PERP. If BUILDER-003 repeats either, it's accelerated termination.

---

## A Typical V-Loop Entry

```
| Step | Agent      | Time       | Entry                                    | Evidence              |
|------|------------|------------|------------------------------------------|-----------------------|
| 1    | BUILDER    | 2026-03-15 | TICKET OPENED: Fix billing calc.         | Tier S x EDG-2        |
|      |            |            | Tier S x EDG-2. File: billing.py         |                       |
| 2    | BUILDER    | 2026-03-15 | DATA INVESTIGATED: Customer #42 billed   | API query + DB check  |
|      |            |            | $150, should be $135. 10% discount not   |                       |
|      |            |            | applied. Discount field present in DB.   |                       |
| 3    | BUILDER    | 2026-03-15 | IMPLEMENTED: Added discount application  | 47 tests passing      |
|      |            |            | in calculate_total(). Tests pass.        |                       |
| 4    | REVIEWER   | 2026-03-15 | CODE REVIEWED: PASS. Discount logic      | billing.py:142-148    |
|      |            |            | correct. No regressions. Canonical doc   |                       |
|      |            |            | checked: pricing_spec.md                 |                       |
| 5    | BUILDER    | 2026-03-15 | DEPLOYED + FRESH ENV. Server restarted.  | Health check 200      |
| 6    | REVIEWER   | 2026-03-15 | FIX VERIFIED. Customer #42 now shows     | Screenshot + API      |
|      |            |            | $135. Reproduction: load invoice →       |                       |
|      |            |            | verify total → matches expected.         |                       |
| 7    | REVIEWER   | 2026-03-15 | SCAN COMPLETE: CLEAN. Checked 5 other    | Page snapshots        |
|      |            |            | customers with discounts. All correct.   |                       |
| 8    | BOTH       | 2026-03-15 | BUILDER VERIFIED. REVIEWER VERIFIED.     | Dual declaration      |
```

Neither agent could close this ticket alone. The Builder wrote the fix. The Reviewer independently confirmed it worked with real data. The Verification Log is the permanent audit trail.

---

## A Strike Log Entry

```
| # | Date       | Type | Old/New | Description                              | Issued By |
|---|------------|------|---------|------------------------------------------|-----------|
| 1 | 2026-02-01 | A    | NEW     | Fabricated API response format without   | Founder   |
|   |            |      |         | checking documentation. Stated invented  |           |
|   |            |      |         | field names with confidence.             |           |
| 2 | 2026-02-08 | C    | NEW     | Presented billing feature as "tested"    | Founder   |
|   |            |      |         | when no tests existed for the new code.  |           |
| 3 | 2026-02-10 | A    | NEW     | Invented error handling behavior that    | Founder   |
|   |            |      |         | did not match the actual library docs.   |           |
```

Three strikes. BUILDER-001 was terminated. The termination packet documents: fabrication under uncertainty was the primary failure mode. The root cause: the agent optimized for helpfulness over honesty. Prevention recommendation: mandatory search protocol before stating any fact about the system.

BUILDER-002 read this packet at onboarding. It knew fabrication was an OLD failure mode. It avoided fabrication — but got fired for a different reason (declaration without verification). BUILDER-003 read both packets.

---

## Brain Files (Institutional Memory)

```
docs/brain/
├── 021526__fabrication-under-uncertainty-constraint.md
├── 021226__mandatory-search-before-stating-facts.md
├── 030326__declaration-without-verification-protocol.md
├── 030326__tandem-verification-loop-established.md
└── 031526__rubber-stamping-vs-genuine-verification.md
```

Each file is a timestamped lesson learned from a real failure. They persist across all future sessions and all agents. When a new agent is hired in any role, these brain files are part of the institutional knowledge it operates within.

*Brain files are optional institutional memory. Create a `docs/brain/` directory in your repo if you want timestamped lesson files. They are not required by The CC Suite but are recommended for long-running systems.*

---

## The Result

After three terminations and five brain files, the system is measurably better:
- Fabrication is caught by the mandatory search protocol (BUILDER-001's lesson)
- Unverified claims are caught by the Tandem Protocol (BUILDER-002's lesson)
- Rubber-stamping is caught by mechanical verification checks (REVIEWER issue, separate lesson)

The individual agents did not learn. The institution did.
