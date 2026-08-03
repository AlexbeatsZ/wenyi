import json
import tempfile
import unittest
from pathlib import Path

from observer.app import Observer


class ObserverTests(unittest.TestCase):
    def _write_chapter(self, root: Path, index: int, segments: list[dict]) -> Path:
        chapters = root / "chapters"
        chapters.mkdir(exist_ok=True)
        path = chapters / f"ch{index}.json"
        path.write_text(
            json.dumps({"index": index, "title": f"章节 {index + 1}", "segments": segments}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_snapshot_exposes_review_evidence_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {
                "title": "测试书",
                "chapters": [
                    {"index": 0, "title": "第一章", "status": "done", "review_status": "done"},
                    {"index": 1, "title": "第二章", "status": "done", "review_status": "running"},
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            events = [
                {"event": "autofix_applied", "chapter": 0, "index": 3, "source": "原文", "before": "旧译", "after": "新译", "issues": [{"detail": "含义错误"}], "ts": "2026-08-03T10:00:00+08:00"},
                {"event": "chapter_reviewed", "chapter": 0, "issue_count": 1, "issues": [{"index": 3, "type": "mistranslation", "detail": "含义错误", "suggestion": "改正", "fixed": True}], "ts": "2026-08-03T10:00:01+08:00"},
            ]
            (root / "events.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in events), encoding="utf-8")
            (root / "usage.json").write_text(json.dumps({"totals": {"calls": 2, "total_tokens": 100}, "by_stage": {"Reviewer": {"total_tokens": 100}}}), encoding="utf-8")
            cfg = root / "config.yaml"
            cfg.write_text("review_llm:\n  provider: codex-cli\n  tiers:\n    strong:\n      model: gpt-5.6-sol\n", encoding="utf-8")
            before = {path.name: path.stat().st_mtime_ns for path in root.iterdir()}
            data = Observer(root, cfg).snapshot()
            after = {path.name: path.stat().st_mtime_ns for path in root.iterdir()}
            self.assertEqual(before, after)
            self.assertEqual(data["current"]["stage"], "review")
            self.assertEqual(data["current"]["chapter"], 1)
            self.assertEqual(data["current"]["model"], "gpt-5.6-sol")
            self.assertEqual(data["quality"], {"issues": 1, "fixed": 1, "unfixed": 0, "types": {"mistranslation": 1}})
            self.assertEqual(data["issues"][0]["before"], "旧译")
            self.assertEqual(data["issues"][0]["after"], "新译")

    def test_latest_review_per_chapter_prevents_rerun_double_count(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "manifest.json").write_text(json.dumps({"chapters": []}), encoding="utf-8")
            rows = [
                {"event": "chapter_reviewed", "chapter": 2, "issues": [{"index": 0, "type": "missing", "fixed": False}], "ts": "2026-08-03T10:00:00+08:00"},
                {"event": "chapter_reviewed", "chapter": 2, "issues": [{"index": 1, "type": "added", "fixed": False}], "ts": "2026-08-03T11:00:00+08:00"},
            ]
            (root / "events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
            data = Observer(root).snapshot()
            self.assertEqual(data["quality"]["issues"], 1)
            self.assertEqual(data["quality"]["types"], {"added": 1})

    def test_combined_reader_returns_only_readable_translated_segments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {
                "title": "剧情测试",
                "chapters": [{"index": 0, "title": "序章", "status": "done", "review_status": "done"}],
            }
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            chapter_path = self._write_chapter(root, 0, [
                {"index": 0, "kind": "text", "source": "原文一", "target": "译文一"},
                {"index": 1, "kind": "text", "source": "原文二", "target": None},
                {"index": 2, "kind": "image", "source": "", "target": ""},
            ])
            before = {path: path.stat().st_mtime_ns for path in (root / "manifest.json", chapter_path)}
            observer = Observer(root)
            book = observer.book_payload()
            chapter = observer.chapter_payload(0)
            after = {path: path.stat().st_mtime_ns for path in (root / "manifest.json", chapter_path)}
            self.assertEqual(before, after)
            self.assertEqual(book["translated_segments"], 1)
            self.assertEqual(book["total_segments"], 2)
            self.assertEqual(book["review_done_chapters"], 1)
            self.assertEqual(chapter["segments"][0]["target"], "译文一")
            self.assertFalse(chapter["segments"][1]["translated"])
            self.assertEqual(len(chapter["segments"]), 2)


if __name__ == "__main__":
    unittest.main()
