/**
 * Durable cursor-based inbox reader.
 *
 * Replaces the consume-and-delete drainFile() pattern for the shared role
 * queue (channels/shared/queue_{role}.jsonl). The Slack poller continues to
 * appendFileSync to that file; this module provides non-destructive reads so
 * multiple channel.ts instances (interactive sessions + agent-view forks) each
 * maintain their own cursor and see all events, regardless of which instance
 * started first.
 *
 * Cursor file: channels/shared/cursor_{role}_{pid}.json
 *   { "line": N, "pid": PID, "role": ROLE }
 *   — tracks how many JSONL lines this instance has already processed.
 *   — one file per running channel.ts process.
 *   — cleaned up for dead PIDs on startup.
 *
 * The inbox file (queue_{role}.jsonl) grows unbounded (queues reached 1000-1700+
 * events in practice — the original "no rotation needed" assumption was wrong).
 * A fresh pid now seeds from the role high-water mark (roleCheckpointFile /
 * drainQueue) so growth no longer causes a cold-start replay "wall"; bounding the
 * file itself (rotation/trim with atomic cursor adjustment) is a separate follow-up.
 */

import { existsSync, readdirSync, readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { createHash } from "node:crypto";

export interface Cursor {
  line: number;
  pid: number;
  role: string;
  // G-e: content-hash of the last-processed line. A line-count cursor CANNOT detect a
  // truncate-then-regrow-PAST-cursor; the anchor catches it by CONTENT so no events drop.
  anchor?: string;
}

/** Load cursor from disk. Returns { line: 0 } if missing or corrupt. */
export function loadCursor(cursorFile: string): Cursor {
  try {
    const raw = readFileSync(cursorFile, "utf-8");
    const parsed = JSON.parse(raw);
    return {
      line: typeof parsed.line === "number" ? parsed.line : 0,
      pid: typeof parsed.pid === "number" ? parsed.pid : 0,
      role: typeof parsed.role === "string" ? parsed.role : "",
      anchor: typeof parsed.anchor === "string" ? parsed.anchor : "",
    };
  } catch {
    return { line: 0, pid: 0, role: "", anchor: "" };
  }
}

/** Persist cursor to disk. Silently swallows write errors. anchor (G-e) = content-hash of
 *  the last-processed line, so a truncate-then-regrow leaving line<=qlen is still caught. */
export function saveCursor(cursorFile: string, line: number, pid: number, role: string, anchor = ""): void {
  try {
    writeFileSync(cursorFile, JSON.stringify({ line, pid, role, anchor }));
  } catch {}
}

/** Torn-guarded list of complete (newline-terminated) non-blank lines — shared basis for
 *  readFromCursor + the drain's content-anchor check. */
export async function completeLines(inboxFile: string): Promise<string[]> {
  if (!existsSync(inboxFile)) return [];
  const raw = await Bun.file(inboxFile).text();
  const safe = raw.endsWith("\n") ? raw : raw.slice(0, raw.lastIndexOf("\n") + 1);
  return safe.split("\n").filter((l) => l.trim().length > 0);
}

/** Short stable content-hash of a queue line (G-e cursor content anchor). */
export function anchorOf(line: string): string {
  return createHash("sha1").update(line, "utf-8").digest("hex").slice(0, 16);
}

/**
 * Role high-water mark file: cursor_{role}_role.json — the highest queue line
 * ANY window of the role has processed. A fresh pid (relaunched window) with no
 * own cursor seeds from this instead of line 0, so it resumes where the role
 * left off (catching gap events) rather than replaying the entire history (the
 * cold-start "wall" bug). Per-pid cursors are unchanged; this is a seed floor
 * for NEW pids only, never a cap on a live pid's own cursor.
 */
export function roleCheckpointFile(sharedDir: string, role: string): string {
  return resolve(sharedDir, `cursor_${role}_role.json`);
}

/** Load the role high-water mark line. Returns 0 if missing or corrupt. */
export function loadRoleCheckpoint(file: string): number {
  try {
    const parsed = JSON.parse(readFileSync(file, "utf-8"));
    return typeof parsed.line === "number" ? parsed.line : 0;
  } catch {
    return 0;
  }
}

/**
 * Advance the role high-water mark — MONOTONIC by default (never moves backward, so a
 * slow window cannot rewind a fast peer's progress). force=true bypasses the monotonic
 * guard to RESET the mark after a truncation/rotation (CF1 desync recovery — the old
 * high-water mark is invalid once the queue is truncated below it). Swallows write errors.
 */
export function saveRoleCheckpoint(file: string, line: number, force = false): void {
  try {
    if (force || line > loadRoleCheckpoint(file)) writeFileSync(file, JSON.stringify({ line }));
  } catch {}
}

/**
 * Remove cursor files for dead PIDs. Called once at channel.ts startup to
 * resolve the resource leak from ~9 orphaned bun channel.ts server processes
 * launched by prior agent-view forks.
 *
 * Uses process.kill(pid, 0) — zero-signal probe: throws ESRCH if PID is dead,
 * no-op if alive.
 */
export function cleanupDeadCursors(sharedDir: string, role: string, myPid: number): void {
  try {
    const pattern = new RegExp(`^cursor_${role}_(\\d+)\\.json$`);
    const files = readdirSync(sharedDir).filter((f) => pattern.test(f));
    for (const f of files) {
      const match = f.match(/(\d+)\.json$/);
      if (!match) continue;
      const pid = parseInt(match[1]!, 10);
      if (pid === myPid) continue;
      try {
        process.kill(pid, 0); // alive → no-op; dead → throws
      } catch {
        try {
          unlinkSync(resolve(sharedDir, f));
        } catch {}
      }
    }
  } catch {}
}

/**
 * Read lines from the inbox file starting at cursorLine (zero-indexed count of
 * already-processed lines). Returns new lines and the updated total line count
 * to persist as the new cursor.
 *
 * The inbox file is NEVER modified or deleted by this function.
 *
 * Torn-line guard (F-B2): if the file does not end with \n the final entry is a
 * partial write in progress and is excluded. The poller always appends complete
 * newline-terminated lines; a missing trailing \n means a write is still in
 * flight. The partial line will be readable on the next drain cycle once the
 * write completes.
 */
export async function readFromCursor(
  inboxFile: string,
  cursorLine: number
): Promise<{ lines: string[]; totalLines: number }> {
  const allLines = await completeLines(inboxFile);
  return {
    lines: allLines.slice(cursorLine),
    totalLines: allLines.length,
  };
}

/**
 * Callback type for processing a single decoded queue event. Throwing causes
 * drainQueue to hold the cursor at the failed event (retry on next cycle).
 */
export type EventHandler = (
  event_type: string,
  content: string,
  meta: Record<string, string>
) => Promise<void>;

export interface DrainResult {
  processed: number;
  held: boolean; // true when a pushEvent throw stopped processing early
}

/**
 * Drain the inbox from the current cursor position, calling onEvent for each
 * decoded event. Advances the cursor only for events that were successfully
 * handled.
 *
 * F-B1 fix: the catch blocks are intentionally separated.
 *   - JSON.parse failure: unrecoverable bad data — advance past the line, continue.
 *   - onEvent throw: transient push failure — hold cursor here, stop processing.
 *     The caller receives held=true and can log appropriately. On the next drain
 *     cycle, the same event will be retried.
 *
 * Extracted from channel.ts drainRoleQueue() to make the push-error / cursor-hold
 * behavior independently testable without starting an MCP server.
 */
export async function drainQueue(
  inboxFile: string,
  cursorFile: string,
  pid: number,
  role: string,
  onEvent: EventHandler
): Promise<DrainResult> {
  // Cold-start replay fix: a relaunched window is a NEW pid with no own cursor.
  // The old loadCursor line-0 fallback replayed the ENTIRE unbounded queue (the
  // "wall" on every init). Seed instead by precedence:
  //   1. own cursor exists            -> resume from this pid's position
  //   2. role high-water mark exists  -> resume from where the role left off
  //                                      (catches gap events, no history replay)
  //   3. neither (genuinely fresh role) -> process from line 0; a brand-new queue's
  //      events are REAL pending, never dropped. The wall on EXISTING busy queues is
  //      bounded by the role checkpoint (advanced by drains / a one-time migration).
  const roleCpFile = roleCheckpointFile(dirname(cursorFile), role);
  const ownCursor = existsSync(cursorFile) ? loadCursor(cursorFile) : null;
  let startLine: number;
  if (ownCursor !== null) {
    startLine = ownCursor.line;               // 1. own per-pid cursor
  } else if (existsSync(roleCpFile)) {
    startLine = loadRoleCheckpoint(roleCpFile); // 2. role high-water mark (no history replay)
  } else {
    startLine = 0;                            // 3. genuinely fresh role -> real pending, no drop
  }
  const allLines = await completeLines(inboxFile);
  const total = allLines.length;

  // DESYNC detection — the queue was truncated/rotated under the cursor:
  //   (CF1) startLine > total -> cursor beyond the queue (truncate-to-smaller); OR
  //   (G-e) the own cursor's CONTENT ANCHOR no longer matches allLines[startLine-1] ->
  //         truncate-then-regrow-PAST-cursor (startLine <= total, but content diverged; a
  //         line-count cursor cannot see this and would SILENTLY DROP the new [0..startLine)).
  // On either: log loud + resync from 0 (DELIVER pending, not 0) + FORCE-reset the checkpoint.
  let desync = startLine > total;
  if (!desync && ownCursor !== null && ownCursor.anchor && startLine > 0) {
    const prev = startLine - 1 < total ? allLines[startLine - 1] : undefined;
    if (prev === undefined || anchorOf(prev) !== ownCursor.anchor) desync = true;
  }
  if (desync) {
    console.error(`[durable_inbox] CURSOR DESYNC role=${role}: cursor ${startLine} vs qlen ${total} — queue truncated/rotated (content-anchor or line-count); resyncing from 0 so pending events deliver`);
    startLine = 0;
    saveRoleCheckpoint(roleCpFile, 0, true);
  }

  const lines = allLines.slice(startLine);
  if (lines.length === 0) {
    // Persist our position (+ anchor + seed the role checkpoint) even with nothing to do.
    const anchor = startLine > 0 && startLine - 1 < total ? anchorOf(allLines[startLine - 1]!) : "";
    saveCursor(cursorFile, startLine, pid, role, anchor);
    saveRoleCheckpoint(roleCpFile, startLine);
    return { processed: 0, held: false };
  }
  let processed = 0;
  let held = false;
  for (const line of lines) {
    let event: { event_type?: string; content?: string; meta?: Record<string, string> };
    try {
      event = JSON.parse(line);
    } catch {
      processed++; // malformed JSON — advance past (unrecoverable bad data)
      continue;
    }
    try {
      await onEvent(event.event_type || "slack_message", event.content || "", event.meta || {});
      processed++;
    } catch {
      held = true; // push failed — hold cursor, retry this event next drain cycle
      break;
    }
  }
  const newLine = startLine + processed;
  const newAnchor = newLine > 0 && newLine - 1 < allLines.length ? anchorOf(allLines[newLine - 1]!) : "";
  saveCursor(cursorFile, newLine, pid, role, newAnchor);
  saveRoleCheckpoint(roleCpFile, newLine); // advance the role high-water mark
  return { processed, held };
}
