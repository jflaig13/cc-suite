// SPDX-License-Identifier: MPL-2.0
/**
 * FDRL Component 3: CoS Arbiter Protocol primitive — handleArbiterAsk.
 *
 * Per docs/brain/052626__fleet-decision-routing-layer-fdrl-canon.md §COMPONENT 3.
 *
 * Dispatches a structured arbiter-ask to CoS-001 by composing a canonical
 * envelope and delegating to OIL-012's send primitive (target_role="cos",
 * event_type="arbiter_ask"). Asking agent's AGENT_ROLE is supplied by the
 * caller (channel.ts holds the env var; this module stays env-free for
 * testability).
 */

export const VALID_TIERS = new Set(["S", "A", "B", "C"]);
export const VALID_EDGS = new Set([0, 1, 2, 3, 4]);
export const VALID_CONFIDENCE = new Set(["VF", "MN", "FDP", "U"]);

export interface ArbiterAskArgs {
  tier: string;
  edg: number;
  confidence: string;
  question: string;
  options: string[];
  recommended_default: string;
  subject_slug: string;
  confidence_pct?: number;
  peer_consulted?: string;
  files_searched?: string[];
  precedent?: string;
  deadline?: string;
  meta?: Record<string, unknown>;
}

export interface SendArgsForArbiter {
  target_role: string;
  content: string;
  event_type?: string;
  meta?: Record<string, unknown>;
}

export type SendFn = (args: SendArgsForArbiter) =>
  | { success: true; send_id: string }
  | { success: false; error: string };

export interface ArbiterContext {
  agent_role: string;
  send: SendFn;
}

export function handleArbiterAsk(
  ctx: ArbiterContext,
  args: ArbiterAskArgs
): { success: true; send_id: string } | { success: false; error: string } {
  if (ctx.agent_role === "cos") {
    return { success: false, error: "CoS-001 cannot dispatch arbiter_ask — arbiter requests target CoS by definition" };
  }

  if (!VALID_TIERS.has(args.tier)) {
    return { success: false, error: `Unknown tier "${args.tier}". Valid: S, A, B, C` };
  }
  if (!VALID_EDGS.has(args.edg)) {
    return { success: false, error: `Unknown EDG ${args.edg}. Valid: 0-4` };
  }
  if (!VALID_CONFIDENCE.has(args.confidence)) {
    return { success: false, error: `Unknown confidence "${args.confidence}". Valid: VF, MN, FDP, U` };
  }
  if (!args.question || args.question.trim().length === 0) {
    return { success: false, error: "Question is required" };
  }
  if (!args.options || args.options.length === 0) {
    return { success: false, error: "At least one option required" };
  }
  if (!args.recommended_default || args.recommended_default.trim().length === 0) {
    return { success: false, error: "Recommended-default is required" };
  }
  if (!args.subject_slug || !/^[a-z0-9-]+$/.test(args.subject_slug)) {
    return { success: false, error: "subject_slug must be kebab-case (lowercase a-z, 0-9, hyphens only)" };
  }

  const confidenceStr = args.confidence_pct != null
    ? `${args.confidence} (${args.confidence_pct}%)`
    : args.confidence;
  const optionsBlock = args.options.map((o, i) => `  ${i + 1}. ${o}`).join("\n");
  const filesStr = args.files_searched && args.files_searched.length > 0
    ? args.files_searched.join(", ")
    : "(none cited)";

  const content = [
    `FROM: ${ctx.agent_role.toUpperCase()}-001`,
    `TO: CoS-001`,
    `SUBJECT: arbiter-ask-${args.subject_slug}`,
    ``,
    `Tier: ${args.tier}`,
    `EDG: ${args.edg}`,
    `Confidence: ${confidenceStr}`,
    `Question: ${args.question}`,
    `Options considered:`,
    optionsBlock,
    `Peer consulted (if any): ${args.peer_consulted || "(none)"}`,
    `Files searched: ${filesStr}`,
    `Precedent (if any): ${args.precedent || "(none)"}`,
    `Recommended-default: ${args.recommended_default}`,
    `Deadline: ${args.deadline || "no deadline"}`,
  ].join("\n");

  return ctx.send({
    target_role: "cos",
    content,
    event_type: "arbiter_ask",
    meta: {
      ...(args.meta || {}),
      arbiter_ask: true,
      tier: args.tier,
      edg: args.edg,
      confidence: args.confidence,
      confidence_pct: args.confidence_pct ?? null,
      deadline: args.deadline || null,
      subject_slug: args.subject_slug,
    },
  });
}
