from __future__ import annotations

import unittest

from trans_novel.glossary.store import TYPE_PERSON, GlossaryTerm
from trans_novel.pipeline.review_validation import ReviewIssueValidator


class TestReviewIssueValidator(unittest.TestCase):
    def setUp(self):
        self.validator = ReviewIssueValidator()

    def test_alias_cannot_inherit_full_name_target(self):
        terms = [
            GlossaryTerm(
                source="吉川ノエル",
                target="吉川诺艾尔",
                type=TYPE_PERSON,
                aliases=["ノエル"],
            )
        ]

        outcome = self.validator.validate(
            [
                {
                    "index": 0,
                    "type": "terminology",
                    "term_source": "ノエル",
                    "detail": "应改成全名",
                    "suggestion": "吉川诺艾尔",
                }
            ],
            ["ノエルは呟いた。"],
            ["诺艾尔低声说道。"],
            terms,
        )

        self.assertEqual(outcome.issues, [])
        self.assertEqual(len(outcome.dismissed), 1)
        self.assertEqual(
            outcome.dismissed[0]["dismissed_reason"],
            "term_source_not_an_exact_mapping",
        )

    def test_independent_short_name_mapping_can_be_enforced(self):
        terms = [
            GlossaryTerm("吉川ノエル", "吉川诺艾尔", type=TYPE_PERSON),
            GlossaryTerm("ノエル", "诺艾尔", type=TYPE_PERSON),
        ]

        outcome = self.validator.validate(
            [
                {
                    "index": 0,
                    "type": "terminology",
                    "term_source": "ノエル",
                    "detail": "简称译名错误",
                    "suggestion": "诺艾尔低声说道。",
                }
            ],
            ["ノエルは呟いた。"],
            ["吉川诺艾尔低声说道。"],
            terms,
        )

        self.assertEqual(len(outcome.issues), 1)
        evidence = outcome.issues[0]["evidence"]
        self.assertEqual(evidence["term_source"], "ノエル")
        self.assertEqual(evidence["term_target"], "诺艾尔")
        self.assertEqual(evidence["validation"], "exact_term_mapping")

    def test_longer_exact_source_wins_over_short_name_substring(self):
        terms = [
            GlossaryTerm("吉川ノエル", "吉川诺艾尔", type=TYPE_PERSON),
            GlossaryTerm("ノエル", "诺艾尔", type=TYPE_PERSON),
        ]

        outcome = self.validator.validate(
            [
                {
                    "index": 0,
                    "type": "terminology",
                    "term_source": "ノエル",
                    "detail": "不应对全名内部的子串套用简称规则",
                    "suggestion": "诺艾尔抵达。",
                }
            ],
            ["吉川ノエルが到着した。"],
            ["吉川诺艾尔抵达了。"],
            terms,
        )

        self.assertEqual(outcome.issues, [])
        self.assertEqual(
            outcome.dismissed[0]["dismissed_reason"],
            "exact_term_source_not_in_segment",
        )

    def test_terminology_issue_is_dismissed_when_target_is_already_present(self):
        terms = [GlossaryTerm("ノエル", "诺艾尔", type=TYPE_PERSON)]

        outcome = self.validator.validate(
            [
                {
                    "index": 0,
                    "type": "terminology",
                    "term_source": "ノエル",
                    "detail": "误报",
                    "suggestion": "诺艾尔低声说道。",
                }
            ],
            ["ノエルは呟いた。"],
            ["诺艾尔低声说道。"],
            terms,
        )

        self.assertEqual(outcome.issues, [])
        self.assertEqual(
            outcome.dismissed[0]["dismissed_reason"],
            "term_target_already_present",
        )

    def test_fluency_issue_keeps_segment_pair_provenance(self):
        outcome = self.validator.validate(
            [
                {
                    "index": 0,
                    "type": "fluency",
                    "detail": "中文谓语生硬",
                    "suggestion": "刚才闪了一下。",
                }
            ],
            ["「光った」"],
            ["「闪光了」"],
            [],
        )

        self.assertEqual(len(outcome.issues), 1)
        self.assertEqual(outcome.issues[0]["evidence"]["validation"], "segment_pair")


if __name__ == "__main__":
    unittest.main()
