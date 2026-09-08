// SPDX-License-Identifier: MPL-2.0
/**
 * FDRL Component 4: FODL Auto-Emission primitive — handleEmitFodl.
 *
 * Per docs/brain/052626__fleet-decision-routing-layer-fdrl-canon.md §COMPONENT 4.
 *
 * Phase 1 scope:
 *   - Append FODL block to docs/fodl/current.md (under "## Open Decisions")
 *   - Append audit entry to docs/fodl/audit_log.jsonl
 *   - Auto-assign FODL-{ID} by scanning existing IDs in current.md (max+1)
 *
 * Deferred to Phase 2:
 *   - Emergency Slack ping for time_sensitivity="emergency" (< 6hr deadline).
 *     Hook off the audit_log entry once Phase 1 ships and CCPO V-Loops.
 *
 * Plain-English render layout per 2026-04-19 canon refinement
 * (docs/brain/041826__fodl-trigger.md v1.1).
 */

import { spawnSync } from "node:child_process";
import { accessSync, constants } from "node:fs";
import { dirname, resolve } from "node:path";

export const VALID_TIME_SENSITIVITY = new Set(["emergency", "normal", "low"]);
export const VALID_TIERS = new Set(["S", "A", "B", "C"]);
export const VALID_EDGS = new Set([0, 1, 2, 3, 4]);

export interface FodlOption {
  label: string;
  text: string;
}

export interface EmitFodlArgs {
  title: string;
  problem_statement: string;
  why_it_matters: string;
  options: FodlOption[];
  recommended_default: string;
  if_no_decision_by: string;
  time_sensitivity: string;
  tier: string;
  edg: number;
  originating_agent: string;
  arbiter_consulted?: string;
  precedent?: string;
}

export interface EmitFodlContext {
  readFile: (path: string) => string;
  writeFile: (path: string, content: string) => void;
  appendFile: (path: string, content: string) => void;
  now: () => Date;
  currentMd: string;
  auditLog: string;
  mirrorKernel?: () => void;
}

export function getNextFodlId(currentMdContent: string): string {
  const regex = /FODL-(\d+)/g;
  const ids: number[] = [];
  let m: RegExpExecArray | null;
  while ((m = regex.exec(currentMdContent)) !== null) {
    ids.push(parseInt(m[1]!, 10));
  }
  const max = ids.length > 0 ? Math.max(...ids) : 0;
  return `FODL-${String(max + 1).padStart(3, "0")}`;
}

export function renderFodlItem(id: string, args: EmitFodlArgs): string {
  const optionLines = args.options.map((o) => `  ${o.label}. ${o.text}`).join("\n");
  const lines = [
    `### ${id} — ${args.title}`,
    ``,
    `${id}: ${args.title}`,
    `What you're deciding: ${args.problem_statement}`,
    `Why it matters: ${args.why_it_matters}`,
    `Options:`,
    optionLines,
    `My pick: ${args.recommended_default}`,
    `If you don't decide by ${args.if_no_decision_by}: (see Why it matters)`,
    `Time-sensitivity: ${args.time_sensitivity}`,
    `Tier × EDG: ${args.tier} × EDG-${args.edg}`,
    `Originating agent: ${args.originating_agent}`,
  ];
  if (args.arbiter_consulted) lines.push(`Arbiter consulted: ${args.arbiter_consulted}`);
  if (args.precedent) lines.push(`Precedent: ${args.precedent}`);
  return lines.join("\n");
}

const EMPTY_PLACEHOLDER_REGEX = /\*\(empty[^)]*\)\*/;
const OPEN_DECISIONS_REGEX = /(^## Open Decisions[ \t]*\n+)/m;
const PROJECT_ROOT = resolve(process.env.CC_SUITE_WORKSPACE || ".");
const PACKAGE_ROOT = resolve(import.meta.dir, "../..");
const CANONICAL_CURRENT_MD = resolve(PROJECT_ROOT, "docs/fodl/current.md");
const CANONICAL_AUDIT_LOG = resolve(PROJECT_ROOT, "docs/fodl/audit_log.jsonl");
const CANONICAL_DISPATCH_INTENTS = resolve(PROJECT_ROOT, "docs/ledger/dispatch_intents.jsonl");

function kernelPython(): string {
  const configured = process.env.CC_SUITE_PYTHON || "";
  if (!configured.startsWith("/")) throw new Error("CC_SUITE_PYTHON is required for the kernel mirror");
  accessSync(configured, constants.X_OK);
  return configured;
}

export function mirrorKernelFromEnvironment(
  currentMd: string,
  auditLog: string,
  runner: typeof spawnSync = spawnSync,
): void {
  const prepared = prepareKernelMirror(currentMd, auditLog);
  if (prepared === null) return;
  runPreparedKernelMirror(prepared, runner);
}

interface PreparedKernelMirror {
  python: string;
  script: string;
  args: string[];
}

function prepareKernelMirror(currentMd: string, auditLog: string): PreparedKernelMirror | null {
  const mode = (process.env.FLEET_KERNEL_M5_WRITE_MODE || "legacy").trim().toLowerCase();
  if (mode !== "legacy" && mode !== "dual") {
    throw new Error("FLEET_KERNEL_M5_WRITE_MODE must be legacy or dual");
  }
  if (
    mode === "legacy"
    || resolve(currentMd) !== CANONICAL_CURRENT_MD
    || resolve(auditLog) !== CANONICAL_AUDIT_LOG
  ) return null;
  if ((process.env.MISE_TENANT || "").trim()) {
    throw new Error("M5 FODL dual-write is tenant-zero only before Phase 7");
  }
  if (
    !(process.env.FLEET_KERNEL_DATABASE_URL || "").trim()
    || !(process.env.FLEET_KERNEL_TENANT_ID || "").trim()
  ) {
    throw new Error("M5 FODL dual-write requires database URL and tenant ID");
  }
  const script = resolve(PACKAGE_ROOT, "scripts/sync_kernel_dispatch_approval_shadow.py");
  return { python: kernelPython(), script, args: [
    "--dispatch-intents", CANONICAL_DISPATCH_INTENTS,
    "--fodl-audit", CANONICAL_AUDIT_LOG,
    "--fodl-current", CANONICAL_CURRENT_MD,
  ] };
}

function runPreparedKernelMirror(
  prepared: PreparedKernelMirror,
  runner: typeof spawnSync,
): void {
  const result = runner(prepared.python, [prepared.script, ...prepared.args], {
    encoding: "utf-8", env: process.env,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error((result.stderr || result.stdout || `sync exited ${result.status}`).trim());
  }
}

function preflightPreparedKernelMirror(
  prepared: PreparedKernelMirror,
  runner: typeof spawnSync,
): void {
  const result = runner(
    prepared.python, [prepared.script, ...prepared.args, "--validate-config-only"],
    { encoding: "utf-8", env: process.env },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error((result.stderr || result.stdout || `preflight exited ${result.status}`).trim());
  }
}

export function insertIntoOpenDecisions(currentMdContent: string, fodlBlock: string): string {
  // Strategy:
  //   1. If "## Open Decisions" exists with an empty placeholder, replace placeholder with block.
  //   2. Otherwise insert block immediately after "## Open Decisions" heading + blank line.
  //   3. If "## Open Decisions" doesn't exist (shouldn't happen for production FODL), throw.

  if (!OPEN_DECISIONS_REGEX.test(currentMdContent)) {
    throw new Error("docs/fodl/current.md missing '## Open Decisions' section");
  }

  if (EMPTY_PLACEHOLDER_REGEX.test(currentMdContent)) {
    return currentMdContent.replace(EMPTY_PLACEHOLDER_REGEX, fodlBlock);
  }

  return currentMdContent.replace(OPEN_DECISIONS_REGEX, `$1${fodlBlock}\n\n`);
}

export function handleEmitFodl(
  ctx: EmitFodlContext,
  args: EmitFodlArgs
): { success: true; fodl_id: string } | { success: false; error: string } {
  if (!args.title || args.title.trim().length === 0) {
    return { success: false, error: "title is required" };
  }
  if (!args.problem_statement || args.problem_statement.trim().length === 0) {
    return { success: false, error: "problem_statement is required" };
  }
  if (!args.why_it_matters || args.why_it_matters.trim().length === 0) {
    return { success: false, error: "why_it_matters is required" };
  }
  if (!args.options || args.options.length < 2) {
    return { success: false, error: "At least 2 options required" };
  }
  for (const opt of args.options) {
    if (!opt.label || !opt.text) {
      return { success: false, error: "Every option needs both label and text" };
    }
  }
  if (!args.recommended_default || args.recommended_default.trim().length === 0) {
    return { success: false, error: "recommended_default is required" };
  }
  if (!args.if_no_decision_by || args.if_no_decision_by.trim().length === 0) {
    return { success: false, error: "if_no_decision_by is required" };
  }
  if (!VALID_TIME_SENSITIVITY.has(args.time_sensitivity)) {
    return { success: false, error: `Unknown time_sensitivity "${args.time_sensitivity}". Valid: emergency, normal, low` };
  }
  if (!VALID_TIERS.has(args.tier)) {
    return { success: false, error: `Unknown tier "${args.tier}". Valid: S, A, B, C` };
  }
  if (!VALID_EDGS.has(args.edg)) {
    return { success: false, error: `Unknown EDG ${args.edg}. Valid: 0-4` };
  }
  if (!args.originating_agent || !/^[A-Z]+-\d+$/.test(args.originating_agent)) {
    return { success: false, error: "originating_agent must match {ROLE}-{N} format (e.g. CCDE-001)" };
  }

  let mirrorKernel = ctx.mirrorKernel;
  if (!mirrorKernel) {
    try {
      const prepared = prepareKernelMirror(ctx.currentMd, ctx.auditLog);
      if (prepared) {
        preflightPreparedKernelMirror(prepared, spawnSync);
        mirrorKernel = () => runPreparedKernelMirror(prepared, spawnSync);
      } else {
        mirrorKernel = () => {};
      }
    } catch (err) {
      return { success: false, error: `Failed to configure M5 kernel mirror: ${err}` };
    }
  }

  let currentMd: string;
  try {
    currentMd = ctx.readFile(ctx.currentMd);
  } catch (err) {
    return { success: false, error: `Failed to read current.md: ${err}` };
  }

  const fodl_id = getNextFodlId(currentMd);
  const block = renderFodlItem(fodl_id, args);

  let updated: string;
  try {
    updated = insertIntoOpenDecisions(currentMd, block);
  } catch (err) {
    return { success: false, error: `${err}` };
  }

  try {
    ctx.writeFile(ctx.currentMd, updated);
  } catch (err) {
    return { success: false, error: `Failed to write current.md: ${err}` };
  }

  const auditEntry = {
    ts: ctx.now().toISOString(),
    fodl_id,
    originating_agent: args.originating_agent,
    arbiter_consulted: args.arbiter_consulted ?? null,
    tier: args.tier,
    edg: args.edg,
    time_sensitivity: args.time_sensitivity,
    if_no_decision_by: args.if_no_decision_by,
    title_length: args.title.length,
    problem_statement_length: args.problem_statement.length,
  };
  try {
    ctx.appendFile(ctx.auditLog, JSON.stringify(auditEntry) + "\n");
  } catch (err) {
    return { success: false, error: `Failed to append audit_log.jsonl: ${err}` };
  }

  try {
    mirrorKernel();
  } catch (err) {
    return {
      success: false,
      error: `Legacy FODL write is committed but synchronous kernel mirror failed: ${err}`,
    };
  }

  return { success: true, fodl_id };
}
