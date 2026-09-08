// SPDX-License-Identifier: MPL-2.0
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  test,
} from "bun:test";
import {
  appendFileSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { drainQueue } from "../shared/durable_inbox.ts";
import {
  DELIVERY_SCHEMA,
  DurableDeliveryStore,
  appendInboundEvent,
  deliverWithJournal,
  normalizeInboundEvent,
  stableEventId,
} from "./durable_delivery.ts";

let tempDir: string;
let journalPath: string;

beforeEach(() => {
  tempDir = mkdtempSync(resolve(tmpdir(), "scribe-delivery-"));
  journalPath = resolve(tempDir, "delivery_journal.jsonl");
});

afterEach(() => {
  rmSync(tempDir, { recursive: true, force: true });
});

function journalRows(): Array<Record<string, unknown>> {
  return readFileSync(journalPath, "utf-8")
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

describe("stable event identity", () => {
  test("preserves a valid source event_id and does not mutate metadata", () => {
    const meta = { event_id: "slack-C123-1770000.001", slack_channel: "tandem" };
    const event = normalizeInboundEvent(
      "slack_message",
      "hello",
      meta,
      () => "2026-07-31T00:00:00.000Z"
    );

    expect(event.event_id).toBe("slack-C123-1770000.001");
    expect(event.meta.event_id).toBe(event.event_id);
    expect(meta).toEqual({
      event_id: "slack-C123-1770000.001",
      slack_channel: "tandem",
    });
  });

  test("keyless legacy row gets the same ID across restart-time timestamps", () => {
    const first = normalizeInboundEvent(
      "message",
      "same immutable row",
      { sender: "founder" },
      () => "2026-07-31T00:00:00.000Z"
    );
    const second = normalizeInboundEvent(
      "message",
      "same immutable row",
      { sender: "founder" },
      () => "2026-08-01T00:00:00.000Z"
    );

    expect(first.event_id).toBe(second.event_id);
    expect(first.event_id).toMatch(/^scribe-event-[a-f0-9]{32}$/);
    expect(stableEventId("message", "different", { sender: "founder" }))
      .not.toBe(first.event_id);
  });

  test("same Slack wire ID in different channels becomes two durable events", () => {
    const first = normalizeInboundEvent("slack_message", "first", {
      event_id: "scribe-1770000000000001",
      slack_channel_id: "C0TANDEM",
    });
    const second = normalizeInboundEvent("slack_message", "second", {
      event_id: "scribe-1770000000000001",
      slack_channel_id: "C0COMMON",
    });

    expect(first.event_id).not.toBe(second.event_id);
    expect(first.meta.source_event_id).toBe("scribe-1770000000000001");
    expect(second.meta.source_event_id).toBe("scribe-1770000000000001");
  });
});

describe("append-only delivery lifecycle", () => {
  test("pending is durable before notify and delivered follows acceptance", async () => {
    const store = new DurableDeliveryStore(journalPath);
    const event = normalizeInboundEvent("message", "activate", {
      event_id: "activation-1",
    });
    let rowsObservedInsideNotify: Array<Record<string, unknown>> = [];

    const result = await deliverWithJournal(store, event, async () => {
      rowsObservedInsideNotify = journalRows();
    });

    expect(rowsObservedInsideNotify.map((row) => row.state)).toEqual(["pending"]);
    expect(result.notification_accepted).toBe(true);
    expect(result.snapshot.state).toBe("delivered");
    expect(journalRows().map((row) => row.state)).toEqual([
      "pending",
      "delivered",
    ]);
    expect(journalRows().every((row) => row.schema === DELIVERY_SCHEMA)).toBe(
      true
    );
  });

  test("notification failure leaves only pending for same-ID retry", async () => {
    const store = new DurableDeliveryStore(journalPath);
    const event = normalizeInboundEvent("message", "retry me", {
      event_id: "retry-1",
    });

    await expect(
      deliverWithJournal(store, event, async () => {
        throw new Error("transport rejected");
      })
    ).rejects.toThrow("transport rejected");

    expect(store.get("retry-1")?.state).toBe("pending");
    expect(journalRows().map((row) => row.state)).toEqual(["pending"]);

    const restarted = new DurableDeliveryStore(journalPath);
    const seen: string[] = [];
    await deliverWithJournal(restarted, event, async (retried) => {
      seen.push(retried.event_id);
    });
    expect(seen).toEqual(["retry-1"]);
    expect(restarted.get("retry-1")?.state).toBe("delivered");
  });

  test("same event_id with different content is rejected", () => {
    const store = new DurableDeliveryStore(journalPath);
    store.ensurePending(
      normalizeInboundEvent("message", "first", { event_id: "bound-id" })
    );
    expect(() =>
      store.ensurePending(
        normalizeInboundEvent("message", "different", {
          event_id: "bound-id",
        })
      )
    ).toThrow("identifies different content");
  });

  test("getBySendId finds the journaled coworker send", () => {
    const store = new DurableDeliveryStore(journalPath);
    const event = normalizeInboundEvent("agent_direct", "ping", {
      event_id: "scribe-event-abcd",
      send_id: "cos-send-me0-0002",
    });
    store.ensurePending(event);
    store.markDelivered(event.event_id);
    expect(store.getBySendId("cos-send-me0-0002")?.event.event_id).toBe(
      "scribe-event-abcd",
    );
    expect(store.getBySendId("missing")).toBeUndefined();
  });

  test("ACK is durable, identical replay is idempotent, conflict is explicit", () => {
    const store = new DurableDeliveryStore(journalPath);
    const event = normalizeInboundEvent("message", "review me", {
      event_id: "ack-1",
    });
    store.ensurePending(event);
    expect(store.acknowledge("ack-1", "noted").kind).toBe("not_delivered");
    expect(store.acknowledge("unknown", "noted").kind).toBe("unknown");

    store.markDelivered("ack-1");
    expect(store.acknowledge("ack-1", "noted").kind).toBe("acknowledged");
    const rowsAfterAck = journalRows();
    expect(rowsAfterAck.map((row) => row.state)).toEqual([
      "pending",
      "delivered",
      "acknowledged",
    ]);

    expect(store.acknowledge("ack-1", "noted").kind).toBe("duplicate");
    expect(journalRows()).toEqual(rowsAfterAck);
    expect(store.acknowledge("ack-1", "different").kind).toBe("conflict");
    expect(journalRows()).toEqual(rowsAfterAck);

    const restarted = new DurableDeliveryStore(journalPath);
    expect(restarted.get("ack-1")?.state).toBe("acknowledged");
    expect(restarted.get("ack-1")?.verdict).toBe("noted");
    expect(restarted.unacknowledged()).toEqual([]);
  });

  test("refresh observes a broker-written ACK without changing store identity", () => {
    const channelStore = new DurableDeliveryStore(journalPath);
    const event = normalizeInboundEvent("message", "broker reply", {
      event_id: "broker-ack-1",
    });
    channelStore.ensurePending(event);
    channelStore.markDelivered(event.event_id);

    const brokerStore = new DurableDeliveryStore(journalPath);
    expect(brokerStore.acknowledge(event.event_id, "noted").kind).toBe(
      "acknowledged"
    );
    expect(channelStore.get(event.event_id)?.state).toBe("delivered");

    channelStore.refreshFromDisk();
    expect(channelStore.get(event.event_id)?.state).toBe("acknowledged");
    expect(channelStore.get(event.event_id)?.verdict).toBe("noted");
    expect(journalRows().map((row) => row.state)).toEqual([
      "pending",
      "delivered",
      "acknowledged",
    ]);
  });

  test("invalid complete history fails closed; torn tail cannot be appended behind", () => {
    writeFileSync(journalPath, "{}\n");
    expect(() => new DurableDeliveryStore(journalPath)).toThrow(
      "unsupported schema"
    );

    writeFileSync(journalPath, "");
    const store = new DurableDeliveryStore(journalPath);
    store.ensurePending(
      normalizeInboundEvent("message", "torn", { event_id: "torn-1" })
    );
    appendFileSync(journalPath, '{"schema":"partial"');
    expect(() => store.markDelivered("torn-1")).toThrow(
      "torn final record"
    );
  });
});

describe("immutable source queues", () => {
  test("direct input appends before delivery and cursor drain never alters source", async () => {
    const queue = resolve(tempDir, "event_queue.jsonl");
    const cursor = resolve(tempDir, "cursor_scribe-local_123.json");
    const event = normalizeInboundEvent("message", "direct", {
      event_id: "direct-1",
    });
    const receipt = appendInboundEvent(queue, event);
    expect(receipt.ok).toBe(true);
    const sourceBefore = readFileSync(queue, "utf-8");

    const store = new DurableDeliveryStore(journalPath);
    const result = await drainQueue(
      queue,
      cursor,
      123,
      "scribe-local",
      async (eventType, content, meta) => {
        await deliverWithJournal(
          store,
          normalizeInboundEvent(eventType, content, meta),
          async () => {}
        );
      }
    );

    expect(result).toEqual({ processed: 1, held: false });
    expect(store.get("direct-1")?.state).toBe("delivered");
    expect(readFileSync(queue, "utf-8")).toBe(sourceBefore);
  });

  test("webhook has no destructive queue primitive", () => {
    const webhook = readFileSync(resolve(import.meta.dir, "webhook.ts"), "utf-8");
    expect(webhook).not.toMatch(/\brenameSync\b/);
    expect(webhook).not.toMatch(/\bunlinkSync\b/);
    expect(webhook).not.toMatch(/\btruncate(?:Sync)?\b/);
    expect(webhook).toContain("appendInboundEvent(QUEUE_FILE, event)");
    expect(webhook).toContain("drainDurableQueue(");
  });

  test("webhook brokers send and reply before refreshing or removing memory", () => {
    const webhook = readFileSync(resolve(import.meta.dir, "webhook.ts"), "utf-8");
    const ackStart = webhook.indexOf("function acknowledgeEvent");
    const pushStart = webhook.indexOf("// --- Push event to Claude", ackStart);
    const ackBody = webhook.slice(ackStart, pushStart);
    const durableAck = ackBody.indexOf('callChannelEffect("channel_reply"');
    const refreshed = ackBody.indexOf("refreshPendingProjection();", durableAck);
    const memoryRemoval = ackBody.indexOf("pendingEvents.delete(");

    expect(ackStart).toBeGreaterThanOrEqual(0);
    expect(durableAck).toBeGreaterThanOrEqual(0);
    expect(refreshed).toBeGreaterThan(durableAck);
    expect(memoryRemoval).toBeGreaterThan(refreshed);
    expect(ackBody).not.toContain("deliveryStore.acknowledge(");
    expect(ackBody).toContain('status: 404');
    expect(ackBody).toContain('status: 409');
    expect(webhook).toContain('callChannelEffect("channel_send"');
    expect(webhook).not.toContain("appendFileSync");
    expect(webhook).not.toContain("appendLineWithReceipt");
    expect(webhook).toContain("process.env.SCRIBE_CHANNEL_EFFECT_SOCKET");
    expect(webhook).not.toContain("process.env.SCRIBE_EFFECT_SOCKET");
    expect(webhook).toContain('required: ["effect_id", "event_id", "text"]');
    expect(webhook).toContain('required: ["effect_id", "target_role", "content"]');
  });
});
