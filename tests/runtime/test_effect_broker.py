# SPDX-License-Identifier: MPL-2.0
"""Tier 1 hostile proofs for the split Company Scribe effect broker."""

from __future__ import annotations

import base64

import errno

import hashlib

import json

import os

import select

import signal

import shutil

import socket

import subprocess

import sys

import threading

import time

from datetime import datetime, timedelta, timezone

from pathlib import Path

from types import SimpleNamespace

import pytest

from fleet_kernel import company_scribe_admission as admission

from fleet_kernel.company_scribe_admission import CompanyScribeAdmissionError

from fleet_kernel import company_scribe_runtime as runtime

from scripts import company_scribe_effect_broker as broker

REPO = Path(__file__).resolve().parents[2]

class LiveAuthority:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.calls = 0
        self.fail_at = fail_at
        self.expiry = datetime.now(timezone.utc) + timedelta(seconds=30)

    def check(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise CompanyScribeAdmissionError("hostile test revocation")
        return SimpleNamespace(
            lease_expires_at=self.expiry,
            admission_id="00000000-0000-4000-8000-000000000001",
            admission_epoch=1,
        )

def _journal(tmp_path: Path) -> broker.MutationJournal:
    state = tmp_path / "broker-state"
    state.mkdir(mode=0o700)
    return broker.MutationJournal(state)


def _jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _delivery_rows(
    event_id: str,
    event_sha: str,
) -> list[dict[str, object]]:
    return [
        {
            "schema": "mise.scribe-delivery-transition.v1",
            "event_id": event_id,
            "event_sha256": event_sha,
            "state": "pending",
            "transitioned_at": "2026-07-31T00:00:00Z",
            "event": {
                "event_id": event_id,
                "event_type": "message",
                "content": "review",
                "meta": {},
                "timestamp": "2026-07-31T00:00:00Z",
            },
        },
        {
            "schema": "mise.scribe-delivery-transition.v1",
            "event_id": event_id,
            "event_sha256": event_sha,
            "state": "delivered",
            "transitioned_at": "2026-07-31T00:00:01Z",
        },
    ]


def _wait_for_path(path: Path, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not path.exists():
        time.sleep(0.01)
    assert path.exists(), f"timed out waiting for {path}"


def _wait_for_process_epoch_to_end(
    pid: int,
    start_fingerprint: str,
    *,
    timeout: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            observed = broker.process_start_fingerprint(pid)
        except CompanyScribeAdmissionError:
            return
        if observed != start_fingerprint:
            return
        time.sleep(0.01)
    pytest.fail(f"process epoch {pid}/{start_fingerprint} survived")


def test_channel_mutations_are_idempotent_and_final_revocation_creates_nothing(
    tmp_path: Path,
):
    shared = tmp_path / "shared"
    shared.mkdir()
    channel_state = tmp_path / "channel"
    channel_state.mkdir()
    journal = _journal(tmp_path)
    arguments = {
        "effect_id": "channel-send-1",
        "target_role": "ccpo",
        "content": "one exact message",
        "event_type": "task_handoff",
        "meta": {"subject": "activation"},
    }
    request_sha = broker._effect_request_sha256(
        "channel", "channel_send", arguments,
    )
    first = broker._journaled_channel_send(
        LiveAuthority(),
        arguments,
        journal=journal,
        request_sha256=request_sha,
        channel_shared_dir=shared,
    )
    replay = broker._journaled_channel_send(
        LiveAuthority(),
        arguments,
        journal=journal,
        request_sha256=request_sha,
        channel_shared_dir=shared,
    )
    assert replay == first
    assert len(_jsonl(shared / "queue_ccpo.jsonl")) == 1
    audit = _jsonl(shared / "audit_sends.jsonl")
    assert len(audit) == 1
    assert audit[0]["content_sha1"] == hashlib.sha1(
        b"one exact message", usedforsecurity=False,
    ).hexdigest()[:12]
    with pytest.raises(CompanyScribeAdmissionError, match="different request"):
        changed = {**arguments, "content": "different"}
        broker._journaled_channel_send(
            LiveAuthority(),
            changed,
            journal=journal,
            request_sha256=broker._effect_request_sha256(
                "channel", "channel_send", changed,
            ),
            channel_shared_dir=shared,
        )

    # Two checks protect each projection: before open and immediately before
    # an absent-file create or existing-file append. If revocation lands after
    # the queue receipt but before the audit receipt, the prepared journal lets
    # an exact retry finish audit without duplicating the queue.
    for fail_at in range(1, 5):
        revoked_shared = tmp_path / f"revoked-shared-{fail_at}"
        revoked_shared.mkdir()
        revoked_args = {
            **arguments,
            "effect_id": f"channel-revoked-{fail_at}",
        }
        revoked_sha = broker._effect_request_sha256(
            "channel", "channel_send", revoked_args,
        )
        with pytest.raises(CompanyScribeAdmissionError, match="revocation"):
            broker._journaled_channel_send(
                LiveAuthority(fail_at=fail_at),
                revoked_args,
                journal=journal,
                request_sha256=revoked_sha,
                channel_shared_dir=revoked_shared,
            )
        expected_queue_rows = 0 if fail_at <= 2 else 1
        assert len(_jsonl(revoked_shared / "queue_ccpo.jsonl")) == expected_queue_rows
        assert _jsonl(revoked_shared / "audit_sends.jsonl") == []
        broker._journaled_channel_send(
            LiveAuthority(), revoked_args,
            journal=journal, request_sha256=revoked_sha,
            channel_shared_dir=revoked_shared,
        )
        assert len(_jsonl(revoked_shared / "queue_ccpo.jsonl")) == 1
        assert len(_jsonl(revoked_shared / "audit_sends.jsonl")) == 1


def test_channel_reply_is_broker_owned_idempotent_and_method_confusion_fails(
    tmp_path: Path,
):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    channel_state = state / "channel"
    channel_state.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    delivery = channel_state / "delivery_journal.jsonl"
    event_sha = "a" * 64
    rows = [
        {
            "schema": "mise.scribe-delivery-transition.v1",
            "event_id": "event-1",
            "event_sha256": event_sha,
            "state": "pending",
            "transitioned_at": "2026-07-31T00:00:00Z",
            "event": {
                "event_id": "event-1", "event_type": "message",
                "content": "review", "meta": {},
                "timestamp": "2026-07-31T00:00:00Z",
            },
        },
        {
            "schema": "mise.scribe-delivery-transition.v1",
            "event_id": "event-1",
            "event_sha256": event_sha,
            "state": "delivered",
            "transitioned_at": "2026-07-31T00:00:01Z",
        },
    ]
    delivery.write_text("".join(json.dumps(row) + "\n" for row in rows))
    journal = broker.MutationJournal(state)
    arguments = {
        "effect_id": "channel-reply-1",
        "event_id": "event-1",
        "text": "noted",
    }
    request_sha = broker._effect_request_sha256(
        "channel", "channel_reply", arguments,
    )
    first = broker._journaled_channel_reply(
        LiveAuthority(), arguments,
        journal=journal, request_sha256=request_sha,
        channel_state_dir=channel_state,
    )
    replay = broker._journaled_channel_reply(
        LiveAuthority(), arguments,
        journal=journal, request_sha256=request_sha,
        channel_state_dir=channel_state,
    )
    assert replay == first
    assert [row["state"] for row in _jsonl(delivery)].count("acknowledged") == 1
    changed_reply = {**arguments, "text": "different"}
    with pytest.raises(CompanyScribeAdmissionError, match="different request"):
        broker._journaled_channel_reply(
            LiveAuthority(), changed_reply,
            journal=journal,
            request_sha256=broker._effect_request_sha256(
                "channel", "channel_reply", changed_reply,
            ),
            channel_state_dir=channel_state,
        )

    supervisor = broker.CommandSupervisor(
        authority=LiveAuthority(), workspace=tmp_path,
        state_dir=state, journal=journal,
    )
    with pytest.raises(CompanyScribeAdmissionError, match="unknown"):
        broker._call_tool(
            LiveAuthority(), tmp_path, "channel_send", {},
            journal=journal, command_supervisor=supervisor, state_dir=state,
        )
    with pytest.raises(CompanyScribeAdmissionError, match="confused"):
        broker._call_channel_tool(
            LiveAuthority(), "workspace_write", {}, journal=journal,
            channel_state_dir=channel_state, channel_shared_dir=shared,
        )


def test_startup_recovers_partial_channel_effects_exactly_once(
    tmp_path: Path,
):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    channel_state = state / "channel"
    channel_state.mkdir(mode=0o700)
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o700)
    journal = broker.MutationJournal(state)

    queue_first = {
        "effect_id": "recover-send-queue-first",
        "target_role": "ccpo",
        "content": "queue exists before audit",
        "event_type": "task_handoff",
        "meta": {"subject": "activation"},
    }
    with pytest.raises(CompanyScribeAdmissionError, match="revocation"):
        broker._journaled_channel_send(
            LiveAuthority(fail_at=4),
            queue_first,
            journal=journal,
            request_sha256=broker._effect_request_sha256(
                "channel", "channel_send", queue_first,
            ),
            channel_shared_dir=shared,
        )
    assert len(_jsonl(shared / "queue_ccpo.jsonl")) == 1
    assert _jsonl(shared / "audit_sends.jsonl") == []
    broker._recover_prepared_channel_effects(
        LiveAuthority(),
        journal=journal,
        channel_state_dir=channel_state,
        channel_shared_dir=shared,
    )
    broker._recover_prepared_channel_effects(
        LiveAuthority(),
        journal=journal,
        channel_state_dir=channel_state,
        channel_shared_dir=shared,
    )
    assert len(_jsonl(shared / "queue_ccpo.jsonl")) == 1
    assert len(_jsonl(shared / "audit_sends.jsonl")) == 1

    audit_first = {
        "effect_id": "recover-send-audit-first",
        "target_role": "ccro",
        "content": "audit exists before queue",
    }
    with pytest.raises(CompanyScribeAdmissionError, match="revocation"):
        broker._journaled_channel_send(
            LiveAuthority(fail_at=1),
            audit_first,
            journal=journal,
            request_sha256=broker._effect_request_sha256(
                "channel", "channel_send", audit_first,
            ),
            channel_shared_dir=shared,
        )
    prepared = json.loads(
        journal.record_path(audit_first["effect_id"]).read_text()
    )
    with (shared / "audit_sends.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            prepared["details"]["audit"],
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n")
    broker._recover_prepared_channel_effects(
        LiveAuthority(),
        journal=journal,
        channel_state_dir=channel_state,
        channel_shared_dir=shared,
    )
    assert len(_jsonl(shared / "queue_ccro.jsonl")) == 1
    assert len([
        row for row in _jsonl(shared / "audit_sends.jsonl")
        if row["send_id"] == prepared["details"]["send_id"]
    ]) == 1

    delivery = channel_state / "delivery_journal.jsonl"
    event_sha = "c" * 64
    delivery.write_text("".join(
        json.dumps(row) + "\n"
        for row in _delivery_rows("recover-reply", event_sha)
    ))
    reply = {
        "effect_id": "recover-reply-absent",
        "event_id": "recover-reply",
        "text": "noted",
    }
    with pytest.raises(CompanyScribeAdmissionError, match="revocation"):
        broker._journaled_channel_reply(
            LiveAuthority(fail_at=1),
            reply,
            journal=journal,
            request_sha256=broker._effect_request_sha256(
                "channel", "channel_reply", reply,
            ),
            channel_state_dir=channel_state,
        )
    broker._recover_prepared_channel_effects(
        LiveAuthority(),
        journal=journal,
        channel_state_dir=channel_state,
        channel_shared_dir=shared,
    )
    broker._recover_prepared_channel_effects(
        LiveAuthority(),
        journal=journal,
        channel_state_dir=channel_state,
        channel_shared_dir=shared,
    )
    assert [
        row["state"] for row in _jsonl(delivery)
        if row["event_id"] == "recover-reply"
    ].count("acknowledged") == 1

    event_sha_2 = "d" * 64
    with delivery.open("a", encoding="utf-8") as handle:
        for row in _delivery_rows("recover-reply-raced", event_sha_2):
            handle.write(json.dumps(row) + "\n")
    raced = {
        "effect_id": "recover-reply-present",
        "event_id": "recover-reply-raced",
        "text": "noted",
    }
    with pytest.raises(CompanyScribeAdmissionError, match="revocation"):
        broker._journaled_channel_reply(
            LiveAuthority(fail_at=1),
            raced,
            journal=journal,
            request_sha256=broker._effect_request_sha256(
                "channel", "channel_reply", raced,
            ),
            channel_state_dir=channel_state,
        )
    with delivery.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "schema": "mise.scribe-delivery-transition.v1",
            "event_id": "recover-reply-raced",
            "event_sha256": event_sha_2,
            "state": "acknowledged",
            "transitioned_at": "2026-07-31T00:00:09Z",
            "verdict": "noted",
        }) + "\n")
    broker._recover_prepared_channel_effects(
        LiveAuthority(),
        journal=journal,
        channel_state_dir=channel_state,
        channel_shared_dir=shared,
    )
    assert [
        row["state"] for row in _jsonl(delivery)
        if row["event_id"] == "recover-reply-raced"
    ].count("acknowledged") == 1


def test_startup_refuses_tampered_or_revoked_channel_recovery(
    tmp_path: Path,
):
    def roots(name: str):
        state = tmp_path / name / "state"
        state.mkdir(parents=True, mode=0o700)
        channel = state / "channel"
        channel.mkdir(mode=0o700)
        shared = tmp_path / name / "shared"
        shared.mkdir(mode=0o700)
        return state, channel, shared, broker.MutationJournal(state)

    state, channel, shared, journal = roots("reserved")
    reserved = {
        "effect_id": "reserved-meta",
        "target_role": "ccpo",
        "content": "hostile",
        "meta": {"sender_role": "attacker"},
    }
    with pytest.raises(CompanyScribeAdmissionError, match="reserved"):
        broker._journaled_channel_send(
            LiveAuthority(),
            reserved,
            journal=journal,
            request_sha256=broker._effect_request_sha256(
                "channel", "channel_send", reserved,
            ),
            channel_shared_dir=shared,
        )
    assert journal.records() == ()

    state, channel, shared, journal = roots("request-hash")
    send = {
        "effect_id": "request-hash-tamper",
        "target_role": "ccpo",
        "content": "exact",
    }
    with pytest.raises(CompanyScribeAdmissionError, match="revocation"):
        broker._journaled_channel_send(
            LiveAuthority(fail_at=1),
            send,
            journal=journal,
            request_sha256=broker._effect_request_sha256(
                "channel", "channel_send", send,
            ),
            channel_shared_dir=shared,
        )
    record_path = journal.record_path(send["effect_id"])
    record = json.loads(record_path.read_text())
    record["request_sha256"] = "0" * 64
    broker._atomic_private_json(record_path, record)
    with pytest.raises(
        CompanyScribeAdmissionError,
        match="request identity",
    ):
        broker._recover_prepared_channel_effects(
            LiveAuthority(),
            journal=journal,
            channel_state_dir=channel,
            channel_shared_dir=shared,
        )
    assert not (shared / "queue_ccpo.jsonl").exists()

    state, channel, shared, journal = roots("row-tamper")
    row_tamper = {
        "effect_id": "row-identity-tamper",
        "target_role": "ccpo",
        "content": "exact",
    }
    with pytest.raises(CompanyScribeAdmissionError, match="revocation"):
        broker._journaled_channel_send(
            LiveAuthority(fail_at=1),
            row_tamper,
            journal=journal,
            request_sha256=broker._effect_request_sha256(
                "channel", "channel_send", row_tamper,
            ),
            channel_shared_dir=shared,
        )
    record_path = journal.record_path(row_tamper["effect_id"])
    record = json.loads(record_path.read_text())
    record["details"]["event"]["meta"]["sender_role"] = "attacker"
    broker._atomic_private_json(record_path, record)
    with pytest.raises(
        CompanyScribeAdmissionError,
        match="changed identity",
    ):
        broker._recover_prepared_channel_effects(
            LiveAuthority(),
            journal=journal,
            channel_state_dir=channel,
            channel_shared_dir=shared,
        )

    state, channel, shared, journal = roots("reply-sha")
    delivery = channel / "delivery_journal.jsonl"
    delivery.write_text("".join(
        json.dumps(row) + "\n"
        for row in _delivery_rows("reply-sha-event", "e" * 64)
    ))
    reply = {
        "effect_id": "reply-sha-tamper",
        "event_id": "reply-sha-event",
        "text": "noted",
    }
    with pytest.raises(CompanyScribeAdmissionError, match="revocation"):
        broker._journaled_channel_reply(
            LiveAuthority(fail_at=1),
            reply,
            journal=journal,
            request_sha256=broker._effect_request_sha256(
                "channel", "channel_reply", reply,
            ),
            channel_state_dir=channel,
        )
    delivery.write_text("".join(
        json.dumps(row) + "\n"
        for row in _delivery_rows("reply-sha-event", "f" * 64)
    ))
    with pytest.raises(
        CompanyScribeAdmissionError,
        match="no longer matches",
    ):
        broker._recover_prepared_channel_effects(
            LiveAuthority(),
            journal=journal,
            channel_state_dir=channel,
            channel_shared_dir=shared,
        )

    state, channel, shared, journal = roots("revoked")
    revoked = {
        "effect_id": "revoked-recovery",
        "target_role": "ccro",
        "content": "exact",
    }
    with pytest.raises(CompanyScribeAdmissionError, match="revocation"):
        broker._journaled_channel_send(
            LiveAuthority(fail_at=1),
            revoked,
            journal=journal,
            request_sha256=broker._effect_request_sha256(
                "channel", "channel_send", revoked,
            ),
            channel_shared_dir=shared,
        )
    with pytest.raises(CompanyScribeAdmissionError, match="revocation"):
        broker._recover_prepared_channel_effects(
            LiveAuthority(fail_at=1),
            journal=journal,
            channel_state_dir=channel,
            channel_shared_dir=shared,
        )
    assert not (shared / "queue_ccro.jsonl").exists()
    assert not (shared / "audit_sends.jsonl").exists()


def test_broker_readiness_refuses_any_unresolved_nonchannel_effect(
    tmp_path: Path,
):
    journal = _journal(tmp_path)
    with journal.locked(
        effect_id="unresolved-workspace",
        request_sha256="a" * 64,
        operation="workspace_write",
    ) as (_record, write):
        write({
            "state": "prepared",
            "details": {
                "path": "canon.md",
                "prestate": {"kind": "absent"},
                "desired_sha256": "b" * 64,
                "desired_mode": 0o644,
            },
        })
    with pytest.raises(
        CompanyScribeAdmissionError,
        match="recovery required.*workspace_write.*unresolved-workspace",
    ):
        broker._assert_no_unresolved_effects(journal)


def test_startup_recovers_prepared_workspace_write_edit_and_mkdir(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = broker.MutationJournal(state)
    authority = LiveAuthority()
    supervisor = broker.CommandSupervisor(
        authority=authority,
        workspace=workspace,
        state_dir=state,
        journal=journal,
    )

    for operation, arguments, expected in (
        (
            "workspace_write",
            {
                "effect_id": "recover-write",
                "path": "written.txt",
                "content": "written exactly",
            },
            ("written.txt", "written exactly"),
        ),
        (
            "workspace_edit",
            {
                "effect_id": "recover-edit",
                "path": "edited.txt",
                "old_text": "before",
                "new_text": "after",
            },
            ("edited.txt", "after"),
        ),
    ):
        if operation == "workspace_edit":
            (workspace / "edited.txt").write_text("before", encoding="utf-8")
        plan = broker._workspace_write_plan(
            workspace, arguments, edit=operation == "workspace_edit",
        )
        request_arguments = {
            key: value for key, value in arguments.items()
            if key != "effect_id"
        }
        request_sha = broker._effect_request_sha256(
            "effect", operation, arguments,
        )
        with journal.locked(
            effect_id=str(arguments["effect_id"]),
            request_sha256=request_sha,
            operation=operation,
        ) as (_record, write):
            write({
                "state": "prepared",
                "details": {
                    "path": str(plan["relative_path"]),
                    "prestate": plan["prestate"],
                    "desired_sha256": str(plan["sha256"]),
                    "desired_mode": int(plan["mode"]),
                    "original_base64": (
                        base64.b64encode(plan["original_data"]).decode("ascii")
                        if isinstance(plan.get("original_data"), bytes)
                        else None
                    ),
                    "request_arguments": request_arguments,
                },
            })
        broker._recover_nonchannel_effects(
            authority,
            journal=journal,
            workspace=workspace,
            state_dir=state,
            command_supervisor=supervisor,
        )
        assert (workspace / expected[0]).read_text(encoding="utf-8") == expected[1]
        broker._assert_no_unresolved_effects(journal)

    mkdir_arguments = {
        "effect_id": "recover-mkdir",
        "path": "created",
    }
    mkdir_sha = broker._effect_request_sha256(
        "effect", "workspace_mkdir", mkdir_arguments,
    )
    with journal.locked(
        effect_id=str(mkdir_arguments["effect_id"]),
        request_sha256=mkdir_sha,
        operation="workspace_mkdir",
    ) as (_record, write):
        write({
            "state": "prepared",
            "details": {
                "path": "created",
                "prestate": {"kind": "absent"},
                "request_arguments": {"path": "created"},
            },
        })
    broker._recover_nonchannel_effects(
        authority,
        journal=journal,
        workspace=workspace,
        state_dir=state,
        command_supervisor=supervisor,
    )
    assert (workspace / "created").is_dir()
    broker._assert_no_unresolved_effects(journal)


@pytest.mark.parametrize(
    ("tamper", "value", "message"),
    (
        ("path", "redirected.txt", "path changed identity"),
        ("desired_sha256", "0" * 64, "desired state changed identity"),
        ("desired_mode", 0o755, "desired state changed identity"),
    ),
)
def test_prepared_workspace_recovery_refuses_tampered_plan(
    tmp_path: Path,
    tamper: str,
    value: object,
    message: str,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = broker.MutationJournal(state)
    authority = LiveAuthority()
    supervisor = broker.CommandSupervisor(
        authority=authority,
        workspace=workspace,
        state_dir=state,
        journal=journal,
    )
    arguments = {
        "effect_id": "tampered-write",
        "path": "authorized.txt",
        "content": "authorized bytes",
    }
    plan = broker._workspace_write_plan(workspace, arguments, edit=False)
    request_sha = broker._effect_request_sha256(
        "effect", "workspace_write", arguments,
    )
    with journal.locked(
        effect_id=str(arguments["effect_id"]),
        request_sha256=request_sha,
        operation="workspace_write",
    ) as (_record, write):
        write({
            "state": "prepared",
            "details": {
                "path": str(plan["relative_path"]),
                "prestate": plan["prestate"],
                "desired_sha256": str(plan["sha256"]),
                "desired_mode": int(plan["mode"]),
                "original_base64": None,
                "request_arguments": {
                    "path": "authorized.txt",
                    "content": "authorized bytes",
                },
            },
        })
    record_path = journal.record_path(str(arguments["effect_id"]))
    record = json.loads(record_path.read_text())
    record["details"][tamper] = value
    record_path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    record_path.chmod(0o600)

    with pytest.raises(CompanyScribeAdmissionError, match=message):
        broker._recover_nonchannel_effects(
            authority,
            journal=journal,
            workspace=workspace,
            state_dir=state,
            command_supervisor=supervisor,
        )
    assert not (workspace / "authorized.txt").exists()
    assert not (workspace / "redirected.txt").exists()


def test_postapply_workspace_errors_remain_exactly_recoverable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = broker.MutationJournal(state)
    authority = LiveAuthority()
    supervisor = broker.CommandSupervisor(
        authority=authority,
        workspace=workspace,
        state_dir=state,
        journal=journal,
    )
    write_arguments = {
        "effect_id": "postapply-write",
        "path": "durable.txt",
        "content": "durable bytes",
    }
    write_sha = broker._effect_request_sha256(
        "effect", "workspace_write", write_arguments,
    )
    commit_write = broker._commit_workspace_write

    def write_then_crash(*args, **kwargs):
        commit_write(*args, **kwargs)
        raise OSError("simulated crash after canonical write")

    monkeypatch.setattr(broker, "_commit_workspace_write", write_then_crash)
    with pytest.raises(OSError, match="after canonical write"):
        broker._journaled_workspace_write(
            authority,
            workspace,
            write_arguments,
            edit=False,
            journal=journal,
            request_sha256=write_sha,
        )
    assert (workspace / "durable.txt").read_text() == "durable bytes"
    assert json.loads(
        journal.record_path("postapply-write").read_text()
    )["state"] == "prepared"
    monkeypatch.setattr(broker, "_commit_workspace_write", commit_write)
    broker._recover_nonchannel_effects(
        authority,
        journal=journal,
        workspace=workspace,
        state_dir=state,
        command_supervisor=supervisor,
    )
    assert json.loads(
        journal.record_path("postapply-write").read_text()
    )["state"] == "completed"

    mkdir_arguments = {
        "effect_id": "postapply-mkdir",
        "path": "durable-directory",
    }
    mkdir_sha = broker._effect_request_sha256(
        "effect", "workspace_mkdir", mkdir_arguments,
    )
    make_directory = broker._make_directory

    def mkdir_then_crash(*args, **kwargs):
        make_directory(*args, **kwargs)
        raise OSError("simulated crash after canonical mkdir")

    monkeypatch.setattr(broker, "_make_directory", mkdir_then_crash)
    with pytest.raises(OSError, match="after canonical mkdir"):
        broker._journaled_make_directory(
            authority,
            workspace,
            mkdir_arguments,
            journal=journal,
            request_sha256=mkdir_sha,
        )
    assert (workspace / "durable-directory").is_dir()
    assert json.loads(
        journal.record_path("postapply-mkdir").read_text()
    )["state"] == "prepared"
    monkeypatch.setattr(broker, "_make_directory", make_directory)
    broker._recover_nonchannel_effects(
        authority,
        journal=journal,
        workspace=workspace,
        state_dir=state,
        command_supervisor=supervisor,
    )
    assert json.loads(
        journal.record_path("postapply-mkdir").read_text()
    )["state"] == "completed"
    broker._assert_no_unresolved_effects(journal)


def test_startup_recovers_prepared_commit_after_private_object_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "Scribe Test"],
        check=True,
    )
    subprocess.run(
        [
            "git", "-C", str(workspace), "config", "user.email",
            "scribe-test@example.invalid",
        ],
        check=True,
    )
    tracked = workspace / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-qm", "base"],
        check=True,
    )
    old_head = subprocess.check_output(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    tracked.write_text("after\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = broker.MutationJournal(state)
    authority = LiveAuthority()
    arguments = {
        "effect_id": "recover-commit",
        "paths": ["tracked.txt"],
        "message": "Recover exact commit",
    }
    request_sha = broker._effect_request_sha256(
        "effect", "workspace_commit", arguments,
    )
    publish = broker._publish_private_git_objects

    def crash_before_publication(*_args, **_kwargs):
        raise KeyboardInterrupt("simulated broker crash")

    monkeypatch.setattr(
        broker, "_publish_private_git_objects", crash_before_publication,
    )
    with pytest.raises(KeyboardInterrupt, match="simulated broker crash"):
        broker._journaled_workspace_commit(
            authority,
            workspace,
            arguments,
            journal=journal,
            request_sha256=request_sha,
            state_dir=state,
        )
    assert json.loads(
        journal.record_path("recover-commit").read_text()
    )["state"] == "prepared"
    monkeypatch.setattr(broker, "_publish_private_git_objects", publish)
    supervisor = broker.CommandSupervisor(
        authority=authority,
        workspace=workspace,
        state_dir=state,
        journal=journal,
    )
    broker._recover_nonchannel_effects(
        authority,
        journal=journal,
        workspace=workspace,
        state_dir=state,
        command_supervisor=supervisor,
    )
    new_head = subprocess.check_output(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    assert new_head != old_head
    assert subprocess.check_output(
        ["git", "-C", str(workspace), "show", f"{new_head}:tracked.txt"],
        text=True,
    ) == "after\n"
    assert subprocess.check_output(
        ["git", "-C", str(workspace), "diff", "--cached", "--name-only"],
        text=True,
    ) == ""
    broker._assert_no_unresolved_effects(journal)


def test_workspace_snapshot_skips_tracked_git_symlinks(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
    )
    (repo / "kept.txt").write_text("kept\n")
    (repo / "link-target.txt").write_text("target\n")
    os.symlink("link-target.txt", repo / "tracked-link")
    subprocess.run(
        ["git", "-C", str(repo), "add", "kept.txt", "link-target.txt", "tracked-link"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "tracked symlink"],
        check=True,
    )
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    journal = broker.MutationJournal(state)
    files, _directories = broker._workspace_snapshot_inventory(
        repo, journal=journal, state_dir=state,
    )
    names = {item.as_posix() for item in files}
    assert "kept.txt" in names
    assert "tracked-link" not in names
    staged = tmp_path / "staged"
    staged.mkdir()
    broker._copy_workspace_snapshot(
        repo, staged / "workspace",
        files=files, directories=_directories,
    )
    assert (staged / "workspace" / "kept.txt").read_text() == "kept\n"
    assert not (staged / "workspace" / "tracked-link").exists()


def test_git_binding_accepts_normal_clone_and_exact_linked_worktree(
    tmp_path: Path,
):
    repo = tmp_path / "main"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Scribe Test"],
        check=True,
    )
    subprocess.run(
        [
            "git", "-C", str(repo), "config", "user.email",
            "scribe-test@example.invalid",
        ],
        check=True,
    )
    (repo / "tracked.txt").write_text("tracked\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    environment = broker._git_environment(state)

    main = broker._git_repository_binding(repo, env=environment)
    assert Path(main["git_dir"]) == (repo / ".git").resolve()
    assert main["git_dir"] == main["git_common_dir"]

    linked = tmp_path / "linked"
    subprocess.run(
        [
            "git", "-C", str(repo), "worktree", "add", "-qb",
            "linked-test", str(linked),
        ],
        check=True,
    )
    worktree = broker._git_repository_binding(linked, env=environment)
    assert Path(worktree["git_dir"]).parent == (
        Path(worktree["git_common_dir"]) / "worktrees"
    )
    assert (linked / ".git").is_file()


def test_runtime_config_separates_effect_and_channel_capabilities(tmp_path: Path):
    payload = tmp_path / "payload"
    python = payload / "runtime" / "python" / "bin" / "python3.14"
    framework = (
        payload / "runtime" / "python" / "Resources" / "Python.app"
        / "Contents" / "MacOS" / "Python"
    )
    bun = payload / "runtime" / "bun"
    webhook = payload / "runtime" / "scribe-channel.js"
    proxy = payload / "scripts" / "company_scribe_effect_broker.py"
    for path in (python, framework, bun, webhook, proxy):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("reviewed", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    channel_shared = tmp_path / "external-channel-bus"
    channel_shared.mkdir()
    config_path = runtime._write_exact_mcp_config(
        payload_root=payload,
        state_dir=state,
        channel_shared_dir=channel_shared,
        admitted_pid=4242,
    )
    servers = json.loads(config_path.read_text())["mcpServers"]
    assert set(servers) == {"scribe-channel-push", "scribe-effects"}
    channel = servers["scribe-channel-push"]
    effect = servers["scribe-effects"]
    assert channel["command"] == str(bun.resolve())
    assert channel["env"]["SCRIBE_CHANNEL_EFFECT_SOCKET"] == str(
        state / "channel-broker.sock"
    )
    assert channel["env"]["SCRIBE_CHANNEL_STATE_DIR"] == str(state / "channel")
    assert channel["env"]["SCRIBE_DELIVERY_STATE_DIR"] == str(state / "channel")
    assert channel["env"]["SCRIBE_CHANNEL_SHARED_DIR"] == str(channel_shared)
    assert "SCRIBE_WORKSPACE_ROOT" not in channel["env"]
    assert channel["env"]["HOME"] == str(state / "mcp-home")
    assert channel["env"]["CLAUDE_CONFIG_DIR"] == str(state / "mcp-config")
    assert channel["env"]["SSH_AUTH_SOCK"] == ""
    assert "SCRIBE_EFFECT_SOCKET" not in channel["env"]
    assert effect["command"] == str(framework.resolve())
    assert effect["args"][-1] == str(state / "effect-broker.sock")
    assert effect["env"] == {
        "CC_SUITE_COMPANY_ID": "example-company",
        "CC_SUITE_SCRIBE_DEPLOYMENT_ID": "Scribe-Example",
        "HOME": str(state / "mcp-home"),
        "CLAUDE_CONFIG_DIR": str(state / "mcp-config"),
        "SSH_AUTH_SOCK": "",
    }


def test_runtime_child_environment_strips_socket_and_secret(tmp_path: Path):
    child = runtime._runtime_child_environment({
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "SCRIBE_EFFECT_SOCKET": "must-not-leak",
        "SCRIBE_CHANNEL_EFFECT_SOCKET": "must-not-leak",
        "BROKER_READINESS_SECRET": "must-not-leak",
    })
    assert all("SOCKET" not in key and "SECRET" not in key for key in child)

