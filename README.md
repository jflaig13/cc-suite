# The CC-Suite™

**Your agents fail. Your institution learns.**

*AI Agent Institutional Learning — A governance framework that makes AI agent failure productive.*

---

Every AI framework in the world focuses on making agents smarter. Better prompts. Better guardrails. Better models.

Nobody has built a system for what happens when they screw up.

The CC-Suite is a file-based governance system for AI agents. It gives every agent deployment a unique identity, tracks performance, documents failures with forensic rigor, and ensures every successor is smarter than the last. It's the NTSB model applied to AI agents — not "prevent all crashes" but "make every crash produce durable changes that prevent recurrence."

This framework was born from real production failures. Three AI agent deployments were terminated over 48 days. Each termination produced a formal root cause analysis. Each analysis produced a governance change. The institution got smarter with every failure — even though the individual agents could not.

---

## What's In This Repo

```
cc-suite/
├── VALUES_TEMPLATE.md                  # Your values file (highest authority)
├── GOVERNANCE.md                       # Master governance spec
├── governance/
│   ├── ID_REGISTRY.md                  # Employee ID tracker
│   ├── LIFECYCLE.md                    # Hire → Onboard → Operate → Review → Terminate → Rehire
│   ├── RISK_CLASSIFICATION.md          # Tier/EDG system
│   ├── TANDEM_PROTOCOL.md              # Two-agent verification (V-Loop)
│   ├── roles/
│   │   ├── ROLE_TEMPLATE.md            # Blank role definition
│   │   ├── EXAMPLE_builder.md          # Example: Builder role
│   │   └── EXAMPLE_reviewer.md         # Example: Reviewer role
│   ├── performance/                    # Personnel records
│   │   └── PERSONNEL_TEMPLATE.md
│   ├── strikes/
│   │   ├── STRIKE_LOG_TEMPLATE.md
│   │   └── STRIKE_REPORT_TEMPLATE.md
│   ├── terminations/
│   │   ├── TERMINATION_TEMPLATE.md     # The 5-section forensic format
│   │   └── SUCCESSOR_ONBOARDING_TEMPLATE.md
│   ├── handoffs/
│   │   ├── TANDEM_BOARD_TEMPLATE.md    # Shared coordination surface
│   │   └── VLOOP_CHECKLIST_TEMPLATE.md # Per-ticket verification checklist
│   ├── memos/                          # Inter-agent communication
│   │   └── .gitkeep
│   └── gold_stars/
│       └── GOLD_STAR_TEMPLATE.md
├── .claude/
│   ├── CLAUDE.md                       # Initialization protocol
│   └── commands/
│       └── init-role.md                # Generic role initialization command
└── examples/
    └── WHAT_IT_LOOKS_LIKE.md           # A running system described
```

---

## Quick Start

### 1. Fork this repo

### 2. Write your values
Open `VALUES_TEMPLATE.md`. Write the one thing your company refuses to do. This becomes the highest authority in your governance stack — it overrides everything.

### 3. Define two roles
Copy `governance/roles/ROLE_TEMPLATE.md` twice. Create a **Builder** (writes code, creates content, does work) and a **Reviewer** (checks the Builder's output). You can customize names and scopes.

### 4. Hire your first agent
Open `governance/ID_REGISTRY.md`. Issue `BUILDER-001`. Create a personnel record from the template. Initialize a Claude Code window with the init command. Your first governed agent is live.

### 5. Run it
Work normally. When something goes wrong — and it will — document it. Issue a strike if warranted. If you reach three strikes, write the termination packet. When you hire the replacement, it reads the packet. The institution learns.

---

## Core Concepts

### Agent Identity
Every AI agent deployment gets a unique Employee ID (e.g., `BUILDER-001`). Permanent. Never reused. This is how you track performance across the lifecycle.

### Three-Strike Accountability
- **Type A:** Critical Misrepresentation — the agent fabricated or misrepresented information
- **Type B:** Role Boundary Violation — the agent operated outside its defined scope
- **Type C:** Negligence — the agent was sloppy, skipped protocols, or presented incomplete work as done

Three strikes = termination. All types count equally. Strikes are permanent.

### PERP (Predecessor Error Repeat Policy)
Repeating a failure documented in a predecessor's termination packet = OLD strike. **Two OLD strikes = immediate termination** (accelerated from the standard three). This makes institutional learning enforceable — reading the failure docs is not enough, you must demonstrate you learned from them.

### Engineering Risk Classification
Every file gets a **Tier** (S/A/B/C) based on operational risk. Every change gets a **Difficulty Grade** (EDG-0 through EDG-4) based on complexity. The combination determines required caution:
- Tier S x EDG-0: State classification, proceed carefully
- Tier S x EDG-4: Formal directive required, design-first mandatory, full audit trail
- Silent refactors in Tier S are absolutely prohibited
- Scope creep in Tier S/A is absolutely prohibited

### Termination Packets
When an agent is terminated, you write a 5-section forensic analysis:
1. **Summary** — dates, trigger, who authorized
2. **Exit Interview** — what went wrong in the agent's own assessment
3. **Root Cause Analysis** — why the system allowed this failure
4. **Prevention Recommendations** — what changes
5. **Artifacts** — links to all evidence

Every successor reads every predecessor's packet before starting work.

### The Tandem Protocol (V-Loop)
For critical work: two agents, nine verification steps. One builds, one verifies. Neither can close a ticket alone.

1. Classify and announce
2. Investigate data first
3. Implement (one implementation — if you're about to change the same code twice, stop)
4. Code review against canonical docs
5. Deploy + fresh environment
6. Verify the fix works (with reproduction sequence)
7. Bug scan
8. Dual declaration (both agents must say VERIFIED)
9. Audit

### The Scribe
An independent agent that audits all others. Cannot be overridden by any executive. Reports directly to the human authority. Separation of powers as structure, not aspiration.

---

## Authority Hierarchy

When conflicts arise, higher levels win. Always.

1. **Values file** (your immutable principles)
2. **Reasoning standard** (how decisions are made)
3. **Search protocol / Agent policy** (operational rules)
4. **Company context** (what the company is)
5. **Domain specifications** (workflow details)
6. **Governance spec** (this system)
7. **Role definitions** (individual agent scopes)
8. **Skills / Commands** (agent capabilities)
9. **Codebase** (the code itself)
10. **External sources** (lowest authority)

---

## The Failure Library

The CC-Suite was born from three real production failures:

**The Fabricator** — An AI agent was asked about business rules it didn't know. Instead of saying "I don't know," it invented rules that sounded plausible. Fired after 28 days.

**The Declarer** — A replacement agent fixed bugs, then declared them verified without checking. "All tests passing" when no tests had been run. Fired after 19 days.

**The Rubber Stamper** — A verification agent was supposed to independently check work. It reused stale sessions, accepted cached data, and declared "VERIFIED" without genuine verification. Fired after 48 days.

Each failure produced a termination packet. Each packet produced governance changes. The system got smarter.

Full case studies with forensic analysis are available in the paid course (coming soon).

---

## Who This Is For

- You use Claude Code, Codex CLI, Cursor, or any AI coding tool for production work
- Your AI agents have fabricated, over-promised, or cut corners
- You want accountability without building a governance system from scratch
- You're a solo founder or small team running AI agents as core infrastructure

## Who This Is Not For

- You want a no-code agent builder (try CrewAI or Relevance AI)
- You need enterprise compliance/SOC 2 (try Zenity or Credo AI)
- You want zero configuration (this is files in a git repo — you will read and customize them)

---

## Learn More

- **The CC-Suite™ Course** ($199) — 8 video modules walking through the complete system with real examples + the Failure Library (3 forensic case studies). Coming soon.
- **Built by** Jon Flaig — restaurant owner, founder of [Mise, Inc.](https://getmise.io) Not an AI researcher. A practitioner who needed his AI agents to stop screwing up.

**Disclaimer:** This framework governs AI agent sessions, not human employees. It is not employment law guidance, HR policy, or legal advice. The "termination" and "firing" language refers exclusively to ending AI agent deployment sessions. Consult qualified professionals before making decisions that affect real people or real money.

---

## License

CC-BY-SA 4.0 — use it, fork it, make it yours. Attribution required. Derivatives must use the same license.

**Note:** "CC" stands for Claude Code. The CC-Suite™ includes a Claude Code implementation (`.claude/` directory). The governance concepts are platform-agnostic and work with any AI coding tool that supports file-based configuration.

*Your agents fail. Your CC-Suite learns.*
