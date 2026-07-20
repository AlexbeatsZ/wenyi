from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from trans_novel.config import Config
from trans_novel.llm.providers.codex_cli import CodexCLIClient, format_codex_prompt


def _config(**overrides) -> Config:
    return Config.from_dict(
        {
            "llm": {"provider": "fake"},
            "review_llm": {
                "provider": "codex-cli",
                "command": "custom-codex",
                "cwd": ".",
                "timeout": 12,
                "tiers": {
                    "cheap": {
                        "model": "gpt-5.6-sol",
                        "options": {"reasoning_effort": "high"},
                    }
                },
                **overrides,
            },
        }
    )


class TestCodexCLI(unittest.TestCase):
    def test_prompt_forbids_tools_and_requires_json(self):
        prompt = format_codex_prompt(
            [{"role": "user", "content": "审校这句话"}], json_mode=True
        )
        self.assertIn("Do not use tools", prompt)
        self.assertIn("exactly one valid JSON", prompt)

    @patch("trans_novel.llm.providers.codex_cli.subprocess.run")
    def test_invokes_ephemeral_read_only_exec_via_stdin(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '{"issues":[]}', "")
        cfg = _config()
        assert cfg.review_llm is not None
        client = CodexCLIClient(cfg.review_llm)

        result = client.complete(
            [{"role": "user", "content": "审校"}],
            tier="cheap",
            json_mode=True,
            stage="Reviewer",
        )

        self.assertEqual(result, '{"issues":[]}')
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["custom-codex", "exec"])
        self.assertIn("--ephemeral", args)
        self.assertIn("read-only", args)
        self.assertIn("gpt-5.6-sol", args)
        self.assertEqual(args[-1], "-")
        self.assertIn("审校", run.call_args.kwargs["input"])
        self.assertEqual(client.usage_summary()["totals"]["calls"], 1)

    def test_factory_accepts_codex_review_client(self):
        from trans_novel.llm.factory import build_client_from_llm

        cfg = _config()
        assert cfg.review_llm is not None
        self.assertIsInstance(build_client_from_llm(cfg.review_llm), CodexCLIClient)


if __name__ == "__main__":
    unittest.main()
