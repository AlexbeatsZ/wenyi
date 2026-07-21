"""agy CLI provider 的离线传输测试。"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from trans_novel.config import Config
from trans_novel.llm.base import ContentPolicyError
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
            "Execution constraint:\n"
            "This is a self-contained text task. Answer directly from the prompt. "
            "Do not call any tools, inspect files, browse, run commands, or use "
            "write_file. Return the answer only in the response text.\n\n"
            "System:\n翻译成简体中文。\n\nUser:\nこんにちは",
        )

    def test_json_mode_adds_plain_prompt_requirement(self):
        prompt = format_agy_prompt(
            [{"role": "user", "content": "返回译文数组"}], json_mode=True
        )
        self.assertIn("valid JSON", prompt)
        self.assertIn("Do not use Markdown fences", prompt)


class TestAgyClient(unittest.TestCase):
    def test_defaults_use_gemini_36_flash(self):
        client = AgyClient(_config(tiers={}).llm)

        self.assertEqual(client.tiers["cheap"].model, "Gemini 3.6 Flash (Medium)")
        self.assertEqual(client.tiers["fast"].model, "Gemini 3.6 Flash (Low)")

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
        self.assertIn("--mode", args)
        self.assertEqual(args[args.index("--mode") + 1], "plan")
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

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_windows_command_line_too_long_is_not_reported_as_missing_cli(self, run):
        error = FileNotFoundError("command line too long")
        error.winerror = 206
        run.side_effect = error

        with self.assertRaisesRegex(RuntimeError, "Windows 命令行过长"):
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
    def test_uses_stable_short_model_id_first(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cfg = _config(tiers={"strong": {"model": "gemini-3.6-flash-high"}})

        AgyClient(cfg.llm).complete([{"role": "user", "content": "x"}])

        self.assertEqual(
            run.call_args.args[0][1:3],
            ["--model", "gemini-3.6-flash-high"],
        )

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_keeps_legacy_gemini_35_short_id_compatible(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cfg = _config(tiers={"strong": {"model": "gemini-3.5-flash-high"}})

        AgyClient(cfg.llm).complete([{"role": "user", "content": "x"}])

        self.assertEqual(
            run.call_args.args[0][1:3],
            ["--model", "gemini-3.5-flash-high"],
        )

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_falls_back_to_legacy_display_name_only_when_slug_is_unknown(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                [],
                1,
                "",
                "model gemini-3.6-flash-high is not recognized as a known model",
            ),
            subprocess.CompletedProcess(
                [],
                1,
                "",
                "model gemini-3.6-flash-high is not recognized as a known model",
            ),
            subprocess.CompletedProcess([], 0, "ok", ""),
        ]
        cfg = _config(tiers={"strong": {"model": "Gemini 3.6 Flash (High)"}})

        result = AgyClient(cfg.llm).complete([{"role": "user", "content": "x"}])

        self.assertEqual(result, "ok")
        self.assertEqual(run.call_count, 3)
        self.assertEqual(
            run.call_args_list[0].args[0][1:3],
            ["--model", "gemini-3.6-flash-high"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0][1:3],
            ["--model", "gemini-3.6-flash-high"],
        )
        self.assertEqual(
            run.call_args_list[2].args[0][1:3],
            ["--model", "Gemini 3.6 Flash (High)"],
        )

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_retries_short_id_before_legacy_fallback(self, run):
        run.side_effect = [
            subprocess.CompletedProcess(
                [],
                1,
                "",
                "model gemini-3.6-flash-medium is not recognized as a known model",
            ),
            subprocess.CompletedProcess([], 0, "ok", ""),
        ]
        cfg = _config(tiers={"strong": {"model": "Gemini 3.6 Flash (Medium)"}})

        result = AgyClient(cfg.llm).complete([{"role": "user", "content": "x"}])

        self.assertEqual(result, "ok")
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(
                call.args[0][1:3],
                ["--model", "gemini-3.6-flash-medium"],
            )

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_retries_headless_tool_permission_denial(self, run):
        denied = (
            'jetski: no output produced — a tool required the "write_file" '
            "permission that headless mode cannot prompt for, so it was auto-denied"
        )
        run.side_effect = [
            subprocess.CompletedProcess([], 0, denied, ""),
            subprocess.CompletedProcess([], 0, '{"translations":["你好"]}', ""),
        ]
        client = AgyClient(_config().llm)

        result = client.complete(
            [{"role": "user", "content": "翻译こんにちは"}], json_mode=True
        )

        self.assertEqual(result, '{"translations":["你好"]}')
        self.assertEqual(run.call_count, 2)
        self.assertIn("Do not call any tools", run.call_args_list[0].args[0][-1])
        self.assertIn("Critical retry instruction", run.call_args.args[0][-1])

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_rejects_persistent_tool_permission_denial(self, run):
        denied = (
            'a tool required the "write_file" permission that headless mode '
            "cannot prompt for, so it was auto-denied"
        )
        run.return_value = subprocess.CompletedProcess([], 0, denied, "")

        with self.assertRaisesRegex(RuntimeError, "误判为工具调用"):
            AgyClient(_config().llm).complete(
                [{"role": "user", "content": "翻译"}]
            )

        self.assertEqual(run.call_count, 3)

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_retries_content_policy_rejection_in_fresh_sessions(self, run):
        rejected = (
            "The prompt could not be submitted. The prompt contains sensitive "
            "words that violate Google's Generative AI Prohibited Use policy."
        )
        run.side_effect = [
            subprocess.CompletedProcess([], 0, rejected, ""),
            subprocess.CompletedProcess([], 0, '{"translations":["译文"]}', ""),
        ]

        result = AgyClient(_config().llm).complete(
            [{"role": "user", "content": "翻译"}], json_mode=True
        )

        self.assertEqual(result, '{"translations":["译文"]}')
        self.assertEqual(run.call_count, 2)

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_complete_json_retries_malformed_json_in_fresh_session(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, '{"issues":[', ""),
            subprocess.CompletedProcess([], 0, '{"issues":[]}', ""),
        ]
        client = AgyClient(_config().llm)

        result = client.complete_json(
            [{"role": "user", "content": "输出 issues JSON"}],
            tier="cheap",
        )

        self.assertEqual(result, {"issues": []})
        self.assertEqual(run.call_count, 2)
        self.assertIn("上一轮返回的 JSON 无效", run.call_args_list[1].args[0][-1])

    @patch("trans_novel.llm.providers.agy.subprocess.run")
    def test_raises_typed_error_after_persistent_policy_rejection(self, run):
        rejected = (
            "The prompt could not be submitted. The prompt contains sensitive "
            "words that violate Google's Generative AI Prohibited Use policy."
        )
        run.return_value = subprocess.CompletedProcess([], 0, rejected, "")

        with self.assertRaises(ContentPolicyError):
            AgyClient(_config().llm).complete(
                [{"role": "user", "content": "翻译"}]
            )

        self.assertEqual(run.call_count, 3)


if __name__ == "__main__":
    unittest.main()
