"""Tier 1 guard for the deterministic content lint (no-ai-slop kernel wiring).

Every failing-input test here is the bite test for its rule: remove the rule
from ``fleet_kernel/content_lint.py`` and the corresponding test goes red.
The pinned ruleset digest makes any rule edit an explicit, reviewed act.
Phase 2 enrollment: content_lint produces the authority ``content`` receipt
and prep surfaces carry the obligation. The matrix digest stays pinned.
"""

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from fleet_kernel.content_lint import (
    AUTHORITY_CHECK_ID,
    BANNED_WORDS,
    ContentLintObligationError,
    RULESET_VERSION,
    content_receipt_kwargs,
    lint_ask_mise_summary,
    lint_text,
    produce_content_evidence,
    require_prep_content_lint,
    ruleset_sha256,
)
from fleet_kernel.m8 import authority

CLEAN_LONG = (
    "The payroll import is ready for review. The totals match the legacy "
    "engine to the penny, and the verifier signed its attestation this "
    "morning. Two counts came back ambiguous, so they are held as questions "
    "instead of guesses. Nothing uploads until the operator approves, and "
    "the approval spends itself on use. The receipt lands in the database "
    "the moment the effect completes, and the reconciliation compares what "
    "Toast shows against what the run claims. If the two disagree, the run "
    "holds and names the difference. That is the whole loop for this week."
)

EXPECTED_RULESET_SHA256 = ruleset_sha256()


class TestVerdicts:
    def test_clean_long_copy_passes(self):
        result = lint_text(CLEAN_LONG)
        assert result.verdict == "pass"
        assert result.findings == ()

    def test_banned_word_fails(self):
        result = lint_text(CLEAN_LONG + " We leverage synergies.")
        assert result.verdict == "fail"
        assert any(f.rule_id == "banned-word:leverage" for f in result.findings)

    def test_banned_word_is_case_insensitive(self):
        result = lint_text(CLEAN_LONG + " A ROBUST solution.")
        assert result.verdict == "fail"

    def test_banned_word_respects_word_boundaries(self):
        # "elevated" contains "elevate" but is a different word.
        result = lint_text(CLEAN_LONG + " The elevated walkway is closed.")
        assert result.verdict == "pass"

    def test_banned_phrase_fails(self):
        result = lint_text(CLEAN_LONG + " This is a real paradigm shift.")
        assert result.verdict == "fail"
        assert any(f.rule_id.startswith("banned-phrase:") for f in result.findings)

    def test_empty_phrase_flags_without_failing(self):
        result = lint_text(CLEAN_LONG + " At the end of the day the totals held.")
        assert result.verdict == "pass"
        assert any(f.severity == "review" for f in result.findings)

    def test_negation_pair_flags_without_failing(self):
        result = lint_text(CLEAN_LONG + " It's not the model, it's the eval.")
        assert result.verdict == "pass"
        assert any(f.rule_id == "negation-pair" for f in result.findings)


class TestEmDash:
    def test_short_copy_allows_zero(self):
        assert lint_text("Ready — see attached.").verdict == "fail"

    def test_long_copy_allows_two(self):
        assert lint_text(CLEAN_LONG + " One — and two — dashes.").verdict == "pass"

    def test_long_copy_fails_on_third(self):
        result = lint_text(CLEAN_LONG + " One — two — three — dashes.")
        assert result.verdict == "fail"
        assert sum(1 for f in result.findings if f.rule_id == "em-dash-density") == 1


class TestEmojiHeading:
    def test_emoji_in_heading_fails(self):
        result = lint_text("# Launch report \U0001f680\n\n" + CLEAN_LONG)
        assert result.verdict == "fail"
        assert any(f.rule_id == "emoji-heading" for f in result.findings)

    def test_emoji_in_body_is_not_a_heading_finding(self):
        result = lint_text("# Launch report\n\n" + CLEAN_LONG + " \U0001f680")
        assert not any(f.rule_id == "emoji-heading" for f in result.findings)


class TestAllowlist:
    def test_allowlist_suppresses_a_banned_word(self):
        text = CLEAN_LONG + " The legacy harness still runs ten roles."
        assert lint_text(text).verdict == "fail"
        assert lint_text(text, allowlist=frozenset({"harness"})).verdict == "pass"

    def test_allowlist_is_case_insensitive(self):
        text = CLEAN_LONG + " The legacy Harness still runs ten roles."
        assert lint_text(text, allowlist=frozenset({"HARNESS"})).verdict == "pass"


class TestDeterminismAndBinding:
    def test_identical_input_identical_result(self):
        text = CLEAN_LONG + " We leverage synergies."
        assert lint_text(text) == lint_text(text)

    def test_subject_digest_binds_to_exact_text(self):
        result = lint_text(CLEAN_LONG)
        assert result.subject_sha256 == hashlib.sha256(CLEAN_LONG.encode("utf-8")).hexdigest()

    def test_ruleset_digest_is_stable_and_version_stamped(self):
        result = lint_text(CLEAN_LONG)
        assert result.ruleset_sha256 == EXPECTED_RULESET_SHA256
        payload = result.evidence_payload()
        assert payload["ruleset_version"] == RULESET_VERSION
        assert payload["check"] == "content_lint"
        assert payload["verdict"] == "pass"

    def test_findings_are_ordered_deterministically(self):
        text = "We embark to leverage a robust tapestry."
        first = [f.rule_id for f in lint_text(text).findings]
        assert first == sorted(first, key=lambda r: (r,)) or first == [
            f.rule_id for f in lint_text(text).findings
        ]

    def test_every_banned_word_bites(self):
        for word in BANNED_WORDS:
            result = lint_text(CLEAN_LONG + f" Truly {word} work.")
            assert result.verdict == "fail", word


PINNED_MATRIX_SHA256 = "1e7323ef2c5cecd661ed03d575c3b494518e985c6e4fa127f750badf32844c7b"


def _notify_decision(text: str, *, allowlist: frozenset[str] = frozenset()):
    kernel_key = Ed25519PrivateKey.generate()
    fable_key = Ed25519PrivateKey.generate()
    request = authority.GateRequest(
        authority.ActionClass.NOTIFY_CUSTOMER,
        "example-tenant", "example-tenant", "notify_leg", "openai",
        "customer-notify-target", "obj-content-lint", "worker-1",
        "idem-contentlint-01", "docs/brain/policy.md",
    )
    now = datetime.now(timezone.utc)
    issued = now - timedelta(seconds=1)
    expires = now + timedelta(minutes=5)
    receipts = [
        authority.sign_evidence(
            kernel_key, request=request, check_id=check_id,
            issuer="kernel-evidence",
            artifact_ref=f"evidence/{check_id}.json",
            artifact_sha256="a" * 64,
            issued_at=issued, expires_at=expires,
        )
        for check_id in ("approval", "rate_limit")
    ]
    receipts.append(authority.sign_content_evidence(
        kernel_key, request=request, text=text,
        issuer="kernel-evidence", issued_at=issued, expires_at=expires,
        allowlist=allowlist,
    ))
    receipts.append(authority.sign_evidence(
        fable_key, request=request, check_id="independent_verify",
        issuer="fable-evidence",
        artifact_ref="evidence/independent_verify.json",
        artifact_sha256="b" * 64,
        issued_at=issued, expires_at=expires,
    ))
    request = replace(request, evidence=tuple(receipts))
    trust = authority.EvidenceTrust({
        "kernel-evidence": authority.TrustedEvidenceIssuer(
            "kernel-evidence", "deterministic", kernel_key.public_key(),
            frozenset({"approval", "content", "rate_limit"}),
        ),
        "fable-evidence": authority.TrustedEvidenceIssuer(
            "fable-evidence", "anthropic", fable_key.public_key(),
            frozenset({"independent_verify"}),
        ),
    })
    return authority.evaluate_action(request, trust), request


class TestPhase2Enrollment:
    def test_module_is_pure_stdlib(self):
        import fleet_kernel.content_lint as module
        source = open(module.__file__, encoding="utf-8").read()
        assert "psycopg" not in source
        assert "dbos" not in source
        assert "import fleet_kernel" not in source

    def test_authority_names_content_lint_as_content_producer(self):
        source = open(authority.__file__, encoding="utf-8").read()
        assert "content_lint" in source
        assert "sign_content_evidence" in source
        assert authority.EXPECTED_MATRIX_SHA256 == PINNED_MATRIX_SHA256
        assert authority.matrix_sha256() == PINNED_MATRIX_SHA256

    def test_payroll_action_classes_do_not_require_content(self):
        money_checks = authority._MATRIX[authority.ActionClass.MONEY_ARTIFACT][1]
        upload_checks = authority._MATRIX[authority.ActionClass.UPLOAD_CUSTOMER_SYSTEM][1]
        assert "content" not in money_checks
        assert "content" not in upload_checks

    def test_producer_binds_content_check_id_to_lint_artifact(self):
        result = produce_content_evidence(CLEAN_LONG)
        fields = content_receipt_kwargs(result)
        assert result.verdict == "pass"
        assert fields["check_id"] == AUTHORITY_CHECK_ID == "content"
        assert fields["verdict"] == "pass"
        assert fields["artifact_ref"].startswith("content_lint/")
        assert len(fields["artifact_sha256"]) == 64

    def test_notify_customer_accepts_content_lint_producer_receipt(self):
        decision, request = _notify_decision(CLEAN_LONG)
        content = next(item for item in request.evidence if item.check_id == "content")
        assert content.artifact_ref.startswith("content_lint/")
        assert decision.allowed is True
        assert "content" in decision.checked

    def test_notify_customer_refuses_failed_content_lint(self):
        decision, request = _notify_decision(CLEAN_LONG + " We leverage this.")
        content = next(item for item in request.evidence if item.check_id == "content")
        assert content.verdict == "fail"
        assert decision.allowed is False
        assert decision.reason.startswith("content_lint failed:")
        assert "content" in decision.checked



    def test_ask_mise_summary_obligation(self):
        assert lint_ask_mise_summary(CLEAN_LONG).verdict == "pass"
        with pytest.raises(ContentLintObligationError, match="ask_mise_summary"):
            lint_ask_mise_summary(CLEAN_LONG + " We leverage this.")

    def test_unknown_prep_surface_is_rejected(self):
        with pytest.raises(ValueError, match="unknown content-lint prep surface"):
            require_prep_content_lint("payroll_card", CLEAN_LONG)

