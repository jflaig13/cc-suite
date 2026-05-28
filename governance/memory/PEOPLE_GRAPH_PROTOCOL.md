# People Graph Protocol

Structured per-person relationship records. One file per person. Relationship layer only — separate from any commercial pipeline.

---

## Concept

For every person the founder (or a designated agent) has a direct interaction with, create one markdown file. Store it in a designated people directory (e.g., `docs/people/`).

The file captures who they are, why they matter, what you have in common, who connected you, and a running log of every interaction. This is not a CRM. It's a memory aid with structure.

---

## Template Fields

Every person file uses the same frontmatter:

| Field | Purpose |
|-------|---------|
| `name` | Full name |
| `role` | Title or function |
| `org` | Company or platform |
| `type` | Relationship category (see types below) |
| `connection_strength` | `warm` / `cool` / `cold` |
| `how_we_met` | One sentence |
| `first_contact` | ISO date of first real interaction |
| `last_touch` | ISO date of most recent interaction |
| `next_action` | One specific next step, or "none pending" |
| `related_brain_files` | Brain files or docs cross-referencing this person |

**Relationship types:** `operator`, `investor`, `peer_founder`, `media`, `advisor`, `connector`, `strategic`

**Connection strength:** `warm` (real conversation, mutual interest established) / `cool` (connected but not yet engaged) / `cold` (no contact yet or very minimal)

The body of each file has four sections: **Context** (who they are, why they matter), **Common Ground** (shared experiences, interests, mutual connections), **Referral Graph** (introduced_by / could_introduce_to / mutual_connections), and **Interaction Log** (append-only table).

---

## Core Principles

**One file per person.** If the same person appears in multiple contexts (prospect, advisor, and connector), that's all in one file.

**Append-only interaction log.** The log at the bottom of each file records every interaction as a new row. Never edit a past row. The log is the honest record of what happened.

**Relationship layer is separate from commercial pipeline.** A pipeline file tracks conversion: status, call prep, deal progression. A people file tracks the relationship. They can coexist for the same person and serve different purposes.

**Lazy loading.** The CC Exec responsible for external relationships loads people files on demand — when preparing for a call, drafting an outreach message, or doing a follow-up. Not bulk-loaded at init.

**`related_brain_files` creates lightweight cross-referencing.** Link each person file to the doctrine or context files relevant to working with them. This surfaces relevant canons without requiring a database.

---

## Referral Graph

Each person file includes a Referral Graph section:

- `introduced_by` — who connected you to this person
- `could_introduce_to` — who this person might connect you to
- `mutual_connections` — shared contacts

Scanning a handful of people files before an outreach pass surfaces more context than starting cold. The value compounds as the graph grows.

---

## When to Create a File

Create a file when the founder (or designated agent) has a direct interaction:

- Sent a DM or email
- Had a call or in-person meeting
- Had a meaningful async exchange (comment thread, direct reply)

Do not create speculative files for people who haven't been touched yet. The interaction is the trigger.

---

## File Naming

Use kebab-case of the person's full name: `garry-tan.md`, `aaron-aday.md`. Keep a `_template.md` (leading underscore) in the same directory for reference.

---

## Origin

This pattern adapts Garry Tan's GBrain project (personal AI memory system, ~17K GitHub stars) to CC-Suite's markdown-first approach. GBrain uses a richer schema and dedicated tooling; this protocol prioritizes simplicity and portability over richness.
