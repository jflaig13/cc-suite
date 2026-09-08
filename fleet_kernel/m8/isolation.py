# SPDX-License-Identifier: MPL-2.0
"""Non-database M8 structural isolation controls."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class IsolationError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerIdentity:
    tenant_id: str
    worker_id: str
    uid: int
    gid: int
    root: Path


def verify_worker_identity(identity: WorkerIdentity) -> None:
    if not identity.tenant_id or not identity.worker_id:
        raise IsolationError("worker tenant and identity are required")
    if os.geteuid() != identity.uid or os.getegid() != identity.gid:
        raise IsolationError("process UID/GID does not match immutable worker identity")
    metadata = identity.root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != identity.uid
        or metadata.st_gid != identity.gid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise IsolationError("tenant worker root must be owned by the worker and mode 0700")


def open_worker_file(identity: WorkerIdentity, relative_path: Path) -> bytes:
    """Open beneath the identity root without symlink or traversal fallback."""
    verify_worker_identity(identity)
    if relative_path.is_absolute() or ".." in relative_path.parts or not relative_path.parts:
        raise IsolationError("worker path escapes tenant root")
    root_fd = os.open(identity.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    current_fd = root_fd
    try:
        for index, part in enumerate(relative_path.parts):
            final = index == len(relative_path.parts) - 1
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if not final:
                flags |= os.O_DIRECTORY
            next_fd = os.open(part, flags, dir_fd=current_fd)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = next_fd
        metadata = os.fstat(current_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != identity.uid:
            raise IsolationError("worker file is not an identity-owned regular file")
        chunks = []
        while True:
            chunk = os.read(current_fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise IsolationError("OS refused tenant worker file access") from exc
    finally:
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)


@dataclass(frozen=True, slots=True)
class CredentialGrant:
    tenant_id: str
    capability: str
    secret_ref: str
    expires_at: datetime


class CredentialBroker:
    """Returns references only; raw credential values never cross this boundary."""

    def __init__(self, grants: Mapping[tuple[str, str], CredentialGrant]) -> None:
        self._grants = dict(grants)

    def checkout(
        self, *, worker: WorkerIdentity, tenant_id: str, capability: str,
        now: datetime,
    ) -> CredentialGrant:
        if worker.tenant_id != tenant_id:
            raise IsolationError("credential checkout tenant mismatch")
        grant = self._grants.get((tenant_id, capability))
        if grant is None:
            raise IsolationError("credential capability is not granted")
        if now.tzinfo is None or now.utcoffset() is None or grant.expires_at <= now:
            raise IsolationError("credential grant is expired or clock is invalid")
        if not grant.secret_ref.startswith("secret://"):
            raise IsolationError("credential grant must be an opaque secret-manager reference")
        return grant


class EgressPolicy:
    def __init__(self, allowed_hosts: Mapping[str, frozenset[str]]) -> None:
        self._allowed = {key: frozenset(value) for key, value in allowed_hosts.items()}

    def require(self, *, capability: str, url: str) -> str:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in {None, 443} or not parsed.hostname
        ):
            raise IsolationError("egress requires credential-free HTTPS on port 443")
        host = parsed.hostname.rstrip(".").lower()
        if host not in self._allowed.get(capability, frozenset()):
            raise IsolationError("default-deny egress refused destination")
        return host


@dataclass(frozen=True, slots=True)
class SignedMessage:
    tenant_id: str
    sender: str
    recipient: str
    message_id: str
    sequence: int
    issued_at: str
    payload_sha256: str
    signature: bytes


def _message_payload(message: SignedMessage) -> bytes:
    return json.dumps(
        {
            "tenant_id": message.tenant_id,
            "sender": message.sender,
            "recipient": message.recipient,
            "message_id": message.message_id,
            "sequence": message.sequence,
            "issued_at": message.issued_at,
            "payload_sha256": message.payload_sha256,
        },
        sort_keys=True, separators=(",", ":"),
    ).encode()


def sign_message(
    private_key: Ed25519PrivateKey, *, tenant_id: str, sender: str,
    recipient: str, sequence: int, payload: bytes, issued_at: datetime | None = None,
) -> SignedMessage:
    if not tenant_id or not sender or not recipient or sequence < 1:
        raise ValueError("signed-message identity and positive sequence are required")
    timestamp = issued_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("signed-message issued_at must be timezone-aware")
    unsigned = SignedMessage(
        tenant_id, sender, recipient, str(uuid.uuid4()), sequence,
        timestamp.isoformat(), hashlib.sha256(payload).hexdigest(), b"",
    )
    return SignedMessage(
        unsigned.tenant_id, unsigned.sender, unsigned.recipient,
        unsigned.message_id, unsigned.sequence, unsigned.issued_at,
        unsigned.payload_sha256, private_key.sign(_message_payload(unsigned)),
    )


class DurableSequenceStore:
    """Process/restart-safe monotonic sequence store for replay refusal."""

    def __init__(self, root: Path) -> None:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
        metadata = root.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise IsolationError("message sequence root must be private mode 0700")
        self._root = root

    def advance(self, *, tenant_id: str, sender: str, sequence: int) -> bool:
        identity = hashlib.sha256(f"{tenant_id}\x1f{sender}".encode()).hexdigest()
        lock_path = self._root / f"{identity}.lock"
        state_path = self._root / f"{identity}.seq"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            lock_metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(lock_metadata.st_mode)
                or stat.S_IMODE(lock_metadata.st_mode) != 0o600
                or lock_metadata.st_uid != os.geteuid()
            ):
                raise IsolationError("message sequence lock is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            last = 0
            if state_path.exists() or state_path.is_symlink():
                state_fd = os.open(state_path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    metadata = os.fstat(state_fd)
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or stat.S_IMODE(metadata.st_mode) != 0o600
                        or metadata.st_uid != os.geteuid()
                    ):
                        raise IsolationError("message sequence state is unsafe")
                    raw = os.read(state_fd, 128)
                    last = int(raw.decode())
                except (OSError, UnicodeDecodeError, ValueError) as exc:
                    raise IsolationError("message sequence state is invalid") from exc
                finally:
                    os.close(state_fd)
            if sequence <= last:
                return False
            temporary_fd, temporary_name = tempfile.mkstemp(
                prefix=f".{identity}.", dir=self._root,
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(temporary_fd, "wb") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(f"{sequence}\n".encode())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, state_path)
                directory_fd = os.open(self._root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                temporary.unlink(missing_ok=True)
            return True
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


class MessageVerifier:
    def __init__(
        self, worker_keys: Mapping[tuple[str, str], Ed25519PublicKey],
        sequence_store: DurableSequenceStore,
        *, max_age: timedelta = timedelta(minutes=5),
    ) -> None:
        self._keys = dict(worker_keys)
        self._sequence_store = sequence_store
        self._max_age = max_age

    def verify(
        self, message: SignedMessage, *, expected_tenant: str,
        expected_recipient: str, payload: bytes,
    ) -> None:
        if message.tenant_id != expected_tenant or message.recipient != expected_recipient:
            raise IsolationError("signed-message tenant or recipient mismatch")
        key = self._keys.get((expected_tenant, message.sender))
        if key is None:
            raise IsolationError("signed-message worker key is unavailable")
        if hashlib.sha256(payload).hexdigest() != message.payload_sha256:
            raise IsolationError("signed-message payload digest mismatch")
        try:
            issued_at = datetime.fromisoformat(message.issued_at)
        except ValueError as exc:
            raise IsolationError("signed-message issued_at is invalid") from exc
        now = datetime.now(timezone.utc)
        if (
            issued_at.tzinfo is None or issued_at.utcoffset() is None
            or issued_at > now + timedelta(seconds=30)
            or now - issued_at > self._max_age
        ):
            raise IsolationError("signed-message is stale or from the future")
        try:
            key.verify(message.signature, _message_payload(message))
        except InvalidSignature as exc:
            raise IsolationError("signed-message signature is invalid") from exc
        if not self._sequence_store.advance(
            tenant_id=message.tenant_id, sender=message.sender, sequence=message.sequence,
        ):
            raise IsolationError("signed-message sequence replay refused")


_SECRET_PATTERN = re.compile(
    rb"(?:sk-ant-|akia[0-9a-z]{16}|-----begin (?:rsa |ec |openssh )?private key-----|"
    rb"bearer\s+[a-z0-9._~+/=-]+|(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_ENCODED_SECRET = re.compile(rb"(?<![A-Za-z0-9+/=])(?:[A-Fa-f0-9]{40,}|[A-Za-z0-9+/]{40,}={0,2})(?![A-Za-z0-9+/=])")


def assert_shared_learning_safe(data: bytes, *, tenant_literals: frozenset[str]) -> None:
    """Shared corpus may contain logic, never credentials or tenant identity/data."""
    lowered = data.lower()
    for literal in tenant_literals:
        encoded = literal.strip().lower().encode()
        if encoded and encoded in lowered:
            raise IsolationError("shared-learning artifact contains a tenant literal")
    if _SECRET_PATTERN.search(data) or _ENCODED_SECRET.search(data):
        raise IsolationError("shared-learning artifact contains credential-shaped data")
