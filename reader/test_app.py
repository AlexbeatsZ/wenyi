import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from reader.app import ReaderState, create_server


class ReaderAppTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temp.name) / "state" / "book"
        chapters = self.run_dir / "chapters"
        chapters.mkdir(parents=True)
        (self.run_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "title": "测试小说",
                    "source_lang": "ja",
                    "target_lang": "zh",
                    "chapters": [
                        {
                            "index": 0,
                            "title": "第一章",
                            "status": "pending",
                            "review_status": "pending",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (chapters / "ch0.json").write_text(
            json.dumps(
                {
                    "index": 0,
                    "title": "第一章",
                    "segments": [
                        {"index": 0, "kind": "heading", "source": "見出し", "target": "标题"},
                        {"index": 1, "kind": "text", "source": "本文", "target": None},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.state = ReaderState(self.run_dir)

    def tearDown(self):
        self.temp.cleanup()

    def test_book_payload_reports_partial_translation_and_review(self):
        payload = self.state.book_payload()
        self.assertEqual(payload["title"], "测试小说")
        self.assertEqual(payload["done_chapters"], 0)
        self.assertEqual(payload["translated_segments"], 1)
        self.assertEqual(payload["total_segments"], 2)
        self.assertEqual(payload["chapters"][0]["status"], "translating")
        self.assertFalse(payload["review_complete"])

    def test_chapter_payload_keeps_source_and_current_target(self):
        payload = self.state.chapter_payload(0)
        self.assertEqual(payload["translated_segments"], 1)
        self.assertEqual(payload["segments"][0]["target"], "标题")
        self.assertIsNone(payload["segments"][1]["target"])

    def test_http_api_and_mobile_page(self):
        server = create_server(self.state, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(f"{base}/api/book", timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["title"], "测试小说")
            with urlopen(base, timeout=3) as response:
                html = response.read().decode("utf-8")
            self.assertIn("每分钟自动查询", html)
            self.assertIn("立即查询", html)
            self.assertIn("显示原文", html)
            self.assertIn("#0b0d10", html)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
