"""审校 / 润色 / 回译抽检 测试（离线）。"""

from __future__ import annotations

import json
import re
import threading
import unittest

from trans_novel.config import Config
from trans_novel.ingest.models import Segment
from trans_novel.llm.base import ContentPolicyError
from trans_novel.llm.providers.fake import FakeClient
from trans_novel.agents.reviewer import Reviewer, BackTranslator
from trans_novel.agents.polisher import Polisher
from trans_novel.pipeline.orchestrator import Orchestrator


def _cfg():
    return Config.from_dict({
        "language": {"source": "ja", "target": "zh"},
        "llm": {"provider": "fake", "tiers": {
            "strong": {"model": "p"}, "cheap": {"model": "f"}}},
    })


class TestReviewer(unittest.TestCase):
    def test_review_reports_issues(self):
        issues = {"issues": [
            {"index": 0, "type": "missing", "detail": "漏了后半句"},
            {"index": 1, "type": "terminology", "detail": "人名译法不符"},
        ]}
        client = FakeClient(handler=lambda m, t, j: json.dumps(issues, ensure_ascii=False))
        r = Reviewer(client, _cfg())
        out = r.review(["あ", "い"], ["甲", "乙"])
        self.assertEqual(len(out), 2)
        self.assertEqual(client.calls[-1]["tier"], "cheap")  # 审校走廉价档

    def test_chapter_review_chunks_run_concurrently_and_merge_in_order(self):
        barrier = threading.Barrier(2)

        def handler(messages, tier, json_mode):
            user = messages[1]["content"]
            barrier.wait(timeout=2)
            detail = "甲" if "源文甲" in user else "乙"
            return json.dumps({"issues": [{
                "index": 0,
                "type": "missing",
                "detail": detail,
            }]}, ensure_ascii=False)

        cfg = _cfg()
        cfg.segment.max_chars_per_batch = 1  # 审校预算=3，使两个 3 字段落各成一块
        cfg.pipeline.review_concurrency = 2
        orch = Orchestrator(cfg, client=FakeClient(handler=handler))
        segments = [
            Segment(index=0, source="源文甲", target="译文甲"),
            Segment(index=1, source="源文乙", target="译文乙"),
        ]

        issues = orch._review_chapter(segments, [])

        self.assertEqual([it["index"] for it in issues], [0, 1])
        self.assertEqual([it["detail"] for it in issues], ["甲", "乙"])

    def test_invalid_json_retries_then_recursively_splits_review_chunk(self):
        """整块连续返回坏 JSON 时二分；子块成功后索引仍映射到原章。"""
        def handler(messages, tier, json_mode):
            user = "\n".join(message["content"] for message in messages)
            count = len(re.findall(r"^\[\d+\] 原文：", user, re.M))
            if count > 1:
                return '{"issues":['
            return json.dumps({"issues": [{
                "index": 0,
                "type": "missing",
                "detail": "单段恢复",
            }]}, ensure_ascii=False)

        cfg = _cfg()
        cfg.segment.max_chars_per_batch = 1000
        cfg.pipeline.review_concurrency = 1
        client = FakeClient(handler=handler)
        orch = Orchestrator(cfg, client=client)
        segments = [
            Segment(index=0, source="源文甲", target="译文甲"),
            Segment(index=1, source="源文乙", target="译文乙"),
        ]

        issues = orch._review_chapter(segments, [])

        self.assertEqual([it["index"] for it in issues], [0, 1])
        review_calls = [
            call for call in client.calls
            if "译文审校" in call["messages"][0]["content"]
        ]
        self.assertEqual(len(review_calls), 4)  # 原块两次 + 两个单段


class TestPolisher(unittest.TestCase):
    def test_polish_ok(self):
        client = FakeClient(handler=lambda m, t, j: json.dumps(
            {"polished": ["润色甲", "润色乙"]}, ensure_ascii=False))
        p = Polisher(client, _cfg())
        out = p.polish(
            ["甲", "乙"],
            sources=["あ", "い"],
            context="前文最终译文",
            book_synopsis="全书概览",
            chapter_digest="本章梗概",
        )
        self.assertEqual(out, ["润色甲", "润色乙"])
        self.assertEqual(client.calls[-1]["tier"], "strong")
        user = client.calls[-1]["messages"][-1]["content"]
        self.assertIn("あ", user)
        self.assertIn("甲", user)
        self.assertIn("前文最终译文", user)
        self.assertIn("全书概览", user)
        self.assertIn("本章梗概", user)

    def test_polish_mismatch_keeps_original(self):
        client = FakeClient(handler=lambda m, t, j: json.dumps(
            {"polished": ["只有一段"]}, ensure_ascii=False))
        p = Polisher(client, _cfg())
        out = p.polish(["甲", "乙"])
        self.assertEqual(out, ["甲", "乙"])  # 段数不符 → 保守保留原译
        self.assertEqual(p.last_failed_indexes, [0, 1])

    def test_policy_rejected_segment_uses_deepseek_fallback_only_for_it(self):
        def gemini_handler(messages, tier, json_mode):
            user = messages[-1]["content"]
            count = len(re.findall(r"^\[(\d+)\]", user, re.M))
            if count > 1 or "拒否対象" in user:
                raise ContentPolicyError("policy rejected")
            return json.dumps({"polished": ["Gemini精修"]}, ensure_ascii=False)

        deepseek = FakeClient(
            handler=lambda messages, tier, json_mode: json.dumps(
                {"polished": ["DeepSeek兜底"]}, ensure_ascii=False
            )
        )
        gemini = FakeClient(handler=gemini_handler)
        polisher = Polisher(gemini, _cfg(), fallback_client=deepseek)

        result = polisher.polish(["通常一", "拒否対象", "通常二"])

        self.assertEqual(
            result,
            ["Gemini精修", "DeepSeek兜底", "Gemini精修"],
        )
        self.assertEqual(len(deepseek.calls), 1)
        self.assertIn("拒否対象", deepseek.calls[0]["messages"][-1]["content"])
        self.assertEqual(polisher.last_policy_fallback_indexes, [1])
        self.assertEqual(polisher.last_failed_indexes, [])

    def test_context_only_policy_rejection_retries_whole_polish_batch(self):
        def handler(messages, tier, json_mode):
            user = messages[-1]["content"]
            if "后文身份反转" in user:
                raise ContentPolicyError("policy rejected combined context")
            count = len(re.findall(r"^\[(\d+)\]", user, re.M))
            return json.dumps(
                {"polished": [f"Pro精修{i}" for i in range(count)]},
                ensure_ascii=False,
            )

        client = FakeClient(handler=handler)
        polisher = Polisher(client, _cfg())

        result = polisher.polish(
            ["初译一", "初译二", "初译三"],
            sources=["原文一", "原文二", "原文三"],
            context="最近译文",
            book_synopsis="后文身份反转",
            chapter_digest="本章结局",
        )

        self.assertEqual(result, ["Pro精修0", "Pro精修1", "Pro精修2"])
        self.assertEqual(polisher.last_policy_context_fallback_indexes, [0, 1, 2])
        self.assertEqual(polisher.last_policy_fallback_indexes, [])
        self.assertEqual(len(client.calls), 2)
        retry_user = client.calls[-1]["messages"][-1]["content"]
        self.assertNotIn("后文身份反转", retry_user)
        self.assertNotIn("本章结局", retry_user)


class TestBackTranslator(unittest.TestCase):
    def test_check(self):
        def handler(messages, tier, json_mode):
            system = messages[0]["content"]
            if "回译译者" in system:
                return json.dumps({"backtranslations": ["あ", "い"]}, ensure_ascii=False)
            if "保真度" in system:
                return json.dumps({"issues": [{"index": 1, "detail": "含义改变"}]},
                                  ensure_ascii=False)
            return "{}"

        bt = BackTranslator(FakeClient(handler=handler), _cfg())
        issues = bt.check(["あ", "い"], ["甲", "乙"])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["index"], 1)


if __name__ == "__main__":
    unittest.main()
