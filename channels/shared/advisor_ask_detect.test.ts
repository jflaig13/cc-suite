// SPDX-License-Identifier: MPL-2.0
import { describe, expect, test } from "bun:test";
import { isAdvisorAsk } from "./advisor_ask_detect";

describe("isAdvisorAsk", () => {
  test("matches an agent_direct send with a SUBJECT: ADVISOR: line", () => {
    const content = "FROM: CCDE-001\nTO: CCTO-007\nSUBJECT: ADVISOR: branch reachability\n\nbody";
    expect(isAdvisorAsk("agent_direct", content)).toBe(true);
  });

  test("rejects task_handoff even with an ADVISOR subject line", () => {
    const content = "FROM: CCDE-001\nTO: CCTO-007\nSUBJECT: ADVISOR: x\n\nbody";
    expect(isAdvisorAsk("task_handoff", content)).toBe(false);
  });

  test("rejects a plain agent_direct with no ADVISOR subject", () => {
    const content = "FROM: CCDE-001\nTO: CCTO-007\nSUBJECT: task-complete-u1\n\nbody";
    expect(isAdvisorAsk("agent_direct", content)).toBe(false);
  });

  test("rejects ADVISOR mentioned in the body but not the SUBJECT line", () => {
    const content = "FROM: CCDE-001\nTO: CCTO-007\nSUBJECT: normal-update\n\nan ADVISOR: fake line in the body";
    expect(isAdvisorAsk("agent_direct", content)).toBe(false);
  });

  test("matches when SUBJECT: ADVISOR: is not the first line", () => {
    const content = "FROM: CCDE-001\nTO: CCTO-007\nSUBJECT: ADVISOR: mid-file\nCC: Scribe-002\n\nbody";
    expect(isAdvisorAsk("agent_direct", content)).toBe(true);
  });

  test("rejects lowercase advisor: (case-sensitive, matches the protocol's exact convention)", () => {
    const content = "FROM: CCDE-001\nTO: CCTO-007\nSUBJECT: advisor: x\n\nbody";
    expect(isAdvisorAsk("agent_direct", content)).toBe(false);
  });

  test("rejects other event types entirely", () => {
    const content = "FROM: CCDE-001\nTO: CCTO-007\nSUBJECT: ADVISOR: x\n\nbody";
    expect(isAdvisorAsk("arbiter_ask", content)).toBe(false);
    expect(isAdvisorAsk("audit_request", content)).toBe(false);
    expect(isAdvisorAsk("reply_classification", content)).toBe(false);
  });

  test("handles empty/undefined content without throwing", () => {
    expect(isAdvisorAsk("agent_direct", "")).toBe(false);
  });

  test("requires the ADVISOR: prefix immediately after SUBJECT: (whitespace-tolerant)", () => {
    const tight = "SUBJECT:ADVISOR: no space";
    const spaced = "SUBJECT:   ADVISOR: extra space";
    expect(isAdvisorAsk("agent_direct", tight)).toBe(true);
    expect(isAdvisorAsk("agent_direct", spaced)).toBe(true);
  });

  test("rejects SUBJECT: text that merely contains the word ADVISOR later", () => {
    const content = "SUBJECT: task-complete-advisor-tooling\n\nbody";
    expect(isAdvisorAsk("agent_direct", content)).toBe(false);
  });
});
