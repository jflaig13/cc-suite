# SPDX-License-Identifier: MPL-2.0
"""EP-110 — the canonical `EvalTargetManifest` fingerprint.

One evaluated target is the whole system under test, not just its code: the
release closure, the model route and its sampling configuration, the system and
role instructions, the full skill bundle, the tool/MCP surface, the tenant
policy in force, and the runtime dependency set.  Identical targets fingerprint
identically; any behaviorally-relevant change fingerprints differently.

Reuse (Gate A packet §8):

* Canonicalization is the Kernel's existing canonical-JSON-then-SHA-256 pattern,
  imported from ``fleet_kernel.identity`` rather than re-derived here.
* Code identity is consumed through the already-computed ``package_sha256`` of a
  Company role package (``company_scribe_admission.build_company_role_package``),
  not by re-enumerating its file closure.
* The MCP/tool config digest is the existing ``workspace_binding_sha256``
  (``fleet_kernel/company_role_runtime.py``), folded in as an input.

Net-new here: the full skill bundle (the existing closure hashes exactly one
``init-<role>/SKILL.md``), the model route with sampling configuration (today a
mutable ``kernel_runtime_workers.model_route`` column, hashed into no release
identity anywhere), the tool definition/schema/permission surface, and the
runtime dependency manifest.

There is deliberately no resolver/skill-index manifest: no skill resolver, index
or routing registry exists in this repository.  Selection is host-native
description matching, so the skill *descriptions* themselves are the behaviorally
relevant input and are carried verbatim in the hash basis per Gate A packet §7(C).

Because there is no registry to fingerprint instead, §7(C) makes the instruction
set, the skill bundle and the tool surface *mandatory* manifest inputs, not
conveniences.  A target that genuinely has none of one of them fingerprints only
behind a `BehavioralInputExemption`, which is itself in the hash basis: the
decision to evaluate a target without one of those inputs is a fingerprinted,
auditable choice rather than an invisible default.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

# Reuse of the Kernel's single canonicalization scheme.  It is module-private in
# `identity`, and EP-110 may not edit that file, so it is imported rather than
# re-implemented -- a second canonicalizer would be a second source of truth.
from fleet_kernel.identity import _canonical_json_bytes as canonical_json_bytes

EVAL_TARGET_MANIFEST_SCHEMA = "mise.eval-target-manifest.v1"

RESOLVER_MANIFEST_NOT_APPLICABLE: Mapping[str, Any] = MappingProxyType({
    "status": "not_applicable",
    "reason": (
        "No skill resolver, index, or routing registry exists in this repository. "
        "Skill selection is host-native description matching, so the skill and "
        "tool descriptions carried in this manifest are the behaviorally relevant "
        "inputs (Gate A packet section 7(C))."
    ),
})

# Bookkeeping only.  Excluded from the hash basis so that re-deriving the same
# target at a different wall-clock time on a different host yields the same
# fingerprint.  `EvalTargetManifest._record` emits every field; this frozenset is
# the only thing that decides which of them the digest covers.
HASH_EXCLUDED_FIELDS = frozenset({"generated_at", "generated_by", "notes"})

# The §7(C) inputs that carry no exactness check of their own -- an empty mapping
# or empty sequence is structurally valid but behaviorally meaningless.  The
# model route and the runtime manifest are mandated by the same paragraph and are
# already enforced: neither is optional, and both reject empty text.
SECTION_7C_REQUIRED_INPUTS = ("instructions", "skills", "tools")

_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"\A[0-9a-f]{40}\Z")


class EvalTargetFingerprintError(ValueError):
    """The described evaluation target is not exact enough to fingerprint."""


def _text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or any(ord(char) < 32 for char in text):
        raise EvalTargetFingerprintError(f"{field_name} is missing or invalid")
    return text


def _digest(value: object, field_name: str, pattern: re.Pattern[str] = _SHA256) -> str:
    text = _text(value, field_name)
    if pattern.fullmatch(text) is None:
        raise EvalTargetFingerprintError(f"{field_name} is not an exact digest")
    return text


def _optional_digest(
    value: object, field_name: str, pattern: re.Pattern[str] = _SHA256,
) -> str | None:
    return None if value is None else _digest(value, field_name, pattern)


def _compact(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Drop absent optional inputs so adding one later is not a silent rehash."""
    return {key: value for key, value in payload.items() if value is not None}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_canonical(value: Mapping[str, Any] | Sequence[Any]) -> str:
    return sha256_bytes(canonical_json_bytes(value))


@dataclass(frozen=True, slots=True)
class CodeIdentity:
    """The exact source under evaluation.

    ``package_sha256`` is the existing Company-role package digest: code closure
    + ``HARNESS_CORE.md`` + one init skill, already content-addressed.  This
    manifest consumes it whole rather than re-enumerating the file list.
    """

    source_commit_sha: str
    source_tree_sha: str
    kernel_release_sha256: str | None = None
    package_sha256: str | None = None
    agent_release_sha256: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return _compact({
            "source_commit_sha": _digest(
                self.source_commit_sha, "source_commit_sha", _GIT_SHA,
            ),
            "source_tree_sha": _digest(
                self.source_tree_sha, "source_tree_sha", _GIT_SHA,
            ),
            "kernel_release_sha256": _optional_digest(
                self.kernel_release_sha256, "kernel_release_sha256",
            ),
            "package_sha256": _optional_digest(
                self.package_sha256, "package_sha256",
            ),
            "agent_release_sha256": _optional_digest(
                self.agent_release_sha256, "agent_release_sha256",
            ),
        })


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """Exact model identity plus the sampling configuration it runs under.

    Today the route survives only as the mutable ``kernel_runtime_workers.
    model_route`` column and is hashed into no release or package identity.  A
    qualification earned on one route must not silently transfer to another, so
    the route is a first-class hash input here.
    """

    provider: str
    model_id: str
    sampling: Mapping[str, Any] = field(default_factory=dict)
    effort_level: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        sampling = {
            _text(key, "sampling parameter name"): value
            for key, value in dict(self.sampling).items()
            if value is not None
        }
        return _compact({
            "provider": _text(self.provider, "model provider"),
            "model_id": _text(self.model_id, "model id"),
            "sampling": dict(sorted(sampling.items())),
            "effort_level": (
                None if self.effort_level is None
                else _text(self.effort_level, "effort level")
            ),
        })


@dataclass(frozen=True, slots=True)
class SkillRecord:
    """One skill in the bundle.

    ``description`` is carried verbatim, not only inside ``content_sha256``,
    because it is the literal input host-native selection matches against.
    """

    path: str
    content_sha256: str
    name: str | None = None
    description: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return _compact({
            "path": _text(self.path, "skill path"),
            "content_sha256": _digest(self.content_sha256, "skill content_sha256"),
            "name": None if self.name is None else _text(self.name, "skill name"),
            "description": (
                None if self.description is None
                else _text(self.description, "skill description")
            ),
        })


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema_sha256: str
    permission: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return _compact({
            "name": _text(self.name, "tool name"),
            "description": _text(self.description, "tool description"),
            "input_schema_sha256": _digest(
                self.input_schema_sha256, "tool input_schema_sha256",
            ),
            "permission": (
                None if self.permission is None
                else _text(self.permission, "tool permission")
            ),
        })


@dataclass(frozen=True, slots=True)
class ToolSurface:
    """Available tools plus the existing launch-time MCP binding digests."""

    tools: Sequence[ToolDefinition] = ()
    workspace_binding_sha256: str | None = None
    mcp_config_sha256: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        payloads = [tool.canonical_payload() for tool in self.tools]
        names = [payload["name"] for payload in payloads]
        if len(set(names)) != len(names):
            raise EvalTargetFingerprintError("tool surface names must be unique")
        return _compact({
            "tools": sorted(payloads, key=lambda payload: payload["name"]),
            "workspace_binding_sha256": _optional_digest(
                self.workspace_binding_sha256, "workspace_binding_sha256",
            ),
            "mcp_config_sha256": _optional_digest(
                self.mcp_config_sha256, "mcp_config_sha256",
            ),
        })


@dataclass(frozen=True, slots=True)
class TenantPolicySnapshot:
    tenant_id: str
    policy_sha256: str
    manifest_sha256: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return _compact({
            "tenant_id": _text(self.tenant_id, "tenant_id"),
            "policy_sha256": _digest(self.policy_sha256, "policy_sha256"),
            "manifest_sha256": _optional_digest(
                self.manifest_sha256, "tenant manifest_sha256",
            ),
        })


@dataclass(frozen=True, slots=True)
class RuntimeManifest:
    harness: str
    harness_version: str
    python_version: str
    dependencies: Mapping[str, str] = field(default_factory=dict)

    def canonical_payload(self) -> dict[str, Any]:
        dependencies = {
            _text(name, "dependency name"): _text(version, f"{name} version")
            for name, version in dict(self.dependencies).items()
        }
        return {
            "harness": _text(self.harness, "harness"),
            "harness_version": _text(self.harness_version, "harness_version"),
            "python_version": _text(self.python_version, "python_version"),
            "dependencies": dict(sorted(dependencies.items())),
        }


@dataclass(frozen=True, slots=True)
class BehavioralInputExemption:
    """A declared, attributed decision to fingerprint a target that truly lacks a
    §7(C) input -- say a deterministic tool-only target with no instruction set.

    No such target type exists in this repository today: every fleet role runs an
    LLM under `HARNESS_CORE.md` with at least its own `init-<role>/SKILL.md`.  The
    exemption exists so that the first one is described honestly instead of
    weakening the mandate for every target, and it is in the hash basis so the
    exemption cannot be granted, or later widened, without moving the digest.
    """

    absent_inputs: Sequence[str]
    reason: str
    authorized_by: str

    def canonical_payload(self) -> dict[str, Any]:
        names = [_text(name, "exempted input name") for name in self.absent_inputs]
        if not names:
            raise EvalTargetFingerprintError("exemption declares no absent input")
        if len(set(names)) != len(names):
            raise EvalTargetFingerprintError("exemption repeats an absent input")
        unknown = sorted(set(names) - set(SECTION_7C_REQUIRED_INPUTS))
        if unknown:
            raise EvalTargetFingerprintError(
                "exemption names inputs that section 7(C) does not mandate: "
                + ",".join(unknown)
            )
        return {
            "absent_inputs": sorted(names),
            "reason": _text(self.reason, "exemption reason"),
            "authorized_by": _text(self.authorized_by, "exemption authorized_by"),
        }


@dataclass(frozen=True, slots=True)
class EvalTargetManifest:
    """The exact system under evaluation, reducible to one stable digest."""

    target_id: str
    fleet_type: str
    role_type: str
    code: CodeIdentity
    model: ModelRoute
    runtime: RuntimeManifest
    instructions: Mapping[str, str]
    skills: Sequence[SkillRecord]
    tools: ToolSurface
    tenant_policy: TenantPolicySnapshot | None = None
    behavioral_input_exemption: BehavioralInputExemption | None = None
    resolver_manifest: Mapping[str, Any] = RESOLVER_MANIFEST_NOT_APPLICABLE
    generated_at: datetime | None = None
    generated_by: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        populated = {
            "instructions": bool(dict(self.instructions)),
            "skills": bool(tuple(self.skills)),
            "tools": bool(tuple(self.tools.tools)),
        }
        absent = {name for name, filled in populated.items() if not filled}
        exemption = self.behavioral_input_exemption
        declared = set() if exemption is None else set(
            exemption.canonical_payload()["absent_inputs"],
        )
        if absent - declared:
            raise EvalTargetFingerprintError(
                "target manifest omits Gate A packet section 7(C) inputs with no "
                "declared exemption: " + ",".join(sorted(absent - declared))
            )
        if declared - absent:
            raise EvalTargetFingerprintError(
                "exemption claims inputs the manifest actually carries: "
                + ",".join(sorted(declared - absent))
            )

    def canonical_payload(self) -> dict[str, Any]:
        """The hash basis: every recorded field except `HASH_EXCLUDED_FIELDS`."""
        return {
            key: value for key, value in self._record().items()
            if key not in HASH_EXCLUDED_FIELDS
        }

    def _record(self) -> dict[str, Any]:
        """Every declared field, hash basis and bookkeeping alike.

        `HASH_EXCLUDED_FIELDS` is the single place that decides which of these the
        digest covers; nothing here restates that split.
        """
        instructions = {
            _text(name, "instruction name"): _digest(
                digest, f"instruction digest for {name}",
            )
            for name, digest in dict(self.instructions).items()
        }
        skills = [skill.canonical_payload() for skill in self.skills]
        paths = [skill["path"] for skill in skills]
        if len(set(paths)) != len(paths):
            raise EvalTargetFingerprintError("skill bundle paths must be unique")
        return {
            "schema": EVAL_TARGET_MANIFEST_SCHEMA,
            "target_id": _text(self.target_id, "target_id"),
            "fleet_type": _text(self.fleet_type, "fleet_type"),
            "role_type": _text(self.role_type, "role_type"),
            "code": self.code.canonical_payload(),
            "model": self.model.canonical_payload(),
            "instructions": dict(sorted(instructions.items())),
            "skills": sorted(skills, key=lambda skill: skill["path"]),
            "resolver_manifest": dict(self.resolver_manifest),
            "tools": self.tools.canonical_payload(),
            "tenant_policy": (
                None if self.tenant_policy is None
                else self.tenant_policy.canonical_payload()
            ),
            "runtime": self.runtime.canonical_payload(),
            "behavioral_input_exemption": (
                None if self.behavioral_input_exemption is None
                else self.behavioral_input_exemption.canonical_payload()
            ),
            "generated_at": (
                None if self.generated_at is None
                else self.generated_at.isoformat()
            ),
            "generated_by": self.generated_by,
            "notes": self.notes,
        }

    @property
    def fingerprint_sha256(self) -> str:
        return sha256_canonical(self.canonical_payload())

    def envelope(self) -> dict[str, Any]:
        """Hash basis plus the digest and the excluded bookkeeping fields."""
        return {
            **self._record(),
            "target_fingerprint_sha256": sha256_canonical(self.canonical_payload()),
        }


_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_FRONTMATTER_FIELD = re.compile(r"^(name|description):\s*(.+?)\s*$", re.MULTILINE)


def skill_record(repo: Path, relative_path: str) -> SkillRecord:
    """Content-hash one `SKILL.md` and lift its selection-relevant frontmatter."""
    source = (repo / relative_path).resolve()
    if not source.is_file():
        raise EvalTargetFingerprintError(f"skill file is absent: {relative_path}")
    raw = source.read_bytes()
    fields: dict[str, str] = {}
    header = _FRONTMATTER.match(raw.decode("utf-8", errors="replace"))
    if header is not None:
        fields = {
            match.group(1): match.group(2).strip().strip("'\"")
            for match in _FRONTMATTER_FIELD.finditer(header.group(1))
        }
    return SkillRecord(
        path=Path(relative_path).as_posix(),
        content_sha256=sha256_bytes(raw),
        name=fields.get("name"),
        description=fields.get("description"),
    )


def skill_bundle(
    repo: Path, roots: Sequence[str] = (".agents/skills", ".claude/skills"),
) -> tuple[SkillRecord, ...]:
    """Content-hash the FULL skill bundle reachable by the target.

    The existing Company-role closure
    (``company_scribe_admission.company_role_release_files``) hashes exactly one
    ``init-<role>/SKILL.md``; every other skill the host can select is invisible
    to it.  For skill-selection and routing evals that is the wrong closure --
    the entire selectable set is the behaviorally relevant input.
    """
    records: dict[str, SkillRecord] = {}
    for root in roots:
        base = repo / root
        if not base.is_dir():
            continue
        for path in base.rglob("SKILL.md"):
            relative = path.relative_to(repo).as_posix()
            records[relative] = skill_record(repo, relative)
    return tuple(records[key] for key in sorted(records))


def tool_definition(
    *,
    name: str,
    description: str,
    input_schema: Mapping[str, Any],
    permission: str | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema_sha256=sha256_canonical(input_schema),
        permission=permission,
    )


def code_identity_from_package_manifest(
    manifest: Mapping[str, Any],
) -> CodeIdentity:
    """Fold an existing built Company-role package manifest in as code identity."""
    missing = {"source_commit_sha", "source_tree_sha", "package_sha256"} - set(manifest)
    if missing:
        raise EvalTargetFingerprintError(
            "package manifest omits code identity: " + ",".join(sorted(missing))
        )
    return CodeIdentity(
        source_commit_sha=manifest["source_commit_sha"],
        source_tree_sha=manifest["source_tree_sha"],
        kernel_release_sha256=manifest.get("kernel_release_sha256"),
        package_sha256=manifest["package_sha256"],
        agent_release_sha256=manifest.get("agent_release_sha256"),
    )


def instruction_digests(repo: Path, paths: Mapping[str, str]) -> dict[str, str]:
    """Hash the host/system/role instruction documents in force for the target."""
    digests: dict[str, str] = {}
    for name, relative in paths.items():
        source = (repo / relative).resolve()
        if not source.is_file():
            raise EvalTargetFingerprintError(f"instruction file is absent: {relative}")
        digests[name] = sha256_file(source)
    return digests
