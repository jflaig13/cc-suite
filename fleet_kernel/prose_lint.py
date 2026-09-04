"""Prose lint for agent-authored text (Told -> Bound program, Phase D).

Pure functions over text. No network, no database, no writes. The rules mirror
`.claude/skills/no-ai-slop/SKILL.md` and HARNESS_CORE §13 / §19, so a memo,
announcement, handoff, or ops receipt that breaks the writing standard is caught
by a test instead of by the founder.

Finding classes:

  em_dash_density          more em dashes than the ceiling allows for the word count
  banned_words             a word from the no-ai-slop banned list
  throat_clearing          an opener that delays the point
  antithetical_parallelism a "not X, it's Y" / "less X, more Y" contrast run
  schedule_invention       an agent-invented date, pacing, or duration phrase

Allow-list (schedule_invention only): a line that quotes the founder, or a line
that carries an explicit calendar date, is treated as an external fact and skipped.
Code blocks and inline code are stripped before every check so file names and
commands never count as prose.

Usage:
    python -m fleet_kernel.prose_lint <path> [<path> ...]
"""
from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "Finding",
    "EM_DASH_CEILING_PER_1000_WORDS",
    "BANNED_WORDS",
    "THROAT_CLEARING_OPENERS",
    "FINDING_CLASSES",
    "lint",
    "lint_file",
    "strip_code",
    "founder_quoted",
    "carries_external_date",
    "main",
]

EM_DASH_CEILING_PER_1000_WORDS = 2.0

BANNED_WORDS: tuple[str, ...] = (
    "delve", "foster", "leverage", "utilize", "facilitate", "empower",
    "streamline", "robust", "cutting-edge", "paradigm shift", "game changer",
    "tapestry", "realm", "beacon", "multifaceted", "meticulous", "intricate",
    "paramount", "transformative", "elevate", "embark", "supercharge",
    "harness", "ever-evolving",
)

THROAT_CLEARING_OPENERS: tuple[str, ...] = (
    "Here's the thing",
    "Let me be clear",
    "I'll be honest",
    "The uncomfortable truth",
    "It's worth noting",
    "It's important to note",
)

FINDING_CLASSES: tuple[str, ...] = (
    "em_dash_density",
    "banned_words",
    "throat_clearing",
    "antithetical_parallelism",
    "schedule_invention",
)


@dataclass(frozen=True, slots=True)
class Finding:
    cls: str
    line: int
    excerpt: str
    message: str

    def render(self, path: str = "<text>") -> str:
        return f"{path}:{self.line}: {self.cls}: {self.message} [{self.excerpt}]"


# ------------------------------------------------------------------ patterns

_EM_DASH = "—"
_APOS = "['’]"

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# Suffixes let "leverages", "leveraging", "delved", "streamlined" count too.
# "harness" is exempt when written in all caps: HARNESS names the canon file.
_BANNED_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in BANNED_WORDS) + r")(?:s|es|d|ed|ing)?\b",
    re.IGNORECASE,
)

_THROAT_RE = re.compile(
    "|".join(
        re.escape(p).replace("'", _APOS) for p in THROAT_CLEARING_OPENERS
    ),
    re.IGNORECASE,
)

_SPAN = r"[^.,;:!?\n]{1,60}?"
_ANTITHESIS_RES: tuple[re.Pattern[str], ...] = (
    # it's not X, it's Y  /  this isn't X. It's Y  /  the point is not X; it is Y
    re.compile(
        rf"\b(?:it{_APOS}?s|this is|that{_APOS}?s|is|isn{_APOS}t|are|aren{_APOS}t|was)\s+not\s+{_SPAN}[,;.]\s*(?:it{_APOS}?s|it is|but)\s+",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bisn{_APOS}t\s+{_SPAN}[,;.]\s*(?:it{_APOS}?s|it is)\s+",
        re.IGNORECASE,
    ),
    # not just X but (also) Y
    re.compile(
        rf"\bnot\s+(?:just|only|merely|simply)\s+{_SPAN}\s+but\s+(?:also\s+)?",
        re.IGNORECASE,
    ),
    # less X, more Y
    re.compile(rf"\bless\s+{_SPAN},\s*more\s+\w", re.IGNORECASE),
    # not X, Y  (sentence-initial negation pair: "Not a plan, a promise.")
    re.compile(rf"(?:^|[.!?]\s+)not\s+(?:a|an|the)\s+{_SPAN},\s+(?:a|an|the|but)\s+", re.IGNORECASE),
)

_WEEKDAY = r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day"
_SCHEDULE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:day|week)\s*(?:\d+|N)\b", re.IGNORECASE),
    re.compile(rf"\bpost-(?:{_WEEKDAY}|[A-Z]\w+|deadline|launch|cutover|close|ship|merge|release|event|activation)\b"),
    re.compile(r"\bnext\s+week\b", re.IGNORECASE),
    re.compile(r"\blater\s+this\s+week\b", re.IGNORECASE),
    re.compile(r"\bin\s+\d+\s*(?:[-–]|to)\s*\d+\s+(?:days?|weeks?|hours?)\b", re.IGNORECASE),
    re.compile(r"\bin\s+\d+\s+(?:days?|weeks?)\b", re.IGNORECASE),
    re.compile(r"\bby\s+EOD\b"),
    re.compile(r"\bby\s+end\s+of\s+(?:the\s+)?day\b", re.IGNORECASE),
    re.compile(r"\bby\s+tomorrow\b", re.IGNORECASE),
)

_FOUNDER_QUOTE_RE = re.compile(r"founder.{0,80}?[\"“‘']", re.IGNORECASE | re.DOTALL)
_EXTERNAL_DATE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(rf"\b{_WEEKDAY}\s+\d{{1,2}}/\d{{1,2}}\b"),
)


# ------------------------------------------------------------------- helpers

def strip_code(text: str) -> str:
    """Blank out fenced and inline code, preserving line count."""
    def _blank(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))
    text = _FENCE_RE.sub(_blank, text)
    return _INLINE_CODE_RE.sub(_blank, text)


def founder_quoted(line: str) -> bool:
    """True when the line attributes a quotation to the founder."""
    return bool(_FOUNDER_QUOTE_RE.search(line))


def carries_external_date(line: str) -> bool:
    """True when the line names an explicit calendar date (an external fact)."""
    return any(rx.search(line) for rx in _EXTERNAL_DATE_RES)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _excerpt(text: str, start: int, end: int, pad: int = 24) -> str:
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    return " ".join(text[lo:hi].split())


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


# ------------------------------------------------------------------- checks

def _check_em_dash(text: str) -> list[Finding]:
    count = text.count(_EM_DASH)
    if count == 0:
        return []
    words = _word_count(text)
    # One em dash is always allowed so a short note is not failed on a single
    # dash; beyond that the ceiling is the density rule from HARNESS §13.
    allowance = max(1, math.floor(EM_DASH_CEILING_PER_1000_WORDS * words / 1000))
    if count <= allowance:
        return []
    density = count * 1000 / max(words, 1)
    first = text.index(_EM_DASH)
    return [Finding(
        "em_dash_density", _line_number(text, first), _excerpt(text, first, first + 1),
        f"{count} em dashes in {words} words ({density:.1f}/1000; ceiling "
        f"{EM_DASH_CEILING_PER_1000_WORDS}/1000, allowance {allowance})",
    )]


def _check_banned(text: str) -> list[Finding]:
    out: list[Finding] = []
    for m in _BANNED_RE.finditer(text):
        word = m.group(1)
        if word.lower() == "harness" and word.isupper():
            continue  # HARNESS = the canon file, not the verb
        out.append(Finding(
            "banned_words", _line_number(text, m.start()),
            _excerpt(text, m.start(), m.end()), f"banned word '{m.group(0)}'",
        ))
    return out


def _check_throat(text: str) -> list[Finding]:
    return [
        Finding("throat_clearing", _line_number(text, m.start()),
                _excerpt(text, m.start(), m.end()), f"throat-clearing opener '{m.group(0)}'")
        for m in _THROAT_RE.finditer(text)
    ]


def _check_antithesis(text: str) -> list[Finding]:
    out: list[Finding] = []
    seen: set[int] = set()
    for rx in _ANTITHESIS_RES:
        for m in rx.finditer(text):
            line = _line_number(text, m.start())
            if line in seen:
                continue
            seen.add(line)
            out.append(Finding(
                "antithetical_parallelism", line, _excerpt(text, m.start(), m.end()),
                "negation-pair contrast; state the positive claim directly",
            ))
    return out


def _check_schedule(text: str) -> list[Finding]:
    out: list[Finding] = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        if founder_quoted(line) or carries_external_date(line):
            continue
        for rx in _SCHEDULE_RES:
            for m in rx.finditer(line):
                out.append(Finding(
                    "schedule_invention", lineno, _excerpt(line, m.start(), m.end()),
                    f"agent-invented schedule phrase '{m.group(0)}'; order by dependency instead",
                ))
    return out


# ----------------------------------------------------------------- entry API

def lint(text: str) -> list[Finding]:
    """Return every finding in `text`, ordered by line then class."""
    prose = strip_code(text)
    findings: list[Finding] = []
    findings.extend(_check_em_dash(prose))
    findings.extend(_check_banned(prose))
    findings.extend(_check_throat(prose))
    findings.extend(_check_antithesis(prose))
    findings.extend(_check_schedule(prose))
    findings.sort(key=lambda f: (f.line, f.cls))
    return findings


def lint_file(path: str | Path) -> list[Finding]:
    return lint(Path(path).read_text(encoding="utf-8", errors="replace"))


def lint_paths(paths: Iterable[str | Path]) -> dict[str, list[Finding]]:
    """Lint files and directories (recursing into `.md` files). Keys are paths."""
    out: dict[str, list[Finding]] = {}
    for raw in paths:
        p = Path(raw)
        files = sorted(p.rglob("*.md")) if p.is_dir() else [p]
        for f in files:
            out[str(f)] = lint_file(f)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    total = 0
    for path, findings in lint_paths(args).items():
        for f in findings:
            print(f.render(path))
        total += len(findings)
    print(f"{total} finding(s)")
    return 1 if total else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
