from __future__ import annotations

import os
import tempfile
import unittest

from trans_novel.glossary.store import GlossaryStore, GlossaryTerm, TYPE_PERSON
from trans_novel.narrative import NarrativeKnowledge, NarrativePosition


class TestNarrativeKnowledge(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GlossaryStore(os.path.join(self.temp.name, "g.db"))
        self.store.upsert_term(
            GlossaryTerm("仮面の人", "蒙面人", type=TYPE_PERSON, status="ok")
        )
        self.knowledge = NarrativeKnowledge(self.store)
        self.knowledge.upsert_entity("person:anna", "安奈")
        self.knowledge.register_alias("仮面の人", "person:anna")
        self.knowledge.add_fact(
            "person:anna",
            "gender",
            "女",
            evidence="她摘下面具，众人称她为女性",
            evidence_at=NarrativePosition(3, 10),
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_later_gender_fact_is_not_visible_early(self):
        early = self.knowledge.view("仮面の人", NarrativePosition(1, 0))
        late = self.knowledge.view("仮面の人", NarrativePosition(3, 10))

        self.assertEqual(early.terms[0].gender, "")
        self.assertEqual(early.facts, [])
        self.assertEqual(late.terms[0].gender, "女")
        self.assertIn("性别=女", late.render_facts())
        self.assertEqual(self.knowledge.change_points(3, 0, 20), [10])

    def test_conflicting_active_fact_is_withheld(self):
        self.knowledge.add_fact(
            "person:anna",
            "gender",
            "男",
            evidence="冲突证据",
            evidence_at=NarrativePosition(3, 10),
        )

        view = self.knowledge.view("仮面の人", NarrativePosition(4, 0))

        self.assertEqual(view.facts, [])
        self.assertEqual(view.terms[0].gender, "")

    def test_time_scoped_role_does_not_inherit_two_people(self):
        self.store.upsert_term(
            GlossaryTerm("店長", "店长", type="称谓", status="ok")
        )
        self.knowledge.upsert_entity("person:ozeki", "尾关")
        self.knowledge.register_alias(
            "店長",
            "person:anna",
            visible_until=NarrativePosition(4, 99),
        )
        self.knowledge.register_alias(
            "店長",
            "person:ozeki",
            visible_from=NarrativePosition(5, 0),
        )
        self.knowledge.add_fact(
            "person:ozeki",
            "gender",
            "女",
            evidence="尾关明确是女性",
            evidence_at=NarrativePosition(0, 0),
        )

        early = self.knowledge.view("店長", NarrativePosition(2, 0))
        late = self.knowledge.view("店長", NarrativePosition(6, 0))

        early_role = next(term for term in early.terms if term.source == "店長")
        late_role = next(term for term in late.terms if term.source == "店長")
        self.assertEqual(early_role.gender, "")
        self.assertEqual(late_role.gender, "女")

    def test_unlocated_model_claim_is_not_persisted_as_fact(self):
        self.knowledge.seed_character(
            {
                "source": "ケンコ",
                "target": "健子",
                "gender": "女",
                "gender_confidence": "confirmed",
                "gender_evidence": "女性と明記",
                "gender_evidence_chapter": None,
            }
        )

        view = self.knowledge.view("ケンコ", NarrativePosition(20, 0))

        self.assertEqual(view.facts, [])


if __name__ == "__main__":
    unittest.main()
