/**
 * Tests for arbiter_ask.ts — FDRL Component 3 (canon 2026-05-26).
 *
 * Coverage:
 *   (a) Valid envelope composes canonical content + dispatches via send
 *   (b) CoS self-arbitration rejected
 *   (c) Unknown tier / EDG / confidence rejected
 *   (d) Missing question / options / recommended_default rejected
 *   (e) Invalid subject_slug (non-kebab) rejected
 *   (f) confidence_pct renders as "VF (85%)" in envelope
 *   (g) Optional fields default to "(none)" / "(none cited)" / "no deadline"
 *   (h) Meta payload includes structured fields (tier, edg, confidence, etc.)
 *   (i) send_id propagated from underlying send
 *   (j) target_role always "cos" regardless of asking agent
 *   (k) event_type always "arbiter_ask"
 *   (l) FROM line reflects asking agent's role (uppercase)
 */

import { test, expect, describe } from "bun:test";
import {
  handleArbiterAsk,
  type ArbiterAskArgs,
  type ArbiterContext,
  type SendArgsForArbiter,
} from "./arbiter_ask";

function makeSpy(): {
  ctx: (role: string) => ArbiterContext;
  lastCall: () => SendArgsForArbiter | null;
  callCount: () => number;
} {
  let captured: SendArgsForArbiter | null = null;
  let count = 0;
  return {
    ctx: (role: string) => ({
      agent_role: role,
      send: (args: SendArgsForArbiter) => {
        captured = args;
        count++;
        return { success: true, send_id: `${role}-send-0042` };
      },
    }),
    lastCall: () => captured,
    callCount: () => count,
  };
}

function baseArgs(): ArbiterAskArgs {
  return {
    tier: "A",
    edg: 2,
    confidence: "MN",
    question: "Should we use approach X or Y for this primitive?",
    options: ["X — simpler, less general", "Y — more general, more code"],
    recommended_default: "X",
    subject_slug: "primitive-design-choice",
  };
}

describe("(a) valid envelope composes + dispatches", () => {
  test("happy path returns send_id and dispatches once", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), baseArgs());
    expect(result.success).toBe(true);
    if (result.success) expect(result.send_id).toBe("ccde-send-0042");
    expect(spy.callCount()).toBe(1);
  });

  test("composed content matches canonical envelope layout", () => {
    const spy = makeSpy();
    handleArbiterAsk(spy.ctx("ccde"), baseArgs());
    const call = spy.lastCall()!;
    expect(call.content).toContain("FROM: CCDE-001");
    expect(call.content).toContain("TO: CoS-001");
    expect(call.content).toContain("SUBJECT: arbiter-ask-primitive-design-choice");
    expect(call.content).toContain("Tier: A");
    expect(call.content).toContain("EDG: 2");
    expect(call.content).toContain("Confidence: MN");
    expect(call.content).toContain("Question: Should we use approach X or Y for this primitive?");
    expect(call.content).toContain("  1. X — simpler, less general");
    expect(call.content).toContain("  2. Y — more general, more code");
    expect(call.content).toContain("Recommended-default: X");
  });
});

describe("(b) CoS self-arbitration", () => {
  test("CoS asking agent is rejected", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("cos"), baseArgs());
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/CoS-001 cannot dispatch arbiter_ask/);
    expect(spy.callCount()).toBe(0);
  });
});

describe("(c) enum validation", () => {
  test("unknown tier rejected", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), tier: "Z" });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/Unknown tier/);
    expect(spy.callCount()).toBe(0);
  });

  test("unknown EDG rejected", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), edg: 9 });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/Unknown EDG/);
    expect(spy.callCount()).toBe(0);
  });

  test("unknown confidence rejected", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), confidence: "XX" });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/Unknown confidence/);
    expect(spy.callCount()).toBe(0);
  });
});

describe("(d) required field validation", () => {
  test("empty question rejected", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), question: "" });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/Question is required/);
  });

  test("whitespace-only question rejected", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), question: "   " });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/Question is required/);
  });

  test("empty options array rejected", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), options: [] });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/At least one option required/);
  });

  test("empty recommended_default rejected", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), recommended_default: "" });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/Recommended-default is required/);
  });
});

describe("(e) subject_slug validation", () => {
  test("uppercase slug rejected", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), subject_slug: "Bad-Slug" });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/kebab-case/);
  });

  test("underscore slug rejected", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), subject_slug: "bad_slug" });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/kebab-case/);
  });

  test("empty slug rejected", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), subject_slug: "" });
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/kebab-case/);
  });

  test("valid kebab-case with digits accepted", () => {
    const spy = makeSpy();
    const result = handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), subject_slug: "oil-012-vloop-question" });
    expect(result.success).toBe(true);
    expect(spy.lastCall()!.content).toContain("SUBJECT: arbiter-ask-oil-012-vloop-question");
  });
});

describe("(f) confidence_pct rendering", () => {
  test("confidence_pct renders as VF (85%)", () => {
    const spy = makeSpy();
    handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), confidence: "VF", confidence_pct: 85 });
    expect(spy.lastCall()!.content).toContain("Confidence: VF (85%)");
  });

  test("missing confidence_pct renders bare confidence", () => {
    const spy = makeSpy();
    handleArbiterAsk(spy.ctx("ccde"), baseArgs());
    expect(spy.lastCall()!.content).toContain("Confidence: MN\n");
  });
});

describe("(g) optional field defaults", () => {
  test("missing optionals render as (none) / (none cited) / no deadline", () => {
    const spy = makeSpy();
    handleArbiterAsk(spy.ctx("ccde"), baseArgs());
    const content = spy.lastCall()!.content;
    expect(content).toContain("Peer consulted (if any): (none)");
    expect(content).toContain("Files searched: (none cited)");
    expect(content).toContain("Precedent (if any): (none)");
    expect(content).toContain("Deadline: no deadline");
  });

  test("provided optionals render verbatim", () => {
    const spy = makeSpy();
    handleArbiterAsk(spy.ctx("ccde"), {
      ...baseArgs(),
      peer_consulted: "CCTO-007 (MN, 70%)",
      files_searched: ["channels/agent/channel.ts", "docs/brain/052626__fdrl-canon.md"],
      precedent: "docs/brain/051726__attestation.md:42",
      deadline: "2026-05-27T12:00:00Z",
    });
    const content = spy.lastCall()!.content;
    expect(content).toContain("Peer consulted (if any): CCTO-007 (MN, 70%)");
    expect(content).toContain("Files searched: channels/agent/channel.ts, docs/brain/052626__fdrl-canon.md");
    expect(content).toContain("Precedent (if any): docs/brain/051726__attestation.md:42");
    expect(content).toContain("Deadline: 2026-05-27T12:00:00Z");
  });
});

describe("(h) meta payload contents", () => {
  test("structured meta includes tier, edg, confidence, subject_slug", () => {
    const spy = makeSpy();
    handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), confidence_pct: 85 });
    const meta = spy.lastCall()!.meta!;
    expect(meta.arbiter_ask).toBe(true);
    expect(meta.tier).toBe("A");
    expect(meta.edg).toBe(2);
    expect(meta.confidence).toBe("MN");
    expect(meta.confidence_pct).toBe(85);
    expect(meta.subject_slug).toBe("primitive-design-choice");
    expect(meta.deadline).toBe(null);
  });

  test("caller-supplied meta is merged in", () => {
    const spy = makeSpy();
    handleArbiterAsk(spy.ctx("ccde"), { ...baseArgs(), meta: { custom_field: "value" } });
    const meta = spy.lastCall()!.meta!;
    expect(meta.custom_field).toBe("value");
    expect(meta.arbiter_ask).toBe(true);
  });
});

describe("(i)(j)(k)(l) dispatch invariants", () => {
  test("target_role always cos", () => {
    const spy = makeSpy();
    handleArbiterAsk(spy.ctx("ccpo"), baseArgs());
    expect(spy.lastCall()!.target_role).toBe("cos");
  });

  test("event_type always arbiter_ask", () => {
    const spy = makeSpy();
    handleArbiterAsk(spy.ctx("ccto"), baseArgs());
    expect(spy.lastCall()!.event_type).toBe("arbiter_ask");
  });

  test("FROM line reflects asking agent role uppercase", () => {
    const spy = makeSpy();
    handleArbiterAsk(spy.ctx("ccpo"), baseArgs());
    expect(spy.lastCall()!.content).toContain("FROM: CCPO-001");
  });

  test("send_id propagated from underlying send", () => {
    let count = 0;
    const ctx: ArbiterContext = {
      agent_role: "scribe", // scribe trying via channel.ts path — should still work at this layer
      send: () => {
        count++;
        return { success: true, send_id: "scribe-send-9999" };
      },
    };
    const result = handleArbiterAsk(ctx, baseArgs());
    expect(result.success).toBe(true);
    if (result.success) expect(result.send_id).toBe("scribe-send-9999");
  });

  test("send failure propagated as arbiter_ask failure", () => {
    const ctx: ArbiterContext = {
      agent_role: "ccde",
      send: () => ({ success: false, error: "downstream send failed" }),
    };
    const result = handleArbiterAsk(ctx, baseArgs());
    expect(result.success).toBe(false);
    if (!result.success) expect(result.error).toMatch(/downstream send failed/);
  });
});
