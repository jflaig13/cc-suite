# SPDX-License-Identifier: MPL-2.0
"""Durable, tenant-bound Fleet Kernel identity plane.

Prompts describe work; they never grant identity. Identity is the chain:
content-addressed role release -> tenant manifest -> durable worker -> leased
session -> capability grant -> run. Every database relation carries the tenant
key so an application bug cannot silently turn into cross-tenant authority.
"""
from __future__ import annotations
from fleet_kernel.configuration import company_id, scribe_deployment_id, authority_issuer, deployment_binding

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class IdentityPlaneError(ValueError):
    """The requested identity transition is not allowed."""


class FleetType(str, Enum):
    COMPANY = "company"
    RESTAURANT = "restaurant"


class EffortLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ModelEffortFamily(str, Enum):
    FABLE = "fable"
    GROK = "grok"
    CODEX = "codex"
    OTHER = "other"


# Founder law 2026-08-12: admitted effort is model-family-scoped.
# Fable: low|medium|high|xhigh|max. Grok: low|medium|high|xhigh (no max).
# Codex and other current kernel families stay on low|medium|high.
ADMITTED_EFFORTS_BY_FAMILY: dict[ModelEffortFamily, frozenset[EffortLevel]] = {
    ModelEffortFamily.FABLE: frozenset({
        EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH,
        EffortLevel.XHIGH, EffortLevel.MAX,
    }),
    ModelEffortFamily.GROK: frozenset({
        EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH,
        EffortLevel.XHIGH,
    }),
    ModelEffortFamily.CODEX: frozenset({
        EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH,
    }),
    ModelEffortFamily.OTHER: frozenset({
        EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH,
    }),
}


def model_effort_family(model_id: str) -> ModelEffortFamily:
    """Map a model id onto the effort-family vocabulary.

    Grok ids include the current `grok-4.6` and any other `grok*` / `*-grok-*`
    form. This helper does not remap role default models.
    """
    lowered = (model_id or "").strip().lower()
    if "fable" in lowered:
        return ModelEffortFamily.FABLE
    if lowered.startswith("grok") or "-grok-" in lowered or lowered.startswith("xai-grok"):
        return ModelEffortFamily.GROK
    if lowered.startswith("codex"):
        return ModelEffortFamily.CODEX
    if lowered.startswith("gpt-") and lowered.endswith("-sol"):
        # Sol models use the Codex admitted-effort vocabulary here;
        # this classification does not change the model identity.
        return ModelEffortFamily.CODEX
    return ModelEffortFamily.OTHER


def admitted_efforts_for_model(model_id: str) -> frozenset[EffortLevel]:
    return ADMITTED_EFFORTS_BY_FAMILY[model_effort_family(model_id)]


def assert_effort_admitted_for_model(model_id: str, effort: EffortLevel | str) -> None:
    level = effort if isinstance(effort, EffortLevel) else EffortLevel(effort)
    family = model_effort_family(model_id)
    if level not in ADMITTED_EFFORTS_BY_FAMILY[family]:
        raise IdentityPlaneError(
            f"effort '{level.value}' is not admitted for model family "
            f"'{family.value}' ({model_id})"
        )


def _assert_release_efforts(spec: AgentTypeSpec) -> None:
    assert_effort_admitted_for_model(spec.default_model, spec.default_effort)
    for level in spec.allowed_effort_levels:
        assert_effort_admitted_for_model(spec.default_model, level)
    if spec.default_effort not in spec.allowed_effort_levels:
        raise IdentityPlaneError("default effort must be in the release allowed set")


RESTAURANT_ROLE_TYPES = frozenset({
    "mise_orchestrator",
    "payroll_worker",
    "inventory_worker",
    "ordering_worker",
    "fable_verifier",
    "browser_navigator",
    "browser_advisor",
})
COMPANY_TENANT_ID = company_id()
# The three product modules a Restaurant Fleet can run. A tenant provisions
# with the ones it has (one, two, or three); the manifest records which.
# Nothing refuses a tenant for lacking a module (HARNESS_CORE §0 class 1,
# killed 2026-09-02: the old three-module equality guarded manifest shape,
# never a customer's money or data).
RESTAURANT_MODULES = frozenset({"payroll", "inventory", "ordering"})
COMPANY_ROLE_TYPES = frozenset({
    "scribe", "cos", "ccto", "ccpo", "ccde", "ccro", "ccfo", "ccmo",
    "cclo", "ccgo", "ccco", "utility",
})


def company_worker_database_principal(worker_id: uuid.UUID, role_type: str) -> str:
    if role_type not in COMPANY_ROLE_TYPES:
        raise IdentityPlaneError("company database principal requires a canonical Company role")
    if role_type == "ccpo":
        return "mise_company_reviewer"
    digest = hashlib.sha256(f"{worker_id}:{role_type}".encode()).hexdigest()[:16]
    return f"mise_company_{role_type}_{digest}"


@dataclass(frozen=True, slots=True)
class AgentTypeSpec:
    fleet_type: FleetType
    role_type: str
    release_version: int
    charter: str
    objective_classes: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    allowed_network_classes: tuple[str, ...]
    default_model: str
    default_effort: EffortLevel
    allowed_effort_levels: tuple[EffortLevel, ...]
    can_plan: bool
    can_execute: bool
    can_review: bool
    evidence_requirements: tuple[str, ...]
    consequence_tiers: tuple[str, ...]
    memory_surfaces: tuple[str, ...]

    def canonical_payload(self) -> dict:
        return {
            "fleet_type": self.fleet_type.value,
            "role_type": self.role_type,
            "release_version": self.release_version,
            "charter": self.charter,
            "objective_classes": sorted(self.objective_classes),
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "allowed_network_classes": sorted(self.allowed_network_classes),
            "default_model": self.default_model,
            "default_effort": self.default_effort.value,
            "allowed_effort_levels": sorted(level.value for level in self.allowed_effort_levels),
            "can_plan": self.can_plan,
            "can_execute": self.can_execute,
            "can_review": self.can_review,
            "evidence_requirements": sorted(self.evidence_requirements),
            "consequence_tiers": sorted(self.consequence_tiers),
            "memory_surfaces": sorted(self.memory_surfaces),
        }

    @property
    def release_sha256(self) -> str:
        return hashlib.sha256(_canonical_json_bytes(self.canonical_payload())).hexdigest()


TENANT_MEMORY = (
    "sanitized_shared_operational_knowledge",
    "tenant_policy_and_configuration",
    "tenant_private_memory_and_history",
    "objective_minimum_necessary_context",
)


# Founder 9/1, as corrected 9/3: "Every agent of the CC-suite should be running
# Fable 5.1. The client fleets should still be running the models they were
# supposed to be running, EXCEPT the agents that were on Fable 5 need to be
# moved to Fable 5.1." So every Company seat is claude-fable-5-1, and on a
# Restaurant Fleet the workers stay on Grok, the navigator stays on Codex, and
# the three Fable seats (orchestrator, verifier, advisor) are 5.1. Producer and
# verifier remain different vendors, which is what makes a Restaurant review
# independent.
#
# This set answers "does this model count as a Fable formal reviewer", not
# "may a seat run it". It keeps the retired id because a review banked under
# claude-fable-5 is provenance: DDL 118 accepts both in SQL, and the D13
# cutover gate reads banked evidence through this set. Which model a LIVE seat
# may run is the registry's job, enforced exactly by the mint guard against
# RESTAURANT_ROLE_SPECS and by DDL 117's model_route check.
FABLE_REVIEW_MODELS = frozenset({"claude-fable-5", "claude-fable-5-1"})

RESTAURANT_ROLE_SPECS: tuple[AgentTypeSpec, ...] = (
    AgentTypeSpec(
        FleetType.RESTAURANT, "mise_orchestrator", 1,
        "Notice work, plan bounded objectives, coordinate workers, present uncertainty, and escalate decisions.",
        ("ask_mise", "scheduled_operations", "workflow_coordination", "decision_presentation"),
        ("objective.propose", "objective.coordinate", "decision.present", "evidence.read"),
        (), "claude-fable-5-1", EffortLevel.HIGH,
        (EffortLevel.MEDIUM, EffortLevel.HIGH), True, False, False,
        ("objective_plan", "uncertainty_record", "operator_escalation"),
        ("routine", "money", "people", "policy", "outside_commitment"), TENANT_MEMORY,
    ),
    AgentTypeSpec(
        FleetType.RESTAURANT, "payroll_worker", 1,
        "Prepare penny-exact payroll work from authorized restaurant sources without submitting payroll.",
        ("payroll.prepare", "payroll.reconcile"),
        ("payroll.read", "payroll.prepare", "evidence.write"),
        ("tenant_approved_payroll_sources",), "grok-4.6", EffortLevel.XHIGH,
        (EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH), False, True, False,
        ("source_population", "penny_exact_output", "freshness", "reconciliation"),
        ("money", "people"), TENANT_MEMORY,
    ),
    AgentTypeSpec(
        FleetType.RESTAURANT, "inventory_worker", 1,
        "Process counts, product resolution, discrepancies, usage, and replenishment signals.",
        ("inventory.prepare", "inventory.reconcile"),
        ("inventory.read", "inventory.prepare", "catalog.resolve", "evidence.write"),
        ("tenant_approved_inventory_sources",), "grok-4.6", EffortLevel.XHIGH,
        (EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH), False, True, False,
        ("count_source", "resolution_basis", "discrepancy_record"),
        ("routine", "money"), TENANT_MEMORY,
    ),
    AgentTypeSpec(
        FleetType.RESTAURANT, "ordering_worker", 1,
        "Prepare evidence-backed vendor order drafts with quantity basis and quarantined uncertainty; never place an order.",
        ("ordering.prepare", "ordering.reconcile"),
        ("ordering.prepare", "catalog.resolve", "vendor_draft.prepare", "evidence.write"),
        ("tenant_approved_ordering_sources",), "grok-4.6", EffortLevel.XHIGH,
        (EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH), False, True, False,
        ("quantity_basis", "vendor_mapping", "uncertainty_quarantine", "duplicate_check"),
        ("money", "outside_commitment"), TENANT_MEMORY,
    ),
    AgentTypeSpec(
        FleetType.RESTAURANT, "fable_verifier", 1,
        "Independently review worker-produced work in a fresh Fable context and issue the sole LLM verdict.",
        ("verification.review",),
        ("review.verdict", "evidence.read"),
        (), "claude-fable-5-1", EffortLevel.XHIGH,
        (EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH, EffortLevel.MAX), False, False, True,
        ("exact_subject_digest", "fresh_context", "independent_verdict"),
        ("routine", "money", "people", "policy", "credentials", "outside_commitment"),
        ("sanitized_shared_operational_knowledge", "tenant_policy_and_configuration", "objective_minimum_necessary_context"),
    ),
    AgentTypeSpec(
        FleetType.RESTAURANT, "browser_navigator", 1,
        "Execute only permitted browser actions through the Kernel tool chokepoint with evidence and safe stops.",
        ("browser.navigate", "payroll.upload"),
        ("browser.navigate", "browser.capture", "payroll.upload", "evidence.write"),
        ("tenant_approved_browser_destinations",), "codex-current", EffortLevel.HIGH,
        (EffortLevel.HIGH,), False, True, False,
        ("pre_action_state", "post_action_state", "idempotency_key", "reconciliation"),
        ("credentials", "money", "people", "outside_commitment"),
        ("sanitized_shared_operational_knowledge", "tenant_policy_and_configuration", "objective_minimum_necessary_context"),
    ),
    AgentTypeSpec(
        FleetType.RESTAURANT, "browser_advisor", 1,
        "Provide sanitized site-behavior knowledge and recovery guidance without acting or reviewing.",
        ("browser.advise",),
        ("browser.advise", "site_knowledge.read"),
        (), "claude-fable-5-1", EffortLevel.XHIGH,
        (EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH, EffortLevel.MAX), False, False, False,
        ("sanitized_site_basis", "recovery_guidance"),
        ("routine", "credentials"),
        ("sanitized_shared_operational_knowledge", "objective_minimum_necessary_context"),
    ),
)


COMPANY_MEMORY = (
    "company_canon_and_policy",
    "company_private_documents",
    "company_operating_history",
    "company_objective_minimum_necessary_context",
)


def _company_spec(
    role_type: str,
    charter: str,
    objective_classes: tuple[str, ...],
    capabilities: tuple[str, ...],
    model: str,
    effort: EffortLevel,
    *,
    can_plan: bool = False,
    can_execute: bool = False,
    can_review: bool = False,
    consequence_tiers: tuple[str, ...] = ("routine", "company_confidential"),
    allowed_effort_levels: tuple[EffortLevel, ...] | None = None,
) -> AgentTypeSpec:
    if allowed_effort_levels is None:
        efforts = (effort,) if effort == EffortLevel.HIGH else (effort, EffortLevel.HIGH)
    else:
        efforts = allowed_effort_levels
    return AgentTypeSpec(
        FleetType.COMPANY, role_type, 1, charter, objective_classes, capabilities, (),
        model, effort, efforts, can_plan, can_execute, can_review,
        ("company_objective", "source_basis", "durable_receipt"),
        consequence_tiers, COMPANY_MEMORY,
    )


COMPANY_ROLE_SPECS: tuple[AgentTypeSpec, ...] = (
    _company_spec(
        "scribe","Preserve Mise Inc. continuity, canon, identity, receipts, and fleet coordination.",
        ("institutional_memory","governance","fleet_coordination"),
        ("company.canon","company.registry","company.receipt","company.handoff"),
        "claude-fable-5-1", EffortLevel.HIGH, can_plan=True,
    ),
    _company_spec(
        "cos","Route company objectives, dependencies, schedules, and founder attention across lanes.",
        ("company_routing","scheduling","decision_escalation"),
        ("company.route","company.schedule","company.escalate","company.handoff"),
        "claude-fable-5-1", EffortLevel.HIGH, can_plan=True,
        allowed_effort_levels=(
            EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH,
        ),
    ),
    _company_spec(
        "ccto","Design technical architecture and plans without issuing the independent review verdict.",
        ("technical_architecture","technical_planning"),
        ("company.plan.technical","company.architecture","company.handoff"),
        "claude-fable-5-1", EffortLevel.HIGH, can_plan=True,
        allowed_effort_levels=(
            EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH, EffortLevel.MAX,
        ),
    ),
    _company_spec(
        "ccpo","Independently verify exact company work in fresh Fable context and own the sole LLM verdict.",
        ("product_planning","verification","quality"),
        ("company.plan.product","company.review.verdict","company.evidence.read"),
        "claude-fable-5-1", EffortLevel.HIGH, can_plan=True, can_review=True,
        allowed_effort_levels=(
            EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH, EffortLevel.MAX,
        ),
    ),
    _company_spec(
        "ccde","Implement reviewed technical plans and produce exact build evidence.",
        ("engineering_build","maintenance"),
        ("company.code","company.test","company.build.receipt"),
        "claude-fable-5-1", EffortLevel.HIGH, can_execute=True,
        allowed_effort_levels=(
            EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH,
        ),
    ),
    _company_spec(
        "ccro","Identify, measure, and mitigate company, product, security, and operational risk.",
        ("risk","security","compliance"),
        ("company.risk","company.audit","company.escalate"),
        "claude-fable-5-1", EffortLevel.HIGH, can_plan=True,
        allowed_effort_levels=(
            EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH,
        ),
        consequence_tiers=("routine", "company_confidential", "security", "policy"),
    ),
    _company_spec(
        "ccfo","Track company spend, runway, budgets, and financial decisions from authoritative sources.",
        ("finance","accounting","budgeting"),
        ("company.finance","company.budget","company.receipt"),
        "claude-fable-5-1", EffortLevel.HIGH, can_execute=True,
        allowed_effort_levels=(
            EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH,
        ),
        consequence_tiers=("money", "company_confidential", "outside_commitment"),
    ),
    _company_spec(
        "ccmo","Create and maintain company marketing strategy and assets under Mise brand policy.",
        ("marketing","brand","content"),
        ("company.marketing","company.asset.prepare","company.receipt"),
        "claude-fable-5-1", EffortLevel.HIGH, can_execute=True,
        allowed_effort_levels=(
            EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH,
        ),
    ),
    _company_spec(
        "cclo","Prepare company legal analysis and documents while preserving founder execution authority.",
        ("legal","corporate","contracts"),
        ("company.legal","company.document.prepare","company.escalate"),
        "claude-fable-5-1", EffortLevel.HIGH, can_execute=True,
        allowed_effort_levels=(
            EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH,
        ),
        consequence_tiers=("policy", "company_confidential", "outside_commitment"),
    ),
    _company_spec(
        "ccgo","Run company growth, prospect research, and operator-to-operator outreach preparation.",
        ("growth","outreach","pipeline"),
        ("company.growth","company.outreach.prepare","company.receipt"),
        "claude-fable-5-1", EffortLevel.HIGH, can_execute=True,
        allowed_effort_levels=(
            EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH,
        ),
        consequence_tiers=("routine", "company_confidential", "outside_commitment"),
    ),
    _company_spec(
        "ccco","Maintain design-partner, customer-pipeline, and company relationship quality.",
        ("customer_success","relationships","pipeline"),
        ("company.relationships","company.followup.prepare","company.receipt"),
        "claude-fable-5-1", EffortLevel.HIGH, can_execute=True,
        allowed_effort_levels=(
            EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH,
        ),
        consequence_tiers=("people", "company_confidential", "outside_commitment"),
    ),
    _company_spec(
        "utility","Handle founder-directed general Company work and the Pocket-notes standing duty without assuming another role's authority.",
        ("founder_utility","pocket_notes","general_company_support"),
        ("company.utility","company.notes","company.handoff"),
        "claude-fable-5-1", EffortLevel.HIGH, can_plan=True, can_execute=True,
        allowed_effort_levels=(
            EffortLevel.LOW, EffortLevel.MEDIUM, EffortLevel.HIGH, EffortLevel.XHIGH,
        ),
    ),
)


# Host-family admission: a Company seat starts only through a registered
# runtime whose model family matches the seat's configured default_model.
RUNTIME_KIND_FAMILIES: dict[str, ModelEffortFamily] = {
    "claude-code": ModelEffortFamily.FABLE,
    "grok-cli": ModelEffortFamily.GROK,
    # Codex runtime admission maps to the CODEX effort family.
    "codex-sol": ModelEffortFamily.CODEX,
}


def restaurant_role_spec(role_type: str) -> AgentTypeSpec:
    for spec in RESTAURANT_ROLE_SPECS:
        if spec.role_type == role_type:
            return spec
    raise IdentityPlaneError(
        f"'{role_type}' is not a canonical Restaurant role"
    )


def company_role_spec(role_type: str) -> AgentTypeSpec:
    for spec in COMPANY_ROLE_SPECS:
        if spec.role_type == role_type:
            return spec
    raise IdentityPlaneError(
        f"'{role_type}' is not a canonical Company role"
    )


def company_role_model_family(role_type: str) -> ModelEffortFamily:
    return model_effort_family(company_role_spec(role_type).default_model)


def assert_company_role_runtime_admitted(
    role_type: str, runtime_kind: str,
) -> None:
    """Refuse a Company seat start on a host outside its official family."""
    spec = company_role_spec(role_type)
    official = model_effort_family(spec.default_model)
    host_family = RUNTIME_KIND_FAMILIES.get(runtime_kind)
    if host_family is None:
        raise IdentityPlaneError(
            f"runtime kind '{runtime_kind}' has no admitted host family"
        )
    if host_family is not official:
        if official is ModelEffortFamily.GROK:
            pointer = "docs/runtime.md (retired model family; configure an admitted host)"
        elif official is ModelEffortFamily.CODEX:
            pointer = (
                "docs/runtime.md (configure an admitted role runtime)"
            )
        else:
            pointer = f"scripts/launch_claude.sh {role_type}"
        raise IdentityPlaneError(
            f"Company role '{role_type}' is officially {official.value} "
            f"({spec.default_model}); the '{runtime_kind}' host is "
            f"{host_family.value} and is refused — this seat clocks in "
            f"through {pointer}"
        )


if {spec.role_type for spec in RESTAURANT_ROLE_SPECS} != RESTAURANT_ROLE_TYPES:
    raise RuntimeError("restaurant role registry must contain exactly the seven canonical roles")
if any("order.place" in spec.allowed_capabilities for spec in RESTAURANT_ROLE_SPECS):
    raise RuntimeError("Restaurant Fleet must not contain order-placement authority")
if [spec.role_type for spec in RESTAURANT_ROLE_SPECS if spec.can_review] != ["fable_verifier"]:
    raise RuntimeError("Fable Verifier must be the sole review-capable Restaurant role")
if {spec.role_type for spec in COMPANY_ROLE_SPECS} != COMPANY_ROLE_TYPES:
    raise RuntimeError("company role registry must contain exactly the twelve canonical roles")
if [spec.role_type for spec in COMPANY_ROLE_SPECS if spec.can_review] != ["ccpo"]:
    raise RuntimeError("CCPO on Fable must be the sole review-capable Company role")
if any(spec.default_model not in FABLE_REVIEW_MODELS for spec in COMPANY_ROLE_SPECS if spec.can_review):
    raise RuntimeError("every formal Company review identity must be Fable")
if any(
    model_effort_family(spec.default_model) is ModelEffortFamily.CODEX
    for spec in (*COMPANY_ROLE_SPECS, *RESTAURANT_ROLE_SPECS)
    if spec.can_review
):
    raise RuntimeError("CMCV reviewer identities cannot default to Codex")
if set(COMPANY_MEMORY) & set(TENANT_MEMORY):
    raise RuntimeError("Company Fleet and Restaurant Fleet memory surfaces cannot overlap")


@dataclass(frozen=True, slots=True)
class ManifestRegistration:
    manifest_id: uuid.UUID
    tenant_id: str
    manifest_version: int
    manifest_sha256: str
    role_releases: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class WorkerRegistration:
    worker_id: uuid.UUID
    tenant_id: str
    manifest_id: uuid.UUID
    role_type: str
    release_sha256: str
    model_route: str
    effort_level: EffortLevel


@dataclass(frozen=True, slots=True)
class SessionRegistration:
    session_id: uuid.UUID
    worker_id: uuid.UUID
    tenant_id: str
    lease_token: str
    lease_expires_at: datetime


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _tenant_id(value: str) -> str:
    tenant = value.strip()
    if not tenant or len(tenant) > 128 or any(ord(char) < 32 for char in tenant):
        raise IdentityPlaneError("tenant_id is invalid")
    return tenant


def _aware(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IdentityPlaneError(f"{field} must be timezone-aware")
    return value


def install_agent_type_releases(
    conn: psycopg.Connection,
    specs: Iterable[AgentTypeSpec] = RESTAURANT_ROLE_SPECS,
) -> dict[str, str]:
    """Install immutable content-addressed role releases idempotently."""
    releases: dict[str, str] = {}
    with conn.cursor(row_factory=dict_row) as cursor:
        for spec in specs:
            if spec.release_version < 1 or not spec.role_type.strip():
                raise IdentityPlaneError("agent release identity is invalid")
            _assert_release_efforts(spec)
            # Codex round 6 MAJOR: the runtime-worker validator only checks
            # that a worker's route equals its release default, so a release
            # minted on a retired id could still become an active seat. Seat
            # identity is minted here, so a canonical role is refused unless
            # its model is the one the registry names for that role. Read from
            # the registries, never a literal. Non-canonical probe roles are
            # untouched: they can never bind to a manifest, which must carry
            # exactly the canonical role set.
            #
            # Codex round 13 MAJOR: this covered Restaurant roles only. Once
            # FABLE_REVIEW_MODELS went back to being the Fable-family
            # acceptance set (so a review banked under the retired id still
            # validates), the Company ccpo checks below stopped pinning an
            # exact model, and a ccpo release mutated to claude-fable-5 minted
            # cleanly. Both archetypes are pinned to their registry here.
            registry_model = None
            if spec.fleet_type is FleetType.RESTAURANT \
                    and spec.role_type in RESTAURANT_ROLE_TYPES:
                registry_model = restaurant_role_spec(spec.role_type).default_model
            elif spec.fleet_type is FleetType.COMPANY \
                    and spec.role_type in COMPANY_ROLE_TYPES:
                registry_model = company_role_spec(spec.role_type).default_model
            if registry_model is not None and spec.default_model != registry_model:
                raise IdentityPlaneError(
                    f"{spec.fleet_type.value} role '{spec.role_type}' is "
                    f"minted on '{spec.default_model}', not its registry "
                    f"model '{registry_model}'"
                )
            if spec.can_review and (
                spec.default_model not in FABLE_REVIEW_MODELS
                or (spec.fleet_type is FleetType.RESTAURANT and spec.role_type!="fable_verifier")
                or (spec.fleet_type is FleetType.COMPANY and spec.role_type!="ccpo")
            ):
                raise IdentityPlaneError("Fable is the sole allowed formal review model")
            # The reviewer ROLES are pinned regardless of can_review (Codex,
            # DDL 105 r3): a fable_verifier or ccpo release that does not
            # review, or routes elsewhere, is not a reviewer release.
            if spec.fleet_type is FleetType.RESTAURANT and spec.role_type == "fable_verifier" and (
                not spec.can_review or spec.default_model not in FABLE_REVIEW_MODELS
            ):
                raise IdentityPlaneError("the Restaurant reviewer is Fable and always reviews")
            if spec.fleet_type is FleetType.COMPANY and spec.role_type == "ccpo" and (
                not spec.can_review or spec.default_model not in FABLE_REVIEW_MODELS
            ):
                raise IdentityPlaneError("the Company reviewer is Fable and always reviews")
            release_sha = spec.release_sha256
            # Probe-then-skip (atom 9, reviewer ruling §21): the rows are a
            # pure function of the code version — static specs into
            # content-addressed immutable rows — so when a release is already
            # present the INSERT is a no-op in effect but NOT in privilege
            # (ON CONFLICT DO NOTHING still demands INSERT, checked before
            # the conflict can no-op).  A present row is VERIFIED, never
            # re-seeded; a genuinely absent row is deployment work, and under
            # a non-deploying credential the INSERT below refuses LOUDLY
            # (InsufficientPrivilege) rather than being silently skipped.
            release_columns = (
                "SELECT fleet_type,role_type,release_version,charter,objective_classes,"
                "allowed_capabilities,allowed_network_classes,default_model,default_effort,"
                "allowed_effort_levels,can_plan,can_execute,can_review,evidence_requirements,"
                "consequence_tiers,memory_surfaces FROM kernel_agent_type_releases "
                "WHERE release_sha256=%s"
            )
            existing = cursor.execute(release_columns, (release_sha,)).fetchone()
            if existing is None:
                try:
                    cursor.execute(
                        "INSERT INTO kernel_agent_type_releases "
                        "(release_sha256,fleet_type,role_type,release_version,charter,objective_classes,"
                        "allowed_capabilities,allowed_network_classes,default_model,default_effort,"
                        "allowed_effort_levels,can_plan,can_execute,can_review,evidence_requirements,"
                        "consequence_tiers,memory_surfaces) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (release_sha256) DO NOTHING",
                        (
                            release_sha, spec.fleet_type.value, spec.role_type,
                            spec.release_version, spec.charter,
                            Jsonb(sorted(spec.objective_classes)),
                            Jsonb(sorted(spec.allowed_capabilities)),
                            Jsonb(sorted(spec.allowed_network_classes)),
                            spec.default_model, spec.default_effort.value,
                            Jsonb(sorted(level.value for level in spec.allowed_effort_levels)),
                            spec.can_plan, spec.can_execute, spec.can_review,
                            Jsonb(sorted(spec.evidence_requirements)),
                            Jsonb(sorted(spec.consequence_tiers)),
                            Jsonb(sorted(spec.memory_surfaces)),
                        ),
                    )
                except psycopg.errors.UniqueViolation as exc:
                    raise IdentityPlaneError("agent role/version identity collision") from exc
                existing = cursor.execute(release_columns, (release_sha,)).fetchone()
            if existing is None or existing != spec.canonical_payload():
                raise IdentityPlaneError("agent release digest identity collision")
            releases[spec.role_type] = release_sha
    return releases


def provision_restaurant_manifest(
    conn: psycopg.Connection,
    *,
    tenant_id: str,
    manifest_version: int,
    enabled_modules: Sequence[str] = ("payroll", "inventory", "ordering"),
    policy_config: Mapping[str, object] | None = None,
    schedule_config: Mapping[str, object] | None = None,
    integration_refs: Mapping[str, object] | None = None,
    approval_authorities: Mapping[str, object] | None = None,
    resource_limits: Mapping[str, object] | None = None,
    notification_channels: Sequence[str] = (),
    memory_refs: Sequence[str] = (),
    replace_active: bool = False,
) -> ManifestRegistration:
    """Create and activate one seven-role Restaurant Fleet manifest.

    ``enabled_modules`` is the set of product modules this tenant runs; any
    subset of ``RESTAURANT_MODULES`` provisions. The manifest records the
    set; scope detail (why a module is off, who approves what) lives in
    ``policy_config``.
    """
    tenant = _tenant_id(tenant_id)
    if tenant == COMPANY_TENANT_ID:
        raise IdentityPlaneError(
            "the reserved Company Fleet identity cannot provision a Restaurant Fleet"
        )
    if manifest_version < 1:
        raise IdentityPlaneError("manifest_version must be positive")
    config = {
        "enabled_modules": sorted(set(enabled_modules)),
        "policy_config": dict(policy_config or {}),
        "schedule_config": dict(schedule_config or {}),
        "integration_refs": dict(integration_refs or {}),
        "approval_authorities": dict(approval_authorities or {}),
        "resource_limits": dict(resource_limits or {}),
        "notification_channels": sorted(set(notification_channels)),
        "memory_refs": sorted(set(memory_refs)),
    }
    unknown = set(config["enabled_modules"]) - RESTAURANT_MODULES
    if unknown:
        raise IdentityPlaneError(
            "Restaurant Fleet module is not one of payroll, inventory, ordering: "
            + ", ".join(sorted(str(name) for name in unknown))
        )

    with conn.transaction():
        role_releases = install_agent_type_releases(conn)
        payload = {
            "schema_version": 1,
            "tenant_id": tenant,
            "fleet_type": FleetType.RESTAURANT.value,
            "manifest_version": manifest_version,
            "roles": dict(sorted(role_releases.items())),
            **config,
        }
        manifest_sha = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
        manifest_id = uuid.uuid5(uuid.NAMESPACE_URL, f"mise-fleet-manifest:{tenant}:{manifest_sha}")
        with conn.cursor(row_factory=dict_row) as cursor:
            active = cursor.execute(
                "SELECT id,manifest_version,manifest_sha256 FROM kernel_tenant_fleet_manifests "
                "WHERE tenant_id=%s AND fleet_type='restaurant' AND status='active' FOR UPDATE",
                (tenant,),
            ).fetchone()
            if active and active["manifest_sha256"] == manifest_sha:
                return ManifestRegistration(
                    active["id"], tenant, active["manifest_version"], manifest_sha, role_releases,
                )
            if active and not replace_active:
                raise IdentityPlaneError("tenant already has a different active fleet manifest")
            if active and manifest_version <= active["manifest_version"]:
                raise IdentityPlaneError("replacement manifest version must increase")
            if active:
                old_id = active["id"]
                cursor.execute(
                    "UPDATE kernel_worker_sessions SET state='ended',ended_at=now() "
                    "WHERE tenant_id=%s AND manifest_id=%s AND state IN ('starting','active','held')",
                    (tenant, old_id),
                )
                cursor.execute(
                    "UPDATE kernel_capability_grants SET status='revoked',revoked_at=now() "
                    "WHERE tenant_id=%s AND worker_id IN "
                    "(SELECT id FROM kernel_runtime_workers WHERE manifest_id=%s) AND status='active'",
                    (tenant, old_id),
                )
                cursor.execute(
                    "UPDATE kernel_runtime_workers SET status='suspended',suspended_at=now(),"
                    "lease_token=NULL,lease_expires_at=NULL "
                    "WHERE tenant_id=%s AND manifest_id=%s AND status='active'",
                    (tenant, old_id),
                )
                cursor.execute(
                    "UPDATE kernel_tenant_fleet_manifests SET status='retired' WHERE id=%s",
                    (old_id,),
                )
            try:
                cursor.execute(
                    "INSERT INTO kernel_tenant_fleet_manifests "
                    "(id,tenant_id,fleet_type,manifest_version,manifest_sha256,status,enabled_modules,"
                    "policy_config,schedule_config,integration_refs,approval_authorities,resource_limits,"
                    "notification_channels,memory_refs) "
                    "VALUES (%s,%s,'restaurant',%s,%s,'draft',%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        manifest_id, tenant, manifest_version, manifest_sha,
                        Jsonb(config["enabled_modules"]), Jsonb(config["policy_config"]),
                        Jsonb(config["schedule_config"]), Jsonb(config["integration_refs"]),
                        Jsonb(config["approval_authorities"]), Jsonb(config["resource_limits"]),
                        Jsonb(config["notification_channels"]), Jsonb(config["memory_refs"]),
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise IdentityPlaneError("manifest version or digest identity collision") from exc
            cursor.executemany(
                "INSERT INTO kernel_tenant_fleet_manifest_roles "
                "(manifest_id,tenant_id,fleet_type,role_type,agent_release_sha256,role_config) "
                "VALUES (%s,%s,'restaurant',%s,%s,'{}'::jsonb)",
                [(manifest_id, tenant, role, role_releases[role]) for role in sorted(role_releases)],
            )
            cursor.execute(
                "UPDATE kernel_tenant_fleet_manifests SET status='active' WHERE id=%s",
                (manifest_id,),
            )
    return ManifestRegistration(manifest_id, tenant, manifest_version, manifest_sha, role_releases)


COMPANY_MODULES = frozenset({
    "governance", "product", "engineering", "verification", "finance",
    "legal", "marketing", "growth", "customer_success", "risk",
})


def provision_company_manifest(
    conn: psycopg.Connection,
    *,
    tenant_id: str = COMPANY_TENANT_ID,
    manifest_version: int,
    enabled_modules: Sequence[str] = tuple(sorted(COMPANY_MODULES)),
    policy_config: Mapping[str, object] | None = None,
    schedule_config: Mapping[str, object] | None = None,
    approval_authorities: Mapping[str, object] | None = None,
    resource_limits: Mapping[str, object] | None = None,
    notification_channels: Sequence[str] = (),
    memory_refs: Sequence[str] = (),
    replace_active: bool = False,
) -> ManifestRegistration:
    """Create the single internal Company Fleet manifest, never a customer fleet."""
    tenant = _tenant_id(tenant_id)
    if tenant != COMPANY_TENANT_ID:
        raise IdentityPlaneError("Company Fleet must use the reserved Mise Inc. company identity")
    if manifest_version < 1:
        raise IdentityPlaneError("manifest_version must be positive")
    modules = sorted(set(enabled_modules))
    if set(modules) != COMPANY_MODULES:
        raise IdentityPlaneError("Company Fleet must enable the exact corporate function registry")
    config = {
        "enabled_modules": modules,
        "policy_config": dict(policy_config or {}),
        "schedule_config": dict(schedule_config or {}),
        "integration_refs": {},
        "approval_authorities": dict(approval_authorities or {}),
        "resource_limits": dict(resource_limits or {}),
        "notification_channels": sorted(set(notification_channels)),
        "memory_refs": sorted(set(memory_refs)),
    }
    with conn.transaction():
        role_releases = install_agent_type_releases(conn, COMPANY_ROLE_SPECS)
        payload = {
            "schema_version": 1,
            "tenant_id": tenant,
            "fleet_type": FleetType.COMPANY.value,
            "manifest_version": manifest_version,
            "roles": dict(sorted(role_releases.items())),
            **config,
        }
        manifest_sha = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
        manifest_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"mise-fleet-manifest:{tenant}:{manifest_sha}",
        )
        with conn.cursor(row_factory=dict_row) as cursor:
            active = cursor.execute(
                "SELECT id,manifest_version,manifest_sha256 FROM kernel_tenant_fleet_manifests "
                "WHERE tenant_id=%s AND fleet_type='company' AND status='active' FOR UPDATE",
                (tenant,),
            ).fetchone()
            if active and active["manifest_sha256"] == manifest_sha:
                return ManifestRegistration(
                    active["id"], tenant, active["manifest_version"], manifest_sha,
                    role_releases,
                )
            if active and not replace_active:
                raise IdentityPlaneError("Mise Inc. already has a different active Company manifest")
            if active and manifest_version <= active["manifest_version"]:
                raise IdentityPlaneError("replacement manifest version must increase")
            if active:
                old_id = active["id"]
                cursor.execute(
                    "UPDATE kernel_worker_sessions SET state='ended',ended_at=now() "
                    "WHERE tenant_id=%s AND manifest_id=%s AND state IN ('starting','active','held')",
                    (tenant, old_id),
                )
                cursor.execute(
                    "UPDATE kernel_capability_grants SET status='revoked',revoked_at=now() "
                    "WHERE tenant_id=%s AND worker_id IN "
                    "(SELECT id FROM kernel_runtime_workers WHERE manifest_id=%s) AND status='active'",
                    (tenant, old_id),
                )
                cursor.execute(
                    "UPDATE kernel_runtime_workers SET status='suspended',suspended_at=now(),"
                    "lease_token=NULL,lease_expires_at=NULL "
                    "WHERE tenant_id=%s AND manifest_id=%s AND status='active'",
                    (tenant, old_id),
                )
                cursor.execute(
                    "UPDATE kernel_tenant_fleet_manifests SET status='retired' WHERE id=%s",
                    (old_id,),
                )
            try:
                cursor.execute(
                    "INSERT INTO kernel_tenant_fleet_manifests "
                    "(id,tenant_id,fleet_type,manifest_version,manifest_sha256,status,enabled_modules,"
                    "policy_config,schedule_config,integration_refs,approval_authorities,resource_limits,"
                    "notification_channels,memory_refs) "
                    "VALUES (%s,%s,'company',%s,%s,'draft',%s,%s,%s,'{}'::jsonb,%s,%s,%s,%s)",
                    (
                        manifest_id, tenant, manifest_version, manifest_sha,
                        Jsonb(config["enabled_modules"]), Jsonb(config["policy_config"]),
                        Jsonb(config["schedule_config"]),
                        Jsonb(config["approval_authorities"]),
                        Jsonb(config["resource_limits"]),
                        Jsonb(config["notification_channels"]),
                        Jsonb(config["memory_refs"]),
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise IdentityPlaneError("Company manifest version or digest identity collision") from exc
            cursor.executemany(
                "INSERT INTO kernel_tenant_fleet_manifest_roles "
                "(manifest_id,tenant_id,fleet_type,role_type,agent_release_sha256,role_config) "
                "VALUES (%s,%s,'company',%s,%s,'{}'::jsonb)",
                [
                    (manifest_id, tenant, role, role_releases[role])
                    for role in sorted(role_releases)
                ],
            )
            cursor.execute(
                "UPDATE kernel_tenant_fleet_manifests SET status='active' WHERE id=%s",
                (manifest_id,),
            )
    return ManifestRegistration(
        manifest_id, tenant, manifest_version, manifest_sha, role_releases,
    )


def register_runtime_worker(
    conn: psycopg.Connection,
    *,
    tenant_id: str,
    manifest_id: uuid.UUID,
    role_type: str,
    os_principal: str,
    filesystem_root: Path,
    database_principal: str,
    signing_public_key: bytes,
    model_route: str | None = None,
    effort_level: EffortLevel | None = None,
    worker_id: uuid.UUID | None = None,
) -> WorkerRegistration:
    tenant = _tenant_id(tenant_id)
    assigned_worker_id = worker_id or uuid.uuid4()
    if not filesystem_root.is_absolute() or not signing_public_key:
        raise IdentityPlaneError("worker root and signing public key are required")
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        release = cursor.execute(
            "SELECT role.agent_release_sha256,release.default_model,release.default_effort,"
            "release.allowed_effort_levels,manifest.status,manifest.fleet_type "
            "FROM kernel_tenant_fleet_manifest_roles AS role "
            "JOIN kernel_tenant_fleet_manifests AS manifest ON manifest.id=role.manifest_id "
            "JOIN kernel_agent_type_releases AS release "
            "ON release.release_sha256=role.agent_release_sha256 "
            "WHERE role.manifest_id=%s AND role.tenant_id=%s AND role.role_type=%s "
            "FOR SHARE OF manifest",
            (manifest_id, tenant, role_type),
        ).fetchone()
        if release is None or release["status"] != "active":
            raise IdentityPlaneError("worker requires an enabled role on the active manifest")
        model = model_route or release["default_model"]
        effort = effort_level or EffortLevel(release["default_effort"])
        if model != release["default_model"]:
            raise IdentityPlaneError("worker model route must match its immutable role release")
        assert_effort_admitted_for_model(model, effort)
        if effort.value not in release["allowed_effort_levels"]:
            raise IdentityPlaneError("worker effort is outside its role release")
        if release["fleet_type"] == FleetType.COMPANY.value:
            expected_principal = company_worker_database_principal(
                assigned_worker_id, role_type,
            )
            if database_principal != expected_principal:
                raise IdentityPlaneError(
                    "Company worker requires its role- and identity-bound database principal"
                )
        release_sha = release["agent_release_sha256"]
        existing = cursor.execute(
            "SELECT tenant_id,manifest_id,role_type,agent_release_sha256,model_route,"
            "effort_level,os_principal,filesystem_root,database_principal,signing_public_key,status "
            "FROM kernel_runtime_workers WHERE id=%s",
            (assigned_worker_id,),
        ).fetchone()
        expected = {
            "tenant_id": tenant,
            "manifest_id": manifest_id,
            "role_type": role_type,
            "agent_release_sha256": release_sha,
            "model_route": model,
            "effort_level": effort.value,
            "os_principal": os_principal,
            "filesystem_root": str(filesystem_root),
            "database_principal": database_principal,
            "signing_public_key": signing_public_key,
            "status": "active",
        }
        if existing is not None:
            if existing != expected:
                raise IdentityPlaneError("worker id collides with a different structural identity")
            return WorkerRegistration(
                assigned_worker_id, tenant, manifest_id, role_type, release_sha, model, effort,
            )
        cursor.execute(
            "INSERT INTO kernel_runtime_workers "
            "(id,tenant_id,manifest_id,fleet_type,role_type,agent_release_sha256,model_route,"
            "effort_level,os_principal,filesystem_root,database_principal,signing_public_key,"
            "signing_public_key_sha256,status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active')",
            (
                assigned_worker_id, tenant, manifest_id, release["fleet_type"], role_type,
                release_sha, model, effort.value,
                os_principal, str(filesystem_root), database_principal, signing_public_key,
                hashlib.sha256(signing_public_key).hexdigest(),
            ),
        )
    return WorkerRegistration(
        assigned_worker_id, tenant, manifest_id, role_type, release_sha, model, effort,
    )


def open_worker_session(
    conn: psycopg.Connection,
    *,
    tenant_id: str,
    worker_id: uuid.UUID,
    objective_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    actual_model: str | None = None,
    effort_level: EffortLevel | None = None,
    now: datetime | None = None,
    lease_duration: timedelta = timedelta(minutes=5),
    package_id: uuid.UUID | None = None,
    runtime_attachment_sha256: str | None = None,
    runtime_attachment_nonce: uuid.UUID | None = None,
    runtime_attachment_issued_at: datetime | None = None,
    runtime_attachment_expires_at: datetime | None = None,
) -> SessionRegistration:
    tenant = _tenant_id(tenant_id)
    started_at = _aware(now or datetime.now(timezone.utc), field="now")
    if lease_duration <= timedelta(0):
        raise IdentityPlaneError("lease duration must be positive")
    expires_at = started_at + lease_duration
    session_id = uuid.uuid4()
    lease_token = uuid.uuid4().hex
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        worker_locator = cursor.execute(
            "SELECT manifest_id FROM kernel_runtime_workers WHERE id=%s AND tenant_id=%s",
            (worker_id, tenant),
        ).fetchone()
        if worker_locator is None:
            raise IdentityPlaneError("session refuses an unknown tenant worker")
        manifest = cursor.execute(
            "SELECT status FROM kernel_tenant_fleet_manifests "
            "WHERE id=%s AND tenant_id=%s FOR SHARE",
            (worker_locator["manifest_id"], tenant),
        ).fetchone()
        worker = cursor.execute(
            "SELECT worker.*,release.default_model,release.allowed_effort_levels "
            "FROM kernel_runtime_workers AS worker "
            "JOIN kernel_agent_type_releases AS release "
            "ON release.release_sha256=worker.agent_release_sha256 "
            "WHERE worker.id=%s AND worker.tenant_id=%s "
            "FOR NO KEY UPDATE OF worker",
            (worker_id, tenant),
        ).fetchone()
        if worker is None or manifest is None or (
            worker["status"] != "active" or manifest["status"] != "active"
        ):
            raise IdentityPlaneError("session refuses an inactive worker or stale manifest")
        model = actual_model or worker["model_route"]
        effort = effort_level or EffortLevel(worker["effort_level"])
        if model != worker["default_model"]:
            raise IdentityPlaneError("session model does not match the role release")
        assert_effort_admitted_for_model(model, effort)
        if effort.value not in worker["allowed_effort_levels"]:
            raise IdentityPlaneError("session effort is outside the role release")
        if run_id is not None:
            run = cursor.execute(
                "SELECT objective_id FROM runs WHERE id=%s AND tenant_id=%s FOR UPDATE",
                (run_id, tenant),
            ).fetchone()
            if run is None or (objective_id is not None and run["objective_id"] != objective_id):
                raise IdentityPlaneError("session run/objective tenant binding is invalid")
            cursor.execute(
                "UPDATE runs SET worker_id=%s WHERE id=%s AND tenant_id=%s",
                (worker_id, run_id, tenant),
            )
        cursor.execute(
            "INSERT INTO kernel_worker_sessions "
            "(id,tenant_id,worker_id,manifest_id,role_type,agent_release_sha256,objective_id,run_id,"
            "actual_model,effort_level,state,lease_token,started_at,heartbeat_at,lease_expires_at,"
            "package_id,runtime_attachment_sha256,runtime_attachment_nonce,"
            "runtime_attachment_issued_at,runtime_attachment_expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                session_id, tenant, worker_id, worker["manifest_id"], worker["role_type"],
                worker["agent_release_sha256"], objective_id, run_id, model, effort.value,
                lease_token, started_at, started_at, expires_at, package_id,
                runtime_attachment_sha256, runtime_attachment_nonce,
                runtime_attachment_issued_at, runtime_attachment_expires_at,
            ),
        )
        cursor.execute(
            "UPDATE kernel_runtime_workers SET current_objective_id=%s,lease_token=%s,"
            "lease_expires_at=%s WHERE id=%s AND tenant_id=%s",
            (objective_id, lease_token, expires_at, worker_id, tenant),
        )
    return SessionRegistration(session_id, worker_id, tenant, lease_token, expires_at)


def grant_worker_capability(
    conn: psycopg.Connection,
    *,
    tenant_id: str,
    worker_id: uuid.UUID,
    capability: str,
    granted_by: str,
    evidence_ref: str,
    scope: Mapping[str, object] | None = None,
    now: datetime | None = None,
    duration: timedelta = timedelta(hours=1),
) -> uuid.UUID:
    tenant = _tenant_id(tenant_id)
    effective_at = _aware(now or datetime.now(timezone.utc), field="now")
    if duration <= timedelta(0):
        raise IdentityPlaneError("capability duration must be positive")
    grant_id = uuid.uuid4()
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO kernel_capability_grants "
                "(id,tenant_id,worker_id,capability,scope,status,granted_by,evidence_ref,"
                "effective_at,expires_at) VALUES (%s,%s,%s,%s,%s,'active',%s,%s,%s,%s)",
                (
                    grant_id, tenant, worker_id, capability, Jsonb(dict(scope or {})),
                    granted_by, evidence_ref, effective_at, effective_at + duration,
                ),
            )
    except psycopg.Error as exc:
        raise IdentityPlaneError(str(exc).splitlines()[0]) from exc
    return grant_id
