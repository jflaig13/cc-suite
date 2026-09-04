/**
 * Tests for durable_inbox.ts — cursor-based inbox reader (Ticket B).
 *
 * Required coverage per Scribe spec (fork-side-inbound-queue-drain memo):
 *   (a) durable persistence across a consuming drain by another server
 *   (b) cursor correctness — second drain returns only new events
 *   (c) 9-server race — concurrent consumers each get all events
 *   (d) non-fork push path unaffected — readFromCursor never deletes the file
 *   (e) empty inbox → clean no-op (supplement from original ticket spec)
 *   (f) idempotency — re-draining from same cursor position is safe
 *   (g) dead cursor cleanup — removes files for dead PIDs, preserves live ones
 *
 * CCPO V-Loop F-B1/F-B2 fix coverage (Ticket B second pass):
 *   (h) pushEvent throw holds cursor — failed event retried, not silently lost
 *   (i) malformed JSON skipped — cursor advances, processing continues
 *   (j) torn final line excluded — partial write in progress not consumed
 */

import { test, expect, describe, beforeEach, afterEach } from "bun:test";
import { join } from "path";
import { mkdtempSync, rmSync, writeFileSync, appendFileSync, existsSync } from "fs";
import { tmpdir } from "os";
import { loadCursor, saveCursor, cleanupDeadCursors, readFromCursor, drainQueue, type EventHandler } from "./durable_inbox";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "mise-inbox-test-"));
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function inboxPath(dir: string = tmpDir): string {
  return join(dir, "queue_ccto.jsonl");
}

function writeEvents(events: object[], dir: string = tmpDir): string {
  const path = inboxPath(dir);
  writeFileSync(path, events.map((e) => JSON.stringify(e)).join("\n") + "\n");
  return path;
}

function appendEvent(path: string, event: object): void {
  appendFileSync(path, JSON.stringify(event) + "\n");
}

function cursorPath(pid: number, dir: string = tmpDir): string {
  return join(dir, `cursor_ccto_${pid}.json`);
}

// ---------------------------------------------------------------------------
// (a) Durable persistence across a consuming drain by another server
// ---------------------------------------------------------------------------

describe("durable persistence across consuming drain", () => {
  test("inbox file survives after consumer A drains it; consumer B reads the same events", async () => {
    const events = [
      { event_type: "slack_message", content: "hello from Scribe", meta: {} },
      { event_type: "slack_message", content: "second message", meta: {} },
    ];
    const path = writeEvents(events);

    // Consumer A drains from cursor 0
    const cursorA = cursorPath(1001);
    const { lines: linesA, totalLines } = await readFromCursor(path, 0);
    saveCursor(cursorA, totalLines, 1001, "ccto");

    expect(linesA).toHaveLength(2);
    expect(existsSync(path)).toBe(true); // inbox survives drain

    // Consumer B starts fresh (cursor 0) — sees the same events
    const cursorB = cursorPath(1002);
    const { lines: linesB, totalLines: totalB } = await readFromCursor(path, 0);
    saveCursor(cursorB, totalB, 1002, "ccto");

    expect(linesB).toHaveLength(2);
    expect(linesB[0]).toBe(linesA[0]); // identical content
    expect(linesB[1]).toBe(linesA[1]);
    expect(existsSync(path)).toBe(true); // still survives
  });
});

// ---------------------------------------------------------------------------
// (b) Cursor correctness — second drain returns only new events
// ---------------------------------------------------------------------------

describe("cursor correctness", () => {
  test("subsequent drains by the same consumer return only unread events", async () => {
    const path = writeEvents([
      { event_type: "slack_message", content: "msg-1", meta: {} },
      { event_type: "slack_message", content: "msg-2", meta: {} },
    ]);
    const cursor = cursorPath(9999);

    // First drain: 2 events
    const { lines: first, totalLines: t1 } = await readFromCursor(path, 0);
    saveCursor(cursor, t1, 9999, "ccto");
    expect(first).toHaveLength(2);

    // Append a third event
    appendEvent(path, { event_type: "slack_message", content: "msg-3", meta: {} });

    // Second drain: only the new event
    const c = loadCursor(cursor);
    const { lines: second, totalLines: t2 } = await readFromCursor(path, c.line);
    saveCursor(cursor, t2, 9999, "ccto");
    expect(second).toHaveLength(1);
    expect(JSON.parse(second[0]).content).toBe("msg-3");

    // Third drain: nothing new
    const c2 = loadCursor(cursor);
    const { lines: third } = await readFromCursor(path, c2.line);
    expect(third).toHaveLength(0);
  });

  test("cursor line count matches total lines after full drain", async () => {
    const path = writeEvents([{ content: "a" }, { content: "b" }, { content: "c" }]);
    const cursor = cursorPath(8888);

    const { totalLines } = await readFromCursor(path, 0);
    saveCursor(cursor, totalLines, 8888, "ccto");

    expect(loadCursor(cursor).line).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// (c) 9-server race — concurrent consumers each get all events
// ---------------------------------------------------------------------------

describe("concurrent consumer race safety", () => {
  test("9 simultaneous consumers starting from cursor 0 all receive all events", async () => {
    const events = Array.from({ length: 10 }, (_, i) => ({
      event_type: "slack_message",
      content: `event-${i}`,
      meta: {},
    }));
    const path = writeEvents(events);

    // All 9 instances drain simultaneously
    const results = await Promise.all(
      Array.from({ length: 9 }, async (_, i) => {
        const { lines, totalLines } = await readFromCursor(path, 0);
        saveCursor(cursorPath(2000 + i), totalLines, 2000 + i, "ccto");
        return lines;
      })
    );

    // Every consumer gets all 10 events — no starvation, no race loss
    for (const lines of results) {
      expect(lines).toHaveLength(10);
    }
    // Inbox still present (none of the 9 deleted it)
    expect(existsSync(path)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// (d) Non-fork push path unaffected — readFromCursor never deletes
// ---------------------------------------------------------------------------

describe("non-fork push path: readFromCursor never deletes the inbox", () => {
  test("inbox file persists after any number of readFromCursor calls", async () => {
    const path = writeEvents([{ event_type: "slack_message", content: "hi", meta: {} }]);

    // Drain 5 times from various cursor positions — none should delete the file
    for (let i = 0; i < 5; i++) {
      await readFromCursor(path, 0);
      expect(existsSync(path)).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// (e) Empty inbox → clean no-op
// ---------------------------------------------------------------------------

describe("empty inbox handling", () => {
  test("non-existent inbox returns empty result without error", async () => {
    const { lines, totalLines } = await readFromCursor(
      join(tmpDir, "queue_ccto_does_not_exist.jsonl"),
      0
    );
    expect(lines).toHaveLength(0);
    expect(totalLines).toBe(0);
  });

  test("existing but empty inbox returns empty result", async () => {
    const path = inboxPath();
    writeFileSync(path, "");
    const { lines, totalLines } = await readFromCursor(path, 0);
    expect(lines).toHaveLength(0);
    expect(totalLines).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// (f) Idempotency — re-draining from the same cursor position is safe
// ---------------------------------------------------------------------------

describe("idempotency", () => {
  test("draining from cursor N twice returns the same events both times", async () => {
    const path = writeEvents([
      { content: "x" },
      { content: "y" },
      { content: "z" },
    ]);

    const { lines: first } = await readFromCursor(path, 1); // start from line 1
    const { lines: second } = await readFromCursor(path, 1); // same cursor, same result
    expect(first).toEqual(second);
    expect(first).toHaveLength(2); // lines 1 and 2 (0-indexed)
  });
});

// ---------------------------------------------------------------------------
// (g) Dead cursor cleanup
// ---------------------------------------------------------------------------

describe("dead cursor cleanup", () => {
  test("cursor files for dead PIDs are removed; own cursor is preserved", () => {
    const myPid = process.pid;
    const deadPid = 99999999; // astronomically unlikely to be alive

    const myCursorPath = cursorPath(myPid);
    const deadCursorPath = cursorPath(deadPid);

    writeFileSync(myCursorPath, JSON.stringify({ line: 10, pid: myPid, role: "ccto" }));
    writeFileSync(deadCursorPath, JSON.stringify({ line: 3, pid: deadPid, role: "ccto" }));

    cleanupDeadCursors(tmpDir, "ccto", myPid);

    expect(existsSync(myCursorPath)).toBe(true);   // own cursor preserved
    expect(existsSync(deadCursorPath)).toBe(false); // dead cursor removed
  });

  test("cleanup is a no-op when no cursor files exist", () => {
    // Should not throw
    expect(() => cleanupDeadCursors(tmpDir, "ccto", process.pid)).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// (h) F-B1 fix: pushEvent throw holds cursor (failed event not silently lost)
// ---------------------------------------------------------------------------

describe("drainQueue: pushEvent throw holds cursor at failed event", () => {
  test("cursor held at line where pushEvent threw; re-drain retries from that line", async () => {
    const path = writeEvents([
      { event_type: "slack_message", content: "msg-1", meta: {} },
      { event_type: "slack_message", content: "msg-2", meta: {} },
      { event_type: "slack_message", content: "msg-3", meta: {} },
    ]);
    const cursor = cursorPath(7777);
    let callCount = 0;

    // First drain: first event succeeds, second throws, third never reached
    const failOn2nd: EventHandler = async () => {
      callCount++;
      if (callCount >= 2) throw new Error("push failed");
    };
    const { processed: p1, held: h1 } = await drainQueue(path, cursor, 7777, "ccto", failOn2nd);
    expect(p1).toBe(1);     // only msg-1 processed
    expect(h1).toBe(true);  // stopped early
    expect(loadCursor(cursor).line).toBe(1); // cursor at 1, not 3

    // Re-drain: retries from msg-2, now succeeds
    const received: string[] = [];
    const alwaysOk: EventHandler = async (_, content) => { received.push(content); };
    const { processed: p2, held: h2 } = await drainQueue(path, cursor, 7777, "ccto", alwaysOk);
    expect(p2).toBe(2);    // msg-2 and msg-3
    expect(h2).toBe(false);
    expect(received).toEqual(["msg-2", "msg-3"]);
    expect(loadCursor(cursor).line).toBe(3); // cursor now at end
  });

  test("all events fail: cursor stays at 0, no events lost", async () => {
    const path = writeEvents([
      { event_type: "slack_message", content: "event-A", meta: {} },
    ]);
    const cursor = cursorPath(7778);

    const alwaysFail: EventHandler = async () => { throw new Error("always fails"); };
    const { processed, held } = await drainQueue(path, cursor, 7778, "ccto", alwaysFail);
    expect(processed).toBe(0);
    expect(held).toBe(true);
    expect(loadCursor(cursor).line).toBe(0); // cursor unmoved

    // Event survives and is retried next drain
    const received: string[] = [];
    const alwaysOk: EventHandler = async (_, content) => { received.push(content); };
    await drainQueue(path, cursor, 7778, "ccto", alwaysOk);
    expect(received).toEqual(["event-A"]);
  });
});

// ---------------------------------------------------------------------------
// (i) F-B1 fix: malformed JSON skipped, cursor advances, processing continues
// ---------------------------------------------------------------------------

describe("drainQueue: malformed JSON line is skipped", () => {
  test("malformed line between valid events is skipped; valid events all received", async () => {
    const path = join(tmpDir, "queue_ccto_malformed.jsonl");
    writeFileSync(
      path,
      '{"event_type":"slack_message","content":"good-1","meta":{}}\n' +
      'NOT_VALID_JSON\n' +
      '{"event_type":"slack_message","content":"good-2","meta":{}}\n'
    );
    const cursor = cursorPath(6666);
    const received: string[] = [];
    const handler: EventHandler = async (_, content) => { received.push(content); };

    const { processed, held } = await drainQueue(path, cursor, 6666, "ccto", handler);
    expect(received).toEqual(["good-1", "good-2"]);
    expect(processed).toBe(3); // 2 valid + 1 malformed (skipped but advanced)
    expect(held).toBe(false);
    expect(loadCursor(cursor).line).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// (j) F-B2 fix: torn final line (no trailing \n) excluded from results
// ---------------------------------------------------------------------------

describe("readFromCursor: torn final line exclusion", () => {
  test("file without trailing newline excludes partial last line", async () => {
    const path = join(tmpDir, "queue_ccto_torn.jsonl");
    // Simulate a mid-write: two complete lines + one partial (no trailing \n)
    writeFileSync(path, '{"content":"complete-1"}\n{"content":"complete-2"}\n{"content":"par');
    const { lines, totalLines } = await readFromCursor(path, 0);
    expect(lines).toHaveLength(2);
    expect(JSON.parse(lines[0]).content).toBe("complete-1");
    expect(JSON.parse(lines[1]).content).toBe("complete-2");
    expect(totalLines).toBe(2); // partial line not counted
  });

  test("file with trailing newline includes all complete lines", async () => {
    const path = join(tmpDir, "queue_ccto_terminated.jsonl");
    writeFileSync(path, '{"content":"event-1"}\n{"content":"event-2"}\n');
    const { lines, totalLines } = await readFromCursor(path, 0);
    expect(lines).toHaveLength(2);
    expect(totalLines).toBe(2);
  });

  test("file with only a partial line (no newline at all) returns empty", async () => {
    const path = join(tmpDir, "queue_ccto_only_partial.jsonl");
    writeFileSync(path, '{"content":"partial_only_no_newline');
    const { lines, totalLines } = await readFromCursor(path, 0);
    expect(lines).toHaveLength(0);
    expect(totalLines).toBe(0);
  });

  test("once partial write completes, next drain picks up the now-complete line", async () => {
    const path = join(tmpDir, "queue_ccto_completes.jsonl");
    // First write: partial (no trailing \n)
    writeFileSync(path, '{"content":"event-1"}\n{"content":"event-2-par');
    const cursor = cursorPath(5555);

    const { lines: first, totalLines: t1 } = await readFromCursor(path, 0);
    saveCursor(cursor, t1, 5555, "ccto");
    expect(first).toHaveLength(1); // partial excluded

    // Write completes (overwrite with full content + new event)
    writeFileSync(path, '{"content":"event-1"}\n{"content":"event-2-complete"}\n{"content":"event-3"}\n');

    const c = loadCursor(cursor);
    const { lines: second } = await readFromCursor(path, c.line);
    expect(second).toHaveLength(2); // event-2-complete and event-3
    expect(JSON.parse(second[0]).content).toBe("event-2-complete");
    expect(JSON.parse(second[1]).content).toBe("event-3");
  });
});
