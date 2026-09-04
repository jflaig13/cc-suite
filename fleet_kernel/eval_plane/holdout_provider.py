"""Evaluator-isolated holdout retrieval for the Fleet Kernel Evaluation Plane.

Founder decision, `docs/brain/080926__ep040-phase0-synthesis-gate-a.md` §7(A): V1
holdouts live out of the repository, and "out of the repository" means the agent
under evaluation cannot reach the bundle, the expected labels, or the grader
logic — not merely a different directory on the same reachable machine.

Two independent boundaries carry that requirement here.

Storage boundary. The store root lives outside every worktree the target can
enumerate, and `isolation_report()` re-derives — from the target's declared
confinement, at every fetch — whether the target could open it anyway. An
admitted Company role is confined by the broker's deny-default `sandbox-exec`
profile (`scripts/company_scribe_effect_broker.py:1678`), whose only content
grants are the OS closure and the per-job staging root, and the staging root is
granted `file-write*` as well as `file-read*` — a store under it could be not
just read but rewritten, so a wrong answer would grade as correct. Both facts
are therefore mandatory inputs: a `BROKER_SANDBOX` principal must declare its
staging root and the full OS read closure, and a principal missing either is
refused rather than certified.

Every other CC-Suite role holds raw, unscoped Bash at the host OS principal.
Owner uid and permission mode do not bound such a role — `sudo`/admin
escalation is available to all of them, and uid 0 bypasses discretionary access
control outright — so V1 returns `isolated=False` for every `UNCONFINED`
principal and refuses a uid-0 target outright. Unconfined roles cannot be
evaluated against holdouts until real per-role ACLs, a separate host, or a
separate credential lands.

Context boundary. `fetch_by_case_id` refuses to return anything until the
target's trajectory is sealed, so a bundle can only enter the grading step,
never the target's turn. The `FetchAuthorization` token this boundary requires
stops every naive forgery — `None`, a bare `object()`, a token minted for a
different case — and prevents an accidental or careless caller elsewhere in
the eval runtime from loading a bundle without the seal and isolation checks
having run. It is a mistake-proofing mechanism for in-process code, not a
capability boundary: code that can import this module can reach
`_mint_authorization` and its closed-over secret, so the real boundary this
module carries is the storage boundary above, not this one. `HoldoutBundle`
carries a redacted repr so an accidental log, trace, or receipt write cannot
spill labels.

Callers name a case, never a path, so a stronger mechanism (real per-role ACL,
separate host, separate credential) can replace `FilesystemHoldoutProvider`
without touching case-author or grader code.
"""
from __future__ import annotations

import abc
import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class HoldoutError(RuntimeError):
    """A holdout request violated the evaluator-isolation contract."""


class HoldoutBoundaryError(HoldoutError):
    """The evaluated target could reach the holdout, or the turn is not sealed."""


class HoldoutPrincipalError(HoldoutError):
    """The declared target principal is incomplete, so isolation cannot be derived."""


class HoldoutNotFoundError(HoldoutError):
    """No holdout bundle is provisioned for the requested case."""


class HoldoutStoreError(HoldoutError):
    """The configured holdout store is unusable or unsafely placed."""


HOLDOUT_SCHEME = "holdout://"
# Dot- and dash-separated segments only: no separator run can produce "..", so a
# case identifier can never escape the store root when used as a filename.
CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*(?:[.-][a-z0-9_]+)*$")
MAX_CASE_ID_LENGTH = 200

# Content-readable roots of the broker's deny-default sandbox profile
# (`scripts/company_scribe_effect_broker.py:1682-1685`). Everything else outside
# the private staging snapshot is metadata-only to a confined target. The
# per-job staging root is granted separately and dynamically, which is why a
# principal must declare it; `tests/test_tier1_eval_plane_holdout_isolation.py`
# parses the broker profile and fails if this copy drifts from it.
BROKER_SANDBOX_READ_ROOTS: tuple[Path, ...] = (
    Path("/System"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/Library/Apple"),
    Path("/opt/homebrew/Cellar"),
)

_RESOLVED_BROKER_READ_ROOTS: tuple[Path, ...] = tuple(
    root.resolve() for root in BROKER_SANDBOX_READ_ROOTS
)

_BUNDLE_PARTS = ("expected", "graders", "fixture")


def _is_within(candidate: Path, ancestor: Path) -> bool:
    return candidate == ancestor or candidate.is_relative_to(ancestor)


class Confinement(str, Enum):
    """How much of the filesystem the evaluated target's own tools can read."""

    BROKER_SANDBOX = "broker_sandbox"
    UNCONFINED = "unconfined"


def normalize_case_id(value: str) -> str:
    case_id = value.strip()
    if (not case_id or len(case_id) > MAX_CASE_ID_LENGTH
            or not CASE_ID_PATTERN.fullmatch(case_id)):
        raise HoldoutError(f"invalid holdout case identifier: {value!r}")
    return case_id


def holdout_ref(case_id: str, part: str) -> str:
    if part not in _BUNDLE_PARTS:
        raise HoldoutError(f"unknown holdout bundle part: {part!r}")
    return f"{HOLDOUT_SCHEME}{normalize_case_id(case_id)}/{part}"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class TargetPrincipal:
    """The agent under evaluation, described well enough to check reachability.

    A `BROKER_SANDBOX` principal is only meaningful if it carries every grant the
    sandbox profile makes, so construction validates that the declared
    `readable_roots` cover the whole OS read closure *and* the per-job staging
    root the profile grants `file-write*` on. Direct construction is therefore
    not a way around `broker_confined()`.
    """

    role_type: str
    os_uid: int
    confinement: Confinement
    workspace_root: Path | None = None
    readable_roots: tuple[Path, ...] = ()
    staging_root: Path | None = None

    def __post_init__(self) -> None:
        if self.confinement is Confinement.UNCONFINED:
            if self.staging_root is not None:
                raise HoldoutPrincipalError(
                    f"unconfined target {self.role_type} declares a broker staging root "
                    f"{self.staging_root}; it runs no sandbox",
                )
            return
        if self.confinement is not Confinement.BROKER_SANDBOX:
            raise HoldoutPrincipalError(f"unknown target confinement: {self.confinement!r}")

        declared = set(self.readable_roots)
        if not declared:
            raise HoldoutPrincipalError(
                f"broker-confined target {self.role_type} declares no readable roots; "
                "the sandbox profile grants at least the OS closure and a staging root",
            )
        missing = sorted(str(root) for root in _RESOLVED_BROKER_READ_ROOTS if root not in declared)
        if missing:
            raise HoldoutPrincipalError(
                f"broker-confined target {self.role_type} omits sandbox-readable OS roots "
                f"{missing}; isolation cannot be derived from an incomplete grant list",
            )
        staging = self.staging_root
        if staging is None:
            raise HoldoutPrincipalError(
                f"broker-confined target {self.role_type} declares no staging root; the "
                "sandbox profile grants file-read* AND file-write* on it "
                "(scripts/company_scribe_effect_broker.py:1708-1709)",
            )
        if not staging.is_absolute() or staging not in declared:
            raise HoldoutPrincipalError(
                f"broker-confined target {self.role_type} declares staging root {staging}, "
                "which is not an absolute path among its readable roots",
            )
        if any(_is_within(staging, os_root) for os_root in _RESOLVED_BROKER_READ_ROOTS):
            raise HoldoutPrincipalError(
                f"broker-confined target {self.role_type} declares staging root {staging} "
                "inside the sandbox OS read closure; a per-job staging root is private",
            )

    @classmethod
    def broker_confined(
        cls,
        role_type: str,
        *,
        os_uid: int,
        workspace_root: Path,
        staging_root: Path,
    ) -> "TargetPrincipal":
        """A role admitted through the effect broker's deny-default sandbox.

        `staging_root` is the per-job root the broker grants the target read and
        write on; it is required because omitting it produced a certified-isolated
        verdict for a store the target could rewrite.
        """
        resolved_staging = staging_root.resolve()
        return cls(
            role_type=role_type,
            os_uid=os_uid,
            confinement=Confinement.BROKER_SANDBOX,
            workspace_root=workspace_root,
            readable_roots=(*_RESOLVED_BROKER_READ_ROOTS, resolved_staging),
            staging_root=resolved_staging,
        )

    @classmethod
    def unconfined(
        cls, role_type: str, *, os_uid: int, workspace_root: Path | None = None,
    ) -> "TargetPrincipal":
        """A CC-Suite role holding raw Bash/Read at the host OS principal."""
        return cls(
            role_type=role_type,
            os_uid=os_uid,
            confinement=Confinement.UNCONFINED,
            workspace_root=workspace_root,
        )


class FetchAuthorization:
    """Evidence that `fetch_by_case_id` ran the seal and isolation checks.

    Only `fetch_by_case_id` mints one in the sanctioned path, and `_load_bundle`
    refuses to run without a valid one — a real improvement over an underscore
    naming convention, and it stops every naive misuse. It is not proof against
    code that can import this module: `_mint_authorization` is reachable as a
    module-level name and its closed-over secret is liftable via
    `__closure__`, so this checks callers' behavior, not their capability. The
    boundary that actually keeps the target out is the storage boundary in
    this module's docstring, not this token.
    """

    __slots__ = ("case_id", "attempt_id", "_secret")

    case_id: str
    attempt_id: str

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise HoldoutBoundaryError(
            "a holdout fetch authorization is minted only by "
            "HoldoutProvider.fetch_by_case_id, after the seal and isolation checks",
        )

    def __repr__(self) -> str:
        return f"FetchAuthorization(case_id={self.case_id!r}, attempt_id={self.attempt_id!r})"


def _authorization_gate() -> tuple[
    Callable[[str, str], FetchAuthorization], Callable[[object, str], None],
]:
    """Close the minting secret over a scope no caller can name."""
    secret = object()

    def mint(case_id: str, attempt_id: str) -> FetchAuthorization:
        token = object.__new__(FetchAuthorization)
        object.__setattr__(token, "case_id", case_id)
        object.__setattr__(token, "attempt_id", attempt_id)
        object.__setattr__(token, "_secret", secret)
        return token

    def verify(authorization: object, case_id: str) -> None:
        if (not isinstance(authorization, FetchAuthorization)
                or getattr(authorization, "_secret", None) is not secret
                or authorization.case_id != case_id):
            raise HoldoutBoundaryError(
                f"holdout bundle for case {case_id} was loaded without a fetch "
                "authorization: the seal and isolation checks did not run",
            )

    return mint, verify


_mint_authorization, _verify_authorization = _authorization_gate()


@dataclass(frozen=True, slots=True)
class GradingContext:
    """Proof that the target's turn is over before any label is handed out."""

    attempt_id: str
    target: TargetPrincipal
    trajectory_sealed: bool


@dataclass(frozen=True, slots=True)
class IsolationReport:
    isolated: bool
    mechanism: str
    reason: str


@dataclass(frozen=True, slots=True, repr=False)
class HoldoutBundle:
    """Expected labels, grader logic, and input fixture for one holdout case."""

    case_id: str
    content_sha256: str
    expected: Mapping[str, Any]
    graders: Mapping[str, Any]
    fixture: Mapping[str, Any]

    def __repr__(self) -> str:
        return (f"HoldoutBundle(case_id={self.case_id!r}, "
                f"content_sha256={self.content_sha256!r}, contents=<redacted>)")

    __str__ = __repr__

    def redacted_summary(self) -> dict[str, Any]:
        """Receipt-safe evidence: identity and digests, never content."""
        return {
            "case_id": self.case_id,
            "content_sha256": self.content_sha256,
            "part_digests": {part: _digest(getattr(self, part)) for part in _BUNDLE_PARTS},
        }


class HoldoutProvider(abc.ABC):
    """Fetch-by-case-id holdout access. Implementations own their own boundary."""

    @abc.abstractmethod
    def has_case(self, case_id: str) -> bool:
        """Whether a bundle is provisioned. Reveals no bundle content."""

    @abc.abstractmethod
    def bundle_digest(self, case_id: str) -> str:
        """Content hash used to bind an in-repo case manifest to its bundle."""

    @abc.abstractmethod
    def isolation_report(self, target: TargetPrincipal) -> IsolationReport:
        """Whether this store is unreachable by that target's own tool access."""

    @abc.abstractmethod
    def _load_bundle(self, case_id: str, *, authorization: FetchAuthorization) -> HoldoutBundle:
        """Implementation hook. Must call `self._require_authorization` first."""

    @staticmethod
    def _require_authorization(authorization: object, case_id: str) -> None:
        """Refuse a load that did not come through `fetch_by_case_id`."""
        _verify_authorization(authorization, case_id)

    def fetch_by_case_id(self, case_id: str, *, grading: GradingContext) -> HoldoutBundle:
        """The only sanctioned read path: sealed turn, proven boundary, then content."""
        normalized = normalize_case_id(case_id)
        if not grading.trajectory_sealed:
            raise HoldoutBoundaryError(
                f"holdout {normalized} requested for attempt {grading.attempt_id} "
                "before the target trajectory was sealed",
            )
        report = self.isolation_report(grading.target)
        if not report.isolated:
            raise HoldoutBoundaryError(
                f"holdout store is reachable by target {grading.target.role_type}: {report.reason}",
            )
        return self._load_bundle(
            normalized, authorization=_mint_authorization(normalized, grading.attempt_id),
        )


class FilesystemHoldoutProvider(HoldoutProvider):
    """V1 store: one JSON bundle per case, on a root no target worktree covers.

    Placement is checked at construction (outside the repository, outside the
    sandbox's readable OS roots, outside every broker job root) and reachability
    is re-checked per fetch against the specific target, because the same
    directory is isolated from a broker-confined role and wide open to an
    unconfined one.

    `broker_job_roots` names the paths under which the broker builds per-job
    staging trees. It is explicit rather than defaulted because a store placed
    there is granted `file-write*` to the target: pass the daemon's job roots, or
    `()` to state that this host runs no broker.
    """

    def __init__(
        self, root: Path, *, repo_root: Path, broker_job_roots: Sequence[Path],
    ) -> None:
        try:
            resolved_root = root.resolve(strict=True)
        except OSError as error:
            raise HoldoutStoreError(f"holdout store root is unreadable: {root}") from error
        if not resolved_root.is_dir():
            raise HoldoutStoreError(f"holdout store root is not a directory: {resolved_root}")

        resolved_repo = repo_root.resolve()
        if _is_within(resolved_root, resolved_repo) or _is_within(resolved_repo, resolved_root):
            raise HoldoutStoreError(
                f"holdout store {resolved_root} overlaps the repository worktree "
                f"{resolved_repo}; any tracked or walkable file is target-readable",
            )
        for os_root in BROKER_SANDBOX_READ_ROOTS:
            if _is_within(resolved_root, os_root):
                raise HoldoutStoreError(
                    f"holdout store {resolved_root} sits under sandbox-readable root {os_root}",
                )
        for job_root in broker_job_roots:
            resolved_job_root = job_root.resolve()
            if (_is_within(resolved_root, resolved_job_root)
                    or _is_within(resolved_job_root, resolved_root)):
                raise HoldoutStoreError(
                    f"holdout store {resolved_root} overlaps broker job root "
                    f"{resolved_job_root}; the sandbox grants the target file-read* and "
                    "file-write* on every per-job staging tree beneath it",
                )
        self._root = resolved_root

    def _bundle_path(self, case_id: str) -> Path:
        return self._root / f"{normalize_case_id(case_id)}.holdout.json"

    def _read_payload(self, case_id: str) -> dict[str, Any]:
        path = self._bundle_path(case_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise HoldoutNotFoundError(f"no holdout bundle provisioned for case {case_id}") from error
        except OSError as error:
            raise HoldoutStoreError(f"holdout bundle for case {case_id} is unreadable") from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise HoldoutStoreError(f"holdout bundle for case {case_id} is not valid JSON") from error
        if not isinstance(payload, dict) or set(payload) != set(_BUNDLE_PARTS):
            raise HoldoutStoreError(
                f"holdout bundle for case {case_id} must contain exactly {sorted(_BUNDLE_PARTS)}",
            )
        if any(not isinstance(payload[part], dict) for part in _BUNDLE_PARTS):
            raise HoldoutStoreError(f"holdout bundle parts for case {case_id} must be objects")
        return payload

    def has_case(self, case_id: str) -> bool:
        return self._bundle_path(case_id).is_file()

    def bundle_digest(self, case_id: str) -> str:
        return _digest(self._read_payload(normalize_case_id(case_id)))

    def isolation_report(self, target: TargetPrincipal) -> IsolationReport:
        if target.os_uid == 0:
            raise HoldoutBoundaryError(
                f"target {target.role_type} runs as uid 0; root bypasses discretionary "
                f"access control, so no owner or mode on {self._root} isolates this store "
                "from it",
            )

        if target.workspace_root is not None:
            workspace = target.workspace_root.resolve()
            if _is_within(self._root, workspace):
                return IsolationReport(
                    isolated=False,
                    mechanism="workspace_containment",
                    reason=f"store {self._root} lies inside the target workspace {workspace}",
                )

        if target.confinement is Confinement.BROKER_SANDBOX:
            declared = set(target.readable_roots)
            staging_root = target.staging_root
            if staging_root is None or staging_root not in declared:
                raise HoldoutStoreError(
                    f"broker-confined target {target.role_type} declares no staging root "
                    "among its readable roots; the sandbox grants file-read* and "
                    "file-write* on that subpath, so isolation cannot be derived",
                )
            missing = sorted(
                str(root) for root in _RESOLVED_BROKER_READ_ROOTS if root not in declared
            )
            if missing:
                raise HoldoutStoreError(
                    f"broker-confined target {target.role_type} omits sandbox-readable OS "
                    f"roots {missing}; isolation cannot be derived from an incomplete "
                    "grant list",
                )
            for readable in target.readable_roots:
                if _is_within(self._root, readable):
                    return IsolationReport(
                        isolated=False,
                        mechanism="broker_sandbox_confinement",
                        reason=f"store {self._root} lies under sandbox-readable root {readable}",
                    )
            return IsolationReport(
                isolated=True,
                mechanism="broker_sandbox_confinement",
                reason=(
                    "the deny-default sandbox profile grants content access only to the OS "
                    f"closure and the private staging root {staging_root}, neither of which "
                    f"covers {self._root}"
                ),
            )

        if target.confinement is Confinement.UNCONFINED:
            # An unconfined role holds raw Bash at the host OS principal, with
            # sudo/admin escalation available. Owner uid and mode bound nothing
            # it cannot lift, so V1 makes no isolation claim at all rather than
            # a DAC-shaped one that is false the moment the role escalates.
            return IsolationReport(
                isolated=False,
                mechanism="unconfined_role_unevaluable",
                reason=(
                    f"target {target.role_type} holds raw, unscoped Bash and Read at the "
                    f"host OS principal (uid {target.os_uid}); file ownership and mode on "
                    f"{self._root} do not bound it, so an unconfined role cannot be "
                    "evaluated against a holdout until real per-role ACLs, a separate host, "
                    "or a separate credential exists"
                ),
            )

        raise HoldoutStoreError(f"unknown target confinement: {target.confinement!r}")

    def _load_bundle(self, case_id: str, *, authorization: FetchAuthorization) -> HoldoutBundle:
        self._require_authorization(authorization, case_id)
        payload = self._read_payload(case_id)
        return HoldoutBundle(
            case_id=case_id,
            content_sha256=_digest(payload),
            expected=payload["expected"],
            graders=payload["graders"],
            fixture=payload["fixture"],
        )
