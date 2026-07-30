"""贴吧逐章发布的正文整理、分层与断点测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trans_novel.ingest.models import Chapter, Segment
from trans_novel.publish.tieba import (
    PublishJournal,
    PublishRunLock,
    TiebaBrowserPublisher,
    TiebaPublishError,
    build_chapter_parts,
    build_publish_plan,
    chapter_paragraphs,
    publish_plan,
    rendered_body_matches,
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

    def test_rendered_body_accepts_equal_length_star_redaction(self):
        self.assertEqual(
            rendered_body_matches(
                "尾关，你给我闭嘴！",
                "尾关，****嘴！",
            ),
            (True, 4),
        )
        self.assertEqual(
            rendered_body_matches("原始正文", "原始错文"),
            (False, 0),
        )
        self.assertEqual(
            rendered_body_matches("原始正文", "****"),
            (True, 4),
        )


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

    def test_publish_lock_rejects_second_process_for_same_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.json.lock"
            with PublishRunLock(path), self.assertRaisesRegex(
                TiebaPublishError,
                "另一个贴吧发布进程",
            ), PublishRunLock(path):
                pass

    def test_publish_plan_recovers_persisted_submitting_part(self):
        part = build_chapter_parts(_chapter())[0]

        class Publisher:
            posted = False

            def __init__(self, **_):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def open_thread(self, _):
                pass

            def inspect_post(self, _):
                return 4

            def post(self, _):
                Publisher.posted = True
                return 0

        with tempfile.TemporaryDirectory() as directory:
            journal = PublishJournal(
                Path(directory) / "journal.json",
                thread_url="https://tieba.baidu.com/p/10905826072",
            )
            journal.mark(part, "submitting")
            with patch(
                "trans_novel.publish.tieba.TiebaBrowserPublisher",
                Publisher,
            ):
                publish_plan(
                    [part],
                    thread_url="https://tieba.baidu.com/p/10905826072",
                    journal=journal,
                    profile_dir=directory,
                    delay_seconds=0,
                    jitter_seconds=0,
                )

            self.assertFalse(Publisher.posted)
            self.assertEqual(journal.status(part), "posted")
            self.assertEqual(journal.data["items"][part.key]["redacted_chars"], 4)

    def test_publish_plan_retries_missing_submitting_part(self):
        part = build_chapter_parts(_chapter())[0]

        class Publisher:
            posted = False

            def __init__(self, **_):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def open_thread(self, _):
                pass

            def inspect_post(self, _):
                return None

            def post(self, _):
                Publisher.posted = True
                return 0

        with tempfile.TemporaryDirectory() as directory:
            journal = PublishJournal(
                Path(directory) / "journal.json",
                thread_url="https://tieba.baidu.com/p/10905826072",
            )
            journal.mark(part, "submitting")
            with patch(
                "trans_novel.publish.tieba.TiebaBrowserPublisher",
                Publisher,
            ):
                publish_plan(
                    [part],
                    thread_url="https://tieba.baidu.com/p/10905826072",
                    journal=journal,
                    profile_dir=directory,
                    delay_seconds=0,
                    jitter_seconds=0,
                )

            self.assertTrue(Publisher.posted)
            self.assertEqual(journal.status(part), "posted")

    def test_editor_render_failure_refreshes_before_giving_up(self):
        publisher = object.__new__(TiebaBrowserPublisher)
        outcomes = iter((False, True))
        navigations = []

        class Page:
            @staticmethod
            def wait_for_timeout(_):
                pass

        publisher.page = Page()
        publisher.thread_url = "https://tieba.baidu.com/p/10905826072"
        publisher._open_editor = lambda: next(outcomes)
        publisher._goto_thread = navigations.append

        publisher._ensure_editor()

        self.assertEqual(navigations, [publisher.thread_url])


if __name__ == "__main__":
    unittest.main()
