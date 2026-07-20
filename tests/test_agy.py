"""agy CLI provider 的离线传输测试。"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from trans_novel.config import Config
from trans_novel.llm.providers.agy import AgyClient, format_agy_prompt


def _config(**llm_overrides) -> Config:
    llm = {
        "provider": "agy",
        "timeout": 12,
        "tiers": {
            "strong": {"model": "pro-model"},
            "cheap": {"model": "flash-model"},
            "fast": {"model": "flash-model"},
        },
        **llm_overrides,
    }
    return Config.from_dict({"llm": llm})


class TestAgyPrompt(unittest.TestCase):
    def test_folds_roles_into_one_ordinary_prompt(self):
        prompt = format_agy_prompt(
            [
                {"role": "system", "content": "翻译成简体中文。"},
                {"role": "user", "content": "こんにちは"},
            ]
        )
        self.assertEqual(
            prompt,
            "System:\n翻译成简体中文。\n\nUser:\nこんにちは",
        )

    def test_json_mode_adds_plain_prompt_requirement(self):
        prompt = format_agy_prompt(
            [{"role": "user", "content": "返回译文数组"}], json_mode=True
        )
        self.assertIn("valid JSON", prompt)
        self.assertIn("Do not use Markdown fences", prompt)


class TestAgyClient(unittest.TestCase):
    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_invokes_fresh_print_session_with_model_and_unicode(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "你好\n", "")
        client = AgyClient(_config(command="custom-agy", cwd=".").llm)

        result = client.complete(
            [{"role": "user", "content": "こんにちは"}],
            tier="cheap",
            stage="Translator",
        )

        self.assertEqual(result, "你好")
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ["custom-agy", "--model", "flash-model"])
        self.assertIn("--print", args)
        self.assertNotIn("--continue", args)
        self.assertIn("こんにちは", args[-1])
        self.assertEqual(run.call_args.kwargs["timeout"], 17)
        self.assertEqual(client.usage_summary()["totals"]["calls"], 1)

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_nonzero_exit_raises_clear_error(self, run):
        run.return_value = subprocess.CompletedProcess([], 7, "", "登录失效")
        with self.assertRaisesRegex(RuntimeError, "退出码 7.*登录失效"):
            AgyClient(_config().llm).complete(
                [{"role": "user", "content": "x"}]
            )

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_timeout_raises_clear_error(self, run):
        run.side_effect = subprocess.TimeoutExpired("agy", 17)
        with self.assertRaisesRegex(RuntimeError, "12 秒后超时"):
            AgyClient(_config().llm).complete(
                [{"role": "user", "content": "x"}]
            )

    def test_factory_accepts_both_provider_names(self):
        from trans_novel.llm.factory import build_client

        self.assertIsInstance(build_client(_config()), AgyClient)
        alias = _config(provider="agy-cli")
        self.assertIsInstance(build_client(alias), AgyClient)

    def test_rejects_missing_working_directory(self):
        with self.assertRaisesRegex(ValueError, "cwd 不是现有目录"):
            AgyClient(_config(cwd="definitely-missing-agy-directory").llm)

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_maps_openclaw_short_model_id_for_current_agy(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cfg = _config(tiers={"strong": {"model": "gemini-3.5-flash-high"}})

        AgyClient(cfg.llm).complete([{"role": "user", "content": "x"}])

        self.assertEqual(
            run.call_args.args[0][1:3],
            ["--model", "Gemini 3.5 Flash (High)"],
        )


if __name__ == "__main__":
    unittest.main()
