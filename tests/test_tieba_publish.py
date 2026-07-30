"""贴吧逐章发布的正文整理、分层与断点测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trans_novel.ingest.models import Chapter, Segment
from trans_novel.publish.tieba import (
    PublishJournal,
    TiebaPublishError,
    build_chapter_parts,
    build_publish_plan,
    chapter_paragraphs,
    thread_id_from_url,
)


def _chapter(
    index: int = 1,
    *,
    body: list[str] | None = None,
) -> Chapter:
    segments = [
        Segment(index=0, source="date", target="2026-07-30"),
        Segment(index=1, source="count", target="本章共 100 个字"),
        Segment(index=2, source="title", target="第1话 标题", kind="heading"),
    ]
    for offset, target in enumerate(body or ["第一段", "第二段"], 3):
        segments.append(Segment(index=offset, source=target, target=target))
    return Chapter(index=index, title="source title", segments=segments)


class TestTiebaFormatting(unittest.TestCase):
    def test_url_requires_tieba_thread(self):
        self.assertEqual(
            thread_id_from_url("https://tieba.baidu.com/p/10905826072?fr=x"),
            "10905826072",
        )
        with self.assertRaises(ValueError):
            thread_id_from_url("https://example.com/p/10905826072")

    def test_chapter_paragraphs_skip_leading_epub_metadata(self):
        chapter = _chapter()
        chapter.segments.append(
            Segment(
                index=5,
                source="continued",
                target="（续）",
                cont=True,
            )
        )

        self.assertEqual(
            chapter_paragraphs(chapter),
            ["第1话 标题", "第一段", "第二段（续）"],
        )

    def test_long_chapter_splits_on_paragraph_boundaries(self):
        chapter = _chapter(body=["甲" * 120, "乙" * 120, "丙" * 120])

        parts = build_chapter_parts(chapter, max_chars=250)

        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0].marker, "【第1话（1/3）】")
        self.assertEqual(parts[2].marker, "【第1话（3/3）】")
        self.assertTrue(all(len(part.body) <= 250 for part in parts))
        self.assertEqual(
            "".join(
                part.body.split("\n\n", 1)[1].replace("\n\n", "")
                for part in parts
            ),
            "第1话 标题" + "甲" * 120 + "乙" * 120 + "丙" * 120,
        )

    def test_plan_rejects_unfinished_chapter(self):
        class Store:
            @staticmethod
            def load_manifest():
                return {
                    "chapters": [
                        {"index": 1, "status": "done"},
                        {"index": 2, "status": "pending"},
                    ]
                }

            @staticmethod
            def load_chapter(index):
                return _chapter(index)

        with self.assertRaisesRegex(ValueError, "第 2 章尚未完成"):
            build_publish_plan(Store(), start=1, end=2)


class TestTiebaJournal(unittest.TestCase):
    def test_journal_persists_and_rejects_changed_body(self):
        part = build_chapter_parts(_chapter())[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.json"
            journal = PublishJournal(
                path,
                thread_url="https://tieba.baidu.com/p/10905826072",
            )
            journal.mark(part, "posted")

            loaded = PublishJournal(
                path,
                thread_url="https://tieba.baidu.com/p/10905826072",
            )
            self.assertEqual(loaded.status(part), "posted")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["items"]["ch1-part1"]["marker"], "【第1话】")

            changed = build_chapter_parts(_chapter(body=["已修改"]))[0]
            with self.assertRaises(TiebaPublishError):
                loaded.status(changed)


if __name__ == "__main__":
    unittest.main()

