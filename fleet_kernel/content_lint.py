"""Deterministic content lint: the mechanically checkable subset of the no-ai-slop doctrine.

This module is the deterministic layer of the content-quality gate. It compiles
the mechanically checkable rules of the house writing standard (the no-ai-slop
skill; HARNESS_CORE §13) into pure functions with no model, no I/O, and no
dependency outside the standard library. The editorial-judgment half of the
doctrine (antithetical structure, fake-profound kickers, robotic rhythm) stays
with a review-capable role; this module never attempts it beyond conservative
review-severity pattern flags.

Contract:
- ``lint_text`` is pure and deterministic: identical input yields an identical
  result, including digests.
- The ruleset is content-addressed (``ruleset_sha256``) so an evidence receipt
  binds to the exact rules that produced it; editing any rule changes the hash.
- Verdict is ``fail`` only on banned-severity findings. Review-severity findings
  report without failing; a reviewing role dispositions them.
- Phase 2 enrollment: this module is the producer of the existing authority
  ``content`` evidence receipt (``AUTHORITY_CHECK_ID``). It does not rewrite
  the action-class matrix. Prep surfaces call ``require_prep_content_lint``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

RULESET_VERSION = "1.0"

# Existing m8 authority check slot. This module produces that receipt; it does
# not add a new matrix row.
AUTHORITY_CHECK_ID = "content"

# Text-bearing preparation surfaces that must run content_lint before they
# may be treated as ready. Payroll money artifacts are not in this set.
PREP_SURFACES = frozenset({"ordering_copy_ready", "ask_mise_summary"})

# Single words banned outright by the writing doctrine. Word-boundary,
# case-insensitive. Context-specific vocabularies (e.g. internal docs that
# legitimately use "harness") suppress via the allowlist parameter.
BANNED_WORDS: tuple[str, ...] = (
    "delve", "foster", "leverage", "utilize", "facilitate", "empower",
    "streamline", "robust", "cutting-edge", "tapestry", "realm", "beacon",
    "multifaceted", "meticulous", "intricate", "paramount", "transformative",
    "elevate", "embark", "supercharge", "harness", "ever-evolving",
)

# Multi-word phrases banned outright.
BANNED_PHRASES: tuple[str, ...] = (
    "paradigm shift", "game changer", "this is huge", "this changes everything",
)

# Often-empty phrases: reported at review severity, never an automatic fail —
# the doctrine cuts them when they add nothing and keeps them when they carry
# the writer's voice, and that judgment belongs to a reviewer.
FLAG_PHRASES: tuple[str, ...] = (
    "it's worth noting", "it's important to note", "at the end of the day",
    "when it comes to", "at its core", "in today's world", "in the age of",
    "in the world of", "the reality is", "the truth is", "in terms of",
    "with regard to", "in order to", "going forward", "in this article",
    "let's dive in",
)

EM_DASH = "—"
# Short copy allows zero em dashes; longer copy allows two.
EM_DASH_SHORT_COPY_WORDS = 80
EM_DASH_MAX_LONG_COPY = 2

_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff☀-➿️]",
)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s", re.MULTILINE)

# Conservative antithetical-parallelism shapes ("it's not X, it's Y";
# "not just X but Y"). Review severity: the pattern space is too rich for a
# regex to judge; these only mark candidates for the reviewing role.
_NEGATION_PAIR_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:is|it's|it’s|that's|that’s)\s+not\s+[^.;\n]{1,60}[.;,]\s*(?:it's|it’s|but)\b", re.IGNORECASE),
    re.compile(r"\bnot\s+just\s+[^.;\n]{1,60}\bbut\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ContentFinding:
    rule_id: str
    severity: str  # "banned" | "review"
    excerpt: str
    line: int


@dataclass(frozen=True)
class ContentLintResult:
    verdict: str  # "pass" | "fail"
    findings: tuple[ContentFinding, ...]
    subject_sha256: str
    ruleset_sha256: str

    def evidence_payload(self) -> dict:
        return {
            "check": "content_lint",
            "ruleset_version": RULESET_VERSION,
            "ruleset_sha256": self.ruleset_sha256,
            "subject_sha256": self.subject_sha256,
            "verdict": self.verdict,
            "findings": [
                {
                    "rule_id": finding.rule_id,
                    "severity": finding.severity,
                    "excerpt": finding.excerpt,
                    "line": finding.line,
                }
                for finding in self.findings
            ],
        }


def ruleset_sha256() -> str:
    canonical = json.dumps(
        {
            "version": RULESET_VERSION,
            "banned_words": list(BANNED_WORDS),
            "banned_phrases": list(BANNED_PHRASES),
            "flag_phrases": list(FLAG_PHRASES),
            "em_dash_short_copy_words": EM_DASH_SHORT_COPY_WORDS,
            "em_dash_max_long_copy": EM_DASH_MAX_LONG_COPY,
            "negation_pair_patterns": [p.pattern for p in _NEGATION_PAIR_RES],
        },
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _excerpt(text: str, start: int, end: int, radius: int = 20) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi].replace("\n", " ").strip()


def _term_findings(
    text: str, terms: tuple[str, ...], rule_prefix: str, severity: str,
    allowlist: frozenset[str],
) -> list[ContentFinding]:
    findings: list[ContentFinding] = []
    for term in terms:
        if term.lower() in allowlist:
            continue
        pattern = re.compile(
            r"(?<![\w-])" + re.escape(term).replace(r"\'", "['’]") + r"(?![\w-])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            findings.append(ContentFinding(
                f"{rule_prefix}:{term}", severity,
                _excerpt(text, match.start(), match.end()),
                _line_of(text, match.start()),
            ))
    return findings


def lint_text(text: str, allowlist: frozenset[str] = frozenset()) -> ContentLintResult:
    normalized_allowlist = frozenset(term.lower() for term in allowlist)
    findings: list[ContentFinding] = []

    findings.extend(_term_findings(text, BANNED_WORDS, "banned-word", "banned", normalized_allowlist))
    findings.extend(_term_findings(text, BANNED_PHRASES, "banned-phrase", "banned", normalized_allowlist))
    findings.extend(_term_findings(text, FLAG_PHRASES, "empty-phrase", "review", normalized_allowlist))

    word_count = len(text.split())
    dash_positions = [i for i, ch in enumerate(text) if ch == EM_DASH]
    allowed = 0 if word_count < EM_DASH_SHORT_COPY_WORDS else EM_DASH_MAX_LONG_COPY
    if len(dash_positions) > allowed:
        for position in dash_positions[allowed:]:
            findings.append(ContentFinding(
                "em-dash-density", "banned",
                _excerpt(text, position, position + 1),
                _line_of(text, position),
            ))

    for heading in _HEADING_RE.finditer(text):
        line_end = text.find("\n", heading.start())
        line_end = len(text) if line_end == -1 else line_end
        heading_text = text[heading.start():line_end]
        if _EMOJI_RE.search(heading_text):
            findings.append(ContentFinding(
                "emoji-heading", "banned",
                heading_text.strip(),
                _line_of(text, heading.start()),
            ))

    for pattern in _NEGATION_PAIR_RES:
        for match in pattern.finditer(text):
            findings.append(ContentFinding(
                "negation-pair", "review",
                _excerpt(text, match.start(), match.end()),
                _line_of(text, match.start()),
            ))

    ordered = tuple(sorted(findings, key=lambda f: (f.line, f.rule_id, f.excerpt)))
    verdict = "fail" if any(f.severity == "banned" for f in ordered) else "pass"
    return ContentLintResult(
        verdict, ordered,
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        ruleset_sha256(),
    )


class ContentLintObligationError(ValueError):
    """A text-bearing prep surface failed the content-lint obligation."""

    def __init__(self, surface: str, result: ContentLintResult) -> None:
        self.surface = surface
        self.result = result
        banned = [finding.rule_id for finding in result.findings if finding.severity == "banned"]
        detail = banned[0] if banned else result.verdict
        super().__init__(f"{surface} failed content_lint ({detail})")


def produce_content_evidence(
    text: str, allowlist: frozenset[str] = frozenset(),
) -> ContentLintResult:
    """Produce the authority ``content`` evidence artifact for ``text``."""
    return lint_text(text, allowlist=allowlist)


def content_receipt_kwargs(result: ContentLintResult) -> dict[str, str]:
    """Keyword arguments for ``authority.sign_evidence`` / ``sign_content_evidence``."""
    payload = json.dumps(
        result.evidence_payload(), sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    if result.verdict == "pass":
        reason = ""
    else:
        banned = [finding.rule_id for finding in result.findings if finding.severity == "banned"]
        reason = (
            "content_lint failed: " + ",".join(banned[:8])
            if banned else "content gate did not pass"
        )
    return {
        "check_id": AUTHORITY_CHECK_ID,
        "verdict": result.verdict,
        "reason": reason,
        "artifact_ref": f"content_lint/{result.subject_sha256}",
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
    }


def require_prep_content_lint(
    surface: str,
    text: str,
    *,
    allowlist: frozenset[str] = frozenset(),
) -> ContentLintResult:
    """Fail closed when a named prep surface's text does not pass content_lint."""
    if surface not in PREP_SURFACES:
        raise ValueError(f"unknown content-lint prep surface: {surface}")
    result = produce_content_evidence(text, allowlist=allowlist)
    if result.verdict != "pass":
        raise ContentLintObligationError(surface, result)
    return result


def lint_ask_mise_summary(
    text: str, *, allowlist: frozenset[str] = frozenset(),
) -> ContentLintResult:
    """Content-lint obligation for Ask Mise-surfaced summary text."""
    return require_prep_content_lint(
        "ask_mise_summary", text, allowlist=allowlist,
    )
