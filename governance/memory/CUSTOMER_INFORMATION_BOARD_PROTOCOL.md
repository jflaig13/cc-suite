# Customer Information Board Protocol

A single canonical board for every warm customer relationship. The commercial-pipeline layer, complementary to the People Graph's relationship layer. One board, read by every agent at session start, never mixed up.

---

## Concept

A multi-agent fleet that sells anything accumulates customer facts in a dozen places: a call gets logged in one agent's handoff, a pricing detail in another's memo, a stage change in a third's announcement. The facts drift apart. Two agents end up holding two different versions of where a relationship stands, and a customer-facing message goes out built on the stale one.

The Customer Information Board (CIB) solves that the way an institution solves any source-of-truth problem: one canonical document, everyone reads it, everyone updates it, nobody keeps a private copy. The board holds every customer the fleet is in a warm relationship with: who they are, what they're buying, where the relationship stands right now, and a dated log of every interaction.

This is the commercial counterpart to the People Graph. The People Graph tracks the relationship (who someone is, how you met, the running interaction log). The CIB tracks the pipeline (what stage the deal is at, what's been provisioned, what the next step is). A single contact can appear in both; they answer different questions.

---

## What Goes On The Board

One record per customer. Each record carries, at minimum:

| Field | Purpose |
|---|---|
| `display_name` | What you call them in conversation |
| `account_id` | The technical key (tenant ID, account slug), distinct from the display name |
| `stage` | Where the relationship stands (see Stage Vocabulary) |
| `temperature` | How warm and active the relationship is right now |
| `contacts` | Who you talk to, and the relationship context |
| `product_scope` | What they're adopting |
| `account_status` | What's been provisioned and what access they have |
| `pricing` | Commercial terms, if framed |
| `key_dates` | The dates that matter (next meeting, go-live, decision) |
| `next_step` | The one specific next action |
| `update_log` | Append-only, dated: every fact as it's learned |

Add fields as a customer needs them. The schema is a floor, not a ceiling.

---

## The Two Load-Bearing Rules

**Never mix one customer up with another.** Every record lists both the display name and the technical account key, precisely so the two can never be confused. The friendly name a human uses and the system key a tenant is stored under are different strings, and conflating them is how one customer's data lands on another's record. Before editing a record, confirm the account key you are editing. Cross-customer field bleed (putting one customer's fact on another's record) is a data-integrity error of the same class as serving one tenant's data to another.

**Read it on init.** Every agent reads the board at session start and knows the current state of every relevant relationship before doing any customer-facing work: outreach, onboarding, a follow-up, a pricing conversation. An agent acting on a stale or half-remembered customer fact is the exact failure this board exists to prevent.

---

## Stage Vocabulary

The board uses one fixed set of stage words, lowest to highest. Fixed vocabulary matters: "doing well" means nothing, "verbally agreed, unsigned" means something exact, and the gap between them is the difference between a forecast and a fact.

`interested` → `demoed` → `account-built` → `trial-scheduled` → `trial-run-done` → `verbal-commitment` → `signed` → `paid`

Adapt the words to your sales motion. The discipline is that there IS a fixed ladder, and that nobody narrates a relationship as further along than its stage word allows. Language honesty is the rule: a verbal yes is not a signature, and the board never writes it as one.

---

## Entry Criterion

A customer goes on the board the moment they express genuine interest: the first real "yes, tell me more" on a call, not a cold name on a prospect list. Cold prospects live in a separate outreach pipeline; the CIB is the warm-and-beyond board. The interest is the trigger, the same way a real interaction is the trigger for a People Graph file.

This keeps the board dense. Everything on it is a live relationship worth knowing cold. A board padded with cold names is a board nobody reads.

---

## Update Discipline

The board is rolling, not a periodic report. Any agent who learns a customer fact (a call, a date, a stage change, an integration detail, a shift in sentiment) updates the record AND appends a dated line to its update log, in the same action. A known-but-unrecorded fact is a defect.

Never delete a record. A customer who goes cold or is lost gets marked as such, with a dated reason. The history is part of the truth.

---

## Sensitivity

The board holds personal and commercial detail: who someone is, what they pay, where a deal stands. It is internal-only. It never goes in a public repository, marketing copy, or any external surface. The pattern is open; the board is not. This document describes the pattern, so the implementing fleet's actual board stays private.

---

## Ownership

Every agent updates the board in its lane: whoever learns the fact records it. The relationship-facing roles (growth, customer success) are the primary update lanes. The Scribe audits the board for cross-customer bleed, stale records, and stage-vocabulary and language-honesty compliance. Auditing the source of truth is part of keeping it true.

---

## Origin

This pattern emerged in the Mise reference implementation after customer facts had scattered across handoffs, memos, and announcements, and two agents briefly held different versions of the same relationship's status. The fix was a single canonical board with read-on-init and never-mix-up as hard rules. It pairs with the People Graph (relationship memory) and the cold-outreach pipeline (pre-interest prospects) as the third leg of a fleet's relationship memory: the warm commercial pipeline, kept honest.
