"""Final-review issue validation and evidence provenance.

The reviewer model proposes candidates.  This module owns the deterministic
part of the review contract: supported issue types, segment provenance, and
the rule that a terminology finding must cite an exact source-to-target
mapping.  Glossary aliases are retrieval hints and can never inherit the
canonical target as an enforceable whole-string translation.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..glossary.store import GlossaryTerm, source_matches_text

ISSUE_TYPES = {
    "missing",
    "added",
    "mistranslation",
    "terminology",
    "pronoun",
    "fluency",
}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def _uncovered_occurrence(
    needle: str,
    haystack: str,
    covering_values: Iterable[str],
) -> bool:
    """Return whether ``needle`` occurs outside every longer known value.

    This gives exact mappings precedence over substring mappings.  For
    example, ``ノエル`` inside ``吉川ノエル`` is not evidence that the short
    name mapping applies, and ``诺艾尔`` inside ``吉川诺艾尔`` is not evidence
    that a short-name target is already present.
    """
    normalized_needle = _normalized(needle)
    normalized_haystack = _normalized(haystack)
    if not normalized_needle:
        return False
    covers: list[tuple[int, int]] = []
    for value in covering_values:
        normalized_value = _normalized(value)
        if (
            len(normalized_value) <= len(normalized_needle)
            or normalized_needle not in normalized_value
        ):
            continue
        start = 0
        while True:
            index = normalized_haystack.find(normalized_value, start)
            if index < 0:
                break
            covers.append((index, index + len(normalized_value)))
            start = index + 1

    start = 0
    while True:
        index = normalized_haystack.find(normalized_needle, start)
        if index < 0:
            return False
        end = index + len(normalized_needle)
        if not any(left <= index and end <= right for left, right in covers):
            return True
        start = index + 1


@dataclass
class ReviewValidationOutcome:
    """Accepted and dismissed review candidates from one review scope."""

    issues: list[dict[str, Any]] = field(default_factory=list)
    dismissed: list[dict[str, Any]] = field(default_factory=list)

    def extend(self, other: ReviewValidationOutcome) -> None:
        self.issues.extend(other.issues)
        self.dismissed.extend(other.dismissed)


class ReviewIssueValidator:
    """Validate reviewer candidates against the visible segment evidence."""

    @staticmethod
    def _exact_term(
        source: str,
        terms: list[GlossaryTerm],
    ) -> GlossaryTerm | None:
        literal = [term for term in terms if term.source == source]
        if len(literal) == 1:
            return literal[0]
        normalized = _normalized(source)
        matches = [term for term in terms if _normalized(term.source) == normalized]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _dismiss(issue: dict[str, Any], reason: str) -> dict[str, Any]:
        dismissed = dict(issue)
        dismissed["dismissed_reason"] = reason
        return dismissed

    def validate(
        self,
        candidates: list[dict[str, Any]],
        sources: list[str],
        targets: list[str],
        terms: Iterable[GlossaryTerm],
    ) -> ReviewValidationOutcome:
        """Return only candidates supported by the visible review evidence."""
        outcome = ReviewValidationOutcome()
        term_list = list(terms)
        for raw in candidates:
            issue = dict(raw)
            index = issue.get("index")
            if not isinstance(index, int) or not 0 <= index < len(sources):
                outcome.dismissed.append(self._dismiss(issue, "invalid_segment_index"))
                continue
            issue_type = _text(issue.get("type"))
            if issue_type not in ISSUE_TYPES:
                outcome.dismissed.append(self._dismiss(issue, "unsupported_issue_type"))
                continue

            source = sources[index]
            target = targets[index]
            evidence: dict[str, Any] = {
                "validation": "segment_pair",
                "source": source,
                "target": target,
            }
            if issue_type == "terminology":
                supplied = issue.get("evidence")
                supplied_term_source = (
                    supplied.get("term_source") if isinstance(supplied, dict) else None
                )
                term_source = _text(issue.get("term_source") or supplied_term_source)
                term = self._exact_term(term_source, term_list)
                if term is None:
                    outcome.dismissed.append(
                        self._dismiss(issue, "term_source_not_an_exact_mapping")
                    )
                    continue
                if not source_matches_text(
                    term.source, source
                ) or not _uncovered_occurrence(
                    term.source,
                    source,
                    (other.source for other in term_list if other is not term),
                ):
                    outcome.dismissed.append(
                        self._dismiss(issue, "exact_term_source_not_in_segment")
                    )
                    continue
                if _uncovered_occurrence(
                    term.target,
                    target,
                    (other.target for other in term_list if other is not term),
                ):
                    outcome.dismissed.append(
                        self._dismiss(issue, "term_target_already_present")
                    )
                    continue
                issue["term_source"] = term.source
                evidence.update(
                    {
                        "validation": "exact_term_mapping",
                        "term_source": term.source,
                        "term_target": term.target,
                    }
                )

            issue["evidence"] = evidence
            outcome.issues.append(issue)
        return outcome
