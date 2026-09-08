// SPDX-License-Identifier: MPL-2.0
/**
 * send_receipt.ts — receipt-or-loud queue append for the bun send seams
 * (OIL-211 surface 2, CCTO design ef2bb1d / CCTO_design__20260706;
 * D1/D2 build-back per the 2026-07-08 offset-anchored refinement ruling).
 *
 * The contract (refined): a send returns success ONLY on proof of durable
 * arrival of the EXACT row, COMPLETE and PARSEABLE — never substring
 * presence. The CCPO OIL-211 verify (verdict 5dbaefd) falsified the original
 * tail-substring receipt two ways:
 *   D1 — torn-tail glue: an append onto a newline-less tail matched the
 *        substring while the glued line was unparseable → ok + permanent loss.
 *   D2 — oversized row: a landed row bigger than the scan window read back
 *        as missing → false SEND FAILED → guaranteed duplicate.
 * The offset-anchored read-back kills both by construction: the tail is
 * newline-repaired BEFORE the append (glue impossible), the append offset is
 * recorded, and the receipt is byte-equality + JSON-parse of exactly the
 * appended line at that offset — no scan window at all.
 *
 * Concurrency (the CCPO/CCTO binding abuse cases, adopted 2026-07-08): the
 * Python mirrors hold flock across the full record-size → append →
 * read-at-offset triple. Bun's node:fs exposes NO flock primitive, so this
 * mirror meets the ruling's intent structurally instead:
 *   (1) the repair-\n + line + \n are written in ONE atomic O_APPEND write
 *       syscall (a regular-file O_APPEND write is atomic in offset AND data),
 *       so a concurrent writer can interleave BETWEEN rows, never inside one;
 *   (2) the receipt fast path is byte-equality at the recorded offset;
 *   (3) if a concurrent writer won the offset race, a bounded forward scan
 *       from the recorded offset looks for the exact line as a COMPLETE
 *       newline-bounded row — the race resolves to a true receipt or a loud
 *       failure, NEVER a false ok;
 *   (4) the torn-tail repair is safe without a lock because every fleet
 *       writer emits a row as one atomic append ending in \n — a newline-less
 *       tail can only be a DEAD writer's residue, never a live mid-row write.
 * This deviation from the literal lock wording is stated here and in the
 * build-back memo — honest bound, not a hidden gap.
 *
 * This is the bun mirror of scripts/receipt_or_loud.py::append_row_with_receipt
 * (mirrors, does not import — the handoff_ledger mirrors-not-imports
 * precedent; a defect in one implementation must not be invisible to the
 * other's tests). Consumers: channels/agent/channel.ts handleSend +
 * channels/scribe/webhook.ts handleScribeSend.
 */

import {
  openSync,
  readSync,
  writeSync,
  closeSync,
  fstatSync,
  fsyncSync,
} from "node:fs";

// Forward-scan bound for the offset-race fallback: from the recorded offset,
// how far past our own row we are willing to look for it as a complete line.
// Racing writers land whole rows; a handful of MiB of interleaved rows before
// ours is already pathological — beyond that we fail LOUD (never false-ok).
export const RACE_SCAN_SLACK_BYTES = 4 * 1024 * 1024;

export interface ReceiptResult {
  ok: boolean;
  reason: string;
}

/**
 * Offset-anchored read-back: the receipt is the exact appended line, complete
 * and parseable, found at (or, under a concurrent-writer offset race, on a
 * line boundary after) the recorded pre-append offset.
 */
export function readBackAtOffset(path: string, offset: number, expectedLine: string): boolean {
  const lineBytes = Buffer.byteLength(expectedLine, "utf-8");
  let fd: number | null = null;
  try {
    fd = openSync(path, "r");
    // Fast path: byte-equality of exactly our record at the recorded offset.
    const exact = Buffer.alloc(lineBytes + 1);
    const got = readSync(fd, exact, 0, lineBytes + 1, offset);
    if (got === lineBytes + 1 && exact.toString("utf-8") === expectedLine + "\n") {
      return true;
    }
    // Offset race fallback: a concurrent O_APPEND writer may have landed rows
    // between our size-record and our append (bun has no flock). Our row can
    // only be AT or AFTER the recorded offset, and — because the tail was
    // newline-repaired and every write lands `line + "\n"` atomically — it is
    // a complete newline-bounded row. Scan forward, bounded.
    const scanLen = lineBytes + RACE_SCAN_SLACK_BYTES;
    const buf = Buffer.alloc(scanLen);
    const n = readSync(fd, buf, 0, scanLen, offset);
    if (n <= 0) return false;
    const text = buf.toString("utf-8", 0, n);
    for (const candidate of text.split("\n")) {
      if (candidate === expectedLine) return true;
    }
    return false;
  } catch {
    return false;
  } finally {
    if (fd !== null) {
      try {
        closeSync(fd);
      } catch {}
    }
  }
}

/**
 * Append one serialized JSONL event line to a queue file, receipt-or-loud.
 * Receipt = an independent re-open finds the EXACT line, complete and
 * parseable, anchored at the recorded pre-append offset. Returns {ok, reason};
 * never throws. There is NO success path without the read-back — restoring
 * one is the mutation CCPO's verify turns RED.
 *
 * `verifier` is injectable ONLY so tests can pin the ok-requires-read-back
 * property (a healthy filesystem cannot naturally produce append-ok +
 * read-back-miss); production callers never pass it.
 */
export function appendLineWithReceipt(
  queuePath: string,
  line: string,
  verifier: (path: string, offset: number, expectedLine: string) => boolean = readBackAtOffset
): ReceiptResult {
  if (!line || line.includes("\n")) {
    return { ok: false, reason: "invalid line: empty or embedded newline" };
  }
  // Receipt = complete AND parseable (the refinement ruling): refuse a
  // non-JSON row up front — every queue consumer JSON-parses per line, so an
  // unparseable "delivered" row is delivered to nobody.
  try {
    JSON.parse(line);
  } catch {
    return { ok: false, reason: "invalid line: not valid JSON (queue rows are JSONL)" };
  }

  let fd: number | null = null;
  let offset: number;
  try {
    fd = openSync(queuePath, "a+");
    const size = fstatSync(fd).size;
    // Torn-tail repair (D1): if the file does not end in a newline, prefix
    // one so glue is impossible by construction. Bundled into the SAME single
    // atomic O_APPEND write as our row (see header note 1).
    let prefix = "";
    if (size > 0) {
      const last = Buffer.alloc(1);
      if (readSync(fd, last, 0, 1, size - 1) === 1 && last.toString("utf-8") !== "\n") {
        prefix = "\n";
      }
    }
    offset = size + Buffer.byteLength(prefix, "utf-8");
    const payload = Buffer.from(prefix + line + "\n", "utf-8");
    writeSync(fd, payload); // "a+" → O_APPEND: one atomic append
    fsyncSync(fd);
  } catch (err) {
    return { ok: false, reason: `append failed: ${err}` };
  } finally {
    if (fd !== null) {
      try {
        closeSync(fd);
      } catch {}
    }
  }
  if (verifier(queuePath, offset, line)) {
    return { ok: true, reason: "receipt: offset-anchored read-back verified" };
  }
  return { ok: false, reason: "RECEIPT MISSING — appended line not found on disk read-back" };
}
