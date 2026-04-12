# Subatomic Audit Protocol — The 7-Wave Standard

**Status:** CANON (CC-Suite™ v2 — Phase 2 Scribe)
**Authority:** Layer 6 (Governance Spec). Extends `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md`.
**Scope:** The canonical pattern for comprehensive system audits requiring 100% (or near) certainty.
**Established:** 2026-04-11 (CC-Suite™ reference implementation), via the founder canon trigger during the cc-suite v2 Layer D final audit Expo Protocol.
**Related:** `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md` (per-event verdict checklist — complementary to this protocol).

---

## Purpose

The Scribe periodically audits the entire governance system to detect drift, broken cross-references, stale patterns, missing implementations, hallucinated documentation, and unknown failure modes. Per-event audits (handled by `SCRIBE_AUDIT_CHECKLIST.md`) catch incremental drift one event at a time. Subatomic audits catch the drift that has already accumulated and the failure modes that no single event would surface.

The 7-wave pattern exists because **naive parallel-agent audits have seven latent failure modes** that cause real drift to escape detection:

| # | Failure mode | Example |
|---|---|---|
| 1 | **Surface seams** | Agent A audits brain files, Agent B audits init files; the connection between them (does init-X reference brain-Y?) belongs to neither agent. |
| 2 | **Confidence collapse** | An agent reports "all clean" without per-item evidence; synthesis trusts the verdict. |
| 3 | **Stale context** | Agent's snapshot was N minutes ago; an edit since then makes its report partially stale. |
| 4 | **Token starvation** | Agent silently truncates when context fills; reports "all clean for what I checked" without flagging the truncation. |
| 5 | **Hallucinated cross-refs** | Agent says "this references X" without grepping; LLMs fabricate references the same way they fabricate facts. |
| 6 | **Disagreement without arbitration** | Two agents reach different conclusions on overlap; no protocol resolves it. |
| 7 | **Unknown unknowns** | Every agent works from a list. If a class of issue isn't on anyone's list, no agent sees it. |

The 7-wave pattern is built specifically to mitigate every one of these through dedicated architectural elements.

---

## The Seven Principles

### 1. ENUMERATE first, then VERIFY (closed set)

Convert open-ended "find anything wrong" into closed-set "every particle in this inventory must get a verdict." Mathematically prove completion when every particle is verdicted.

### 2. ORTHOGONAL slices (every agent sees every surface through ONE lens)

Instead of partitioning by surface (Agent 1 = brain files, Agent 2 = init files), each agent reads ALL relevant surfaces through ONE concern lens. The seams disappear.

### 3. ADVERSARIAL red-team (try to break, not confirm)

Stranger-fork simulator, fresh-onboarding simulator, hook event replay, trigger fuzzer. Adversarial agents catch what verification agents won't.

### 4. MECHANICAL ground truth (not LLM judgment)

For mechanically-verifiable particles (file existence, syntax validity, JSON parseability, cross-ref resolution), bash/python executes the check. The LLM only enumerates WHAT to check. Eliminates hallucinated verification.

### 5. COVERAGE OVERLAP for critical surfaces

Two independent agents do the same audit on critical areas with different framings. Agreement = high confidence. Disagreement triggers an arbitrator.

### 6. META-AUDIT the audit itself

After the per-concern agents finish, ONE agent audits the audit: every particle verdicted? "all clean" claims have evidence? agent disagreements? silent token starvation?

### 7. STRATIFIED SAMPLING for unknown unknowns

Pure-random samples from each pool (brain files, recent commits, scripts, init files) deep-read by an LLM agent. The only mitigation for issues that aren't on any list.

---

## The 7 Waves (Canonical Execution)

### Wave 1 — Enumeration (mechanical)

**Owner:** The Scribe primary running the audit.

**Method:** Bash + glob + grep to build the particle inventory. No LLM agents.

**Inputs:** Audit scope (e.g., "entire governance system" or "channel architecture only").

**Outputs:** A particle inventory file at the audit ledger directory with every artifact in scope numbered sequentially.

**Particles enumerated (default scope — adjust per audit):**

- Every governance markdown file
- Every script in your scripts/tools directory
- Every JSON config
- Every cross-reference of the form ` `path/to/file` `
- Every cron job, scheduled task, or hook entry
- Every environment-specific configuration

**Success criterion:** The inventory exists, has been counted, and the count is logged.

### Wave 2 — Orthogonal Concern Agents (8 parallel LLM agents, default)

**Owner:** Scribe primary, dispatching parallel LLM agents.

**Method:** Each agent receives ALL relevant particles in the inventory and audits them through ONE concern lens. Every agent sees every relevant surface; only the lens differs.

**Default 8 concerns (adjust per audit scope):**

| # | Concern | Lens |
|---|---|---|
| 2.1 | Cross-reference resolution | Every reference in every doc — does the target file/section exist? |
| 2.2 | Protocol → implementation | Every protocol description — does the implementation script/file exist and match the spec? |
| 2.3 | Init flow consistency | Every initialization file — does it mention every relevant capability? Does it reference your central rules file? |
| 2.4 | Runtime compatibility | Every script — syntactic check + version-specific gotchas (bash 3.2 vs 4+, python version, etc.). |
| 2.5 | Cross-layer consistency | Same fact stated in multiple places — do they agree? |
| 2.6 | Index completeness | Every governance doc — referenced from ≥1 of your indexes? Orphans flagged. |
| 2.7 | Stale-pattern sweep | Grep for deprecated patterns from your deprecation list. |
| 2.8 | Secret + PII leak | Comprehensive grep across all surfaces for credentials, hostnames, IPs, paths, real PII. |

**Per-agent prompt structure:** Self-contained, includes the particle inventory path, the specific concern, expected output format (write to shared findings ledger), and a token budget. Each agent reports findings to the shared ledger, NOT back to Scribe directly.

**Success criterion:** Every relevant particle has been seen by every applicable concern agent.

### Wave 3 — Adversarial Red-Team (4 parallel LLM agents, default)

**Owner:** Scribe primary, dispatching in parallel with Wave 2.

**Method:** Each agent simulates a hostile/naive consumer and reports what breaks.

**Default 4 adversarial roles:**

| # | Role | What this agent does |
|---|---|---|
| 3.1 | Stranger fork simulator | Pretend to clone the public repo. Read README. Try to follow Quick Start. Report any confusion, missing prerequisite, or workflow break. |
| 3.2 | Fresh agent onboarding simulator | Pick a role. Read its initialization sequence from start to finish as if you'd never seen it. Report any missing context, broken cross-ref, or "you'd be lost without prior knowledge" gap. |
| 3.3 | Hook event replay | Manually execute every automated hook script. Verify they work under their declared trigger conditions. |
| 3.4 | Trigger fuzzer | List every shorthand trigger from your operator-facing docs. Verify the documented behavior is mechanically achievable for each. |

**Per-agent prompt structure:** Adversarial framing — "your job is to find what breaks, not to confirm what works."

**Success criterion:** Every adversarial role has produced a report.

### Wave 4 — Ground Truth (mechanical)

**Owner:** Scribe primary, running shell + python directly.

**Method:** For each particle in Wave 1's inventory, run the actual mechanical check. No LLM judgment — pure pass/fail per particle.

**Standard checks per particle type:**

| Particle type | Mechanical check |
|---|---|
| File path | `test -f` + `test -r` |
| Script path | `test -x` + syntactic validation (`bash -n`, `python3 -c "import ast; ast.parse(...)"`, etc.) |
| JSON file | parse via `python3 -m json.tool` or equivalent |
| Cross-reference target | File existence check on the referenced path |
| Hook script in your settings file | Existence + executable bit |

**Output:** A per-particle verdict table at the audit ledger directory.

**Success criterion:** Every applicable particle has a mechanical verdict.

**This is the wave that catches LLM hallucinations.** Where Wave 2 says "the cross-reference resolves," Wave 4 actually proves it. Disagreements between Wave 2 and Wave 4 are flagged for the meta-audit.

### Wave 5 — Stratified Sampling (4 parallel LLM agents, default)

**Owner:** Scribe primary, dispatching in parallel with Waves 2/3.

**Method:** Pure-random sampling against pools, then deep read by an LLM agent.

**Default 4 sample classes:**

| Sample | Pool | Method |
|---|---|---|
| 5 random brain files | All governance markdown | `find ... \| sort -R \| head -5`, full deep-read |
| 5 random commits from last 7 days | Recent git history | `git log --since="7 days ago" --pretty=format:%H \| sort -R \| head -5`, deep-read each |
| 5 random scripts | Scripts directory | Same — sort -R, head -5, deep-read |
| 5 random init files | Initialization sequence | Same |

**Why pure random:** Eliminates Scribe selection bias.

**Per-agent task:** Read the 5 sampled artifacts in full. Look for any drift, bug, or stale pattern not caught by Wave 2's lens-based checks.

**Success criterion:** Each sampling agent has produced a report.

### Wave 6 — Meta-Audit (1 LLM agent)

**Owner:** Scribe primary, dispatching ONE agent after Waves 2/3/4/5 complete.

**Method:** The meta-audit agent reads the entire shared findings ledger + the ground truth table + the particle inventory, then audits the audit itself.

**Meta-checks:**

- Did every particle in the inventory get a verdict from at least one wave?
- Did any agent claim "all clean" without per-item evidence?
- Are there observable seams between agent assignments where nothing was checked?
- What's the confidence distribution? How many low-confidence findings?
- Did Waves 2 and 4 (LLM agent vs mechanical ground truth) agree on overlapping checks? Disagreement = LLM hallucination.
- Did any agent silently truncate (e.g., reported on 100 particles when there were 200)?
- Are there any classes of issue mentioned in Wave 2 that don't appear in Wave 5's sampling? (Suggests under-sampling.)

**Output:** Meta-verdict + list of any gaps requiring follow-up agents.

**Success criterion:** Meta-verdict produced. If gaps exist, dispatch follow-up agents and re-run Wave 6.

### Wave 7 — Synthesis + Replate (Scribe primary)

**Owner:** Scribe primary, NOT a delegated agent.

**Method:** Read the entire findings ledger. Compile findings into a unified severity-ranked report (Critical / Moderate / Polish). Fix all Critical items via Scribe-lane authority. Re-run the affected concern agent on each fix to confirm clean. Repeat until clean.

**Final output:** Audit report posted to your coordination channel with broadcast marker, plus internal final report at the audit ledger directory.

**Success criterion:** Zero remaining Critical items, every Moderate item either fixed or explicitly flagged for the human authority, every Polish item documented for the next meta-audit cycle.

---

## Shared Findings Ledger

All agents in Waves 2-6 write to ONE shared file, NOT back to the Scribe primary. This eliminates synthesis-compression loss and provides an audit trail.

```
audit_ledger_directory/
├── particle_inventory.md       (Wave 1 output)
├── findings.md                 (shared ledger — every agent appends a section)
├── ground_truth.md             (Wave 4 output)
├── meta_audit.md               (Wave 6 output)
└── final_report.md             (Wave 7 output)
```

**Ledger format:** Each agent writes a section with header `## Agent <wave>.<id> — <concern>` followed by findings. Every claim has an attribution. Findings reference particle IDs from the inventory.

**Why a file ledger and not in-memory synthesis:** Token limits + stale-context + the meta-audit needs the raw evidence, not a Scribe summary.

---

## Agent Count Defaults and Adjustments

| Wave | Agents | Type |
|---|---|---|
| 1 | 0 | Mechanical (bash/grep) |
| 2 | 8 (default) | LLM (orthogonal concerns) |
| 3 | 4 (default) | LLM (adversarial red-team) |
| 4 | 0 | Mechanical (bash/python) |
| 5 | 4 (default) | LLM (stratified sampling) |
| 6 | 1 | LLM (meta-audit) |
| 7 | 0 | Scribe primary (synthesis + replate) |
| **Total** | **17 LLM agents** + 2 mechanical waves + Scribe synthesis |

**Adjust per scope:**

- **Narrow scope** (single file or single subsystem): 3 concern agents, 0 red-team, 2 sampling, skip meta-audit if findings are zero
- **Default scope** (entire governance system): 17 agents as documented
- **Broad scope** (multiple repos or cross-system audit): 10-12 concern agents, 6 sampling agents

---

## When to Use the 7-Wave Pattern

**Mandatory for:**

- Any periodic deep audit (weekly or monthly cadence)
- Any audit triggered by a major event (production deploy, governance change, agent termination, verification failure)
- Any human-authority request phrased as "comprehensive audit" or "100% certainty" or "every particle"

**Recommended for:**

- Major refactors or governance shifts that touch multiple layers of the system
- Pre-launch verification before pushing to public-facing repos
- Post-incident audits where root cause is documented but completeness is uncertain

**NOT required for:**

- Daily light audits (use a grep-based scan of known drift patterns instead)
- Single-file fixes
- Read-only investigations where no fix is needed

---

## Why 17 Agents Instead of 5

The previous "5 parallel agents covering 5 surfaces" pattern was useful for catching the most obvious drift but had the seven failure modes documented above. The 7-wave pattern is built specifically to mitigate every one of those failure modes through architectural elements that don't exist in the 5-agent pattern.

The token cost of 17 agents is offset by:
- Higher confidence in the final verdict (every particle has a verdict, not just "the surface looks clean")
- Mechanical ground truth eliminates LLM hallucination as a failure source
- Adversarial red-team catches usability and onboarding issues that internal-consistency checks miss
- Stratified sampling catches unknown unknowns
- Meta-audit catches confidence collapse and silent truncation

For audits where 100% certainty is the goal, the 17-agent cost is justified. For lower-stakes audits, scale down per the per-scope guidance.

---

## Reference Implementation

The CC-Suite™ reference implementation (Mise, Inc.) ratified this canon on 2026-04-11 via the founder "this is the way" trigger during the Expo Protocol for the cc-suite v2 Layer D final audit. The inaugural execution scoped the entire governance system and Scribe-lane infrastructure. Mise's full canonical brain file with origin context and execution log: `docs/brain/041126__7-wave-subatomic-audit-canon.md`.

Adopters of the CC-Suite framework should treat this protocol as the canonical pattern when comprehensive audit certainty is required. Adjust the agent counts, particle inventory scope, and ground-truth checks to match your domain.
