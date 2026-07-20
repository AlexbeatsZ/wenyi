"""翻译 agent 的对齐保证测试（离线 FakeClient）。"""

from __future__ import annotations

import json
import re
import unittest

from trans_novel.config import Config
from trans_novel.agents import prompts
from trans_novel.llm.providers.fake import FakeClient
from trans_novel.llm.base import ContentPolicyError
from trans_novel.agents.translator import Translator
from trans_novel.pipeline.checks import length_flags


def _count_segments(user_content: str) -> int:
    return len(re.findall(r"^\[(\d+)\]", user_content, re.M))


class TestTranslatorAlignment(unittest.TestCase):
    def _config(self):
        return Config.from_dict({
            "language": {"source": "ja", "target": "zh"},
            "llm": {"provider": "fake", "tiers": {
                "strong": {"model": "deepseek-v4-pro"},
                "cheap": {"model": "deepseek-v4-flash"},
            }},
            "pipeline": {"align_retry_limit": 1},
        })

    def test_happy_path_aligned(self):
        def handler(messages, tier, json_mode):
            n = _count_segments(messages[-1]["content"])
            return json.dumps({"translations": [f"译{i}" for i in range(n)]},
                              ensure_ascii=False)

        t = Translator(FakeClient(handler=handler), self._config())
        out = t.translate_batch(["あ", "い", "う"])
        self.assertEqual(len(out), 3)
        self.assertEqual(out, ["译0", "译1", "译2"])

    def test_fallback_to_per_segment_on_mismatch(self):
        # 多段批次故意少返回一段；单段调用正常 → 触发逐段兜底
        def handler(messages, tier, json_mode):
            n = _count_segments(messages[-1]["content"])
            trans = [f"译{i}" for i in range(n)]
            if n > 1:
                trans = trans[:-1]  # 故意制造段数不符
            return json.dumps({"translations": trans}, ensure_ascii=False)

        client = FakeClient(handler=handler)
        t = Translator(client, self._config())
        out = t.translate_batch(["あ", "い", "う"])
        self.assertEqual(len(out), 3)  # 兜底后仍保证 1:1
        # 验证确实回退到了逐段（出现过 n==1 的调用）
        single_calls = [c for c in client.calls
                        if _count_segments(c["messages"][-1]["content"]) == 1]
        self.assertGreaterEqual(len(single_calls), 3)

    def test_empty_per_segment_fallback_is_rejected(self):
        client = FakeClient(
            handler=lambda messages, tier, json_mode: json.dumps(
                {"translations": []}
            )
        )
        translator = Translator(client, self._config())

        with self.assertRaisesRegex(Exception, "第 0 段失败"):
            translator.translate_batch(["あ", "い"])

    def test_non_string_translation_is_rejected(self):
        client = FakeClient(
            handler=lambda messages, tier, json_mode: json.dumps(
                {"translations": [None]}
            )
        )
        translator = Translator(client, self._config())

        with self.assertRaisesRegex(Exception, "第 0 段失败"):
            translator.translate_batch(["あ"])

    def test_policy_rejected_single_segment_retries_without_future_context(self):
        def handler(messages, tier, json_mode):
            user = messages[-1]["content"]
            if "后文身份反转" in user:
                raise ContentPolicyError("policy rejected combined context")
            n = _count_segments(user)
            return json.dumps(
                {"translations": ["保守完成的初译" for _ in range(n)]},
                ensure_ascii=False,
            )

        client = FakeClient(handler=handler)
        translator = Translator(client, self._config())

        result = translator.translate_batch(
            ["当前原文"],
            style="完整风格",
            context="最近译文",
            book_synopsis="后文身份反转",
            chapter_digest="本章结局",
        )

        self.assertEqual(result, ["保守完成的初译"])
        self.assertEqual(translator.last_policy_context_fallback_indexes, [0])
        fallback_user = client.calls[-1]["messages"][-1]["content"]
        self.assertNotIn("后文身份反转", fallback_user)
        self.assertNotIn("本章结局", fallback_user)
        self.assertIn("当前原文", fallback_user)

class TestTranslatorPromptOrder(unittest.TestCase):
    def test_static_chapter_digest_precedes_dynamic_glossary(self):
        for template in (prompts.TRANSLATOR_USER, prompts.TRANSLATOR_FIX_USER):
            self.assertLess(
                template.template.index("【本章梗概】"),
                template.template.index("【专有名词对照表】"),
            )


class TestChecks(unittest.TestCase):
    def test_length_flags(self):
        sources = ["これは長い日本語の文章です。" * 3, "短い", "x" * 10]
        targets = ["", "短い但正常的中文译文内容", "x" * 40]
        flags = length_flags(sources, targets)
        kinds = {f.index: f.reason for f in flags}
        self.assertEqual(kinds.get(0), "empty")     # 译文为空
        self.assertEqual(kinds.get(2), "too_long")  # 比值过大


if __name__ == "__main__":
    unittest.main()
