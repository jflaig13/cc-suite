/**
 * Durable inbound-delivery state for the Scribe channel.
 *
 * The source JSONL queues are immutable inboxes. This module records the
 * independent delivery lifecycle as an append-only journal:
 *
 *   pending -> delivered -> acknowledged
 *
 * A pending record is fsync'd and read-back verified before notification.
 * A delivered record is written only after the notification transport accepts
 * the event. An acknowledged record is durable before callers remove any
 * in-memory pending entry.
 */

import {
  existsSync,
  mkdirSync,
  readFileSync,
} from "node:fs";
import { createHash } from "node:crypto";
import { dirname } from "node:path";
import {
  appendLineWithReceipt,
  type ReceiptResult,
} from "../shared/send_receipt.ts";

export const DELIVERY_SCHEMA = "mise.scribe-delivery-transition.v1";

export type DeliveryState = "pending" | "delivered" | "acknowledged";

export interface InboundEvent {
  event_id: string;
  event_type: string;
  content: string;
  meta: Record<string, string>;
  timestamp: string;
}

export interface DeliverySnapshot {
  event: InboundEvent;
  event_sha256: string;
  state: DeliveryState;
  verdict?: string;
  acknowledged_at?: string;
}

interface TransitionRecord {
  schema: typeof DELIVERY_SCHEMA;
  event_id: string;
  event_sha256: string;
  state: DeliveryState;
  transitioned_at: string;
  event?: InboundEvent;
  verdict?: string;
}

export type AcknowledgeResult =
  | { kind: "acknowledged"; snapshot: DeliverySnapshot }
  | { kind: "duplicate"; snapshot: DeliverySnapshot }
  | { kind: "conflict"; snapshot: DeliverySnapshot }
  | { kind: "not_delivered"; snapshot: DeliverySnapshot }
  | { kind: "unknown" };

export interface EnsurePendingResult {
  created: boolean;
  snapshot: DeliverySnapshot;
}

export interface DeliveryAttemptResult {
  notification_accepted: boolean;
  snapshot: DeliverySnapshot;
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value !== null && typeof value === "object") {
    const source = value as Record<string, unknown>;
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(source).sort()) {
      sorted[key] = canonicalize(source[key]);
    }
    return sorted;
  }
  return value;
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(canonicalize(value));
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf-8").digest("hex");
}

function cloneEvent(event: InboundEvent): InboundEvent {
  return {
    event_id: event.event_id,
    event_type: event.event_type,
    content: event.content,
    meta: { ...event.meta },
    timestamp: event.timestamp,
  };
}

function cloneSnapshot(snapshot: DeliverySnapshot): DeliverySnapshot {
  return {
    event: cloneEvent(snapshot.event),
    event_sha256: snapshot.event_sha256,
    state: snapshot.state,
    ...(snapshot.verdict === undefined ? {} : { verdict: snapshot.verdict }),
    ...(snapshot.acknowledged_at === undefined
      ? {}
      : { acknowledged_at: snapshot.acknowledged_at }),
  };
}

function isSafeExplicitEventId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value)
  );
}

/**
 * Preserve a valid source event_id. Legacy rows without one receive a stable
 * content-derived ID, so retrying the same immutable source row after restart
 * cannot create a second logical event.
 */
export function stableEventId(
  event_type: string,
  content: string,
  meta: Record<string, string> = {}
): string {
  if (isSafeExplicitEventId(meta.event_id)) {
    // Slack timestamps are unique only within one channel. The shared poller
    // intentionally keeps its historical wire ID, so bind the durable Scribe
    // identity to the channel as well or two real cross-channel messages can
    // collide and permanently hold the second source cursor.
    if (
      event_type === "slack_message" &&
      isSafeExplicitEventId(meta.slack_channel_id)
    ) {
      const scoped = `${meta.event_id}:${meta.slack_channel_id}`;
      if (isSafeExplicitEventId(scoped)) return scoped;
      return (
        `${meta.event_id.slice(0, 150)}:` +
        sha256(meta.slack_channel_id).slice(0, 32)
      );
    }
    return meta.event_id;
  }

  const identityMeta = { ...meta };
  delete identityMeta.event_id;
  delete identityMeta.event_type;
  const digest = sha256(
    canonicalJson({
      event_type,
      content,
      meta: identityMeta,
    })
  );
  return `scribe-event-${digest.slice(0, 32)}`;
}

/**
 * Clone and stamp an inbound event without mutating the caller's metadata.
 */
export function normalizeInboundEvent(
  event_type: string,
  content: string,
  meta: Record<string, string> = {},
  now: () => string = () => new Date().toISOString()
): InboundEvent {
  const eventId = stableEventId(event_type, content, meta);
  const timestamp =
    (typeof meta.timestamp === "string" && meta.timestamp) ||
    (typeof meta.queued_at === "string" && meta.queued_at) ||
    (typeof meta.sent_at === "string" && meta.sent_at) ||
    now();
  const sourceEventId =
    typeof meta.event_id === "string" && meta.event_id !== eventId
      ? meta.event_id
      : undefined;
  return {
    event_id: eventId,
    event_type,
    content,
    meta: {
      ...meta,
      ...(sourceEventId === undefined
        ? {}
        : { source_event_id: sourceEventId }),
      event_type,
      event_id: eventId,
      timestamp,
    },
    timestamp,
  };
}

/**
 * Fingerprint the logical event, excluding delivery-added duplicate metadata.
 * The event_id itself is bound separately in every journal record.
 */
export function eventFingerprint(event: InboundEvent): string {
  const identityMeta = { ...event.meta };
  delete identityMeta.event_id;
  delete identityMeta.event_type;
  delete identityMeta.timestamp;
  return sha256(
    canonicalJson({
      event_type: event.event_type,
      content: event.content,
      meta: identityMeta,
    })
  );
}

function assertTransitionRecord(value: unknown, lineNumber: number): TransitionRecord {
  if (value === null || typeof value !== "object") {
    throw new Error(`delivery journal line ${lineNumber}: record is not an object`);
  }
  const record = value as Partial<TransitionRecord>;
  if (record.schema !== DELIVERY_SCHEMA) {
    throw new Error(`delivery journal line ${lineNumber}: unsupported schema`);
  }
  if (!isSafeExplicitEventId(record.event_id)) {
    throw new Error(`delivery journal line ${lineNumber}: invalid event_id`);
  }
  if (
    typeof record.event_sha256 !== "string" ||
    !/^[a-f0-9]{64}$/.test(record.event_sha256)
  ) {
    throw new Error(`delivery journal line ${lineNumber}: invalid event_sha256`);
  }
  if (
    record.state !== "pending" &&
    record.state !== "delivered" &&
    record.state !== "acknowledged"
  ) {
    throw new Error(`delivery journal line ${lineNumber}: invalid state`);
  }
  if (typeof record.transitioned_at !== "string" || !record.transitioned_at) {
    throw new Error(`delivery journal line ${lineNumber}: invalid transitioned_at`);
  }
  if (record.state === "pending") {
    const event = record.event as Partial<InboundEvent> | undefined;
    if (
      event === undefined ||
      event.event_id !== record.event_id ||
      typeof event.event_type !== "string" ||
      typeof event.content !== "string" ||
      typeof event.timestamp !== "string" ||
      event.meta === null ||
      typeof event.meta !== "object" ||
      Array.isArray(event.meta)
    ) {
      throw new Error(`delivery journal line ${lineNumber}: invalid pending event`);
    }
  }
  if (record.state === "acknowledged" && typeof record.verdict !== "string") {
    throw new Error(`delivery journal line ${lineNumber}: missing verdict`);
  }
  return record as TransitionRecord;
}

export class DurableDeliveryStore {
  readonly journalPath: string;
  private readonly now: () => string;
  private snapshots = new Map<string, DeliverySnapshot>();

  constructor(
    journalPath: string,
    now: () => string = () => new Date().toISOString()
  ) {
    this.journalPath = journalPath;
    this.now = now;
    mkdirSync(dirname(journalPath), { recursive: true, mode: 0o700 });
    this.refreshFromDisk();
  }

  /** Atomically replace the projection after an external broker mutation. */
  refreshFromDisk(): void {
    const previous = this.snapshots;
    this.snapshots = new Map<string, DeliverySnapshot>();
    try {
      this.load();
    } catch (error) {
      this.snapshots = previous;
      throw error;
    }
  }

  private load(): void {
    if (!existsSync(this.journalPath)) return;
    const raw = readFileSync(this.journalPath, "utf-8");
    // A crash can leave a torn final row. It is not authoritative and is
    // ignored on read, but appendTransition refuses to append behind it.
    const complete = raw.endsWith("\n")
      ? raw
      : raw.slice(0, raw.lastIndexOf("\n") + 1);
    const lines = complete.split("\n").filter((line) => line.trim().length > 0);

    for (const [index, line] of lines.entries()) {
      let decoded: unknown;
      try {
        decoded = JSON.parse(line);
      } catch {
        throw new Error(`delivery journal line ${index + 1}: invalid JSON`);
      }
      const record = assertTransitionRecord(decoded, index + 1);
      const current = this.snapshots.get(record.event_id);

      if (record.state === "pending") {
        if (current !== undefined || record.event === undefined) {
          throw new Error(
            `delivery journal line ${index + 1}: pending is not the first transition`
          );
        }
        const event = cloneEvent(record.event);
        if (eventFingerprint(event) !== record.event_sha256) {
          throw new Error(
            `delivery journal line ${index + 1}: pending fingerprint mismatch`
          );
        }
        this.snapshots.set(record.event_id, {
          event,
          event_sha256: record.event_sha256,
          state: "pending",
        });
        continue;
      }

      if (current === undefined || current.event_sha256 !== record.event_sha256) {
        throw new Error(
          `delivery journal line ${index + 1}: transition has no matching pending event`
        );
      }

      if (record.state === "delivered") {
        if (current.state !== "pending") {
          throw new Error(
            `delivery journal line ${index + 1}: delivered does not follow pending`
          );
        }
        current.state = "delivered";
        continue;
      }

      if (current.state !== "delivered") {
        throw new Error(
          `delivery journal line ${index + 1}: acknowledged does not follow delivered`
        );
      }
      current.state = "acknowledged";
      current.verdict = record.verdict;
      current.acknowledged_at = record.transitioned_at;
    }
  }

  private assertCompleteTail(): void {
    if (!existsSync(this.journalPath)) return;
    const raw = readFileSync(this.journalPath);
    if (raw.length > 0 && raw[raw.length - 1] !== 0x0a) {
      throw new Error(
        "delivery journal has a torn final record; refusing to append behind it"
      );
    }
  }

  private appendTransition(record: TransitionRecord): void {
    this.assertCompleteTail();
    const receipt = appendLineWithReceipt(
      this.journalPath,
      JSON.stringify(record)
    );
    if (!receipt.ok) {
      throw new Error(`delivery journal append failed: ${receipt.reason}`);
    }
  }

  get(eventId: string): DeliverySnapshot | undefined {
    const snapshot = this.snapshots.get(eventId);
    return snapshot === undefined ? undefined : cloneSnapshot(snapshot);
  }

  getBySendId(sendId: string): DeliverySnapshot | undefined {
    if (!sendId) return undefined;
    for (const snapshot of this.snapshots.values()) {
      if (snapshot.event.meta.send_id === sendId) {
        return cloneSnapshot(snapshot);
      }
    }
    return undefined;
  }

  ensurePending(event: InboundEvent): EnsurePendingResult {
    const normalized = cloneEvent(event);
    const fingerprint = eventFingerprint(normalized);
    const current = this.snapshots.get(normalized.event_id);
    if (current !== undefined) {
      if (current.event_sha256 !== fingerprint) {
        throw new Error(
          `event_id conflict: ${normalized.event_id} identifies different content`
        );
      }
      return { created: false, snapshot: cloneSnapshot(current) };
    }

    const transitionedAt = this.now();
    this.appendTransition({
      schema: DELIVERY_SCHEMA,
      event_id: normalized.event_id,
      event_sha256: fingerprint,
      state: "pending",
      transitioned_at: transitionedAt,
      event: normalized,
    });
    const snapshot: DeliverySnapshot = {
      event: normalized,
      event_sha256: fingerprint,
      state: "pending",
    };
    this.snapshots.set(normalized.event_id, snapshot);
    return { created: true, snapshot: cloneSnapshot(snapshot) };
  }

  markDelivered(eventId: string): DeliverySnapshot {
    const current = this.snapshots.get(eventId);
    if (current === undefined) {
      throw new Error(`cannot mark unknown event delivered: ${eventId}`);
    }
    if (current.state === "delivered" || current.state === "acknowledged") {
      return cloneSnapshot(current);
    }

    this.appendTransition({
      schema: DELIVERY_SCHEMA,
      event_id: eventId,
      event_sha256: current.event_sha256,
      state: "delivered",
      transitioned_at: this.now(),
    });
    current.state = "delivered";
    return cloneSnapshot(current);
  }

  acknowledge(eventId: string, verdict: string): AcknowledgeResult {
    const current = this.snapshots.get(eventId);
    if (current === undefined) return { kind: "unknown" };
    if (current.state === "pending") {
      return { kind: "not_delivered", snapshot: cloneSnapshot(current) };
    }
    if (current.state === "acknowledged") {
      return {
        kind: current.verdict === verdict ? "duplicate" : "conflict",
        snapshot: cloneSnapshot(current),
      };
    }

    const acknowledgedAt = this.now();
    this.appendTransition({
      schema: DELIVERY_SCHEMA,
      event_id: eventId,
      event_sha256: current.event_sha256,
      state: "acknowledged",
      transitioned_at: acknowledgedAt,
      verdict,
    });
    current.state = "acknowledged";
    current.verdict = verdict;
    current.acknowledged_at = acknowledgedAt;
    return { kind: "acknowledged", snapshot: cloneSnapshot(current) };
  }

  unacknowledged(): DeliverySnapshot[] {
    return [...this.snapshots.values()]
      .filter((snapshot) => snapshot.state !== "acknowledged")
      .map(cloneSnapshot);
  }

  acknowledgedAuditEntries(): Array<{
    event_id: string;
    event_type: string;
    content: string;
    verdict: string;
    timestamp_event: string;
    timestamp_ack: string;
  }> {
    return [...this.snapshots.values()]
      .filter(
        (
          snapshot
        ): snapshot is DeliverySnapshot & {
          verdict: string;
          acknowledged_at: string;
        } =>
          snapshot.state === "acknowledged" &&
          snapshot.verdict !== undefined &&
          snapshot.acknowledged_at !== undefined
      )
      .map((snapshot) => ({
        event_id: snapshot.event.event_id,
        event_type: snapshot.event.event_type,
        content: snapshot.event.content,
        verdict: snapshot.verdict,
        timestamp_event: snapshot.event.timestamp,
        timestamp_ack: snapshot.acknowledged_at,
      }));
  }
}

/**
 * The only normal notification path: pending is durable before notify, and
 * delivered is durable only after the notification promise resolves.
 */
export async function deliverWithJournal(
  store: DurableDeliveryStore,
  event: InboundEvent,
  notify: (event: InboundEvent) => Promise<void>
): Promise<DeliveryAttemptResult> {
  const pending = store.ensurePending(event).snapshot;
  if (pending.state === "acknowledged") {
    return { notification_accepted: false, snapshot: pending };
  }
  await notify(pending.event);
  return {
    notification_accepted: true,
    snapshot: store.markDelivered(pending.event.event_id),
  };
}

/**
 * Direct HTTP inputs first join the same immutable JSONL source queue as every
 * other event. A successful return is an fsync'd, exact read-back receipt.
 */
export function appendInboundEvent(
  queuePath: string,
  event: InboundEvent
): ReceiptResult {
  mkdirSync(dirname(queuePath), { recursive: true, mode: 0o700 });
  return appendLineWithReceipt(
    queuePath,
    JSON.stringify({
      event_type: event.event_type,
      content: event.content,
      meta: event.meta,
    })
  );
}
