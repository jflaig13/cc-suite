/**
 * bun test — OIL-211 surface 2: receipt-or-loud queue append (send seams),
 * D1/D2 build-back edition (offset-anchored refinement ruling, 2026-07-08).
 *
 * The bite (2026-07-06): CCPO's wind-down send returned ok while the Scribe
 * queue stayed empty — a SILENT VOID. Then the V-Loop (verdict 5dbaefd)
 * falsified the tail-substring receipt itself: D1 torn-tail glue granted ok
 * on a permanently-lost row; D2 reported a landed oversized row as missing.
 * Contract under test now: success ONLY on offset-anchored read-back of the
 * EXACT line, complete and parseable; every degraded path a loud {ok:false}.
 */

import { test, expect, describe } from "bun:test";
import { mkdtempSync, readFileSync, writeFileSync, existsSync, appendFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { appendLineWithReceipt, readBackAtOffset } from "./send_receipt.ts";

function tmpQueuePath(): string {
  return resolve(mkdtempSync(resolve(tmpdir(), "oil211-")), "queue_scribe.jsonl");
}

describe("appendLineWithReceipt — ok ONLY on offset-anchored read-back", () => {
  test("normal append: line lands on disk AND the receipt confirms it", () => {
    const q = tmpQueuePath();
    const line = JSON.stringify({ event_type: "task_handoff", meta: { send_id: "ccpo-send-x-0001" } });
    const r = appendLineWithReceipt(q, line);
    expect(r.ok).toBe(true);
    expect(r.reason).toContain("read-back");
    expect(readFileSync(q, "utf-8")).toBe(line + "\n");
  });

  test("unwritable path (missing parent dir) → LOUD {ok:false}, never a throw", () => {
    const r = appendLineWithReceipt(
      resolve(tmpdir(), "oil211-noexist", "deeper", "queue_x.jsonl"),
      JSON.stringify({ a: 1 })
    );
    expect(r.ok).toBe(false);
    expect(r.reason).toContain("append failed");
  });

  test("empty / embedded-newline / non-JSON lines are refused loudly (queue integrity)", () => {
    const q = tmpQueuePath();
    expect(appendLineWithReceipt(q, "").ok).toBe(false);
    expect(appendLineWithReceipt(q, '{"a":1}\n{"b":2}').ok).toBe(false);
    // Refinement ruling: a receipt is a PARSEABLE row — a non-JSON line is
    // "delivered" to nobody (every consumer JSON-parses per line).
    const rNotJson = appendLineWithReceipt(q, "not json at all");
    expect(rNotJson.ok).toBe(false);
    expect(rNotJson.reason).toContain("not valid JSON");
    expect(existsSync(q)).toBe(false); // nothing was written
  });

  test("D1 (the 5dbaefd reproduction): append onto a torn newline-less tail is REPAIRED, never glued", () => {
    const q = tmpQueuePath();
    // A dead writer's residue: no trailing newline.
    writeFileSync(q, '{"meta":{"send_id":"torn-');
    const line = JSON.stringify({ content: "fresh", meta: { send_id: "fresh-1" } });
    const r = appendLineWithReceipt(q, line);
    expect(r.ok).toBe(true);
    const lines = readFileSync(q, "utf-8").split("\n").filter((l) => l.length > 0);
    expect(lines.length).toBe(2);
    // The fresh row is its own complete parseable line — a consumer drains it.
    expect(JSON.parse(lines[1]).meta.send_id).toBe("fresh-1");
    // The torn fragment stays isolated (still unparseable, but OURS is not glued into it).
    expect(() => JSON.parse(lines[0])).toThrow();
  });

  test("D1 two-caller walk (CCPO abuse case, both directions): one repair, both rows intact", () => {
    const q = tmpQueuePath();
    writeFileSync(q, '{"half":');
    const l1 = JSON.stringify({ meta: { send_id: "walk-1" } });
    const l2 = JSON.stringify({ meta: { send_id: "walk-2" } });
    expect(appendLineWithReceipt(q, l1).ok).toBe(true);
    expect(appendLineWithReceipt(q, l2).ok).toBe(true);
    const lines = readFileSync(q, "utf-8").split("\n").filter((l) => l.length > 0);
    expect(lines.length).toBe(3);
    expect(JSON.parse(lines[1]).meta.send_id).toBe("walk-1");
    expect(JSON.parse(lines[2]).meta.send_id).toBe("walk-2");
  });

  test("clean tail gets NO spurious repair (over-repair control)", () => {
    const q = tmpQueuePath();
    const l1 = JSON.stringify({ meta: { send_id: "a-1" } });
    const l2 = JSON.stringify({ meta: { send_id: "a-2" } });
    expect(appendLineWithReceipt(q, l1).ok).toBe(true);
    expect(appendLineWithReceipt(q, l2).ok).toBe(true);
    const body = readFileSync(q, "utf-8");
    expect(body).toBe(l1 + "\n" + l2 + "\n"); // no blank line injected
  });

  test("D2 (the 5dbaefd reproduction): a >256KiB row verifies — no scan window exists to miss it", () => {
    const q = tmpQueuePath();
    const line = JSON.stringify({ content: "z".repeat(300 * 1024), meta: { send_id: "big-1" } });
    const r = appendLineWithReceipt(q, line);
    expect(r.ok).toBe(true);
    // And it is genuinely on disk, complete + parseable.
    const rows = readFileSync(q, "utf-8").split("\n").filter((l) => l.length > 0);
    expect(JSON.parse(rows[0]).content.length).toBe(300 * 1024);
  });

  test("ok REQUIRES the read-back: a failing verifier makes an otherwise-successful append LOUD", () => {
    // A healthy filesystem cannot naturally produce append-ok + read-back-miss,
    // so the property is pinned via the injectable verifier: success is
    // conditional on the read-back, not on the append call returning. The
    // skip-the-read-back mutant dies here.
    const q = tmpQueuePath();
    const line = JSON.stringify({ meta: { send_id: "s-void" } });
    const r = appendLineWithReceipt(q, line, () => false);
    expect(r.ok).toBe(false);
    expect(r.reason).toContain("RECEIPT MISSING");
    // And the verifier is genuinely consulted with the appended line + its offset:
    let askedLine: string | null = null;
    let askedOffset: number | null = null;
    const r2 = appendLineWithReceipt(q, line, (_p, offset, expected) => {
      askedOffset = offset;
      askedLine = expected;
      return true;
    });
    expect(r2.ok).toBe(true);
    expect(askedLine).toBe(line);
    // Second append: the recorded offset is the size BEFORE this append
    // (first line + newline), proving the anchor is real, not zero.
    expect(askedOffset).toBe(Buffer.byteLength(line, "utf-8") + 1);
  });

  test("appends compose: multiple sends land in order, each with its own receipt", () => {
    const q = tmpQueuePath();
    const l1 = JSON.stringify({ meta: { send_id: "s-1" } });
    const l2 = JSON.stringify({ meta: { send_id: "s-2" } });
    expect(appendLineWithReceipt(q, l1).ok).toBe(true);
    expect(appendLineWithReceipt(q, l2).ok).toBe(true);
    expect(readFileSync(q, "utf-8")).toBe(l1 + "\n" + l2 + "\n");
  });
});

describe("readBackAtOffset — the offset-anchored receipt check", () => {
  test("exact line at the recorded offset → true; wrong content there → race fallback rules", () => {
    const q = tmpQueuePath();
    const line = JSON.stringify({ meta: { send_id: "here" } });
    writeFileSync(q, line + "\n");
    expect(readBackAtOffset(q, 0, line)).toBe(true);
    expect(readBackAtOffset(q, 0, JSON.stringify({ meta: { send_id: "absent" } }))).toBe(false);
    expect(readBackAtOffset(resolve(tmpdir(), "oil211-none", "q.jsonl"), 0, "x")).toBe(false);
  });

  test("offset race fallback (no-flock bun mirror): a racing writer's row at our offset resolves to a TRUE receipt via forward scan", () => {
    const q = tmpQueuePath();
    const racer = JSON.stringify({ meta: { send_id: "racer" } });
    const ours = JSON.stringify({ meta: { send_id: "ours" } });
    // We recorded offset 0, then the racer's row landed first, then ours.
    writeFileSync(q, racer + "\n" + ours + "\n");
    expect(readBackAtOffset(q, 0, ours)).toBe(true);
  });

  test("substring embedding is NOT a receipt: our line inside ANOTHER row never matches (newline-bounded complete lines only)", () => {
    const q = tmpQueuePath();
    const inner = JSON.stringify({ meta: { send_id: "inner" } });
    // A row that merely CONTAINS our serialized line as a JSON string value.
    const wrapper = JSON.stringify({ content: inner, meta: { send_id: "wrapper" } });
    writeFileSync(q, wrapper + "\n");
    expect(readBackAtOffset(q, 0, inner)).toBe(false);
  });
});
