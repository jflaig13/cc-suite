// SPDX-License-Identifier: MPL-2.0
/**
 * Cold-start replay fix — acceptance tests (spec:
 * cc_execs/memos/ccde/20260626__durable-inbox-cold-start-replay-FIX-SPEC.md).
 * Verifies a fresh pid no longer replays the entire queue, with no event loss.
 */
import { test, expect, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { tmpdir } from "node:os";
import { drainQueue, loadRoleCheckpoint, roleCheckpointFile, loadCursor, saveCursor, saveRoleCheckpoint } from "./durable_inbox";

let dir: string;
const ROLE = "testrole";
const qFile = () => resolve(dir, `queue_${ROLE}.jsonl`);
const cFile = (pid: number) => resolve(dir, `cursor_${ROLE}_${pid}.json`);
const cpFile = () => roleCheckpointFile(dir, ROLE);

function seedQueue(n: number) {
  const lines = Array.from({ length: n }, (_, i) =>
    JSON.stringify({ event_type: "slack_message", content: `e${i}`, meta: {} })
  );
  writeFileSync(qFile(), lines.length ? lines.join("\n") + "\n" : "");
}
function appendQueue(contents: string[]) {
  const lines = contents.map((c) => JSON.stringify({ event_type: "slack_message", content: c, meta: {} }));
  writeFileSync(qFile(), readFileSync(qFile(), "utf-8") + lines.join("\n") + "\n");
}

beforeEach(() => { dir = mkdtempSync(resolve(tmpdir(), "durinbox-")); });
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

test("genuinely fresh role (no cursor, no checkpoint): processes from 0 + seeds checkpoint", async () => {
  // A brand-new role with no checkpoint processes its queue (no event loss). The
  // WALL on EXISTING busy queues is prevented by the migration (next test), not by
  // changing this default — which would lose events on a genuinely new queue.
  seedQueue(5);
  const seen: string[] = [];
  const r = await drainQueue(qFile(), cFile(101), 101, ROLE, async (_t, c) => { seen.push(c); });
  expect(r.processed).toBe(5);
  expect(seen).toEqual(["e0", "e1", "e2", "e3", "e4"]);
  expect(loadRoleCheckpoint(cpFile())).toBe(5); // checkpoint now seeded for the next window
});

test("MIGRATION = no wall: a relaunch onto a checkpointed 1700-event queue replays nothing", async () => {
  seedQueue(1700);
  writeFileSync(cpFile(), JSON.stringify({ line: 1700 })); // migration pre-seeded at current end
  const seen: string[] = [];
  const r = await drainQueue(qFile(), cFile(999), 999, ROLE, async (_t, c) => { seen.push(c); });
  expect(r.processed).toBe(0);    // zero replay — this is the fix the founder needs
  expect(seen.length).toBe(0);
});

test("fresh pid resumes from the role checkpoint, not line 0 (bounded cold-start)", async () => {
  seedQueue(1700);
  writeFileSync(cpFile(), JSON.stringify({ line: 1690 })); // a prior window got to 1690
  const seen: string[] = [];
  const r = await drainQueue(qFile(), cFile(202), 202, ROLE, async (_t, c) => { seen.push(c); });
  expect(r.processed).toBe(10);                // only the 10 new, not 1700
  expect(seen).toEqual(["e1690","e1691","e1692","e1693","e1694","e1695","e1696","e1697","e1698","e1699"]);
});

test("NO LOSS: events appended after the checkpoint are caught by the next fresh window", async () => {
  seedQueue(50);
  writeFileSync(cpFile(), JSON.stringify({ line: 50 }));
  appendQueue(["g0","g1","g2","g3","g4"]);     // arrived while no window was live
  const seen: string[] = [];
  const r = await drainQueue(qFile(), cFile(303), 303, ROLE, async (_t, c) => { seen.push(c); });
  expect(r.processed).toBe(5);
  expect(seen).toEqual(["g0","g1","g2","g3","g4"]);
});

test("own pid cursor beats the role checkpoint (a live window keeps its position)", async () => {
  seedQueue(100);
  writeFileSync(cFile(404), JSON.stringify({ line: 30, pid: 404, role: ROLE }));
  writeFileSync(cpFile(), JSON.stringify({ line: 90 }));
  const seen: string[] = [];
  const r = await drainQueue(qFile(), cFile(404), 404, ROLE, async (_t, c) => { seen.push(c); });
  expect(r.processed).toBe(70);                // from its own 30, not the role's 90
  expect(seen[0]).toBe("e30");
});

test("role checkpoint advances monotonically to the max", async () => {
  seedQueue(100);
  writeFileSync(cpFile(), JSON.stringify({ line: 80 }));
  await drainQueue(qFile(), cFile(505), 505, ROLE, async () => {});
  expect(loadRoleCheckpoint(cpFile())).toBe(100);
  // a stale lower write must NOT rewind it
  writeFileSync(cFile(506), JSON.stringify({ line: 10, pid: 506, role: ROLE }));
  await drainQueue(qFile(), cFile(506), 506, ROLE, async () => {});
  expect(loadRoleCheckpoint(cpFile())).toBe(100);
});

test("F-B1 preserved: an onEvent throw HOLDS the cursor at the failed event", async () => {
  seedQueue(5);
  writeFileSync(cpFile(), JSON.stringify({ line: 0 })); // process from the start
  let count = 0;
  const r = await drainQueue(qFile(), cFile(606), 606, ROLE, async () => {
    count++; if (count === 3) throw new Error("push fail");
  });
  expect(r.held).toBe(true);
  expect(r.processed).toBe(2);                 // e0,e1 ok; held at e2
  expect(JSON.parse(readFileSync(cFile(606), "utf-8")).line).toBe(2);
});

test("malformed JSON line is skipped, not held (advance past bad data)", async () => {
  writeFileSync(qFile(), "not-json\n" + JSON.stringify({ event_type: "slack_message", content: "ok", meta: {} }) + "\n");
  writeFileSync(cpFile(), JSON.stringify({ line: 0 }));
  const seen: string[] = [];
  const r = await drainQueue(qFile(), cFile(707), 707, ROLE, async (_t, c) => { seen.push(c); });
  expect(r.held).toBe(false);
  expect(r.processed).toBe(2);                 // skipped the bad line, processed the good one
  expect(seen).toEqual(["ok"]);
});

// --- CF1: CURSOR DESYNC recovery (stale-HIGH cursor after truncation) ---

test("CF1 desync: a stale-high own cursor resyncs from 0 and DELIVERS (not 0)", async () => {
  seedQueue(12); // queue truncated to 12
  writeFileSync(cFile(1), JSON.stringify({ line: 1723, pid: 1, role: ROLE })); // stale-HIGH
  const seen: string[] = [];
  const r = await drainQueue(qFile(), cFile(1), 1, ROLE, async (_t, c) => { seen.push(c); });
  expect(r.processed).toBe(12);        // DELIVERED, not 0
  expect(seen.length).toBe(12);
  expect(loadCursor(cFile(1)).line).toBe(12);
});

test("CF1 desync: a stale-high role checkpoint force-resets + delivers", async () => {
  seedQueue(5);
  writeFileSync(cpFile(), JSON.stringify({ line: 999 })); // stale-high checkpoint, no per-pid cursor
  const seen: string[] = [];
  const r = await drainQueue(qFile(), cFile(2), 2, ROLE, async (_t, c) => { seen.push(c); });
  expect(r.processed).toBe(5);
  expect(loadRoleCheckpoint(cpFile())).toBe(5); // 999 -> 5 (force-reset)
});

test("CF1 saveRoleCheckpoint force bypasses the monotonic guard", () => {
  const f = cpFile();
  writeFileSync(f, JSON.stringify({ line: 999 }));
  saveRoleCheckpoint(f, 5);        // monotonic: 5 < 999 -> NO write
  expect(loadRoleCheckpoint(f)).toBe(999);
  saveRoleCheckpoint(f, 5, true);  // force -> reset
  expect(loadRoleCheckpoint(f)).toBe(5);
});

// --- G-e: CONTENT ANCHOR catches truncate-then-regrow-PAST-cursor (line-count blind) ---

test("G-e truncate-then-regrow-past-cursor is caught by the content anchor + delivers all", async () => {
  seedQueue(20);
  const seen: string[] = [];
  await drainQueue(qFile(), cFile(1), 1, ROLE, async (_t, c) => { seen.push(c); }); // cursor=20 + anchor
  expect(seen.length).toBe(20);
  // regrow to 25 DIFFERENT lines: cursor.line=20 <= qlen=25 (line-count blind), content diverged.
  const lines = Array.from({ length: 25 }, (_, i) =>
    JSON.stringify({ event_type: "slack_message", content: `NEW${i}`, meta: {} }));
  writeFileSync(qFile(), lines.join("\n") + "\n");
  const seen2: string[] = [];
  const r = await drainQueue(qFile(), cFile(1), 1, ROLE, async (_t, c) => { seen2.push(c); });
  expect(r.processed).toBe(25);
  expect(seen2).toEqual(Array.from({ length: 25 }, (_, i) => `NEW${i}`));
});

test("G-e no false desync on a plain append (content at cursor-1 unchanged)", async () => {
  seedQueue(10);
  const seen: string[] = [];
  await drainQueue(qFile(), cFile(1), 1, ROLE, async (_t, c) => { seen.push(c); });
  appendQueue(["a", "b"]);
  const seen2: string[] = [];
  const r = await drainQueue(qFile(), cFile(1), 1, ROLE, async (_t, c) => { seen2.push(c); });
  expect(r.processed).toBe(2);
  expect(seen2).toEqual(["a", "b"]);
});
